#!/usr/bin/env python3
"""Read a roads / earthworks BOQ workbook (skill 16, SPC-0006 Rev N §4.16; DN-2 / DN-6).

Dual openpyxl passes (R2): formulas (`data_only=False`) + cached values (`data_only=True`).
Detects `#REF!` as values **and** inside formulas (R3). Principal-quantity SUMIF/SUM links
are compared to part-item totals (R3). Header / role synonyms cover real Parsons layouts (R1).

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

# R1 — role keywords (roads + earthworks only — DN-1). Order matters for prefer_* below.
ROLE_PATTERNS = [
    ("import", re.compile(r"\b(import(?:ed)?(?:\s+fill)?|borrow)\b", re.I)),
    ("disposal", re.compile(r"\b(dispos\w+|spoil|waste\s+material|cut\s+to\s+spoil)\b", re.I)),
    ("rock", re.compile(r"\b(rock|hard\s+material)\b", re.I)),
    ("cut", re.compile(
        r"\b(cut|excavation|excavate|bulk\s+excav|general\s+excav|common\s+excav|"
        r"earthworks?\s+excav|site\s+clear)\b", re.I)),
    ("fill", re.compile(r"\b(suitable\s+fill|fill|embankment|compaction)\b", re.I)),
    ("road_area", re.compile(
        r"\b(road\s+(?:pavement|surfacing|area|layer|layers?)|carriageway|asphalt|"
        r"flexible\s+pavement|pavement\s+area)\b", re.I)),
    ("kerb", re.compile(r"\b(kerb|curb)s?\b", re.I)),
]
CODE_RE = re.compile(r"\b([A-Z]{1,3}\d{2,4}(?:\.\d+)?|[A-Z]{2,5}-\d{2,4})\b")
UNIT_RE = re.compile(r"\b(m3|m³|m2|m²|m|nr|no\.?|sum|item)\b", re.I)

# R1 — header synonyms (independent column match; no elif stealing)
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
PRINCIPAL_SHEET = re.compile(r"principal|summary\s*quant|pq\b", re.I)


def need_openpyxl():
    try:
        import openpyxl  # noqa: F401
        return None
    except ImportError:
        return "openpyxl is not installed"


def cell_addr(row, col):
    from openpyxl.utils import get_column_letter
    return f"{get_column_letter(col)}{row}"


def classify_role(text):
    """R1: mutually prefer import/disposal over bare fill; return role list."""
    roles = []
    for name, pat in ROLE_PATTERNS:
        if pat.search(text or ""):
            roles.append(name)
    # "Imported fill" → import only (not also fill)
    if "import" in roles and "fill" in roles:
        roles = [r for r in roles if r != "fill"]
    # "Rock excavation" keeps both rock and cut
    return roles


def header_map(row_vals):
    """R1: map each header independently (first match per role wins)."""
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


def _sheet_is_principal(name):
    return bool(PRINCIPAL_SHEET.search(name or ""))


def read_sheet(ws_f, ws_v=None):
    """Scan one sheet using formula workbook + optional value workbook (R2)."""
    items = []
    errors = []
    formulas = []
    ratios = []
    sum_links = []
    headers = None
    header_row = None
    max_row = ws_f.max_row or 0
    max_col = ws_f.max_column or 0

    for r in range(1, max_row + 1):
        vals_f = [ws_f.cell(r, c).value for c in range(1, max_col + 1)]
        vals_v = None
        if ws_v is not None:
            vals_v = [ws_v.cell(r, c).value for c in range(1, max_col + 1)]

        if headers is None:
            hm = header_map(vals_f)
            # Also try value-row headers (rare) then fall through
            if "qty" not in hm and vals_v:
                hm = header_map(vals_v) or hm
            if "qty" in hm and ("desc" in hm or "code" in hm):
                headers = hm
                header_row = r
                continue

        for c, v in enumerate(vals_f, start=1):
            addr = f"{ws_f.title}!{cell_addr(r, c)}"
            v_cached = vals_v[c - 1] if vals_v and c <= len(vals_v) else None

            # R3: #REF! as value OR inside a formula string
            if is_cell_error(v) or is_cell_error(v_cached):
                errors.append({
                    "sheet": ws_f.title, "cell": cell_addr(r, c), "address": addr,
                    "value": str(v if is_cell_error(v) else v_cached).strip(),
                    "kind": "cell_error",
                })
                continue
            if isinstance(v, str) and v.startswith("=") and REF_IN_FORMULA.search(v):
                errors.append({
                    "sheet": ws_f.title, "cell": cell_addr(r, c), "address": addr,
                    "value": "#REF!", "formula": v, "kind": "formula_ref_error",
                })
                continue

            if isinstance(v, str) and v.startswith("="):
                bf = parse_base_factor(v)
                if bf:
                    base, factor = bf
                    formulas.append({
                        "sheet": ws_f.title, "cell": cell_addr(r, c), "address": addr,
                        "formula": v, "base": base, "factor": factor,
                        "stated_value": base * factor,
                        "cached_value": float(v_cached) if isinstance(v_cached, (int, float)) else None,
                    })
                elif RATIO_RE.match(v.strip()):
                    m = RATIO_RE.match(v.strip())
                    ratios.append({
                        "sheet": ws_f.title, "cell": cell_addr(r, c), "address": addr,
                        "formula": v, "ratio": float(m.group(1)),
                    })
                elif SUM_LINK_RE.match(v.strip()):
                    sum_links.append({
                        "sheet": ws_f.title, "cell": cell_addr(r, c), "address": addr,
                        "formula": v,
                        "cached_value": float(v_cached) if isinstance(v_cached, (int, float)) else None,
                        "kind": SUM_LINK_RE.match(v.strip()).group(1).upper(),
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
                    # R2: non-base×factor formula — use cached value as stated; base unknown
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
                "principal_sheet": _sheet_is_principal(ws_f.title),
            })

    return {
        "name": ws_f.title,
        "header_row": header_row,
        "headers": headers or {},
        "items": items,
        "formulas": formulas,
        "ratios": ratios,
        "sum_links": sum_links,
        "errors": errors,
        "principal_sheet": _sheet_is_principal(ws_f.title),
    }


def summarise(sheets):
    """Pick earthworks / road totals from roles; prefer part sheets over principal (R1)."""
    all_items = [it for sh in sheets for it in sh["items"]]
    all_forms = [f for sh in sheets for f in sh["formulas"]]
    all_ratios = [r for sh in sheets for r in sh["ratios"]]
    all_errors = [e for sh in sheets for e in sh["errors"]]
    all_sum_links = [s for sh in sheets for s in sh["sum_links"]]

    # Also fold item-level formula_ref / cell errors into cell_errors
    for it in all_items:
        if it.get("error"):
            all_errors.append({
                "sheet": it.get("sheet"), "cell": it.get("cell"),
                "address": it.get("address"), "value": it["error"],
                "kind": "item_error", "formula": it.get("formula"),
            })

    def first_role(role):
        # Prefer non-principal part items (R1)
        for prefer_principal in (False, True):
            for it in all_items:
                if bool(it.get("principal_sheet")) != prefer_principal:
                    continue
                if role in it["roles"] and it.get("base") is not None and not it.get("error"):
                    return it
            for it in all_items:
                if bool(it.get("principal_sheet")) != prefer_principal:
                    continue
                if role in it["roles"] and it.get("quantity") is not None and not it.get("error"):
                    return it
        return None

    cut = first_role("cut")
    fill = first_role("fill")
    disposal = first_role("disposal")
    rock = first_role("rock")
    imp = first_role("import")
    road = first_role("road_area")
    kerb = first_role("kerb")

    # R3: principal SUMIF/SUM vs part-item totals (by role)
    principal_checks = []
    part_items = [it for it in all_items if not it.get("principal_sheet") and not it.get("error")]
    for link in all_sum_links:
        # Compare cached principal total to sum of part bases with matching unit when possible
        part_sum = sum(float(it["base"]) for it in part_items
                       if it.get("base") is not None
                       and (not link.get("sheet") or True))
        cached = link.get("cached_value")
        if cached is None:
            principal_checks.append({
                **link, "part_sum": part_sum, "result": "not_run",
                "note": "no cached value (open workbook in Excel once to cache)",
            })
            continue
        # Only flag when the link lives on a principal sheet and part_sum is non-zero
        if _sheet_is_principal(link.get("sheet")) and part_sum > 0:
            diff = abs(cached - part_sum)
            pd = diff / max(abs(cached), abs(part_sum), 1e-12)
            principal_checks.append({
                **link, "part_sum": part_sum, "difference": diff, "pct_difference": pd,
                "result": "flag" if pd > 0.10 and diff > 1.0 else "pass",
            })
        else:
            principal_checks.append({**link, "part_sum": part_sum, "result": "information"})

    # de-dupe errors by address
    seen = set()
    uniq_errors = []
    for e in all_errors:
        key = e.get("address") or f"{e.get('sheet')}!{e.get('cell')}"
        if key in seen:
            continue
        seen.add(key)
        uniq_errors.append(e)

    ratio_vals = sorted({round(r["ratio"], 4) for r in all_ratios})
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
        "ratios": all_ratios,
        "base_factor_formulas": all_forms,
        "sum_links": all_sum_links,
        "principal_checks": principal_checks,
        "cell_errors": uniq_errors,
        "item_count": len(all_items),
    }


def _qty_rec(it):
    if not it:
        return None
    return {
        "base": it.get("base"),
        "factor": it.get("factor"),
        "quantity": it.get("quantity"),
        "unit": it.get("unit"),
        "description": it.get("description"),
        "code": it.get("code"),
        "address": it.get("address"),
        "formula": it.get("formula"),
        "sheet": it.get("sheet"),
        "part": it.get("part"),
        "document_no": it.get("document_no"),
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--xlsx", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--document-no", default=None, dest="document_no",
                    help="Parsons BOQ document number (R8); recorded on summary")
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

        # R2: dual pass — formulas, then cached values
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
                "document_no": doc_no,
                "dual_pass": wb_v is not None,
            },
            "sheets": sheets,
            "summary": summary,
            "tool": {"script": "read_boq.py", "version": __version__},
            "note": "DN-6: compare base before factor; factor recorded as stated, not judged. R2 dual openpyxl pass.",
        }
        atomic_write_json(a.out, out)
        emit({
            "status": "ok",
            "out": a.out,
            "sheets": len(sheets),
            "items": summary["item_count"],
            "cell_errors": len(summary["cell_errors"]),
            "base_factor": len(summary["base_factor_formulas"]),
            "ratios": summary["ratios_recorded"],
            "dual_pass": True,
            "document_no": doc_no,
        })
        return EXIT_OK

    return main_guard(run)


if __name__ == "__main__":
    sys.exit(main())
