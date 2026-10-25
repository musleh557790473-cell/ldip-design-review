#!/usr/bin/env python3
"""CRS comment-closure report (skill 7 M4). Report only — no findings (DM-7).

Reads an RCRC / AICC CRS workbook. Types each row L / D / G from references/row_types.md
(DM-5); reviewer overrides via --types CSV. Outcomes: Addressed / Not addressed / Cannot tell
with evidence. Flags deferred_by_response (DM-8). Never writes Closed / Open (DM-6).
G rows may carry supporting_refs; never an outcome derived from that list (DM-8).

Usage:
  python check_closed_comments.py --crs <crs.xlsx> --out <closure.json>
       [--xlsx <closure.xlsx>] [--types <id,type.csv>] [--row-types <row_types.md>]

Exit: 0 done · 2 input error · 3 missing openpyxl. No network, no shell calls.
"""
__version__ = "0.1.0"

import argparse
import csv
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from rev_common import EXIT_DEP, EXIT_INPUT, EXIT_OK, atomic_write_json, emit, fail, path_ok  # noqa: E402

SKILL_DIR = Path(__file__).resolve().parents[1]
DEFAULT_ROW_TYPES = SKILL_DIR / "references" / "row_types.md"

# Expected CRS header labels (RCRC / AICC template).
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

# Words we must never emit as our outcome (DM-6).
FORBIDDEN_OUTCOMES = {"closed", "open"}


def need_openpyxl():
    try:
        import openpyxl  # noqa: F401
        return None
    except ImportError:
        return "openpyxl is not installed"


def parse_row_types_md(path):
    """Keyword lists for L / D / G from references/row_types.md."""
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


def classify_row(comment, response, ref, keywords):
    blob = f"{comment or ''} {response or ''} {ref or ''}".lower()
    scores = {t: sum(1 for kw in kws if kw in blob) for t, kws in keywords.items()}
    # Prefer the highest score; tie-break L > D > G (drawing-specific first)
    best = sorted(scores, key=lambda t: (-scores[t], "LDG".index(t)))
    if scores[best[0]] == 0:
        return "G", "no keyword hit — default G"
    return best[0], f"keywords:{scores}"


def outcome_for(response, row_type):
    """Addressed / Not addressed / Cannot tell. Never Closed/Open (DM-6).
    G rows: outcome from response text only — supporting_refs never decide (DM-8).
    A deferred response (DM-8) cannot count as Addressed in this submittal."""
    text = (response or "").strip()
    deferred = bool(text and DEFER_PAT.search(text))
    if not text:
        return "Cannot tell", False, "empty contractor response"
    if NOT_ADDRESSED_PAT.search(text) and not ADDRESSED_PAT.search(text):
        return "Not addressed", deferred, "response declines or rejects the comment"
    # DM-8: deferral to a later stage → Cannot tell (not Addressed), even if the
    # sentence contains "addressed" ("will be addressed at …").
    if deferred:
        return "Cannot tell", True, "response defers without confirming the change in this submittal"
    if ADDRESSED_PAT.search(text):
        return "Addressed", False, "response indicates action taken"
    return "Cannot tell", False, "response does not clearly confirm or reject"


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
    # Find header row
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
            # Raw comment-status cell kept for the reviewer; we never copy Closed/Open as outcome.
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
        # DM-6: Outcome column uses only Addressed / Not addressed / Cannot tell
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


def supporting_refs_for(row, row_type):
    """Optional list for G rows only (DM-8). Never used to set outcome."""
    if row_type != "G":
        return None
    refs = []
    if row.get("ref"):
        refs.append(row["ref"])
    # Pull drawing-like tokens from the comment as soft refs
    for tok in re.findall(r"\b[A-Z0-9]+(?:-[A-Z0-9]+){3,}\b", row.get("comment") or ""):
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
    a = ap.parse_args(argv)
    try:
        if need_openpyxl():
            return fail(EXIT_DEP, need_openpyxl())
        for p in (a.crs, a.out, a.row_types):
            if not path_ok(p):
                return fail(EXIT_INPUT, f"path longer than 250 characters: {p}")
        if a.xlsx and not path_ok(a.xlsx):
            return fail(EXIT_INPUT, f"path longer than 250 characters: {a.xlsx}")
        keywords = parse_row_types_md(a.row_types)
        overrides = load_type_overrides(a.types) if a.types else {}
        raw_rows = read_crs(a.crs)
        results = []
        for row in raw_rows:
            key = str(row.get("no") or row["row"])
            if key in overrides:
                rtype, why = overrides[key], "reviewer override (--types)"
            else:
                rtype, why = classify_row(row["comment"], row["response"], row.get("ref"), keywords)
            outcome, deferred, evidence = outcome_for(row["response"], rtype)
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
                # Intentionally omit Closed/Open; raw cell available only as comment_status_raw
                # for audit and is not an outcome field.
                "comment_status_raw": row.get("comment_status_raw"),
            }
            srefs = supporting_refs_for(row, rtype)
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
            "meta": {"skill": "comparing-drawing-revisions", "skill_version": __version__},
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
