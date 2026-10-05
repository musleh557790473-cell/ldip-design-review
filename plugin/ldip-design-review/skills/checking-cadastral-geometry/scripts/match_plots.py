#!/usr/bin/env python3
"""DLUS ↔ drawing plot matching: GIS-004, 005, 006, 007, 010, 011, 016 (SPC-0004 §7; SPC-0006 §4.6.3).

Usage (review run):
  python match_plots.py --ingestion <a.dwg.json> [...] --dlus <dlus.json> --gis001 <gis001.json>
                        --rules <rules_GIS.json> --site <SITE> --out <match.json>
Usage (owner profiling outside a run — no findings, no rule values; never feed it to build_candidates):
  python match_plots.py --profile --ingestion <a.dwg.json> [...] [--dlus <dlus.json>]
                        --polygon-layers L1 [L2 ...] --text-layers T1 [...] --out <profile.json>

Plot table (needs GIS-005 `plot_label_source` for the site: {"polygon_layers": [...], "text_layers": [...]}):
closed model-space polylines on the polygon layers; a plot ID is the one text on a text layer whose
insert point lies inside the polygon (0 → unlabelled, >1 → ambiguous; both recorded, not matched).
Plot IDs listed by GIS-001 are excluded from every comparison (SPC-0004 §5.6).
Rules, each only when it is in the rules file (not inactive) and its values are set:
  GIS-005 set difference both ways                        → in_dwg_not_dlus, in_dlus_not_dwg
  GIS-006 |DWG area − DLUS Land Area| > tolerance          → area_differs   (tolerance_abs; when
          tolerance_rel is also set, the difference must exceed both)
  GIS-004 layer → Level 2 via `layer_mapping` (per site)  → land_use_differs
  GIS-010 DLUS Land Area < `min_area_by_land_use`[Level 2] → below_min_area
  GIS-011 Σ DLUS Land Area vs `site_boundary` polygon area (per site) > tolerance_abs → site_area_differs
  GIS-007, GIS-016 → not_run (reference cadastre / CMP source not defined [GAP])
Exit 0 ran · 2 input error · 3 missing package. No network, no shell calls."""
__version__ = "0.1.0"

import argparse
import collections
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from dwg_geom import (MAX_SAMPLES, InputError, check_out_path, dwg_source, layer_list, load_ingestion,  # noqa: E402
                      load_rules, need_shapely, norm, param, pattern, polygon, read_json, write_result)

RULES = ("GIS-004", "GIS-005", "GIS-006", "GIS-007", "GIS-010", "GIS-011", "GIS-016")


def pid_key(v):
    return " ".join(str(v).split()).upper() if v is not None else ""


def plot_table(ings, polygon_layers, text_layers):
    """List of plots {id, handle, layer, area, src, ...} plus unlabelled / ambiguous / orphan counts."""
    import numpy as np
    import shapely
    from shapely.strtree import STRtree
    plots, unlabelled, ambiguous, orphans = [], [], [], 0
    for ing in ings:
        src = dwg_source(ing)
        c = ing["content"]
        polys = [p for p in c.get("polylines", []) if p["layer"] in polygon_layers and p["n_vertices"] >= 3]
        texts = [t for t in c.get("texts", []) if t.get("space") == "model" and t["layer"] in text_layers
                 and str(t.get("text", "")).strip()]
        geoms = []
        for p in polys:
            g = polygon(p)
            geoms.append(g if g.is_valid else g.buffer(0))
        hits = collections.defaultdict(list)
        used = set()
        if geoms and texts:
            tree = STRtree(geoms)
            xs = np.asarray([t["insert"][0] for t in texts], dtype=float)
            ys = np.asarray([t["insert"][1] for t in texts], dtype=float)
            pts = shapely.points(xs, ys)
            ti, gi = tree.query(pts, predicate="intersects")
            for t_i, g_i in zip(ti.tolist(), gi.tolist()):
                hits[g_i].append(t_i)
                used.add(t_i)
        orphans += len(texts) - len(used)
        for i, p in enumerate(polys):
            labels = sorted({pid_key(texts[t]["text"]) for t in hits.get(i, [])})
            rec = {"handle": p["handle"], "layer": p["layer"], "area": p["area"], "src": src,
                   "text_handles": sorted(texts[t]["handle"] for t in hits.get(i, []))}
            if not labels:
                unlabelled.append(rec)
            elif len(labels) > 1:
                ambiguous.append(dict(rec, labels=labels))
            else:
                plots.append(dict(rec, id=labels[0]))
    return plots, unlabelled, ambiguous, orphans


def dlus_rows(dlus, excluded):
    ex = {pid_key(x) for x in excluded}
    rows = [r for r in dlus.get("rows", []) if not r.get("street") and r.get("plot_id") not in (None, "")]
    return {pid_key(r["plot_id"]): r for r in rows if pid_key(r["plot_id"]) not in ex}, ex


def dlus_source(dlus):
    s = dict(dlus["source"])
    return s


def dwg_sample(p, extract, row=None):
    s = {"handles": [p["handle"]] + p.get("text_handles", [])[:1], "layer": p["layer"], "plot_id": p.get("id"),
         "extract": extract}
    if row is not None:
        s["cells"] = [row["cells"]["land_area"]] if "land_area" in row.get("cells", {}) else []
    return s


def run(a):
    _, rules = load_rules(a.rules)
    ings = [load_ingestion(p) for p in a.ingestion]  # inputs are checked even when no rule runs
    results = []
    present = {r: rules.get(r) for r in RULES}

    def not_run(rid, reason):
        results.append({"rule_id": rid, "result": "not_run", "reason": reason, "patterns": []})

    for rid in ("GIS-007", "GIS-016"):
        if present[rid] is None:
            not_run(rid, "rule not in the rules file (inactive or not for this agent)")
        else:
            not_run(rid, {"GIS-007": "reference cadastre input not defined [GAP: reference_dataset]",
                          "GIS-016": "CMP reference not defined [GAP: cmp_source]"}[rid])
    dlus = read_json(a.dlus) if a.dlus else None
    if dlus is not None and dlus.get("result", {}).get("result") != "pass" and any(
            i["pattern"] in ("header_not_found", "missing_column") for i in dlus["result"]["issues"]):
        for rid in ("GIS-004", "GIS-005", "GIS-006", "GIS-010", "GIS-011"):
            not_run(rid, "GIS-002 failed: DLUS not readable")
        return {"results": results, "plots": None}
    excluded = read_json(a.gis001).get("excluded_plot_ids", []) if a.gis001 else []
    by_id, ex = dlus_rows(dlus, excluded) if dlus else ({}, set())

    # DLUS-only rules
    r010 = present["GIS-010"]
    mins = param(r010, "min_area_by_land_use")
    if r010 is None:
        not_run("GIS-010", "rule not in the rules file (inactive or not for this agent)")
    elif dlus is None:
        not_run("GIS-010", "no DLUS input")
    elif mins is None:
        not_run("GIS-010", "parameter min_area_by_land_use not set [GAP]")
    else:
        mins_n = {norm(k): float(v) for k, v in mins.items()}
        bad = collections.defaultdict(list)
        for k, r in sorted(by_id.items(), key=lambda kv: (len(kv[0]), kv[0])):
            m = mins_n.get(norm(r.get("level2")))
            if m is not None and r.get("land_area") is not None and r["land_area"] < m:
                bad[r.get("level2")].append({"plot_id": r["plot_id"], "cells": [r["cells"]["land_area"]],
                                             "extract": f"{r['cells']['land_area']}={r['land_area']} ({r.get('level2')}, minimum {m:g})"})
        pats = [pattern(f"below_min_area/{l2}", f"{len(v)} {l2} plots are below the minimum area", v[:MAX_SAMPLES], len(v),
                        dlus_source(dlus)) for l2, v in sorted(bad.items(), key=lambda kv: str(kv[0]))]
        results.append({"rule_id": "GIS-010", "result": "fail" if pats else "pass", "patterns": pats})

    r011 = present["GIS-011"]
    if r011 is None:
        not_run("GIS-011", "rule not in the rules file (inactive or not for this agent)")
    elif dlus is None:
        not_run("GIS-011", "no DLUS input")
    else:
        tol = param(r011, "tolerance_abs")
        bnd = param(r011, "site_boundary", a.site)
        if tol is None or bnd is None:
            not_run("GIS-011", "parameter " + ("tolerance_abs" if tol is None else "site_boundary") + " not set [GAP]")
        else:
            from shapely.geometry import Polygon
            barea = Polygon([tuple(map(float, p)) for p in bnd]).area
            total = sum(r["land_area"] or 0.0 for r in dlus["rows"])
            d = total - barea
            pats = [] if abs(d) <= float(tol) else [pattern(
                "site_area_differs", f"DLUS total {total:,.2f} m2 differs from the site boundary area {barea:,.2f} m2 by {d:+,.2f} m2",
                [{"cells": [], "extract": f"sum(Land Area)={total:.2f}; boundary={barea:.2f}; diff={d:+.2f}"}], 1, dlus_source(dlus))]
            results.append({"rule_id": "GIS-011", "result": "fail" if pats else "pass", "patterns": pats})

    # drawing rules
    r005 = present["GIS-005"]
    pls = param(r005, "plot_label_source", a.site)
    if r005 is None or pls is None:
        reason = ("rule not in the rules file (inactive or not for this agent)" if r005 is None
                  else f"plot label source for {a.site} not set [GAP: plot_label_source]")
        for rid in ("GIS-004", "GIS-005", "GIS-006"):
            if not any(x["rule_id"] == rid for x in results):
                not_run(rid, reason if rid == "GIS-005" else f"needs the GIS-005 plot table: {reason}")
        return {"results": results, "plots": None}
    if dlus is None:
        for rid in ("GIS-004", "GIS-005", "GIS-006"):
            not_run(rid, "no DLUS input")
        return {"results": results, "plots": None}
    plots, unl, amb, orphans = plot_table(ings, set(layer_list(pls.get("polygon_layers")) or []),
                                          set(layer_list(pls.get("text_layers")) or []))
    dwg_by = collections.defaultdict(list)
    for p in plots:
        if p["id"] not in ex:
            dwg_by[p["id"]].append(p)
    summary = {"plots_labelled": len(plots), "unlabelled_polygons": len(unl), "ambiguous_polygons": len(amb),
               "orphan_labels": orphans, "excluded_ids": sorted(ex),
               "dwg_ids_repeated": sorted(k for k, v in dwg_by.items() if len(v) > 1)[:50]}
    key = lambda k: (len(k), k)  # noqa: E731
    # GIS-005
    only_dwg = sorted(set(dwg_by) - set(by_id), key=key)
    only_dlus = sorted(set(by_id) - set(dwg_by), key=key)
    pats = []
    if only_dwg:
        smp = [dwg_sample(dwg_by[k][0], f"plot {k} on layer {dwg_by[k][0]['layer']} ({dwg_by[k][0]['handle']}), not in DLUS")
               for k in only_dwg[:MAX_SAMPLES]]
        pats.append(pattern("in_dwg_not_dlus", f"{len(only_dwg)} plot IDs in the drawing are not in the DLUS", smp,
                            len(only_dwg), dwg_by[only_dwg[0]][0]["src"]))
    if only_dlus:
        smp = [{"plot_id": by_id[k]["plot_id"], "cells": [by_id[k]["cells"]["plot_id"]],
                "extract": f"{by_id[k]['cells']['plot_id']}={by_id[k]['plot_id']} ({by_id[k].get('level2')}), not in the drawing"}
               for k in only_dlus[:MAX_SAMPLES]]
        pats.append(pattern("in_dlus_not_dwg", f"{len(only_dlus)} DLUS plot IDs are not in the drawing", smp, len(only_dlus),
                            dlus_source(dlus)))
    results.append({"rule_id": "GIS-005", "result": "fail" if pats else "pass", "patterns": pats})
    both = sorted(set(dwg_by) & set(by_id), key=key)
    # GIS-006
    r006 = present["GIS-006"]
    t_abs, t_rel = param(r006, "tolerance_abs"), param(r006, "tolerance_rel")
    if r006 is None:
        not_run("GIS-006", "rule not in the rules file (inactive or not for this agent)")
    elif t_abs is None:
        not_run("GIS-006", "parameter tolerance_abs not set [GAP]")
    else:
        bad = []
        for k in both:
            p, r = dwg_by[k][0], by_id[k]
            if r.get("land_area") is None:
                continue
            d = p["area"] - r["land_area"]
            over = abs(d) > float(t_abs) and (t_rel is None or abs(d) > float(t_rel) / 100.0 * abs(r["land_area"]))
            if over:
                bad.append(dwg_sample(p, f"plot {k}: DWG {p['area']:.2f} vs DLUS {r['cells']['land_area']}={r['land_area']:.2f} "
                                         f"({d:+.2f} m2)", r))
        pats = [pattern("area_differs", f"{len(bad)} plots differ in area between the drawing and the DLUS", bad, len(bad),
                        dwg_by[bad[0]["plot_id"]][0]["src"])] if bad else []
        results.append({"rule_id": "GIS-006", "result": "fail" if pats else "pass", "patterns": pats})
    # GIS-004
    r004 = present["GIS-004"]
    lm = param(r004, "layer_mapping", a.site)
    if r004 is None:
        not_run("GIS-004", "rule not in the rules file (inactive or not for this agent)")
    elif lm is None:
        not_run("GIS-004", f"layer mapping for {a.site} not set [GAP: layer_mapping]")
    else:
        bad = []
        for k in both:
            p, r = dwg_by[k][0], by_id[k]
            drawn = lm.get(p["layer"])
            if drawn is None:
                continue
            if norm(drawn) != norm(r.get("level2")):
                bad.append(dwg_sample(p, f"plot {k}: layer {p['layer']} = {drawn}; DLUS {r['cells'].get('level2', '')}={r.get('level2')}", r))
        pats = [pattern("land_use_differs", f"{len(bad)} plots are drawn with a land use other than the DLUS Level 2", bad,
                        len(bad), dwg_by[bad[0]["plot_id"]][0]["src"])] if bad else []
        results.append({"rule_id": "GIS-004", "result": "fail" if pats else "pass", "patterns": pats})
    return {"results": results, "plots": summary}


def profile(a):
    ings = [load_ingestion(p) for p in a.ingestion]
    plots, unl, amb, orphans = plot_table(ings, set(a.polygon_layers), set(a.text_layers))
    out = {"mode": "profile", "note": "owner profiling only — no findings, no rule values",
           "polygon_layers": sorted(a.polygon_layers), "text_layers": sorted(a.text_layers),
           "plots_labelled": len(plots), "unlabelled_polygons": len(unl), "ambiguous_polygons": len(amb),
           "orphan_labels": orphans, "unlabelled_samples": [u["handle"] for u in unl[:MAX_SAMPLES]],
           "ambiguous_samples": [{"handle": x["handle"], "labels": x["labels"][:5]} for x in amb[:MAX_SAMPLES]]}
    if a.dlus:
        dlus = read_json(a.dlus)
        by_id, _ = dlus_rows(dlus, [])
        ids = {p["id"] for p in plots}
        diffs = sorted(round(p["area"] - by_id[p["id"]]["land_area"], 2) for p in plots
                       if p["id"] in by_id and by_id[p["id"]].get("land_area") is not None)
        out.update({"dlus_plots": len(by_id), "matched_ids": len(ids & set(by_id)),
                    "dwg_ids_not_in_dlus": len(ids - set(by_id)), "dlus_ids_not_in_dwg": len(set(by_id) - ids),
                    "area_diff_m2": {"n": len(diffs), "min": diffs[0] if diffs else None, "max": diffs[-1] if diffs else None,
                                     "abs_le_0.01": sum(1 for d in diffs if abs(d) <= 0.01),
                                     "abs_le_0.1": sum(1 for d in diffs if abs(d) <= 0.1),
                                     "abs_le_1": sum(1 for d in diffs if abs(d) <= 1)}})
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--ingestion", nargs="+", required=True)
    ap.add_argument("--dlus")
    ap.add_argument("--gis001")
    ap.add_argument("--rules")
    ap.add_argument("--site")
    ap.add_argument("--out", required=True)
    ap.add_argument("--profile", action="store_true")
    ap.add_argument("--polygon-layers", nargs="+")
    ap.add_argument("--text-layers", nargs="+")
    a = ap.parse_args(argv)
    miss = need_shapely()
    if miss:
        print(json.dumps({"status": "error", "code": 3, "detail": miss}))
        return 3
    try:
        check_out_path(a.out)
        if a.profile:
            if not (a.polygon_layers and a.text_layers):
                raise InputError("--profile needs --polygon-layers and --text-layers")
            res = profile(a)
        else:
            if not (a.rules and a.site):
                raise InputError("--rules and --site are required (or use --profile)")
            res = run(a)
    except InputError as exc:
        print(json.dumps({"status": "error", "code": 2, "detail": str(exc)}))
        return 2
    res["script"] = {"name": "match_plots.py", "version": __version__}
    write_result(a.out, res)
    if a.profile:
        print(json.dumps({"status": "ok", "mode": "profile", "plots_labelled": res["plots_labelled"], "out": a.out}))
    else:
        print(json.dumps({"status": "ok", "results": {r["rule_id"]: r["result"] for r in res["results"]}, "out": a.out}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
