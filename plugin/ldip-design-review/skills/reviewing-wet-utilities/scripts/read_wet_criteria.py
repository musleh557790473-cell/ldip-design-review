#!/usr/bin/env python3
"""Read wet-utility design criteria from skill-2 PDF text JSON (skill 11).

Follows skill 10 ``read_criteria`` pattern: heading / keyword locator on
``*.pdf.json`` vector words → ``criteria.json`` with clause and page.
Also extracts storm Table 10.9 (10 columns, unique by label) and sewer
Tables 6.1 / 6.3. Skips TOC pages; anchors network totals to Configuration
sub-sections (§5.5.1 / §6.5.1 / §10.5.5).

Usage:
  python read_wet_criteria.py --text <report.pdf.json> --out <criteria.json>
      [--pages 1-400]

Exit: 0 written (also when no table found) · 2 input · 4 schema. No network, no shell.
"""
__version__ = "0.1.1"

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from wet_common import (  # noqa: E402
    EXIT_INPUT, EXIT_OK, EXIT_SCHEMA, SKILL_ROOT, atomic_write_json, emit, fail,
    main_guard, path_ok, read_json,
)

SCHEMA_PATH = SKILL_ROOT / "templates" / "criteria.schema.json"
LINE_TOL = 2.5
TOC_EARLY_MAX_PAGE = 20

# Section numbers must not match inside longer numbers (e.g. 5.5 inside 10.5.5).
_SEC = r"(?<![\d.])"

SECTION_HINTS = {
    # Prefer specific network keywords; section numbers use _SEC lookbehind.
    "storm": re.compile(
        r"storm\s*water|stormwater|drainage\s+network|surface\s+water|"
        rf"(?:§\s*)?{_SEC}10\.2\b|(?:§\s*)?{_SEC}10\.5(?:\.\d+)?\b|Table\s*10\.9\b",
        re.I,
    ),
    "sewer": re.compile(
        r"sewer|wastewater|foul|gravity\s+sewer|force\s+main|"
        rf"(?:§\s*)?{_SEC}6\.3\b|(?:§\s*)?{_SEC}6\.5(?:\.\d+)?\b|Table\s*6\.[13]\b",
        re.I,
    ),
    "potable_water": re.compile(
        r"potable\s+water|water\s+supply|fire\s+fighting|"
        rf"(?:§\s*)?{_SEC}5\.3\b.{{0,40}}(?:design|criteria|potable|water)|"
        rf"(?:§\s*)?{_SEC}5\.5(?:\.\d+)?\b",
        re.I,
    ),
    "irrigation": re.compile(r"irrigation|TSE|treated\s+sewage", re.I),
}

CLAUSE_HINTS = [
    ("potable_water", "§5.3", re.compile(
        rf"(?:§\s*)?{_SEC}5\.3\b|section\s+5\.3\b", re.I)),
    ("sewer", "§6.3", re.compile(rf"(?:§\s*)?{_SEC}6\.3\b|section\s+6\.3\b", re.I)),
    ("sewer", "Table 6.1", re.compile(r"Table\s*6\.1\b", re.I)),
    ("sewer", "Table 6.3", re.compile(r"Table\s*6\.3\b", re.I)),
    ("storm", "§10.2", re.compile(rf"(?:§\s*)?{_SEC}10\.2\b|section\s+10\.2\b", re.I)),
    ("storm", "Table 10.9", re.compile(r"Table\s*10\.9\b", re.I)),
]

# Configuration anchors for declared totals (S3)
CONFIG_ANCHORS = {
    "potable_water": re.compile(rf"(?:§\s*)?{_SEC}5\.5(?:\.1)?\b", re.I),
    "sewer": re.compile(rf"(?:§\s*)?{_SEC}6\.5(?:\.1)?\b", re.I),
    "storm": re.compile(rf"(?:§\s*)?{_SEC}10\.5(?:\.5)?\b", re.I),
}

CRITERION_PATTERNS = [
    ("min_diameter_mm", re.compile(
        r"min(?:imum)?\s+(?:pipe\s+)?diam(?:eter)?[^0-9]{0,20}(\d+(?:\.\d+)?)\s*mm", re.I)),
    ("min_slope_pct", re.compile(
        r"min(?:imum)?\s+(?:slope|gradient)[^0-9%]{0,20}(\d+(?:\.\d+)?)\s*%", re.I)),
    ("min_velocity_ms", re.compile(
        r"min(?:imum)?\s+veloc(?:ity)?[^0-9]{0,20}(\d+(?:\.\d+)?)\s*m\s*/?\s*s", re.I)),
    ("max_velocity_ms", re.compile(
        r"max(?:imum)?\s+veloc(?:ity)?[^0-9]{0,20}(\d+(?:\.\d+)?)\s*m\s*/?\s*s", re.I)),
    ("max_depth_m", re.compile(
        r"max(?:imum)?\s+depth[^0-9]{0,20}(\d+(?:\.\d+)?)\s*m\b", re.I)),
    ("cover_m", re.compile(
        r"(?:min(?:imum)?\s+)?cover[^0-9]{0,20}(\d+(?:\.\d+)?)\s*m\b", re.I)),
    ("mh_spacing_m", re.compile(
        r"(?:manhole|MH)\s+spac(?:ing|e)[^0-9]{0,30}(\d+(?:\.\d+)?)\s*m\b", re.I)),
    ("hydrant_spacing_m", re.compile(
        r"hydrant\s+spac(?:ing|e)[^0-9]{0,30}(\d+(?:\.\d+)?)\s*m\b", re.I)),
    ("pressure_min_bar", re.compile(
        r"min(?:imum)?\s+(?:residual\s+)?pressure[^0-9]{0,20}(\d+(?:\.\d+)?)\s*bar", re.I)),
    ("pressure_max_bar", re.compile(
        r"max(?:imum)?\s+pressure[^0-9]{0,20}(\d+(?:\.\d+)?)\s*bar", re.I)),
    ("hw_c", re.compile(r"(?:Hazen[- ]Williams|HW)\s*C[^0-9]{0,10}(\d+(?:\.\d+)?)", re.I)),
    ("return_period_yr", re.compile(
        r"(?:return\s+period|design\s+storm)[^0-9]{0,20}(\d+)\s*(?:[- ]?year|yr)", re.I)),
    ("d_over_d_max", re.compile(r"d\s*/\s*D[^0-9]{0,15}(\d+(?:\.\d+)?)", re.I)),
    ("valve_spacing_m", re.compile(
        r"valve[^0-9]{0,40}(\d+(?:\.\d+)?)\s*m\b", re.I)),
    ("separation_m", re.compile(
        r"separat(?:ion|ed)[^0-9]{0,30}(\d+(?:\.\d+)?)\s*m\b", re.I)),
]

PRESSURE_PAIR = re.compile(
    r"(\d+(?:\.\d+)?)\s*/\s*(\d+(?:\.\d+)?)\s*bar",
    re.I,
)
DIAMETER_PAIR_MM = re.compile(
    r"(?:min(?:imum)?\s+)?(?:pipe\s+)?diam(?:eter)?[^0-9]{0,30}"
    r"(\d+(?:\.\d+)?)\s*/\s*(\d+(?:\.\d+)?)\s*mm",
    re.I,
)
VELOCITY_RANGE = re.compile(
    r"(?:veloc(?:ity)?|V)\s*[:=]?\s*(\d+(?:\.\d+)?)\s*(?:–|-|to)\s*(\d+(?:\.\d+)?)\s*m\s*/?\s*s",
    re.I,
)
HW_C_PAIR = re.compile(
    r"(?:Hazen[- ]Williams|HW)\s*C[^0-9]{0,15}(\d+(?:\.\d+)?)\s*/\s*(\d+(?:\.\d+)?)",
    re.I,
)
COVER_PAIR = re.compile(
    r"cover[^0-9]{0,30}(\d+(?:\.\d+)?)\s*/\s*(\d+(?:\.\d+)?)\s*m",
    re.I,
)
SEPARATION_PAIR = re.compile(
    r"separat(?:ion|ed)[^0-9]{0,40}(\d+(?:\.\d+)?)\s*m[^0-9]{0,20}(\d+(?:\.\d+)?)\s*mm",
    re.I,
)
HEAD_LOSS_TABLE = re.compile(r"head[- ]?loss.{0,40}Table\s*5\.3", re.I)

# Table 6.1 — MH spacing by diameter (D mm → spacing m)
TABLE_61_ROW = re.compile(
    r"(?P<d>\d{2,4})\s*mm\b[^0-9]{0,40}(?P<span>\d+(?:\.\d+)?)\s*m\b",
    re.I,
)
# Table 6.3 — min gradient by D with both velocity columns
# e.g. "200 mm  0.50 %  0.60 m/s  0.75 m/s"
TABLE_63_ROW = re.compile(
    r"(?P<d>\d{2,4})\s*mm\s+"
    r"(?P<slope>\d+(?:\.\d+)?)\s*%\s+"
    r"(?P<v1>\d+(?:\.\d+)?)\s*m\s*/?\s*s"
    r"(?:\s+(?P<v2>\d+(?:\.\d+)?)\s*m\s*/?\s*s)?",
    re.I,
)
GRAD_ROW = TABLE_63_ROW  # alias used for storm gradient rows too

STORM_PIPE_ROW = re.compile(
    r"(?P<d>\d{2,4})\s*mm\s+(?P<len>\d+(?:\.\d+)?)\s*(?:m|km)",
    re.I,
)

# Table 10.9 — 10 columns (S2):
# label, invert start, invert stop, length, slope %, section type, size, material, n, average cover
STORM_109_ID = re.compile(r"\b(?P<id>P-\d{2,4})\b", re.I)
STORM_109_NUMS = re.compile(r"[-+]?\d+(?:\.\d+)?")
STORM_109_HEADER = re.compile(
    r"invert|average\s*cover|section\s*type|mann(?:ing)?|\bmaterial\b",
    re.I,
)

TOTAL_LENGTH = re.compile(
    r"(?:total\s+)?(?:network\s+)?(?:pipe\s+)?length[^0-9]{0,30}(\d+(?:\.\d+)?)\s*km",
    re.I,
)
MANHOLE_PROPOSED = re.compile(
    r"(?:proposed\s+)?(?:no\.?\s+of\s+)?manholes?\s*[:=]?\s*(\d+)\s*(?:manholes?)?",
    re.I,
)
MANHOLE_COUNT = re.compile(
    r"(?:proposed\s+(?:no\.?\s+of\s+)?manholes?[^0-9]{0,20}|manholes?\s*[:=]\s*)"
    r"(\d{2,4})\b|"
    r"\b(\d{2,4})\s+manholes\b",
    re.I,
)
DEPTH_RANGE = re.compile(
    r"depth[^0-9]{0,20}(\d+(?:\.\d+)?)\s*(?:–|-|to)\s*(\d+(?:\.\d+)?)\s*m",
    re.I,
)

TOC_HINT = re.compile(r"\b(contents|table\s+of\s+contents|\btoc\b)\b", re.I)
TOC_DOT_LEADERS = re.compile(r"(?:\.{3,}|(?:\.\s*){3,})\s*\d{1,3}\s*$", re.M)
TOC_DOTS_ONLY = re.compile(r"(?:\.{3,}|(?:\.\s*){3,})")
TOC_PAGE_NUM_LINE = re.compile(r"^.{4,120}?\s+(\d{1,3})\s*$", re.M)


def spans_of(ingestion, page_lo=None, page_hi=None):
    """Collect word spans from skill-2 pdf.json (pages[].words or .spans)."""
    pages = ingestion.get("pages") or ingestion.get("content", {}).get("pages") or []
    out = []
    for p in pages:
        num = p.get("page") or p.get("page_no") or p.get("number")
        if num is None:
            continue
        if page_lo is not None and num < page_lo:
            continue
        if page_hi is not None and num > page_hi:
            continue
        words = p.get("words") or p.get("spans") or p.get("tokens") or []
        for w in words:
            text = w.get("text") or w.get("str") or ""
            bbox = w.get("bbox") or w.get("box") or [0, 0, 0, 0]
            if len(bbox) < 4:
                continue
            out.append({"page": num, "text": text, "bbox": list(bbox)})
        if not words and p.get("text"):
            out.append({
                "page": num, "text": p["text"], "bbox": [0, 0, 1000, 12], "plain": True,
            })
    if not out and ingestion.get("words"):
        for w in ingestion["words"]:
            out.append({
                "page": w.get("page", 1),
                "text": w.get("text", ""),
                "bbox": w.get("bbox") or [0, 0, 0, 0],
            })
    return out


def lines_of(spans):
    by_page = {}
    for s in spans:
        by_page.setdefault(s["page"], []).append(s)
    lines = []
    for page, items in sorted(by_page.items()):
        plain = [s for s in items if s.get("plain")]
        if plain:
            for s in plain:
                for ln in str(s["text"]).splitlines():
                    if ln.strip():
                        lines.append({"page": page, "text": ln.strip(), "top": 0.0})
            continue
        items = sorted(items, key=lambda s: (s["bbox"][1], s["bbox"][0]))
        cur = None
        for s in items:
            if cur and abs(s["bbox"][1] - cur["top"]) < LINE_TOL:
                cur["spans"].append(s)
            else:
                if cur:
                    cur["spans"].sort(key=lambda x: x["bbox"][0])
                    cur["text"] = " ".join(x["text"] for x in cur["spans"])
                    lines.append(cur)
                cur = {"page": page, "top": s["bbox"][1], "spans": [s]}
        if cur:
            cur["spans"].sort(key=lambda x: x["bbox"][0])
            cur["text"] = " ".join(x["text"] for x in cur["spans"])
            lines.append(cur)
    return lines


def _toc_score(text):
    leaders = TOC_DOT_LEADERS.findall(text)
    if len(leaders) >= 3:
        return len(leaders)
    dots = TOC_DOTS_ONLY.findall(text)
    page_lines = TOC_PAGE_NUM_LINE.findall(text)
    return max(len(dots), len(page_lines) // 2)


def mark_toc_pages(lines):
    """Return set of TOC page numbers (early dotted-leader / contents pages)."""
    by_page = {}
    for ln in lines:
        by_page.setdefault(ln["page"], []).append(ln["text"])
    toc = set()
    started = False
    for page in sorted(by_page):
        if page > TOC_EARLY_MAX_PAGE:
            break
        text = "\n".join(by_page[page])
        if TOC_HINT.search(text[:800]) or _toc_score(text) >= 3:
            toc.add(page)
            started = True
            continue
        if started and (_toc_score(text) >= 1 or len(TOC_PAGE_NUM_LINE.findall(text)) >= 3):
            toc.add(page)
            continue
        if started:
            break
    return toc


def is_toc_line(text):
    if TOC_DOT_LEADERS.search(text):
        return True
    if TOC_DOTS_ONLY.search(text) and re.search(r"\d+\s*$", text):
        return True
    return False


def section_of(text, current):
    for name, rx in SECTION_HINTS.items():
        if rx.search(text):
            return name
    return current


def parse_pages_arg(s):
    if not s:
        return None, None
    if "-" in s:
        a, b = s.split("-", 1)
        return int(a), int(b)
    n = int(s)
    return n, n


def _parse_storm_109_row(text, page):
    """Parse one Table 10.9 data line into a 10-column row (S2)."""
    m = STORM_109_ID.search(text)
    if not m:
        return None
    if STORM_109_HEADER.search(text) and text.casefold().count("p-") == 0:
        return None
    # Reject pure header repeats that include the word Invert near P-
    if re.search(r"\binvert\s+start\b|\baverage\s+cover\b", text, re.I):
        return None
    pid = m.group("id").upper()
    after = text[m.end():]
    # Tokenise remaining cells: numbers + non-numeric words
    tokens = []
    # Split on whitespace while keeping numbers and words
    for tok in re.split(r"\s+", after.strip()):
        if not tok:
            continue
        tokens.append(tok)
    nums = []
    words = []
    for tok in tokens:
        # strip trailing % from slope token
        raw = tok.rstrip("%")
        if re.fullmatch(r"[-+]?\d+(?:\.\d+)?", raw):
            nums.append(float(raw))
            if tok.endswith("%"):
                words.append("%")
        else:
            words.append(tok)
    # Expected numeric order after label:
    # invert_start, invert_stop, length, slope, [size?], n?, cover
    # Size may be numeric (diameter mm) sitting among words/numbers.
    # Layout: inv_s, inv_e, length, slope, section_type(word), size(num|word),
    #         material(word+), n(num), cover(num)
    if len(nums) < 5:
        return None
    invert_start = nums[0]
    invert_stop = nums[1]
    length_m = nums[2]
    slope_pct = nums[3]
    # Remaining numbers: size (optional large), n (~0.01–0.02), cover (last, small)
    cover_m = nums[-1]
    n_val = None
    size_val = None
    mid = nums[4:-1]
    for v in mid:
        if 0.005 <= v <= 0.05 and n_val is None:
            n_val = v
        elif v >= 50 and size_val is None:
            size_val = v
        elif size_val is None and n_val is None and v > 0.05:
            # Ambiguous mid value — prefer as size if ≥ 50 else skip
            if v >= 50:
                size_val = v
    # Section type / material from non-numeric words (drop lone %)
    word_cells = [w for w in words if w != "%" and not re.fullmatch(r"[-+]?\d+(?:\.\d+)?", w)]
    section_type = word_cells[0] if word_cells else None
    material = " ".join(word_cells[1:]) if len(word_cells) > 1 else None
    # If size was a word like "Ø400" / "400mm"
    if size_val is None:
        for w in word_cells:
            sm = re.search(r"(\d{2,4})", w)
            if sm and 50 <= float(sm.group(1)) <= 3000:
                size_val = float(sm.group(1))
                break
    return {
        "id": pid,
        "invert_start_m": invert_start,
        "invert_stop_m": invert_stop,
        "length_m": length_m,
        "slope_pct": slope_pct,
        "section_type": section_type,
        "size": size_val,
        "material": material,
        "n": n_val,
        "cover_m": cover_m,
        "page": page,
        "text": text[:240],
    }


def _set_criterion(bucket, key, value, page, text, clause=None, extra=None):
    if key in bucket:
        return
    row = {"value": value, "page": page, "text": text[:240], "clause": clause}
    if extra:
        row.update(extra)
    bucket[key] = row


def _extract_pw_criteria(bucket, text, page):
    """Fill potable-water §5.3 declared values (S3)."""
    pm = PRESSURE_PAIR.search(text)
    if pm:
        bucket.setdefault("pressure_pair_bar", {
            "min_bar": float(pm.group(1)),
            "max_bar": float(pm.group(2)),
            "page": page,
            "text": text[:240],
            "clause": "§5.3",
        })
        _set_criterion(bucket, "pressure_min_bar", float(pm.group(1)), page, text, "§5.3")
        _set_criterion(bucket, "pressure_max_bar", float(pm.group(2)), page, text, "§5.3")
    dm = DIAMETER_PAIR_MM.search(text)
    if dm:
        bucket.setdefault("min_diameter_mm_pair", {
            "service_mm": float(dm.group(1)),
            "distribution_mm": float(dm.group(2)),
            "page": page,
            "text": text[:240],
            "clause": "§5.3",
        })
        _set_criterion(bucket, "min_diameter_mm", float(dm.group(1)), page, text, "§5.3",
                       extra={"distribution_mm": float(dm.group(2))})
    vm = VELOCITY_RANGE.search(text)
    if vm:
        bucket.setdefault("velocity_range_ms", {
            "min_ms": float(vm.group(1)),
            "max_ms": float(vm.group(2)),
            "page": page,
            "text": text[:240],
            "clause": "§5.3",
        })
        _set_criterion(bucket, "min_velocity_ms", float(vm.group(1)), page, text, "§5.3")
        _set_criterion(bucket, "max_velocity_ms", float(vm.group(2)), page, text, "§5.3")
    hm = HW_C_PAIR.search(text)
    if hm:
        bucket.setdefault("hw_c_pair", {
            "c_new": float(hm.group(1)),
            "c_old": float(hm.group(2)),
            "page": page,
            "text": text[:240],
            "clause": "§5.3",
        })
        _set_criterion(bucket, "hw_c", float(hm.group(1)), page, text, "§5.3",
                       extra={"c_old": float(hm.group(2))})
    cm = COVER_PAIR.search(text)
    if cm:
        bucket.setdefault("cover_m_pair", {
            "road_m": float(cm.group(1)),
            "other_m": float(cm.group(2)),
            "page": page,
            "text": text[:240],
            "clause": "§5.3",
        })
        _set_criterion(bucket, "cover_m", float(cm.group(1)), page, text, "§5.3",
                       extra={"other_m": float(cm.group(2))})
    sm = SEPARATION_PAIR.search(text)
    if sm:
        bucket.setdefault("separation", {
            "m": float(sm.group(1)),
            "mm": float(sm.group(2)),
            "page": page,
            "text": text[:240],
            "clause": "§5.3",
        })
    if HEAD_LOSS_TABLE.search(text):
        bucket.setdefault("head_loss_table", {
            "value": "Table 5.3",
            "page": page,
            "text": text[:240],
            "clause": "§5.3",
        })
    for key, rx in CRITERION_PATTERNS:
        m = rx.search(text)
        if m and key in ("valve_spacing_m", "hw_c", "cover_m", "min_diameter_mm",
                         "pressure_min_bar", "pressure_max_bar",
                         "min_velocity_ms", "max_velocity_ms"):
            _set_criterion(bucket, key, float(m.group(1)), page, text, "§5.3")


def _store_total(declared_totals, key, network, value, page, text, *, force=False):
    scoped = f"{network}:{key}" if network else key
    payload = {"value": value, "page": page, "text": text[:240], "network": network}
    # Never overwrite a scoped total with a different network's value
    if scoped not in declared_totals or force:
        existing = declared_totals.get(scoped)
        if existing is None or existing.get("network") == network:
            declared_totals[scoped] = payload
    if key not in declared_totals or force:
        existing = declared_totals.get(key)
        if existing is None or existing.get("network") in (None, network):
            declared_totals[key] = payload


def _store_depth(declared_totals, network, lo, hi, page, text, *, force=False):
    key = "depth_range_m"
    scoped = f"{network}:{key}" if network else key
    payload = {
        "min_m": lo, "max_m": hi, "page": page, "text": text[:240], "network": network,
    }
    if force or scoped not in declared_totals:
        declared_totals[scoped] = payload
    if force or key not in declared_totals:
        declared_totals[key] = payload


def extract(lines):
    toc_pages = mark_toc_pages(lines)
    criteria = {k: {} for k in SECTION_HINTS}
    gradient_tables = {k: [] for k in ("sewer", "storm")}
    mh_spacing_table = []  # Table 6.1
    storm_pipe_table = []
    storm_table_109 = []
    storm_109_by_id = {}
    declared_totals = {}
    declared_tables = []
    current = None
    in_table_109 = False
    in_table_61 = False
    in_table_63 = False
    in_config = {k: False for k in CONFIG_ANCHORS}

    for ln in lines:
        text = ln["text"]
        page = ln["page"]
        on_toc = page in toc_pages or is_toc_line(text)

        # Clause / table markers (allow on TOC only for declared_tables presence)
        for net, clause, rx in CLAUSE_HINTS:
            if rx.search(text):
                if not any(t.get("clause") == clause for t in declared_tables):
                    # Prefer non-TOC page for the recorded page number
                    if not on_toc or not any(t.get("clause") == clause for t in declared_tables):
                        if not on_toc or clause.startswith("§"):
                            declared_tables.append({
                                "network": net, "clause": clause,
                                "page": page, "text": text[:200],
                            })
                if on_toc:
                    continue
                if clause == "Table 10.9":
                    in_table_109 = True
                    current = "storm"
                elif clause == "Table 6.1":
                    in_table_61 = True
                    in_table_63 = False
                    current = "sewer"
                elif clause == "Table 6.3":
                    in_table_63 = True
                    in_table_61 = False
                    current = "sewer"
                elif clause.startswith("§"):
                    current = net

        if on_toc:
            continue

        current = section_of(text, current)

        # Configuration sub-section anchors (S3) — require numbered heading
        if current in CONFIG_ANCHORS and CONFIG_ANCHORS[current].search(text):
            in_config[current] = True
        elif current and re.search(r"\bconfiguration\b", text, re.I):
            # Only when a network section is already active
            in_config[current] = True

        if current is None:
            continue

        bucket = criteria[current]

        # PW §5.3 structured criteria
        if current == "potable_water":
            _extract_pw_criteria(bucket, text, page)

        for key, rx in CRITERION_PATTERNS:
            m = rx.search(text)
            if not m:
                continue
            if current == "potable_water" and key in (
                "valve_spacing_m", "cover_m", "hw_c", "min_diameter_mm",
                "pressure_min_bar", "pressure_max_bar",
                "min_velocity_ms", "max_velocity_ms", "separation_m",
            ):
                _set_criterion(bucket, key, float(m.group(1)), page, text, "§5.3")
            elif current != "potable_water":
                _set_criterion(bucket, key, float(m.group(1)), page, text, current)

        # Pressure residual pair also on configuration totals (2.76 / 5.27)
        pm = PRESSURE_PAIR.search(text)
        if pm and current == "potable_water":
            # Prefer configuration-anchored residual pair for totals pressure
            if in_config["potable_water"] or "residual" in text.casefold():
                bucket.setdefault("pressure_pair_bar", {
                    "min_bar": float(pm.group(1)),
                    "max_bar": float(pm.group(2)),
                    "page": page,
                    "text": text[:240],
                    "clause": current,
                })

        # Table 6.1 — MH spacing by D
        if in_table_61 and current == "sewer":
            if re.search(r"Table\s*6\.3\b", text, re.I):
                in_table_61 = False
                in_table_63 = True
            else:
                for m in TABLE_61_ROW.finditer(text):
                    d = float(m.group("d"))
                    span = float(m.group("span"))
                    # Spacing rows: diameter 100–2000, spacing 20–200 m
                    if not (100 <= d <= 2000 and 20 <= span <= 250):
                        continue
                    if any(r["diameter_mm"] == d for r in mh_spacing_table):
                        continue
                    mh_spacing_table.append({
                        "diameter_mm": d,
                        "mh_spacing_m": span,
                        "page": page,
                        "text": m.group(0),
                    })

        # Table 6.3 — min gradient + both velocity columns
        if (in_table_63 or current in ("sewer", "storm")) and current in ("sewer", "storm"):
            if re.search(r"Table\s*6\.1\b", text, re.I) and current == "sewer":
                in_table_63 = False
                in_table_61 = True
            for m in TABLE_63_ROW.finditer(text):
                row = {
                    "diameter_mm": float(m.group("d")),
                    "min_slope_pct": float(m.group("slope")),
                    "velocity_half_full_ms": float(m.group("v1")),
                    "velocity_peak_ms": float(m.group("v2")) if m.group("v2") else None,
                    # Keep legacy key for W3 declared gradient lookup
                    "velocity_ms": float(m.group("v2") or m.group("v1")),
                    "page": page,
                    "text": m.group(0),
                }
                # Dedup by diameter within network
                existing = gradient_tables[current]
                if any(abs(r["diameter_mm"] - row["diameter_mm"]) < 0.5 for r in existing):
                    continue
                # Only accept while in Table 6.3 / storm gradient context, or
                # when the line clearly has two velocity columns / slope
                if current == "sewer" and not in_table_63:
                    # Allow if line looks like a gradient table row near §6.3
                    if not re.search(r"%", text):
                        continue
                existing.append(row)

        if current == "storm":
            for m in STORM_PIPE_ROW.finditer(text):
                length = float(m.group("len"))
                unit_km = "km" in m.group(0).casefold()
                storm_pipe_table.append({
                    "diameter_mm": float(m.group("d")),
                    "length_m": length * 1000.0 if unit_km else length,
                    "page": page,
                    "text": m.group(0),
                })
            if in_table_109 or STORM_109_ID.search(text):
                row = _parse_storm_109_row(text, page)
                if row and row["id"] not in storm_109_by_id:
                    storm_109_by_id[row["id"]] = row
                    storm_table_109.append(row)

        # Declared totals — prefer Configuration anchors; never TOC
        force = bool(current and in_config.get(current))
        lm = TOTAL_LENGTH.search(text)
        if lm:
            _store_total(
                declared_totals, "total_length_km", current,
                float(lm.group(1)), page, text, force=force,
            )
        # Manholes: prefer "Proposed No. of manholes: 173"
        mh = MANHOLE_COUNT.search(text) or MANHOLE_PROPOSED.search(text)
        if mh and current == "sewer":
            # group 1 or 2 from MANHOLE_COUNT; group 1 from PROPOSED
            raw = next((g for g in mh.groups() if g), None)
            if raw:
                val = int(raw)
                # Reject tiny TOC-like counts unless forced by configuration
                if val >= 10 or force:
                    _store_total(
                        declared_totals, "manhole_count", current,
                        float(val), page, text,
                        force=force or val >= 100 or "proposed" in text.casefold(),
                    )
        dm = DEPTH_RANGE.search(text)
        if dm:
            _store_depth(
                declared_totals, current,
                float(dm.group(1)), float(dm.group(2)), page, text, force=force,
            )

    # Attach Table 6.1 into criteria / gradient side structure
    criteria["sewer"]["table_6_1_mh_spacing"] = mh_spacing_table
    return (
        criteria, gradient_tables, storm_pipe_table, declared_totals,
        declared_tables, storm_table_109, mh_spacing_table, sorted(toc_pages),
    )


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--text", required=True, help="skill-2 *.pdf.json")
    ap.add_argument("--out", required=True)
    ap.add_argument("--pages", default=None)
    a = ap.parse_args(argv)

    def run():
        path = Path(a.text)
        if not path.is_file():
            return fail(EXIT_INPUT, f"text JSON not found: {path}")
        if not path_ok(path):
            return fail(EXIT_INPUT, f"path longer than 250 characters: {path}")
        ingestion = read_json(path)
        lo, hi = parse_pages_arg(a.pages)
        spans = spans_of(ingestion, lo, hi)
        lines = lines_of(spans)
        (criteria, grads, storm_pipes, totals, declared_tables,
         storm_109, mh_61, toc_pages) = extract(lines)
        doc = {
            "criteria_version": "1.0",
            "source": {"path": str(path), "kind": "skill2_pdf_json"},
            "criteria": criteria,
            "gradient_tables": grads,
            "mh_spacing_table": mh_61,
            "storm_pipe_table": storm_pipes,
            "storm_table_109": storm_109,
            "declared_tables": declared_tables,
            "declared_totals": totals,
            "meta": {
                "reader": f"read_wet_criteria/{__version__}",
                "line_count": len(lines),
                "pages": [lo, hi],
                "toc_pages": toc_pages,
            },
        }
        try:
            from jsonschema import Draft202012Validator
            import json
            schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
            errs = list(Draft202012Validator(schema).iter_errors(doc))
            if errs:
                return fail(EXIT_SCHEMA, "criteria.json failed schema",
                            errors=[e.message for e in errs[:20]])
        except ImportError:
            pass
        atomic_write_json(a.out, doc)
        n_vals = sum(len(v) for v in criteria.values() if isinstance(v, dict))
        emit({
            "status": "ok", "code": EXIT_OK, "out": str(a.out),
            "criteria_values": n_vals,
            "gradient_rows": sum(len(v) for v in grads.values()),
            "mh_spacing_rows": len(mh_61),
            "storm_pipe_rows": len(storm_pipes),
            "storm_table_109_rows": len(storm_109),
            "declared_tables": [t["clause"] for t in declared_tables],
            "toc_pages": toc_pages,
        })
        return EXIT_OK

    return main_guard(run)


if __name__ == "__main__":
    sys.exit(main())
