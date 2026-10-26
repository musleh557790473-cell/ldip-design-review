#!/usr/bin/env python3
"""CRS comment-closure report (skill 7 M4, Rev M §4.15). Report only — no findings (DM-7).

Reads an RCRC / AICC CRS workbook. Types each row L / D / G from references/row_types.md
(DM-5); reviewer overrides via --types CSV.

Evidence → outcome (Rev M M4):
  L → location changed / unchanged in N vs N-1 (--rev-diff) → Addressed / Not addressed
  D → deliverable present / absent in N (--package-n) → Addressed / Not addressed
  G → Cannot tell; optional supporting_refs from skills 3 / 13 (DM-8), never an outcome
When L/D evidence inputs are omitted, fall back to contractor-response heuristics so synthetic
tests still run; the response and status code are always copied. "Later stage" responses set
deferred_by_response (DM-8). Never writes Closed / Open (DM-6).

Usage:
  python check_closed_comments.py --crs <crs.xlsx> --out <closure.json>
       [--xlsx <closure.xlsx>] [--types <id,type.csv>] [--row-types <row_types.md>]
       [--package-n <package.json>] [--rev-diff <rev_diff.json>]
       [--supporting-refs <id,ref;ref.csv>]

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

SKILL_DIR = Path(__file__).resolve().parents[1]
DEFAULT_ROW_TYPES = SKILL_DIR / "references" / "row_types.md"

HEADERS = {
    "no": re.compile(r"^#|no\.?$", re.I),
    "ref": re.compile(r"page\s*/\s*para\s*/\s*clause\s*/\s*doc\.?\s*no\.?", re.I),
    "comment": re.compile(r"review\s+comment", re.I),
    "response": re.compile(r"contractor.?s?\s+response", re.I),
    "status_code": re.compile(r"status\s+code", re.I),
    "comment_status": re.compile(r"comment\s+status", re.I),
}

DEFER_PAT = re.compile(
    r"\b(deferr\w*|later\s+stage|next\s+(stage|submission|phase)|detailed\s+design|"
    r"will\s+be\s+addressed\s+(at|in|during)|subsequent\s+submission|future\s+stage)\b",
    re.I,
)
ADDRESSED_PAT = re.compile(
    r"\b(addressed|revised|amended|updated|corrected|incorporated|accepted|"
    r"implemented|done|completed|closed\s+out|actioned)\b",
    re.I,
)
NOT_ADDRESSED_PAT = re.compile(
    r"\b(not\s+accepted|disagree|rejected|no\s+change|will\s+not|retained\s+as\s+is|"
    r"unchanged|not\s+applicable)\b",
    re.I,
)
DOC_TOKEN = re.compile(r"\b[A-Z0-9]+(?:-[A-Z0-9]+){3,}\b")
FORBIDDEN_OUTCOMES = {"closed", "open"}


def need_openpyxl():
    try:
        import openpyxl  # noqa: F401
        return None
    except ImportError:
        return "openpyxl is not installed"


def parse_row_types_md(path):
    text = Path(path).read_text(encoding="utf-8")
    buckets = {"L": [], "D": [], "G": []}
    current = None
    for line in text.splitlines():
        m = re.match(r"^##\s*([LDG])\b", line.strip(), re.I)
        if m:
            current = m.group(1).upper()
            continue
        if current and line.strip().startswith("-"):
            kw = line.strip().lstrip("-").strip().strip("`\"'").lower()
            if kw:
                buckets[current].append(kw)
    return buckets


def load_type_overrides(path):
    out = {}
    with open(path, newline="", encoding="utf-8") as fh:
        rows = list(csv.reader(fh))
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
    """CSV: id,ref;ref2 or id,ref (one ref per extra column). For G rows (DM-8)."""
    out = {}
    with open(path, newline="", encoding="utf-8") as fh:
        rows = list(csv.reader(fh))
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


def classify_row(comment, response, ref, keywords):
    blob = f"{comment or ''} {response or ''} {ref or ''}".lower()
    scores = {t: sum(1 for kw in kws if kw in blob) for t, kws in keywords.items()}
    best = sorted(scores, key=lambda t: (-scores[t], "LDG".index(t)))
    if scores[best[0]] == 0:
        return "G", "no keyword hit — default G"
    return best[0], f"keywords:{scores}"


def deferred_flag(response):
    text = (response or "").strip()
    return bool(text and DEFER_PAT.search(text))


def response_fallback(response):
    """Used only when L/D location/package evidence is unavailable."""
    text = (response or "").strip()
    deferred = deferred_flag(text)
    if not text:
        return "Cannot tell", deferred, "empty contractor response; no L/D evidence input"
    if deferred:
        return "Cannot tell", True, "response defers without confirming the change in this submittal"
    if NOT_ADDRESSED_PAT.search(text) and not ADDRESSED_PAT.search(text):
        return "Not addressed", False, "response declines or rejects the comment (no L/D evidence)"
    if ADDRESSED_PAT.search(text):
        return "Addressed", False, "response indicates action taken (no L/D evidence)"
    return "Cannot tell", False, "response unclear; no L/D evidence input"


def package_bases(pkg):
    bases = set()
    for row in pkg.get("sheet_register") or []:
        if row.get("base"):
            bases.add(str(row["base"]).upper())
        for n in (row.get("numbers") or {}).values():
            if n:
                bases.add(str(n).upper())
                # also without revision suffix
                parts = str(n).upper().split("-")
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
    """L: changed / unchanged at the location (Rev M M4)."""
    if not rev_diff:
        return None
    tokens = set(tokens_from_row(row))
    sheet_hint = (row.get("ref") or "").lower()
    changed = False
    seen = False
    for ed in rev_diff.get("entity_diffs") or []:
        # pair sides may name documents
        seen = True
        if ed.get("changed") or ed.get("moved"):
            # If the comment names no document token, any change counts as location change
            # when the reviewer typed L; otherwise require a token hit on pair ids later.
            if not tokens:
                changed = bool(ed.get("changed") or ed.get("moved"))
                if changed:
                    break
            changed = bool(ed.get("changed") or ed.get("moved"))
            if changed and not tokens:
                break
    # Prefer matching a named document against pairs
    if tokens and rev_diff.get("pairs"):
        for i, pair in enumerate(rev_diff["pairs"]):
            sides = f"{pair.get('previous', {}).get('number', '')} {pair.get('current', {}).get('number', '')}".upper()
            if any(t in sides or t.rsplit("-", 1)[0] in sides for t in tokens):
                ed = next((e for e in (rev_diff.get("entity_diffs") or []) if e.get("pair_index") == i), None)
                if ed is None:
                    return "Cannot tell", "named document paired but no entity_diff row"
                if ed.get("changed") or ed.get("moved"):
                    return "Addressed", "location changed on the named document (entity hash)"
                return "Not addressed", "location unchanged on the named document (entity hash)"
    if not seen and not (rev_diff.get("entity_diffs") or rev_diff.get("raster_diffs")):
        return None
    # Sheet-level: any changed pair / raster when comment cites a sheet
    if "sheet" in sheet_hint or "sheet" in (row.get("comment") or "").lower():
        if any(e.get("changed") or e.get("moved") for e in (rev_diff.get("entity_diffs") or [])):
            return "Addressed", "sheet location changed between N-1 and N"
        if any(r.get("changed") for r in (rev_diff.get("raster_diffs") or [])):
            return "Addressed", "sheet raster region changed between N-1 and N"
        if rev_diff.get("entity_diffs") or rev_diff.get("raster_diffs"):
            return "Not addressed", "sheet location unchanged between N-1 and N"
    if changed:
        return "Addressed", "geometry changed at a paired location"
    if rev_diff.get("entity_diffs"):
        return "Not addressed", "no geometry change recorded for L row"
    return None


def evidence_D(row, package_n):
    """D: deliverable / data present or absent in N (Rev M M4)."""
    if not package_n:
        return None
    bases = package_bases(package_n)
    tokens = tokens_from_row(row)
    if not tokens:
        # No concrete document id — cannot decide presence
        return "Cannot tell", "D row names no document number to look up in N"
    hits = [t for t in tokens if t in bases or any(t.startswith(b) or b.startswith(t.rsplit("-", 1)[0]) for b in bases)]
    # Simpler: token or its base (strip revision) in bases
    found = []
    for t in tokens:
        base = t.rsplit("-", 1)[0] if re.search(r"-(0[A-Z]|\d{2}|A\d)$", t) else t
        if t in bases or base in bases:
            found.append(t)
    if found:
        return "Addressed", f"deliverable present in N: {', '.join(found)}"
    return "Not addressed", f"deliverable absent in N (looked for {', '.join(tokens)})"


def outcome_for(row, row_type, rev_diff=None, package_n=None):
    """Rev M M4 evidence paths. G → Cannot tell always (DM-8). Never Closed/Open (DM-6)."""
    deferred = deferred_flag(row.get("response"))
    if row_type == "G":
        return ("Cannot tell", deferred,
                "G design-statement row — Cannot tell (DM-8); response copied, not used as outcome")
    if row_type == "L":
        ev = evidence_L(row, rev_diff)
        if ev:
            outcome, evidence = ev
            return outcome, deferred, evidence
        return response_fallback(row.get("response"))
    if row_type == "D":
        ev = evidence_D(row, package_n)
        if ev:
            outcome, evidence = ev
            return outcome, deferred, evidence
        return response_fallback(row.get("response"))
    return response_fallback(row.get("response"))


def read_crs(path):
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
        comment, response = cell("comment"), cell("response")
        if not comment and not response and not cell("no"):
            continue
        out.append({
            "row": rnum,
            "no": cell("no"),
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
    """G rows only (DM-8). Overrides from --supporting-refs (skills 3 / 13) win."""
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
    ap.add_argument("--xlsx", help="Reviewer workbook (closure.xlsx)")
    ap.add_argument("--types", help="CSV overrides: id/no, type (L|D|G)")
    ap.add_argument("--row-types", default=str(DEFAULT_ROW_TYPES))
    ap.add_argument("--package-n", help="N package.json for D present/absent checks")
    ap.add_argument("--rev-diff", help="rev_diff.json for L changed/unchanged checks")
    ap.add_argument("--supporting-refs", help="CSV id,ref for G rows (skills 3/13, DM-8)")
    a = ap.parse_args(argv)
    try:
        if need_openpyxl():
            return fail(EXIT_DEP, need_openpyxl())
        for p in (a.crs, a.out, a.row_types):
            if not path_ok(p):
                return fail(EXIT_INPUT, f"path longer than 250 characters: {p}")
        for p in (a.xlsx, a.types, a.package_n, a.rev_diff, a.supporting_refs):
            if p and not path_ok(p):
                return fail(EXIT_INPUT, f"path longer than 250 characters: {p}")
        keywords = parse_row_types_md(a.row_types)
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
                rtype, why = classify_row(row["comment"], row["response"], row.get("ref"), keywords)
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
            "marking": "report only — no findings (DM-7); outcomes are not Closed/Open (DM-6)",
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
