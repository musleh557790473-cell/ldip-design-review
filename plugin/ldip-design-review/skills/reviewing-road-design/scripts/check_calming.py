#!/usr/bin/env python3
"""Phase C3 calming checks — ROD-044 / 045 / 046 / 047 (SPC-0006 Rev J §4.13G.4–G.6).

Detail dimensions from read_details.py; placement/spacing (advisory Observations); mini-roundabout
diameter and chicane lateral shift (DJ-5 [AGREED]). Bridges not run (R-J4).

Usage: python check_calming.py --details <details.json> --network <network.json> --class <road_class.json>
           --rules <rules_RDE.json> --stage <name> --site <SITE>
           [--file plan_geometry=<dwg.json>] [--file details=<dwg.json>]
           [--landxml <xml.json>] [--criteria <criteria.json>] [--out <calming_checks.json>]
Exit: 0 written · 2 input · 3 dependency. No network, no shell calls.
"""
__version__ = "0.1.0"

import argparse
import math
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from c3_common import (c3_params, fail_rec, gate, grade_at_station, load_carrier_files, merge_calming,  # noqa: E402
                       nearest_road, parse_file_args, phase_of)
from location_common import closed_polygons, shapely_mods, xy  # noqa: E402
from road_common import (EXIT_OK, RoadError, applies, atomic_write_json, emit, main_guard, param,  # noqa: E402
                         read_json, rules_index, source_of, tol)

SPACING = {30: (50, 80), 40: (80, 150)}  # design speed → (min, max) m


def val_of(detail, kind):
    for v in detail.get("values") or []:
        if v["kind"] == kind:
            return v
    return None


def compare_eq(obs, req):
    return abs(float(obs) - float(req)) <= 1e-9


def dim_checks(details, rules, stage_map, stage, skipped):
    checks, not_run, passed = [], [], []
    by_kind = {}
    for d in details:
        by_kind.setdefault(d["kind"], []).append(d)

    specs = [
        ("ROD-044", "speed_hump", [("length", 3.7, "m"), ("height", 75.0, "mm")]),
        ("ROD-045", "speed_table", [("length", 6.0, "m"), ("flat_top", 3.0, "m"), ("height", 75.0, "mm")]),
        ("ROD-046", "raised_intersection", [("height", 75.0, "mm")]),
    ]
    for rid, kind, dims in specs:
        rule, ph = gate(rules, skipped, rid, stage_map, stage)
        if rule is None:
            not_run.append({"rule_id": rid, "phase": "C3", "reason": ph})
            continue
        items = by_kind.get(kind) or []
        if not items:
            not_run.append({"rule_id": rid, "phase": "C3", "reason": f"no {kind.replace('_', ' ')} detail"})
            continue
        for d in items:
            src = {**(d.get("source") or {}), "source_type": (d.get("source") or {}).get("source_type") or "dwg_vector"}
            for pname, req, unit in dims:
                p = param(rule, pname) or {"value": req, "unit": unit, "cap": "Confirmed"}
                req_v = float(p["value"]) if not isinstance(p["value"], str) else p["value"]
                v = val_of(d, pname)
                if v is None:
                    not_run.append({"rule_id": rid, "parameter": pname, "reason": f"no {pname} in detail '{d['title']}'"})
                    continue
                obs = v["value"]
                ok = compare_eq(obs, req_v)
                link = "Probable" if v.get("assigned") == "positional" else None
                rec = fail_rec(rid, pname, f"detail '{d['title']}' — {pname}", obs, req_v, "eq", unit, src,
                               assigned=v.get("assigned"), link_cap=link, handles=[v.get("handle"), d.get("title_handle")],
                               extract=f"{d['title']}: {pname}={v.get('raw')} ({v.get('assigned')})",
                               result="pass" if ok else "fail", mode_tag="c3_detail")
                (passed if ok else checks).append(rec)
            # ramp for ROD-046
            if rid == "ROD-046":
                p = param(rule, "ramp") or {"value": "1:20", "unit": "ratio"}
                v = val_of(d, "ramp")
                if v is None:
                    not_run.append({"rule_id": rid, "parameter": "ramp", "reason": f"no ramp in detail '{d['title']}'"})
                else:
                    # steeper than 1:20 fails (ratio > 1/20)
                    req_ratio = 1 / 20
                    obs_ratio = v.get("ratio")
                    if obs_ratio is None and isinstance(v.get("value"), str) and ":" in str(v["value"]):
                        a, b = str(v["value"]).split(":")
                        obs_ratio = float(a) / float(b)
                    ok = obs_ratio is not None and obs_ratio <= req_ratio + 1e-12
                    link = "Probable" if v.get("assigned") == "positional" else None
                    rec = fail_rec(rid, "ramp", f"detail '{d['title']}' — ramp", v.get("value"), p["value"], "le",
                                   "ratio", src, assigned=v.get("assigned"), link_cap=link,
                                   handles=[v.get("handle"), d.get("title_handle")],
                                   extract=f"{d['title']}: ramp={v.get('raw')}", result="pass" if ok else "fail",
                                   mode_tag="c3_detail")
                    (passed if ok else checks).append(rec)
    return checks, not_run, passed, by_kind


def missing_detail_for_devices(devices, by_kind, rules, skipped, stage_map, stage, detail_sources):
    checks, not_run = [], []
    mapping = {"speed_hump": "ROD-044", "speed_table": "ROD-045", "raised_intersection": "ROD-046"}
    phase = phase_of(stage_map, stage)
    searched = [s.get("path") for s in detail_sources if s.get("path")]
    seen_type = set()
    for d in devices:
        rid = mapping.get(d["type"])
        if not rid or d["type"] in seen_type:
            continue
        rule, ph = gate(rules, skipped, rid, stage_map, stage)
        if rule is None:
            not_run.append({"rule_id": rid, "phase": "C3", "reason": ph})
            continue
        kind = d["type"]
        if by_kind.get(kind):
            continue
        seen_type.add(kind)
        if phase == "PD":
            not_run.append({"rule_id": rid, "phase": "C3", "reason": "not run at PD: device drawn, no detail of its type"})
            continue
        checks.append(fail_rec(rid, "detail", f"{d['id']} ({kind}) without a detail", 0, 1, "present", None,
                               d.get("src") or {"source_type": "dwg_vector"},
                               status_override="Missing information", issue_type="missing_information",
                               xy_pt=d["xy"], handles=d.get("handles"),
                               extract=f"device {d['id']} drawn; detail sheets searched: {', '.join(searched) or '(none)'}",
                               mode_tag="c3_missing_detail"))
    return checks, not_run


def placement_checks(devices, net, class_doc, rules, skipped, stage_map, stage, landxml, criteria, g):
    checks, not_run, passed = [], [], []
    rule, ph = gate(rules, skipped, "ROD-044", stage_map, stage)
    if rule is None:
        not_run.append({"rule_id": "ROD-044", "phase": "C3", "reason": ph, "topic": "placement"})
        return checks, not_run, passed
    humps = [d for d in devices if d["type"] == "speed_hump"]
    if not humps:
        not_run.append({"rule_id": "ROD-044", "phase": "C3", "reason": "no speed hump devices for placement"})
        return checks, not_run, passed

    # design speed / posted speed
    cls_map = (class_doc or {}).get("roads") or {}
    posted = {}
    if criteria:
        for col in (criteria.get("columns") or criteria.get("classes") or []):
            pass
        # criteria.json from read_criteria: values by class
        for row in (criteria.get("rows") or []):
            if row.get("criterion") == "posted_speed":
                for cls, cell in (row.get("by_class") or {}).items():
                    if isinstance(cell, dict) and cell.get("values"):
                        posted[cls] = cell["values"][0]
                    elif isinstance(cell, (int, float)):
                        posted[cls] = cell

    # chainage per road; posted speed ≤ 50 km/h (§4.13G.5)
    by_road = {}
    for d in humps:
        pt = g.Point(d["xy"])
        road, ch, dist = nearest_road(net, pt, g)
        if road is None or dist > 30:
            not_run.append({"rule_id": "ROD-044", "phase": "C3", "reason": f"{d['id']}: not on a road"})
            continue
        info = cls_map.get(road) or {}
        cls = info.get("class")
        if cls and cls != "local_access":
            not_run.append({"rule_id": "ROD-044", "phase": "C3",
                            "reason": f"{d['id']}: placement only on local roads (class {cls})"})
            continue
        ps = posted.get(cls) if cls else None
        if ps is None and criteria:
            for row in (criteria.get("rows") or []):
                if row.get("criterion") == "posted_speed":
                    cell = (row.get("by_class") or {}).get(cls or "local_access")
                    if isinstance(cell, dict) and cell.get("values"):
                        ps = cell["values"][0]
                    elif isinstance(cell, (int, float)):
                        ps = cell
        if ps is not None and float(ps) > 50 + 1e-9:
            not_run.append({"rule_id": "ROD-044", "phase": "C3",
                            "reason": f"{d['id']}: posted speed {ps:g} km/h > 50 — placement not applicable"})
            continue
        by_road.setdefault(road, []).append((ch, d, cls))

    # spacing
    for road, items in by_road.items():
        items.sort(key=lambda t: t[0])
        # design speed from class: local often 30
        speed = 30
        cls = items[0][2] or "local_access"
        # try criteria design_speed
        if criteria:
            for row in (criteria.get("rows") or []):
                if row.get("criterion") == "design_speed":
                    cell = (row.get("by_class") or {}).get(cls)
                    if isinstance(cell, dict) and cell.get("values"):
                        speed = int(cell["values"][0])
                    elif isinstance(cell, (int, float)):
                        speed = int(cell)
        lo, hi = SPACING.get(speed, (50, 80))
        for (c0, d0, _), (c1, d1, _) in zip(items, items[1:]):
            gap = abs(c1 - c0)
            ok = lo - 1e-9 <= gap <= hi + 1e-9
            rec = fail_rec("ROD-044", "tier2_spacing", f"{d0['id']}–{d1['id']} on {road}", gap, f"{lo}–{hi}",
                           "in", "m", d0.get("src") or {"source_type": "dwg_vector"}, cls=cls,
                           xy_pt=d0["xy"], extract=f"hump spacing {gap:g} m at design speed {speed} km/h",
                           result="pass" if ok else "fail", mode_tag="c3_placement",
                           note="advisory placement (Observation)")
            (passed if ok else checks).append(rec)

    # curve: R ≤ 70 or within 20 m of PC/PT of such a curve
    for road, items in by_road.items():
        segs = []
        for name, r in (net.get("roads") or {}).items():
            if name != road:
                continue
            for loc in r.get("locations") or []:
                pass
        # segments from network alignments if present
        for al_name, al in (net.get("alignments") or {}).items():
            if al_name != road and road not in al_name:
                continue
            for seg in al.get("segments") or []:
                if seg.get("type") == "curve" and seg.get("radius") is not None:
                    segs.append(seg)
        # also from road geometry metadata
        for seg in (net.get("roads") or {}).get(road, {}).get("segments") or []:
            if seg.get("type") == "curve" and seg.get("radius") is not None:
                segs.append(seg)
        for ch, d, cls in items:
            bad = False
            for seg in segs:
                R = float(seg["radius"])
                if R > 70:
                    continue
                pts = [tuple(p[:2]) for p in seg.get("points") or []]
                if len(pts) < 2:
                    continue
                line = g.LineString(pts)
                if line.distance(g.Point(d["xy"])) <= 20 + 1e-9:
                    bad = True
                    break
            # synthetic test may pass curve info via device note or network.curves
            for cv in net.get("curves") or []:
                if cv.get("road") == road and float(cv.get("radius", 999)) <= 70:
                    pc, pt = cv.get("pc"), cv.get("pt")
                    if pc and math.dist(d["xy"], pc) <= 20 + 1e-9 or pt and math.dist(d["xy"], pt) <= 20 + 1e-9:
                        bad = True
                    if cv.get("contains") and d["id"] in cv["contains"]:
                        bad = True
            if bad:
                checks.append(fail_rec("ROD-044", "tier2_on_curve", f"{d['id']} on curve R≤70", 1, 0, "eq", None,
                                       d.get("src") or {"source_type": "dwg_vector"}, cls=cls, xy_pt=d["xy"],
                                       extract=f"{d['id']} on or within 20 m of a curve with R ≤ 70 m",
                                       mode_tag="c3_placement", note="advisory placement (Observation)"))
            else:
                passed.append({"rule_id": "ROD-044", "parameter": "tier2_on_curve", "subject": d["id"], "result": "pass"})

    # grade from LandXML design profiles at hump chainage (§4.13G.5); optional hump_grades test hook
    if landxml:
        src_lx = {"source_type": "landxml", "path": (landxml.get("source") or landxml).get("path")
                  if isinstance(landxml.get("source"), dict) else landxml.get("path"),
                  "sha256": (landxml.get("source") or landxml).get("sha256")
                  if isinstance(landxml.get("source"), dict) else landxml.get("sha256")}
        content = landxml.get("content") or {}
        graded = set()
        for prof in content.get("profiles") or []:
            if prof.get("kind") != "design":
                continue
            aname = prof.get("alignment") or ""
            for road, items in by_road.items():
                if aname != road and road.split("(")[0].strip() not in aname and aname not in road:
                    continue
                for ch, d, cls in items:
                    g_pct = grade_at_station(prof, ch)
                    if g_pct is None:
                        continue
                    graded.add(d["id"])
                    if g_pct > 6 + 1e-9:
                        checks.append(fail_rec(
                            "ROD-044", "tier2_grade", f"{d['id']} grade", round(g_pct, 3), 6, "le", "%",
                            {**src_lx, "xy": d["xy"]}, xy_pt=d["xy"],
                            extract=f"design-profile grade {g_pct:g} % at sta {ch:.3f} on {aname} > 6 %",
                            mode_tag="c3_placement", note="advisory placement (Observation)"))
                    else:
                        passed.append({"rule_id": "ROD-044", "parameter": "tier2_grade", "subject": d["id"],
                                       "observed": g_pct, "result": "pass"})
        # legacy / synthetic hook when no profile content
        for item in landxml.get("hump_grades") or []:
            d = next((x for x in humps if x["id"] == item.get("id")), None)
            if d is None or d["id"] in graded:
                continue
            g_pct = float(item.get("grade_pct", 0))
            graded.add(d["id"])
            if g_pct > 6 + 1e-9:
                checks.append(fail_rec(
                    "ROD-044", "tier2_grade", f"{d['id']} grade", g_pct, 6, "le", "%",
                    {**src_lx, "xy": d["xy"]}, xy_pt=d["xy"],
                    extract=f"design-profile grade {g_pct} % > 6 % (hook)",
                    mode_tag="c3_placement", note="advisory placement (Observation)"))
        if not graded and not (content.get("profiles") or landxml.get("hump_grades")):
            not_run.append({"rule_id": "ROD-044", "parameter": "tier2_grade", "phase": "C3",
                            "reason": "LandXML given but no design profile grade at hump stations"})
    else:
        not_run.append({"rule_id": "ROD-044", "parameter": "tier2_grade", "phase": "C3",
                        "reason": "grade check needs LandXML design profile (DD)"})

    not_run.append({"rule_id": "ROD-044", "parameter": "bridge_clearance", "phase": "C3",
                    "reason": "bridges not run: no bridge carrier (R-J4 [GAP])"})
    return checks, not_run, passed


def mini_roundabout_checks(files, net, rules, skipped, stage_map, stage, c3, g):
    checks, not_run, passed = [], [], []
    rule, ph = gate(rules, skipped, "ROD-047", stage_map, stage)
    if rule is None:
        not_run.append({"rule_id": "ROD-047", "phase": "C3", "reason": ph, "topic": "mini_roundabout"})
        return checks, not_run, passed
    max_d = c3.get("mini_rb_max_d_m", 25.0)
    search = c3.get("mini_rb_search_m", 15.0)
    network_rbs = {(tuple(xy(r["centre"])), round(float(r.get("radius") or 0), 2))
                   for r in net.get("roundabouts") or [] if r.get("centre")}
    found = 0
    for carrier, path, ing, src in files:
        for poly, layer, handle in closed_polygons(ing, []):
            # circle-like: area ~ π r^2, or exterior nearly circular
            if poly.area <= 0 or poly.area >= math.pi * (max_d / 2) ** 2 * 1.2:
                continue
            # diameter from minimum rotated rect / bounding
            minx, miny, maxx, maxy = poly.bounds
            d = max(maxx - minx, maxy - miny)
            if d >= max_d or d < 5:
                continue
            c = poly.centroid
            # near a B2 junction that is NOT a network roundabout
            junc = None
            for j in net.get("junctions") or []:
                if math.dist(j["xy"], (c.x, c.y)) <= search:
                    junc = j
                    break
            if junc is None:
                continue
            # skip if this is a network roundabout centre
            if any(math.dist(c_xy, (c.x, c.y)) < 1.0 for c_xy, _ in network_rbs):
                continue
            found += 1
            ok = 12 - 1e-9 <= d <= 20 + 1e-9
            rec = fail_rec("ROD-047", "diameter", f"mini-roundabout at {junc['id']}", round(d, 3), "12–20",
                           "in", "m", {**src, "source_type": "dwg_vector"}, handles=[handle],
                           xy_pt=xy((c.x, c.y)), extract=f"closed ring diameter {d:g} m on layer {layer}",
                           result="pass" if ok else "fail", mode_tag="c3_mini_rb")
            (passed if ok else checks).append(rec)
    if not found:
        not_run.append({"rule_id": "ROD-047", "phase": "C3", "reason": "no mini-roundabout ring found",
                        "topic": "mini_roundabout"})
    return checks, not_run, passed


def chicane_checks(devices, files, net, rules, skipped, stage_map, stage, g):
    checks, not_run, passed = [], [], []
    rule, ph = gate(rules, skipped, "ROD-047", stage_map, stage)
    if rule is None:
        not_run.append({"rule_id": "ROD-047", "phase": "C3", "reason": ph, "topic": "chicane"})
        return checks, not_run, passed
    chicanes = [d for d in devices if d["type"] == "chicane"]
    if not chicanes:
        not_run.append({"rule_id": "ROD-047", "phase": "C3", "reason": "no chicane devices", "topic": "chicane"})
        return checks, not_run, passed

    # curb lines from polylines on curb-ish layers or linework tagged curb
    curb_lines = []
    for carrier, path, ing, src in files:
        for p in (ing.get("content") or {}).get("polylines", []):
            layer = (p.get("layer_short") or p.get("layer") or "")
            if not re_curb(layer) and "CURB" not in layer.upper() and "KERB" not in layer.upper():
                # also accept explicit test layer CURB_L / CURB_R
                if not re.search(r"curb|kerb", layer, re.I):
                    continue
            verts = [tuple(v[:2]) for v in (p.get("vertices") or [])]
            if len(verts) >= 2:
                curb_lines.append((g.LineString(verts), src, p.get("handle"), layer))

    for d in chicanes:
        pt = g.Point(d["xy"])
        road, ch, dist = nearest_road(net, pt, g)
        if road is None:
            not_run.append({"rule_id": "ROD-047", "phase": "C3", "reason": f"{d['id']}: not on a road"})
            continue
        road_geom = (net.get("roads") or {}).get(road, {}).get("geometry") or []
        if len(road_geom) < 2:
            not_run.append({"rule_id": "ROD-047", "phase": "C3", "reason": f"{d['id']}: road has no geometry"})
            continue
        centre = g.LineString([tuple(p[:2]) for p in road_geom])
        measured_any = False
        # sides: left/right of centreline — measure curb lateral shift first (DJ-5)
        for side_name, sign in (("left", 1), ("right", -1)):
            near = [(ln, src, h, ly) for ln, src, h, ly in curb_lines if ln.distance(pt) < 40]
            side_lines = []
            for ln, src, h, ly in near:
                mid = ln.interpolate(0.5, normalized=True)
                proj = centre.project(mid)
                bearing_p = centre.interpolate(min(centre.length, max(0.0, proj)))
                a = centre.interpolate(max(0.0, proj - 1))
                b = centre.interpolate(min(centre.length, proj + 1))
                vx, vy = b.x - a.x, b.y - a.y
                wx, wy = mid.x - bearing_p.x, mid.y - bearing_p.y
                if (vx * wy - vy * wx) * sign > 0:
                    side_lines.append((ln, src, h, ly))
            if not side_lines:
                not_run.append({"rule_id": "ROD-047", "parameter": "chicane_shift", "phase": "C3",
                                "reason": f"{d['id']} {side_name}: no curb line work"})
                continue
            ln, src, h, ly = min(side_lines, key=lambda t: t[0].distance(pt))
            samples = []
            step = max(0.5, ln.length / 80)
            s = 0.0
            while s <= ln.length + 1e-9:
                q = ln.interpolate(s)
                samples.append((centre.project(q), q))
                s += step
            samples.sort()
            ch0 = centre.project(pt)
            before = [q for c, q in samples if ch0 - 40 <= c <= ch0 - 20]
            after = [q for c, q in samples if ch0 + 20 <= c <= ch0 + 40]
            mid = [q for c, q in samples if ch0 - 20 <= c <= ch0 + 20]
            if len(before) < 1 or len(after) < 1 or len(mid) < 1:
                not_run.append({"rule_id": "ROD-047", "parameter": "chicane_shift", "phase": "C3",
                                "reason": f"{d['id']} {side_name}: insufficient curb samples"})
                continue
            b0 = g.Point(sum(p.x for p in before) / len(before), sum(p.y for p in before) / len(before))
            a0 = g.Point(sum(p.x for p in after) / len(after), sum(p.y for p in after) / len(after))
            straight = g.LineString([(b0.x, b0.y), (a0.x, a0.y)])
            shift = max(straight.distance(q) for q in mid)
            measured_any = True
            ok = 0.6 - 1e-9 <= shift <= 0.8 + 1e-9
            rec = fail_rec("ROD-047", "chicane_shift", f"{d['id']} {side_name}", round(shift, 3), "0.6–0.8", "in", "m",
                           {**src, "source_type": "dwg_vector"}, handles=[h], xy_pt=d["xy"],
                           extract=f"chicane lateral shift {shift:g} m ({side_name}, layer {ly})",
                           result="pass" if ok else "fail", mode_tag="c3_chicane")
            (passed if ok else checks).append(rec)
        # fallback only when curb measurement produced no side result (synthetic / incomplete linework)
        if not measured_any and d.get("chicane_shift_m") is not None:
            shift = float(d["chicane_shift_m"])
            src = d.get("src") or {"source_type": "dwg_vector"}
            ok = 0.6 - 1e-9 <= shift <= 0.8 + 1e-9
            rec = fail_rec("ROD-047", "chicane_shift", f"{d['id']}", shift, "0.6–0.8", "in", "m",
                           {**src, "source_type": "dwg_vector"}, handles=d.get("handles"), xy_pt=d["xy"],
                           extract=f"chicane lateral shift {shift:g} m (no measurable curb; pre-measured)",
                           result="pass" if ok else "fail", mode_tag="c3_chicane")
            (passed if ok else checks).append(rec)
    return checks, not_run, passed


def re_curb(layer):
    return bool(re.search(r"curb|kerb", layer or "", re.I))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--details", required=True)
    ap.add_argument("--network", required=True)
    ap.add_argument("--class", dest="klass", required=True)
    ap.add_argument("--rules", required=True)
    ap.add_argument("--stage", required=True)
    ap.add_argument("--site", required=True, choices=["QRWN", "NMAR", "NRJS"])
    ap.add_argument("--file", action="append", default=[])
    ap.add_argument("--landxml", default=None)
    ap.add_argument("--criteria", default=None)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)

    def run():
        g, _ = shapely_mods()
        c3, site_p, _ = c3_params(a.site)
        details_doc = read_json(a.details)
        net = read_json(a.network)
        class_doc = read_json(a.klass)
        rules, skipped, stage_map = rules_index(read_json(a.rules))
        files = load_carrier_files(parse_file_args(a.file))
        landxml = read_json(a.landxml) if a.landxml else None
        criteria = read_json(a.criteria) if a.criteria else None

        checks, not_run, passed, by_kind = dim_checks(details_doc.get("details") or [], rules, stage_map, a.stage, skipped)

        plan_files = [f for f in files if f[0] in ("plan_geometry", "signing_marking_plan", "details")]
        layers = site_p.get("calming_layers")
        devices = merge_calming(plan_files, g, merge_m=(all_params_b2()), layers=layers)
        # allow test devices embedded in network
        for td in net.get("c3_devices") or []:
            devices.append({**td, "point": g.Point(td["xy"]), "src": td.get("src") or {"source_type": "dwg_vector"}})

        m_checks, m_nr = missing_detail_for_devices(devices, by_kind, rules, skipped, stage_map, a.stage,
                                                    details_doc.get("sources") or [])
        checks += m_checks
        not_run += m_nr

        p_checks, p_nr, p_pass = placement_checks(devices, net, class_doc, rules, skipped, stage_map, a.stage,
                                                  landxml, criteria, g)
        checks += p_checks
        not_run += p_nr
        passed += p_pass

        mr_c, mr_n, mr_p = mini_roundabout_checks(plan_files, net, rules, skipped, stage_map, a.stage, c3, g)
        checks += mr_c
        not_run += mr_n
        passed += mr_p

        ch_c, ch_n, ch_p = chicane_checks(devices, plan_files, net, rules, skipped, stage_map, a.stage, g)
        checks += ch_c
        not_run += ch_n
        passed += ch_p

        atomic_write_json(a.out, {"tool": {"script": "check_calming.py", "version": __version__},
                                  "mode": "c3_calming", "devices": [{"id": d["id"], "xy": d["xy"], "type": d["type"],
                                                                     "labels": d.get("labels")} for d in devices],
                                  "checks": checks, "passed": passed, "not_run": not_run})
        emit({"status": "ok", "out": a.out, "devices": len(devices), "checks": len(checks),
              "fail": sum(1 for c in checks if c.get("result") == "fail"), "not_run": len(not_run)})
        return EXIT_OK
    return main_guard(run)


def all_params_b2():
    from location_common import params
    return float((params().get("b2") or {}).get("calming_merge_m", 10.0))


if __name__ == "__main__":
    sys.exit(main())
