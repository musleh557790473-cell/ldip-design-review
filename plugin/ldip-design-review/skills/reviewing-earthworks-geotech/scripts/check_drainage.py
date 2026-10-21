#!/usr/bin/env python3
"""EWK-003 plot drainage / trapping for skill 13 (SPC-0006 Rev L §4.14.9 + L-a DL-14).

Trapping on one surface (design_grading or finished_grading) via priority-flood.
Frontage and 0.30 m floor checks only on declared finished levels (DD).
MIXED_LEVELS refused for composite road_top + design_grading.

Usage:
  python check_drainage.py --surfaces <surfaces.json> --rules <rules_RDE.json>
      --stage "<stage>" [--plots <plots.dwg.json>] [--site SITE]
      [--finished-levels] [--plot-levels <plot_levels.json>] --out <drainage.json>
"""
__version__ = "0.1.0"

import argparse
import math
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from ewk_common import (EXIT_INPUT, EXIT_OK, EwkError, atomic_write_json, emit, ewk_gate,  # noqa: E402
                        fail_rec, load_params, load_tin, main_guard, param, priority_flood,
                        read_json, rules_index, tin_raster_z)


def _load_plots(plots_path, plot_layers):
    """Closed polylines on plot_layers from a DWG ingestion. Merge duplicates (centroid ±0.1 m)."""
    if not plots_path:
        return []
    try:
        from shapely.geometry import Polygon
        from shapely.ops import unary_union
    except ImportError as exc:
        raise EwkError(3, "shapely is not installed") from exc
    ing = read_json(plots_path)
    layers = set(plot_layers or [])
    polys = []
    for p in (ing.get("content") or {}).get("polylines") or []:
        layer = p.get("layer") or p.get("layer_short") or ""
        if layers and layer not in layers and not any(L in layer for L in layers):
            continue
        verts = p.get("vertices") or []
        if len(verts) < 3:
            continue
        coords = [(float(v[0]), float(v[1])) for v in verts]
        if coords[0] != coords[-1]:
            coords.append(coords[0])
        try:
            poly = Polygon(coords)
        except Exception:
            continue
        if not poly.is_valid or poly.area < 1e-3:
            continue
        polys.append({"handle": p.get("handle"), "layer": layer, "polygon": poly,
                      "centroid": (poly.centroid.x, poly.centroid.y), "area_m2": poly.area})
    # merge duplicates
    merged = []
    for p in sorted(polys, key=lambda x: -x["area_m2"]):
        if any(math.hypot(p["centroid"][0] - m["centroid"][0],
                          p["centroid"][1] - m["centroid"][1]) <= 0.1 for m in merged):
            continue
        merged.append(p)
    return merged


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--surfaces", required=True)
    ap.add_argument("--rules", required=True)
    ap.add_argument("--stage", required=True)
    ap.add_argument("--site", default=None)
    ap.add_argument("--plots", default=None)
    ap.add_argument("--plot-levels", default=None)
    ap.add_argument("--finished-levels", action="store_true",
                    help="Caller declares the grading surface holds finished levels")
    ap.add_argument("--allow-mixed", action="store_true", help="(forbidden) test MIXED_LEVELS")
    ap.add_argument("--composite", action="store_true",
                    help="Attempt road_top+design_grading composite (must error)")
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)

    def run():
        surf_doc = read_json(a.surfaces)
        if a.composite or (surf_doc.get("mixed_levels_forbidden") and a.allow_mixed):
            raise EwkError(EXIT_INPUT, "MIXED_LEVELS")
        if a.composite:
            raise EwkError(EXIT_INPUT, "MIXED_LEVELS")

        rb = read_json(a.rules)
        rules, skipped, stage_map = rules_index(rb)
        params, site_p, _ = load_params(a.site)
        cell_m = float(params.get("cell_m", 1.0))
        trap_depth = float(params.get("trap_depth_m", 0.05))
        trap_area = float(params.get("trap_area_m2", 10.0))
        plot_layers = params.get("plot_layers") or site_p.get("plot_layers") or []

        by_role = {s["role"]: s for s in surf_doc.get("surfaces") or []}
        # Refuse composite of road_top + design for trapping
        if a.composite:
            raise EwkError(EXIT_INPUT, "MIXED_LEVELS")

        surface_role = "finished_grading" if "finished_grading" in by_role else "design_grading"
        design = by_role.get(surface_role)
        checks, passed, not_run = [], [], []
        listed = []

        # Trapping (L-a: active at PD)
        rule, phase, _ = ewk_gate(rules, skipped, "EWK-003", "positive_drainage", stage_map, a.stage)
        if rule is None:
            not_run.append({"rule_id": "EWK-003", "parameter": "positive_drainage", "reason": phase})
        elif design is None:
            not_run.append({"rule_id": "EWK-003", "parameter": "positive_drainage",
                            "reason": "no design_grading / finished_grading surface"})
        else:
            plots = _load_plots(a.plots, plot_layers)
            if a.plots and not plot_layers:
                not_run.append({"rule_id": "EWK-003", "parameter": "positive_drainage",
                                "reason": "plot_layers not set"})
            elif a.plots and not plots:
                not_run.append({"rule_id": "EWK-003", "parameter": "positive_drainage",
                                "reason": "no plot polygons on plot_layers"})
            else:
                tin = load_tin(design["path"])
                grid, e0, n0, e1, n1 = tin_raster_z(tin, cell_m=cell_m)
                deps = priority_flood(grid, trap_depth_m=trap_depth)
                src = {"path": design["path"], "sha256": design["sha256"],
                       "source_type": "script_output"}
                if not plots:
                    # whole-surface depressions (profile); no per-plot finding without plots
                    for d in deps:
                        area = d["n_cells"] * cell_m * cell_m
                        if area < trap_area:
                            continue
                        # centroid of cells
                        cx = e0 + (sum(c[0] for c in d["cells"]) / len(d["cells"]) + 0.5) * cell_m
                        cy = n0 + (sum(c[1] for c in d["cells"]) / len(d["cells"]) + 0.5) * cell_m
                        item = {"area_m2": round(area, 2), "depth_m": round(d["depth_m"], 3),
                                "volume_m3": round(d["volume_m3"] * cell_m * cell_m, 2),
                                "centroid_en": [round(cx, 3), round(cy, 3)], "plot": None}
                        listed.append(item)
                    # Without plots, still list surface depressions as findings at DD/PD if any ≥ trap_area
                    # Spec: per plot. Without plots → not_run for the rule finding path.
                    if not a.plots:
                        not_run.append({"rule_id": "EWK-003", "parameter": "positive_drainage",
                                        "reason": "no plot outlines given"})
                        # keep listed for profile
                    else:
                        pass
                else:
                    try:
                        from shapely.geometry import Point
                    except ImportError as exc:
                        raise EwkError(3, "shapely is not installed") from exc
                    for d in deps:
                        area = d["n_cells"] * cell_m * cell_m
                        # map cells to plots
                        for plot in plots:
                            n_in = 0
                            for (ix, iy) in d["cells"]:
                                px = e0 + (ix + 0.5) * cell_m
                                py = n0 + (iy + 0.5) * cell_m
                                if plot["polygon"].covers(Point(px, py)):
                                    n_in += 1
                            plot_area = n_in * cell_m * cell_m
                            if plot_area >= trap_area:
                                item = {
                                    "area_m2": round(plot_area, 2),
                                    "depth_m": round(d["depth_m"], 3),
                                    "volume_m3": round(d["mean_depth_m"] * plot_area, 2),
                                    "centroid_en": list(plot["centroid"]),
                                    "plot": plot.get("handle"),
                                }
                                listed.append(item)
                                checks.append(fail_rec(
                                    "EWK-003", "positive_drainage",
                                    f"plot {plot.get('handle')} trap {item['area_m2']} m²",
                                    item["depth_m"], trap_depth, "le", "m", src,
                                    status_override="Probable", link_cap="Probable",
                                    xy_pt=item["centroid_en"],
                                ))
                    if not any(c["rule_id"] == "EWK-003" for c in checks):
                        passed.append({"rule_id": "EWK-003", "parameter": "positive_drainage",
                                       "result": "pass", "observed": "no plot traps ≥ threshold"})

        # Frontage (DD + finished levels only)
        rule, phase, _ = ewk_gate(rules, skipped, "EWK-003", "frontage", stage_map, a.stage)
        # frontage is not a real parameter name in rules — use positive_drainage gate at DD only via finished
        if phase == "not required at stage" or rule is None:
            # Explicit: frontage never on rough grading
            if not a.finished_levels and "finished_grading" not in by_role and not a.plot_levels:
                not_run.append({"rule_id": "EWK-003", "parameter": "frontage",
                                "reason": "frontage check requires finished levels declaration"})
            elif rule is None:
                not_run.append({"rule_id": "EWK-003", "parameter": "frontage", "reason": phase})
        else:
            if not a.finished_levels and "finished_grading" not in by_role and not a.plot_levels:
                not_run.append({"rule_id": "EWK-003", "parameter": "frontage",
                                "reason": "frontage check requires finished levels declaration"})
            else:
                not_run.append({"rule_id": "EWK-003", "parameter": "frontage",
                                "reason": "frontage geometry not supplied in this run"})

        # 0.30 m advisory
        rule, phase, _ = ewk_gate(rules, skipped, "EWK-003", "floor_above_street", stage_map, a.stage)
        if rule is None:
            not_run.append({"rule_id": "EWK-003", "parameter": "floor_above_street", "reason": phase})
        elif not a.plot_levels:
            not_run.append({"rule_id": "EWK-003", "parameter": "floor_above_street",
                            "reason": "plot_levels not given"})
        else:
            p = param(rule, "floor_above_street")
            lim = float(p["value"]) if p else 0.30
            # plot_levels JSON: [{handle, floor_z, street_z}]
            levels = read_json(a.plot_levels)
            items = levels if isinstance(levels, list) else levels.get("plots") or []
            src = {"source_type": "script_output", "path": a.plot_levels}
            n_fail = 0
            for it in items:
                dz = float(it["floor_z"]) - float(it["street_z"])
                if dz < lim:
                    n_fail += 1
                    checks.append(fail_rec(
                        "EWK-003", "floor_above_street",
                        f"plot {it.get('handle')} floor−street",
                        round(dz, 3), lim, "ge", "m", src,
                        status_override="Probable",
                        severity_override="Observation",
                    ))
            if n_fail == 0:
                passed.append({"rule_id": "EWK-003", "parameter": "floor_above_street",
                               "result": "pass"})

        out = {
            "tool": {"script": "check_drainage.py", "version": __version__},
            "mode": "drainage",
            "stage": a.stage,
            "checks": checks,
            "passed": passed,
            "not_run": not_run,
            "traps": listed,
        }
        atomic_write_json(a.out, out)
        emit({"status": "ok", "out": a.out, "checks": len(checks), "traps": len(listed),
              "not_run": len(not_run)})
        return EXIT_OK

    return main_guard(run)


if __name__ == "__main__":
    sys.exit(main())
