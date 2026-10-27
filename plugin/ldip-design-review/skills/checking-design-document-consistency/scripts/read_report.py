#!/usr/bin/env python3
"""Locate earthworks / pavement / geotech claims in skill-2 PDF text (skill 16; DN-2).

R4: skip TOC pages; attach claims to section ranges; basis_ratio needs rock/suitable/disposal context.
R5: rebuild page text in bbox reading order (top→bottom, left→right).
R9: `balanced` is only meaningful inside the earthworks section (tagged on the claim).

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

SECTION_HEADINGS = {
    "earthworks": re.compile(
        r"(?:^\s*|\b)(?:\d+(?:\.\d+)*\s+)?(?:earthworks?|cut\s*(?:and|&|/)\s*fill|grading|mass\s+haul)\b",
        re.I),
    "pavement": re.compile(
        r"(?:^\s*|\b)(?:\d+(?:\.\d+)*\s+)?(?:pavement|road\s+construction|flexible\s+pavement|wearing\s+course)\b",
        re.I),
    "geotechnical": re.compile(
        r"(?:^\s*|\b)(?:\d+(?:\.\d+)*\s+)?(?:geotech(?:nical)?|ground\s+condition|foundation)\b",
        re.I),
}

TOC_HINT = re.compile(r"\b(contents|table\s+of\s+contents|\btoc\b)\b", re.I)
TOC_DOT_LEADERS = re.compile(r"\.{3,}\s*\d+\s*$", re.M)
# Numbered major heading (closes prior section when not one of the three tracked sections)
OTHER_HEADING = re.compile(
    r"(?:^|\n)\s*(\d+(?:\.\d+)*)\s+([A-Z][A-Za-z][\w\s/-]{1,60})",
)

CLAIM_PATTERNS = {
    "balanced": re.compile(
        r"\bbalanced\b|cut\s*(?:and|&)\s*fill\s+balanc\w*|earthworks?\s+balanc\w*", re.I),
    "import": re.compile(r"\bimport(?:ed)?\s+(?:fill|material)\b|\bborrow\b", re.I),
    "export": re.compile(r"\bexport(?:ed)?\b|\bdispos(?:al|ed)\s+off[- ]?site\b", re.I),
    # R4: basis_ratio requires rock / suitable / disposal context near the ratio tokens
    "basis_ratio": re.compile(
        r"(?:rock|suitable|disposal|spoil).{0,60}(?:0\.[268]|[268]0\s*%)"
        r"|(?:0\.[268]|[268]0\s*%).{0,60}(?:rock|suitable|disposal|spoil)"
        r"|(?:rock|suitable|disposal).{0,80}(?:0\.2|20\s*%).{0,40}(?:0\.8|80\s*%)",
        re.I | re.S),
    "pavement_depth": re.compile(
        r"(?:pavement|wearing|thickness|ToS|top\s+of\s+subgrade).{0,40}?(\d+(?:\.\d+)?)\s*mm"
        r"|(\d+(?:\.\d+)?)\s*mm.{0,40}?(?:pavement|wearing|thickness|ToS)",
        re.I | re.S),
}


def _bbox_key(span):
    """R5: top then left; missing bbox → end."""
    bb = span.get("bbox") or span.get("bbox_pt")
    if not bb or len(bb) < 2:
        return (1e9, 1e9)
    return (round(float(bb[1]), 1), round(float(bb[0]), 1))


def page_text(page):
    """R5: join spans in bbox reading order, then OCR spans similarly."""
    parts = []
    spans = list(page.get("spans") or [])
    spans.sort(key=_bbox_key)
    for sp in spans:
        t = sp.get("text")
        if t:
            parts.append(str(t))
    ocr = list(page.get("ocr_spans") or [])
    ocr.sort(key=_bbox_key)
    for sp in ocr:
        t = sp.get("text")
        if t:
            parts.append(str(t))
    return " ".join(parts)


def is_toc_page(text, page_no):
    """R4: TOC pages — 'contents' heading or many dotted leaders / section-title+page lines."""
    if not text:
        return False
    if TOC_HINT.search(text[:400]) and page_no <= 5:
        return True
    leaders = TOC_DOT_LEADERS.findall(text)
    if len(leaders) >= 5:
        return True
    # Early pages that list every section heading once with page numbers
    hits = sum(1 for pat in SECTION_HEADINGS.values() if pat.search(text))
    if page_no <= 4 and hits >= 2 and re.search(r"\b\d{1,3}\s*$", text, re.M):
        # Likely TOC if short lines dominate
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()] or text.split(".")
        if len(lines) >= 6 and sum(1 for ln in lines if len(ln) < 80) >= max(4, len(lines) // 2):
            return True
    return False


def load_pages(path):
    """Return list of {page, text, is_toc} from skill-2 JSON, claims JSON, or .txt."""
    path = Path(path)
    if not path_ok(path):
        raise ValueError(f"path longer than 250 characters: {path}")
    if not path.is_file():
        raise ValueError(f"report text not found: {path}")

    if path.suffix.lower() == ".txt":
        text = path.read_text(encoding="utf-8")
        return [{"page": 1, "text": text, "is_toc": is_toc_page(text, 1)}]

    doc = read_json(path)
    if isinstance(doc, dict) and "claims" in doc and "pages" not in (doc.get("content") or {}):
        pages = doc.get("pages") or [{"page": 1, "text": doc.get("text") or ""}]
        if not pages and doc.get("text"):
            pages = [{"page": 1, "text": doc["text"]}]
        out = []
        for p in pages:
            pg = int(p.get("page") or 1)
            text = p.get("text") or ""
            out.append({"page": pg, "text": text, "is_toc": bool(p.get("is_toc")) or is_toc_page(text, pg)})
        return out

    content = doc.get("content") or doc
    pages_out = []
    for p in content.get("pages") or []:
        pg = int(p.get("page") or len(pages_out) + 1)
        text = page_text(p)
        pages_out.append({"page": pg, "text": text, "is_toc": is_toc_page(text, pg)})
    if not pages_out and isinstance(doc.get("text"), str):
        pages_out = [{"page": 1, "text": doc["text"], "is_toc": is_toc_page(doc["text"], 1)}]
    return pages_out


def _heading_events(pages):
    """Yield (page, section_name|None, title) for tracked + other numbered headings."""
    events = []
    for p in pages:
        if p.get("is_toc"):
            continue
        text = p["text"] or ""
        # Tracked sections
        for name, pat in SECTION_HEADINGS.items():
            if pat.search(text):
                events.append({"page": p["page"], "section": name, "title": name})
        # Other numbered headings close ranges (e.g. "5 Drainage")
        for m in OTHER_HEADING.finditer(text):
            title = m.group(2).strip()
            low = title.casefold()
            tracked = None
            for name, pat in SECTION_HEADINGS.items():
                if pat.search(title) or pat.search(low):
                    tracked = name
                    break
            if tracked is None:
                events.append({"page": p["page"], "section": None, "title": title})
    # de-dupe same page+section
    out, seen = [], set()
    for e in sorted(events, key=lambda x: (x["page"], x["section"] or "")):
        key = (e["page"], e["section"], e["title"].casefold())
        if key in seen:
            continue
        seen.add(key)
        out.append(e)
    return out


def locate_sections(pages):
    """R4: first non-TOC hit per tracked section; page_end at next heading (tracked or other)."""
    events = _heading_events(pages)
    # First occurrence of each tracked section
    seen = {}
    for e in events:
        if e["section"] and e["section"] not in seen:
            seen[e["section"]] = {"section": e["section"], "page": e["page"]}
    ordered = sorted(seen.values(), key=lambda h: h["page"])
    for h in ordered:
        # End at the next heading event after this section starts (any heading)
        end = 10**9
        for e in events:
            if e["page"] > h["page"]:
                # Don't let another mention of the same section close itself
                if e["section"] == h["section"]:
                    continue
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
    """Extract claims from non-TOC pages; tag section (R4 / R9)."""
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
                rec = {
                    "family": family,
                    "page": p["page"],
                    "section": sec,
                    "extract": m.group(0)[:240],
                    "span": [m.start(), m.end()],
                }
                if family == "pavement_depth":
                    mm = m.group(1) or m.group(2)
                    if mm is not None:
                        rec["value_mm"] = float(mm)
                        rec["value_m"] = float(mm) / 1000.0
                # R9: balanced outside earthworks is recorded but marked in_section=False
                if family == "balanced":
                    rec["in_earthworks_section"] = sec == "earthworks"
                claims.append(rec)
    return claims


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--text", required=True, help="skill-2 .pdf.json, claims JSON, or .txt")
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)

    def run():
        pages = load_pages(a.text)
        if not pages:
            return fail(EXIT_INPUT, f"no pages/text in {a.text}")
        sections = locate_sections(pages)
        claims = extract_claims(pages, sections)
        # families_present for C3: R9 — balanced only counts when in earthworks section
        families = set()
        for c in claims:
            if c["family"] == "balanced" and not c.get("in_earthworks_section"):
                continue
            families.add(c["family"])
        out = {
            "report_claims_version": "1.0",
            "source": {"path": str(a.text)},
            "sections": sections,
            "claims": claims,
            "pages_read": len(pages),
            "toc_pages": [p["page"] for p in pages if p.get("is_toc")],
            "families_present": sorted(families),
            "document_no": None,
            "tool": {"script": "read_report.py", "version": __version__},
            "claims_ref": str(CLAIMS_MD.relative_to(SKILL_ROOT)) if CLAIMS_MD.is_file() else None,
        }
        # Optional document_no from skill-2 title block
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
            "claims": len(claims),
            "families": out["families_present"],
        })
        return EXIT_OK

    return main_guard(run)


if __name__ == "__main__":
    sys.exit(main())
