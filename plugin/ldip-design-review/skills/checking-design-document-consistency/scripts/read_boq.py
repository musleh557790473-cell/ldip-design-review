#!/usr/bin/env python3
"""Read a roads / earthworks BOQ workbook (skill 16, SPC-0006 Rev N §4.16; DN-2 / DN-6).

R1: roles only from part sheets (4-EARTHWORKS, 6- Roads and Pavings) + A-List cross-check —
    never Dayworks / Electrical trap sheets. Cut/fill from base×factor totals (I13-style).
R2: dual openpyxl passes (formulas + cached values).
R3: `#REF!` in values and formulas; principal SUMIF vs parts.

Usage:
  python read_boq.py --xlsx <boq.xlsx> --out <boq.json>

Exit: 0 done · 2 input · 3 missing openpyxl · 4 schema. No network, no shell.
"""
__version__ = "0.1.0"

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cons_common import (  # noqa: E402
    EXIT_DEP, EXIT_INPUT, EXIT_OK, RATIO_RE, atomic_write_json, emit, fail,
    is_cell_error, main_guard, parse_base_factor, path_ok,
)

# R1d: rock = "excavation … in rock" (words allowed between); strip "other than…"
# exclusions so "other than … hard material" / "… rock or …" do not trigger rock.
ROLE_PATTERNS = [
    ("import", re.compile(r"\b(import(?:ed)?(?:\s+\w+)?|borrow)\b", re.I)),
    ("disposal", re.compile(r"\b(dispos\w+|spoil|waste\s+material|cut\s+to\s+spoil)\b", re.I)),
    ("rock", re.compile(
        r"\bexcav(?:ation|ate|ating)?\b[\w\s,;/-]{0,60}?\bin\s+rock\b"
        r"|\brock\s+excav\w*"
        r"|\b(?:artificial\s+)?hard\s+material\b", re.I)),
    ("cut", re.compile(
        r"\b(?:cut(?!\s+and\s+fill|tings)|excavation|excavate|bulk\s+excav|"
        r"general\s+excav|common\s+excav|earthworks?\s+excav|site\s+clear)\b", re.I)),
    ("fill", re.compile(
        r"\b(suitable\s+(?:fill|excavated\s+material)|filling|(?<!import(?:ed)\s)\bfills?\b|"
        r"embankments?)\b", re.I)),
    ("road_area", re.compile(
        r"\b(road\s+(?:pavement|surfacing|area|layer|layers?)|carriageway|asphalt|"
        r"flexible\s+pavement|pavement\s+area|road\s+layers?)\b", re.I)),
    ("kerb", re.compile(r"\b(kerb|curb)s?\b", re.I)),
]
# Exclusion clauses: "other than top soil, rock or artificial hard material"
OTHER_THAN_EXCLUSION = re.compile(r"other\s+than\b[^.;]{0,100}", re.I)
# R11: A-List principal quantity rows for roads/earthworks only (not Electrical).
ALIST_EW_ROADS_ROW_MIN = 26
ALIST_EW_ROADS_ROW_MAX = 47
# R11b: evaluate SUM('4-EARTHWORKS'!F13:F15) against that sheet's rows only
SUM_RANGE_RE = re.compile(
    r"SUM\s*\(\s*(?:'([^']+)'!)?([A-Z]+)(\d+)\s*:\s*(?:'([^']+)'!)?([A-Z]+)(\d+)\s*\)",
    re.I)
CODE_RE = re.compile(r"\b([A-Z]{1,3}\d{2,4}(?:\.\d+)?|[A-Z]{2,5}-\d{2,4})\b")
UNIT_RE = re.compile(r"\b(m3|m³|m2|m²|m|nr|no\.?|sum|item)\b", re.I)

HEADER_RULES = [
    ("desc", re.compile(r"^(item\s*)?(desc(ription)?|particulars?|works?|activity)$|"
                        r"item\s*description|description\s*of\s*works?", re.I)),
    ("qty", re.compile(r"^(quantit(y|ies)|qty|amount|volume|area|length|net\s*qty)$", re.I)),
    ("unit", re.compile(r"^(unit|uom|unit\s*of\s*meas(ure(ment)?)?)$", re.I)),
    ("code", re.compile(r"^(code|csi|cesmm|cesmm4|item\s*no\.?|ref\.?)$", re.I)),
    ("part", re.compile(r"^(part|bill|section|bill\s*no\.?)$", re.I)),
]
SUM_LINK_RE = re.compile(r"=\s*(SUMIF|SUMIFS|SUM|VLOOKUP)\s*\(", re.I)
REF_IN_FORMULA = re.compile(r"#REF!", re.I)
RATIO_FACTOR_RE = re.compile(r"\*\s*(0?\.\d+)\s*$")
# F13-style `=I13*0.2` — resolve against same-sheet helper / cell value
CELL_REF_RATIO_RE = re.compile(
    r"^=\s*(?:'([^']+)'!)?([A-Z]+)(\d+)\s*\*\s*(0?\.\d+|1(?:\.0+)?)\s*$", re.I)
CELL_REF_RE = re.compile(
    r"^=\s*(?:'([^']+)'!)?([A-Z]+)(\d+)\s*$", re.I)
CELL_MINUS_RE = re.compile(
    r"^=\s*(?:'([^']+)'!)?([A-Z]+)(\d+)\s*-\s*(?:'([^']+)'!)?([A-Z]+)(\d+)\s*$", re.I)

# R1 — sheet kinds (DN-1: roads + earthworks parts only for roles)
SHEET_EARTHWORKS = re.compile(r"earthworks?", re.I)
SHEET_ROADS = re.compile(r"roads?\b.*pav|\broads?\b|pavings?", re.I)
SHEET_ALIST = re.compile(r"a\s*-?\s*list|principal\s*quant", re.I)
SHEET_TRAP = re.compile(r"dayworks?|electrical", re.I)
TOTAL_HINT = re.compile(r"\b(total|bulk|general)\b", re.I)
PORTION_HINT = re.compile(r"\b(common|portion|ratio|20\s*%|80\s*%|60\s*%)\b", re.I)


def need_openpyxl():
    try:
        import openpyxl  # noqa: F401
        return None
    except ImportError:
        return "openpyxl is not installed"


def cell_addr(row, col):
    from openpyxl.utils import get_column_letter
    return f"{get_column_letter(col)}{row}"


def sheet_kind(name):
    """R1: classify sheet — earthworks / roads / alist / trap / other."""
    n = name or ""
    if SHEET_TRAP.search(n):
        return "trap"
    if SHEET_ALIST.search(n):
        return "alist"
    if SHEET_EARTHWORKS.search(n):
        return "earthworks"
    if SHEET_ROADS.search(n):
        return "roads"
    return "other"


def classify_role(text):
    text = text or ""
    # R1d: scrub "other than …" before rock matching (row 13 false positive)
    scrubbed = OTHER_THAN_EXCLUSION.sub(" ", text)
    roles = []
    for name, pat in ROLE_PATTERNS:
        probe = scrubbed if name == "rock" else text
        if pat.search(probe):
            roles.append(name)
    # Imported fill is import, not fill; import row must not win rock.
    if "import" in roles:
        roles = [r for r in roles if r not in ("fill", "rock")]
    return roles


def header_map(row_vals):
    cols = {}
    for i, v in enumerate(row_vals, start=1):
        if v is None:
            continue
        s = str(v).strip()
        if not s:
            continue
        for key, pat in HEADER_RULES:
            if key in cols:
                continue
            if pat.search(s):
                cols[key] = i
                break
    return cols


def _is_ratio_formula(formula):
    if not formula:
        return False
    if RATIO_RE.match(str(formula).strip()):
        return True
    return bool(RATIO_FACTOR_RE.search(str(formula))) and "*" in str(formula) and not parse_base_factor(formula)


def read_sheet(ws_f, ws_v=None):
    items = []
    errors = []
    formulas = []
    ratios = []
    sum_links = []
    headers = None
    header_row = None
    kind = sheet_kind(ws_f.title)
    max_row = ws_f.max_row or 0
    max_col = ws_f.max_column or 0

    for r in range(1, max_row + 1):
        vals_f = [ws_f.cell(r, c).value for c in range(1, max_col + 1)]
        vals_v = [ws_v.cell(r, c).value for c in range(1, max_col + 1)] if ws_v is not None else None

        if headers is None:
            hm = header_map(vals_f)
            if "qty" not in hm and vals_v:
                hm = header_map(vals_v) or hm
            if "qty" in hm and ("desc" in hm or "code" in hm):
                headers = hm
                header_row = r
                continue

        for c, v in enumerate(vals_f, start=1):
            addr = f"{ws_f.title}!{cell_addr(r, c)}"
            v_cached = vals_v[c - 1] if vals_v and c <= len(vals_v) else None
            if is_cell_error(v) or is_cell_error(v_cached):
                errors.append({
                    "sheet": ws_f.title, "cell": cell_addr(r, c), "address": addr,
                    "value": str(v if is_cell_error(v) else v_cached).strip(),
                    "kind": "cell_error", "sheet_kind": kind,
                })
                continue
            if isinstance(v, str) and v.startswith("=") and REF_IN_FORMULA.search(v):
                errors.append({
                    "sheet": ws_f.title, "cell": cell_addr(r, c), "address": addr,
                    "value": "#REF!", "formula": v, "kind": "formula_ref_error",
                    "sheet_kind": kind,
                })
                continue
            if isinstance(v, str) and v.startswith("="):
                bf = parse_base_factor(v)
                if bf:
                    base, factor = bf
                    # Row description for associating helper cells (I13-style)
                    desc_guess = None
                    if headers and headers.get("desc") and headers["desc"] <= len(vals_f):
                        d = vals_f[headers["desc"] - 1]
                        desc_guess = str(d).strip() if d is not None else None
                    formulas.append({
                        "sheet": ws_f.title, "cell": cell_addr(r, c), "address": addr,
                        "formula": v, "base": base, "factor": factor,
                        "stated_value": base * factor,
                        "cached_value": float(v_cached) if isinstance(v_cached, (int, float)) else None,
                        "description": desc_guess, "sheet_kind": kind, "row": r,
                    })
                elif RATIO_RE.match(v.strip()):
                    m = RATIO_RE.match(v.strip())
                    ratios.append({
                        "sheet": ws_f.title, "cell": cell_addr(r, c), "address": addr,
                        "formula": v, "ratio": float(m.group(1)), "sheet_kind": kind,
                    })
                elif SUM_LINK_RE.match(v.strip()):
                    sum_links.append({
                        "sheet": ws_f.title, "cell": cell_addr(r, c), "address": addr,
                        "formula": v, "row": r,
                        "cached_value": float(v_cached) if isinstance(v_cached, (int, float)) else None,
                        "kind": SUM_LINK_RE.match(v.strip()).group(1).upper(),
                        "sheet_kind": kind,
                    })

        if headers and r != header_row:
            desc_i = headers.get("desc")
            qty_i = headers.get("qty")
            unit_i = headers.get("unit")
            code_i = headers.get("code")
            part_i = headers.get("part")
            desc = vals_f[desc_i - 1] if desc_i and desc_i <= len(vals_f) else None
            qty_raw = vals_f[qty_i - 1] if qty_i and qty_i <= len(vals_f) else None
            qty_cached = vals_v[qty_i - 1] if vals_v and qty_i and qty_i <= len(vals_v) else None
            if desc is None and qty_raw is None:
                continue
            if not desc and not (isinstance(qty_raw, (int, float)) or (
                    isinstance(qty_raw, str) and (qty_raw.startswith("=") or is_cell_error(qty_raw)))):
                continue
            desc_s = str(desc or "").strip()
            unit = None
            if unit_i and unit_i <= len(vals_f) and vals_f[unit_i - 1] is not None:
                unit = str(vals_f[unit_i - 1]).strip()
            code = None
            if code_i and code_i <= len(vals_f) and vals_f[code_i - 1] is not None:
                code = str(vals_f[code_i - 1]).strip()
            elif desc_s:
                cm = CODE_RE.search(desc_s)
                if cm:
                    code = cm.group(1)
            if not unit and desc_s:
                um = UNIT_RE.search(desc_s)
                if um:
                    unit = um.group(1)

            formula = qty_raw if isinstance(qty_raw, str) and qty_raw.startswith("=") else None
            base = factor = stated = None
            err = None
            if formula and REF_IN_FORMULA.search(formula):
                err = "#REF!"
            elif is_cell_error(qty_raw) or is_cell_error(qty_cached):
                err = str(qty_raw if is_cell_error(qty_raw) else qty_cached).strip()
            elif formula:
                bf = parse_base_factor(formula)
                if bf:
                    base, factor = bf
                    stated = base * factor
                elif isinstance(qty_cached, (int, float)):
                    stated = float(qty_cached)
                    base = stated
                    factor = 1.0
            elif isinstance(qty_raw, (int, float)):
                stated = float(qty_raw)
                base = stated
                factor = 1.0
            elif isinstance(qty_cached, (int, float)):
                stated = float(qty_cached)
                base = stated
                factor = 1.0

            part = None
            if part_i and part_i <= len(vals_f) and vals_f[part_i - 1] is not None:
                part = str(vals_f[part_i - 1]).strip()
            items.append({
                "sheet": ws_f.title,
                "row": r,
                "cell": cell_addr(r, qty_i) if qty_i else None,
                "address": f"{ws_f.title}!{cell_addr(r, qty_i)}" if qty_i else None,
                "part": part,
                "code": code,
                "description": desc_s,
                "unit": unit,
                "formula": formula,
                "base": base,
                "factor": factor,
                "quantity": stated,
                "cached_value": float(qty_cached) if isinstance(qty_cached, (int, float)) else None,
                "roles": classify_role(desc_s),
                "error": err,
                "sheet_kind": kind,
                "is_ratio": _is_ratio_formula(formula),
                "principal_sheet": kind == "alist",
            })

    # R1d: attach same-row base×factor helpers (I13 / I23) so fill uses I23 not I13×ratio
    by_row = {}
    for f in formulas:
        by_row.setdefault(f.get("row"), []).append(f)
    for it in items:
        helpers = [f for f in by_row.get(it.get("row"), [])
                   if f.get("cell") and f.get("cell") != it.get("cell")]
        if not helpers:
            continue
        best = max(helpers, key=lambda f: float(f["base"]))
        it["helper_base"] = best["base"]
        it["helper_factor"] = best["factor"]
        it["helper_address"] = best["address"]
        it["helper_formula"] = best["formula"]
        it["helper_cell"] = best["cell"]

    # Resolve =I13*0.2 / =I23-F23 when openpyxl cache is empty (synthetic / formula pass)
    cell_stated = {}
    for f in formulas:
        cell_stated[f["cell"]] = float(f["base"]) * float(f["factor"])
    for it in items:
        if it.get("quantity") is not None and it.get("cell"):
            cell_stated[it["cell"]] = float(it["quantity"])
    for it in items:
        if it.get("quantity") is not None or it.get("error"):
            continue
        formula = it.get("formula") or ""
        m = CELL_REF_RATIO_RE.match(formula)
        if m:
            ref = f"{m.group(2).upper()}{m.group(3)}"
            if ref in cell_stated:
                stated = cell_stated[ref] * float(m.group(4))
                it["quantity"] = stated
                it["base"] = stated
                it["factor"] = 1.0
                cell_stated[it["cell"]] = stated
            continue
        m = CELL_MINUS_RE.match(formula)
        if m:
            a = f"{m.group(2).upper()}{m.group(3)}"
            b = f"{m.group(5).upper()}{m.group(6)}"
            if a in cell_stated and b in cell_stated:
                stated = cell_stated[a] - cell_stated[b]
                it["quantity"] = stated
                it["base"] = stated
                it["factor"] = 1.0
                cell_stated[it["cell"]] = stated
            continue
        m = CELL_REF_RE.match(formula)
        if m:
            ref = f"{m.group(2).upper()}{m.group(3)}"
            if ref in cell_stated:
                stated = cell_stated[ref]
                it["quantity"] = stated
                it["base"] = stated
                it["factor"] = 1.0
                cell_stated[it["cell"]] = stated

    return {
        "name": ws_f.title,
        "header_row": header_row,
        "headers": headers or {},
        "items": items,
        "formulas": formulas,
        "ratios": ratios,
        "sum_links": sum_links,
        "errors": errors,
        "sheet_kind": kind,
        "principal_sheet": kind == "alist",
    }


def _synth_sum(items, description):
    """Synthetic quantity record = sum of item bases (NRJS F13+F15 / F23+F25)."""
    if not items:
        return None
    base = sum(float(it["base"]) for it in items if it.get("base") is not None)
    qty = sum(float(it.get("quantity") or it["base"]) for it in items if it.get("base") is not None)
    addrs = [it.get("address") for it in items if it.get("address")]
    return {
        "base": base,
        "factor": 1.0,
        "quantity": qty,
        "formula": None,
        "address": "+".join(addrs) if addrs else None,
        "sheet": items[0].get("sheet"),
        "description": description,
        "unit": items[0].get("unit") or "m3",
        "code": None,
        "part": items[0].get("part"),
    }


def _from_helper(it, fallback_desc):
    """Build a qty record from a same-row I13/I23 helper (R1d)."""
    if not it or it.get("helper_base") is None:
        return None
    base = float(it["helper_base"])
    factor = float(it.get("helper_factor") or 1.0)
    return {
        "base": base,
        "factor": factor,
        "quantity": base * factor,
        "formula": it.get("helper_formula"),
        "address": it.get("helper_address"),
        "sheet": it.get("sheet"),
        "description": it.get("description") or fallback_desc,
        "unit": it.get("unit") or "m3",
        "code": it.get("code"),
        "part": it.get("part"),
        "row": it.get("row"),
    }


def _item_stated(it):
    """Qty-cell stated value only (never the same-row I-helper — R11b / R1d)."""
    if it.get("quantity") is not None:
        return float(it["quantity"])
    if it.get("cached_value") is not None:
        return float(it["cached_value"])
    if it.get("base") is not None:
        return float(it["base"]) * float(it.get("factor") or 1.0)
    return None


def _pick_cut(ew_items, ew_forms):
    """R1d: excavation total — I13 helper, else common + rock (F13+F15)."""
    cut_role = [it for it in ew_items
                if "cut" in it["roles"] and not it.get("error")]
    # Prefer same-row helper on a cut row (I13), largest base
    helpers = [_from_helper(it, "excavation total (I13)") for it in cut_role
               if it.get("helper_base") is not None]
    helpers = [h for h in helpers if h]
    if helpers:
        return max(helpers, key=lambda h: float(h["base"]))

    cands = [it for it in cut_role
             if not it.get("is_ratio") and it.get("base") is not None]
    totals = [it for it in cands if TOTAL_HINT.search(it.get("description") or "")
              and not PORTION_HINT.search(it.get("description") or "")]
    if totals:
        return max(totals, key=lambda it: float(it["base"]))
    with_factor = [it for it in cands
                   if it.get("factor") and abs(float(it["factor"]) - 1.0) > 1e-9
                   and not PORTION_HINT.search(it.get("description") or "")]
    if with_factor:
        return max(with_factor, key=lambda it: float(it["base"]))

    # Sheet-level I13-style helper not tied to a ratio-only read
    if ew_forms:
        # Prefer helpers whose row is a cut-role row
        cut_rows = {it.get("row") for it in cut_role}
        row_forms = [f for f in ew_forms if f.get("row") in cut_rows] or ew_forms
        best_f = max(row_forms, key=lambda f: float(f["base"]))
        cand_max = max((float(it["base"]) for it in cands), default=0)
        if float(best_f["base"]) >= cand_max:
            return {
                "base": best_f["base"], "factor": best_f["factor"],
                "quantity": best_f["stated_value"], "formula": best_f["formula"],
                "address": best_f["address"], "sheet": best_f["sheet"],
                "description": best_f.get("description") or "excavation total (helper)",
                "unit": "m3", "code": None, "part": None,
            }

    # R1d NRJS: cut = common (non-rock cut) + rock (F13 + F15)
    rock = [it for it in ew_items
            if "rock" in it["roles"] and not it.get("error")
            and _item_stated(it) is not None]
    common = [it for it in cut_role
              if "rock" not in it["roles"] and not it.get("error")
              and _item_stated(it) is not None
              and not (it.get("is_ratio") and it.get("helper_base") is None)]
    if rock and common:
        # Use stated/cached values as bases for the sum
        parts = []
        for it in common + rock:
            v = _item_stated(it)
            parts.append({
                **it,
                "base": v if it.get("helper_base") is None and not (
                    it.get("factor") and abs(float(it.get("factor") or 1) - 1) > 1e-9)
                else (it.get("helper_base") or it.get("base") or v),
                "quantity": v,
            })
        # Simpler: sum stated values as bases (NRJS has factor 1)
        return {
            "base": sum(_item_stated(it) for it in common + rock),
            "factor": 1.0,
            "quantity": sum(_item_stated(it) for it in common + rock),
            "formula": None,
            "address": "+".join(it.get("address") or "" for it in common + rock),
            "sheet": (common + rock)[0].get("sheet"),
            "description": "common + rock excavation (F13+F15)",
            "unit": "m3", "code": None, "part": None,
        }
    if cands:
        return max(cands, key=lambda it: float(it["base"]))
    return None


def _pick_fill(ew_items, ew_forms):
    """R1d: fill base from same-row helper I23; else suitable + imported (F23+F25)."""
    suitable = [it for it in ew_items
                if "fill" in it["roles"] and "import" not in it["roles"]
                and not it.get("error")]
    imported = [it for it in ew_items
                if "import" in it["roles"] and not it.get("error")
                and _item_stated(it) is not None]

    # Prefer same-row I23 helper on the suitable-fill row
    for it in suitable:
        rec = _from_helper(it, "suitable fill (I23)")
        if rec is not None:
            return rec

    suitable_qty = [it for it in suitable
                    if not it.get("is_ratio") and it.get("base") is not None]
    if suitable_qty:
        with_factor = [it for it in suitable_qty
                       if it.get("factor") and abs(float(it["factor"]) - 1.0) > 1e-9]
        if with_factor:
            return max(with_factor, key=lambda it: float(it["base"]))
        if imported:
            return {
                "base": sum(_item_stated(it) for it in suitable_qty + imported),
                "factor": 1.0,
                "quantity": sum(_item_stated(it) for it in suitable_qty + imported),
                "formula": None,
                "address": "+".join(it.get("address") or "" for it in suitable_qty + imported),
                "sheet": suitable_qty[0].get("sheet"),
                "description": "suitable + imported fill (F23+F25)",
                "unit": "m3", "code": None, "part": None,
            }
        return max(suitable_qty, key=lambda it: float(it["base"]))
    # Ratio-only suitable rows still count toward NRJS sum with import
    if suitable and imported:
        return {
            "base": sum(_item_stated(it) for it in suitable + imported if _item_stated(it) is not None),
            "factor": 1.0,
            "quantity": sum(_item_stated(it) for it in suitable + imported if _item_stated(it) is not None),
            "formula": None,
            "address": None,
            "sheet": suitable[0].get("sheet"),
            "description": "suitable + imported fill (F23+F25)",
            "unit": "m3", "code": None, "part": None,
        }
    return None


def _pick_role(items, role):
    cands = [it for it in items
             if role in it["roles"] and not it.get("error") and it.get("base") is not None
             and not it.get("is_ratio")]
    if not cands:
        cands = [it for it in items
                 if role in it["roles"] and not it.get("error") and _item_stated(it) is not None]
    if not cands:
        return None
    # Prefer true rock excavation over any leftover false positives
    if role == "rock":
        in_rock = [it for it in cands
                   if re.search(r"\bin\s+rock\b", it.get("description") or "", re.I)]
        if in_rock:
            cands = in_rock
    return max(cands, key=lambda it: float(it.get("base") or _item_stated(it) or 0))


def _sum_formula_range(formula, part_items):
    """R11b: sum only the sheet/rows the A-List SUM formula references."""
    m = SUM_RANGE_RE.search(formula or "")
    if not m:
        return None
    sheet1, col1, r1, sheet2, col2, r2 = (
        m.group(1), m.group(2).upper(), int(m.group(3)),
        m.group(4), m.group(5).upper(), int(m.group(6)),
    )
    sheet = sheet1 or sheet2
    if col1 != col2:
        return None
    lo, hi = min(r1, r2), max(r1, r2)
    total = 0.0
    n = 0
    for it in part_items:
        if sheet and it.get("sheet") != sheet:
            continue
        row = it.get("row")
        if row is None:
            continue
        if not (lo <= int(row) <= hi):
            continue
        # Prefer stated qty (F-column value); else base×factor / helper
        v = _item_stated(it)
        if v is None:
            continue
        total += v
        n += 1
    return total if n else None


def summarise(sheets):
    """R1: roles from earthworks/roads part sheets only — never trap sheets."""
    all_items = [it for sh in sheets for it in sh["items"]]
    all_forms = [f for sh in sheets for f in sh["formulas"]]
    all_ratios = [r for sh in sheets for r in sh["ratios"]]
    all_errors = [e for sh in sheets for e in sh["errors"]]
    all_sum_links = [s for sh in sheets for s in sh["sum_links"]]

    for it in all_items:
        if it.get("error"):
            all_errors.append({
                "sheet": it.get("sheet"), "cell": it.get("cell"),
                "address": it.get("address"), "value": it["error"],
                "kind": "item_error", "formula": it.get("formula"),
                "sheet_kind": it.get("sheet_kind"),
            })

    ew_items = [it for it in all_items if it.get("sheet_kind") == "earthworks"]
    rd_items = [it for it in all_items if it.get("sheet_kind") == "roads"]
    ew_forms = [f for f in all_forms if f.get("sheet_kind") == "earthworks"]
    # Ratios only from earthworks/roads part sheets (not traps)
    part_ratios = [r for r in all_ratios if r.get("sheet_kind") in ("earthworks", "roads")]

    cut = _pick_cut(ew_items, ew_forms)
    fill = _pick_fill(ew_items, ew_forms)
    disposal = _pick_role(ew_items, "disposal")
    rock = _pick_role(ew_items, "rock")
    imp = _pick_role(ew_items, "import")
    road = _pick_role(rd_items, "road_area")
    kerb = _pick_role(rd_items, "kerb")

    # R3/R11b: A-List principal SUM vs formula references only (E26–E47; no kerb bleed)
    principal_checks = []
    part_items = [it for it in all_items
                  if it.get("sheet_kind") in ("earthworks", "roads") and not it.get("error")]
    for link in all_sum_links:
        kind = link.get("sheet_kind")
        if kind != "alist":
            continue
        # R11: skip Electrical / out-of-scope A-List rows (keep E26–E47)
        row = link.get("row")
        if row is not None and not (ALIST_EW_ROADS_ROW_MIN <= int(row) <= ALIST_EW_ROADS_ROW_MAX):
            continue
        cached = link.get("cached_value")
        # R11b: part_sum = evaluate the A-List formula's own sheet/row references
        part_sum = _sum_formula_range(link.get("formula"), part_items)
        if cached is None:
            principal_checks.append({
                **link, "part_sum": part_sum, "result": "not_run",
                "note": "no cached value",
            })
            continue
        if part_sum is None:
            principal_checks.append({
                **link, "part_sum": None, "result": "not_run",
                "note": "could not resolve SUM range on earthworks/roads parts",
            })
            continue
        diff = abs(float(cached) - float(part_sum))
        pd = diff / max(abs(float(cached)), abs(float(part_sum)), 1e-12)
        principal_checks.append({
            **link, "part_sum": part_sum, "difference": diff, "pct_difference": pd,
            "result": "flag" if pd > 0.10 and diff > 1.0 else "pass",
        })

    seen = set()
    uniq_errors = []
    for e in all_errors:
        key = e.get("address") or f"{e.get('sheet')}!{e.get('cell')}"
        if key in seen:
            continue
        seen.add(key)
        if "sheet_kind" not in e:
            e = {**e, "sheet_kind": sheet_kind(e.get("sheet"))}
        uniq_errors.append(e)

    # R3b: group errors for scoped reporting
    error_groups = _group_errors(uniq_errors)

    ratio_vals = sorted({round(r["ratio"], 4) for r in part_ratios})
    return {
        "earthworks": {
            "cut": _qty_rec(cut),
            "fill": _qty_rec(fill),
            "disposal": _qty_rec(disposal),
            "rock": _qty_rec(rock),
            "import": _qty_rec(imp),
        },
        "roads": {
            "road_area": _qty_rec(road),
            "kerb": _qty_rec(kerb),
        },
        "ratios_recorded": ratio_vals,
        "ratios": part_ratios,
        "base_factor_formulas": [f for f in all_forms if f.get("sheet_kind") in ("earthworks", "roads")],
        "sum_links": all_sum_links,
        "principal_checks": principal_checks,
        "cell_errors": uniq_errors,
        "error_groups": error_groups,
        "item_count": len(all_items),
        "role_scope": ["earthworks", "roads"],
    }


def _group_errors(errors):
    """R3b: group #REF! by sheet kind for handful of findings."""
    groups = {
        "alist": {"label": "A-List", "cells": [], "count": 0},
        "earthworks": {"label": "earthworks part", "cells": [], "count": 0},
        "roads": {"label": "roads part", "cells": [], "count": 0},
        "other": {"label": "other sheets", "cells": [], "count": 0},
    }
    sumif_ref = []
    for e in errors:
        kind = e.get("sheet_kind") or sheet_kind(e.get("sheet"))
        bucket = kind if kind in groups else "other"
        if kind == "trap":
            bucket = "other"
        groups[bucket]["cells"].append(e.get("address") or e.get("cell"))
        groups[bucket]["count"] += 1
        if e.get("kind") == "formula_ref_error" and e.get("formula") and SUM_LINK_RE.search(e["formula"]):
            sumif_ref.append(e)
    out = []
    for key in ("alist", "earthworks", "roads"):
        g = groups[key]
        if g["count"]:
            out.append({
                "group": key, "label": g["label"], "count": g["count"],
                "cells": g["cells"][:50],
                "in_scope": True,
                "pattern": f"{g['label']}: {g['count']} cells are #REF!",
            })
    if sumif_ref:
        parts = sorted({e.get("sheet") for e in sumif_ref})
        out.append({
            "group": "sumif_ref", "label": "part summaries",
            "count": len(sumif_ref),
            "cells": [e.get("address") for e in sumif_ref[:50]],
            "in_scope": True,
            "pattern": f"part summaries: SUMIF(#REF!) in {', '.join(parts)}",
        })
    other = groups["other"]
    if other["count"]:
        out.append({
            "group": "other", "label": other["label"], "count": other["count"],
            "cells": [], "in_scope": False,
            "pattern": f"other sheets: {other['count']} #REF! cells (information only)",
        })
    return out


def _qty_rec(it):
    if not it:
        return None
    return {
        "base": it.get("base"),
        "factor": it.get("factor"),
        "quantity": it.get("quantity"),
        "unit": it.get("unit") or "m3",
        "description": it.get("description"),
        "code": it.get("code"),
        "address": it.get("address"),
        "formula": it.get("formula"),
        "sheet": it.get("sheet"),
        "part": it.get("part"),
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--xlsx", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--document-no", default=None, dest="document_no")
    a = ap.parse_args(argv)

    miss = need_openpyxl()
    if miss:
        return fail(EXIT_DEP, miss)

    def run():
        import openpyxl
        xlsx = Path(a.xlsx)
        if not path_ok(xlsx):
            return fail(EXIT_INPUT, f"path longer than 250 characters: {xlsx}")
        if not xlsx.is_file():
            return fail(EXIT_INPUT, f"BOQ workbook not found: {xlsx}")

        wb_f = openpyxl.load_workbook(xlsx, data_only=False, read_only=False)
        try:
            wb_v = openpyxl.load_workbook(xlsx, data_only=True, read_only=False)
        except Exception:
            wb_v = None
        sheets = []
        for name in wb_f.sheetnames:
            ws_v = wb_v[name] if wb_v is not None and name in wb_v.sheetnames else None
            sheets.append(read_sheet(wb_f[name], ws_v))
        wb_f.close()
        if wb_v is not None:
            wb_v.close()

        summary = summarise(sheets)
        doc_no = a.document_no or Path(xlsx).stem
        out = {
            "boq_version": "1.0",
            "source": {
                "path": str(xlsx),
                "sheets": [s["name"] for s in sheets],
                "sheet_kinds": {s["name"]: s["sheet_kind"] for s in sheets},
                "document_no": doc_no,
                "dual_pass": wb_v is not None,
            },
            "sheets": sheets,
            "summary": summary,
            "tool": {"script": "read_boq.py", "version": __version__},
            "note": "R1: roles from earthworks/roads part sheets only. R2 dual pass. DN-6 base before factor.",
        }
        atomic_write_json(a.out, out)
        emit({
            "status": "ok",
            "out": a.out,
            "sheets": len(sheets),
            "items": summary["item_count"],
            "cell_errors": len(summary["cell_errors"]),
            "error_groups": len(summary["error_groups"]),
            "base_factor": len(summary["base_factor_formulas"]),
            "ratios": summary["ratios_recorded"],
            "dual_pass": True,
            "document_no": doc_no,
            "cut_base": (summary["earthworks"]["cut"] or {}).get("base"),
            "fill_base": (summary["earthworks"]["fill"] or {}).get("base"),
        })
        return EXIT_OK

    return main_guard(run)


if __name__ == "__main__":
    sys.exit(main())
