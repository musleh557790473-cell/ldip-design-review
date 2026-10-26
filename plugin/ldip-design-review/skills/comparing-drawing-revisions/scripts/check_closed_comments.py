#!/usr/bin/env python3
"""CRS comment-closure report (skill 7 M4; S31 R4–R7). Report only — no findings (DM-7).

Types each row L / D / G per Rev M M4 / review R5 (not loose keyword bags).
Evidence → outcome:
  L → changed / unchanged at location (--rev-diff)
  D → present / absent in N (--package-n)
  G → Cannot tell; optional supporting_refs (DM-8)
Without L/D evidence the outcome is **Cannot tell** — contractor response is copied only (R4).
Never writes Closed / Open (DM-6). Deferred phrases per R6. Integer `#` only; stop at sign-off (R7).

Usage:
  python check_closed_comments.py --crs <crs.xlsx> --out <closure.json>
       [--xlsx <closure.xlsx>] [--types <id,type.csv>]
       [--package-n <package.json>] [--rev-diff <rev_diff.json>]
       [--supporting-refs <id,ref.csv>]

Exit: 0 done · 2 input error · 3 missing openpyxl. No network, no shell calls.
"""
__version__ = "0.1.0"

import argparse
import csv
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from rev_common import (  # noqa: E402
    EXIT_DEP, EXIT_INPUT, EXIT_OK, atomic_write_json, emit, fail, path_ok, read_json,
)

HEADERS = {
    "no": re.compile(r"^#|no\.?$", re.I),
    "ref": re.compile(r"page\s*/\s*para\s*/\s*clause\s*/\s*doc\.?\s*no\.?", re.I),
    "comment": re.compile(r"review\s+comment", re.I),
    "response": re.compile(r"contractor.?s?\s+response", re.I),
    "status_code": re.compile(r"status\s+code", re.I),
    "comment_status": re.compile(r"comment\s+status", re.I),
}

# R6 deferred phrases
DEFER_PAT = re.compile(
    r"\b("
    r"deferr\w*|later\s+stage|next\s+(stage|submission|phase)|detailed\s+design|"
    r"will\s+be\s+addressed\s+(at|in|during)|subsequent\s+submission|future\s+stage|"
    r"IFC\s+submission|IFC\s+stage|subsequent\s+stages?|"
    r"will\s+be\s+submitted|will\s+be\s+part\s+of\s+a\s+separate|in\s+all\s+stages"
    r")\b",
    re.I,
)

DOC_TOKEN = re.compile(r"\b[A-Z0-9]+(?:-[A-Z0-9]+){3,}\b")
# L: sheet / grid / coordinate / chainage pattern (R5)
LOC_PAT = re.compile(
    r"\b("
    r"sheet\s*\d+|sh\.?\s*\d+|grid\s*[A-Z]?\-?\d*|[A-Z]\s*/\s*\d+|"
    r"chainage|ch\.?\s*\d+|station\s*\d+|easting|northing|"
    r"coord(?:inate)?s?|N\s*=\s*\d|E\s*=\s*\d"
    r")\b",
    re.I,
)
SHEET_IN_REF = re.compile(r"\b(?:sheet|sh\.?)\s*\d+", re.I)
# D: request verb + deliverable noun (R5)
REQ_VERB = re.compile(r"\b(submit|provide|furnish|supply|include|attach)\b", re.I)
DELIV_NOUN = re.compile(
    r"\b("
    r"report|survey|surface|sections?|spot\s+levels?|elevations?|quantities|"
    r"drawings?|package|details?|landxml|ifc(?:\s+model)?|model\s+file|native\s+file|"
    r"schedule|register|workbook|deliverable"
    r")\b",
    re.I,
)
SIGN_OFF = re.compile(
    r"reviewed\s+action|sign[\s-]?off|reviewer\s*:|a[\s-]?no\s+objection|"
    r"b[\s-]?no\s+objection|c[\s-]?rejected|contractor.?s?\s+acknowledg",
    re.I,
)
FORBIDDEN_OUTCOMES = {"closed", "open"}


def need_openpyxl():
    try:
        import openpyxl  # noqa: F401
        return None
    except ImportError:
        return "openpyxl is not installed"


def load_type_overrides(path):
    out = {}
    with open(path, newline="", encoding="utf-8") as fh:
        rows = list(csv.reader(fh, delimiter=",", quotechar='"'))
    start = 0
    if rows and rows[0] and str(rows[0][0]).strip().lower() in ("id", "no", "#", "row"):
        start = 1
    for row in rows[start:]:
        if len(row) < 2:
            continue
        key, typ = str(row[0]).strip(), str(row[1]).strip().upper()
        if typ in ("L", "D", "G"):
            out[key] = typ
    return out


def load_supporting_refs_csv(path):
    out = {}
    with open(path, newline="", encoding="utf-8") as fh:
        rows = list(csv.reader(fh, delimiter=",", quotechar='"'))
    start = 0
    if rows and rows[0] and str(rows[0][0]).strip().lower() in ("id", "no", "#"):
        start = 1
    for row in rows[start:]:
        if not row:
            continue
        key = str(row[0]).strip()
        refs = []
        for cell in row[1:]:
            for part in re.split(r"[;|]", str(cell)):
                part = part.strip()
                if part:
                    refs.append(part)
        if key and refs:
            out[key] = refs
    return out


def classify_row(comment, response, ref):
    """R5: L = doc number + location pattern (or Doc.No. has number + sheet);
    D = request verb + deliverable noun; else G."""
    comment = comment or ""
    ref = ref or ""
    blob = f"{ref} {comment}"
    docs = DOC_TOKEN.findall(blob)
    has_loc = bool(LOC_PAT.search(blob))
    ref_has_doc = bool(DOC_TOKEN.search(ref))
    ref_has_sheet = bool(SHEET_IN_REF.search(ref))
    if (docs and has_loc) or (ref_has_doc and ref_has_sheet) or (ref_has_doc and has_loc):
        return "L", "document number + sheet/grid/coordinate (R5)"
    if REQ_VERB.search(comment) and DELIV_NOUN.search(comment):
        return "D", "request verb + deliverable noun (R5)"
    return "G", "design statement / other (R5 default)"


def deferred_flag(response):
    return bool((response or "").strip() and DEFER_PAT.search(response))


def package_bases(pkg):
    bases = set()
    for row in pkg.get("sheet_register") or []:
        if row.get("base"):
            bases.add(str(row["base"]).upper())
        nums = row.get("numbers") or {}
        vals = nums.values() if isinstance(nums, dict) else (nums if isinstance(nums, list) else [])
        for n in vals:
            if not n:
                continue
            n = str(n).upper()
            bases.add(n)
            parts = n.split("-")
            if len(parts) > 1:
                bases.add("-".join(parts[:-1]))
    for d in pkg.get("drawings") or []:
        if d.get("number"):
            bases.add(str(d["number"]).upper())
        if d.get("base"):
            bases.add(str(d["base"]).upper())
    return bases


def tokens_from_row(row):
    blob = f"{row.get('ref') or ''} {row.get('comment') or ''}"
    return [t.upper() for t in DOC_TOKEN.findall(blob)]


def evidence_L(row, rev_diff):
    if not rev_diff:
        return None
    tokens = set(tokens_from_row(row))
    if tokens and rev_diff.get("pairs"):
        for i, pair in enumerate(rev_diff["pairs"]):
            sides = f"{pair.get('previous', {}).get('number', '')} {pair.get('current', {}).get('number', '')}".upper()
            if any(t in sides or t.rsplit("-", 1)[0] in sides for t in tokens):
                ed = next((e for e in (rev_diff.get("entity_diffs") or []) if e.get("pair_index") == i), None)
                rd = next((e for e in (rev_diff.get("raster_diffs") or []) if e.get("pair_index") == i), None)
                changed = (ed and (ed.get("changed") or ed.get("moved"))) or (rd and rd.get("changed"))
                if ed is None and rd is None:
                    return "Cannot tell", "named document paired but no entity/raster diff"
                if changed:
                    return "Addressed", "location changed on the named document"
                return "Not addressed", "location unchanged on the named document"
    if any(e.get("changed") or e.get("moved") for e in (rev_diff.get("entity_diffs") or [])):
        return "Addressed", "geometry changed between N-1 and N"
    if any(r.get("changed") for r in (rev_diff.get("raster_diffs") or [])):
        return "Addressed", "raster region changed between N-1 and N"
    if rev_diff.get("entity_diffs") or rev_diff.get("raster_diffs"):
        return "Not addressed", "no location change recorded for L row"
    return None


def evidence_D(row, package_n):
    if not package_n:
        return None
    bases = package_bases(package_n)
    tokens = tokens_from_row(row)
    if not tokens:
        return "Cannot tell", "D row names no document number to look up in N"
    found = []
    for t in tokens:
        base = t.rsplit("-", 1)[0] if re.search(r"-(0[A-Z]|\d{2}|A\d)$", t) else t
        if t in bases or base in bases:
            found.append(t)
    if found:
        return "Addressed", f"deliverable present in N: {', '.join(found)}"
    return "Not addressed", f"deliverable absent in N (looked for {', '.join(tokens)})"


def outcome_for(row, row_type, rev_diff=None, package_n=None):
    """R4: response never sets outcome. Without L/D evidence → Cannot tell."""
    deferred = deferred_flag(row.get("response"))
    if row_type == "G":
        return ("Cannot tell", deferred,
                "G design-statement row — Cannot tell (DM-8); response copied only")
    if row_type == "L":
        ev = evidence_L(row, rev_diff)
        if ev:
            return ev[0], deferred, ev[1]
        return ("Cannot tell", deferred,
                "no L location evidence (--rev-diff); response copied only (R4)")
    if row_type == "D":
        ev = evidence_D(row, package_n)
        if ev:
            return ev[0], deferred, ev[1]
        return ("Cannot tell", deferred,
                "no D package evidence (--package-n); response copied only (R4)")
    return ("Cannot tell", deferred, "unknown row type; response copied only")


def _is_int_no(v):
    if v is None:
        return False
    s = str(v).strip()
    if not s or not re.fullmatch(r"\d+", s):
        return False
    return True


def read_crs(path):
    """R7: accept only rows whose # cell is an integer; stop at reviewer / sign-off block."""
    err = need_openpyxl()
    if err:
        raise RuntimeError(err)
    import openpyxl
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    wb.close()
    if not rows:
        raise ValueError(f"{path} has no rows")
    header_idx, cols = None, {}
    for i, row in enumerate(rows[:30]):
        cells = [str(c).strip() if c is not None else "" for c in row]
        found = {}
        for j, cell in enumerate(cells):
            for key, pat in HEADERS.items():
                if cell and pat.search(cell) and key not in found:
                    found[key] = j
        if "comment" in found and "response" in found:
            header_idx, cols = i, found
            break
    if header_idx is None:
        raise ValueError("CRS header row not found (need Review Comment and Contractor's Response)")
    out = []
    for rnum, row in enumerate(rows[header_idx + 1:], start=header_idx + 2):
        def cell(key):
            j = cols.get(key)
            if j is None or j >= len(row):
                return None
            v = row[j]
            return str(v).strip() if v is not None and str(v).strip() else None
        no = cell("no")
        comment, response = cell("comment"), cell("response")
        blob = " ".join(x for x in (no, comment, response, cell("ref")) if x)
        if SIGN_OFF.search(blob or ""):
            break  # R7: stop at reviewer / sign-off block
        if not _is_int_no(no):
            # Skip non-integer # (footer / blanks); stop if we already have data rows
            if out and (comment or response):
                # non-integer after real rows → footer
                break
            continue
        if not comment and not response:
            continue
        out.append({
            "row": rnum,
            "no": str(int(no)),
            "ref": cell("ref"),
            "comment": comment or "",
            "response": response or "",
            "status_code": cell("status_code"),
            "comment_status_raw": cell("comment_status"),
        })
    return out


def write_xlsx(path, rows_out):
    err = need_openpyxl()
    if err:
        raise RuntimeError(err)
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Closure"
    headers = ["#", "Page/Para/Clause/Doc. No.", "Review Comment", "Contractor's Response",
               "Status Code", "Row type", "Outcome", "Deferred by response", "Evidence",
               "Supporting refs"]
    ws.append(headers)
    for r in rows_out:
        assert r["outcome"].lower() not in FORBIDDEN_OUTCOMES
        ws.append([
            r.get("no"), r.get("ref"), r.get("comment"), r.get("response"),
            r.get("status_code"), r.get("row_type"), r.get("outcome"),
            "yes" if r.get("deferred_by_response") else "no",
            r.get("evidence"),
            "; ".join(r.get("supporting_refs") or []),
        ])
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)


def supporting_refs_for(row, row_type, overrides):
    if row_type != "G":
        return None
    key = str(row.get("no") or row.get("row"))
    if key in overrides:
        return list(overrides[key])
    refs = []
    if row.get("ref"):
        refs.append(row["ref"])
    for tok in DOC_TOKEN.findall(row.get("comment") or ""):
        if tok not in refs:
            refs.append(tok)
    return refs or []


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--crs", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--xlsx")
    ap.add_argument("--types")
    ap.add_argument("--package-n")
    ap.add_argument("--rev-diff")
    ap.add_argument("--supporting-refs")
    a = ap.parse_args(argv)
    try:
        if need_openpyxl():
            return fail(EXIT_DEP, need_openpyxl())
        for p in (a.crs, a.out):
            if not path_ok(p):
                return fail(EXIT_INPUT, f"path longer than 250 characters: {p}")
        for p in (a.xlsx, a.types, a.package_n, a.rev_diff, a.supporting_refs):
            if p and not path_ok(p):
                return fail(EXIT_INPUT, f"path longer than 250 characters: {p}")
        overrides = load_type_overrides(a.types) if a.types else {}
        sref_over = load_supporting_refs_csv(a.supporting_refs) if a.supporting_refs else {}
        package_n = read_json(a.package_n) if a.package_n else None
        rev_diff = read_json(a.rev_diff) if a.rev_diff else None
        raw_rows = read_crs(a.crs)
        results = []
        for row in raw_rows:
            key = str(row.get("no") or row["row"])
            if key in overrides:
                rtype, why = overrides[key], "reviewer override (--types)"
            else:
                rtype, why = classify_row(row["comment"], row["response"], row.get("ref"))
            outcome, deferred, evidence = outcome_for(row, rtype, rev_diff=rev_diff, package_n=package_n)
            if outcome.lower() in FORBIDDEN_OUTCOMES:
                return fail(EXIT_INPUT, f"internal error: forbidden outcome {outcome}")
            rec = {
                "no": row.get("no") or str(row["row"]),
                "ref": row.get("ref"),
                "comment": row["comment"],
                "response": row["response"],
                "status_code": row.get("status_code"),
                "row_type": rtype,
                "row_type_reason": why,
                "outcome": outcome,
                "deferred_by_response": deferred,
                "evidence": evidence,
                "comment_status_raw": row.get("comment_status_raw"),
            }
            srefs = supporting_refs_for(row, rtype, sref_over)
            if srefs is not None:
                rec["supporting_refs"] = srefs
            results.append(rec)

        counts = {
            "rows": len(results),
            "L": sum(1 for r in results if r["row_type"] == "L"),
            "D": sum(1 for r in results if r["row_type"] == "D"),
            "G": sum(1 for r in results if r["row_type"] == "G"),
            "Addressed": sum(1 for r in results if r["outcome"] == "Addressed"),
            "Not addressed": sum(1 for r in results if r["outcome"] == "Not addressed"),
            "Cannot tell": sum(1 for r in results if r["outcome"] == "Cannot tell"),
            "deferred_by_response": sum(1 for r in results if r["deferred_by_response"]),
        }
        doc = {
            "closure_version": "1.0",
            "marking": "report only — no findings (DM-7); outcomes are not Closed/Open (DM-6); "
                       "response copied only (R4)",
            "rows": results,
            "counts": counts,
            "meta": {
                "skill": "comparing-drawing-revisions",
                "skill_version": __version__,
                "package_n": bool(package_n),
                "rev_diff": bool(rev_diff),
            },
        }
        atomic_write_json(a.out, doc)
        if a.xlsx:
            write_xlsx(a.xlsx, results)
        emit({"status": "ok", "out": str(a.out), "xlsx": a.xlsx, **{k: counts[k] for k in
              ("rows", "L", "D", "G", "Addressed", "deferred_by_response")}})
        return EXIT_OK
    except RuntimeError as exc:
        return fail(EXIT_DEP, str(exc))
    except ValueError as exc:
        return fail(EXIT_INPUT, str(exc))
    except OSError as exc:
        return fail(EXIT_INPUT, str(exc))


if __name__ == "__main__":
    sys.exit(main())
