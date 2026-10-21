#!/usr/bin/env python3
"""EWK-001 / EWK-002 slope checks for skill 13 (SPC-0006 Rev L §4.14.7–8 + L-a DL-14/18).

EWK-001: unpaved faces < g_min − grade_pct, grouped; drop groups < ewk001_min_area_m2.
EWK-002: unpaved faces > 100/H:V + grade_pct, grouped; drop height < ewk002_min_height_m
         (at PD / 30 % EWK-102 uses 1.0 m — see check_presence); curbs and walls excluded.
FoS text from design_report at DD only (L-a).

Usage:
  python check_slopes.py --surfaces <surfaces.json> --rules <rules_RDE.json>
      --stage "<stage>" [--site SITE] [--report <design_report.pdf.json>]
      [--existing-dz] --out <slopes.json>
"""
__version__ = "0.1.0"

import argparse
import math
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from ewk_common import (EXIT_INPUT, EXIT_OK, EwkError, atomic_write_json, emit, ewk_gate,  # noqa: E402
                        fail_rec, group_faces_by_vertex, iter_faces, load_params, load_tin,
                        main_guard, param, point_map, read_json, rules_index, tol_value)


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


def _centroid_in_paved(cx, cy, paved):
    if not paved or not paved.get("cells"):
        return False
    e0, n0, cell = paved["e0"], paved["n0"], paved["cell_m"]
    ix = int(math.floor((cx - e0) / cell))
    iy = int(math.floor((cy - n0) / cell))
    # cells may be list of lists (JSON) or tuples
    cells = paved["cells"]
    if cells and isinstance(cells[0], (list, tuple)):
        return [ix, iy] in cells or (ix, iy) in {tuple(c) for c in cells}
    return (ix, iy) in set(map(tuple, cells))


def _collect_faces(tin, paved, *, min_slope=None, max_slope=None, wall_slope_pct=1000.0):
    """Return face records and wall faces (slope > wall_slope_pct)."""
    faces, walls, curb = [], [], []
    for ids, p1, p2, p3, slope, nz, cent, area in iter_faces(tin):
        cx, cy, cz = cent
        in_paved = _centroid_in_paved(cx, cy, paved)
        rec = {"ids": ids, "slope": slope, "centroid": (cx, cy, cz), "area": area,
               "z_min": min(p1[2], p2[2], p3[2]), "z_max": max(p1[2], p2[2], p3[2]),
               "in_paved": in_paved}
        if slope > wall_slope_pct:
            walls.append(rec)
            continue
        if in_paved:
            curb.append(rec)
            continue
        if min_slope is not None and slope >= min_slope:
            continue
        if max_slope is not None and slope <= max_slope:
            continue
        faces.append(rec)
    return faces, walls, curb


def _groups(face_records, *, min_area=None, min_height=None):
    if not face_records:
        return []
    idx_groups = group_faces_by_vertex(face_records)
    out = []
    for idxs in idx_groups:
        members = [face_records[i] for i in idxs]
        area = sum(m["area"] for m in members)
        z_min = min(m["z_min"] for m in members)
        z_max = max(m["z_max"] for m in members)
        height = z_max - z_min
        if min_area is not None and area < min_area:
            continue
        if min_height is not None and height < min_height:
            continue
        cx = sum(m["centroid"][0] * m["area"] for m in members) / area
        cy = sum(m["centroid"][1] * m["area"] for m in members) / area
        slopes = sorted(m["slope"] for m in members)
        mean_s = sum(m["slope"] * m["area"] for m in members) / area
        out.append({
            "area_m2": round(area, 3),
            "height_m": round(height, 3),
            "mean_slope_pct": round(mean_s, 3),
            "max_slope_pct": round(slopes[-1], 3),
            "median_slope_pct": round(slopes[len(slopes) // 2], 3),
            "face_count": len(members),
            "centroid_en": [round(cx, 3), round(cy, 3)],
            "hv": round(100.0 / mean_s, 3) if mean_s > 1e-9 else None,
        })
    out.sort(key=lambda g: (-g.get("area_m2", 0), -g.get("height_m", 0)))
    return out


def _cut_fill(groups, design_tin, existing_tin):
    if existing_tin is None:
        for g in groups:
            g["cut_fill"] = "unknown"
        return
    # Sample existing Z at group centroid via nearest face / barycentric — simple: average design−existing
    # at face centroids of a coarse approach: use design faces near centroid
    from ewk_common import _bary_z, point_map
    epts = point_map(existing_tin)
    efaces = existing_tin.get("faces") or []

    def existing_z(e, n):
        best, best_d = None, 1e18
        for face in efaces:
            try:
                p1 = epts[str(face[0])]; p2 = epts[str(face[1])]; p3 = epts[str(face[2])]
            except KeyError:
                continue
            cx = (p1[0] + p2[0] + p3[0]) / 3.0
            cy = (p1[1] + p2[1] + p3[1]) / 3.0
            d = (cx - e) ** 2 + (cy - n) ** 2
            if d < best_d:
                z = _bary_z(p1, p2, p3, e, n)
                if z is not None:
                    best, best_d = z, d
        return best

    dpts = point_map(design_tin)
    for g in groups:
        e, n = g["centroid_en"]
        # design z ≈ mean of nearby — use centroid z from group height mid
        # Recompute from design faces: approximate with existing comparison at centroid
        # Design elevation: average z of design faces — use mid of z_min/z_max stored? We have height only.
        # Sample design tin similarly
        dz = None
        best, best_d = None, 1e18
        for face in design_tin.get("faces") or []:
            try:
                p1 = dpts[str(face[0])]; p2 = dpts[str(face[1])]; p3 = dpts[str(face[2])]
            except KeyError:
                continue
            cx = (p1[0] + p2[0] + p3[0]) / 3.0
            cy = (p1[1] + p2[1] + p3[1]) / 3.0
            d = (cx - e) ** 2 + (cy - n) ** 2
            if d < best_d:
                z = _bary_z(p1, p2, p3, e, n)
                if z is not None:
                    best, best_d = z, d
        ez = existing_z(e, n)
        if best is not None and ez is not None:
            dz = best - ez
            g["cut_fill"] = "cut" if dz < -0.05 else ("fill" if dz > 0.05 else "near_zero")
            g["dz_m"] = round(dz, 3)
        else:
            g["cut_fill"] = "unknown"


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

        by_role = {s["role"]: s for s in surf_doc.get("surfaces") or []}
        paved = surf_doc.get("paved_footprint")
        checks, passed, not_run = [], [], []
        ewk001_groups, ewk002_groups, walls = [], [], []

        design = by_role.get("design_grading") or by_role.get("finished_grading")
        existing = by_role.get("existing")

        # --- EWK-001 ---
        rule, phase, _ = ewk_gate(rules, skipped, "EWK-001", "g_min", stage_map, a.stage)
        if rule is None:
            not_run.append({"rule_id": "EWK-001", "parameter": "g_min", "reason": phase})
        elif design is None:
            not_run.append({"rule_id": "EWK-001", "parameter": "g_min",
                            "reason": "no design_grading surface"})
        elif paved is None:
            not_run.append({"rule_id": "EWK-001", "parameter": "g_min",
                            "reason": "unpaved area unknown"})
        else:
            p = param(rule, "g_min")
            g_min = float(p["value"])
            gt = tol_value(p, tolerances, 0.05)
            threshold = g_min - gt
            tin = load_tin(design["path"])
            flat, _w, _c = _collect_faces(tin, paved, min_slope=threshold)
            # flat faces are those with slope < threshold (max_slope filter inverted)
            # _collect_faces with min_slope skips slope >= min_slope, so remaining are < threshold. Good.
            ewk001_groups = _groups(flat, min_area=min_area)
            src = {"path": design["path"], "sha256": design["sha256"],
                   "source_type": "script_output"}
            if ewk001_groups:
                for g in ewk001_groups[:10]:
                    checks.append(fail_rec(
                        "EWK-001", "g_min",
                        f"flat group {g['area_m2']} m² @ E{g['centroid_en'][0]} N{g['centroid_en'][1]}",
                        g["mean_slope_pct"], g_min, "ge", "%", src,
                        status_override="Probable", link_cap="Probable",
                        xy_pt=g["centroid_en"],
                        note=f"faces={g['face_count']}; 3H:1V preferred is not checked (no parameter)",
                    ))
            else:
                passed.append({"rule_id": "EWK-001", "parameter": "g_min", "result": "pass",
                               "observed": "no flat groups ≥ threshold", "required": g_min})

        # --- EWK-002 slopes ---
        rule, phase, milestone = ewk_gate(rules, skipped, "EWK-002", "slope_min_h_per_v",
                                          stage_map, a.stage)
        if rule is None:
            not_run.append({"rule_id": "EWK-002", "parameter": "slope_min_h_per_v", "reason": phase})
        elif design is None:
            not_run.append({"rule_id": "EWK-002", "parameter": "slope_min_h_per_v",
                            "reason": "no design_grading surface"})
        else:
            p = param(rule, "slope_min_h_per_v")
            h_per_v = float(p["value"])
            gt = tol_value(p, tolerances, 0.05)
            steep_lim = 100.0 / h_per_v + gt  # 50.05 %
            tin = load_tin(design["path"])
            # faces steeper than limit: use max_slope filter inverted → pass min_slope=steep_lim via custom
            steep, walls, curb = [], [], []
            for ids, p1, p2, p3, slope, nz, cent, area in iter_faces(tin):
                cx, cy, cz = cent
                in_paved = _centroid_in_paved(cx, cy, paved)
                rec = {"ids": ids, "slope": slope, "centroid": (cx, cy, cz), "area": area,
                       "z_min": min(p1[2], p2[2], p3[2]), "z_max": max(p1[2], p2[2], p3[2]),
                       "in_paved": in_paved}
                if slope > wall_pct:
                    walls.append(rec)
                    continue
                if in_paved:
                    curb.append(rec)
                    continue
                if slope > steep_lim:
                    steep.append(rec)
            ewk002_groups = _groups(steep, min_height=min_height)
            exist_tin = load_tin(existing["path"]) if existing else None
            _cut_fill(ewk002_groups, tin, exist_tin)
            src = {"path": design["path"], "sha256": design["sha256"],
                   "source_type": "script_output"}
            if ewk002_groups:
                for g in ewk002_groups[:10]:
                    checks.append(fail_rec(
                        "EWK-002", "slope_min_h_per_v",
                        f"steep group h={g['height_m']} m {g.get('cut_fill', '?')} @ "
                        f"E{g['centroid_en'][0]} N{g['centroid_en'][1]}",
                        g["mean_slope_pct"], steep_lim, "le", "%", src,
                        status_override="Probable", link_cap="Probable",
                        xy_pt=g["centroid_en"],
                        note=f"H:V≈{g['hv']}; max={g['max_slope_pct']}%; "
                             f"3H:1V preferred not checked (no parameter)",
                    ))
            else:
                passed.append({"rule_id": "EWK-002", "parameter": "slope_min_h_per_v",
                               "result": "pass", "observed": "no steep groups ≥ height threshold",
                               "required": steep_lim})

        # --- EWK-002 FoS (DD only) ---
        rule, phase, _ = ewk_gate(rules, skipped, "EWK-002", "fos_static", stage_map, a.stage)
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

        out = {
            "tool": {"script": "check_slopes.py", "version": __version__},
            "mode": "slopes",
            "stage": a.stage,
            "checks": checks,
            "passed": passed,
            "not_run": not_run,
            "ewk001_groups": ewk001_groups,
            "ewk002_groups": ewk002_groups,
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
