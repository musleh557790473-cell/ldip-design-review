#!/usr/bin/env python3
"""Phase D surface checks — ROD-056 gradient, ROD-062 slope, ROD-043 crossfall/drainage
(SPC-0006 §4.13H / DK-1…DK-4).

Reuses C3-accepted element polygons (network hooks or --elements). Samples the LandXML design
TIN per DK-4 (centroid-in-polygon faces; max face slope % for ≤ limits; min for ≥ / *_min).
ROD-043 apron is out of D. Numeric crossfall/drainage limits come only from the rules file
or an optional rulebook path (`--rulebook` / `LDIP_RULEBOOK`) — if absent → not_run [GAP],
never invented. Loading parameters does **not** activate inactive rules (DJ-9 / R37).

Usage: python check_surfaces.py --network <network.json> --rules <rules_RDE.json> --stage <name>
           --site <SITE> [--landxml <X>.xml.json] [--surface <side.json>] [--surface-index N]
           [--elements <elements.json>] [--file drainage_plan=<dwg.json>]
           [--rulebook <rules_vN.json|rod043_excerpt.json>]
           --out <surface_checks.json>
Exit: 0 written · 2 input · 3 dependency. No network, no shell calls.
"""
__version__ = "0.1.1"

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from c3_common import fail_rec, gate, parse_file_args  # noqa: E402
from location_common import shapely_mods, xy  # noqa: E402
from road_common import (EXIT_OK, atomic_write_json, emit, main_guard,  # noqa: E402
                         param, read_json, rules_index)
from tin_surface import (load_tin, max_face_slope_in_polygon, min_face_slope_in_polygon,  # noqa: E402
                         resolve_tin_from_landxml, tin_sha256)

LIMIT_056_062 = 2.0  # % — Rev J / §4.13H.3
# Real names from rules_v7 ROD-043 (see tests/fixtures/rod043_excerpt.json). Do not invent.
CROSSFALL_PARAM_NAMES = ("circulatory_crossfall", "crossfall", "crossfall_max", "crossfall_pct")
GAP_043 = ("ROD-043 crossfall/drainage parameters not available — pass --rulebook "
           "<rules_vN.json|excerpt> or set LDIP_RULEBOOK (full rules_v*.json stays out of Git; "
           "CI uses tests/fixtures/rod043_excerpt.json) [GAP — do not invent limits]")


def resolve_rulebook_path(cli_path=None):
    """CLI `--rulebook` wins; else LDIP_RULEBOOK or LDIP_RULES_V7. None if unset."""
    for cand in (cli_path, os.environ.get("LDIP_RULEBOOK"), os.environ.get("LDIP_RULES_V7")):
        if cand:
            return cand
    return None


def load_rod043_from_rulebook(path):
    """Return (parameters, statement, None) from a full rulebook or ROD-043 excerpt.

    Accepts either `{"rules":[...]}` or a single-rule excerpt with `rule_id` ROD-043.
    Returns (None, None, reason) when ROD-043 / parameters are missing — never invents.
    """
    doc = read_json(path)
    rule = None
    if doc.get("rule_id") == "ROD-043":
        rule = doc
    else:
        for r in doc.get("rules") or []:
            if r.get("rule_id") == "ROD-043":
                rule = r
                break
    if rule is None:
        return None, None, f"ROD-043 not found in rulebook file: {path}"
    params = rule.get("parameters") or {}
    if not params:
        return None, None, f"ROD-043 has no parameters in rulebook file: {path}"
    return params, rule.get("statement"), None


def _merge_rod043_params(rule, rulebook_path):
    """Overlay ROD-043 parameters from rulebook when the RDE rule object lacks them.

    Does not change `state` — activation stays with the agent rules file / get_rules.
    """
    existing = dict(rule.get("parameters") or {})
    has_cf = any(param({"parameters": existing}, n) for n in CROSSFALL_PARAM_NAMES)
    has_dr = any(
        (n.startswith("drainage") and param({"parameters": existing}, n) is not None)
        for n in existing
    ) or param({"parameters": existing}, "drainage") is not None
    if has_cf and has_dr:
        return rule, None, "rde_rules"
    if not rulebook_path:
        return rule, None, None
    if not Path(rulebook_path).is_file():
        return rule, f"rulebook file not found: {rulebook_path}", None
    overlay, statement, err = load_rod043_from_rulebook(rulebook_path)
    if err:
        return rule, err, None
    merged = dict(rule)
    merged_params = dict(existing)
    for k, v in overlay.items():
        if k == "apron_width":
            # Apron stays out of D; keep for provenance only if already present.
            if k not in merged_params:
                merged_params[k] = v
            continue
        cur = merged_params.get(k)
        if cur is None or cur.get("value") is None or cur.get("tag") == "GAP":
            merged_params[k] = v
    merged["parameters"] = merged_params
    if statement and not (merged.get("statement") or "").strip():
        merged["statement"] = statement
    return merged, None, str(rulebook_path)


def _poly(g, ring):
    coords = [tuple(p[:2]) for p in ring]
    if len(coords) < 3:
        return None
    if coords[0] != coords[-1]:
        coords = coords + [coords[0]]
    poly = g.Polygon(coords)
    return poly if poly.is_valid and poly.area > 0 else None


def _elements_from(net, elements_doc):
    """Merge --elements JSON over network.c3_* hooks (C3-accepted geometry)."""
    src = dict(elements_doc or {})
    refuges = list(src.get("refuges") or net.get("c3_refuges") or [])
    bays = list(src.get("accessible_bays") or net.get("c3_accessible_bays") or [])
    footprints = list(src.get("crossfall_footprints") or net.get("c3_crossfall_footprints") or [])
    return refuges, bays, footprints


def _surface_evidence(tin_path, tin_meta, slope, n_faces, handle=None, extra="", *, slope_label="max_slope_pct"):
    sha = tin_sha256(tin_path) if tin_path else None
    name = (tin_meta or {}).get("name") or (tin_meta or {}).get("surface")
    parts = [f"TIN {Path(tin_path).name}" if tin_path else "TIN",
             f"sha256={sha[:12]}…" if sha else None,
             f"surface={name}" if name else None,
             f"faces_in_element={n_faces}",
             f"{slope_label}={slope:.4g}" if slope is not None else None,
             extra or None]
    return {"source_type": "landxml", "path": tin_path, "sha256": sha,
            "handles": [handle] if handle else [],
            "extract": "; ".join(p for p in parts if p)}


def _check_face_slope(rid, pname, subject, poly, handle, tin, tin_path, tin_meta, limit, op, g,
                      checks, passed, not_run):
    """op 'le' → max face slope ≤ limit; op 'ge' → min face slope ≥ limit (drainage_g_min)."""
    if op == "ge":
        val, n = min_face_slope_in_polygon(tin, poly, g)
        label = "min_slope_pct"
        ok = val is not None and val >= limit - 1e-9
    else:
        val, n = max_face_slope_in_polygon(tin, poly, g)
        label = "max_slope_pct"
        ok = val is not None and val <= limit + 1e-9
    if n == 0 or val is None:
        not_run.append({"rule_id": rid, "parameter": pname, "phase": "D",
                        "reason": f"{subject}: surface does not cover element (no face centroid inside)"})
        return
    src = _surface_evidence(tin_path, tin_meta, val, n, handle, slope_label=label)
    cxy = xy((poly.centroid.x, poly.centroid.y))
    rec = fail_rec(rid, pname, subject, round(val, 4), limit, op, "%", src,
                   handles=[handle] if handle else None, xy_pt=cxy,
                   extract=src["extract"], result="pass" if ok else "fail",
                   link_cap="Probable", mode_tag="d_surface")
    (passed if ok else checks).append(rec)


def _check_max_slope(rid, pname, subject, poly, handle, tin, tin_path, tin_meta, limit, g,
                     checks, passed, not_run):
    _check_face_slope(rid, pname, subject, poly, handle, tin, tin_path, tin_meta, limit, "le", g,
                      checks, passed, not_run)


def _crossfall_param(rule):
    for name in CROSSFALL_PARAM_NAMES:
        p = param(rule, name)
        if p is not None and p.get("value") is not None:
            return name, p
    return None, None


def _drainage_params(rule):
    """Named drainage parameters only — never invent radii / ROD-107 reuse."""
    out = []
    seen = set()
    for key in ("drainage", "drainage_plan", "drainage_presence", "drainage_g_min"):
        p = param(rule, key)
        if p is not None:
            out.append((key, p))
            seen.add(key)
    for pname, p in (rule.get("parameters") or {}).items():
        if pname.startswith("drainage") and pname not in seen:
            if param(rule, pname) is not None:
                out.append((pname, p))
                seen.add(pname)
    return out


def _drainage_op(pname, p):
    """Infer comparison from parameter name / note; *_min → ge, else numeric ≤ or presence."""
    if pname.endswith("_min") or pname == "g_min" or "g_min" in pname:
        return "ge"
    if pname.endswith("_max"):
        return "le"
    note = (p.get("note") or "").lower()
    if "at least" in note or "minimum" in note:
        return "ge"
    return "le"


def _load_surface(a):
    """Return (tin, tin_path, tin_meta, not_run_reason)."""
    if a.surface:
        path = a.surface
        if not Path(path).is_file():
            return None, None, None, f"surface side file not found: {path}"
        return load_tin(path), path, {"surface": Path(path).stem, "index": a.surface_index}, None
    if a.landxml:
        path, meta, reason = resolve_tin_from_landxml(a.landxml, a.surface_index)
        if reason:
            return None, None, None, reason
        return load_tin(path), path, meta, None
    return None, None, None, "no design surface in the package"


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--network", required=True)
    ap.add_argument("--rules", required=True)
    ap.add_argument("--stage", required=True)
    ap.add_argument("--site", required=True, choices=["QRWN", "NMAR", "NRJS"])
    ap.add_argument("--landxml", default=None, help="LandXML ingestion JSON with content.surfaces[]")
    ap.add_argument("--surface", default=None, help="Direct TIN side-file path (overrides --landxml pick)")
    ap.add_argument("--surface-index", type=int, default=None,
                    help="When several surfaces exist, pick by content.surfaces[].index ([GAP] otherwise)")
    ap.add_argument("--elements", default=None,
                    help="JSON with refuges / accessible_bays / crossfall_footprints (C3-accepted)")
    ap.add_argument("--file", action="append", default=[], help="carrier=path (drainage_plan for ROD-043)")
    ap.add_argument("--rulebook", default=None,
                    help="Full rules_vN.json or ROD-043 excerpt for numeric params "
                         "(env LDIP_RULEBOOK / LDIP_RULES_V7). Does not activate inactive rules.")
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)

    def run():
        g, _ = shapely_mods()
        net = read_json(a.network)
        rules, skipped, stage_map = rules_index(read_json(a.rules))
        elements_doc = read_json(a.elements) if a.elements else {}
        refuges, bays, footprints = _elements_from(net, elements_doc)
        carriers = {c: p for c, p in parse_file_args(a.file)}
        checks, not_run, passed = [], [], []
        rulebook_path = resolve_rulebook_path(a.rulebook)
        params_source = None

        tin, tin_path, tin_meta, surf_reason = _load_surface(a)

        # ---- ROD-056 gradient
        rule, ph = gate(rules, skipped, "ROD-056", stage_map, a.stage)
        if rule is None:
            not_run.append({"rule_id": "ROD-056", "parameter": "gradient", "phase": "D", "reason": ph})
        elif not refuges:
            not_run.append({"rule_id": "ROD-056", "parameter": "gradient", "phase": "D",
                            "reason": "no element — no C3-accepted refuge outline"})
        elif tin is None:
            not_run.append({"rule_id": "ROD-056", "parameter": "gradient", "phase": "D",
                            "reason": surf_reason or "no design surface in the package"})
        else:
            for i, el in enumerate(refuges, 1):
                poly = _poly(g, el.get("polygon") or el.get("ring") or [])
                if poly is None:
                    not_run.append({"rule_id": "ROD-056", "parameter": "gradient", "phase": "D",
                                    "reason": f"refuge {el.get('id') or i}: invalid polygon"})
                    continue
                subject = f"refuge {el.get('id') or el.get('handle') or i}"
                _check_max_slope("ROD-056", "gradient", subject, poly, el.get("handle"),
                                 tin, tin_path, tin_meta, LIMIT_056_062, g, checks, passed, not_run)

        # ---- ROD-062 slope (accessible bay polygon only — not aisle)
        rule, ph = gate(rules, skipped, "ROD-062", stage_map, a.stage)
        if rule is None:
            not_run.append({"rule_id": "ROD-062", "parameter": "slope", "phase": "D", "reason": ph})
        elif not bays:
            not_run.append({"rule_id": "ROD-062", "parameter": "slope", "phase": "D",
                            "reason": "no element — no C3-accepted accessible bay polygon"})
        elif tin is None:
            not_run.append({"rule_id": "ROD-062", "parameter": "slope", "phase": "D",
                            "reason": surf_reason or "no design surface in the package"})
        else:
            for i, el in enumerate(bays, 1):
                poly = _poly(g, el.get("polygon") or el.get("ring") or [])
                if poly is None:
                    not_run.append({"rule_id": "ROD-062", "parameter": "slope", "phase": "D",
                                    "reason": f"accessible bay {el.get('id') or i}: invalid polygon"})
                    continue
                subject = f"accessible bay {el.get('id') or el.get('handle') or i}"
                _check_max_slope("ROD-062", "slope", subject, poly, el.get("handle"),
                                 tin, tin_path, tin_meta, LIMIT_056_062, g, checks, passed, not_run)

        # ---- ROD-043 crossfall / drainage (apron out of D)
        not_run.append({"rule_id": "ROD-043", "parameter": "apron", "phase": "D",
                        "reason": "apron out of phase D — synthetic/activation with first 60 % (SPC-0004 §4.8E)"})
        rule, ph = gate(rules, skipped, "ROD-043", stage_map, a.stage)
        if rule is None:
            not_run.append({"rule_id": "ROD-043", "parameter": "crossfall", "phase": "D", "reason": ph})
            not_run.append({"rule_id": "ROD-043", "parameter": "drainage", "phase": "D", "reason": ph})
        else:
            rule, merge_err, params_source = _merge_rod043_params(rule, rulebook_path)
            # Crossfall — real rules_v7 name is circulatory_crossfall
            cf_name, p_cf = _crossfall_param(rule)
            if p_cf is None:
                reason = GAP_043
                if merge_err:
                    reason = f"{GAP_043}; rulebook: {merge_err}"
                not_run.append({"rule_id": "ROD-043", "parameter": "crossfall", "phase": "D",
                                "reason": f"Missing information (rule parameters) — {reason}",
                                "status_hint": "Missing information"})
            elif not footprints:
                not_run.append({"rule_id": "ROD-043", "parameter": cf_name or "crossfall", "phase": "D",
                                "reason": ("no crossfall footprint [GAP: exact polygon source — circulatory "
                                           "outline vs apron ring — not in deposit without rule text]")})
            elif tin is None:
                not_run.append({"rule_id": "ROD-043", "parameter": cf_name or "crossfall", "phase": "D",
                                "reason": surf_reason or "no design surface in the package"})
            else:
                limit = float(p_cf["value"])
                for i, el in enumerate(footprints, 1):
                    poly = _poly(g, el.get("polygon") or el.get("ring") or [])
                    if poly is None:
                        not_run.append({"rule_id": "ROD-043", "parameter": cf_name, "phase": "D",
                                        "reason": f"footprint {el.get('id') or i}: invalid polygon"})
                        continue
                    subject = f"roundabout crossfall {el.get('id') or i}"
                    _check_face_slope("ROD-043", cf_name, subject, poly, el.get("handle"),
                                      tin, tin_path, tin_meta, limit, "le", g, checks, passed, not_run)

            # Drainage — only parameters present in the rule / rulebook; never invent
            drain_params = _drainage_params(rule)
            if not drain_params:
                reason = GAP_043
                if merge_err:
                    reason = f"{GAP_043}; rulebook: {merge_err}"
                not_run.append({"rule_id": "ROD-043", "parameter": "drainage", "phase": "D",
                                "reason": f"Missing information (rule parameters) — {reason}",
                                "status_hint": "Missing information"})
            else:
                has_plan = "drainage_plan" in carriers and Path(carriers["drainage_plan"]).is_file()
                for pname, p in drain_params:
                    val = p.get("value")
                    if val in (True, "present", "required") or pname in ("drainage_plan", "drainage_presence"):
                        if not has_plan:
                            phase = ph
                            req_from = rule.get("required_from_phase") or "DD"
                            if phase == "PD" and req_from == "DD":
                                not_run.append({"rule_id": "ROD-043", "parameter": pname, "phase": "D",
                                                "reason": "not required at stage"})
                            else:
                                not_run.append({"rule_id": "ROD-043", "parameter": pname, "phase": "D",
                                                "reason": "drainage_plan carrier missing",
                                                "status_hint": "Missing information"})
                            continue
                        src = {"source_type": "dwg_vector", "path": carriers["drainage_plan"],
                               "extract": "drainage_plan present"}
                        passed.append(fail_rec("ROD-043", pname, "drainage plan", "present", "present",
                                               "present", None, src, result="pass", link_cap="Probable",
                                               mode_tag="d_drainage"))
                    elif isinstance(val, (int, float)):
                        if tin is None:
                            not_run.append({"rule_id": "ROD-043", "parameter": pname, "phase": "D",
                                            "reason": surf_reason or "no design surface in the package"})
                            continue
                        if not footprints:
                            not_run.append({"rule_id": "ROD-043", "parameter": pname, "phase": "D",
                                            "reason": ("no drainage footprint [GAP: circulatory outline "
                                                       "vs apron ring — not fixed without rule text]")})
                            continue
                        op = _drainage_op(pname, p)
                        for i, el in enumerate(footprints, 1):
                            poly = _poly(g, el.get("polygon") or el.get("ring") or [])
                            if poly is None:
                                continue
                            subject = f"drainage {pname} {el.get('id') or i}"
                            _check_face_slope("ROD-043", pname, subject, poly, el.get("handle"),
                                              tin, tin_path, tin_meta, float(val), op, g,
                                              checks, passed, not_run)
                    else:
                        not_run.append({"rule_id": "ROD-043", "parameter": pname, "phase": "D",
                                        "reason": (f"drainage parameter {pname!r} present but check method "
                                                   f"needs rule statement [GAP] — value={val!r}")})

        atomic_write_json(a.out, {"tool": {"script": "check_surfaces.py", "version": __version__},
                                  "mode": "d_surfaces", "checks": checks, "passed": passed, "not_run": not_run,
                                  "gap_notes": {
                                      "ROD-043_params": GAP_043,
                                      "ROD-043_params_source": params_source,
                                      "multi_surface": "prefer --surface-index when content.surfaces[] length > 1",
                                      "activation": "loading --rulebook / LDIP_RULEBOOK does not activate inactive ROD-043",
                                  }})
        emit({"status": "ok", "out": a.out, "checks": len(checks),
              "fail": sum(1 for c in checks if c.get("result") == "fail"),
              "pass": len(passed), "not_run": len(not_run)})
        return EXIT_OK
    return main_guard(run)


if __name__ == "__main__":
    sys.exit(main())
