#!/usr/bin/env python3
"""EWK-001 / EWK-002 slope checks for skill 13 (SPC-0006 Rev L §4.14.7–8 + L-a + L-b).

L-b (DL-19…DL-23): face comparisons use m = max(grade_pct, u) with
u = 100 · δz · P / (2 · A), δz = slope_precision_dz_m (1 mm). Hysteresis grouping:
possibly-flagged faces connected by vertex coordinates (1 mm); a group is reported
only if it has at least one clearly-flagged face (EWK-002) or clearly-flat area
≥ ewk001_min_area_m2 (EWK-001). Format-independent (same δz for IFC and LandXML).

EWK-102 uses EWK-002 groups (height ≥ 1.0 m at PD — L-a DL-18, unchanged).
FoS text from design_report at DD only (L-a).

Usage:
  python check_slopes.py --surfaces <surfaces.json> --rules <rules_RDE.json>
      --stage "<stage>" [--site SITE] [--report <design_report.pdf.json>]
      [--existing-dz] --out <slopes.json>
"""
__version__ = "0.2.0"

import argparse
import math
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from ewk_common import (EXIT_OK, atomic_write_json, emit, ewk_gate,  # noqa: E402
                        fail_rec, face_perimeter_2d, group_faces_by_vertex, iter_faces,
                        load_params, load_tin, main_guard, param, read_json, rules_index,
                        slope_uncertainty_pct, tin_raster_z, tol_value)


FOS_RE = re.compile(
    r"(?:factor\s+of\s+safety|f\.?\s*o\.?\s*s\.?|f\.?\s*s\.?)\s*"
    r"(?:\((?P<kind>static|seismic|pseudo-?static)\)|\b(?P<kind2>static|seismic|pseudo-?static)\b)?"
    r"[^\d]{0,20}(?P<val>\d+(?:\.\d+)?)",
    re.I,
)
FOS_PAIR = re.compile(
    r"(?P<s>\d+(?:\.\d+)?)\s*/\s*(?:seismic\s*)?(?P<e>\d+(?:\.\d+)?)",
    re.I,
)


def _paved_index(paved):
    """Build a set of paved cells once (D4 performance)."""
    if not paved or not paved.get("cells"):
        return None
    return {
        "e0": paved["e0"], "n0": paved["n0"], "cell_m": paved["cell_m"],
        "cells": {tuple(c) for c in paved["cells"]},
    }


def _centroid_in_paved(cx, cy, paved_idx):
    if not paved_idx:
        return False
    e0, n0, cell = paved_idx["e0"], paved_idx["n0"], paved_idx["cell_m"]
    ix = int(math.floor((cx - e0) / cell))
    iy = int(math.floor((cy - n0) / cell))
    return (ix, iy) in paved_idx["cells"]


def _face_rec(ids, p1, p2, p3, slope, cent, area, *, in_paved, dz_m, tol_pct):
    """Build a face record with L-b precision margin m = max(tol, u)."""
    peri = face_perimeter_2d(p1, p2, p3)
    u = slope_uncertainty_pct(area, peri, dz_m)
    m = max(float(tol_pct), u)
    cx, cy, cz = cent
    return {
        "ids": ids,
        "verts": (p1, p2, p3),
        "slope": slope,
        "centroid": (cx, cy, cz),
        "area": area,
        "perimeter_m": peri,
        "u_pct": u,
        "m_pct": m,
        "z_min": min(p1[2], p2[2], p3[2]),
        "z_max": max(p1[2], p2[2], p3[2]),
        "in_paved": in_paved,
    }


def _hysteresis_groups(face_records, *, mode, min_area=None, min_height=None, sort_by="area"):
    """L-b DL-20: group possibly-flagged faces; keep only groups with a clear flag.

    mode='flat': keep if clearly-flat area ≥ min_area.
    mode='steep': keep if ≥1 clearly-steep face and height ≥ min_height.
    """
    if not face_records:
        return []
    idx_groups = group_faces_by_vertex(face_records)
    out = []
    for idxs in idx_groups:
        members = [face_records[i] for i in idxs]
        clearly = [m for m in members if m.get("clearly")]
        if mode == "steep":
            if not clearly:
                continue
        elif mode == "flat":
            clear_area = sum(m["area"] for m in clearly)
            if min_area is not None and clear_area < min_area:
                continue
        else:
            raise ValueError(mode)
        area = sum(m["area"] for m in members)
        if area <= 0:
            continue
        z_min = min(m["z_min"] for m in members)
        z_max = max(m["z_max"] for m in members)
        height = z_max - z_min
        if min_height is not None and height < min_height:
            continue
        cx = sum(m["centroid"][0] * m["area"] for m in members) / area
        cy = sum(m["centroid"][1] * m["area"] for m in members) / area
        slopes = sorted(m["slope"] for m in members)
        mean_s = sum(m["slope"] * m["area"] for m in members) / area
        max_s = slopes[-1]
        hv_mean = round(100.0 / mean_s, 3) if mean_s > 1e-9 else None
        hv_max = round(100.0 / max_s, 3) if max_s > 1e-9 else None
        out.append({
            "area_m2": round(area, 3),
            "height_m": round(height, 3),
            "mean_slope_pct": round(mean_s, 3),
            "max_slope_pct": round(max_s, 3),
            "median_slope_pct": round(slopes[len(slopes) // 2], 3),
            "hv": hv_mean,
            "hv_max": hv_max,
            "face_count": len(members),
            "clear_face_count": len(clearly),
            "centroid_en": [round(cx, 3), round(cy, 3)],
            # D4: keep face samples for area-weighted cut/fill
            "_faces": [{"centroid": m["centroid"], "area": m["area"]} for m in members],
        })
    if sort_by == "height":
        out.sort(key=lambda g: (-g.get("height_m", 0), -g.get("area_m2", 0)))
    else:
        out.sort(key=lambda g: (-g.get("area_m2", 0), -g.get("height_m", 0)))
    return out


def _cut_fill(groups, existing_tin, cell_m=1.0):
    """D4: area-weighted design−existing via one existing raster (Rev L §4.14.8 item 5)."""
    if existing_tin is None:
        for g in groups:
            g["cut_fill"] = "unknown"
        return
    egrid, e0, n0, _e1, _n1 = tin_raster_z(existing_tin, cell_m=cell_m)
    if not egrid:
        for g in groups:
            g["cut_fill"] = "unknown"
        return
    for g in groups:
        cut_a = fill_a = zero_a = 0.0
        wdz = 0.0
        wsum = 0.0
        for f in g.get("_faces") or []:
            cx, cy, cz = f["centroid"]
            ix = int(math.floor((cx - e0) / cell_m))
            iy = int(math.floor((cy - n0) / cell_m))
            ez = egrid.get((ix, iy))
            if ez is None:
                continue
            dz = cz - ez
            a = f["area"]
            wdz += dz * a
            wsum += a
            if dz < -0.05:
                cut_a += a
            elif dz > 0.05:
                fill_a += a
            else:
                zero_a += a
        if wsum <= 0:
            g["cut_fill"] = "unknown"
            continue
        if cut_a >= fill_a and cut_a > zero_a:
            g["cut_fill"] = "cut"
        elif fill_a > cut_a and fill_a > zero_a:
            g["cut_fill"] = "fill"
        else:
            g["cut_fill"] = "near_zero"
        g["dz_m"] = round(wdz / wsum, 3)


def read_fos(report_path):
    """Extract FoS static / seismic from PDF ingestion text."""
    if not report_path:
        return None
    ing = read_json(report_path)
    texts = []
    for t in (ing.get("content") or {}).get("texts") or []:
        if isinstance(t, dict) and t.get("text"):
            texts.append(t["text"])
        elif isinstance(t, str):
            texts.append(t)
    # also pages[].lines
    for page in (ing.get("content") or {}).get("pages") or []:
        for line in page.get("lines") or page.get("texts") or []:
            if isinstance(line, dict):
                texts.append(line.get("text") or "")
            else:
                texts.append(str(line))
    blob = "\n".join(texts)
    if not blob.strip():
        # raw extract field
        blob = ing.get("content", {}).get("extract") or ing.get("text") or ""
    static = seismic = None
    for m in FOS_RE.finditer(blob):
        kind = (m.group("kind") or m.group("kind2") or "").lower()
        val = float(m.group("val"))
        if "seis" in kind or "pseudo" in kind:
            seismic = val
        else:
            static = val
    m = FOS_PAIR.search(blob)
    if m and static is None:
        static = float(m.group("s"))
        seismic = float(m.group("e"))
    # bare "FoS (static) 1.30"
    if static is None:
        m = re.search(r"FoS\s*\(\s*static\s*\)\s*(\d+(?:\.\d+)?)", blob, re.I)
        if m:
            static = float(m.group(1))
    return {"static": static, "seismic": seismic, "searched": [report_path],
            "source_type": "pdf_vector_text", "path": (ing.get("source") or {}).get("path"),
            "sha256": (ing.get("source") or {}).get("sha256")}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--surfaces", required=True)
    ap.add_argument("--rules", required=True)
    ap.add_argument("--stage", required=True)
    ap.add_argument("--site", default=None)
    ap.add_argument("--report", default=None)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)

    def run():
        surf_doc = read_json(a.surfaces)
        rb = read_json(a.rules)
        rules, skipped, stage_map = rules_index(rb)
        tolerances = rb.get("tolerances") or {}
        params, _site_p, _ = load_params(a.site)
        min_area = float(params.get("ewk001_min_area_m2", 25.0))
        min_height = float(params.get("ewk002_min_height_m", 0.5))
        wall_pct = float(params.get("wall_slope_pct", 1000.0))
        cell_m = float(params.get("cell_m", 1.0))
        dz_m = float(params.get("slope_precision_dz_m", 0.001))
        midp_30 = surf_doc.get("midp_schedules_carriers_at_30")

        by_role = {s["role"]: s for s in surf_doc.get("surfaces") or []}
        paved_idx = _paved_index(surf_doc.get("paved_footprint"))
        checks, passed, not_run = [], [], []
        ewk001_groups, ewk002_groups, walls = [], [], []

        design = by_role.get("design_grading") or by_role.get("finished_grading")
        existing = by_role.get("existing")
        doc_no = (design or {}).get("document_no")

        # --- EWK-001 (L-b hysteresis) ---
        rule, phase, _ = ewk_gate(rules, skipped, "EWK-001", "g_min", stage_map, a.stage,
                                  midp_schedules_30=midp_30)
        if rule is None:
            not_run.append({"rule_id": "EWK-001", "parameter": "g_min", "reason": phase})
        elif design is None:
            not_run.append({"rule_id": "EWK-001", "parameter": "g_min",
                            "reason": "no design_grading surface"})
        elif paved_idx is None:
            not_run.append({"rule_id": "EWK-001", "parameter": "g_min",
                            "reason": "unpaved area unknown"})
        else:
            p = param(rule, "g_min")
            g_min = float(p["value"])
            gt = tol_value(p, tolerances, 0.05)
            tin = load_tin(design["path"])
            possibly_flat = []
            for ids, p1, p2, p3, slope, nz, cent, area in iter_faces(tin):
                cx, cy, cz = cent
                if _centroid_in_paved(cx, cy, paved_idx):
                    continue
                rec = _face_rec(ids, p1, p2, p3, slope, cent, area,
                                in_paved=False, dz_m=dz_m, tol_pct=gt)
                # clearly flat = slope < g_min − m; possibly flat = slope < g_min + m
                if slope < g_min + rec["m_pct"]:
                    rec["clearly"] = slope < g_min - rec["m_pct"]
                    possibly_flat.append(rec)
            ewk001_groups = _hysteresis_groups(
                possibly_flat, mode="flat", min_area=min_area, sort_by="area")
            src = {"path": design["path"], "sha256": design["sha256"],
                   "source_type": "script_output", "document_no": doc_no}
            if ewk001_groups:
                total = len(ewk001_groups)
                for g in ewk001_groups[:10]:
                    rec = fail_rec(
                        "EWK-001", "g_min",
                        f"flat group {g['area_m2']} m² @ E{g['centroid_en'][0]} N{g['centroid_en'][1]}",
                        g["mean_slope_pct"], g_min, "ge", "%", src,
                        status_override="Probable", link_cap="Probable",
                        xy_pt=g["centroid_en"], tolerance=gt, total_count=total,
                        document_no=doc_no,
                        note=f"faces={g['face_count']}; mean={g['mean_slope_pct']}%",
                    )
                    checks.append(rec)
            else:
                passed.append({"rule_id": "EWK-001", "parameter": "g_min", "result": "pass",
                               "observed": "no flat groups ≥ threshold", "required": g_min})

        # --- EWK-002 slopes (L-b hysteresis) ---
        rule, phase, milestone = ewk_gate(rules, skipped, "EWK-002", "slope_min_h_per_v",
                                          stage_map, a.stage, midp_schedules_30=midp_30)
        if rule is None:
            not_run.append({"rule_id": "EWK-002", "parameter": "slope_min_h_per_v", "reason": phase})
        elif design is None:
            not_run.append({"rule_id": "EWK-002", "parameter": "slope_min_h_per_v",
                            "reason": "no design_grading surface"})
        else:
            p = param(rule, "slope_min_h_per_v")
            h_per_v = float(p["value"])
            gt = tol_value(p, tolerances, 0.05)
            limit_pct = 100.0 / h_per_v          # D9: required = 50, not 50.05
            tin = load_tin(design["path"])
            possibly_steep, walls = [], []
            for ids, p1, p2, p3, slope, nz, cent, area in iter_faces(tin):
                cx, cy, cz = cent
                in_paved = _centroid_in_paved(cx, cy, paved_idx)
                if slope > wall_pct:
                    walls.append({"ids": ids, "slope": slope, "centroid": cent, "area": area,
                                  "z_min": min(p1[2], p2[2], p3[2]),
                                  "z_max": max(p1[2], p2[2], p3[2]),
                                  "in_paved": in_paved})
                    continue
                if in_paved:
                    continue
                rec = _face_rec(ids, p1, p2, p3, slope, cent, area,
                                in_paved=False, dz_m=dz_m, tol_pct=gt)
                # clearly steep > L + m; possibly steep > L − m (and ≤ wall)
                if slope > limit_pct - rec["m_pct"]:
                    rec["clearly"] = slope > limit_pct + rec["m_pct"]
                    possibly_steep.append(rec)
            ewk002_groups = _hysteresis_groups(
                possibly_steep, mode="steep", min_height=min_height, sort_by="height")
            exist_tin = load_tin(existing["path"]) if existing else None
            _cut_fill(ewk002_groups, exist_tin, cell_m=cell_m)
            src = {"path": design["path"], "sha256": design["sha256"],
                   "source_type": "script_output", "document_no": doc_no}
            if ewk002_groups:
                total = len(ewk002_groups)
                for g in ewk002_groups[:10]:
                    # DL-23: mean and max slope and H:V
                    hv = g.get("hv")
                    hv_max = g.get("hv_max")
                    note = (f"mean={g['mean_slope_pct']}% (H:V≈{hv}); "
                            f"max={g['max_slope_pct']}% (H:V≈{hv_max})")
                    checks.append(fail_rec(
                        "EWK-002", "slope_min_h_per_v",
                        f"steep group h={g['height_m']} m {g.get('cut_fill', '?')} @ "
                        f"E{g['centroid_en'][0]} N{g['centroid_en'][1]}",
                        g["mean_slope_pct"], limit_pct, "le", "%", src,
                        status_override="Probable", link_cap="Probable",
                        xy_pt=g["centroid_en"], tolerance=gt, total_count=total,
                        document_no=doc_no,
                        note=note,
                    ))
            else:
                passed.append({"rule_id": "EWK-002", "parameter": "slope_min_h_per_v",
                               "result": "pass", "observed": "no steep groups ≥ height threshold",
                               "required": limit_pct})

        # --- EWK-002 FoS (DD only) ---
        rule, phase, _ = ewk_gate(rules, skipped, "EWK-002", "fos_static", stage_map, a.stage,
                                  midp_schedules_30=midp_30)
        if rule is None:
            not_run.append({"rule_id": "EWK-002", "parameter": "fos_static", "reason": phase})
        else:
            fos = read_fos(a.report)
            p_s = param(rule, "fos_static")
            p_e = param(rule, "fos_seismic")
            lim_s = float(p_s["value"]) if p_s else 1.5
            lim_e = float(p_e["value"]) if p_e else 1.1
            if fos is None or (fos.get("static") is None and fos.get("seismic") is None):
                if ewk002_groups:
                    src = {"source_type": "script_output",
                           "extract": f"searched: {a.report or '(no report given)'}"}
                    checks.append(fail_rec(
                        "EWK-002", "fos_static",
                        "slope stability calculation (A12)",
                        "not found", lim_s, "ge", None, src,
                        status_override="Missing information",
                        issue_type="missing_information",
                        note="steep groups present; no FoS text found",
                    ))
                else:
                    not_run.append({"rule_id": "EWK-002", "parameter": "fos_static",
                                    "reason": "no steep groups and no FoS text"})
            else:
                src = {"source_type": fos["source_type"], "path": fos.get("path"),
                       "sha256": fos.get("sha256"),
                       "extract": f"FoS static={fos.get('static')} seismic={fos.get('seismic')}"}
                justification = False
                if fos.get("static") is not None and fos["static"] < lim_s:
                    checks.append(fail_rec("EWK-002", "fos_static", "FoS static",
                                           fos["static"], lim_s, "ge", None, src,
                                           status_override="Probable"))
                elif fos.get("static") is not None and fos["static"] >= lim_s:
                    justification = True
                if fos.get("seismic") is not None and fos["seismic"] < lim_e:
                    checks.append(fail_rec("EWK-002", "fos_seismic", "FoS seismic",
                                           fos["seismic"], lim_e, "ge", None, src,
                                           status_override="Probable"))
                elif fos.get("seismic") is not None and fos["seismic"] >= lim_e:
                    justification = True
                if justification and not any(c["rule_id"] == "EWK-002" and c["parameter"].startswith("fos")
                                             and c["result"] == "fail" for c in checks):
                    passed.append({"rule_id": "EWK-002", "parameter": "fos_static", "result": "pass",
                                   "observed": fos, "required": {"static": lim_s, "seismic": lim_e},
                                   "note": "justification found — approval to be confirmed"})

        def _public_groups(groups):
            return [{k: v for k, v in g.items() if k != "_faces"} for g in groups]

        out = {
            "tool": {"script": "check_slopes.py", "version": __version__},
            "mode": "slopes",
            "stage": a.stage,
            "slope_precision_dz_m": dz_m,
            "checks": checks,
            "passed": passed,
            "not_run": not_run,
            "ewk001_groups": _public_groups(ewk001_groups),
            "ewk002_groups": _public_groups(ewk002_groups),
            "wall_faces": len(walls),
            "walls": [{"centroid": list(w["centroid"][:2]), "slope": w["slope"]} for w in walls[:50]],
        }
        atomic_write_json(a.out, out)
        emit({"status": "ok", "out": a.out, "checks": len(checks), "passed": len(passed),
              "not_run": len(not_run), "ewk001_groups": len(ewk001_groups),
              "ewk002_groups": len(ewk002_groups)})
        return EXIT_OK

    return main_guard(run)


if __name__ == "__main__":
    sys.exit(main())
