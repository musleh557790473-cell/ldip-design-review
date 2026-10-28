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

ROLE_PATTERNS = [
    ("import", re.compile(r"\b(import(?:ed)?(?:\s+\w+)?|borrow)\b", re.I)),
    ("disposal", re.compile(r"\b(dispos\w+|spoil|waste\s+material|cut\s+to\s+spoil)\b", re.I)),
    ("rock", re.compile(r"\b(rock|hard\s+material)\b", re.I)),
    ("cut", re.compile(
        r"\b(cut|excavation|excavate|bulk\s+excav|general\s+excav|common\s+excav|"
        r"earthworks?\s+excav|site\s+clear)\b", re.I)),
    ("fill", re.compile(r"\b(suitable\s+fill|(?<!import(?:ed)\s)\bfill\b|embankment)\b", re.I)),
    ("road_area", re.compile(
        r"\b(road\s+(?:pavement|surfacing|area|layer|layers?)|carriageway|asphalt|"
        r"flexible\s+pavement|pavement\s+area|road\s+layers?)\b", re.I)),
    ("kerb", re.compile(r"\b(kerb|curb)s?\b", re.I)),
]
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
    roles = []
    for name, pat in ROLE_PATTERNS:
        if pat.search(text or ""):
            roles.append(name)
    if "import" in roles and "fill" in roles:
        roles = [r for r in roles if r != "fill"]
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
                        "formula": v,
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


def _pick_cut(ew_items, ew_forms):
    """R1: excavation total base — prefer base×factor total (I13-style), not common 20 %."""
    # Candidates: cut-role items that are not ratio portions
    cands = [it for it in ew_items
             if "cut" in it["roles"] and not it.get("error") and not it.get("is_ratio")
             and it.get("base") is not None]
    # Prefer total/bulk/general over common/portion
    totals = [it for it in cands if TOTAL_HINT.search(it.get("description") or "")
              and not PORTION_HINT.search(it.get("description") or "")]
    if totals:
        return max(totals, key=lambda it: float(it["base"]))
    non_portion = [it for it in cands if not PORTION_HINT.search(it.get("description") or "")]
    if non_portion:
        # Prefer largest base×factor (the total), not a small common slice
        with_factor = [it for it in non_portion if it.get("factor") and abs(float(it["factor"]) - 1.0) > 1e-9]
        if with_factor:
            return max(with_factor, key=lambda it: float(it["base"]))
        return max(non_portion, key=lambda it: float(it["base"]))
    # Fall back to largest base×factor on earthworks sheet (helper cell I13)
    if ew_forms:
        return {
            "base": max(ew_forms, key=lambda f: f["base"])["base"],
            "factor": max(ew_forms, key=lambda f: f["base"])["factor"],
            "quantity": max(ew_forms, key=lambda f: f["base"])["stated_value"],
            "formula": max(ew_forms, key=lambda f: f["base"])["formula"],
            "address": max(ew_forms, key=lambda f: f["base"])["address"],
            "sheet": max(ew_forms, key=lambda f: f["base"])["sheet"],
            "description": max(ew_forms, key=lambda f: f["base"]).get("description") or "excavation total (helper)",
            "unit": "m3", "code": None, "part": None,
        }
    if cands:
        return max(cands, key=lambda it: float(it["base"]))
    return None


def _pick_fill(ew_items, ew_forms):
    """R1: suitable + imported fill total on earthworks part sheet."""
    suitable = [it for it in ew_items
                if "fill" in it["roles"] and "import" not in it["roles"]
                and not it.get("error") and it.get("base") is not None and not it.get("is_ratio")]
    imported = [it for it in ew_items
                if "import" in it["roles"] and not it.get("error") and it.get("base") is not None]
    if suitable:
        # Prefer largest suitable (I23-style total with factor)
        best = max(suitable, key=lambda it: float(it["base"]))
        return best
    # Sum suitable+imported bases when only portions exist
    if imported and not suitable:
        return None  # fill separate from import
    return None


def _pick_role(items, role):
    cands = [it for it in items
             if role in it["roles"] and not it.get("error") and it.get("base") is not None
             and not it.get("is_ratio")]
    if not cands:
        cands = [it for it in items
                 if role in it["roles"] and not it.get("error") and it.get("quantity") is not None]
    if not cands:
        return None
    return max(cands, key=lambda it: float(it.get("base") or it.get("quantity") or 0))


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

    # R3: principal / A-List SUMIF vs part items (earthworks+roads only)
    principal_checks = []
    part_items = [it for it in all_items
                  if it.get("sheet_kind") in ("earthworks", "roads") and not it.get("error")]
    for link in all_sum_links:
        if link.get("sheet_kind") not in ("alist", "earthworks", "roads"):
            continue
        part_sum = sum(float(it["base"]) for it in part_items if it.get("base") is not None)
        cached = link.get("cached_value")
        if cached is None:
            principal_checks.append({
                **link, "part_sum": part_sum, "result": "not_run",
                "note": "no cached value",
            })
            continue
        if link.get("sheet_kind") == "alist" and part_sum > 0:
            diff = abs(cached - part_sum)
            pd = diff / max(abs(cached), abs(part_sum), 1e-12)
            principal_checks.append({
                **link, "part_sum": part_sum, "difference": diff, "pct_difference": pd,
                "result": "flag" if pd > 0.10 and diff > 1.0 else "pass",
            })
        else:
            principal_checks.append({**link, "part_sum": part_sum, "result": "information"})

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
