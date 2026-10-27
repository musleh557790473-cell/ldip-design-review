#!/usr/bin/env python3
"""Locate earthworks / pavement / geotech claims in skill-2 PDF text (skill 16; DN-2).

Reads a skill-2 ingestion JSON (`content.pages[].spans[]`) or a plain-text / JSON
claims fixture. Heading locator + claim families from `references/claims.md`.

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
    "earthworks": re.compile(r"earthworks?|cut\s*(?:and|&|/)\s*fill|grading|mass\s+haul", re.I),
    "pavement": re.compile(r"pavement|road\s+construction|flexible\s+pavement|wearing\s+course", re.I),
    "geotechnical": re.compile(r"geotech|ground\s+condition|foundation|\bsoil\b", re.I),
}

CLAIM_PATTERNS = {
    "balanced": re.compile(
        r"\bbalanced\b|cut\s*(?:and|&)\s*fill\s+balanc\w*|earthworks?\s+balanc\w*", re.I),
    "import": re.compile(r"\bimport(?:ed)?\s+(?:fill|material)\b|\bborrow\b", re.I),
    "export": re.compile(r"\bexport(?:ed)?\b|\bdispos(?:al|ed)\s+off[- ]?site\b", re.I),
    "basis_ratio": re.compile(
        r"(?:0\.2|20\s*%).{0,40}(?:0\.8|80\s*%)|(?:rock|suitable|disposal).{0,30}(?:0\.[268]|[268]0\s*%)",
        re.I | re.S),
    "pavement_depth": re.compile(
        r"(?:pavement|wearing|thickness|ToS|top\s+of\s+subgrade).{0,40}?(\d+(?:\.\d+)?)\s*mm"
        r"|(\d+(?:\.\d+)?)\s*mm.{0,40}?(?:pavement|wearing|thickness|ToS)",
        re.I | re.S),
}


def page_text(page):
    parts = []
    for sp in page.get("spans") or []:
        t = sp.get("text")
        if t:
            parts.append(str(t))
    for sp in page.get("ocr_spans") or []:
        t = sp.get("text")
        if t:
            parts.append(str(t))
    return " ".join(parts)


def load_pages(path):
    """Return list of {page, text} from skill-2 JSON, claims JSON, or .txt."""
    path = Path(path)
    if not path_ok(path):
        raise ValueError(f"path longer than 250 characters: {path}")
    if not path.is_file():
        raise ValueError(f"report text not found: {path}")

    if path.suffix.lower() == ".txt":
        return [{"page": 1, "text": path.read_text(encoding="utf-8")}]

    doc = read_json(path)
    # Already-normalised claims fixture
    if isinstance(doc, dict) and "claims" in doc and "pages" not in (doc.get("content") or {}):
        pages = doc.get("pages") or [{"page": 1, "text": doc.get("text") or ""}]
        if not pages and doc.get("text"):
            pages = [{"page": 1, "text": doc["text"]}]
        return [{"page": int(p.get("page") or 1), "text": p.get("text") or ""} for p in pages]

    content = doc.get("content") or doc
    pages_out = []
    for p in content.get("pages") or []:
        pages_out.append({"page": int(p.get("page") or len(pages_out) + 1), "text": page_text(p)})
    if not pages_out and isinstance(doc.get("text"), str):
        pages_out = [{"page": 1, "text": doc["text"]}]
    return pages_out


def locate_sections(pages):
    hits = []
    for p in pages:
        for name, pat in SECTION_HEADINGS.items():
            if pat.search(p["text"] or ""):
                hits.append({"section": name, "page": p["page"]})
    # unique by section (first page)
    seen = {}
    for h in hits:
        seen.setdefault(h["section"], h)
    return list(seen.values())


def extract_claims(pages):
    claims = []
    for p in pages:
        text = p["text"] or ""
        if not text.strip():
            continue
        for family, pat in CLAIM_PATTERNS.items():
            for m in pat.finditer(text):
                rec = {
                    "family": family,
                    "page": p["page"],
                    "extract": m.group(0)[:240],
                    "span": [m.start(), m.end()],
                }
                if family == "pavement_depth":
                    mm = m.group(1) or m.group(2)
                    if mm is not None:
                        rec["value_mm"] = float(mm)
                        rec["value_m"] = float(mm) / 1000.0
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
        claims = extract_claims(pages)
        out = {
            "report_claims_version": "1.0",
            "source": {"path": str(a.text)},
            "sections": sections,
            "claims": claims,
            "pages_read": len(pages),
            "families_present": sorted({c["family"] for c in claims}),
            "tool": {"script": "read_report.py", "version": __version__},
            "claims_ref": str(CLAIMS_MD.relative_to(SKILL_ROOT)) if CLAIMS_MD.is_file() else None,
        }
        atomic_write_json(a.out, out)
        emit({
            "status": "ok",
            "out": a.out,
            "pages": len(pages),
            "sections": [s["section"] for s in sections],
            "claims": len(claims),
            "families": out["families_present"],
        })
        return EXIT_OK

    return main_guard(run)


if __name__ == "__main__":
    sys.exit(main())
