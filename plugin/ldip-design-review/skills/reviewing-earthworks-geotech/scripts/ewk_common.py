"""Shared helpers for skill 13 reviewing-earthworks-geotech (SPC-0006 Rev L §4.14 + L-a).

Copied patterns from skill 10 (road_common / tin_surface / c3_common) — skills stay independent;
do not import across skills. Rule values come only from skill 8 get_rules.py --agent RDE.
Detection thresholds live in references/earthworks_params.json (not rule values).
"""
__version__ = "0.1.0"

import hashlib
import json
import math
import os
import re
import sys
from pathlib import Path

EXIT_OK, EXIT_INPUT, EXIT_DEPENDENCY, EXIT_SCHEMA = 0, 2, 3, 4
MAX_PATH = 250
STATUS_RANK = {"Confirmed": 3, "Missing information": 3, "Probable": 2, "Not auto-verifiable": 1}
EVIDENCE_TIER = {"dwg_vector": "deterministic", "landxml": "deterministic", "script_output": "deterministic",
                 "pdf_vector_text": "extracted_text_crop", "pdf_ocr": "extracted_text_crop"}
CONFIDENCE = {"deterministic": "High", "extracted_text_crop": "Medium"}

# Amendment L-a DL-14: these (rule, parameter) pairs raise findings at PD / 30 %.
# FoS, frontage / 0.30 m, and EWK-101 stay at DD (60 %).
PD_ACTIVE = {
    ("EWK-001", "g_min"),
    ("EWK-002", "slope_min_h_per_v"),
    ("EWK-003", "positive_drainage"),
    ("EWK-102", "required_element"),
}

ROLE_TITLE_HINTS = (
    (("ngl", "existing", "survey", "egl", "topo"), "existing"),
    (("grading", "mass grading", "rough grading", "formation"), "design_grading"),
    (("road surface", "roads surface", "corridor", "tos", "road top"), "road_top"),
    (("finished grading", "finished levels"), "finished_grading"),
)


class EwkError(Exception):
    def __init__(self, code, detail):
        super().__init__(detail)
        self.code, self.detail = code, detail


def check_path(path, must_exist=False):
    p = Path(path)
    if len(str(p.resolve())) > MAX_PATH:
        raise EwkError(EXIT_INPUT, f"path longer than {MAX_PATH} characters: {path}")
    if must_exist and not p.is_file():
        raise EwkError(EXIT_INPUT, f"file not found: {path}")
    return p


def read_json(path):
    try:
        return json.loads(check_path(path, must_exist=True).read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise EwkError(EXIT_INPUT, f"not valid JSON: {path}: {exc}") from exc


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def atomic_write_json(path, obj):
    p = check_path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(obj, indent=1, sort_keys=True, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, p)


def emit(obj):
    print(json.dumps(obj, ensure_ascii=False, sort_keys=True))


def lower(a, b):
    return a if STATUS_RANK.get(a, 0) <= STATUS_RANK.get(b, 0) else b


def fail_exit(code, detail):
    emit({"status": "error", "code": code, "detail": detail})
    return code


def main_guard(fn):
    try:
        return fn()
    except EwkError as e:
        return fail_exit(e.code, e.detail)


def rules_index(rb):
    return {r["rule_id"]: r for r in rb.get("rules", [])}, rb.get("rules_skipped", []), rb.get("stage_map") or {}


def param(rule, name):
    p = (rule.get("parameters") or {}).get(name)
    if p is None or p.get("tag") == "GAP":
        return None
    return p


def tol_value(p, tolerances=None, default=0.0):
    """Parameter tolerance, or tolerances[tolerance_ref], else default."""
    if not p:
        return default
    t = p.get("tolerance") or {}
    if t.get("value") is not None:
        return float(t["value"])
    ref = p.get("tolerance_ref")
    if ref and tolerances and ref in tolerances:
        return float(tolerances[ref]["value"])
    return default


def phase_of(stage_map, stage):
    for name, info in (stage_map or {}).items():
        if name == stage or name.lower() in stage.lower() or stage.lower() in name.lower():
            return info.get("phase"), info.get("milestone")
    if "60" in stage or "dd" in stage.lower() or "detailed" in stage.lower():
        return "DD", 60
    if "30" in stage or "pd" in stage.lower() or "concept" in stage.lower():
        return "PD", 30
    return None, None


def ewk_gate(rules, skipped, rid, pname, stage_map, stage, *, midp_schedules_30=None):
    """Return (rule, phase, milestone) or (None, reason, None).

    Inactive rules never run. Amendment L-a DL-14 widens PD for selected
    (rule, parameter) pairs; other EWK parameters stay DD-only.

    D7: when MIDP is supplied and does **not** schedule earthworks carriers at 30 %,
    PD findings are not raised (`midp_schedules_30 is False`).
    `None` = MIDP not consulted (L-a PD_ACTIVE applies).
    """
    if rid in {s.get("rule_id") for s in skipped}:
        return None, f"{rid} inactive / skipped", None
    rule = rules.get(rid)
    if rule is None:
        return None, f"{rid} not in rules file", None
    if rule.get("state") not in ("active", "advisory"):
        return None, f"{rid} state {rule.get('state')!r} — not run", None
    phase, milestone = phase_of(stage_map, stage)
    if phase is None:
        return None, f"stage '{stage}' not mapped", None
    req = rule.get("required_from_phase") or "DD"
    if phase == "PD" and req == "DD":
        if midp_schedules_30 is False:
            return None, "not scheduled at stage by MIDP", None
        if (rid, pname) in PD_ACTIVE:
            return rule, phase, milestone
        return None, "not required at stage", None
    return rule, phase, milestone


def load_params(site=None):
    """Detection thresholds and optional site overrides from references/earthworks_params.json."""
    path = Path(__file__).resolve().parents[1] / "references" / "earthworks_params.json"
    doc = read_json(path) if path.is_file() else {}
    base = dict(doc.get("detection") or {})
    site_p = {}
    if site:
        sites = doc.get("sites") or {}
        site_p = dict(sites.get(site) or {})
        for k in ("plot_layers", "surface_roles", "wall_layers", "protection_layers"):
            if k in site_p and k not in base:
                base[k] = site_p[k]
    return base, site_p, doc


def propose_role_from_title(title):
    t = (title or "").lower()
    for keys, role in ROLE_TITLE_HINTS:
        if any(k in t for k in keys):
            return role
    return None


def midp_model_match(model, midp_entry):
    """DL-15 / ADDENDUM / D2: match by document number / source file first, then title+role."""
    num = (midp_entry.get("number") or midp_entry.get("deliverable") or "").lower()
    title = (midp_entry.get("title") or "").lower()
    role = midp_entry.get("role")
    m_doc = (model.get("document_no") or "").lower()
    m_src = (model.get("source_file") or "").lower()
    m_num = (model.get("number") or model.get("name") or "").lower()
    m_title = (model.get("title") or model.get("name") or "").lower()
    m_role = model.get("role")
    m_internal = (model.get("internal_name") or "").lower()
    # D2: source file / document number first
    if num:
        if num in m_doc or m_doc and m_doc in num:
            return True
        if num in m_src or (m_src and num.replace("-", "") in m_src.replace("-", "")):
            return True
        if num in m_num or num in m_internal or (m_num and m_num in num):
            return True
    if role and m_role and role == m_role:
        if title and (title in m_title or m_title in title or any(
                w in m_title for w in title.split() if len(w) > 3)):
            return True
    if title and m_title and (title in m_title or m_title in title):
        return True
    return False


def norm_format(fmt):
    """Normalise exchange format labels for DL-15 comparison."""
    f = (fmt or "").strip().lower()
    if f in ("xml", "landxml"):
        return "xml"
    if f in ("ifc",):
        return "ifc"
    if f in ("dwg",):
        return "dwg"
    if f in ("nwc", "nwd"):
        return "nwc"
    if f in ("dwfx", "dwf"):
        return "dwfx"
    if f in ("pdf",):
        return "pdf"
    return f


def adapt_midp_rows(midp_doc, *, stage_hint="30"):
    """D5: adapt skill 6 `read_midp.py` output → earthworks MIDP model entries.

    Keeps rows whose exchanges mention the stage hint (default 30 % / Concept) and
    whose number/title look like NGL / grading / road-surface models, or any row
    at that stage with ROD-MOD-90000 / IRO-MOD.
    """
    rows = midp_doc.get("rows") if isinstance(midp_doc, dict) else midp_doc
    if not isinstance(rows, list):
        rows = (midp_doc or {}).get("models") or (midp_doc or {}).get("deliverables") or []
    out = []
    hint = (stage_hint or "30").lower()
    for r in rows:
        exs = r.get("exchanges") or []
        stages = " ".join((e.get("stage") or "") for e in exs).lower()
        stage_field = (r.get("stage") or "").lower()
        at_stage = (hint in stages or hint in stage_field
                    or "concept" in stages or r.get("milestone") in (30, "30"))
        if r.get("stage") in ("30%", "30% Concept Design", "PD"):
            at_stage = True
        if not at_stage and r.get("milestone") not in (30, "30"):
            continue
        num = r.get("number") or r.get("deliverable") or ""
        title = r.get("title") or ""
        blob = f"{num} {title}".lower()
        looks_ewk = any(k in blob for k in (
            "rod-mod-90000", "iro-mod", "grading", "ngl", "egl", "road surface",
            "roads surface", "mass grading", "site grading",
        ))
        if not looks_ewk and not r.get("role"):
            continue
        role = r.get("role") or propose_role_from_title(title)
        out.append({
            "number": num,
            "title": title,
            "role": role,
            "formats": list(r.get("formats") or r.get("exchange_formats") or []),
            "stage": r.get("stage") or "30% Concept Design",
            "milestone": r.get("milestone") if r.get("milestone") is not None else 30,
        })
    return out


# --- TIN geometry (copied from skill 10 tin_surface.py; skills stay independent) ---

def load_tin(path):
    doc = read_json(path)
    if not doc.get("points") or not doc.get("faces"):
        raise EwkError(EXIT_INPUT, f"surface has no points/faces: {path}")
    return doc


def point_map(tin):
    out = {}
    for row in tin.get("points") or []:
        if len(row) < 4:
            continue
        pid, e, n, z = row[0], float(row[1]), float(row[2]), float(row[3])
        if pid is None:
            continue
        out[str(pid)] = (e, n, z)
    return out


def face_plane_slope_pct(p1, p2, p3):
    (x1, y1, z1), (x2, y2, z2), (x3, y3, z3) = p1, p2, p3
    ux, uy, uz = x2 - x1, y2 - y1, z2 - z1
    vx, vy, vz = x3 - x1, y3 - y1, z3 - z1
    nx = uy * vz - uz * vy
    ny = uz * vx - ux * vz
    nz = ux * vy - uy * vx
    if abs(nz) < 1e-12:
        return None, nx, ny, nz
    a = -nx / nz
    b = -ny / nz
    return 100.0 * math.sqrt(a * a + b * b), nx, ny, nz


def face_centroid(p1, p2, p3):
    return ((p1[0] + p2[0] + p3[0]) / 3.0,
            (p1[1] + p2[1] + p3[1]) / 3.0,
            (p1[2] + p2[2] + p3[2]) / 3.0)


def face_area_2d(p1, p2, p3):
    return abs((p2[0] - p1[0]) * (p3[1] - p1[1]) - (p3[0] - p1[0]) * (p2[1] - p1[1])) / 2.0


def iter_faces(tin):
    """Yield (face_ids, p1, p2, p3, slope_pct, nz, centroid, area2d)."""
    pts = point_map(tin)
    for face in tin.get("faces") or []:
        if len(face) < 3:
            continue
        ids = [str(face[0]), str(face[1]), str(face[2])]
        try:
            p1, p2, p3 = pts[ids[0]], pts[ids[1]], pts[ids[2]]
        except KeyError:
            continue
        slope, _nx, _ny, nz = face_plane_slope_pct(p1, p2, p3)
        if slope is None:
            continue
        area = face_area_2d(p1, p2, p3)
        if area < 1e-12:
            continue
        yield ids, p1, p2, p3, slope, nz, face_centroid(p1, p2, p3), area


def make_flat_tin(origin_e, origin_n, size_e, size_n, z00, z10, z01, z11=None, *, surface="SYN", index=1):
    e0, n0 = origin_e, origin_n
    e1, n1 = origin_e + size_e, origin_n + size_n
    if z11 is None:
        z11 = z10 + z01 - z00
    points = [
        ["1", e0, n0, float(z00)],
        ["2", e1, n0, float(z10)],
        ["3", e0, n1, float(z01)],
        ["4", e1, n1, float(z11)],
    ]
    faces = [["1", "2", "4"], ["1", "4", "3"]]
    return {"template": "landxml-surface-1.0", "surface": surface, "index": index,
            "points": points, "faces": faces, "breaklines": []}


def group_faces_by_vertex(face_records):
    """Union-find groups of faces that share a vertex. face_records: list of dicts with 'ids'."""
    parent = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    vert_to_faces = {}
    for i, rec in enumerate(face_records):
        for vid in rec["ids"]:
            if vid in vert_to_faces:
                union(i, vert_to_faces[vid])
            else:
                vert_to_faces[vid] = i
            vert_to_faces[vid] = find(i)
    groups = {}
    for i in range(len(face_records)):
        groups.setdefault(find(i), []).append(i)
    return list(groups.values())


def tin_raster_z(tin, cell_m=1.0):
    """Exact TIN raster: dict (ix, iy) -> z at cell centre (barycentric)."""
    pts = point_map(tin)
    if not pts:
        return {}, None, None, None, None
    es = [p[0] for p in pts.values()]
    ns = [p[1] for p in pts.values()]
    e0, e1 = min(es), max(es)
    n0, n1 = min(ns), max(ns)
    grid = {}
    for face in tin.get("faces") or []:
        if len(face) < 3:
            continue
        try:
            p1, p2, p3 = pts[str(face[0])], pts[str(face[1])], pts[str(face[2])]
        except KeyError:
            continue
        xs = [p1[0], p2[0], p3[0]]
        ys = [p1[1], p2[1], p3[1]]
        ix0 = int(math.floor((min(xs) - e0) / cell_m))
        ix1 = int(math.floor((max(xs) - e0) / cell_m))
        iy0 = int(math.floor((min(ys) - n0) / cell_m))
        iy1 = int(math.floor((max(ys) - n0) / cell_m))
        for ix in range(ix0, ix1 + 1):
            for iy in range(iy0, iy1 + 1):
                cx = e0 + (ix + 0.5) * cell_m
                cy = n0 + (iy + 0.5) * cell_m
                z = _bary_z(p1, p2, p3, cx, cy)
                if z is not None:
                    grid[(ix, iy)] = z
    return grid, e0, n0, e1, n1


def _bary_z(p1, p2, p3, x, y):
    x1, y1, z1 = p1
    x2, y2, z2 = p2
    x3, y3, z3 = p3
    denom = (y2 - y3) * (x1 - x3) + (x3 - x2) * (y1 - y3)
    if abs(denom) < 1e-18:
        return None
    a = ((y2 - y3) * (x - x3) + (x3 - x2) * (y - y3)) / denom
    b = ((y3 - y1) * (x - x3) + (x1 - x3) * (y - y3)) / denom
    c = 1.0 - a - b
    if a < -1e-9 or b < -1e-9 or c < -1e-9:
        return None
    return a * z1 + b * z2 + c * z3


def priority_flood(grid, trap_depth_m=0.05):
    """Barnes-style priority-flood; return list of depression dicts (cells, depth, area cells).

    Site-edge cells (neighbours missing from grid) are outlets. Depression = filled - z > trap_depth_m.
    """
    import heapq
    if not grid:
        return []
    cells = set(grid.keys())
    filled = {}
    pq = []
    # Seed outlets: cells with a 4-neighbour outside the grid
    for (ix, iy), z in grid.items():
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            if (ix + dx, iy + dy) not in cells:
                heapq.heappush(pq, (z, ix, iy))
                filled[(ix, iy)] = z
                break
    if not pq:
        # closed domain: seed the lowest cell as outlet
        (ix, iy), z = min(grid.items(), key=lambda kv: kv[1])
        heapq.heappush(pq, (z, ix, iy))
        filled[(ix, iy)] = z
    while pq:
        f, ix, iy = heapq.heappop(pq)
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1)):
            n = (ix + dx, iy + dy)
            if n not in cells or n in filled:
                continue
            nz = grid[n]
            nf = max(f, nz)
            filled[n] = nf
            heapq.heappush(pq, (nf, n[0], n[1]))
    # Collect depression cells
    dep_cells = {c: (filled[c] - grid[c]) for c in cells
                 if c in filled and (filled[c] - grid[c]) > trap_depth_m}
    # 8-connected components
    seen = set()
    depressions = []
    for seed in dep_cells:
        if seed in seen:
            continue
        stack = [seed]
        seen.add(seed)
        comp = []
        while stack:
            c = stack.pop()
            comp.append(c)
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1)):
                n = (c[0] + dx, c[1] + dy)
                if n in dep_cells and n not in seen:
                    seen.add(n)
                    stack.append(n)
        depths = [dep_cells[c] for c in comp]
        depressions.append({
            "cells": comp,
            "n_cells": len(comp),
            "depth_m": max(depths),
            "mean_depth_m": sum(depths) / len(depths),
            "volume_m3": sum(depths),  # × cell_m² applied by caller
        })
    return depressions


def fail_rec(rid, pname, subject, observed, required, op, unit, src, *, result="fail",
             link_cap=None, status_override=None, issue_type=None, mode_tag=None,
             note=None, xy_pt=None, severity_override=None, tolerance=None, total_count=None,
             document_no=None):
    ev = {**(src or {}), "source_type": (src or {}).get("source_type") or "script_output",
          "extract": (src or {}).get("extract") or f"{subject}: {observed}"}
    if xy_pt is not None:
        ev["xy"] = list(xy_pt) if not isinstance(xy_pt, list) else xy_pt
    if document_no:
        ev["document_no"] = document_no
    r = {"rule_id": rid, "parameter": pname, "subject": subject, "observed": observed,
         "required": required, "comparison": op,
         "tolerance": 0.0 if tolerance is None else float(tolerance),
         "unit": unit, "result": result, "evidence": ev}
    if link_cap:
        r["link_cap"] = link_cap
    if status_override:
        r["status_override"] = status_override
    if issue_type:
        r["issue_type"] = issue_type
    if mode_tag:
        r["mode_tag"] = mode_tag
    if note:
        r["note"] = note
    if severity_override:
        r["severity_override"] = severity_override
    if total_count is not None:
        r["total_count"] = int(total_count)
    return r


if __name__ == "__main__":
    sys.exit(0)
