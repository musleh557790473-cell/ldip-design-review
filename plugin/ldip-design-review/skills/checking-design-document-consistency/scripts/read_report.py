#!/usr/bin/env python3
"""Locate earthworks / pavement / geotech claims in skill-2 PDF text (skill 16; DN-2).

R4d: TOC = early consecutive contents run after the cover (p2–p9); body sections
     start at numbered headings after the last TOC page (`9.3.2 Earthworks Assessment`).
R5: rebuild page text by bbox **lines** (cluster by y, then left→right).
R9: `balanced` only in earthworks section; drop ICT / non-earthworks context.

Usage:
  python read_report.py --text <report.pdf.json|claims.json|report.txt> --out <report_claims.json>

Exit: 0 done · 2 input · 4 schema. No network, no shell.
"""
__version__ = "0.1.0"

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cons_common import (  # noqa: E402
    EXIT_INPUT, EXIT_OK, SKILL_ROOT, atomic_write_json, emit, fail, main_guard,
    path_ok, read_json,
)

CLAIMS_MD = SKILL_ROOT / "references" / "claims.md"

# R4c: section starts at a numbered heading (9.3.2 / 1.3.2 …), not bare keywords.
NUMBERED_HEADING = re.compile(
    r"(?:^|\n)\s*(\d+(?:\.\d+)+)\s+([A-Z][A-Za-z][\w\s/-]{1,80})",
)
SECTION_TITLE = {
    "earthworks": re.compile(
        r"^(?:earthworks?|cut\s*(?:and|&|/)\s*fill|grading|mass\s+haul)\b", re.I),
    "pavement": re.compile(
        r"^(?:pavement|road\s+construction|flexible\s+pavement|wearing\s+course)\b", re.I),
    "geotechnical": re.compile(
        r"^(?:geotech(?:nical)?|ground\s+condition|foundation)\b", re.I),
}

TOC_HINT = re.compile(r"\b(contents|table\s+of\s+contents|\btoc\b)\b", re.I)
# Dot leaders may arrive as "...." or spaced ". . . ." after bbox rebuild
TOC_DOT_LEADERS = re.compile(
    r"(?:\.{3,}|(?:\.\s*){3,})\s*\d{1,3}\s*$", re.M)
TOC_DOTS_ONLY = re.compile(r"(?:\.{3,}|(?:\.\s*){3,})")
# Page-number-at-end lines (TOC style without requiring dots)
TOC_PAGE_NUM_LINE = re.compile(r"^.{4,120}?\s+(\d{1,3})\s*$", re.M)
TOC_LEADER_MIN = 3
TOC_CONTINUE_MIN = 1  # R4d: once TOC started, keep consecutive weak-leader pages
TOC_EARLY_MAX_PAGE = 20  # TOC only at document start (after cover)
LINE_Y_TOL = 3.0  # R5: spans within this many pt share a line

# R9: ICT / telecom / etc. close earthworks; balanced needs earthworks context
ICT_HINT = re.compile(r"\b(ICT|information\s+and\s+communications?|telecom)\b", re.I)
EARTHWORKS_CONTEXT = re.compile(r"\b(earthworks?|cut|fill|grading|excavation|embankments?)\b", re.I)
PAVEMENT_DEPTH_CONTEXT = re.compile(r"\bpavement\b", re.I)
SUBGRADE_CONTEXT = re.compile(r"\b(?:subgrade|ToS|top\s+of\s+subgrade)\b", re.I)
STORMWATER_FREEBOARD = re.compile(r"\b(?:free\s*board|storm\s*water|stormwater)\b", re.I)

CLAIM_PATTERNS = {
    "balanced": re.compile(
        r"\bbalanced\b|cut\s*(?:and|&)\s*fill\s+balanc\w*|earthworks?\s+balanc\w*", re.I),
    "import": re.compile(r"\bimport(?:ed)?\s+(?:fill|material)\b|\bborrow\b", re.I),
    "export": re.compile(r"\bexport(?:ed)?\b|\bdispos(?:al|ed)\s+off[- ]?site\b", re.I),
    "basis_ratio": re.compile(
        r"(?:rock|suitable|disposal|spoil).{0,60}(?:0\.[268]|[268]0\s*%)"
        r"|(?:0\.[268]|[268]0\s*%).{0,60}(?:rock|suitable|disposal|spoil)"
        r"|(?:rock|suitable|disposal).{0,80}(?:0\.2|20\s*%).{0,40}(?:0\.8|80\s*%)",
        re.I | re.S),
    "pavement_depth": re.compile(
        r"(?:pavement|wearing|thickness|ToS|top\s+of\s+subgrade).{0,80}?(\d+(?:\.\d+)?)\s*mm"
        r"|(\d+(?:\.\d+)?)\s*mm.{0,80}?(?:pavement|wearing|thickness|ToS|subgrade)",
        re.I | re.S),
}


def _bbox(span):
    bb = span.get("bbox") or span.get("bbox_pt")
    if not bb or len(bb) < 2:
        return None
    return [float(bb[0]), float(bb[1]), float(bb[2]) if len(bb) > 2 else float(bb[0]),
            float(bb[3]) if len(bb) > 3 else float(bb[1])]


def page_text(page):
    """R5: cluster spans into lines by y, sort each line by x, join with newlines."""
    spans = list(page.get("spans") or []) + list(page.get("ocr_spans") or [])
    rows = []
    for sp in spans:
        t = sp.get("text")
        if not t:
            continue
        bb = _bbox(sp)
        if bb is None:
            rows.append((1e9, 1e9, str(t)))
        else:
            rows.append((bb[1], bb[0], str(t)))
    if not rows:
        return ""
    rows.sort(key=lambda r: (round(r[0] / LINE_Y_TOL) * LINE_Y_TOL, r[1]))
    lines = []
    cur_y = None
    cur = []
    for y, x, t in rows:
        y_key = round(y / LINE_Y_TOL) * LINE_Y_TOL
        if cur_y is None or abs(y_key - cur_y) <= LINE_Y_TOL:
            cur.append((x, t))
            if cur_y is None:
                cur_y = y_key
        else:
            cur.sort(key=lambda p: p[0])
            lines.append(" ".join(p[1] for p in cur))
            cur = [(x, t)]
            cur_y = y_key
    if cur:
        cur.sort(key=lambda p: p[0])
        lines.append(" ".join(p[1] for p in cur))
    return "\n".join(lines)


def _toc_score(text):
    """Count TOC-like lines (dot leaders preferred; trailing page numbers weaker)."""
    if not text:
        return 0
    leaders = TOC_DOT_LEADERS.findall(text)
    if leaders:
        return len(leaders)
    # Spaced/broken leaders still count via any multi-dot runs + page nums
    dots = TOC_DOTS_ONLY.findall(text)
    page_lines = TOC_PAGE_NUM_LINE.findall(text)
    if dots and page_lines:
        return max(len(dots), len(page_lines) // 2)
    return len(page_lines) // 2


def mark_toc_pages(pages):
    """R4d: TOC = early consecutive run after cover (QRWN p2–p9).

    Mid-document tables must NOT be marked TOC. Continuation after the first
    TOC page uses a lower leader threshold so sparse OCR pages stay in the run.
    """
    ordered = sorted(pages, key=lambda p: p["page"])
    for p in ordered:
        p["is_toc"] = False
    if not ordered:
        return pages

    cover_page = ordered[0]["page"]

    def strong_toc(p):
        if p["page"] == cover_page or p["page"] > TOC_EARLY_MAX_PAGE:
            return False
        text = p.get("text") or ""
        return bool(TOC_HINT.search(text[:800])) or _toc_score(text) >= TOC_LEADER_MIN

    def weak_toc(p):
        if p["page"] == cover_page or p["page"] > TOC_EARLY_MAX_PAGE:
            return False
        text = p.get("text") or ""
        if TOC_HINT.search(text[:800]):
            return True
        if _toc_score(text) >= TOC_CONTINUE_MIN:
            return True
        if len(TOC_PAGE_NUM_LINE.findall(text)) >= 3:
            return True
        return False

    start_i = next((i for i, p in enumerate(ordered) if strong_toc(p)), None)
    if start_i is None:
        # Fallback: first weak early page after cover
        start_i = next((i for i, p in enumerate(ordered) if weak_toc(p)), None)
    if start_i is None:
        return pages

    # Also include consecutive weak pages immediately before the strong hit (e.g. p2 before p3)
    while start_i > 0:
        prev = ordered[start_i - 1]
        if prev["page"] == cover_page:
            break
        if prev["page"] + 1 != ordered[start_i]["page"]:
            break
        if weak_toc(prev):
            start_i -= 1
        else:
            break

    prev_page = None
    for j in range(start_i, len(ordered)):
        p = ordered[j]
        if prev_page is not None and p["page"] != prev_page + 1:
            break
        if j == start_i or weak_toc(p):
            p["is_toc"] = True
            prev_page = p["page"]
        else:
            break
    return pages


def load_pages(path):
    path = Path(path)
    if not path_ok(path):
        raise ValueError(f"path longer than 250 characters: {path}")
    if not path.is_file():
        raise ValueError(f"report text not found: {path}")

    if path.suffix.lower() == ".txt":
        text = path.read_text(encoding="utf-8")
        pages = [{"page": 1, "text": text, "is_toc": False}]
        return mark_toc_pages(pages)

    doc = read_json(path)
    if isinstance(doc, dict) and "claims" in doc and "pages" not in (doc.get("content") or {}):
        pages = doc.get("pages") or [{"page": 1, "text": doc.get("text") or ""}]
        if not pages and doc.get("text"):
            pages = [{"page": 1, "text": doc["text"]}]
        out = []
        for p in pages:
            pg = int(p.get("page") or 1)
            text = p.get("text") or ""
            out.append({"page": pg, "text": text, "is_toc": bool(p.get("is_toc"))})
        # Honour explicit is_toc; still run multi-page TOC detection
        return mark_toc_pages(out)

    content = doc.get("content") or doc
    pages_out = []
    for p in content.get("pages") or []:
        pg = int(p.get("page") or len(pages_out) + 1)
        pages_out.append({"page": pg, "text": page_text(p), "is_toc": False})
    if not pages_out and isinstance(doc.get("text"), str):
        pages_out = [{"page": 1, "text": doc["text"], "is_toc": False}]
    return mark_toc_pages(pages_out)


def _heading_events(pages):
    """R4d: numbered body headings only (skip TOC pages and TOC-entry lines)."""
    last_toc = max((p["page"] for p in pages if p.get("is_toc")), default=0)
    events = []
    for p in pages:
        if p.get("is_toc"):
            continue
        # R4d: body sections only after the last TOC page
        if last_toc and p["page"] <= last_toc:
            continue
        text = p["text"] or ""
        for m in NUMBERED_HEADING.finditer(text):
            num, title = m.group(1), m.group(2).strip()
            # Skip TOC-style lines that leaked onto a non-marked page
            line_start = text.rfind("\n", 0, m.start()) + 1
            line_end = text.find("\n", m.end())
            line = text[line_start: line_end if line_end >= 0 else len(text)]
            if TOC_DOTS_ONLY.search(line) and re.search(r"\d+\s*$", line):
                continue
            tracked = None
            for name, pat in SECTION_TITLE.items():
                if pat.search(title):
                    tracked = name
                    break
            events.append({
                "page": p["page"],
                "section": tracked,
                "title": f"{num} {title}",
            })
        # ICT / similar also closes earthworks (R9)
        if ICT_HINT.search(text):
            events.append({"page": p["page"], "section": None, "title": "ICT"})
    out, seen = [], set()
    for e in sorted(events, key=lambda x: (x["page"], x["section"] or "", x["title"])):
        key = (e["page"], e["section"], e["title"].casefold())
        if key in seen:
            continue
        seen.add(key)
        out.append(e)
    return out


def locate_sections(pages):
    """R4d: first body page (after TOC) with a numbered section heading."""
    events = _heading_events(pages)
    seen = {}
    for e in events:
        if e["section"] and e["section"] not in seen:
            seen[e["section"]] = {"section": e["section"], "page": e["page"]}
    ordered = sorted(seen.values(), key=lambda h: h["page"])
    for h in ordered:
        end = 10**9
        for e in events:
            if e["page"] > h["page"] and (e["section"] is None or e["section"] != h["section"]):
                end = e["page"] - 1
                break
        h["page_end"] = end if end >= h["page"] else h["page"]
    return ordered


def section_for_page(sections, page):
    for s in sections:
        if s["page"] <= page <= s.get("page_end", s["page"]):
            return s["section"]
    return None


def extract_claims(pages, sections):
    claims = []
    for p in pages:
        if p.get("is_toc"):
            continue
        text = p["text"] or ""
        if not text.strip():
            continue
        sec = section_for_page(sections, p["page"])
        for family, pat in CLAIM_PATTERNS.items():
            for m in pat.finditer(text):
                if family == "balanced":
                    # R9: require earthworks section AND local earthworks context (drop ICT p152)
                    window = text[max(0, m.start() - 100): m.end() + 100]
                    if sec != "earthworks":
                        continue
                    if not EARTHWORKS_CONTEXT.search(window):
                        continue
                    if ICT_HINT.search(window) and not EARTHWORKS_CONTEXT.search(
                            text[max(0, m.start() - 40): m.end() + 40]):
                        continue
                rec = {
                    "family": family,
                    "page": p["page"],
                    "section": sec,
                    "extract": m.group(0)[:240].replace("\n", " "),
                    "span": [m.start(), m.end()],
                }
                if family == "pavement_depth":
                    mm = m.group(1) or m.group(2)
                    if mm is not None:
                        rec["value_mm"] = float(mm)
                        rec["value_m"] = float(mm) / 1000.0
                    window = text[max(0, m.start() - 80): m.end() + 80]
                    # R10: drop stormwater freeboard; tag pavement+subgrade preference
                    if STORMWATER_FREEBOARD.search(window) and not (
                            PAVEMENT_DEPTH_CONTEXT.search(window)
                            and SUBGRADE_CONTEXT.search(window)):
                        continue
                    rec["pavement_subgrade"] = bool(
                        PAVEMENT_DEPTH_CONTEXT.search(window)
                        and SUBGRADE_CONTEXT.search(window))
                if family == "balanced":
                    rec["in_earthworks_section"] = True
                claims.append(rec)
    return claims


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--text", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)

    def run():
        pages = load_pages(a.text)
        if not pages:
            return fail(EXIT_INPUT, f"no pages/text in {a.text}")
        sections = locate_sections(pages)
        claims = extract_claims(pages, sections)
        families = sorted({c["family"] for c in claims})
        out = {
            "report_claims_version": "1.0",
            "source": {"path": str(a.text)},
            "sections": sections,
            "claims": claims,
            "pages_read": len(pages),
            "toc_pages": [p["page"] for p in pages if p.get("is_toc")],
            "families_present": families,
            "document_no": None,
            "tool": {"script": "read_report.py", "version": __version__},
            "claims_ref": str(CLAIMS_MD.relative_to(SKILL_ROOT)) if CLAIMS_MD.is_file() else None,
        }
        try:
            raw = read_json(a.text)
            tb = (raw.get("content") or raw).get("title_block") or raw.get("title_block") or {}
            out["document_no"] = tb.get("document_no") or raw.get("document_no")
        except Exception:
            pass
        atomic_write_json(a.out, out)
        emit({
            "status": "ok",
            "out": a.out,
            "pages": len(pages),
            "toc_pages": out["toc_pages"],
            "sections": [s["section"] for s in sections],
            "section_pages": {s["section"]: s["page"] for s in sections},
            "claims": len(claims),
            "families": out["families_present"],
        })
        return EXIT_OK

    return main_guard(run)


if __name__ == "__main__":
    sys.exit(main())
