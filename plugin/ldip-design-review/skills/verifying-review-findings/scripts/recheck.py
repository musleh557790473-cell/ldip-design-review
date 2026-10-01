#!/usr/bin/env python3
"""Independent re-check of candidate findings (SPC-0006 §4.7.4; SPC-0005 §5.7.1).

Dispatch by the evidence source_type:
  xlsx_cell / csv_cell  every "CELL=value" quoted in the evidence extract is re-read with a reader
                        different from the finder's (package XML for xlsx; own parser for CSV) and
                        compared at the printed precision. The source file hash must still equal
                        location.file_sha256; a changed file is reported as `disagrees`.
  dwg_vector, pdf_*,    no second path is built yet → `not_rechecked` with the reason (skills 1/2
  script_output, other  re-crop and 0.ai_context recompute come with the DWG/PDF finders).

Usage: python recheck.py --candidates findings/<run>/candidates/gis.json [more.json ...]
                         --out findings/<run>/evidence/verifier/recheck.json [--root .] [--finding <id>]
Output: one record per finding: recheck_result, method, compared cells (sheet, cell, expected,
found, ok), notes. Exit 0 written · 2 input error. No network, no shell calls.
"""
__version__ = "0.1.0"

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from verify_common import (  # noqa: E402
    EXIT_OK, VerifyError, atomic_write_json, csv_cells, read_json, sha256_file, values_agree, xlsx_cells,
)

# "<prefix> CELL=value" tokens in an extract; prefix names another sheet of the same workbook
PREFIX = {"summary": "Overall Summary", "overall summary": "Overall Summary", "dlus": "DLUS",
          "pa": "Planning Assumptions", "planning assumptions": "Planning Assumptions",
          "community facilities": "Community Facilities"}
TOKEN = re.compile(r"(?:(?P<prefix>Overall Summary|Summary|DLUS|PA|Planning Assumptions|Community Facilities)\s+)?"
                   r"\b(?P<cell>[A-Z]{1,3}[0-9]{1,7})=(?P<val>[^;|()]+)")


def tokens(extract, default_sheet):
    out = []
    for m in TOKEN.finditer(extract or ""):
        pre = (m.group("prefix") or "").lower()
        out.append({"sheet": PREFIX.get(pre, default_sheet), "cell": m.group("cell"), "expected": m.group("val").strip()})
    return out


def recheck_cells(f, root):
    loc = f.get("location") or {}
    ev = [e for e in f.get("evidence", []) if e.get("source_type") in ("xlsx_cell", "csv_cell")]
    rel = loc.get("file_path")
    if not rel:
        return "not_rechecked", "no location.file_path to re-read", []
    path = Path(root) / rel
    if not path.is_file():
        return "not_rechecked", f"source file not found under the review root: {rel}", []
    sheet = (loc.get("table") or {}).get("sheet") or path.stem
    is_csv = path.suffix.lower() == ".csv"
    toks = []
    for e in ev:
        toks += tokens(e.get("extract", ""), sheet)
    if is_csv:                              # a CSV holds one sheet: other-sheet tokens are in other files
        toks = [t for t in toks if t["sheet"] == sheet]
    if not toks:
        return "not_rechecked", "evidence extract quotes no cell values that can be re-read", []
    by_sheet = {}
    for t in toks:
        by_sheet.setdefault(t["sheet"], set()).add(t["cell"])
    found = {}
    try:
        for sh, refs in by_sheet.items():
            vals = csv_cells(path, refs) if is_csv else xlsx_cells(path, sh, refs)
            for r, v in vals.items():
                found[(sh, r)] = v
    except (VerifyError, KeyError, OSError, ValueError) as exc:
        detail = exc.detail if isinstance(exc, VerifyError) else f"{type(exc).__name__}: {exc}"
        return "not_rechecked", f"independent reader could not read the source: {detail}", []
    compared = []
    for t in toks:
        v = found.get((t["sheet"], t["cell"]))
        compared.append({**t, "found": v, "ok": values_agree(t["expected"], v)})
    reader = "own CSV parser" if is_csv else "package-XML reader (zipfile + ElementTree)"
    finder = "csv module" if is_csv else "openpyxl"
    same_file = sha256_file(path) == loc.get("file_sha256")
    bad = [c for c in compared if not c["ok"]]
    method = (f"Independent re-read of {len(compared)} quoted cell value(s) in {rel} with the {reader} "
              f"(finder: {finder}); ")
    if not same_file:
        return "disagrees", method + "source file hash differs from location.file_sha256 (file changed since " \
                                     "the finder read it)", compared
    if bad:
        return "disagrees", method + f"{len(bad)} value(s) differ: " + "; ".join(
            f"{c['sheet']}!{c['cell']} quoted {c['expected']} read {c['found']}" for c in bad[:5]), compared
    return "agrees", method + "file hash matches; all values agree (the rule arithmetic itself is not " \
                              "recomputed)", compared


def recheck(f, root):
    kinds = {e.get("source_type") for e in f.get("evidence", [])}
    if kinds & {"xlsx_cell", "csv_cell"}:
        return recheck_cells(f, root)
    if "dwg_vector" in kinds:
        return "not_rechecked", "dwg_vector: recompute from 0.ai_context not built yet (no DWG finder rules active)", []
    if kinds & {"pdf_vector_text", "pdf_ocr"}:
        return "not_rechecked", "PDF re-crop needs skill 2 crop.py (not built yet)", []
    return "not_rechecked", f"no independent second path for evidence types {sorted(kinds)} (SPC-0005 §5.7.1)", []


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--candidates", nargs="+", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--root", default=".")
    ap.add_argument("--finding")
    a = ap.parse_args(argv)
    try:
        findings = []
        for p in a.candidates:
            doc = read_json(p)
            if not isinstance(doc, list):
                raise VerifyError(2, f"{p}: a candidates file is a JSON array")
            findings += doc
        if a.finding:
            findings = [f for f in findings if f.get("finding_id") == a.finding]
            if not findings:
                raise VerifyError(2, f"finding {a.finding} not in the candidates")
        out = {"script": f"recheck.py {__version__}", "results": {}}
        for f in findings:
            res, method, compared = recheck(f, a.root)
            out["results"][f["finding_id"]] = {"recheck_result": res, "method": method, "compared": compared}
        atomic_write_json(a.out, out)
        counts = {}
        for r in out["results"].values():
            counts[r["recheck_result"]] = counts.get(r["recheck_result"], 0) + 1
        print(json.dumps({"status": "ok", "findings": len(findings), "results": counts, "out": a.out}))
        return EXIT_OK
    except VerifyError as e:
        print(json.dumps({"status": "error", "code": e.code, "detail": e.detail}))
        return e.code


if __name__ == "__main__":
    sys.exit(main())
