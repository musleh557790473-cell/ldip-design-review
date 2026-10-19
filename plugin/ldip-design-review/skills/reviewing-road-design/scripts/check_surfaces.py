#!/usr/bin/env python3
"""Phase D surface checks — ROD-056 gradient, ROD-062 slope, ROD-043 crossfall/drainage
(SPC-0006 §4.13H / DK-1…DK-4).

Reuses C3-accepted element polygons (network hooks or --elements). Samples the LandXML design
TIN per DK-4 (centroid-in-polygon faces; max face slope %). ROD-043 apron is out of D; numeric
crossfall/drainage limits come only from the rules file — if absent → not_run [GAP], never invented.

Usage: python check_surfaces.py --network <network.json> --rules <rules_RDE.json> --stage <name>
           --site <SITE> [--landxml <X>.xml.json] [--surface <side.json>] [--surface-index N]
           [--elements <elements.json>] [--file drainage_plan=<dwg.json>]
           --out <surface_checks.json>
Exit: 0 written · 2 input · 3 dependency. No network, no shell calls.
"""
__version__ = "0.1.0"

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from c3_common import fail_rec, gate, parse_file_args  # noqa: E402
from location_common import shapely_mods, xy  # noqa: E402
from road_common import (EXIT_OK, atomic_write_json, emit, main_guard,  # noqa: E402
                         param, read_json, rules_index)
from tin_surface import (load_tin, max_face_slope_in_polygon, resolve_tin_from_landxml,  # noqa: E402
                         tin_sha256)

LIMIT_056_062 = 2.0  # % — Rev J / §4.13H.3
GAP_043 = ("rules_v7 ROD-043 statement/parameters not in this environment "
           "[GAP — local deposit offline; do not invent limits]")


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


def _surface_evidence(tin_path, tin_meta, max_slope, n_faces, handle=None, extra=""):
    sha = tin_sha256(tin_path) if tin_path else None
    name = (tin_meta or {}).get("name") or (tin_meta or {}).get("surface")
    parts = [f"TIN {Path(tin_path).name}" if tin_path else "TIN",
             f"sha256={sha[:12]}…" if sha else None,
             f"surface={name}" if name else None,
             f"faces_in_element={n_faces}",
             f"max_slope_pct={max_slope:.4g}" if max_slope is not None else None,
             extra or None]
    return {"source_type": "landxml", "path": tin_path, "sha256": sha,
            "handles": [handle] if handle else [],
            "extract": "; ".join(p for p in parts if p)}


def _check_max_slope(rid, pname, subject, poly, handle, tin, tin_path, tin_meta, limit, g, checks, passed, not_run):
    max_s, n = max_face_slope_in_polygon(tin, poly, g)
    if n == 0 or max_s is None:
        not_run.append({"rule_id": rid, "parameter": pname, "phase": "D",
                        "reason": f"{subject}: surface does not cover element (no face centroid inside)"})
        return
    ok = max_s <= limit + 1e-9
    src = _surface_evidence(tin_path, tin_meta, max_s, n, handle)
    cxy = xy((poly.centroid.x, poly.centroid.y))
    rec = fail_rec(rid, pname, subject, round(max_s, 4), limit, "le", "%", src,
                   handles=[handle] if handle else None, xy_pt=cxy,
                   extract=src["extract"], result="pass" if ok else "fail",
                   link_cap="Probable", mode_tag="d_surface")
    (passed if ok else checks).append(rec)


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
            # Crossfall
            p_cf = param(rule, "crossfall") or param(rule, "crossfall_max") or param(rule, "crossfall_pct")
            if p_cf is None or p_cf.get("value") is None:
                not_run.append({"rule_id": "ROD-043", "parameter": "crossfall", "phase": "D",
                                "reason": f"Missing information (rule parameters) — {GAP_043}",
                                "status_hint": "Missing information"})
            elif not footprints:
                not_run.append({"rule_id": "ROD-043", "parameter": "crossfall", "phase": "D",
                                "reason": ("no crossfall footprint [GAP: exact polygon source — circulatory "
                                           "outline vs apron ring — not in deposit without rule text]")})
            elif tin is None:
                not_run.append({"rule_id": "ROD-043", "parameter": "crossfall", "phase": "D",
                                "reason": surf_reason or "no design surface in the package"})
            else:
                limit = float(p_cf["value"])
                for i, el in enumerate(footprints, 1):
                    poly = _poly(g, el.get("polygon") or el.get("ring") or [])
                    if poly is None:
                        not_run.append({"rule_id": "ROD-043", "parameter": "crossfall", "phase": "D",
                                        "reason": f"footprint {el.get('id') or i}: invalid polygon"})
                        continue
                    subject = f"roundabout crossfall {el.get('id') or i}"
                    _check_max_slope("ROD-043", "crossfall", subject, poly, el.get("handle"),
                                     tin, tin_path, tin_meta, limit, g, checks, passed, not_run)

            # Drainage — only parameters present in the rule file; never invent radii / ROD-107 reuse
            drain_params = []
            for key in ("drainage", "drainage_plan", "drainage_presence"):
                p = param(rule, key)
                if p is not None:
                    drain_params.append((key, p))
            # also any parameter name starting with drainage_
            for pname, p in (rule.get("parameters") or {}).items():
                if pname.startswith("drainage") and pname not in {k for k, _ in drain_params}:
                    drain_params.append((pname, p))
            if not drain_params:
                not_run.append({"rule_id": "ROD-043", "parameter": "drainage", "phase": "D",
                                "reason": f"Missing information (rule parameters) — {GAP_043}",
                                "status_hint": "Missing information"})
            else:
                has_plan = "drainage_plan" in carriers and Path(carriers["drainage_plan"]).is_file()
                for pname, p in drain_params:
                    # Presence-style: required True / "present" → need drainage_plan carrier
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
                    elif isinstance(val, (int, float)) and tin is not None and footprints:
                        # Numeric drainage parameter that is surface-based — same TIN max-slope family
                        for i, el in enumerate(footprints, 1):
                            poly = _poly(g, el.get("polygon") or el.get("ring") or [])
                            if poly is None:
                                continue
                            subject = f"drainage {pname} {el.get('id') or i}"
                            _check_max_slope("ROD-043", pname, subject, poly, el.get("handle"),
                                             tin, tin_path, tin_meta, float(val), g, checks, passed, not_run)
                    else:
                        not_run.append({"rule_id": "ROD-043", "parameter": pname, "phase": "D",
                                        "reason": (f"drainage parameter {pname!r} present but check method "
                                                   f"needs rule statement [GAP] — value={val!r}")})

        atomic_write_json(a.out, {"tool": {"script": "check_surfaces.py", "version": __version__},
                                  "mode": "d_surfaces", "checks": checks, "passed": passed, "not_run": not_run,
                                  "gap_notes": {
                                      "ROD-043": GAP_043,
                                      "multi_surface": "prefer --surface-index when content.surfaces[] length > 1",
                                  }})
        emit({"status": "ok", "out": a.out, "checks": len(checks),
              "fail": sum(1 for c in checks if c.get("result") == "fail"),
              "pass": len(passed), "not_run": len(not_run)})
        return EXIT_OK
    return main_guard(run)


if __name__ == "__main__":
    sys.exit(main())
