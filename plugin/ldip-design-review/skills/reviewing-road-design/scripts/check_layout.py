#!/usr/bin/env python3
"""Phase C2: junction and roundabout layout (SPC-0006 Rev I §4.13F; DI-1 ... DI-10).

Reads the network of phase B2 (network.json: junctions, roundabouts with centre and approaches), the road classes
(road_class.json), the rules file (skill 8, agent RDE), optionally the design criteria (criteria.json: design
vehicle, declared departures) and the DWG ingestion files of the plan carriers with their skill 1 line work
(`--file plan_geometry=<x.dwg.json>`, DD also `--file landscape_plan=<x.dwg.json>`). The curb layers of a site
are a skill parameter (references/road_class_params.json sites -> curb_layers, R-I3). Arcs of the curb line work
are ARC records and same-turn runs of short polyline segments, each fitted with a least-squares circle
(location_common.fitted_arcs); arcs shorter than arc_min_len_m (not full circles) are dropped, and a curve drawn twice
or on two curb layers (same centre and mid point, radius within 0.5 m) counts once, from the first listed layer. Checks:
- ROD-032 corner returns (§4.13F.3, DI-2 a): an arc with corner_r_min_m <= R < corner_r_max_m and sweep in corner_sweep_deg whose
  mid point lies within corner_search_m of a junction centre belongs to the nearest junction; the highest class of
  the roads meeting there sets the `corner_radius` range; the fitted radius (0.1 m) is compared without tolerance.
  A junction with a road without a class is not judged (K23). Design vehicle: the stated design vehicle of the
  criteria table vs "WB-15 or city bus"; a declared departure on it (DEP-nn) is quoted and never cancels the
  finding (as ROD-115, DF-8); not stated -> not run at PD, Missing information from DD (C5). swept_path is left to
  ROD-106 (presence, DD).
- Roundabouts (§4.13F.4, DI-3 ... DI-6): central island = smallest concentric CIRCLE / closed ring; outer edge =
  largest common radius of the concentric curb pieces below ring_max_m (else the setting-out radius, noted);
  junction type C-C / C-L / L-L from the approach classes (unknown -> not run). ROD-039 ICD = 2 x outer radius.
  ROD-042 splitter islands = closed curb outlines < island_max_area_m2 on an approach within island_search_m of
  the centre; minimum rotated rectangle -> width / length; length_desirable is advisory (Observation). ROD-040
  entry width only where an island bounds the entry: shortest distance from the island to the curb on the
  entry side (right of the approach direction, right-hand traffic) within entry_band_m outside the outer edge.
  ROD-043 apron = concentric ring between island and outer edge (PD not drawn -> not run; DD -> Missing
  information); its crossfall and drainage grade are phase D.
- ROD-033 driveway returns and ROD-034 sight triangles (§4.13F.5, DI-7) are DD rules: not run before DD.
  Driveway returns = curb arcs with R <= driveway_r_max_m away from junctions and roundabouts, next to a plot
  (site plot_layers; without them the frontage condition is not applied, noted); the class of the nearest road
  sets `driveway_radius`. Sight triangles = legs along the two curb tangents from the tangent intersection point
  of each corner (10 m) and driveway return (5 m); obstructions are blocks and line work of the landscape and
  plan carriers whose layer or block name holds an obstruction term.
- ROD-041 entry deflection is not run (measuring method [GAP] R-I1).
Class from drawings caps at Probable (DH-4); a check of an advisory rule carries severity_override Observation
(as DH-7; ROD-040 in rules_v7); inactive rules are listed as not run.

Usage: python check_layout.py --network <network.json> --class <road_class.json> --rules <rules_RDE.json>
           --stage "<stage name>" --file plan_geometry=<x.dwg.json> [...] [--file landscape_plan=<x.dwg.json>]
           [--criteria <criteria.json>] --out <layout_checks.json>
Exit: 0 written · 2 input error · 3 shapely missing. No network, no shell calls.
"""
__version__ = "0.1.1"

import argparse
import math
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from road_common import (EXIT_INPUT, EXIT_OK, RoadError, applies, atomic_write_json, emit, main_guard,  # noqa: E402
                         param, read_json, source_of, tol)
from location_common import fitted_arcs, layer_match, params, shapely_mods, xy  # noqa: E402

C2_RULES = ("ROD-032", "ROD-033", "ROD-034", "ROD-039", "ROD-040", "ROD-041", "ROD-042", "ROD-043")
DD_RULES = ("ROD-033", "ROD-034")
PHASE_ORDER = {"PD": 1, "DD": 2, "FD": 3}
RANK = {"local_access": 1, "service_road": 1, "sub_collector": 2, "arterial": 3}
TYPE_LETTER = {"sub_collector": "C", "local_access": "L"}
VEHICLE_OK = re.compile(r"\bWB-?\s?15\b|\bcity\s+bus\b|\bbus\b", re.I)
VEHICLE_DEP = re.compile(r"design\s+vehicle|WB-?\s?15|curb\s+return", re.I)
PLAN_CARRIERS = ("plan_geometry",)
LANDSCAPE_CARRIERS = ("landscape_plan",)


def junction_type(classes):
    letters = [TYPE_LETTER.get(c or "") for c in classes]
    if not letters or None in letters:
        return None
    if all(x == "C" for x in letters):
        return "C-C"
    if all(x == "L" for x in letters):
        return "L-L"
    return "C-L"


def bearing(a, b):
    return math.degrees(math.atan2(b[1] - a[1], b[0] - a[0])) % 360.0


def ang_diff(a, b):
    d = abs(a - b) % 360.0
    return min(d, 360.0 - d)


def approach_legs(rbt, Ro, roads_geom, g, probe_m=10.0):
    """Approach legs of a roundabout: where each approach road crosses a circle probe_m outside the outer edge
    (approach centrelines may be drawn to the centre, so the B2 approach point can be the centre itself). A road
    crossing twice gives two legs. Falls back to the B2 approach point when it lies off the centre."""
    ctr = rbt["centre"]
    R = (Ro or float(rbt.get("radius") or 20.0)) + probe_m
    ring = g.Point(ctr).buffer(R, 128).exterior
    legs = []
    for road in sorted({x["road"] for x in rbt.get("approaches", [])}):
        pts = []
        geom = roads_geom.get(road)
        if geom is not None:
            hit = geom.intersection(ring)
            pts = [hit] if hit.geom_type == "Point" else [q for q in getattr(hit, "geoms", []) if q.geom_type == "Point"]
        bears = sorted({round(bearing(ctr, (q.x, q.y)), 1) for q in pts})
        if not bears:
            bears = sorted({round(bearing(ctr, x["xy"]), 1) for x in rbt.get("approaches", [])
                            if x["road"] == road and math.dist(x["xy"], ctr) > 1.0})
        for i, b in enumerate(bears):
            legs.append({"road": road, "leg": road if len(bears) == 1 else f"{road}#{i + 1}", "bearing": b})
    return legs


def triangle(pi, p0, p1, leg, g):
    """Sight triangle: legs of `leg` m from the tangent intersection point toward the two tangent points."""
    def unit(p):
        L = math.dist(pi, p) or 1.0
        return ((p[0] - pi[0]) / L, (p[1] - pi[1]) / L)
    u0, u1 = unit(p0), unit(p1)
    return g.Polygon([pi, (pi[0] + leg * u0[0], pi[1] + leg * u0[1]), (pi[0] + leg * u1[0], pi[1] + leg * u1[1])])


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--network", required=True)
    ap.add_argument("--class", dest="road_class", required=True)
    ap.add_argument("--rules", required=True)
    ap.add_argument("--stage", required=True)
    ap.add_argument("--file", action="append", default=[])
    ap.add_argument("--criteria")
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)

    def run():
        g, ops = shapely_mods()
        P = params()
        C2 = P.get("c2") or {}
        rb = read_json(a.rules)
        smap = rb.get("stage_map") or {}
        if a.stage not in smap:
            raise RoadError(EXIT_INPUT, f"stage '{a.stage}' not in the rulebook stage_map ({', '.join(smap)})")
        phase = smap[a.stage]["phase"] if isinstance(smap[a.stage], dict) else smap[a.stage]
        net, rc = read_json(a.network), read_json(a.road_class)
        site = net.get("site")
        site_p = (P.get("sites") or {}).get(site) or {}
        crit = read_json(a.criteria) if a.criteria else {}
        files = []
        for spec in a.file:
            if "=" not in spec:
                raise RoadError(EXIT_INPUT, f"--file must be <carrier>=<ingestion.json>: {spec}")
            carrier, path = spec.split("=", 1)
            ing = read_json(path)
            if (ing.get("source") or {}).get("file_type") != "dwg":
                continue
            lw = []
            for s in (ing.get("content") or {}).get("side_files", []):
                if s.get("kind") == "linework":
                    lw = read_json(Path(path).parent / s["path"]).get("records", [])
            files.append((carrier, ing, lw))
        rules = {r["rule_id"]: r for r in rb.get("rules", [])}
        checks, not_run, passed = [], [], []
        for s in rb.get("rules_skipped", []):
            if s.get("rule_id") in C2_RULES:
                not_run.append({"rule_id": s["rule_id"], "phase": "C2", "reason": s.get("reason", "inactive")})
        cls_of = {n: (r.get("class") if isinstance(r, dict) else None) for n, r in (rc.get("roads") or {}).items()}

        def add(rid, pname, subject, obs, req, op, p, ok, ev, cls=None, extra=None, cap="Probable"):
            c = {"rule_id": rid, "parameter": pname, "class": cls, "subject": subject, "observed": obs, "required": req,
                 "comparison": op, "tolerance": tol(p), "unit": p.get("unit"), "result": "pass" if ok else "fail",
                 "evidence": ev}
            if cap:
                c["link_cap"] = cap                      # class from drawings (DH-4)
            if extra:
                c.update(extra)
            if rules[rid].get("state") == "advisory":
                c["severity_override"] = "Observation"          # advisory rule (as DH-7; ROD-040 in rules_v7)
            checks.append(c)

        corner_max = float(C2.get("corner_r_max_m", 30.0))
        corner_min = float(C2.get("corner_r_min_m", 3.0))          # median / island noses are not corners
        search = float(C2.get("corner_search_m", 25.0))
        sw_lo, sw_hi = (float(x) for x in C2.get("corner_sweep_deg", [45, 135]))
        ring_max = float(C2.get("ring_max_m", 30.0))
        conc_m = float(C2.get("concentric_m", 1.0))
        ring_tol = float(C2.get("ring_tol_m", 0.3))
        isl_area = float(C2.get("island_max_area_m2", 200.0))
        isl_search = float(C2.get("island_search_m", 40.0))
        isl_bearing = float(C2.get("island_bearing_deg", 30.0))
        entry_band = float(C2.get("entry_band_m", 15.0))
        dw_max = float(C2.get("driveway_r_max_m", 8.0))
        frontage = float(C2.get("driveway_frontage_m", 5.0))
        terms = [str(x) for x in C2.get("obstruction_terms", [])]
        curb_layers = site_p.get("curb_layers") or []
        plan = [(ing, lw) for carrier, ing, lw in files if carrier in PLAN_CARRIERS]
        recs = [(r, source_of(ing).get("path"), source_of(ing).get("sha256")) for ing, lw in plan for r in lw]
        arcs = fitted_arcs(recs, curb_layers, float(C2.get("arc_fit_tol", 0.05)), float(C2.get("seg_max_m", 3.0))) \
            if curb_layers else []
        min_len = float(C2.get("arc_min_len_m", 2.0))
        arcs = [x for x in arcs if x["full"] or x["length"] >= min_len]   # transition slivers of the fit
        prio = {lay: i for i, lay in enumerate(curb_layers)}
        uniq = []                                        # a curve drawn twice, or on two curb layers (curb face and
        for x in sorted(arcs, key=lambda x: prio.get(x["layer"], len(prio))):   # edge line): once, first layer wins
            if not any(math.dist(x["c"], u["c"]) < 0.5 and abs(x["R"] - u["R"]) < 0.5
                       and math.dist(x["mid"], u["mid"]) < 1.0 and not x["full"] for u in uniq):
                uniq.append(x)
        arcs = sorted(uniq, key=lambda a: (str(a["path"]), str(a["handle"]), a["run"]))
        curb_recs = [x for x in recs if curb_layers and layer_match(x[0], curb_layers)]
        geo_rules = [r for r in ("ROD-032", "ROD-033", "ROD-034", "ROD-039", "ROD-040", "ROD-042", "ROD-043") if r in rules]
        if not curb_layers:
            for rid in geo_rules:
                not_run.append({"rule_id": rid, "phase": "C2", "reason": f"curb layer not set for site {site} (R-I3)"})
        elif not plan:
            for rid in geo_rules:
                not_run.append({"rule_id": rid, "phase": "C2", "reason": "no plan_geometry DWG with line work given"})
        if "ROD-041" in rules:
            not_run.append({"rule_id": "ROD-041", "phase": "C2", "reason": "entry deflection: measuring method not fixed (R-I1)"})
        rbouts = [r for r in net.get("roundabouts", []) if r.get("centre")]
        juncs = net.get("junctions", [])

        def ev_arc(arc, extract):
            return {"source_type": "dwg_vector", "path": arc["path"], "sha256": arc["sha256"], "handles": [arc["handle"]],
                    "layer": arc["layer"], "xy": xy(arc["mid"]), "extract": extract}

        def concentric(arc):
            return any(math.dist(arc["c"], r["centre"]) <= conc_m for r in rbouts)

        # ---------------------------------------------------------------- ROD-032 corner returns (DI-1, DI-2 a)
        corners = []
        if arcs and juncs:
            for arc in arcs:
                if arc["full"] or not corner_min <= arc["R"] < corner_max or not (sw_lo <= arc["sweep"] <= sw_hi) or concentric(arc):
                    continue
                j = min(juncs, key=lambda j: (math.dist(arc["mid"], j["xy"]), j["id"]))
                if math.dist(arc["mid"], j["xy"]) <= search:
                    corners.append((j, arc))
        r32 = rules.get("ROD-032")
        summary = {}
        if r32 and arcs:
            p = param(r32, "corner_radius")
            unknown, nocls = set(), set()
            for j, arc in corners:
                classes = [cls_of.get(r) for r in j["roads"]]
                if None in classes:
                    unknown.add(j["id"])
                    continue
                hi = max(classes, key=lambda c: RANK.get(c, 0))
                rng = (p["value"] or {}).get(hi) if p else None
                if not rng or not applies(r32, hi):
                    nocls.add(f"{j['id']} ({hi})")
                    continue
                R = round(arc["R"], 1)
                ok = float(rng[0]) - 1e-9 <= R <= float(rng[1]) + 1e-9
                summary.setdefault(hi, Counter())[f"{R:g}"] += 1
                add("ROD-032", "corner_radius", f"{j['id']} ({' / '.join(j['roads'])}) corner R {R:g} m", R, rng, "in", p, ok,
                    ev_arc(arc, f"{j['id']} ({' / '.join(j['roads'])}; junction class {hi}): curb return {arc['type']} "
                                f"{arc['handle']} on {arc['layer']} fitted R {arc['R']:.2f} m, sweep {arc['sweep']:.0f}°, "
                                f"{math.dist(arc['mid'], j['xy']):.1f} m from the junction centre"),
                    hi, {"junction": j["id"]})
            if unknown:
                not_run.append({"rule_id": "ROD-032", "phase": "C2", "parameter": "corner_radius",
                                "reason": f"{len(unknown)} junction(s) with a road without a class not judged (K23): "
                                          + ", ".join(sorted(unknown)[:15]), "status_hint": "Not auto-verifiable"})
            if nocls:
                not_run.append({"rule_id": "ROD-032", "phase": "C2", "parameter": "corner_radius",
                                "reason": "no corner range for the junction class: " + ", ".join(sorted(nocls)[:15])})
            no_corner = sorted({j["id"] for j in juncs} - {j["id"] for j, _ in corners})
            passed.append({"rule_id": "ROD-032", "phase": "C2", "junctions": len(juncs),
                           "junctions_with_corners": len({j["id"] for j, _ in corners}), "corners": len(corners),
                           "junctions_without_corner_arc": no_corner[:20]})
        if r32:
            not_run.append({"rule_id": "ROD-032", "phase": "C2", "parameter": "swept_path",
                            "reason": "swept-path evidence is checked by ROD-106 (presence, DD)"})
            p = param(r32, "design_vehicle")
            cells = []
            for c, row in sorted((crit.get("criteria") or {}).items()):
                cell = (row or {}).get("design_vehicle")
                if cell and str(cell.get("text") or "").strip():
                    cells.append((c, cell))
            deps = [d for d in crit.get("departures", []) if VEHICLE_DEP.search(d.get("text", ""))]
            dep_txt = ("; declared departure(s) in the design report (quoted, not cancelling): "
                       + "; ".join(f"{d['ref']} p.{d['page']}: {d['text'][:160]}" for d in deps)) if deps else ""
            src = crit.get("source") or {}
            if p and cells:
                bad = [(c, cell) for c, cell in cells if not VEHICLE_OK.search(cell["text"])]
                if bad:
                    c0, cell0 = bad[0]
                    add("ROD-032", "design_vehicle", "design vehicle: " + "; ".join(f"{c} '{cell['text']}'" for c, cell in bad)
                        + (f" (declared departure {', '.join(d['ref'] for d in deps)} quoted in the evidence; it does not "
                           "cancel the finding)" if deps else ""),
                        " / ".join(sorted({cell["text"] for _, cell in bad})), p["value"], "eq", p, False,
                        {"source_type": "pdf_vector_text", "path": src.get("path"), "sha256": src.get("sha256"),
                         "page": cell0.get("page"), "bbox": cell0.get("bbox"),
                         "extract": "Design criteria: " + "; ".join(f"{c}: Design vehicle '{cell['text']}' (p.{cell.get('page')})"
                                                                    for c, cell in bad) + dep_txt},
                        None, {"departures_quoted": [d["ref"] for d in deps]})
                else:
                    passed.append({"rule_id": "ROD-032", "phase": "C2", "parameter": "design_vehicle",
                                   "stated": {c: cell["text"] for c, cell in cells}})
            elif p:
                if PHASE_ORDER.get(phase, 0) >= PHASE_ORDER["DD"]:
                    add("ROD-032", "design_vehicle", "design vehicle not stated (C5)", "not stated", p["value"], "present", p,
                        False, {"source_type": "script_output", "path": None, "sha256": None,
                                "extract": "no design vehicle row in the design criteria table"
                                           + (f" of {Path(src.get('path') or '').name}" if src.get("path") else
                                              " (no criteria file given)")},
                        None, {"status_override": "Missing information", "issue_type": "missing_information"}, cap=None)
                else:
                    not_run.append({"rule_id": "ROD-032", "phase": "C2", "parameter": "design_vehicle",
                                    "reason": "design vehicle not stated; C5 is required from DD"})
        # ---------------------------------------------------------------- roundabouts (DI-3 ... DI-6)
        rb_info = []
        closed = []
        for r, path, sha in curb_recs:
            if r.get("closed") and r["type"] != "CIRCLE" and len(r["points"]) >= 4:
                poly = g.Polygon([tuple(q[:2]) for q in r["points"]])
                if not poly.is_valid:
                    poly = poly.buffer(0)
                if not poly.is_empty and 0 < poly.area < isl_area:
                    closed.append((poly, r, path, sha))
        island_handles = {r.get("handle") for _, r, _, _ in closed}
        curb_lines = [(g.LineString([tuple(q[:2]) for q in r["points"]]), r) for r, _, _ in curb_recs
                      if not r.get("closed") and len(r["points"]) >= 2]
        roads_geom = {n: ops.unary_union([g.LineString(pt) for pt in rd["geometry"] if len(pt) >= 2])
                      for n, rd in sorted((net.get("roads") or {}).items()) if rd.get("geometry")}
        r39, r40, r42, r43 = (rules.get(x) for x in ("ROD-039", "ROD-040", "ROD-042", "ROD-043"))
        if r43:
            not_run.append({"rule_id": "ROD-043", "phase": "C2", "parameter": "circulatory_crossfall / drainage_g_min",
                            "reason": "need sections and surfaces: phase D (DI-1)"})
        for rbt in (rbouts if arcs else []):
            ctr = rbt["centre"]
            conc = [x for x in arcs if math.dist(x["c"], ctr) <= conc_m and x["R"] < ring_max
                    and x["handle"] not in island_handles]          # edges of splitter islands are not rings
            full = sorted([x for x in conc if x["full"]], key=lambda x: (x["R"], str(x["handle"])))
            island = full[0] if full else None
            Ri = island["R"] if island else None
            others = [x for x in conc if Ri is None or abs(x["R"] - Ri) > ring_tol]
            outer = None
            if others:
                Rmax = max(x["R"] for x in others)
                pieces = [x for x in others if Rmax - x["R"] <= ring_tol]
                outer = (sum(x["R"] for x in pieces) / len(pieces), pieces)
            Ro = outer[0] if outer else float(rbt.get("radius") or 0) or None
            basis = "outer curb line work" if outer else "setting-out radius (no concentric outer curb found)"
            legs = approach_legs(rbt, Ro, roads_geom, g)
            classes = [cls_of.get(x["road"]) for x in rbt.get("approaches", [])]
            jt = junction_type(classes)
            apron = None
            if Ri is not None and Ro is not None:
                mids = [x for x in others if Ri + ring_tol < x["R"] < Ro - ring_tol]
                if mids:
                    apron = min(mids, key=lambda x: x["R"])
            info = {"name": rbt["name"], "centre": xy(ctr), "island_R": round(Ri, 2) if Ri else None,
                    "outer_R": round(Ro, 2) if Ro else None, "outer_basis": basis, "junction_type": jt,
                    "approach_classes": dict(zip([x["road"] for x in rbt.get("approaches", [])], classes)),
                    "apron_width": round(apron["R"] - Ri, 2) if apron else None, "islands": [], "entries": []}
            handles = [x["handle"] for x in (outer[1] if outer else [])] + ([island["handle"]] if island else [])
            if outer:
                src0 = (outer[1][0]["path"], outer[1][0]["sha256"])
            elif island:
                src0 = (island["path"], island["sha256"])
            else:
                src0 = ((rbt.get("evidence") or {}).get("path"), (rbt.get("evidence") or {}).get("sha256"))
            ev_rb = {"source_type": "dwg_vector", "path": src0[0], "sha256": src0[1], "handles": handles, "xy": xy(ctr)}
            if r39:
                p = param(r39, "icd")
                if jt is None:
                    not_run.append({"rule_id": "ROD-039", "phase": "C2", "location": rbt["name"],
                                    "reason": "junction type unknown: approach without a class (K23)",
                                    "status_hint": "Not auto-verifiable"})
                elif Ro and p and jt in (p["value"] or {}):
                    icd = round(2 * Ro, 1)
                    rng = p["value"][jt]
                    add("ROD-039", "icd", f"{rbt['name']} ({jt}) ICD {icd:g} m", icd, rng, "in", p,
                        float(rng[0]) - 1e-9 <= icd <= float(rng[1]) + 1e-9,
                        dict(ev_rb, extract=f"{rbt['name']}: outer edge R {Ro:.2f} m ({basis})"
                                            + (f", central island R {Ri:.2f} m" if Ri else "")
                                            + f" -> ICD {icd:g} m; approaches "
                                            + ", ".join(f"{k} {v}" for k, v in info["approach_classes"].items())),
                        jt, {"roundabout": rbt["name"], "outer_basis": basis})
            if r43:
                p = param(r43, "apron_width")
                if apron and p:
                    w = round(apron["R"] - Ri, 2)
                    lo, hi = float(p["value"][0]), float(p["value"][1])
                    add("ROD-043", "apron_width", f"{rbt['name']} apron {w:g} m", w, p["value"], "in", p,
                        lo - tol(p) - 1e-9 <= w <= hi + tol(p) + 1e-9,
                        dict(ev_rb, handles=[apron["handle"]] + ([island["handle"]] if island else []),
                             extract=f"{rbt['name']}: apron ring R {apron['R']:.2f} m around the island R {Ri:.2f} m -> {w:g} m"),
                        None, {"roundabout": rbt["name"]})
                elif p and PHASE_ORDER.get(phase, 0) >= PHASE_ORDER["DD"]:
                    add("ROD-043", "apron_width", f"{rbt['name']} apron not drawn", "not drawn", p["value"], "present", p, False,
                        dict(ev_rb, extract=f"{rbt['name']}: no concentric ring between the central island"
                                            + (f" (R {Ri:.2f} m)" if Ri else "") + f" and the outer edge (R {Ro:.2f} m)"),
                        None, {"roundabout": rbt["name"], "status_override": "Missing information",
                               "issue_type": "missing_information"}, cap=None)
                elif p:
                    not_run.append({"rule_id": "ROD-043", "phase": "C2", "parameter": "apron_width", "location": rbt["name"],
                                    "reason": "apron not drawn; required at DD"})
            # splitter islands and entries
            for poly, r, path, sha in closed:
                cen = (poly.centroid.x, poly.centroid.y)
                d = math.dist(cen, ctr)
                if d > isl_search or poly.contains(g.Point(ctr)) or d <= (Ri or 0) + 1.0:
                    continue
                appr = sorted(legs, key=lambda x: (ang_diff(x["bearing"], bearing(ctr, cen)), x["leg"]))
                if not appr or ang_diff(appr[0]["bearing"], bearing(ctr, cen)) > isl_bearing:
                    continue
                ap_ = appr[0]
                rect = poly.minimum_rotated_rectangle
                cs = list(rect.exterior.coords)
                sides = sorted([math.dist(cs[0], cs[1]), math.dist(cs[1], cs[2])])
                width, length = round(sides[0], 2), round(sides[1], 2)
                info["islands"].append({"approach": ap_["leg"], "handle": r.get("handle"), "width": width, "length": length,
                                        "centroid": xy(cen)})
                ev_i = {"source_type": "dwg_vector", "path": path, "sha256": sha, "handles": [r.get("handle")],
                        "layer": r.get("layer_short"), "xy": xy(cen),
                        "extract": f"{rbt['name']} approach {ap_['road']}: closed island {r.get('handle')} on {r.get('layer_short')}, "
                                   f"area {poly.area:.1f} m², minimum rectangle {width:g} × {length:g} m, centroid "
                                   f"{d:.1f} m from the centre"}
                if r42:
                    pw, pl, pd = param(r42, "width_min"), param(r42, "length_min"), param(r42, "length_desirable")
                    subj = f"{rbt['name']} splitter island on {ap_['road']}"
                    if pw:
                        add("ROD-042", "width_min", f"{subj} width {width:g} m", width, pw["value"], "ge", pw,
                            width >= float(pw["value"]) - tol(pw) - 1e-9, ev_i, None, {"roundabout": rbt["name"]})
                    if pl:
                        okl = length >= float(pl["value"]) - 1e-9
                        add("ROD-042", "length_min", f"{subj} length {length:g} m", length, pl["value"], "ge", pl, okl, ev_i,
                            None, {"roundabout": rbt["name"]})
                        if pd and okl:
                            add("ROD-042", "length_desirable", f"{subj} length {length:g} m", length, pd["value"], "ge", pd,
                                length >= float(pd["value"]) - 1e-9, ev_i, None,
                                {"roundabout": rbt["name"], "severity_override": "Observation"})
                if r40:
                    p = param(r40, "entry_width")
                    if jt is None or not p or jt not in (p["value"] or {}) or not Ro:
                        not_run.append({"rule_id": "ROD-040", "phase": "C2", "location": f"{rbt['name']} {ap_['road']}",
                                        "reason": "junction type unknown (K23)" if jt is None else "no entry-width value"})
                        continue
                    hd = (ap_["bearing"] + 180.0) % 360.0          # heading of an entering vehicle
                    hx, hy = math.cos(math.radians(hd)), math.sin(math.radians(hd))
                    far = Ro + entry_band + 50
                    right = g.Polygon([ctr, (ctr[0] - hx * far, ctr[1] - hy * far),
                                       (ctr[0] - hx * far + hy * far, ctr[1] - hy * far - hx * far),
                                       (ctr[0] + hy * far, ctr[1] - hx * far)])
                    band = g.Point(ctr).buffer(Ro + entry_band, 64).difference(g.Point(ctr).buffer(Ro + ring_tol, 64))
                    zone = band.intersection(right)                  # outside the circulatory edge, entry side
                    near = [ln.intersection(zone) for ln, _ in curb_lines if ln.intersects(zone)]
                    near = [x for x in near if not x.is_empty and x.distance(poly) > 0.05]
                    if not near:
                        not_run.append({"rule_id": "ROD-040", "phase": "C2", "location": f"{rbt['name']} {ap_['road']}",
                                        "reason": "no nearside curb line found at the entry"})
                        continue
                    ew = round(min(x.distance(poly) for x in near), 2)
                    rng = p["value"][jt]
                    info["entries"].append({"approach": ap_["road"], "entry_width": ew})
                    add("ROD-040", "entry_width", f"{rbt['name']} entry from {ap_['road']} ({jt}) {ew:g} m", ew, rng, "in", p,
                        float(rng[0]) - tol(p) - 1e-9 <= ew <= float(rng[1]) + tol(p) + 1e-9,
                        dict(ev_i, extract=f"{rbt['name']} entry from {ap_['road']}: shortest distance from the splitter island "
                                           f"{r.get('handle')} to the nearside curb within {entry_band:g} m outside the outer "
                                           f"edge = {ew:g} m ({jt})"),
                        jt, {"roundabout": rbt["name"]})
            with_isl = {i["approach"] for i in info["islands"]}
            if r40:
                for x in legs:
                    if x["leg"] not in with_isl:
                        not_run.append({"rule_id": "ROD-040", "phase": "C2", "location": f"{rbt['name']} {x['leg']}",
                                        "reason": "entry not bounded by a splitter island"})
            info["legs"] = [{"leg": x["leg"], "bearing": round(x["bearing"], 1)} for x in legs]
            info["approaches_without_island"] = sorted({x["leg"] for x in legs} - with_isl)
            rb_info.append(info)
        # ---------------------------------------------------------------- DD: driveways and sight triangles (DI-7)
        dd = PHASE_ORDER.get(phase, 0) >= PHASE_ORDER["DD"]
        for rid in DD_RULES:
            if rid in rules and not dd:
                not_run.append({"rule_id": rid, "phase": "C2", "reason": f"not required at stage (DD rule; submission phase {phase})"})
        driveways = []
        if dd and arcs and ("ROD-033" in rules or "ROD-034" in rules):
            plot_layers = site_p.get("plot_layers") or []
            plots = [g.Polygon([tuple(q[:2]) for q in r["points"]]).boundary for r, _, _ in recs
                     if plot_layers and layer_match(r, plot_layers) and r.get("closed") and len(r["points"]) >= 3]
            roads = roads_geom
            for arc in arcs:
                if arc["full"] or arc["R"] > dw_max or not (sw_lo <= arc["sweep"] <= sw_hi) or concentric(arc):
                    continue
                if any(math.dist(arc["mid"], j["xy"]) <= search for j in juncs):
                    continue
                if any(math.dist(arc["mid"], r["centre"]) <= ring_max + 10 for r in rbouts):
                    continue
                if plots and min(pl.distance(g.Point(arc["mid"])) for pl in plots) > frontage:
                    continue
                rd = min(roads, key=lambda n: (roads[n].distance(g.Point(arc["mid"])), n)) if roads else None
                driveways.append((arc, rd))
            r33 = rules.get("ROD-033")
            if r33:
                p = param(r33, "driveway_radius")
                for arc, rd in driveways:
                    cls = cls_of.get(rd)
                    val = (p["value"] or {}).get(cls) if p and cls else None
                    if val is None or not applies(r33, cls):
                        not_run.append({"rule_id": "ROD-033", "phase": "C2", "location": arc["handle"],
                                        "reason": f"road {rd} without a class or a driveway value"})
                        continue
                    R = round(arc["R"], 1)
                    add("ROD-033", "driveway_radius", f"driveway return on {rd} R {R:g} m", R, val, "eq", p,
                        abs(R - float(val)) < 1e-6,
                        ev_arc(arc, f"driveway return {arc['type']} {arc['handle']} on {arc['layer']} off {rd} ({cls}): "
                                    f"fitted R {arc['R']:.2f} m, sweep {arc['sweep']:.0f}°"
                                    + ("" if plots else "; plot frontage not checked (site plot layers not set)")),
                        cls, {"road": rd})
            r34 = rules.get("ROD-034")
            if r34:
                obst = []
                pat = re.compile("|".join(terms), re.I) if terms else None
                for carrier, ing, lw in files:
                    if pat is None or carrier not in PLAN_CARRIERS + LANDSCAPE_CARRIERS:
                        continue
                    src = source_of(ing)
                    for b in (ing.get("content") or {}).get("blocks", []):
                        if b.get("space", "model") == "model" and b.get("insert") and \
                                (pat.search(str(b.get("name") or "")) or pat.search(str(b.get("layer") or ""))):
                            obst.append((g.Point(b["insert"][:2]), f"block {b.get('name')} ({b.get('handle')})", src))
                    for r in lw:
                        names = [r.get("layer_short") or ""] + [str(v) for v in r.get("via_block") or []]
                        if len(r.get("points") or []) >= 2 and any(pat.search(n) for n in names):
                            geom = g.Polygon(r["points"]) if r.get("closed") and len(r["points"]) >= 3 else g.LineString(r["points"])
                            obst.append((geom, f"{r['type']} {r.get('handle')} on {r.get('layer_short')}", src))
                has_land = any(c in LANDSCAPE_CARRIERS for c, _, _ in files)
                tri = []
                pi_ = param(r34, "intersection_triangle")
                pd_ = param(r34, "driveway_triangle")
                for j, arc in corners:
                    if arc["pi"] and pi_:
                        tri.append(("intersection_triangle", pi_, j["id"], arc))
                for arc, rd in driveways:
                    if arc["pi"] and pd_:
                        tri.append(("driveway_triangle", pd_, f"driveway on {rd}", arc))
                for pname, p, where, arc in tri:
                    leg = float(p["value"][0])
                    T = triangle(arc["pi"], arc["p0"], arc["p1"], leg, g)
                    hits = [o for o in obst if o[0].intersects(T) and not o[0].touches(T)]
                    add("ROD-034", pname, f"{where} sight triangle {leg:g} × {leg:g} m", len(hits), "no obstruction", "eq", p,
                        not hits,
                        dict(ev_arc(arc, f"{where}: triangle {leg:g} × {leg:g} m from the tangent intersection of curb return "
                                         f"{arc['handle']}; obstructions: "
                                         + (", ".join(h[1] for h in hits[:6]) if hits else "none")
                                         + ("" if has_land else "; no landscape plan given")),
                             xy=xy(arc["pi"])),
                        None, {"obstructions": [h[1] for h in hits[:10]]})
        out = {"tool": {"script": "check_layout.py", "version": __version__}, "mode": "layout", "stage": a.stage,
               "phase": phase, "site": site,
               "params": {k: C2.get(k) for k in sorted(C2) if k != "status"} | {"curb_layers": curb_layers},
               "checks": checks, "passed": passed, "not_run": not_run,
               "corners": {c: dict(sorted(v.items(), key=lambda kv: float(kv[0]))) for c, v in sorted(summary.items())},
               "roundabouts": rb_info, "driveways": len(driveways),
               "counts": {"arcs": len(arcs), "corners": len(corners), "roundabouts": len(rb_info), "checks": len(checks),
                          "fail": sum(1 for c in checks if c["result"] == "fail")}}
        by_rule = {r: sum(1 for c in checks if c["rule_id"] == r and c["result"] == "fail")
                   for r in sorted({c["rule_id"] for c in checks})}
        atomic_write_json(a.out, out)
        emit({"status": "ok", "out": a.out, "phase": phase, **out["counts"], "fail_by_rule": by_rule,
              "not_run": len(not_run)})
        return EXIT_OK
    return main_guard(run)


if __name__ == "__main__":
    sys.exit(main())
