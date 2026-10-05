#!/usr/bin/env python3
"""GIS-008 — DWG in EPSG:32638, metre units, geometry inside the site envelope (SPC-0004 §7; SPC-0006 §4.6.3).

Usage: python check_crs.py --ingestion <a.dwg.json> [<b.dwg.json> ...] --rules <rules_GIS.json>
                           --site <SITE> --out <gis008.json>
Sub-checks, per drawing (values only from the rules file):
  units    header $INSUNITS = parameter `insunits`                       → pattern insunits_not_metres
  crs      a declared CRS (GEODATA) other than parameter `crs`           → pattern crs_declared_other
           no declared CRS → recorded as `not_declared`, no finding (absence is not a breach)
  envelope model-space vertices, texts and inserts outside parameter `site_envelope` (per site:
           {"bbox": [E0, N0, E1, N1]} or {"polygon": [[E, N], ...]}) grown by `envelope_buffer`
           → pattern outside_site_envelope, one per layer. Without both values → not_run [GAP].
           Extents come from the geometry, not $EXTMIN/$EXTMAX (empty with some converters).
GIS-017 (benchmark distance) is a separate, inactive rule and is not run here.
Exit 0 ran · 2 input error · 3 missing package. No network, no shell calls."""
__version__ = "0.1.0"

import argparse
import collections
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from dwg_geom import (MAX_SAMPLES, InputError, check_out_path, dwg_source, load_ingestion, load_rules,  # noqa: E402
                      model_points, need_shapely, param, pattern, write_result)

RULE = "GIS-008"


def envelope_geom(env, buffer):
    from shapely.geometry import Polygon, box
    if isinstance(env, dict) and env.get("bbox") and len(env["bbox"]) == 4:
        g = box(*map(float, env["bbox"]))
    elif isinstance(env, dict) and env.get("polygon") and len(env["polygon"]) >= 3:
        g = Polygon([tuple(map(float, p)) for p in env["polygon"]])
    else:
        raise InputError("parameter site_envelope must hold 'bbox' [E0, N0, E1, N1] or 'polygon' [[E, N], ...]")
    if not g.is_valid or g.area <= 0:
        raise InputError("parameter site_envelope is not a valid area")
    return g.buffer(float(buffer), join_style=2) if buffer else g


def check_file(ing, rule, site, env_g, out_subchecks):
    src = dwg_source(ing)
    doc = src["document_no"] or Path(src["path"]).stem
    pats = []
    per_file = {"file": src["path"], "document_no": doc}
    # units
    want = param(rule, "insunits")
    got = ing["content"].get("header", {}).get("insunits")
    if want is None:
        per_file["units"] = "not_run"
        out_subchecks.add(("units", "parameter insunits not set [GAP]"))
    elif got != want:
        per_file["units"] = "fail"
        pats.append(pattern("insunits_not_metres", f"Drawing units $INSUNITS = {got}, expected {want} (metres)",
                            [{"handles": [], "extract": f"$INSUNITS={got}"}], 1, src))
    else:
        per_file["units"] = "pass"
    # declared CRS
    crs_want = param(rule, "crs")
    declared = (ing.get("crs") or {}).get("declared")
    if declared is None:
        per_file["crs"] = "not_declared"
    elif crs_want is None:
        per_file["crs"] = "not_run"
        out_subchecks.add(("crs", "parameter crs not set [GAP]"))
    elif str(declared).upper() != str(crs_want).upper():
        per_file["crs"] = "fail"
        pats.append(pattern("crs_declared_other", f"Declared coordinate system {declared}, expected {crs_want}",
                            [{"handles": [], "extract": f"GEODATA={declared}"}], 1, src))
    else:
        per_file["crs"] = "pass"
    # envelope
    if env_g is None:
        per_file["envelope"] = "not_run"
    else:
        import numpy as np
        import shapely
        recs = list(model_points(ing["content"]))
        xs = [r[3][0] for r in recs]
        ys = [r[3][1] for r in recs]
        inside = shapely.intersects_xy(env_g, np.asarray(xs, dtype=float), np.asarray(ys, dtype=float)) if recs else []
        by_layer = collections.OrderedDict()
        seen = set()
        for (kind, layer, handle, (x, y)), ok in zip(recs, inside):
            if ok or (handle, layer) in seen:
                continue
            seen.add((handle, layer))
            by_layer.setdefault(layer, []).append({"handle": handle, "kind": kind, "e": round(x, 3), "n": round(y, 3)})
        per_file["envelope"] = "fail" if by_layer else "pass"
        per_file["geometry_extents"] = ([round(min(xs), 3), round(min(ys), 3), round(max(xs), 3), round(max(ys), 3)]
                                        if xs else None)
        per_file["entities_outside_by_layer"] = {k: len(v) for k, v in sorted(by_layer.items())}
        for layer in sorted(by_layer, key=lambda k: (-len(by_layer[k]), k)):
            ents = by_layer[layer]
            samples = [{"handles": [e["handle"]], "layer": layer,
                        "extract": f"{e['kind']} {e['handle']} at E {e['e']:.3f} N {e['n']:.3f}"} for e in ents[:MAX_SAMPLES]]
            pats.append(pattern(f"outside_site_envelope/{layer}",
                                f"{len(ents)} model-space entities on layer '{layer}' lie outside the {site} site envelope",
                                samples, len(ents), src))
    return per_file, pats


def run(ingestion_paths, rules_path, site):
    _, rules = load_rules(rules_path)
    ings = [load_ingestion(p) for p in ingestion_paths]  # inputs are checked even when the rule does not run
    rule = rules.get(RULE)
    if rule is None:
        return {"rule_id": RULE, "result": "not_run", "reason": "rule not in the rules file (inactive or not for this agent)",
                "patterns": [], "files": []}
    env, buf = param(rule, "site_envelope", site), param(rule, "envelope_buffer")
    subchecks = set()
    env_g = None
    if env is None or buf is None:
        subchecks.add(("envelope", "site envelope coordinates for " + site + " not in the rules file [GAP] (P-S3-44)"
                       if env is None else "parameter envelope_buffer not set [GAP]"))
    else:
        env_g = envelope_geom(env, buf)
    files, pats = [], []
    for ing in ings:
        f, ps = check_file(ing, rule, site, env_g, subchecks)
        files.append(f)
        pats += ps
    return {"rule_id": RULE, "result": "fail" if pats else "pass", "patterns": pats, "files": files,
            "subchecks_not_run": [{"subcheck": s, "reason": r} for s, r in sorted(subchecks)],
            "source": pats[0]["source"] if pats else None}


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
    res["script"] = {"name": "check_crs.py", "version": __version__}
    write_result(a.out, res)
    print(json.dumps({"status": "ok", "rule_id": RULE, "result": res["result"], "patterns": len(res["patterns"]),
                      "files": len(res.get("files", [])), "subchecks_not_run": [s["subcheck"] for s in res.get("subchecks_not_run", [])],
                      "out": a.out}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
