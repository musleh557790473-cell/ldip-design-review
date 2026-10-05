#!/usr/bin/env python3
"""Independent re-check of candidate findings (SPC-0006 §4.7.4; SPC-0005 §5.7.1).

Dispatch by the evidence source_type:
  xlsx_cell / csv_cell  every "CELL=value" quoted in the evidence extract is re-read with a reader
                        different from the finder's (package XML for xlsx; own parser for CSV) and
                        compared by value type (P-S3-43: a number only when the whole token is a
                        number with at most one known unit; otherwise text). The source file hash must
                        still equal location.file_sha256; a changed file is reported as `disagrees`.
                        With --rulebook, the rule formula of GIS-012 / 013 / 014 is recomputed from the
                        re-read operand cells (P-S3-24, recompute.py); a sample with no breach →
                        `disagrees`.
  pdf_vector_text       every quoted "text" in the extract is looked for in the text that pdfplumber
                        (not the finder's pypdfium2) reads inside location.pdf bbox_pt on that page;
                        file hash checked as for cells. Rotated pages and pages above
                        LARGE_PAGE_OBJECTS (pdfplumber memory) → `not_rechecked`.
  dwg_vector, pdf_ocr,  no independent second path → `not_rechecked` with the reason.
  script_output, other

Usage: python recheck.py --candidates findings/<run>/candidates/gis.json [more.json ...]
                         --out findings/<run>/evidence/verifier/recheck.json [--root .] [--finding <id>]
                         [--rulebook rulebook/rules_vN.json]
Output: one record per finding: recheck_result, method, compared cells (sheet, cell, expected,
found, ok), notes. Exit 0 written · 2 input error. No network, no shell calls.
"""
__version__ = "0.2.0"

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "applying-ldip-rulebook" / "scripts"))
import recompute as rc  # noqa: E402

LARGE_PAGE_OBJECTS = 200_000   # = extracting-pdf-drawings pdf_common (owner approved 5 Oct 2026)
from verify_common import (  # noqa: E402
    EXIT_OK, VerifyError, as_number, atomic_write_json, csv_cells, read_json, sha256_file, value_kind, values_agree,
    xlsx_cells,
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


def recheck_cells(f, root, rules=None):
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
    rid = (f.get("reference") or {}).get("rule_id")
    samples = []
    if rules is not None and rid in ("GIS-012", "GIS-013", "GIS-014"):
        for e in ev:
            samples += [rc.parse_sample(seg, sheet) for seg in (e.get("extract") or "").split(" | ")]
        extra = rc.needed_cells(samples)
        if is_csv and any(sh != sheet for sh in extra):
            samples = []                    # operands in another CSV file: no recomputation
        else:
            for sh, refs in extra.items():
                by_sheet.setdefault(sh, set()).update(refs)
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
        compared.append({**t, "kind": value_kind(t["expected"]), "found": v, "ok": values_agree(t["expected"], v)})
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
    recs = []
    for ops, consts, aggs in samples:
        r = rc.recompute_sample(rid, ops, consts, aggs, lambda sh, cell: found.get((sh, cell)), as_number, rules)
        if r is not None:
            recs.append({"breach": r[0], "detail": r[1]})
    if recs:
        compared.append({"recompute": recs, "formula_version": rc.__version__})
        nob = [x for x in recs if not x["breach"]]
        note = " (a labelled reconciliation line is not re-evaluated)" if rid == "GIS-012" else ""
        if nob:
            return "disagrees", method + f"file hash matches; values agree; rule {rid} recomputed on {len(recs)} " \
                                         f"sample(s): {len(nob)} show no breach: " + "; ".join(x["detail"] for x in nob[:3]) + note, compared
        return "agrees", method + f"file hash matches; all values agree; rule {rid} recomputed on {len(recs)} sample(s) " \
                                  f"from the re-read cells: breach confirmed{note}", compared
    return "agrees", method + "file hash matches; all values agree (the rule arithmetic itself is not " \
                              "recomputed)", compared


QUOTE = re.compile(r'"([^"]{1,300})"')


def _norm(t):
    return " ".join(str(t).split()).casefold()


def recheck_pdf_text(f, root):
    loc = f.get("location") or {}
    pdfloc = loc.get("pdf") or {}
    rel = loc.get("file_path")
    if not rel or not pdfloc.get("bbox_pt"):
        return "not_rechecked", "pdf_vector_text: no location.file_path or location.pdf.bbox_pt to re-read", []
    path = Path(root) / rel
    if not path.is_file():
        return "not_rechecked", f"source file not found under the review root: {rel}", []
    quotes = []
    for e in f.get("evidence", []):
        if e.get("source_type") == "pdf_vector_text":
            quotes += QUOTE.findall(e.get("extract") or "")
    if not quotes:
        return "not_rechecked", 'pdf_vector_text: the extract quotes no "text" to re-read', []
    try:
        import pdfplumber
        import pypdfium2 as pdfium
    except ImportError as exc:
        return "not_rechecked", f"{exc.name} is not installed (independent PDF reader)", []
    try:
        doc = pdfium.PdfDocument(str(path))      # size check only; the text is read with pdfplumber
        try:
            pg = doc[int(pdfloc["page"]) - 1]
            if pg.get_rotation() % 360:
                return "not_rechecked", "pdf_vector_text: rotated page; bbox re-read not supported", []
            nobj = 0
            for _ in pg.get_objects(max_depth=15):
                nobj += 1
                if nobj > LARGE_PAGE_OBJECTS:
                    return "not_rechecked", (f"pdf_vector_text: page holds more than {LARGE_PAGE_OBJECTS:,} objects; "
                                             "the independent reader (pdfplumber) is not used on it"), []
        finally:
            doc.close()
        with pdfplumber.open(str(path)) as pl:
            page = pl.pages[int(pdfloc["page"]) - 1]
            x0, top, x1, bottom = map(float, pdfloc["bbox_pt"])
            text = page.within_bbox((x0, top, x1, bottom), relative=False, strict=False).extract_text() or ""
    except Exception as exc:  # reader errors
        return "not_rechecked", f"independent reader could not read the PDF: {type(exc).__name__}: {str(exc)[:160]}", []
    hay = _norm(text)
    compared = [{"page": pdfloc["page"], "bbox_pt": pdfloc["bbox_pt"], "expected": q, "kind": "text",
                 "ok": _norm(q) in hay} for q in quotes]
    method = (f"Independent re-read of {len(compared)} quoted text(s) on page {pdfloc['page']} of {rel} inside the "
              f"bbox with pdfplumber (finder: pypdfium2); ")
    if sha256_file(path) != loc.get("file_sha256"):
        return "disagrees", method + "source file hash differs from location.file_sha256", compared
    bad = [c for c in compared if not c["ok"]]
    if bad:
        return "disagrees", method + f"{len(bad)} quoted text(s) not found: " + "; ".join(c["expected"] for c in bad[:5]), compared
    return "agrees", method + "file hash matches; all quoted texts found", compared


def recheck(f, root, rules=None):
    kinds = {e.get("source_type") for e in f.get("evidence", [])}
    if kinds & {"xlsx_cell", "csv_cell"}:
        return recheck_cells(f, root, rules)
    if "dwg_vector" in kinds:
        return "not_rechecked", "dwg_vector: no independent second path yet (drawing findings are re-checked by the reviewer)", []
    if "pdf_vector_text" in kinds:
        return recheck_pdf_text(f, root)
    if "pdf_ocr" in kinds:
        return "not_rechecked", "pdf_ocr: no independent second reader (OCR again is not independent); crop.py gives the image", []
    return "not_rechecked", f"no independent second path for evidence types {sorted(kinds)} (SPC-0005 §5.7.1)", []


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--candidates", nargs="+", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--root", default=".")
    ap.add_argument("--finding")
    ap.add_argument("--rulebook")
    a = ap.parse_args(argv)
    try:
        rules = None
        if a.rulebook:
            from rulebook_lib import RulebookError, load_rulebook
            try:
                rules = {r["rule_id"]: r for r in load_rulebook(a.rulebook)["rules"]}
            except RulebookError as e:
                raise VerifyError(e.code, e.detail)
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
            res, method, compared = recheck(f, a.root, rules)
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
