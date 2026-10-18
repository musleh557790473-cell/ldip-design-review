#!/usr/bin/env python3
"""Phase C3 pedestrian checks — ROD-055 crosswalks, ROD-056 refuge islands (SPC-0006 Rev J §4.13G.7–G.8).

Crosswalk width along the road; commercial frontage from DLUS (6.0 m vs 4.0 m); offset from a C2 corner
return tangent point. Refuges: minimum rotated rectangle; tier-2 width Observation once; gradient →
phase D without a surface.

Usage: python check_pedestrian.py --network <network.json> --rules <rules_RDE.json> --stage <name>
           --site <SITE> --file signing_marking_plan=<dwg.json> [--file plan_geometry=<dwg.json>]
           [--corner-returns <c2_returns.json>] [--class <road_class.json>]
           [--plots <cad.dwg.json>] [--dlus <dlus.json>] [--plot-label-source <pls.json>]
           --out <pedestrian_checks.json>
Exit: 0 written · 2 input · 3 dependency. No network, no shell calls.
"""
__version__ = "0.1.0"

import argparse
import math
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from c3_common import (angle_class, c3_params, fail_rec, gate, load_carrier_files, min_rotated_rect,  # noqa: E402
                       parse_file_args)
from location_common import closed_polygons, plot_polygons, shapely_mods, xy  # noqa: E402
from road_common import (EXIT_OK, atomic_write_json, emit, main_guard, param, read_json, rules_index,  # noqa: E402
                         source_of, tol)


def marking_lines(ing, layers):
    g, _ = shapely_mods()
    out = []
    for p in (ing.get("content") or {}).get("polylines", []):
        layer = p.get("layer_short") or p.get("layer") or ""
        if layers and layer not in layers and not re.search(r"mark|zebra|cross", layer, re.I):
            continue
        verts = [tuple(v[:2]) for v in (p.get("vertices") or [])]
        if len(verts) < 2:
            continue
        closed = bool(p.get("closed")) or (len(verts) >= 3 and math.dist(verts[0], verts[-1]) < 0.05)
        geom = g.Polygon(verts) if closed and len(verts) >= 3 else g.LineString(verts)
        out.append((geom, layer, p.get("handle"), bool(closed)))
    return out


def group_zebras(lines, gap_m, g):
    """Group parallel short bars within gap_m across the same road."""
    bars = []
    for geom, layer, handle, closed in lines:
        if closed:
            # hatched outline → use as a single crosswalk band
            bars.append({"geom": geom, "layer": layer, "handle": handle, "kind": "hatch",
                         "angle": None, "centre": geom.centroid})
            continue
        if geom.length > 8:  # long edge line
            bars.append({"geom": geom, "layer": layer, "handle": handle, "kind": "edge",
                         "angle": _line_angle(geom), "centre": geom.centroid})
            continue
        bars.append({"geom": geom, "layer": layer, "handle": handle, "kind": "bar",
                     "angle": _line_angle(geom), "centre": geom.centroid})
    # cluster bars by proximity and parallel angle
    clusters, used = [], set()
    for i, b in enumerate(bars):
        if i in used or b["kind"] == "hatch":
            if b["kind"] == "hatch" and i not in used:
                clusters.append([b])
                used.add(i)
            continue
        if b["kind"] != "bar":
            continue
        cl = [b]
        used.add(i)
        for j, o in enumerate(bars):
            if j in used or o["kind"] != "bar":
                continue
            if b["centre"].distance(o["centre"]) <= gap_m + 3 and _parallel(b["angle"], o["angle"], 15):
                cl.append(o)
                used.add(j)
        if len(cl) >= 2:
            clusters.append(cl)
    # edge-line pairs
    edges = [b for b in bars if b["kind"] == "edge"]
    used_e = set()
    for i, a in enumerate(edges):
        if i in used_e:
            continue
        for j in range(i + 1, len(edges)):
            if j in used_e:
                continue
            if a["centre"].distance(edges[j]["centre"]) < 10 and _parallel(a["angle"], edges[j]["angle"], 15):
                clusters.append([a, edges[j]])
                used_e |= {i, j}
                break
    return clusters


def _line_angle(line):
    coords = list(line.coords)
    return math.degrees(math.atan2(coords[-1][1] - coords[0][1], coords[-1][0] - coords[0][0])) % 180


def _parallel(a, b, tol):
    if a is None or b is None:
        return True
    d = abs(a - b) % 180
    d = min(d, 180 - d)
    return d <= tol


def crosswalk_width(cluster, road_line, g):
    """Extent of the group measured along the road."""
    if len(cluster) == 2 and all(c["kind"] == "edge" for c in cluster):
        # distance between the two edge lines
        return cluster[0]["geom"].distance(cluster[1]["geom"])
    # project centres onto road and take range; for bars, also use envelope along road
    projs = []
    for c in cluster:
        if c["geom"].geom_type == "Polygon":
            for x, y in c["geom"].exterior.coords:
                projs.append(road_line.project(g.Point(x, y)))
        else:
            for x, y in c["geom"].coords:
                projs.append(road_line.project(g.Point(x, y)))
    return max(projs) - min(projs) if projs else 0.0


def commercial_frontage(pt, plots, dlus_rows, commercial_terms, row_half):
    if not plots:
        return False, "no DLUS"
    g, _ = shapely_mods()
    P = g.Point(pt)
    near = [p for p in plots if p["polygon"].distance(P) <= row_half + 3]
    if not near:
        return False, "no DLUS"
    by_id = {r["plot_id"].upper(): r for r in dlus_rows}
    for p in near:
        row = by_id.get(str(p["id"]).upper())
        if not row:
            continue
        blob = f"{row.get('level1', '')} {row.get('level2', '')}".lower()
        if any(re.search(t, blob, re.I) for t in commercial_terms):
            return True, "commercial"
    return False, "general"


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--network", required=True)
    ap.add_argument("--rules", required=True)
    ap.add_argument("--stage", required=True)
    ap.add_argument("--site", required=True, choices=["QRWN", "NMAR", "NRJS"])
    ap.add_argument("--file", action="append", default=[])
    ap.add_argument("--corner-returns", default=None, help="C2 corner returns JSON (optional)")
    ap.add_argument("--class", dest="klass", default=None)
    ap.add_argument("--plots", default=None)
    ap.add_argument("--dlus", default=None)
    ap.add_argument("--plot-label-source", default=None)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)

    def run():
        g, _ = shapely_mods()
        c3, site_p, full = c3_params(a.site)
        net = read_json(a.network)
        rules, skipped, stage_map = rules_index(read_json(a.rules))
        files = load_carrier_files(parse_file_args(a.file))
        class_doc = read_json(a.klass) if a.klass else {"roads": {}}
        returns = (read_json(a.corner_returns).get("returns") if a.corner_returns else
                   net.get("corner_returns") or [])
        checks, not_run, passed = [], [], []

        mark_layers = site_p.get("marking_layers") or c3.get("marking_layers")
        search_m = c3.get("crosswalk_search_m", 30.0)
        gap = c3.get("zebra_gap_m", 1.0)
        commercial_terms = c3.get("commercial_land_uses") or [r"commercial", r"retail", r"shop"]

        # plots / dlus
        plots, dlus_rows = [], []
        if a.plots and a.dlus and a.plot_label_source:
            pls = read_json(a.plot_label_source)
            site_pls = pls.get(a.site) or pls
            plots, _, _ = plot_polygons([read_json(a.plots)], site_pls.get("polygon_layers") or [],
                                        site_pls.get("text_layers") or [])
            dlus_rows = read_json(a.dlus).get("rows") or []

        # ---- ROD-055
        rule, ph = gate(rules, skipped, "ROD-055", stage_map, a.stage)
        if rule is None:
            not_run.append({"rule_id": "ROD-055", "phase": "C3", "reason": ph})
        else:
            mark_files = [f for f in files if f[0] in ("signing_marking_plan", "plan_geometry")]
            all_clusters = []
            for carrier, path, ing, src in mark_files:
                lines = marking_lines(ing, mark_layers)
                for cl in group_zebras(lines, gap, g):
                    all_clusters.append((cl, src))
            # associate with junctions
            n_found = 0
            for cl, src in all_clusters:
                centre = g.MultiPoint([c["centre"] for c in cl]).centroid
                junc = min(net.get("junctions") or [{"id": "J?", "xy": [centre.x, centre.y]}],
                           key=lambda j: math.dist(j["xy"], (centre.x, centre.y)))
                if math.dist(junc["xy"], (centre.x, centre.y)) > search_m:
                    continue
                # road line: first road of junction
                road_name = (junc.get("roads") or [None])[0]
                road = (net.get("roads") or {}).get(road_name) or {}
                geom = road.get("geometry") or []
                if len(geom) < 2:
                    # fabricate short road line through junction
                    road_line = g.LineString([(junc["xy"][0] - 20, junc["xy"][1]),
                                              (junc["xy"][0] + 20, junc["xy"][1])])
                    row_half = 7.5
                else:
                    road_line = g.LineString([tuple(p[:2]) for p in geom])
                    info = (class_doc.get("roads") or {}).get(road_name) or {}
                    row_half = (info.get("width") or 15) / 2
                width = crosswalk_width(cl, road_line, g)
                commercial, note = commercial_frontage((centre.x, centre.y), plots, dlus_rows,
                                                       commercial_terms, row_half)
                req = 6.0 if commercial else 4.0
                p_w = param(rule, "width") or {"value": req, "tolerance": {"value": 0}}
                # commercial overrides the general 4.0 m parameter when frontage is commercial
                req_v = req
                t_w = tol(p_w)
                n_found += 1
                ok = width + t_w + 1e-9 >= req_v
                handles = [c["handle"] for c in cl if c.get("handle")]
                rec = fail_rec("ROD-055", "width", f"crosswalk at {junc['id']}", round(width, 3), req_v, "ge", "m",
                               {**src, "source_type": "dwg_vector"}, handles=handles,
                               xy_pt=xy((centre.x, centre.y)),
                               extract=f"crosswalk width {width:g} m along road ({note}; required {req_v:g} m ± {t_w:g})",
                               result="pass" if ok else "fail", mode_tag="c3_crosswalk")
                if t_w:
                    rec["tolerance"] = t_w
                (passed if ok else checks).append(rec)
                # offset from nearest C2 corner return (near edge of crosswalk → tangent point along road)
                if returns:
                    projs = []
                    for c in cl:
                        if c["geom"].geom_type == "Polygon":
                            projs += [road_line.project(g.Point(x, y)) for x, y in c["geom"].exterior.coords]
                        else:
                            projs += [road_line.project(g.Point(x, y)) for x, y in c["geom"].coords]
                    lo, hi = (min(projs), max(projs)) if projs else (road_line.project(centre),) * 2
                    best = None
                    for r in returns:
                        if r.get("junction") and r["junction"] != junc["id"]:
                            continue
                        tp = r.get("tangent_point") or r.get("xy")
                        if not tp:
                            continue
                        tp_ch = road_line.project(g.Point(tp))
                        d_along = min(abs(tp_ch - lo), abs(tp_ch - hi))
                        if best is None or d_along < best[0]:
                            best = (d_along, tp)
                    if best:
                        off = best[0]
                        ok_o = 2.0 - 1e-9 <= off <= 3.0 + 1e-9
                        rec = fail_rec("ROD-055", "setback", f"crosswalk at {junc['id']} setback", round(off, 3),
                                       "2.0–3.0", "in", "m", {**src, "source_type": "dwg_vector"},
                                       handles=handles, xy_pt=xy((centre.x, centre.y)),
                                       extract=f"offset from radius return {off:g} m",
                                       result="pass" if ok_o else "fail", mode_tag="c3_crosswalk")
                        (passed if ok_o else checks).append(rec)
                else:
                    not_run.append({"rule_id": "ROD-055", "parameter": "setback", "phase": "C3",
                                    "reason": f"crosswalk at {junc['id']}: no C2 corner returns given"})
            if not n_found:
                not_run.append({"rule_id": "ROD-055", "phase": "C3",
                                "reason": "no crosswalk (zebra bars / edge lines) near a junction"})

        # ---- ROD-056: closed outline crossed by a crosswalk, or holding a refuge term
        rule, ph = gate(rules, skipped, "ROD-056", stage_map, a.stage)
        if rule is None:
            not_run.append({"rule_id": "ROD-056", "phase": "C3", "reason": ph})
        else:
            tier2_once = False
            plan_files = [f for f in files if f[0] in ("plan_geometry", "signing_marking_plan", "details")]
            # rebuild crosswalk geometries for the "crossed by" test (§4.13G.8)
            xwalk_geoms = []
            for carrier, path, ing, src in [f for f in files if f[0] in ("signing_marking_plan", "plan_geometry")]:
                for cl in group_zebras(marking_lines(ing, mark_layers), gap, g):
                    geoms = [c["geom"] for c in cl]
                    if len(geoms) == 1:
                        xwalk_geoms.append(geoms[0])
                    else:
                        from shapely.ops import unary_union
                        xwalk_geoms.append(unary_union(geoms))
            found = 0
            p_w = param(rule, "width") or {"value": 2.0, "tolerance": {"value": 0}}
            t_w = tol(p_w)
            for carrier, path, ing, src in plan_files:
                for poly, layer, handle in closed_polygons(ing, []):
                    if poly.area <= 0 or poly.area >= 200:
                        continue
                    blob = layer or ""
                    texts = [t for t in (ing.get("content") or {}).get("texts", [])
                             if t.get("insert") and poly.intersects(g.Point(t["insert"][:2]))]
                    has_term = bool(re.search(r"refuge|ped.*island", blob, re.I)) or any(
                        re.search(r"refuge", t.get("text", ""), re.I) for t in texts)
                    crossed = any(poly.intersects(xw) or poly.distance(xw) < 0.5 for xw in xwalk_geoms)
                    if not has_term and not crossed:
                        continue
                    w, L, _ = min_rotated_rect(poly)
                    found += 1
                    ok_w = w + t_w + 1e-9 >= 2.0
                    ok_l = L + 1e-9 >= 10.0
                    how = "term" if has_term else "crossed by crosswalk"
                    if not ok_w or not ok_l:
                        checks.append(fail_rec("ROD-056", "size", f"refuge {handle}", {"width": round(w, 3), "length": round(L, 3)},
                                               {"width": 2.0, "length": 10.0}, "ge", "m",
                                               {**src, "source_type": "dwg_vector"}, handles=[handle],
                                               xy_pt=xy((poly.centroid.x, poly.centroid.y)),
                                               extract=f"refuge {w:g} × {L:g} m ({how})", mode_tag="c3_refuge"))
                    else:
                        passed.append(fail_rec("ROD-056", "size", f"refuge {handle}", {"width": round(w, 3), "length": round(L, 3)},
                                               {"width": 2.0, "length": 10.0}, "ge", "m",
                                               {**src, "source_type": "dwg_vector"}, handles=[handle],
                                               xy_pt=xy((poly.centroid.x, poly.centroid.y)),
                                               extract=f"refuge {w:g} × {L:g} m ({how})", result="pass", mode_tag="c3_refuge"))
                    if w + 1e-9 < 3.5 and not tier2_once:
                        tier2_once = True
                        checks.append(fail_rec("ROD-056", "tier2_width", f"refuge {handle} tier-2 width", round(w, 3),
                                               3.5, "ge", "m", {**src, "source_type": "dwg_vector"},
                                               handles=[handle], xy_pt=xy((poly.centroid.x, poly.centroid.y)),
                                               extract=f"refuge width {w:g} m < tier-2 3.5 m (Observation once)",
                                               mode_tag="c3_refuge", note="tier-2 Observation once per submittal"))
            if not found:
                not_run.append({"rule_id": "ROD-056", "phase": "C3",
                                "reason": "no refuge island (term or crossed by a crosswalk)"})
            not_run.append({"rule_id": "ROD-056", "parameter": "gradient", "phase": "C3",
                            "reason": "gradient ≤ 2 % not run without a LandXML design surface (phase D)"})

        atomic_write_json(a.out, {"tool": {"script": "check_pedestrian.py", "version": __version__},
                                  "mode": "c3_pedestrian", "checks": checks, "passed": passed, "not_run": not_run})
        emit({"status": "ok", "out": a.out, "checks": len(checks),
              "fail": sum(1 for c in checks if c.get("result") == "fail"), "not_run": len(not_run)})
        return EXIT_OK
    return main_guard(run)


if __name__ == "__main__":
    sys.exit(main())
