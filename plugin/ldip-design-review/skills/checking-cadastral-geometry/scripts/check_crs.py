#!/usr/bin/env python3
"""GIS-008 — CRS, metre units, site envelope, displacement, model sources, control (SPC-0006 §4.18).

Usage: python check_crs.py --ingestion <a.dwg.json> [<b.json> ...] --rules <rules_GIS.json>
                           --site <SITE> --out <gis008.json>
Sub-checks (values only from the rules file):
  C1 units     header $INSUNITS = `insunits`                         → insunits_not_metres
  C2 crs       GEODATA / LandXML CoordinateSystem vs `crs`; none → not_declared, no finding
  C3 envelope  model-space vertices/texts/inserts (expanded blocks incl.) outside
               `site_envelope[SITE]` + `envelope_buffer`; `context_layers[SITE]` → profile only
               → outside_site_envelope/<layer>
  C4 displace  layer groups with ≥ `displacement_min_entities` outside: known vector (≥95 % inside
               after undoing it) → displaced_layer_group/<layer>; else candidate vector (info)
  C5 models    LandXML/IFC design surfaces & alignments → inside test; survey/existing
               (provenance employer, or role/name NGL/EGL) → site boundary inside surface extent
               → outside_site_envelope/<source>, survey_not_covering_site
  C6 control   datum_reference[SITE] points vs site boundary / centroid → control[] (info only)
GIS-017 is check_control.py. Exit 0 ran · 2 input error · 3 missing package. No network."""
__version__ = "0.2.0"

import argparse
import collections
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from dwg_geom import (  # noqa: E402
    MAX_SAMPLES, InputError, boundary_geom, check_out_path, displacement_vectors, envelope_geom,
    is_context_layer, is_survey_surface, layer_list, load_model_file, load_rules, model_points,
    model_source, need_shapely, param, pattern, surface_extent_box, write_result,
)

RULE = "GIS-008"


def _site_boundary(rules, site, env_raw):
    """Prefer GIS-011 site_boundary; else the unbuffered site_envelope polygon/bbox."""
    r011 = rules.get("GIS-011")
    bnd = param(r011, "site_boundary", site) if r011 else None
    if bnd is not None:
        return boundary_geom(bnd)
    if env_raw is None:
        return None
    return envelope_geom(env_raw, 0)


def check_dwg(ing, rule, site, env_g, env_unbuf, context, known, min_ents, out_subchecks):
    src = model_source(ing)
    doc = src["document_no"] or Path(src["path"]).stem
    pats = []
    per_file = {"file": src["path"], "document_no": doc, "file_type": "dwg"}
    # C1 units
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
    # C2 declared CRS
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
    # C3 / C4 envelope + displacement
    profile_context = {}
    candidates = []
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
        for (kind, layer, handle, (x, y), via), ok in zip(recs, inside):
            if ok or (handle, layer) in seen:
                continue
            seen.add((handle, layer))
            ent = {"handle": handle, "kind": kind, "e": round(x, 3), "n": round(y, 3),
                   "layer": layer, "via_block": list(via or [])}
            if is_context_layer(layer, via, context):
                profile_context.setdefault(layer, []).append(ent)
            else:
                by_layer.setdefault(layer, []).append(ent)
        per_file["profile_context_outside"] = {k: len(v) for k, v in sorted(profile_context.items())}
        per_file["geometry_extents"] = ([round(min(xs), 3), round(min(ys), 3), round(max(xs), 3), round(max(ys), 3)]
                                        if xs else None)
        # C4 displacement on non-context outside groups
        displaced_layers = set()
        min_n = int(min_ents) if min_ents is not None else None
        if min_n is not None and by_layer:
            env_w = env_unbuf.bounds[2] - env_unbuf.bounds[0] if env_unbuf is not None else None
            env_h = env_unbuf.bounds[3] - env_unbuf.bounds[1] if env_unbuf is not None else None
            site_c = env_unbuf.centroid if env_unbuf is not None else None
            for layer in list(by_layer):
                ents = by_layer[layer]
                if len(ents) < min_n:
                    continue
                matched = None
                for dE, dN in known:
                    # undo displacement: point - vector
                    xs2 = [e["e"] - dE for e in ents]
                    ys2 = [e["n"] - dN for e in ents]
                    ok2 = shapely.intersects_xy(env_g, np.asarray(xs2, dtype=float), np.asarray(ys2, dtype=float))
                    if float(sum(bool(x) for x in ok2)) / len(ents) >= 0.95:
                        matched = (dE, dN)
                        break
                if matched is not None:
                    displaced_layers.add(layer)
                    dE, dN = matched
                    samples = [{"handles": [e["handle"]], "layer": layer,
                                "extract": (f"{e['kind']} {e['handle']} at E {e['e']:.3f} N {e['n']:.3f}; "
                                            f"known displacement dE={dE:g} dN={dN:g}")}
                               for e in ents[:MAX_SAMPLES]]
                    pats.append(pattern(
                        f"displaced_layer_group/{layer}",
                        (f"{len(ents)} model-space entities on layer '{layer}' lie outside the {site} site envelope "
                         f"and match known displacement ({dE:g}, {dN:g})"),
                        samples, len(ents), src))
                    del by_layer[layer]
                elif env_w is not None and site_c is not None:
                    xs_e = [e["e"] for e in ents]
                    ys_e = [e["n"] for e in ents]
                    gw, gh = max(xs_e) - min(xs_e), max(ys_e) - min(ys_e)
                    if gw <= env_w + 1e-6 and gh <= env_h + 1e-6:
                        cx, cy = sum(xs_e) / len(xs_e), sum(ys_e) / len(ys_e)
                        candidates.append({
                            "layer": layer, "count": len(ents),
                            "candidate_dE": round(cx - site_c.x, 3),
                            "candidate_dN": round(cy - site_c.y, 3),
                        })
        per_file["displacement_candidates"] = candidates
        per_file["entities_outside_by_layer"] = {k: len(v) for k, v in sorted(by_layer.items())}
        per_file["envelope"] = "fail" if by_layer or displaced_layers else "pass"
        for layer in sorted(by_layer, key=lambda k: (-len(by_layer[k]), k)):
            ents = by_layer[layer]
            samples = [{"handles": [e["handle"]], "layer": layer,
                        "extract": f"{e['kind']} {e['handle']} at E {e['e']:.3f} N {e['n']:.3f}"} for e in ents[:MAX_SAMPLES]]
            pats.append(pattern(f"outside_site_envelope/{layer}",
                                f"{len(ents)} model-space entities on layer '{layer}' lie outside the {site} site envelope",
                                samples, len(ents), src))
    return per_file, pats


def _landxml_crs(ing, rule, per_file, pats, src, out_subchecks):
    crs_want = param(rule, "crs")
    cs = (ing.get("content") or {}).get("header", {}).get("coordinate_system") or {}
    declared = cs.get("epsg_code") or cs.get("desc")
    if not declared and (ing.get("crs") or {}).get("declared"):
        declared = ing["crs"]["declared"]
    if declared is None:
        per_file["crs"] = "not_declared"
    elif crs_want is None:
        per_file["crs"] = "not_run"
        out_subchecks.add(("crs", "parameter crs not set [GAP]"))
    elif str(declared).upper() != str(crs_want).upper() and str(crs_want).upper() not in str(declared).upper():
        per_file["crs"] = "fail"
        pats.append(pattern("crs_declared_other", f"Declared coordinate system {declared}, expected {crs_want}",
                            [{"handles": [], "extract": f"CoordinateSystem={declared}"}], 1, src))
    else:
        per_file["crs"] = "pass"


def check_landxml(ing, rule, site, env_g, site_bnd, out_subchecks):
    src = model_source(ing)
    doc = src["document_no"] or Path(src["path"]).stem
    pats = []
    per_file = {"file": src["path"], "document_no": doc, "file_type": "landxml",
                "units": "n/a", "envelope": "not_run"}
    _landxml_crs(ing, rule, per_file, pats, src, out_subchecks)
    if env_g is None and site_bnd is None:
        out_subchecks.add(("model_sources", "site envelope / boundary not in the rules file [GAP]"))
        return per_file, pats
    from shapely.geometry import box, Point
    content = ing.get("content") or {}
    design_outside = []
    survey_issues = []
    # surfaces
    for i, surf in enumerate(content.get("surfaces") or []):
        ext = surface_extent_box(surf.get("extents"))
        label = surf.get("name") or f"surface[{i}]"
        if ext is None:
            continue
        if is_survey_surface(ing, surf):
            if site_bnd is None:
                out_subchecks.add(("survey_coverage", "site_boundary not set for coverage test [GAP]"))
                continue
            if not site_bnd.within(ext.buffer(0.01)):
                survey_issues.append((label, ext))
                pats.append(pattern(
                    "survey_not_covering_site",
                    f"Survey surface '{label}' does not cover the {site} site boundary",
                    [{"handles": [], "extract": f"surface={label}; extents={surf.get('extents')}"}], 1, src))
        else:
            # design: corners of extent box must lie inside envelope
            target = env_g if env_g is not None else site_bnd
            if target is None:
                continue
            corners = list(ext.exterior.coords)[:4]
            bad = [c for c in corners if not Point(c).within(target) and not target.intersects(Point(c))]
            # also accept if extent intersects and centroid inside
            if bad and not (ext.centroid.within(target) and ext.intersects(target)):
                # stricter: any corner outside → finding (C3-like)
                outs = [c for c in corners if not target.contains(Point(c)) and not target.touches(Point(c))]
                # use intersects_xy style: outside if not covered by envelope
                from shapely import intersects
                outs = [c for c in corners if not intersects(target, Point(c))]
                if outs:
                    design_outside.append(label)
                    pats.append(pattern(
                        f"outside_site_envelope/{label}",
                        f"Design surface '{label}' extent lies outside the {site} site envelope",
                        [{"handles": [], "extract": f"surface={label}; outside_corners={len(outs)}"}], 1, src))
    # alignments — sample start/end of elements
    for i, al in enumerate(content.get("alignments") or []):
        pts = []
        for el in al.get("elements") or []:
            for key in ("start", "end", "pi", "center"):
                v = el.get(key)
                if isinstance(v, (list, tuple)) and len(v) >= 2:
                    pts.append((float(v[0]), float(v[1])))
        if not pts:
            continue
        label = al.get("name") or f"alignment[{i}]"
        target = env_g if env_g is not None else site_bnd
        if target is None:
            continue
        import numpy as np
        import shapely
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        ok = shapely.intersects_xy(target, np.asarray(xs, dtype=float), np.asarray(ys, dtype=float))
        n_out = int(sum(1 for x in ok if not x))
        if n_out:
            design_outside.append(label)
            pats.append(pattern(
                f"outside_site_envelope/{label}",
                f"Design alignment '{label}' has {n_out} element points outside the {site} site envelope",
                [{"handles": [], "extract": f"alignment={label}; outside={n_out}"}], n_out, src))
    per_file["envelope"] = "fail" if (design_outside or survey_issues) else "pass"
    per_file["design_outside"] = design_outside
    per_file["survey_not_covering"] = [s[0] for s in survey_issues]
    return per_file, pats


def check_surface_side(side, rule, site, env_g, site_bnd, out_subchecks):
    """IFC / LandXML surface side file — treat as one surface with optional role in doc."""
    src = model_source(side)
    doc = side["doc"]
    pats = []
    name = doc.get("surface") or doc.get("product_name") or Path(side["path"]).stem
    per_file = {"file": src["path"], "document_no": src["document_no"], "file_type": "surface",
                "units": "n/a", "crs": "n/a", "surface": name}
    ext = surface_extent_box(doc.get("extent") or doc.get("extents"))
    if ext is None and doc.get("points"):
        from shapely.geometry import box
        pts = doc["points"]
        # points may be [id,E,N,Z] or [E,N,(Z)]
        es, ns = [], []
        for p in pts:
            if len(p) >= 4 and not isinstance(p[0], (int, float)):
                es.append(float(p[1])); ns.append(float(p[2]))
            elif len(p) >= 2:
                es.append(float(p[0])); ns.append(float(p[1]))
        if es:
            ext = box(min(es), min(ns), max(es), max(ns))
    if ext is None:
        per_file["envelope"] = "not_run"
        out_subchecks.add(("model_sources", f"no extents on surface side file {name}"))
        return per_file, pats
    fake_ing = {"source": {"provenance": doc.get("provenance")}}
    surf = {"name": name, "role": doc.get("role"), "extents": {
        "e_min": ext.bounds[0], "n_min": ext.bounds[1], "e_max": ext.bounds[2], "n_max": ext.bounds[3]}}
    if is_survey_surface(fake_ing, surf) or any(t in name.upper() for t in ("NGL", "EGL")):
        if site_bnd is None:
            per_file["envelope"] = "not_run"
            out_subchecks.add(("survey_coverage", "site_boundary not set for coverage test [GAP]"))
            return per_file, pats
        if not site_bnd.within(ext.buffer(0.01)):
            per_file["envelope"] = "fail"
            pats.append(pattern(
                "survey_not_covering_site",
                f"Survey surface '{name}' does not cover the {site} site boundary",
                [{"handles": [], "extract": f"surface={name}"}], 1, src))
        else:
            per_file["envelope"] = "pass"
    else:
        target = env_g if env_g is not None else site_bnd
        if target is None:
            per_file["envelope"] = "not_run"
            return per_file, pats
        from shapely.geometry import Point
        from shapely import intersects
        corners = list(ext.exterior.coords)[:4]
        outs = [c for c in corners if not intersects(target, Point(c))]
        if outs:
            per_file["envelope"] = "fail"
            pats.append(pattern(
                f"outside_site_envelope/{name}",
                f"Design surface '{name}' extent lies outside the {site} site envelope",
                [{"handles": [], "extract": f"surface={name}; outside_corners={len(outs)}"}], 1, src))
        else:
            per_file["envelope"] = "pass"
    return per_file, pats


def control_info(datum, site_bnd):
    """C6 — information only. datum_reference[SITE]: benchmark / site_tbm / other {E,N,Z} points."""
    if not isinstance(datum, dict) or site_bnd is None:
        return []
    from shapely.geometry import Point
    out = []
    cen = site_bnd.centroid
    pts = []
    for key in ("benchmark", "site_tbm"):
        val = datum.get(key)
        if isinstance(val, dict) and val.get("E") is not None and val.get("N") is not None:
            pts.append((key, val))
    for key, val in datum.items():
        if key in ("benchmark", "site_tbm", "horizontal", "vertical_datum", "projection_check",
                   "other_points", "class"):
            continue
        if isinstance(val, dict) and val.get("E") is not None and val.get("N") is not None:
            pts.append((key, val))
    for label, p in pts:
        pt = Point(float(p["E"]), float(p["N"]))
        inside = bool(site_bnd.covers(pt) or site_bnd.intersects(pt))
        dist = 0.0 if inside else float(pt.distance(site_bnd))
        out.append({
            "id": p.get("id") or label, "role": label,
            "E": float(p["E"]), "N": float(p["N"]), "Z": p.get("Z"),
            "inside": inside,
            "distance_to_boundary_m": round(dist, 3),
            "distance_to_centroid_m": round(float(pt.distance(cen)), 3),
        })
    return out


def run(ingestion_paths, rules_path, site):
    _, rules = load_rules(rules_path)
    files_in = [load_model_file(p) for p in ingestion_paths]
    rule = rules.get(RULE)
    if rule is None:
        return {"rule_id": RULE, "result": "not_run", "reason": "rule not in the rules file (inactive or not for this agent)",
                "patterns": [], "files": [], "control": []}
    env, buf = param(rule, "site_envelope", site), param(rule, "envelope_buffer")
    context = layer_list(param(rule, "context_layers", site)) or []
    # also allow a shared "all" / non-site list via the raw table's common keys — keep site-only
    known = displacement_vectors(param(rule, "known_displacements", site))
    min_ents = param(rule, "displacement_min_entities")
    datum = param(rule, "datum_reference", site)
    subchecks = set()
    env_g = env_unbuf = None
    if env is None or buf is None:
        subchecks.add(("envelope", "site envelope coordinates for " + site + " not in the rules file [GAP] (P-S3-44)"
                       if env is None else "parameter envelope_buffer not set [GAP]"))
    else:
        env_g = envelope_geom(env, buf)
        env_unbuf = envelope_geom(env, 0)
    try:
        site_bnd = _site_boundary(rules, site, env)
    except InputError:
        site_bnd = env_unbuf
    files, pats = [], []
    for item in files_in:
        if item.get("_kind") == "surface_side":
            f, ps = check_surface_side(item, rule, site, env_g, site_bnd, subchecks)
        elif item.get("source", {}).get("file_type") == "landxml":
            f, ps = check_landxml(item, rule, site, env_g, site_bnd, subchecks)
        else:
            f, ps = check_dwg(item, rule, site, env_g, env_unbuf, context, known, min_ents, subchecks)
        files.append(f)
        pats += ps
    control = control_info(datum, site_bnd)
    return {"rule_id": RULE, "result": "fail" if pats else "pass", "patterns": pats, "files": files,
            "control": control,
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
                      "files": len(res.get("files", [])), "control": len(res.get("control", [])),
                      "subchecks_not_run": [s["subcheck"] for s in res.get("subchecks_not_run", [])],
                      "out": a.out}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
