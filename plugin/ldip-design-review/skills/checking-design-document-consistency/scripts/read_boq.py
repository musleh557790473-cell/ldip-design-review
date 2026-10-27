#!/usr/bin/env python3
"""Read a roads / earthworks BOQ workbook (skill 16, SPC-0006 Rev N §4.16; DN-2 / DN-6).

openpyxl reads **values and formulas**. Records parts, items, units, CSI / CESMM4-like codes,
base × factor (DN-6), constant split ratios, and cell errors (#REF! …) for C5.

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

# Item-role keywords (roads + earthworks only — DN-1)
ROLE_PATTERNS = [
    ("cut", re.compile(r"\b(cut|excavation|excavate|bulk\s+excav)\b", re.I)),
    ("fill", re.compile(r"\b(fill|embankment|suitable\s+fill|imported\s+fill)\b", re.I)),
    ("disposal", re.compile(r"\b(dispos\w+|spoil|waste\s+material)\b", re.I)),
    ("rock", re.compile(r"\b(rock|hard\s+material)\b", re.I)),
    ("import", re.compile(r"\b(import(?:ed)?(?:\s+fill)?|borrow)\b", re.I)),
    ("road_area", re.compile(r"\b(road\s+(?:pavement|surfacing|area|layer)|carriageway|asphalt)\b", re.I)),
    ("kerb", re.compile(r"\b(kerb|curb)\b", re.I)),
]
CODE_RE = re.compile(r"\b([A-Z]{1,3}\d{2,4}(?:\.\d+)?|[A-Z]{2,5}-\d{2,4})\b")
UNIT_RE = re.compile(r"\b(m3|m³|m2|m²|m|nr|no\.?|sum|item)\b", re.I)
HEADER_QTY = re.compile(r"quantit|qty|amount|volume|area|length", re.I)
HEADER_DESC = re.compile(r"desc|item|particular|work", re.I)
HEADER_UNIT = re.compile(r"^unit$|uom|measure", re.I)
HEADER_CODE = re.compile(r"code|csi|cesmm|ref", re.I)
HEADER_PART = re.compile(r"^part$|bill|section", re.I)


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
    roles = []
    for name, pat in ROLE_PATTERNS:
        if pat.search(text or ""):
            roles.append(name)
    return roles


def header_map(row_vals):
    cols = {}
    for i, v in enumerate(row_vals, start=1):
        if v is None:
            continue
        s = str(v).strip()
        if HEADER_DESC.search(s) and "desc" not in cols:
            cols["desc"] = i
        elif HEADER_QTY.search(s) and "qty" not in cols:
            cols["qty"] = i
        elif HEADER_UNIT.search(s) and "unit" not in cols:
            cols["unit"] = i
        elif HEADER_CODE.search(s) and "code" not in cols:
            cols["code"] = i
        elif HEADER_PART.search(s) and "part" not in cols:
            cols["part"] = i
    return cols


def read_sheet(ws):
    """Scan one sheet: items, formula bases, ratios, errors."""
    items = []
    errors = []
    formulas = []
    ratios = []
    headers = None
    header_row = None

    for r in range(1, (ws.max_row or 0) + 1):
        vals = [ws.cell(r, c).value for c in range(1, (ws.max_column or 0) + 1)]
        if headers is None:
            hm = header_map(vals)
            if "qty" in hm and ("desc" in hm or "code" in hm):
                headers = hm
                header_row = r
                continue

        for c, v in enumerate(vals, start=1):
            addr = f"{ws.title}!{cell_addr(r, c)}"
            if is_cell_error(v):
                errors.append({"sheet": ws.title, "cell": cell_addr(r, c), "address": addr,
                               "value": str(v).strip(), "kind": "cell_error"})
                continue
            if isinstance(v, str) and v.startswith("="):
                bf = parse_base_factor(v)
                if bf:
                    base, factor = bf
                    formulas.append({
                        "sheet": ws.title, "cell": cell_addr(r, c), "address": addr,
                        "formula": v, "base": base, "factor": factor,
                        "stated_value": base * factor,
                    })
                elif RATIO_RE.match(v.strip()):
                    m = RATIO_RE.match(v.strip())
                    ratios.append({
                        "sheet": ws.title, "cell": cell_addr(r, c), "address": addr,
                        "formula": v, "ratio": float(m.group(1)),
                    })

        if headers and r != header_row:
            desc_i = headers.get("desc")
            qty_i = headers.get("qty")
            unit_i = headers.get("unit")
            code_i = headers.get("code")
            part_i = headers.get("part")
            desc = vals[desc_i - 1] if desc_i and desc_i <= len(vals) else None
            qty_cell = ws.cell(r, qty_i) if qty_i else None
            qty_raw = qty_cell.value if qty_cell else None
            if desc is None and qty_raw is None:
                continue
            if not desc and not (isinstance(qty_raw, (int, float)) or (
                    isinstance(qty_raw, str) and qty_raw.startswith("="))):
                continue
            desc_s = str(desc or "").strip()
            unit = None
            if unit_i and unit_i <= len(vals) and vals[unit_i - 1] is not None:
                unit = str(vals[unit_i - 1]).strip()
            code = None
            if code_i and code_i <= len(vals) and vals[code_i - 1] is not None:
                code = str(vals[code_i - 1]).strip()
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
            if formula:
                bf = parse_base_factor(formula)
                if bf:
                    base, factor = bf
                    stated = base * factor
                elif is_cell_error(qty_raw):
                    pass
            elif isinstance(qty_raw, (int, float)):
                stated = float(qty_raw)
                base = stated
                factor = 1.0

            roles = classify_role(desc_s)
            # Also classify from nearby part label
            part = None
            if part_i and part_i <= len(vals) and vals[part_i - 1] is not None:
                part = str(vals[part_i - 1]).strip()
            items.append({
                "sheet": ws.title,
                "row": r,
                "cell": cell_addr(r, qty_i) if qty_i else None,
                "address": f"{ws.title}!{cell_addr(r, qty_i)}" if qty_i else None,
                "part": part,
                "code": code,
                "description": desc_s,
                "unit": unit,
                "formula": formula,
                "base": base,
                "factor": factor,
                "quantity": stated,
                "roles": roles,
                "error": str(qty_raw).strip() if is_cell_error(qty_raw) else None,
            })

    return {
        "name": ws.title,
        "header_row": header_row,
        "headers": headers or {},
        "items": items,
        "formulas": formulas,
        "ratios": ratios,
        "errors": errors,
    }


def summarise(sheets):
    """Pick earthworks / road totals from roles + base×factor cells (DN-6 uses base)."""
    all_items = [it for sh in sheets for it in sh["items"]]
    all_forms = [f for sh in sheets for f in sh["formulas"]]
    all_ratios = [r for sh in sheets for r in sh["ratios"]]
    all_errors = [e for sh in sheets for e in sh["errors"]]

    def first_role(role):
        for it in all_items:
            if role in it["roles"] and it.get("base") is not None:
                return it
        for it in all_items:
            if role in it["roles"] and it.get("quantity") is not None:
                return it
        return None

    cut = first_role("cut")
    fill = first_role("fill")
    disposal = first_role("disposal")
    rock = first_role("rock")
    imp = first_role("import")
    road = first_role("road_area")
    kerb = first_role("kerb")

    # Fallback: largest base×factor on principal / summary sheets tagged excavation-ish
    if cut is None and all_forms:
        # Prefer formulas whose row neighbours mention excavation — already in items.
        # Else take the largest base among formulas as cut candidate only when one item says cut.
        pass

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
        "cell_errors": all_errors,
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
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--xlsx", required=True)
    ap.add_argument("--out", required=True)
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

        # data_only=False keeps formulas; a second pass is not required — values that are
        # plain numbers stay available; formula results are reconstructed from base×factor.
        wb = openpyxl.load_workbook(xlsx, data_only=False, read_only=False)
        sheets = []
        for name in wb.sheetnames:
            sheets.append(read_sheet(wb[name]))
        wb.close()

        summary = summarise(sheets)
        out = {
            "boq_version": "1.0",
            "source": {"path": str(xlsx), "sheets": [s["name"] for s in sheets]},
            "sheets": sheets,
            "summary": summary,
            "tool": {"script": "read_boq.py", "version": __version__},
            "note": "DN-6: compare base before factor; factor recorded as stated, not judged.",
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
        })
        return EXIT_OK

    return main_guard(run)


if __name__ == "__main__":
    sys.exit(main())
