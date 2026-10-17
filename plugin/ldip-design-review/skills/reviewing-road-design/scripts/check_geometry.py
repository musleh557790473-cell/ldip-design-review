#!/usr/bin/env python3
"""Phase C1: alignment and junction geometry (SPC-0006 Rev H §4.13E; DH-1 ... DH-10).

Reads the position source of phase B2 — setting-out tables (setout.json, PD) or LandXML alignments
(landxml_network.json, DD; DG-1) — with network.json (build_network.py 0.3.0), road_class.json (read_class.py),
the rules file (skill 8, agent RDE), and optionally criteria.json (stated design speeds), cross_check.json and
LandXML ingestion files (design profiles, ROD-037). Checks:
- ROD-017 / 018 drawn curves (§4.13E.4, DH-2): radius of each curve of a classed road vs r_min, as printed (no
  tolerance). With LandXML as the position source the arcs are already checked by check_alignment.py (phase A),
  so the rule is listed as not run here. Separate finding from the phase-A stated-criteria finding
  (`mode_tag` drawn_curves).
- ROD-019 arc length and ROD-020 spirals (§4.13E.5 - 4.13E.6) at the stated speed of the class and, where it is
  below the ER minimum (ROD-006 / 007 / 009 vd_min), also at that minimum (D10-5, DH-6); a speed that is not a
  key of the rule table is not interpolated; with no stated speed the ER minimum alone is used. A spiral is a
  `spiral` segment next to the curve; at PD the absence reads "no spiral in the setting-out table" (R-H1).
- ROD-035 junction angle (§4.13E.7, DH-3): legs of one junction within `through_tolerance_deg` of a straight
  line form one through arm; each pair of arms of different roads gives the acute crossing angle, which must
  lie in angle_range (75° ... 105°). Roundabout approaches are not junctions here (C2).
- ROD-036 junction spacing (§4.13E.8, DH-7): centre-to-centre chainage between consecutive junctions /
  roundabouts on each classed road vs spacing_local / spacing_collector; spacing_signalised is not run (no
  signal positions, R-H4). An advisory rule gives Observation findings (DH-7).
- ROD-037 grade near a junction (§4.13E.9): steepest design-profile grade within `junction_window_m` (30 m) of
  the junction station, vs g_max ± tolerance; not run without LandXML design profiles (PD).
- Tangents (§4.13E.10, DH-8): every tangent with its neighbouring curves (reverse / same direction) is listed as
  information in `tangents`; no finding (no rule, R-H2).
Class and positions from drawings cap at Probable (K19-1, DH-4); inactive rules are listed as not run.

Usage: python check_geometry.py --setout <setout.json | landxml_network.json> --network <network.json>
           --class <road_class.json> --rules <rules_RDE.json> --stage "<stage name>" [--criteria <criteria.json>]
           [--cross-check <cross_check.json>] [--landxml <x.xml.json> ...] --out <geometry_checks.json>
Exit: 0 written · 2 input error · 3 shapely missing. No network, no shell calls.
"""
__version__ = "0.1.0"

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from road_common import (EXIT_INPUT, EXIT_OK, RoadError, applies, atomic_write_json, emit, main_guard,  # noqa: E402
                         param, read_json, source_of, tol)
from check_alignment import keyed, speeds_for, SPEED_MIN_RULE  # noqa: E402
from location_common import params, shapely_mods, xy  # noqa: E402
from build_network import station_value  # noqa: E402
from find_low_points import to_internal  # noqa: E402

C1_RULES = ("ROD-017", "ROD-018", "ROD-019", "ROD-020", "ROD-035", "ROD-036", "ROD-037")
SPACING_PARAM = {"local_access": "spacing_local", "sub_collector": "spacing_collector"}


def turn(seg):
    """+1 left, -1 right, 0 unknown — from the curve's PC / PI / PT (3 control points)."""
    pts = seg.get("points") or []
    if len(pts) < 3:
        return 0
    (ax, ay), (bx, by), (cx, cy) = pts[0][:2], pts[1][:2], pts[-1][:2]
    z = (bx - ax) * (cy - by) - (by - ay) * (cx - bx)
    return 0 if abs(z) < 1e-9 else (1 if z > 0 else -1)


def acute(b1, b2):
    """Acute angle (0 ... 90°) between two lines with bearings b1, b2 (degrees)."""
    d = abs(b1 - b2) % 180.0
    return min(d, 180.0 - d)


def arms_of(legs, tol_deg):
    """Group the legs of a junction into arms: two legs within tol of a straight line are one through arm."""
    legs = sorted(legs, key=lambda x: (x["bearing"], x["road"]))
    used, arms = set(), []
    for i, a in enumerate(legs):
        if i in used:
            continue
        mate = None
        for j in range(i + 1, len(legs)):
            if j in used:
                continue
            d = abs(((legs[j]["bearing"] - a["bearing"]) % 360.0) - 180.0)
            if d <= tol_deg and (mate is None or d < mate[1]):
                mate = (j, d)
        if mate:
            used |= {i, mate[0]}
            arms.append({"bearing": a["bearing"], "roads": sorted({a["road"], legs[mate[0]]["road"]}), "through": True})
        else:
            used.add(i)
            arms.append({"bearing": a["bearing"], "roads": [a["road"]], "through": False})
    return arms


def grade_profile(prof):
    """Piecewise grade function of a design profile: [(s0, s1, g0, g1)] in printed stations (curves linear)."""
    pv = sorted(prof.get("pvis") or prof.get("pvi") or [], key=lambda p: p["sta"])
    if len(pv) < 2:
        return []
    curves = {round(c["pvi_sta"], 3): c for c in prof.get("curves") or [] if c.get("pvi_sta") is not None}
    g = [100.0 * (pv[i + 1]["elev"] - pv[i]["elev"]) / (pv[i + 1]["sta"] - pv[i]["sta"]) for i in range(len(pv) - 1)]
    pieces, cur = [], pv[0]["sta"]
    for i in range(1, len(pv)):
        c = curves.get(round(pv[i]["sta"], 3)) if i < len(pv) - 1 else None
        L = float(c.get("length") or 0) if c else 0.0
        bvc = pv[i]["sta"] - L / 2
        pieces.append((cur, bvc, g[i - 1], g[i - 1]))
        if L > 0:
            pieces.append((bvc, pv[i]["sta"] + L / 2, g[i - 1], g[i]))
        cur = pv[i]["sta"] + L / 2
    pieces[-1] = (pieces[-1][0], pv[-1]["sta"], pieces[-1][2], pieces[-1][3])
    return pieces


def steepest(pieces, s0, s1):
    """(|grade| max, station) of the profile over [s0, s1]; None when the window misses the profile."""
    best = None
    for a, b, g0, g1 in pieces:
        lo, hi = max(a, s0), min(b, s1)
        if lo > hi + 1e-9 or b <= a:
            continue
        for s in (lo, hi):
            gs = g0 + (g1 - g0) * (s - a) / (b - a)
            if best is None or abs(gs) > best[0] + 1e-12:
                best = (abs(gs), s)
    return best


def printed_station(d, al):
    """Distance along the alignment -> printed station (inverse of find_low_points.to_internal)."""
    internal = (al.get("sta_start") or 0.0) + d
    eqs = sorted([q for q in al.get("station_equations") or [] if q.get("sta_internal") is not None
                  and q.get("sta_ahead") is not None], key=lambda q: q["sta_internal"])
    sta = internal
    for q in eqs:
        if internal >= q["sta_internal"] - 1e-6:
            sta = q["sta_ahead"] + (internal - q["sta_internal"])
    return sta


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--setout", required=True)
    ap.add_argument("--network", required=True)
    ap.add_argument("--class", dest="road_class", required=True)
    ap.add_argument("--rules", required=True)
    ap.add_argument("--stage", required=True)
    ap.add_argument("--criteria")
    ap.add_argument("--cross-check")
    ap.add_argument("--landxml", nargs="*", default=[])
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)

    def run():
        g, _ = shapely_mods()
        C1 = params().get("c1") or {}
        thru = float(C1.get("through_tolerance_deg", 15.0))
        window = float(C1.get("junction_window_m", 30.0))
        rb = read_json(a.rules)
        smap = rb.get("stage_map") or {}
        if a.stage not in smap:
            raise RoadError(EXIT_INPUT, f"stage '{a.stage}' not in the rulebook stage_map ({', '.join(smap)})")
        phase = smap[a.stage]["phase"] if isinstance(smap[a.stage], dict) else smap[a.stage]
        so, net, rc = read_json(a.setout), read_json(a.network), read_json(a.road_class)
        source = net.get("position_source") or so.get("position_source") or "tables"
        crit = read_json(a.criteria) if a.criteria else {}
        xc = read_json(a.cross_check) if a.cross_check else {}
        uncertain = {c["subject"].split(":")[0] for c in xc.get("checks", [])}
        rules = {r["rule_id"]: r for r in rb.get("rules", [])}
        checks, not_run, passed, info = [], [], [], {}
        for s in rb.get("rules_skipped", []):
            if s.get("rule_id") in C1_RULES:
                not_run.append({"rule_id": s["rule_id"], "phase": "C1", "reason": s.get("reason", "inactive")})
        cls_of = {n: (r.get("class") if isinstance(r, dict) else None) for n, r in (rc.get("roads") or {}).items()}
        rbout_names = {r["name"] for r in net.get("roundabouts", [])}

        def stated(cls):
            v = (((crit.get("criteria") or {}).get(cls) or {}).get("design_speed") or {}).get("numbers") or []
            return v[0] if v else None

        def speeds(cls):
            sp = speeds_for(cls, stated(cls), rules)
            if sp:
                return sp
            r = rules.get(SPEED_MIN_RULE.get(cls or "", ""))
            p = param(r, "vd_min") if r else None
            return [("ER minimum for the class (no stated speed)", float(p["value"]))] if p else []

        def seg_ev(al, seg, extract):
            e = dict(al.get("evidence") or {})
            st = "landxml" if source == "landxml" else "dwg_vector"
            pts = seg.get("points") or [[None, None]]
            return {"source_type": st, "path": e.get("path"), "sha256": e.get("sha256"),
                    "handles": list(seg.get("handles") or []), "xy": xy(pts[0]) if pts[0][0] is not None else None,
                    "extract": extract}

        def add(rid, pname, subject, obs, req, op, p, ok, ev, cls=None, extra=None):
            c = {"rule_id": rid, "parameter": pname, "class": cls, "subject": subject, "observed": obs, "required": req,
                 "comparison": op, "tolerance": tol(p), "unit": p.get("unit"), "result": "pass" if ok else "fail",
                 "link_cap": "Probable", "evidence": ev}
            if extra:
                c.update(extra)
            if rules[rid].get("state") == "advisory":
                c["severity_override"] = "Observation"          # DH-7 (advisory rule)
            checks.append(c)

        aligns = so.get("alignments") or {}
        # ---------------- curves: ROD-017 / 018 drawn (DH-2), ROD-019, ROD-020; tangents (DH-8)
        tangents, unclassed = [], []
        for name in sorted(aligns):
            if name in rbout_names:
                continue
            al = aligns[name]
            segs = sorted(al.get("segments") or [], key=lambda s: (station_value((s.get("stations") or [None])[0])
                                                                   if s.get("stations") else (s.get("sta_start") or 0)))
            cls = cls_of.get(name)
            for i, s in enumerate(segs):
                if s["type"] == "tangent":
                    prev = segs[i - 1] if i > 0 and segs[i - 1]["type"] == "curve" else None
                    nxt = segs[i + 1] if i + 1 < len(segs) and segs[i + 1]["type"] == "curve" else None
                    rel = None
                    if prev and nxt and turn(prev) and turn(nxt):
                        rel = "reverse" if turn(prev) != turn(nxt) else "same direction"
                    tangents.append({"road": name, "tag": s.get("tag"), "length": s.get("length"),
                                     "stations": s.get("stations") or s.get("sta_start"),
                                     "between": [x.get("tag") for x in (prev, nxt) if x], "curves": rel})
            curves = [s for s in segs if s["type"] == "curve" and s.get("radius")]
            if not curves:
                continue
            if not cls:
                unclassed.append(name)
                continue
            for rid in ("ROD-017", "ROD-018"):
                rule = rules.get(rid)
                if not rule or not applies(rule, cls):
                    continue
                if source == "landxml":
                    continue
                p = param(rule, "r_min")
                for s in curves:
                    R = float(s["radius"])
                    add(rid, "r_min", f"{name} {s.get('tag')} R {R:g} m", R, p["value"], "ge", p, R >= float(p["value"]) - 1e-9,
                        seg_ev(al, s, f"{name} {s.get('tag')}: R = {R:g} m, L = {s.get('length')} m, Δ {s.get('delta')}, "
                                      f"sta {' – '.join(s.get('stations') or [])}"),
                        cls, {"mode_tag": "drawn_curves", "position_uncertain": name in uncertain})
            sp = speeds(cls)
            r19, r20 = rules.get("ROD-019"), rules.get("ROD-020")
            for basis, v in sp:
                if r19 and applies(r19, cls):
                    p = param(r19, "arc_min")
                    req = keyed(p, v) if p else None
                    if req is None:
                        not_run.append({"rule_id": "ROD-019", "phase": "C1", "road": name,
                                        "reason": f"{basis} speed {v:g} km/h is not a key of the rule table"})
                    else:
                        for s in curves:
                            if s.get("length") is None:
                                continue
                            Lc = float(s["length"])
                            add("ROD-019", "arc_min", f"{name} {s.get('tag')} at {v:g} km/h ({basis})", Lc, req, "ge", p,
                                Lc >= req - 1e-9, seg_ev(al, s, f"{name} {s.get('tag')}: arc length {Lc:g} m, R {s['radius']} m"),
                                cls, {"speed_kmh": v, "speed_basis": basis})
                if r20 and applies(r20, cls):
                    p = param(r20, "spiral_upper_radius")
                    lim0 = param(r20, "spiral_not_required_up_to")
                    if lim0 and v <= float(lim0["value"]):
                        not_run.append({"rule_id": "ROD-020", "phase": "C1", "road": name,
                                        "reason": f"{basis} speed {v:g} km/h: spiral not required up to {lim0['value']} km/h"})
                        continue
                    req = keyed(p, v) if p else None
                    if req is None:
                        not_run.append({"rule_id": "ROD-020", "phase": "C1", "road": name,
                                        "reason": f"{basis} speed {v:g} km/h is not a key of the rule table"})
                        continue
                    for s in curves:
                        R = float(s["radius"])
                        if R > req:
                            continue
                        k = segs.index(s)
                        nb = [segs[j] for j in (k - 1, k + 1) if 0 <= j < len(segs)]
                        has = any(x["type"] == "spiral" for x in nb)
                        where = "LandXML alignment" if source == "landxml" else "setting-out table (R-H1)"
                        add("ROD-020", "spiral_upper_radius", f"{name} {s.get('tag')} R {R:g} m at {v:g} km/h ({basis})",
                            "spiral" if has else f"no spiral in the {where}", f"spiral where R ≤ {req:g} m", "present", p, has,
                            seg_ev(al, s, f"{name} {s.get('tag')}: R = {R:g} m ≤ {req:g} m at {v:g} km/h; neighbours: "
                                          + ", ".join(f"{x.get('tag')} ({x['type']})" for x in nb)),
                            cls, {"speed_kmh": v, "speed_basis": basis})
        if unclassed:
            for rid in ("ROD-017", "ROD-018", "ROD-019", "ROD-020"):
                if rid in rules:
                    not_run.append({"rule_id": rid, "phase": "C1", "reason": "road without a class not judged (RC-2 / K23): "
                                    + ", ".join(unclassed[:15]), "status_hint": "Not auto-verifiable"})
        if source == "landxml":
            for rid in ("ROD-017", "ROD-018"):
                if rid in rules:
                    not_run.append({"rule_id": rid, "phase": "C1", "reason": "LandXML arcs are checked by check_alignment.py (phase A)"})
        # ---------------- ROD-035 junction angle (DH-3)
        r35 = rules.get("ROD-035")
        if r35:
            p = param(r35, "angle_range")
            lo, hi = float(p["value"][0]), float(p["value"][1])
            low = min(lo, 180.0 - hi)
            nok = 0
            for j in net.get("junctions", []):
                arms = arms_of(j.get("leg_bearings") or [], thru)
                worst = None
                for i in range(len(arms)):
                    for k in range(i + 1, len(arms)):
                        if set(arms[i]["roads"]) & set(arms[k]["roads"]):
                            continue
                        ang = acute(arms[i]["bearing"], arms[k]["bearing"])
                        if worst is None or ang < worst[0]:
                            worst = (ang, arms[i]["roads"], arms[k]["roads"])
                if worst is None:
                    continue
                ang, ra, rbb = worst
                roads = sorted(set(ra) | set(rbb))
                ok = ang >= low - 1e-9
                nok += ok
                pos = j["xy"]
                add("ROD-035", "angle_range", f"{j['id']} ({' / '.join(roads)}) at E {pos[0]:.1f} N {pos[1]:.1f}",
                    round(ang, 1), p["value"], "in", p, ok,
                    {"source_type": "script_output", "path": None, "sha256": None, "xy": xy(pos),
                     "extract": f"{j['id']}: {' / '.join(ra)} meets {' / '.join(rbb)} at {ang:.1f}° (legs: "
                                + ", ".join(f"{x['road']} {x['bearing']:g}°" for x in j.get('leg_bearings') or [])
                                + f"; bearings {net.get('angle_offset_m', 10)} m from the junction)"},
                    None, {"position_uncertain": any(r in uncertain for r in roads)})
            passed.append({"rule_id": "ROD-035", "phase": "C1", "junctions_ok": nok})
        # ---------------- ROD-036 junction spacing (DH-7)
        r36 = rules.get("ROD-036")
        if r36:
            not_run.append({"rule_id": "ROD-036", "phase": "C1", "parameter": "spacing_signalised",
                            "reason": "no signal positions in the carriers (R-H4)"})
            jxy = {j["id"]: j["xy"] for j in net.get("junctions", [])}
            for r_ in net.get("roundabouts", []):
                if r_.get("centre"):
                    jxy[r_["name"]] = r_["centre"]
            for name, road in sorted((net.get("roads") or {}).items()):
                cls = cls_of.get(name)
                locs = [x for x in road.get("locations", []) if x.get("chainage") is not None]
                if len(locs) < 2:
                    continue
                pname = SPACING_PARAM.get(cls or "")
                if not cls or not pname or not param(r36, pname):
                    not_run.append({"rule_id": "ROD-036", "phase": "C1", "road": name,
                                    "reason": "road without a class" if not cls else f"no spacing value for class {cls}"})
                    continue
                p = param(r36, pname)
                for x0, x1 in zip(locs, locs[1:]):
                    if x0["location"] == x1["location"]:
                        continue
                    d = round(x1["chainage"] - x0["chainage"], 1)
                    pos = jxy.get(x0["location"]) or [0, 0]
                    add("ROD-036", pname, f"{name}: {x0['location']} – {x1['location']} {d:g} m", d, p["value"], "ge", p,
                        d >= float(p["value"]) - 1e-9,
                        {"source_type": "script_output", "path": None, "sha256": None, "xy": xy(pos),
                         "extract": f"{name} ({cls}): {x0['location']} at ch {x0['chainage']:g} m to {x1['location']} at ch "
                                    f"{x1['chainage']:g} m = {d:g} m (centre to centre"
                                    + (", chainage from table stations" if "stations" in (x0.get("chainage_basis"), x1.get("chainage_basis")) else "")
                                    + ")"},
                        cls, {"position_uncertain": name in uncertain})
            for np_ in net.get("not_positioned", []):
                not_run.append({"rule_id": "ROD-036", "phase": "C1", "road": np_["road"],
                                "reason": f"{np_['location']}: {np_['reason']}"})
        # ---------------- ROD-037 grade near a junction (DD)
        r37 = rules.get("ROD-037")
        if r37:
            profs = {}
            als = {}
            for path in a.landxml:
                ing = read_json(path)
                for al in (ing.get("content") or {}).get("alignments", []):
                    als.setdefault(al.get("name"), (al, source_of(ing)))
                for pr in (ing.get("content") or {}).get("profiles", []):
                    if pr.get("kind") == "design":
                        profs.setdefault(pr.get("alignment"), []).append(pr)
            if not profs:
                not_run.append({"rule_id": "ROD-037", "phase": "C1", "reason": "no LandXML design profile (DD carrier)"
                                + (f"; submission phase {phase}" if phase == "PD" else "")})
            else:
                p = param(r37, "g_max")
                dist = param(r37, "distance")
                w = float(dist["value"]) if dist else window
                for j in net.get("junctions", []):
                    for name in j["roads"]:
                        if name not in profs or name not in als or name not in (net.get("roads") or {}):
                            continue
                        al, src = als[name]
                        geom = [c for part in net["roads"][name]["geometry"] for c in part]
                        line = g.LineString(geom) if len(geom) >= 2 else None
                        if line is None:
                            continue
                        dj = line.project(g.Point(j["xy"]))
                        sta = printed_station(dj, al)
                        for pr in profs[name]:
                            st = steepest(grade_profile(pr), sta - w, sta + w)
                            if st is None:
                                continue
                            gmax, s_at = st
                            if to_internal(s_at, al) is None:
                                continue
                            add("ROD-037", "g_max", f"{j['id']} on {name} ({pr.get('name')}) sta {sta:.1f}", round(gmax, 2),
                                p["value"], "le", p, gmax <= float(p["value"]) + tol(p) + 1e-9,
                                {"source_type": "landxml", "path": src.get("path"), "sha256": src.get("sha256"),
                                 "xy": xy(j["xy"]),
                                 "extract": f"{name} profile {pr.get('name')}: steepest grade {gmax:.2f} % at sta {s_at:.1f} "
                                            f"within ± {w:g} m of {j['id']} (sta {sta:.1f})"},
                                cls_of.get(name))
        out = {"tool": {"script": "check_geometry.py", "version": __version__}, "mode": "geometry", "stage": a.stage,
               "phase": phase, "position_source": source,
               "params": {"through_tolerance_deg": thru, "angle_offset_m": net.get("angle_offset_m"), "junction_window_m": window},
               "checks": checks, "passed": passed, "not_run": not_run, "tangents": tangents,
               "counts": {"checks": len(checks), "fail": sum(1 for c in checks if c["result"] == "fail"),
                          "tangents": len(tangents),
                          "reverse_tangents": sum(1 for t in tangents if t["curves"] == "reverse")}}
        info["by_rule"] = {r: sum(1 for c in checks if c["rule_id"] == r and c["result"] == "fail")
                           for r in sorted({c["rule_id"] for c in checks})}
        atomic_write_json(a.out, out)
        emit({"status": "ok", "out": a.out, "phase": phase, "position_source": source, **out["counts"],
              "fail_by_rule": info["by_rule"], "not_run": len(not_run)})
        return EXIT_OK
    return main_guard(run)


if __name__ == "__main__":
    sys.exit(main())
