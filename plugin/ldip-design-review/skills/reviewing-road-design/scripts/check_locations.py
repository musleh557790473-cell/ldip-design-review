#!/usr/bin/env python3
"""Phase B2: presence rules per location (SPC-0006 Rev F §4.13C.4 - 4.13C.5; DF-6 ... DF-9).

Reads network.json (build_network.py), road_class.json (read_class.py), the rules file (skill 8, agent RDE),
the stage name and the DWG ingestion files of the package with their carrier type. Phase B1 steps 1-3 are
kept (inactive rules are not run; stage gate; accepted carriers at the submission phase — check_presence).
Element hits with a position are taken from the accepted carriers: text records (insert point), block
references (name), and line work of skill 1 whose layer name holds a term of the element (B1 term lists,
road_common.PRESENCE_ELEMENTS). For each location the element is searched within a radius (DF-6,
references/road_class_params.json): 25 m at junctions, 30 m at dead ends and roundabout approaches.
- ROD-101 per road at PD (DF-7): the typical section matching the road's ROW width (section_checks.json)
  must show two sidewalks, or sidewalk line work must lie along both sides of the road; a road where
  neither holds is listed. The report's partial-provision statement stays the B1 package finding (DB-5).
- ROD-115 (DF-8): each dead end needs a turning-area term within 30 m, or a widening of the ROW boundary
  at the end beyond the road's own width (`turning_widening_m`); a declared departure of the design
  report on dead ends (criteria.json `departures`) is quoted and listed for the reviewer, never cancelling.
- Junction elements (ROD-102 curb ramps, ROD-103 tactile tiles, ROD-105 sight triangles, ROD-109 street-
  name signs) at every junction; ROD-104 crossings location-level from DD only (DB-3); ROD-112 splitter
  islands at every roundabout approach.
0.2.0 (SPC-0006 Rev G §4.13D, DG-2 ... DG-5) adds the remaining location lists:
- ROD-107 (DG-2): each low point of low_points.json (find_low_points.py) needs a catch basin / gully / inlet
  hit within `radius_low_point_m` (15 m); sidewalk low points are not checked (no sidewalk profiles, R-G3);
  low points from LandXML keep the rule cap.
- ROD-108 (DG-3): parking areas are closed polylines (content and line work) of the accepted carriers whose
  layer holds a parking term (`parking_area_terms`) or matches the site's `parking_layers`; areas below
  `parking_min_area_m2` (25 m²) and areas inside a larger one are ignored; each area needs an accessible-
  parking hit inside it or within `parking_bay_distance_m` (3 m). The parking schedule is not read (R-G1).
- ROD-111 (DG-4): calming devices are the traffic-calming hits (texts and blocks at their insert point, line
  work at its centroid) of the accepted carriers and the plan / signing carriers, merged within
  `calming_merge_m` (10 m); each device needs a W2-1 / SLOW hit within `radius_calming_m` (30 m).
- ROD-113 (DG-5): plots from the cadastral DWG (--plots) on the GIS-005 `plot_label_source` layers of the site
  (--plot-label-source), joined to the DLUS land use (--dlus, skill 9 read_dlus.py); a plot whose land use
  holds a `frontage_land_uses` term fronts each local_access road within ROW / 2 + `frontage_offset_m` (3 m);
  the frontage is that part of the road ± `frontage_extend_m` (30 m) and needs a calming device on it. A road
  without a class is listed as not run; no plot_label_source -> not_run (R-G2).
Locations without the element -> one check per location (mode `location_<kind>`); build_candidates.py
makes one finding per rule with the count and up to ten locations, plus what was searched (M4, M5).
Positions from drawings cap at Probable (K19-1); a location on a road that cross_check.py marked
inconsistent is flagged `position_uncertain` (DF-9).

Usage: python check_locations.py --network <network.json> --class <road_class.json> --rules <rules_RDE.json>
           --stage "<stage name>" --file <carrier>=<x.dwg.json> [...] [--sections <section_checks.json>]
           [--criteria <criteria.json>] [--cross-check <cross_check.json>] [--low-points <low_points.json>]
           [--plots <cadastral.dwg.json> ...] [--dlus <dlus.json>] [--plot-label-source <rules_GIS.json | source.json>]
           --out <location_checks.json>
Exit: 0 written · 2 input error · 3 shapely missing. No network, no shell calls.
"""
__version__ = "0.2.0"

import argparse
import math
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from road_common import (EXIT_INPUT, EXIT_OK, PRESENCE_ELEMENTS, RoadError, atomic_write_json, emit,  # noqa: E402
                         main_guard, read_json, source_of, term_hits)
from check_presence import ELEMENT_OF, PHASE_ORDER, element_carriers  # noqa: E402
from location_common import closed_polygons, params, plot_polygons, shapely_mods, xy  # noqa: E402

JUNCTION_RULES = ("ROD-102", "ROD-103", "ROD-104", "ROD-105", "ROD-109")
DEAD_END_TERMS = r"dead[\s-]*ends?|cul-?de-?sacs?|turning"


def hits_of(files, element, g):
    """[(geometry, file, kind, text, handle)] — positioned hits of an element in DWG ingestion files."""
    terms = PRESENCE_ELEMENTS[element]["terms"]
    out = []
    for carrier, path, ing, lw in files:
        src = source_of(ing)
        c = ing.get("content") or {}
        for t in c.get("texts", []):
            if t.get("space") != "model" or not t.get("insert"):
                continue
            hs = [h for h in term_hits(t.get("text"), terms) if not h[3]]
            if hs:
                out.append((g.Point(t["insert"][:2]), src, carrier, "text", str(t["text"])[:80], t.get("handle")))
        for b in c.get("blocks", []):
            if b.get("space") == "model" and b.get("insert") and term_hits(b.get("name"), terms):
                out.append((g.Point(b["insert"][:2]), src, carrier, "block", b["name"], b.get("handle")))
        for r in lw:
            if term_hits(r.get("layer_short") or r.get("layer"), terms) and len(r["points"]) >= 2:
                out.append((g.LineString(r["points"]), src, carrier, "layer", r.get("layer_short"), r.get("handle")))
    return out


def plot_label_source(path, site):
    """GIS-005 `plot_label_source` of the site: from a rules file of skill 8 (GIS agent) or a plain JSON value
    ({"polygon_layers": [...], "text_layers": [...]} or per site). None when not set (P-S3-44)."""
    if not path:
        return None
    d = read_json(path)
    if "rules" in d:
        r = next((x for x in d["rules"] if x.get("rule_id") == "GIS-005"), None)
        p = ((r or {}).get("parameters") or {}).get("plot_label_source") or {}
        if p.get("tag") == "GAP":
            return None
        d = p.get("value")
    if isinstance(d, dict) and site in d:
        d = d[site]
    if not isinstance(d, dict) or not d.get("polygon_layers") or not d.get("text_layers"):
        return None
    return {"polygon_layers": list(d["polygon_layers"]), "text_layers": list(d["text_layers"])}


def searched_text(files, carriers):
    parts = [f"{Path(source_of(ing)['path'] or '').name} ({carrier}; {len((ing.get('content') or {}).get('texts', []))} texts, "
             f"{len((ing.get('content') or {}).get('blocks', []))} blocks, {len(lw)} line-work records)"
             for carrier, _, ing, lw in files if carrier in carriers]
    return "; ".join(parts) if parts else "no DWG of the accepted carriers was given"


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--network", required=True)
    ap.add_argument("--class", dest="road_class", required=True)
    ap.add_argument("--rules", required=True)
    ap.add_argument("--stage", required=True)
    ap.add_argument("--file", action="append", default=[])
    ap.add_argument("--sections")
    ap.add_argument("--criteria")
    ap.add_argument("--cross-check")
    ap.add_argument("--low-points")
    ap.add_argument("--plots", nargs="*", default=[])
    ap.add_argument("--dlus")
    ap.add_argument("--plot-label-source")
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)

    def run():
        g, ops = shapely_mods()
        P = params()
        B2, site_p = P["b2"], None
        rb = read_json(a.rules)
        smap = rb.get("stage_map") or {}
        if a.stage not in smap:
            raise RoadError(EXIT_INPUT, f"stage '{a.stage}' not in the rulebook stage_map ({', '.join(smap)})")
        phase = smap[a.stage]["phase"] if isinstance(smap[a.stage], dict) else smap[a.stage]
        net, rc = read_json(a.network), read_json(a.road_class)
        site_p = P["sites"].get(net.get("site"), {})
        files = []
        for spec in a.file:
            if "=" not in spec:
                raise RoadError(EXIT_INPUT, f"--file must be <carrier>=<ingestion.json>: {spec}")
            carrier, path = spec.split("=", 1)
            ing = read_json(path)
            if (ing.get("source") or {}).get("file_type") != "dwg":
                continue                                   # B2 needs positions: DWG only
            lw = []
            for s in (ing.get("content") or {}).get("side_files", []):
                if s.get("kind") == "linework":
                    lw = read_json(Path(path).parent / s["path"]).get("records", [])
            files.append((carrier, path, ing, lw))
        secs = read_json(a.sections) if a.sections else None
        crit = read_json(a.criteria) if a.criteria else None
        xc = read_json(a.cross_check) if a.cross_check else None
        uncertain = {c["subject"].split(":")[0] for c in (xc or {}).get("checks", [])}
        rules = {r["rule_id"]: r for r in rb.get("rules", [])}
        checks, not_run, passed = [], [], []
        for s in rb.get("rules_skipped", []):
            if s.get("rule_id") in ELEMENT_OF:
                not_run.append({"rule_id": s["rule_id"], "reason": s.get("reason", "inactive"), "phase": "B2"})

        def gate(rid):
            rule = rules.get(rid)
            if rule is None:
                return None, None
            if PHASE_ORDER.get(rule.get("required_from_phase") or "PD", 1) > PHASE_ORDER[phase]:
                not_run.append({"rule_id": rid, "reason": "not required at stage", "phase": "B2"})
                return None, None
            carriers, _ = element_carriers(rule, phase)
            if not carriers:
                not_run.append({"rule_id": rid, "reason": "not required at stage", "phase": "B2"})
                return None, None
            return rule, carriers

        def fail(rid, kind, loc_id, roads, pos, observed, searched, extra=None):
            road_note = f" ({' / '.join(roads)})" if roads else ""
            unc = any(r in uncertain for r in roads)
            rule = rules[rid]
            req = ((rule.get("parameters") or {}).get("required_element") or {}).get("value") or rule["title"]
            c = {"rule_id": rid, "parameter": "required_element", "class": None, "result": "fail",
                 "subject": f"{loc_id}{road_note} at E {pos[0]:.1f} N {pos[1]:.1f}", "observed": observed,
                 "required": req, "comparison": "present", "unit": None, "issue_type": "missing_information",
                 "link_cap": "Probable", "mode_tag": f"location_{kind}", "position_uncertain": unc,
                 "evidence": {"source_type": "script_output", "path": None, "sha256": None, "xy": xy(pos),
                              "extract": f"{loc_id}{road_note} at E {pos[0]:.1f} N {pos[1]:.1f}: {observed}. "
                                         f"Searched: {searched}"}}
            if extra:
                c.update(extra)
            checks.append(c)

        # ROD-101 per road (DF-7)
        rule, carriers = gate("ROD-101")
        if rule:
            acc = [f for f in files if f[0] in carriers]
            walk_lines = [h for h in hits_of(acc, "sidewalk", g) if h[3] == "layer"]
            sec_by_w = {}
            for s in (secs or {}).get("sections", []):
                if s.get("title_width_m"):
                    n_walk = sum(1 for e in s.get("elements", []) if e.get("element") == "sidewalk")
                    sec_by_w.setdefault(float(s["title_width_m"]), []).append((s.get("title"), n_walk))
            searched = searched_text(acc, carriers) + (f"; typical sections: {len((secs or {}).get('sections', []))}"
                                                        if secs else "; no typical sections given")
            no_section, n_ok = [], 0
            for name, r in sorted(net["roads"].items()):
                w = (rc["roads"].get(name) or {}).get("width")
                secs_w = sec_by_w.get(w, []) if w else []
                line = ops.unary_union([g.LineString(p) for p in r["geometry"]])
                both = False
                if walk_lines and w:
                    near = ops.unary_union([h[0] for h in walk_lines if h[0].distance(line) <= w / 2 + 1.0])
                    if not near.is_empty:
                        sides = set()
                        for p in r["geometry"]:
                            if len(p) < 2:
                                continue
                            (x0, y0), (x1, y1) = p[0], p[-1]
                            mx, my = (x0 + x1) / 2, (y0 + y1) / 2
                            L = math.dist(p[0], p[-1]) or 1.0
                            nx, ny = -(y1 - y0) / L, (x1 - x0) / L
                            for sg in (1, -1):
                                ray = g.LineString([(mx, my), (mx + sg * nx * (w / 2 + 1), my + sg * ny * (w / 2 + 1))])
                                if ray.intersects(near):
                                    sides.add(sg)
                        both = len(sides) == 2
                if both or any(nw >= 2 for _, nw in secs_w):
                    n_ok += 1
                    continue
                mid = line.interpolate(0.5, normalized=True)
                if not secs_w:
                    no_section.append(name)
                    continue
                fail("ROD-101", "road", name, [], (mid.x, mid.y),
                     f"ROW {w:g} m; matched typical section(s) "
                     + ", ".join(f"'{t}' ({nw} sidewalk(s))" for t, nw in secs_w) + "; no sidewalk line work on both sides",
                     searched)
            if no_section:
                not_run.append({"rule_id": "ROD-101", "phase": "B2", "reason": f"{len(no_section)} road(s) without a typical "
                                "section of their ROW width or without a known width: " + ", ".join(no_section[:15]),
                                "status_hint": "Not auto-verifiable"})
            passed.append({"rule_id": "ROD-101", "phase": "B2", "roads_ok": n_ok})
        # ROD-115 dead ends (DF-8)
        rule, carriers = gate("ROD-115")
        if rule:
            acc = [f for f in files if f[0] in carriers]
            hits = hits_of(acc, "turning_area", g)
            bl = []
            for _, _, _, lw in acc:
                for r in lw:
                    if any(re.fullmatch(p, r.get("layer_short") or "", re.I) for p in site_p.get("row_boundary_layers", [])):
                        bl.append(g.LineString(r["points"]))
            B = ops.unary_union(bl) if bl else None
            deps = [d for d in (crit or {}).get("departures", []) if re.search(DEAD_END_TERMS, d.get("text", ""), re.I)]
            dep_txt = ("; declared departure(s) in the design report: "
                       + "; ".join(f"{d['ref']} p.{d['page']}: {d['text'][:140]}" for d in deps)) if deps else ""
            searched = searched_text(acc, carriers) + f"; terms {', '.join(PRESENCE_ELEMENTS['turning_area']['terms'])}" \
                + ("; ROW boundary widening at the end" if B is not None else "; no ROW boundary line work given")
            ok = 0
            for e in net.get("ends", []):
                if e["type"] != "dead_end":
                    continue
                pos = e["xy"]
                Pt = g.Point(pos)
                if any(h[0].distance(Pt) <= B2["radius_dead_end_m"] for h in hits):
                    ok += 1
                    continue
                widened = None
                w = (rc["roads"].get(e["road"]) or {}).get("width")
                if B is not None and w:
                    r = net["roads"][e["road"]]
                    line = ops.unary_union([g.LineString(p) for p in r["geometry"]])
                    d_end = line.project(Pt)
                    probe = line.interpolate(max(d_end - 5.0, 0) if d_end > line.length / 2 else min(d_end + 5.0, line.length))
                    seg = min((g.LineString(p) for p in r["geometry"]), key=lambda s_: s_.distance(Pt))
                    (x0, y0), (x1, y1) = seg.coords[0], seg.coords[-1]
                    L = math.dist((x0, y0), (x1, y1)) or 1.0
                    nx, ny = -(y1 - y0) / L, (x1 - x0) / L
                    sides = []
                    for sg in (1, -1):
                        ray = g.LineString([(probe.x, probe.y), (probe.x + sg * nx * (w + 25), probe.y + sg * ny * (w + 25))])
                        hit = ray.intersection(B)
                        pts_ = [hit] if hit.geom_type == "Point" else [q for q in getattr(hit, "geoms", []) if q.geom_type == "Point"]
                        sides.append(min(probe.distance(q) for q in pts_) if pts_ else None)
                    widened = round(sum(sides), 2) if None not in sides else None
                if widened is not None and widened >= w + B2["turning_widening_m"]:
                    ok += 1
                    continue
                obs = (f"no turning-area term within {B2['radius_dead_end_m']:g} m and "
                       + (f"ROW {widened:g} m wide 5 m before the end (road {w:g} m)" if widened is not None
                          else "no ROW widening found at the end") + dep_txt
                       + ("" if e.get("exit_checked") else "; site boundary not given: the end may be a site exit (GIS-011 "
                          "site_boundary [GAP])"))
                fail("ROD-115", "dead_end", e["id"], [e["road"]], pos, obs, searched,
                     {"exit_checked": e.get("exit_checked", False), "end_basis": e.get("basis")})
            passed.append({"rule_id": "ROD-115", "phase": "B2", "dead_ends_ok": ok,
                           "departures_quoted": [d["ref"] for d in deps]})
        # junction elements
        for rid in JUNCTION_RULES:
            rule, carriers = gate(rid)
            if not rule:
                continue
            if rid == "ROD-104" and phase == "PD":
                not_run.append({"rule_id": rid, "phase": "B2", "reason": "location level from DD only (DB-3); PD is checked "
                                "at package level in phase B1"})
                continue
            el = ELEMENT_OF[rid]
            acc = [f for f in files if f[0] in carriers]
            hits = hits_of(acc, el, g)
            searched = searched_text(acc, carriers) + f"; terms {', '.join(PRESENCE_ELEMENTS[el]['terms'])}"
            ok = 0
            for j in net.get("junctions", []):
                Pt = g.Point(j["xy"])
                if any(h[0].distance(Pt) <= B2["radius_junction_m"] for h in hits):
                    ok += 1
                    continue
                fail(rid, "junction", j["id"], j["roads"], j["xy"],
                     f"no {el.replace('_', ' ')} within {B2['radius_junction_m']:g} m", searched)
            passed.append({"rule_id": rid, "phase": "B2", "junctions_ok": ok})
        # roundabout approaches
        rule, carriers = gate("ROD-112")
        if rule:
            acc = [f for f in files if f[0] in carriers]
            hits = hits_of(acc, "splitter_island", g)
            searched = searched_text(acc, carriers)
            ok = 0
            for rbt in net.get("roundabouts", []):
                for ap_ in rbt.get("approaches", []):
                    Pt = g.Point(ap_["xy"])
                    if any(h[0].distance(Pt) <= B2["radius_roundabout_m"] for h in hits):
                        ok += 1
                        continue
                    fail("ROD-112", "roundabout_approach", rbt["name"], [ap_["road"]], ap_["xy"],
                         f"no splitter island within {B2['radius_roundabout_m']:g} m of the approach", searched)
            passed.append({"rule_id": "ROD-112", "phase": "B2", "approaches_ok": ok})
        # ---- Rev G §4.13D: remaining location lists (DG-2 ... DG-5)
        def acc_of(carr):
            return [f for f in files if f[0] in carr]

        def merge_devices(carr):
            hs = hits_of(acc_of(carr), "traffic_calming", g)
            pts = sorted([((h[0] if h[0].geom_type == "Point" else h[0].centroid), h) for h in hs],
                         key=lambda t: (round(t[0].x, 3), round(t[0].y, 3), str(t[1][4])))
            clusters = []
            for pt, h in pts:
                c = next((k for k in clusters if k["seed"].distance(pt) <= B2["calming_merge_m"]), None)
                if c is None:
                    c = {"seed": pt, "pts": [], "labels": []}
                    clusters.append(c)
                c["pts"].append(pt)
                if h[4] not in c["labels"]:
                    c["labels"].append(h[4])
            out = []
            for i, c in enumerate(clusters, 1):
                cx = sum(p.x for p in c["pts"]) / len(c["pts"])
                cy = sum(p.y for p in c["pts"]) / len(c["pts"])
                out.append({"id": f"CD{i:03d}", "point": g.Point(cx, cy), "xy": xy((cx, cy)), "hits": len(c["pts"]),
                            "labels": [str(x) for x in c["labels"]][:3]})
            return out, len(hs)

        DEVICE_CARRIERS = {"plan_geometry", "signing_marking_plan"}
        # ROD-107 low points (DG-2)
        rule, carriers = gate("ROD-107")
        if rule:
            if not a.low_points:
                not_run.append({"rule_id": "ROD-107", "phase": "B2", "reason": "no low points: LandXML design profiles not given "
                                "(find_low_points.py)", "status_hint": "Not auto-verifiable"})
            else:
                lp = read_json(a.low_points)
                acc = acc_of(carriers)
                hits = hits_of(acc, "catch_basin", g)
                R = B2["radius_low_point_m"]
                searched = (searched_text(acc, carriers) + f"; terms {', '.join(PRESENCE_ELEMENTS['catch_basin']['terms'])}"
                            + "; sidewalk low points not checked: no sidewalk profiles (R-G3 [GAP])")
                cap = None if lp.get("position_source") == "landxml" else "Probable"
                ok = 0
                for r in lp.get("low_points", []):
                    Pt = g.Point(r["xy"])
                    if any(h[0].distance(Pt) <= R for h in hits):
                        ok += 1
                        continue
                    what = {"sag_curve": "sag-curve low point", "pvi": "low PVI", "dead_end": "low dead end"}.get(r["kind"], "low point")
                    fail("ROD-107", "low_point", f"{r['id']} sta {r['station']:.3f}", [r["alignment"]], r["xy"],
                         f"no catch basin / gully / inlet within {R:g} m of the {what} (elev {r['elevation']:.3f}; {r['detail']})",
                         searched, {"link_cap": cap, "station": r["station"], "low_point_kind": r["kind"]})
                passed.append({"rule_id": "ROD-107", "phase": "B2", "low_points_ok": ok,
                               "low_points": len(lp.get("low_points", [])),
                               "ends_not_used": len(lp.get("ends_not_used", [])),
                               "sidewalk_low_points": "not checked: no sidewalk profiles (R-G3 [GAP])"})
        # ROD-108 parking areas (DG-3)
        rule, carriers = gate("ROD-108")
        if rule:
            acc = acc_of(carriers)
            pterms, plyr = B2["parking_area_terms"], site_p.get("parking_layers", [])
            aterms = PRESENCE_ELEMENTS["accessible_parking"]["terms"]
            areas, bays, small = [], [], 0
            for carrier, path, ing, lw in acc:
                src = source_of(ing)
                for poly, layer, handle in closed_polygons(ing, lw):
                    if term_hits(layer, aterms):
                        bays.append(poly)                       # an accessible bay outline, not a parking area
                        continue
                    if not (term_hits(layer, pterms) or any(re.fullmatch(p_, layer, re.I) for p_ in plyr)):
                        continue
                    if poly.area < B2["parking_min_area_m2"]:
                        small += 1
                        continue
                    areas.append((poly, layer, handle, src.get("path") or ""))
            areas.sort(key=lambda t: (-round(t[0].area, 3), t[3], str(t[2])))
            kept, inside = [], 0
            for t in areas:
                if any(k[0].buffer(0.01).contains(t[0]) for k in kept):
                    inside += 1
                    continue
                kept.append(t)
            bay_hits = [h[0] for h in hits_of(acc, "accessible_parking", g)] + bays
            D = B2["parking_bay_distance_m"]
            searched = (searched_text(acc, carriers) + f"; parking areas: closed polylines on layers with "
                        f"{', '.join(pterms)}" + (f" or {', '.join(plyr)}" if plyr else "")
                        + f" (>= {B2['parking_min_area_m2']:g} m²); accessible terms {', '.join(aterms)}"
                        + "; parking schedule not read (R-G1 [GAP])")
            if not kept:
                not_run.append({"rule_id": "ROD-108", "phase": "B2", "reason": "no parking area on parking layers in the accepted "
                                f"carriers ({small} closed polyline(s) below {B2['parking_min_area_m2']:g} m² ignored)",
                                "status_hint": "Not auto-verifiable"})
            else:
                ok = 0
                for i, (poly, layer, handle, path) in enumerate(kept, 1):
                    if any(b.distance(poly) <= D for b in bay_hits):
                        ok += 1
                        continue
                    c = poly.representative_point()
                    fail("ROD-108", "parking_area", f"PA{i:03d}", [], (c.x, c.y),
                         f"parking area {poly.area:.0f} m² (layer '{layer}', handle {handle}, {Path(path).name}) without an "
                         f"accessible bay inside or within {D:g} m", searched, {"area_m2": round(poly.area, 1)})
                passed.append({"rule_id": "ROD-108", "phase": "B2", "parking_areas_ok": ok, "parking_areas": len(kept),
                               "small_ignored": small, "inside_larger": inside,
                               "schedule": "not read: format unknown (R-G1 [GAP])"})
        # ROD-111 calming devices (DG-4)
        rule, carriers = gate("ROD-111")
        if rule:
            devs, n_hits = merge_devices(carriers | DEVICE_CARRIERS)
            acc = acc_of(carriers)
            signs = hits_of(acc, "calming_sign", g)
            R = B2["radius_calming_m"]
            searched = (searched_text(acc_of(carriers | DEVICE_CARRIERS), carriers | DEVICE_CARRIERS)
                        + f"; devices: {', '.join(PRESENCE_ELEMENTS['traffic_calming']['terms'])} (merged within "
                        f"{B2['calming_merge_m']:g} m); signs: {', '.join(PRESENCE_ELEMENTS['calming_sign']['terms'])}")
            if not devs:
                not_run.append({"rule_id": "ROD-111", "phase": "B2", "reason": "not applicable: no traffic-calming device in the "
                                "searched files", "status_hint": "Not auto-verifiable"})
            else:
                ok = 0
                for d in devs:
                    if any(h[0].distance(d["point"]) <= R for h in signs):
                        ok += 1
                        continue
                    fail("ROD-111", "calming_device", d["id"], [], d["xy"],
                         f"calming device ({'; '.join(d['labels'])}; {d['hits']} hit(s)) without a W2-1 sign or SLOW legend "
                         f"within {R:g} m", searched)
                passed.append({"rule_id": "ROD-111", "phase": "B2", "devices_ok": ok, "devices": len(devs), "device_hits": n_hits})
        # ROD-113 school / playground frontages (DG-5)
        rule, carriers = gate("ROD-113")
        if rule:
            src_lbl = plot_label_source(a.plot_label_source, net.get("site"))
            if src_lbl is None:
                not_run.append({"rule_id": "ROD-113", "phase": "B2", "reason": "GIS-005 plot_label_source not set for the site "
                                "(P-S3-44, R-G2 [GAP]): plots cannot be read", "status_hint": "Not auto-verifiable"})
            elif not a.plots:
                not_run.append({"rule_id": "ROD-113", "phase": "B2", "reason": "no cadastral drawing given (--plots)",
                                "status_hint": "Not auto-verifiable"})
            elif not a.dlus:
                not_run.append({"rule_id": "ROD-113", "phase": "B2", "reason": "DLUS land uses not given (C8)",
                                "status_hint": "Not auto-verifiable"})
            else:
                plots, unl, amb = plot_polygons([read_json(p_) for p_ in a.plots], src_lbl["polygon_layers"], src_lbl["text_layers"])
                dl = read_json(a.dlus)
                lu_of = {" ".join(str(r["plot_id"]).split()).upper(): " / ".join(str(r.get(k)) for k in ("level1", "level2") if r.get(k))
                         for r in dl.get("rows", []) if not r.get("street") and r.get("plot_id") not in (None, "")}
                lu_re = re.compile("|".join(rf"\b{t}\b" for t in B2["frontage_land_uses"]), re.I)
                school = [p_ for p_ in plots if lu_re.search(lu_of.get(p_["id"], ""))]
                devs, _ = merge_devices(carriers | DEVICE_CARRIERS)
                off, ext = B2["frontage_offset_m"], B2["frontage_extend_m"]
                wmax = max(P["class"]["standard_widths_m"])
                searched = (searched_text(acc_of(carriers | DEVICE_CARRIERS), carriers | DEVICE_CARRIERS)
                            + f"; devices: {', '.join(PRESENCE_ELEMENTS['traffic_calming']['terms'])}; plots on "
                            f"{', '.join(src_lbl['polygon_layers'])} with DLUS land use {', '.join(B2['frontage_land_uses'])}")
                ok, unjudged, other = 0, [], 0
                for pl_ in sorted(school, key=lambda p_: p_["id"]):
                    poly = pl_["polygon"]
                    for name in sorted(net["roads"]):
                        rinfo = rc["roads"].get(name) or {}
                        w, cls = rinfo.get("width"), rinfo.get("class")
                        parts = [g.LineString(p_) for p_ in net["roads"][name]["geometry"]]
                        u = ops.unary_union(parts)
                        line = ops.linemerge(u) if u.geom_type == "MultiLineString" else u
                        lines = list(line.geoms) if line.geom_type == "MultiLineString" else [line]
                        reach = (w if w else wmax) / 2 + off
                        near = [ln for ln in lines if ln.distance(poly) <= reach]
                        if not near:
                            continue
                        if cls is None:
                            unjudged.append(f"{pl_['id']} / {name}" + ("" if w else f" (width unknown: within {reach:g} m)"))
                            continue
                        if cls != "local_access":
                            other += 1
                            continue
                        for ln in near:
                            seg = ln.intersection(poly.buffer(reach))
                            cs = [c for gg in (getattr(seg, "geoms", None) or [seg]) for c in gg.coords]
                            if not cs:
                                continue
                            ds = [ln.project(g.Point(c)) for c in cs]
                            front = ops.substring(ln, max(0.0, min(ds) - ext), min(ln.length, max(ds) + ext))
                            if any(d["point"].distance(front) <= w / 2 + 1.0 for d in devs):
                                ok += 1
                                continue
                            m = front.interpolate(0.5, normalized=True)
                            fail("ROD-113", "frontage", f"{pl_['id']} frontage", [name], (m.x, m.y),
                                 f"{lu_of.get(pl_['id'])} plot {pl_['id']} fronts {name} (local_access, ROW {w:g} m) over "
                                 f"{front.length:.0f} m (± {ext:g} m); no traffic-calming device on it", searched,
                                 {"plot_id": pl_["id"], "land_use": lu_of.get(pl_["id"])})
                if unjudged:
                    not_run.append({"rule_id": "ROD-113", "phase": "B2", "reason": f"{len(unjudged)} frontage(s) on roads without a "
                                    "class (RC-2 disagreement or K23) not judged: " + ", ".join(unjudged[:15]),
                                    "status_hint": "Not auto-verifiable"})
                passed.append({"rule_id": "ROD-113", "phase": "B2", "frontages_ok": ok, "school_plots": len(school),
                               "plots": len(plots), "unlabelled_plots": unl, "ambiguous_plots": amb,
                               "frontages_other_class": other})
        out = {"tool": {"script": "check_locations.py", "version": __version__}, "mode": "locations", "stage": a.stage,
               "phase": phase, "radii": {k: B2[k] for k in ("radius_junction_m", "radius_dead_end_m", "radius_roundabout_m", "radius_low_point_m",
                                             "radius_calming_m", "parking_bay_distance_m")},
               "position_source": net.get("position_source", "tables"),
               "checks": checks, "passed": passed, "not_run": not_run}
        atomic_write_json(a.out, out)
        emit({"status": "ok", "out": a.out, "phase": phase, "fail": len(checks),
              "by_rule": {r: sum(1 for c in checks if c["rule_id"] == r) for r in sorted({c["rule_id"] for c in checks})},
              "not_run": len(not_run)})
        return EXIT_OK
    return main_guard(run)


if __name__ == "__main__":
    sys.exit(main())
