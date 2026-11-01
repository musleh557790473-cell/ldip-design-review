#!/usr/bin/env python3
"""GIS-017 — site survey control / TBM distance (SPC-0006 §4.18 C7).

Usage: python check_control.py --rules <rules_GIS.json> --site <SITE> --out <gis017.json>
       [--ingestion <a.dwg.json> ...]   # optional; not required for the TBM check

Reads `datum_reference[SITE]` from GIS-008 (or GIS-017 when present), `max_distance_tbm` from
GIS-017, and the site boundary from GIS-011 `site_boundary[SITE]` (else GIS-008 `site_envelope`).
Patterns:
  no_site_tbm   — site_tbm absent / null → result fail (issue_type missing_information)
  tbm_too_far   — distance from TBM to the site boundary > max_distance_tbm
Without max_distance_tbm or a boundary → not_run [GAP]. Exit 0 · 2 · 3. No network."""
__version__ = "0.1.0"

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from dwg_geom import (  # noqa: E402
    InputError, boundary_geom, check_out_path, envelope_geom, load_rules, need_shapely, param,
    pattern, write_result,
)

RULE = "GIS-017"
RULE_DATUM = "GIS-008"
RULE_BND = "GIS-011"


def _boundary(rules, site):
    r011 = rules.get(RULE_BND)
    bnd = param(r011, "site_boundary", site) if r011 else None
    if bnd is not None:
        return boundary_geom(bnd)
    r008 = rules.get(RULE_DATUM)
    env = param(r008, "site_envelope", site) if r008 else None
    if env is None:
        return None
    return envelope_geom(env, 0)


def _datum(rules, site):
    for rid in (RULE_DATUM, RULE):
        r = rules.get(rid)
        if r is None:
            continue
        v = param(r, "datum_reference", site)
        if v is not None:
            return v, rid
    return None, None


def _tbm_point(datum):
    if not isinstance(datum, dict):
        return None
    tbm = datum.get("site_tbm")
    if tbm is None:
        return None
    if isinstance(tbm, dict) and tbm.get("E") is not None and tbm.get("N") is not None:
        return tbm
    return None


def run(rules_path, site, ingestion_paths=None):
    _, rules = load_rules(rules_path)
    # Touch optional ingestion paths so callers can pass them without changing the method
    if ingestion_paths:
        from dwg_geom import load_model_file
        for p in ingestion_paths:
            load_model_file(p)
    rule = rules.get(RULE)
    if rule is None:
        return {"rule_id": RULE, "result": "not_run",
                "reason": "rule not in the rules file (inactive or not for this agent)", "patterns": []}
    max_d = param(rule, "max_distance_tbm")
    datum, datum_from = _datum(rules, site)
    bnd = _boundary(rules, site)
    subchecks = []
    if max_d is None:
        return {"rule_id": RULE, "result": "not_run", "reason": "parameter max_distance_tbm not set [GAP]",
                "patterns": [], "subchecks_not_run": [{"subcheck": "tbm_distance",
                                                       "reason": "parameter max_distance_tbm not set [GAP]"}]}
    if bnd is None:
        return {"rule_id": RULE, "result": "not_run",
                "reason": f"site boundary / envelope for {site} not in the rules file [GAP]",
                "patterns": [], "subchecks_not_run": [{"subcheck": "tbm_distance",
                                                       "reason": f"site boundary for {site} not set [GAP]"}]}
    if datum is None:
        return {"rule_id": RULE, "result": "not_run",
                "reason": f"datum_reference for {site} not in the rules file [GAP]",
                "patterns": [], "subchecks_not_run": [{"subcheck": "tbm_distance",
                                                       "reason": f"datum_reference for {site} not set [GAP]"}]}
    from shapely.geometry import Point
    tbm = _tbm_point(datum)
    src = {"source_type": "script_output", "path": f"rules:{datum_from or RULE_DATUM}/datum_reference/{site}",
           "sha256": "0" * 64, "document_no": f"{site}-datum_reference", "file_type": "rules"}
    pats = []
    detail = {"site": site, "max_distance_tbm": float(max_d), "datum_from": datum_from}
    if tbm is None:
        pats.append(pattern(
            "no_site_tbm",
            f"No site TBM in datum_reference for {site}",
            [{"handles": [], "extract": f"site_tbm=null; benchmark={datum.get('benchmark', {}).get('id') if isinstance(datum.get('benchmark'), dict) else None}"}],
            1, src))
        detail["site_tbm"] = None
        return {"rule_id": RULE, "result": "fail", "patterns": pats, "detail": detail, "source": src,
                "subchecks_not_run": subchecks}
    pt = Point(float(tbm["E"]), float(tbm["N"]))
    inside = bool(bnd.covers(pt) or bnd.intersects(pt))
    dist = 0.0 if inside else float(pt.distance(bnd))
    detail["site_tbm"] = {"E": float(tbm["E"]), "N": float(tbm["N"]), "Z": tbm.get("Z"),
                          "inside": inside, "distance_to_boundary_m": round(dist, 3)}
    if dist > float(max_d):
        pats.append(pattern(
            "tbm_too_far",
            (f"Site TBM for {site} is {dist:.1f} m from the site boundary "
             f"(limit {float(max_d):g} m)"),
            [{"handles": [], "extract": (f"TBM E={tbm['E']} N={tbm['N']}; "
                                         f"distance_m={dist:.3f}; max={float(max_d):g}")}],
            1, src))
    return {"rule_id": RULE, "result": "fail" if pats else "pass", "patterns": pats,
            "detail": detail, "source": src if pats else None, "subchecks_not_run": subchecks}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--rules", required=True)
    ap.add_argument("--site", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--ingestion", nargs="*")
    a = ap.parse_args(argv)
    miss = need_shapely()
    if miss:
        print(json.dumps({"status": "error", "code": 3, "detail": miss}))
        return 3
    try:
        check_out_path(a.out)
        res = run(a.rules, a.site, a.ingestion)
    except InputError as exc:
        print(json.dumps({"status": "error", "code": 2, "detail": str(exc)}))
        return 2
    res["script"] = {"name": "check_control.py", "version": __version__}
    write_result(a.out, res)
    print(json.dumps({"status": "ok", "rule_id": RULE, "result": res["result"],
                      "patterns": len(res.get("patterns", [])),
                      "subchecks_not_run": [s["subcheck"] for s in res.get("subchecks_not_run", [])],
                      "out": a.out}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
