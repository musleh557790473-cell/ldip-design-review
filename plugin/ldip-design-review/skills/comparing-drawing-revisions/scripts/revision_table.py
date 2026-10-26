"""Read the newest revision code from a PDF title-block revision table (S31 R3).

Uses pdfplumber text (skill-2 second reader). Typical rows:
  "00 · 30% CONCEPT DESIGN · SEPT. 2026"
  "01 Issued for Approval 08-10-2026"
  "00 Code-C 17-08-2026"

When several codes appear, prefer the one with the latest parseable date; otherwise the first
code in reading order (Saudi title blocks list the newest row first).

No network, no shell calls.
"""
__version__ = "0.1.0"

import re
from datetime import datetime
from pathlib import Path

REV_CODE = re.compile(r"\b(0[A-Z]|[0-9]{2}|A[0-9])\b")
DATE_DMY = re.compile(r"\b(\d{1,2})[./\-](\d{1,2})[./\-](\d{2,4})\b")
DATE_MON = re.compile(
    r"\b(\d{1,2}\s+)?(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|SEPT|OCT|NOV|DEC)[A-Z]*\.?\s+(\d{4})\b",
    re.I,
)
# Row-like: code near stage / issue wording
ROWISH = re.compile(
    r"\b(0[A-Z]|[0-9]{2}|A[0-9])\b.{0,80}?\b(concept|issued|approval|code-[a-z]|preliminary|detail)",
    re.I,
)


def _parse_date(text):
    m = DATE_DMY.search(text or "")
    if m:
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if y < 100:
            y += 2000
        try:
            return datetime(y, mo, d)
        except ValueError:
            pass
    m = DATE_MON.search(text or "")
    if m:
        months = {"JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
                  "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12}
        key = m.group(2).upper()
        mo = 9 if key.startswith("SEPT") else months.get(key[:3])
        y = int(m.group(3))
        d = int(m.group(1)) if m.group(1) else 1
        if mo:
            try:
                return datetime(y, mo, d)
            except ValueError:
                return datetime(y, mo, 1)
    return None


def revision_from_pdf_text(pdf_path, page_index=0):
    """Return (revision_code, source='revision_table') or (None, None)."""
    try:
        import pdfplumber
    except ImportError as exc:
        raise RuntimeError("pdfplumber is not installed") from exc
    path = Path(pdf_path)
    if not path.is_file():
        return None, None
    with pdfplumber.open(str(path)) as pdf:
        if page_index < 0 or page_index >= len(pdf.pages):
            return None, None
        text = pdf.pages[page_index].extract_text() or ""
    return revision_from_text(text)


def revision_from_text(text):
    """Parse newest revision code from extracted page text."""
    if not text:
        return None, None
    candidates = []
    # Prefer row-like matches
    for m in ROWISH.finditer(text):
        code = m.group(1).upper()
        if code in ("OF",):  # guard
            continue
        span = text[max(0, m.start() - 20): m.end() + 40]
        candidates.append((code, _parse_date(span), m.start()))
    if not candidates:
        for m in REV_CODE.finditer(text):
            code = m.group(1).upper()
            # Skip lone "OF" sheet counts like "01 OF 05" — require nearby revision cues
            window = text[max(0, m.start() - 30): m.end() + 50]
            if re.search(r"\bOF\b", window) and not re.search(
                    r"revision|issued|concept|approval|code-", window, re.I):
                continue
            candidates.append((code, _parse_date(window), m.start()))
    if not candidates:
        return None, None
    dated = [c for c in candidates if c[1] is not None]
    if dated:
        dated.sort(key=lambda x: x[1], reverse=True)
        return dated[0][0], "revision_table"
    # Newest-first table: first match in reading order
    candidates.sort(key=lambda x: x[2])
    return candidates[0][0], "revision_table"
