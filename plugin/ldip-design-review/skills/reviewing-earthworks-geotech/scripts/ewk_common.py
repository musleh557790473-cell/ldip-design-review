"""Shared helpers for skill 13 reviewing-earthworks-geotech (SPC-0006 Rev L §4.14 + L-a + L-b).

Copied patterns from skill 10 (road_common / tin_surface / c3_common) — skills stay independent;
do not import across skills. Rule values come only from skill 8 get_rules.py --agent RDE.
Detection thresholds live in references/earthworks_params.json (not rule values).
"""
__version__ = "0.2.0"

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

# R2: section codes in the deliverable number (field 5 of SRC-02 / MIDP layout)
SITE_SECTION_CODES = {
    "QRWN": ("AQ", "QRWN", "QN"),
    "NRJS": ("AN", "NRJS"),
    "NMAR": ("DN", "NMAR"),
}

# Formats that cannot be judged from surface side files alone (need --inventory) — R1
INVENTORY_ONLY_FORMATS = frozenset({"dwg", "nwc", "dwfx", "pdf", "nwd", "dwf"})

# R3 / S3: model number patterns only — drawing carriers are title-based (not 10300–10600)
EWK_NUMBER_RE = re.compile(
    r"(?i)(ROD-MOD-90000|IRO-MOD)"
)
# R3 / R7 / S3: whole-word / phrase title patterns (negative: "Single-Line Diagram",
# "Road Setting-Out Plan" — not a DL-13 carrier; section numbers differ per site)
EWK_TITLE_RE = re.compile(
    r"(?i)(\bNGL\b|\bEGL\b|existing\s+contour|contour\s+plan|"
    r"cut\s*/\s*fill|cut\s*&\s*fill|cut\s+and\s+fill|stake-?out|"
    r"roads?\s+solids?|site\s+grading|mass\s+grading|rough\s+grading|"
    r"roads?\s+surface|road\s+surface|design\s+grading|"
    r"(?:mass|road|site)\s+grading\s+plan|grading\s+plan|grading\s+contour)"
)
PROXY_TITLE_RE = re.compile(r"(?i)buildingelementproxy|ifc-\d+|proxy\(\d+\)")

ROLE_TITLE_HINTS = (
    (("existing contour", "contour plan", "existing ground"), "existing"),
    (("roads solids", "solids model", "site grading", "mass grading",
      "rough grading", "formation"), "design_grading"),
    (("road surface", "roads surface", "corridor", "tos", "road top"), "road_top"),
    (("finished grading", "finished levels"), "finished_grading"),
    (("cut/fill", "cut & fill", "cut and fill", "stake-out", "stake out",
      "grading plan", "grading contour"), "drawing"),
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
    """Map MIDP title → surface role (R7: Roads Solids → design_grading; contour → existing)."""
    t = (title or "").lower()
    # Whole-word NGL / EGL → existing (R3: not substring of Single-Line)
    if re.search(r"(?i)\bNGL\b|\bEGL\b", title or ""):
        return "existing"
    for keys, role in ROLE_TITLE_HINTS:
        if any(k in t for k in keys):
            return role
    return None


def is_real_title(title):
    """True when title is usable for MIDP fallback (not an IFC proxy name) — R4."""
    t = (title or "").strip()
    if len(t) < 4:
        return False
    return PROXY_TITLE_RE.search(t) is None


def number_key(num):
    """Normalise a deliverable number; strip trailing `-00` / `-0A` revision (R1)."""
    n = (num or "").strip().upper().replace("_", "-")
    if not n:
        return ""
    parts = n.split("-")
    # Strip short revision tokens only (2 chars: 00, 0A, AA) — keep 3-digit sheet (001)
    while len(parts) >= 2 and re.fullmatch(r"[0-9]{2}|0[A-Z]|[A-Z]{2}", parts[-1]):
        parts.pop()
    return "-".join(parts)


def numbers_match(a, b):
    """True when two document numbers refer to the same deliverable (R1 strip rev)."""
    ka, kb = number_key(a), number_key(b)
    if not ka or not kb:
        return False
    if ka == kb:
        return True
    if ka in kb or kb in ka:
        return True
    # dash-insensitive containment of the shorter key
    sa, sb = ka.replace("-", ""), kb.replace("-", "")
    return sa in sb or sb in sa


def row_matches_site(number, site):
    """R2: keep MIDP rows whose section code matches the site (AQ=QRWN, AN=NRJS, DN=NMAR)."""
    if not site:
        return True
    codes = SITE_SECTION_CODES.get(str(site).upper())
    if not codes:
        return True
    num = (number or "").upper()
    parts = num.split("-")
    if len(parts) > 4 and parts[4] in codes:
        return True
    return any(re.search(rf"(?:^|-){re.escape(c)}(?:-|$)", num) for c in codes)


def looks_like_ewk_carrier(number, title):
    """R3: whole-word / number patterns only (not 'ngl' inside Single-Line)."""
    if EWK_NUMBER_RE.search(number or ""):
        return True
    if EWK_TITLE_RE.search(title or ""):
        return True
    return False


def is_midp_model_row(midp_entry):
    """True when the MIDP row is a model deliverable (`-MOD-`), not a drawing."""
    num = (midp_entry.get("number") or midp_entry.get("deliverable") or "").upper()
    return "-MOD-" in num


def is_midp_drawing_row(midp_entry):
    """True when the MIDP row is a drawing deliverable (`-DWG-`)."""
    num = (midp_entry.get("number") or midp_entry.get("deliverable") or "").upper()
    return "-DWG-" in num


def inventory_number_match(inv_item, midp_entry):
    """True when an inventory file number/name matches the MIDP deliverable number."""
    num = midp_entry.get("number") or midp_entry.get("deliverable") or ""
    if not num:
        return False
    for cand in (inv_item.get("number"), inv_item.get("name"),
                 Path(inv_item.get("path") or "").stem,
                 Path(inv_item.get("name") or "").stem):
        if cand and numbers_match(num, cand):
            return True
    return False


def midp_model_match(model, midp_entry, *, allow_role_title=None):
    """Match a surface (or inventory-like) item to a MIDP entry.

    Returns (matched: bool, matched_by: str|None, note: str|None).

    S1 order for every MIDP row:
      1. document number / source file (revision stripped)
      2. role fallback **only for model rows** (`-MOD-`) with no number match (R4)
      3. real title (never IFC proxy names)
    Drawing rows (`-DWG-`) never match a surface via role or title.
    """
    num = midp_entry.get("number") or midp_entry.get("deliverable") or ""
    title = midp_entry.get("title") or ""
    role = midp_entry.get("role")
    m_doc = model.get("document_no") or ""
    m_src = model.get("source_file") or model.get("path") or model.get("name") or ""
    m_num = model.get("number") or ""
    m_title = model.get("title") or model.get("name") or ""
    m_role = model.get("role")
    m_internal = model.get("internal_name") or ""

    for cand in (m_doc, m_src, m_num, m_internal, Path(str(m_src)).stem if m_src else ""):
        if num and cand and numbers_match(num, cand):
            return True, "document_no/source_file", None

    # S1: never role/title-match a drawing row to a surface
    drawing = is_midp_drawing_row(midp_entry)
    model_row = is_midp_model_row(midp_entry)
    if allow_role_title is False or drawing:
        return False, None, None
    if allow_role_title is None and not model_row and not drawing:
        # Compact JSON without -MOD-/-DWG-: keep role/title for backwards compat
        pass
    elif not model_row:
        return False, None, None

    # R4 / S1: old-scheme — role fallback only for model rows when numbers differ
    if role and m_role and role == m_role and role in (
            "existing", "design_grading", "road_top", "finished_grading"):
        note = None
        if num and (m_doc or m_num or m_src) and not numbers_match(num, m_doc or m_num or m_src):
            note = f"number differs from MIDP ({num} vs {m_doc or m_num or m_src})"
        return True, "role", note

    # Real title only (not BUILDINGELEMENTPROXY) — R4 / S1
    if title and is_real_title(m_title):
        tl, mt = title.lower(), m_title.lower()
        if tl in mt or mt in tl or any(w in mt for w in tl.split() if len(w) > 3):
            return True, "title", None
    return False, None, None


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


def adapt_midp_rows(midp_doc, *, stage_hint="30", site=None):
    """D5 / R2 / R3 / R7: skill 6 `read_midp.py` → earthworks MIDP entries.

    Filters by stage, site section code (AQ/AN/DN), and whole-word / number patterns.
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
        if not row_matches_site(num, site):
            continue
        if not looks_like_ewk_carrier(num, title) and not r.get("role"):
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


def load_package_inventory(inv_path):
    """R1: load skill 6 inventory / sheet register / plain file list.

    Returns list of {path, name, format, number}.
    """
    if not inv_path:
        return None
    doc = read_json(inv_path)
    files = []

    def add(path, fmt=None, number=None, name=None):
        p = str(path or "")
        nm = name or Path(p).name
        f = norm_format(fmt or Path(nm).suffix.lstrip("."))
        num = number
        if not num:
            stem = Path(nm).stem
            # document number at start of stem
            m = re.match(r"([A-Za-z0-9]+(?:-[A-Za-z0-9]+){5,})", stem)
            num = m.group(1) if m else stem
        files.append({"path": p, "name": nm, "format": f, "number": num})

    if isinstance(doc, list):
        for item in doc:
            if isinstance(item, str):
                add(item)
            elif isinstance(item, dict):
                add(item.get("path") or item.get("name") or item.get("file"),
                    item.get("format") or item.get("ext"),
                    item.get("number") or (item.get("number_inherited") or {}).get("number"),
                    item.get("name"))
        return files
    if not isinstance(doc, dict):
        return files
    if doc.get("sheet_register"):
        for reg in doc["sheet_register"]:
            base = reg.get("base") or (reg.get("numbers") or [None])[0]
            for f in reg.get("files") or []:
                add(f.get("path") or f.get("name"), f.get("format") or f.get("type"),
                    f.get("number") or base, f.get("name"))
            # carriers.file_name
            for f in (reg.get("carriers") or {}).get("file_name") or []:
                add(f.get("path") or f.get("number"), None, f.get("number") or base)
        return files
    if doc.get("files"):
        for f in doc["files"]:
            inherited = (f.get("number_inherited") or {}).get("number")
            add(f.get("path") or f.get("name"), f.get("format") or f.get("ext"),
                f.get("number") or inherited, f.get("name"))
    return files


def formats_present_for(entry, surfaces, inv_files, *, matched_surface=None, matched_by=None):
    """Union of formats from matched surfaces and inventory files (R1 / S2).

    After a role match, also look up inventory with the matched file's **own**
    document number (old scheme, e.g. NRJS-IRO-MOD-00-00001).
    """
    have = set()
    num = entry.get("number") or entry.get("deliverable") or ""
    lookup_nums = []
    if num:
        lookup_nums.append(num)
    # S2: role-matched surface → judge formats under the file's own number
    if matched_by == "role" and matched_surface:
        own = matched_surface.get("document_no") or matched_surface.get("number") or ""
        if not own:
            src = matched_surface.get("source_file") or ""
            own = Path(src).stem if src else ""
        elif "." in Path(str(own)).name:
            own = Path(str(own)).stem
        if own:
            lookup_nums.append(own)
    for s in surfaces or []:
        ok, _by, _note = midp_model_match({
            "document_no": s.get("document_no"), "source_file": s.get("source_file"),
            "number": s.get("document_no"), "title": s.get("title"),
            "role": s.get("role"), "internal_name": s.get("internal_name"),
            "path": s.get("path"),
        }, entry)
        if ok:
            have.add(norm_format(s.get("source_format")))
    for f in inv_files or []:
        f_num = f.get("number") or f.get("name") or ""
        f_stem = Path(f.get("path") or "").stem
        for look in lookup_nums:
            if numbers_match(look, f_num) or numbers_match(look, f_stem):
                have.add(norm_format(f.get("format")))
                break
        else:
            if inventory_number_match(f, entry):
                have.add(norm_format(f.get("format")))
    return {h for h in have if h}


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


def face_perimeter_2d(p1, p2, p3):
    """Plan perimeter of a triangular face (m)."""
    return (math.hypot(p2[0] - p1[0], p2[1] - p1[1])
            + math.hypot(p3[0] - p2[0], p3[1] - p2[1])
            + math.hypot(p1[0] - p3[0], p1[1] - p3[1]))


def slope_uncertainty_pct(area_m2, perimeter_m, dz_m):
    """L-b DL-19: u = 100 · δz · P / (2 · A) [%]."""
    if area_m2 <= 1e-18:
        return float("inf")
    return 100.0 * float(dz_m) * float(perimeter_m) / (2.0 * float(area_m2))


def vert_coord_key(e, n, *, round_m=0.001):
    """Vertex key rounded to millimetres (L-b DL-20)."""
    return (int(round(float(e) / round_m)), int(round(float(n) / round_m)))


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


def group_faces_by_vertex(face_records, *, coord_round_m=0.001):
    """Union-find groups of faces that share a vertex coordinate (L-b DL-20).

    Prefer rec['verts'] as iterable of (e, n[, z]); fall back to rec['ids'] (legacy).
    Coordinates are rounded to coord_round_m (default 1 mm) so IFC/LandXML point IDs
    do not split one physical vertex into two groups.
    """
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
        keys = []
        verts = rec.get("verts")
        if verts:
            for v in verts:
                keys.append(vert_coord_key(v[0], v[1], round_m=coord_round_m))
        else:
            for vid in rec.get("ids") or []:
                keys.append(("id", str(vid)))
        for key in keys:
            if key in vert_to_faces:
                union(i, vert_to_faces[key])
            else:
                vert_to_faces[key] = i
            vert_to_faces[key] = find(i)
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
