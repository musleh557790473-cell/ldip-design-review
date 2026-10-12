#!/usr/bin/env python3
"""Read Parsons MIDP workbooks (SRC-14) into one JSON file (owner decision D2, 11 Oct 2026).

Usage: python read_midp.py --file <MIDP.xlsm> [--file <MIDP2.xlsm> ...] --out <midp.json>
Columns are found by their header labels, never by position (the Common MIDP has XML and XLSX swapped):
the header row is the row whose column B reads "Document Number"; the row above it gives the data
exchange columns (Owner, Planned, Actual, Revision) and the row above that names each exchange's stage
("Stage:" + name). A row with text in B, nothing in C and no format flag is a section heading. A data
row is one whose B cell starts with a letter-digit number token. Each row records its sheet, row, cell,
title, section, flagged exchange formats (any non-empty cell under a format label) with the flag
cells, the file-name fields and every data exchange (stage, owner, planned, actual, revision).
The workbook's own identity is read from the label cells (Author, Date Last Updated, Document
Reference ...) and from its file name; nothing is assumed from the revision field alone (owner
decision, 11 Oct 2026: a draft update keeps the same revision, so "Date Last Updated" and the file
hash identify the version).
Exit: 0 written · 2 input error (missing file, no MIDP sheet) · 3 openpyxl missing. No network.
"""
__version__ = "0.1.0"

import argparse
import datetime as dt
import re
import sys
import warnings
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pkg_common import (EXIT_DEP, EXIT_INPUT, EXIT_OK, MIDP_FORMATS, atomic_write_json, col_letter, emit,  # noqa: E402
                        fail, number_from_name, path_ok, sha256_file)

META_LABELS = ("Client", "Project Title", "Author", "Date Created", "Date Last Updated", "Document Reference")
NUM_CELL = re.compile(r"^[A-Za-z0-9]+(-[A-Za-z0-9]+){4,}$")
FMT_BY_LABEL = {f.upper(): f for f in MIDP_FORMATS}


def cell_text(v):
    if v is None:
        return ""
    if isinstance(v, (dt.datetime, dt.date)):
        return v.strftime("%Y-%m-%d")
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v).strip()


def is_stage_marker(v):
    """The "Stage:" label cell itself (a stage NAME may also start with "Stage")."""
    return cell_text(v).lower().rstrip(":").strip() == "stage"


def find_header(rows):
    """Row whose B cell reads "Document Number" and which carries the exchange-format labels."""
    for i, r in enumerate(rows[:40]):
        if len(r) > 1 and cell_text(r[1]).lower() == "document number" \
                and any(cell_text(v).upper() in FMT_BY_LABEL for v in r):
            return i
    return None


def read_sheet(name, rows):
    h = find_header(rows)
    if h is None or h < 2:
        return None, []
    hdr, lab, stg = rows[h], rows[h - 1], rows[h - 2]
    fmt_cols = {FMT_BY_LABEL[cell_text(v).upper()]: j for j, v in enumerate(hdr) if cell_text(v).upper() in FMT_BY_LABEL}
    if not fmt_cols:
        return None, []
    title_col = next((j for j, v in enumerate(hdr) if cell_text(v).lower() == "document title"), 2)
    name_fields = {}
    first_ex = None
    for j, v in enumerate(stg):
        if is_stage_marker(v):
            first_ex = j if first_ex is None else min(first_ex, j)
    for j, v in enumerate(hdr):
        t = cell_text(v)
        if j > max(fmt_cols.values()) and (first_ex is None or j < first_ex) and t:
            name_fields[t] = j
    exchanges = []
    for j, v in enumerate(stg):
        if is_stage_marker(v):
            stage = cell_text(stg[j + 1]) if j + 1 < len(stg) else ""
            block = {}
            for k in range(j, min(j + 4, len(lab))):
                t = cell_text(lab[k]).lower()
                for key in ("owner", "planned", "actual", "revision"):
                    if t.startswith(key) and key not in block:
                        block[key] = k
            exchanges.append({"index": len(exchanges) + 1, "stage": stage, "cols": block})
    info = {"name": name, "header_row": h + 1, "format_columns": {f: col_letter(j) for f, j in sorted(fmt_cols.items())},
            "file_name_fields": {k: col_letter(j) for k, j in name_fields.items()},
            "exchanges": [{"index": e["index"], "stage": e["stage"],
                           "columns": {k: col_letter(c) for k, c in e["cols"].items()}} for e in exchanges]}
    out, section = [], None
    for i in range(h + 1, len(rows)):
        r = rows[i]
        b = cell_text(r[1]) if len(r) > 1 else ""
        if not b:
            continue
        c = cell_text(r[title_col]) if len(r) > title_col else ""
        flags = {f: col_letter(j) + str(i + 1) for f, j in fmt_cols.items() if j < len(r) and cell_text(r[j])}
        if not NUM_CELL.match(b):
            if not c and not flags:
                section = b
            continue
        exs = []
        for e in exchanges:
            vals = {k: (cell_text(r[col]) if col < len(r) else "") for k, col in e["cols"].items()}
            if any(vals.values()):
                exs.append({"index": e["index"], "stage": e["stage"], **vals})
        out.append({"sheet": name, "row": i + 1, "cell": f"B{i + 1}", "number": b.upper(), "title": c,
                    "section": section, "formats": sorted(flags), "format_cells": dict(sorted(flags.items())),
                    "file_name_fields": {k: cell_text(r[j]) if j < len(r) else "" for k, j in name_fields.items()},
                    "exchanges": exs})
    info["rows"] = len(out)
    return info, out


def read_meta(ws_rows, sheet):
    meta = {}
    for i, r in enumerate(ws_rows[:15]):
        for j, v in enumerate(r[:-1]):
            t = cell_text(v)
            if t in META_LABELS and t not in meta:
                meta[t] = {"value": cell_text(r[j + 1]), "sheet": sheet, "cell": f"{col_letter(j + 1)}{i + 1}"}
    return meta


def read_file(path):
    try:
        import openpyxl
    except ImportError:
        raise RuntimeError("dependency")
    sheets, rows, meta = [], [], {}
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # openpyxl: unsupported data-validation extension
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        sheet_data = [(ws.title, [list(r) for r in ws.iter_rows(values_only=True)]) for ws in wb.worksheets]
    for title, data in sheet_data:
        ws_title = title
        info, rr = read_sheet(ws_title, data)
        if info:
            sheets.append(info)
            rows += rr
            for k, v in read_meta(data, ws_title).items():
                meta.setdefault(k, v)
    wb.close()
    return sheets, rows, meta


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", action="append", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    if not path_ok(a.out):
        return fail(EXIT_INPUT, "output path longer than 250 characters")
    files, all_rows = [], []
    for k, f in enumerate(a.file):
        p = Path(f)
        if not p.is_file():
            return fail(EXIT_INPUT, f"MIDP file not found: {p}")
        if not path_ok(p):
            return fail(EXIT_INPUT, f"path longer than 250 characters: {p}")
        try:
            sheets, rows, meta = read_file(p)
        except RuntimeError:
            return fail(EXIT_DEP, "openpyxl is not installed")
        except Exception as exc:  # unreadable workbook
            return fail(EXIT_INPUT, f"cannot read {p.name}: {type(exc).__name__}: {exc}")
        if not sheets:
            return fail(EXIT_INPUT, f"{p.name}: no MIDP sheet (no 'Document Number' header with format columns)")
        for r in rows:
            r["file"] = k
        files.append({"index": k, "path": p.as_posix(), "name": p.name, "sha256": sha256_file(p),
                      "number_from_name": number_from_name(p.name), "meta": meta, "sheets": sheets,
                      "rows": len(rows)})
        all_rows += rows
    doc = {"tool": {"name": "read_midp.py", "version": __version__}, "files": files, "rows": all_rows,
           "counts": {"files": len(files), "rows": len(all_rows),
                      "by_sheet": {f"{files[r['file']]['name']}|{r['sheet']}": 0 for r in all_rows}}}
    for r in all_rows:
        doc["counts"]["by_sheet"][f"{files[r['file']]['name']}|{r['sheet']}"] += 1
    atomic_write_json(a.out, doc)
    emit({"status": "ok", "out": str(a.out), "files": len(files), "rows": len(all_rows),
          "updated": [f["meta"].get("Date Last Updated", {}).get("value") for f in files]})
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
