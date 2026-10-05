#!/usr/bin/env python3
"""GIS-009 — plot polygons closed and valid, no overlaps, gaps or slivers (SPC-0004 §7; SPC-0006 §4.6.3).

Usage: python check_topology.py --ingestion <a.dwg.json> [...] --rules <rules_GIS.json> --site <SITE>
                                --out <gis009.json>
Plot polygons = closed model-space polylines on the layers of parameter `plot_layers` (per site).
Without it every sub-check is not_run [GAP: layer mapping]; validity is still measured on every
closed polyline and written to `profile_invalid_by_layer` as information only (no finding).
Sub-checks (each needs its own value from the rules file; a missing value → not_run, non-blocking):
  validity  Shapely is_valid on the vertex ring                          → pattern invalid_polygon/<layer>
            polygons with arcs (bulges) are counted as not_checked_bulge (the ingestion keeps vertices only);
            their area (computed with the arcs by skill 1) is used for sliver, their chords for overlap
  closure   open polylines on a plot layer                              → pattern open_polyline/<layer>
  overlap   pairwise intersection area > `overlap_tol`                   → pattern overlap
  sliver    polygon area < `sliver_min_area`                             → pattern sliver/<layer>
  gap       not_run: needs the site boundary and a definition of non-plot areas [GAP]
Exit 0 ran · 2 input error · 3 missing package. No network, no shell calls."""
__version__ = "0.1.0"

import argparse
import collections
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from dwg_geom import (MAX_SAMPLES, InputError, check_out_path, dwg_source, layer_list, load_ingestion,  # noqa: E402
                      load_rules, need_shapely, param, pattern, polygon, write_result)

RULE = "GIS-009"


def _samples(items, layer=None):
    return [{"handles": it["handles"], "layer": it.get("layer", layer), "extract": it["extract"]} for it in items[:MAX_SAMPLES]]


def check_file(ing, plot_layers, overlap_tol, sliver_min):
    from shapely.strtree import STRtree
    from shapely.validation import explain_validity
    src = dwg_source(ing)
    c = ing["content"]
    profile = collections.Counter()
    bulge = collections.Counter()
    pats = []
    invalid = collections.defaultdict(list)
    sliver = collections.defaultdict(list)
    plots = []
    for p in c.get("polylines", []):
        if p["n_vertices"] < 3:
            ok, why = False, "fewer than 3 vertices"
        elif p.get("has_bulge"):
            # arcs are not in the ingestion; validity is not judged on chords. Area (from the arcs) and
            # overlap (on chords) are still checked.
            bulge[p["layer"]] += 1
            g = polygon(p)
            ok, why = True, None
            if not g.is_valid:
                g = g.buffer(0)
        else:
            g = polygon(p)
            ok = g.is_valid
            why = None if ok else explain_validity(g)
        if not ok:
            profile[p["layer"]] += 1
        if plot_layers is None or p["layer"] not in plot_layers:
            continue
        if not ok:
            invalid[p["layer"]].append({"handles": [p["handle"]], "extract": f"{p['handle']}: {why}"})
            continue
        plots.append((p, g))
        if sliver_min is not None and p["area"] < float(sliver_min):
            sliver[p["layer"]].append({"handles": [p["handle"]], "extract": f"{p['handle']}: area {p['area']:.2f} m2"})
    res = {"file": src["path"], "document_no": src["document_no"] or Path(src["path"]).stem,
           "profile_invalid_by_layer": dict(sorted(profile.items())),
           "not_checked_bulge_by_layer": dict(sorted(bulge.items()))}
    if plot_layers is None:
        return res, pats
    res["plot_polygons_checked"] = len(plots)
    for layer in sorted(invalid):
        pats.append(pattern(f"invalid_polygon/{layer}", f"{len(invalid[layer])} plot polygons on layer '{layer}' are not valid "
                            "(self-intersection or degenerate ring)", _samples(invalid[layer], layer), len(invalid[layer]), src))
    opened = {k: v for k, v in c.get("open_polylines_by_layer", {}).items() if k in plot_layers and v}
    for layer in sorted(opened):
        pats.append(pattern(f"open_polyline/{layer}", f"{opened[layer]} open polylines on plot layer '{layer}' (plot not closed)",
                            [{"handles": [], "layer": layer, "extract": f"layer {layer}: {opened[layer]} open polylines"}],
                            opened[layer], src))
    for layer in sorted(sliver):
        pats.append(pattern(f"sliver/{layer}", f"{len(sliver[layer])} plot polygons on layer '{layer}' are smaller than "
                            f"{float(sliver_min):g} m2", _samples(sliver[layer], layer), len(sliver[layer]), src))
    if overlap_tol is not None and plots:
        geoms = [g for _, g in plots]
        tree = STRtree(geoms)
        hits = []
        for i, g in enumerate(geoms):
            for j in tree.query(g, predicate="intersects"):
                j = int(j)
                if j <= i:
                    continue
                a = g.intersection(geoms[j]).area
                if a > float(overlap_tol):
                    pi, pj = plots[i][0], plots[j][0]
                    hits.append({"handles": [pi["handle"], pj["handle"]], "layer": pi["layer"],
                                 "extract": f"{pi['handle']} ({pi['layer']}) ∩ {pj['handle']} ({pj['layer']}) = {a:.2f} m2"})
        if hits:
            pats.append(pattern("overlap", f"{len(hits)} pairs of plot polygons overlap by more than {float(overlap_tol):g} m2",
                                _samples(hits), len(hits), src))
    return res, pats


def run(ingestion_paths, rules_path, site):
    _, rules = load_rules(rules_path)
    ings = [load_ingestion(p) for p in ingestion_paths]  # inputs are checked even when the rule does not run
    rule = rules.get(RULE)
    if rule is None:
        return {"rule_id": RULE, "result": "not_run", "reason": "rule not in the rules file (inactive or not for this agent)",
                "patterns": [], "files": []}
    plot_layers = layer_list(param(rule, "plot_layers", site))
    overlap_tol = param(rule, "overlap_tol")
    sliver_min = param(rule, "sliver_min_area")
    sub = [("gap", "needs the site boundary and a definition of non-plot areas [GAP]")]
    if plot_layers is None:
        sub += [(s, f"plot layers for {site} not in the rules file [GAP: layer mapping]")
                for s in ("closure", "overlap", "sliver", "validity")]
    else:
        if overlap_tol is None:
            sub.append(("overlap", "parameter overlap_tol not set [GAP]"))
        if sliver_min is None:
            sub.append(("sliver", "parameter sliver_min_area not set [GAP]"))
    files, pats = [], []
    for ing in ings:
        f, ps = check_file(ing, plot_layers, overlap_tol, sliver_min)
        files.append(f)
        pats += ps
    return {"rule_id": RULE, "result": "fail" if pats else ("pass" if plot_layers else "not_run"),
            "reason": None if plot_layers else f"plot layers for {site} not in the rules file [GAP: layer mapping]",
            "patterns": pats, "files": files,
            "subchecks_not_run": [{"subcheck": s, "reason": r} for s, r in sorted(set(sub))]}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--ingestion", nargs="+", required=True)
    ap.add_argument("--rules", required=True)
    ap.add_argument("--site", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    miss = need_shapely()
    if miss:
        print(json.dumps({"status": "error", "code": 3, "detail": miss}))
        return 3
    try:
        check_out_path(a.out)
        res = run(a.ingestion, a.rules, a.site)
    except InputError as exc:
        print(json.dumps({"status": "error", "code": 2, "detail": str(exc)}))
        return 2
    res["script"] = {"name": "check_topology.py", "version": __version__}
    write_result(a.out, res)
    print(json.dumps({"status": "ok", "rule_id": RULE, "result": res["result"], "patterns": len(res["patterns"]),
                      "files": len(res.get("files", [])),
                      "subchecks_not_run": [s["subcheck"] for s in res.get("subchecks_not_run", [])], "out": a.out}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
