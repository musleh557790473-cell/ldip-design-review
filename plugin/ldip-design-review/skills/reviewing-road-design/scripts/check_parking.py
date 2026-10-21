#!/usr/bin/env python3
"""Phase C3 parking checks — ROD-059 / 060 / 061 / 062 (SPC-0006 Rev J §4.13G.9–G.10).

Plan bays by angle class; accessible bays with aisle; counts from the plan until the schedule format
is known (R-G1 / R-J2). Slope → phase D without a surface. Typical-section parking width is checked
in check_sections.py 0.3.0 (ROD-059 at PD).

G-1: plan bays are read only from site `parking_layers` in road_class_params.json (R-I3 style, like
curb_layers). If that list is empty or absent, plan parking is not read — each rule is not_run with a
clear GAP reason. Closed polygons must also pass the stall size gate (c3.bay_width_m / bay_length_m)
and bay_area_m2 before counting as a stall. No inventing layer names.

Usage: python check_parking.py --network <network.json> --class <road_class.json>
           --rules <rules_RDE.json> --stage <name> --site <SITE>
           --file plan_geometry=<dwg.json> [--dlus <dlus.json>] [--plots <cad.dwg.json>]
           [--plot-label-source <pls.json>] [--parking-areas <from B2 optional>] --out <parking_checks.json>
Exit: 0 written · 2 input · 3 dependency. No network, no shell calls.
"""
__version__ = "0.2.0"

import argparse
import math
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from c3_common import (ACCESSIBLE_DEFAULT, angle_class, c3_params, fail_rec, gate, load_carrier_files,  # noqa: E402
                       min_rotated_rect, parking_layer_match, parse_file_args, src25_required,
                       stall_size_ok)
from location_common import closed_polygons, plot_polygons, shapely_mods, xy  # noqa: E402
from road_common import (EXIT_OK, atomic_write_json, emit, main_guard, param, read_json, rules_index,  # noqa: E402
                         term_hits, tol)

SIZES = {
    "parallel": {"rule": "ROD-059", "w": 2.5, "L": 6.0, "tier2_L": 6.5},
    "perpendicular": {"rule": "ROD-060", "w": 2.6, "L": 5.5},
    "angle_60": {"rule": "ROD-060", "w": 2.7, "L": 5.8},
}
ACC_SIZES = {
    "perpendicular": (4.2, 6.1),
    "parallel": (3.9, 5.4),
    "angle_60": (5.45, 6.35),
}


def bay_angle_to_curb(poly, curb_lines, g):
    w, L, ang = min_rotated_rect(poly)
    if not curb_lines:
        return ang, "perpendicular" if abs(ang - 90) < 20 else "parallel"
    # angle of nearest curb
    c = poly.centroid
    nearest = min(curb_lines, key=lambda ln: ln.distance(c))
    coords = list(nearest.coords)
    cang = math.degrees(math.atan2(coords[-1][1] - coords[0][1], coords[-1][0] - coords[0][0])) % 180
    delta = abs(ang - cang) % 180
    delta = min(delta, 180 - delta)
    return delta, None


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--network", required=True)
    ap.add_argument("--class", dest="klass", required=True)
    ap.add_argument("--rules", required=True)
    ap.add_argument("--stage", required=True)
    ap.add_argument("--site", required=True, choices=["QRWN", "NMAR", "NRJS"])
    ap.add_argument("--file", action="append", default=[])
    ap.add_argument("--dlus", default=None)
    ap.add_argument("--plots", default=None)
    ap.add_argument("--plot-label-source", default=None)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)

    def run():
        g, _ = shapely_mods()
        c3, site_p, full = c3_params(a.site)
        net = read_json(a.network)
        class_doc = read_json(a.klass)
        rules, skipped, stage_map = rules_index(read_json(a.rules))
        files = load_carrier_files(parse_file_args(a.file))
        checks, not_run, passed = [], [], []

        area_lo, area_hi = (c3.get("bay_area_m2") or [10, 40])[:2]
        ang_classes = c3.get("angle_classes_deg") or {"parallel_max": 20, "angle_lo": 45, "angle_hi": 75, "perp_min": 75}
        acc_terms = c3.get("accessible_terms") or ACCESSIBLE_DEFAULT
        # G-1: only explicit site parking_layers (R-I3). Empty/absent → do not read plan parking.
        park_layers = list(site_p.get("parking_layers") or [])
        commercial_terms = c3.get("commercial_land_uses") or [r"commercial", r"retail"]
        layers_gap_reason = (f"parking layers for {a.site} not set in road_class_params.json "
                             f"[GAP: parking_layers] (G-1; same style as curb_layers / R-I3)")

        plots, dlus_rows = [], []
        if a.plots and a.dlus and a.plot_label_source:
            pls = read_json(a.plot_label_source)
            site_pls = pls.get(a.site) or pls
            plots, _, _ = plot_polygons([read_json(a.plots)], site_pls.get("polygon_layers") or [],
                                        site_pls.get("text_layers") or [])
            dlus_rows = read_json(a.dlus).get("rows") or []
        by_plot = {r["plot_id"].upper(): r for r in dlus_rows}

        # curb lines for angle
        curb_lines = []
        bays = []
        aisles = []
        if park_layers:
            for carrier, path, ing, src in files:
                for p in (ing.get("content") or {}).get("polylines", []):
                    layer = p.get("layer_short") or p.get("layer") or ""
                    if re.search(r"curb|kerb", layer, re.I):
                        verts = [tuple(v[:2]) for v in (p.get("vertices") or [])]
                        if len(verts) >= 2:
                            curb_lines.append(g.LineString(verts))
                for poly, layer, handle in closed_polygons(ing, []):
                    if not parking_layer_match(layer, park_layers):
                        continue
                    w_L = min_rotated_rect(poly)[:2]
                    if not (area_lo - 1e-9 <= poly.area <= area_hi + 1e-9) or not stall_size_ok(w_L[0], w_L[1], c3):
                        # hatched aisle may be flagged separately (still on a parking layer)
                        texts = [t for t in (ing.get("content") or {}).get("texts", [])
                                 if t.get("insert") and poly.intersects(g.Point(t["insert"][:2]))]
                        if any(re.search(r"aisle|hatch", t.get("text", ""), re.I) for t in texts) or re.search(r"aisle", layer, re.I):
                            aisles.append({"poly": poly, "handle": handle, "src": src, "layer": layer})
                        continue
                    delta, _ = bay_angle_to_curb(poly, curb_lines, g)
                    kind = angle_class(delta, ang_classes)
                    # accessible?
                    texts = [t for t in (ing.get("content") or {}).get("texts", [])
                             if t.get("insert") and poly.buffer(0.5).intersects(g.Point(t["insert"][:2]))]
                    blocks = [b for b in (ing.get("content") or {}).get("blocks", [])
                              if b.get("insert") and poly.buffer(0.5).intersects(g.Point(b["insert"][:2]))]
                    labels = [t.get("text", "") for t in texts] + [str(b.get("name") or "") for b in blocks]
                    accessible = any(term_hits(lab, acc_terms) for lab in labels)
                    bays.append({"poly": poly, "handle": handle, "src": src, "layer": layer, "kind": kind,
                                 "accessible": accessible, "labels": labels,
                                 "w_L": w_L, "xy": xy((poly.centroid.x, poly.centroid.y))})

        # also accept pre-baked bays on network for deterministic tests (not plan-derived)
        for tb in net.get("c3_bays") or []:
            bays.append({**tb, "poly": None, "w_L": (tb["width"], tb["length"]),
                         "xy": tb.get("xy") or [0, 0], "src": tb.get("src") or {"source_type": "dwg_vector"},
                         "handle": tb.get("handle"), "labels": tb.get("labels") or []})

        tier2_len_once = False
        perp_advisory_once = False
        er_amb_once = False

        # size checks ROD-059 / 060 / 061
        for bay in bays:
            w, L = bay["w_L"]
            kind = bay["kind"]
            src = {**bay["src"], "source_type": bay["src"].get("source_type") or "dwg_vector"}
            if bay.get("accessible"):
                rule, ph = gate(rules, skipped, "ROD-061", stage_map, a.stage)
                if rule is None:
                    not_run.append({"rule_id": "ROD-061", "phase": "C3", "reason": ph})
                    continue
                req_w, req_L = ACC_SIZES.get(kind, ACC_SIZES["perpendicular"])
                # aisle
                aisle_w = bay.get("aisle_m")
                if aisle_w is None and bay.get("poly") is not None:
                    for aisl in aisles:
                        if bay["poly"].distance(aisl["poly"]) < 0.2 or bay["poly"].touches(aisl["poly"]):
                            aw, al, _ = min_rotated_rect(aisl["poly"])
                            aisle_w = min(aw, al)
                            break
                ok = w + 1e-9 >= req_w and L + 1e-9 >= req_L and (aisle_w is None or aisle_w + 1e-9 >= 1.5)
                obs = {"width": round(w, 3), "length": round(L, 3), "aisle": aisle_w}
                rec = fail_rec("ROD-061", "size", f"accessible bay {bay.get('handle') or bay.get('id')}", obs,
                               {"width": req_w, "length": req_L, "aisle": 1.5}, "ge", "m", src,
                               handles=[bay.get("handle")], xy_pt=bay["xy"],
                               extract=f"accessible {kind} {w:g}×{L:g} aisle {aisle_w}",
                               result="pass" if ok else "fail", mode_tag="c3_parking")
                (passed if ok else checks).append(rec)
                if not er_amb_once:
                    er_amb_once = True
                    checks.append(fail_rec("ROD-061", "tier2_er_ambiguity", "ER accessible-bay size ambiguity",
                                           "p.61 vs p.65", "RFI", "present", None, src,
                                           extract="ER ambiguity once (Observation, RFI candidate, E13)",
                                           mode_tag="c3_parking", note="Observation once per submittal"))
                continue

            if kind == "parallel":
                rule, ph = gate(rules, skipped, "ROD-059", stage_map, a.stage)
                if rule is None:
                    not_run.append({"rule_id": "ROD-059", "phase": "C3", "reason": ph})
                    continue
                p_w = param(rule, "width") or param(rule, "width_min") or {"value": 2.5, "tolerance": {"value": 0}}
                req_w = float(p_w["value"])
                t_w = tol(p_w)
                # width ± width_m (eq with tolerance); length ≥ 6.0
                ok = abs(w - req_w) <= t_w + 1e-9 and L + 1e-9 >= 6.0
                rec = fail_rec("ROD-059", "size", f"parallel bay {bay.get('handle') or bay.get('id')}",
                               {"width": round(w, 3), "length": round(L, 3)}, {"width": req_w, "length": 6.0},
                               "eq", "m", src, handles=[bay.get("handle")], xy_pt=bay["xy"],
                               extract=f"parallel bay {w:g}×{L:g} m (width ± {t_w:g})",
                               result="pass" if ok else "fail", mode_tag="c3_parking")
                rec["tolerance"] = t_w
                (passed if ok else checks).append(rec)
                if L + 1e-9 < 6.5 and not tier2_len_once:
                    tier2_len_once = True
                    checks.append(fail_rec("ROD-059", "tier2_length", f"parallel bay tier-2 length",
                                           round(L, 3), 6.5, "ge", "m", src, handles=[bay.get("handle")],
                                           xy_pt=bay["xy"], extract=f"length {L:g} m < tier-2 6.5 m",
                                           mode_tag="c3_parking"))
            else:
                # perpendicular / angle — only service roads or commercial frontage
                rule, ph = gate(rules, skipped, "ROD-060", stage_map, a.stage)
                if rule is None:
                    not_run.append({"rule_id": "ROD-060", "phase": "C3", "reason": ph})
                    continue
                road_ok = bay.get("on_service_or_commercial")
                if road_ok is None:
                    # classify by nearest road class / plot
                    road_ok = False
                    pt = g.Point(bay["xy"])
                    for name, info in (class_doc.get("roads") or {}).items():
                        if info.get("class") == "service_road":
                            geom = (net.get("roads") or {}).get(name, {}).get("geometry") or []
                            if len(geom) >= 2 and g.LineString([tuple(p[:2]) for p in geom]).distance(pt) < 30:
                                road_ok = True
                    if plots and by_plot:
                        for p in plots:
                            if p["polygon"].distance(pt) < 20:
                                row = by_plot.get(str(p["id"]).upper())
                                if row and any(re.search(t, f"{row.get('level1','')} {row.get('level2','')}", re.I)
                                               for t in commercial_terms):
                                    road_ok = True
                if not road_ok:
                    not_run.append({"rule_id": "ROD-060", "phase": "C3",
                                    "reason": f"{kind} bay {bay.get('handle') or bay.get('id')}: not on service road / commercial frontage"})
                    if not perp_advisory_once:
                        perp_advisory_once = True
                        checks.append(fail_rec("ROD-060", "tier2_parallel_only_advisory",
                                               "perpendicular/angle on-street parking", 1, 0, "eq", None, src,
                                               extract="only parallel on-street parking should be considered (Observation once)",
                                               mode_tag="c3_parking"))
                    continue
                sz = SIZES[kind]
                ok = w + 1e-9 >= sz["w"] and L + 1e-9 >= sz["L"]
                rec = fail_rec("ROD-060", "size", f"{kind} bay {bay.get('handle') or bay.get('id')}",
                               {"width": round(w, 3), "length": round(L, 3)}, {"width": sz["w"], "length": sz["L"]},
                               "ge", "m", src, handles=[bay.get("handle")], xy_pt=bay["xy"],
                               extract=f"{kind} bay {w:g}×{L:g} m", result="pass" if ok else "fail",
                               mode_tag="c3_parking")
                (passed if ok else checks).append(rec)

        if not bays:
            no_bay_reason = layers_gap_reason if not park_layers else "no parking bay element on the plan"
            for rid in ("ROD-059", "ROD-060", "ROD-061"):
                rule, ph = gate(rules, skipped, rid, stage_map, a.stage)
                if rule is None:
                    not_run.append({"rule_id": rid, "phase": "C3", "reason": ph})
                else:
                    not_run.append({"rule_id": rid, "phase": "C3", "reason": no_bay_reason})

        # ROD-062 counts per parking area
        rule, ph = gate(rules, skipped, "ROD-062", stage_map, a.stage)
        if rule is None:
            not_run.append({"rule_id": "ROD-062", "phase": "C3", "reason": ph})
        elif not park_layers and not bays and not (net.get("c3_parking_areas") or []):
            not_run.append({"rule_id": "ROD-062", "phase": "C3", "reason": layers_gap_reason})
            not_run.append({"rule_id": "ROD-062", "parameter": "slope", "phase": "C3",
                            "reason": "bay slope ≤ 2 % not run without a LandXML design surface (phase D)"})
        else:
            areas = net.get("c3_parking_areas") or []
            if not areas:
                # one synthetic area from all bays
                if bays:
                    areas = [{"id": "PA001", "total": len(bays),
                              "accessible": sum(1 for b in bays if b.get("accessible")),
                              "land_use": "residential", "xy": bays[0]["xy"]}]
            if not areas:
                not_run.append({"rule_id": "ROD-062", "phase": "C3",
                                "reason": "no parking area / no bay element"})
            for area in areas:
                total = int(area["total"])
                acc = int(area["accessible"])
                use = (area.get("land_use") or "residential").lower()
                ratio = 1 / 25 if "resid" in use else 1 / 50
                req = max(math.ceil(total * ratio), src25_required(total))
                ok = acc >= req
                src = area.get("src") or {"source_type": "dwg_vector"}
                rec = fail_rec("ROD-062", "accessible_count", f"parking area {area['id']}",
                               {"total": total, "accessible": acc, "required": req}, req, "ge", "bays", src,
                               xy_pt=area.get("xy"), extract=(f"plan counts: {acc}/{total} accessible; "
                               f"required max(ceil({total}×{ratio:g}), SRC-25)={req}; schedule format unknown (R-J2)"),
                               result="pass" if ok else "fail", mode_tag="c3_parking_count")
                (passed if ok else checks).append(rec)
            not_run.append({"rule_id": "ROD-062", "parameter": "slope", "phase": "C3",
                            "reason": "bay slope ≤ 2 % not run without a LandXML design surface (phase D)"})

        atomic_write_json(a.out, {"tool": {"script": "check_parking.py", "version": __version__},
                                  "mode": "c3_parking", "bays": len(bays), "checks": checks,
                                  "passed": passed, "not_run": not_run})
        emit({"status": "ok", "out": a.out, "bays": len(bays), "checks": len(checks),
              "fail": sum(1 for c in checks if c.get("result") == "fail"), "not_run": len(not_run)})
        return EXIT_OK
    return main_guard(run)


if __name__ == "__main__":
    sys.exit(main())
