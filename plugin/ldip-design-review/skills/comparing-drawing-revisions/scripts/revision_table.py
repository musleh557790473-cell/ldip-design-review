"""Read the newest revision code from the PDF title-block **revision table** (S31 R3 re-review).

Crop to the title-block strip → pdfplumber ``extract_words`` (target ≤ 2 s/PDF). Find header
words Revision / Description / Date. Take a standalone revision token from the Revision column
in data rows (newest by date, else nearest the header). Reject tokens followed by ``%``, and
never take codes from ``SHEET-nn`` or ``nn OF nn``.

Returns ``(code, "revision_table")`` or ``(None, "not_read")``.

No network, no shell calls.
"""
__version__ = "0.1.1"

import re
from datetime import datetime
from pathlib import Path

REV_TOKEN = re.compile(r"^(0[A-Z]|[0-9]{2}|A[0-9])$")
DATE_DMY = re.compile(r"^(\d{1,2})[./\-](\d{1,2})[./\-](\d{2,4})$")
DATE_MON = re.compile(
    r"^(\d{1,2})?\s*(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|SEPT|OCT|NOV|DEC)[A-Z]*\.?\s*(\d{4})$",
    re.I,
)
HEADER_REV = re.compile(r"^rev(?:ision)?\.?:?$", re.I)
HEADER_DESC = re.compile(r"^desc(?:ription)?\.?:?$", re.I)
HEADER_DATE = re.compile(r"^date\.?:?$", re.I)
SHEET_TOKEN = re.compile(r"^sheet[- ]?\d+$", re.I)


def _parse_date_token(text):
    t = (text or "").strip()
    m = DATE_DMY.match(t)
    if m:
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if y < 100:
            y += 2000
        try:
            return datetime(y, mo, d)
        except ValueError:
            return None
    m = DATE_MON.match(t)
    if m:
        key = m.group(2).upper()
        months = {"JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
                  "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12}
        mo = 9 if key.startswith("SEPT") else months.get(key[:3])
        y = int(m.group(3))
        d = int(m.group(1)) if m.group(1) else 1
        if mo:
            try:
                return datetime(y, mo, d)
            except ValueError:
                return datetime(y, mo, 1)
    # "SEPT. 2026" / "SEPT.2026" as single or split handled by joining below
    return None


def _parse_date_from_row_words(row_words):
    """Try single tokens, then adjacent pairs (e.g. SEPT. + 2026)."""
    texts = [w["text"] for w in row_words]
    for t in texts:
        dt = _parse_date_token(t)
        if dt:
            return dt
    for i in range(len(texts) - 1):
        dt = _parse_date_token(f"{texts[i]} {texts[i + 1]}")
        if dt:
            return dt
        dt = _parse_date_token(f"{texts[i]}{texts[i + 1]}")
        if dt:
            return dt
    return None


def _same_line(a, b, tol=4.0):
    return abs(float(a["top"]) - float(b["top"])) <= tol


def _find_headers(words):
    """Return (rev_hdr, desc_hdr, date_hdr) word dicts on one line, or None.

    Prefer Revision + Description + Date on one header row. Fall back to Revision+Date
    or Revision+Description (nearest-header selection when no Date column).
    """
    revs = [w for w in words if HEADER_REV.match(w["text"].strip())]
    descs = [w for w in words if HEADER_DESC.match(w["text"].strip())]
    dates = [w for w in words if HEADER_DATE.match(w["text"].strip())]
    for rv in revs:
        ds = [d for d in descs if _same_line(rv, d) and float(d["x0"]) > float(rv["x0"])]
        dts = [d for d in dates if _same_line(rv, d) and float(d["x0"]) > float(rv["x0"])]
        if ds and dts:
            desc = min(ds, key=lambda w: float(w["x0"]))
            date = min(dts, key=lambda w: float(w["x0"]))
            return rv, desc, date
        # Revision + Date only (Description optional)
        if dts:
            date = min(dts, key=lambda w: float(w["x0"]))
            return rv, None, date
        # Revision + Description only — newest by date unavailable; use nearest row
        if ds:
            desc = min(ds, key=lambda w: float(w["x0"]))
            return rv, desc, None
    return None


def _valid_rev_code(word, words_by_x, idx_in_line):
    """Standalone rev token; not followed by %; not SHEET-nn / nn OF nn."""
    raw = word["text"].strip().strip("·.•,;")
    if SHEET_TOKEN.match(raw):
        return None
    if not REV_TOKEN.match(raw.upper()):
        return None
    # Previous token SHEET
    if idx_in_line > 0:
        prev = words_by_x[idx_in_line - 1]["text"].strip().upper().rstrip(".")
        if prev in ("SHEET", "SH"):
            return None
    # Next token % or OF
    if idx_in_line + 1 < len(words_by_x):
        nxt = words_by_x[idx_in_line + 1]["text"].strip()
        if nxt.startswith("%") or nxt.upper() == "OF":
            return None
        # "30%" as one token already fails REV_TOKEN; "30" + "%" caught above
    return raw.upper()


def revision_from_words(words):
    """Parse newest revision from a list of pdfplumber word dicts (cropped strip)."""
    if not words:
        return None, "not_read"
    headers = _find_headers(words)
    if not headers:
        return None, "not_read"
    rev_h, desc_h, date_h = headers
    header_bottom = max(
        float(rev_h["bottom"]),
        float(desc_h["bottom"]) if desc_h else 0.0,
        float(date_h["bottom"]) if date_h else 0.0,
    )
    # Revision column x-range: from rev header to desc (or date) header
    x_left = float(rev_h["x0"]) - 2.0
    if desc_h is not None:
        x_right = float(desc_h["x0"]) - 1.0
    elif date_h is not None:
        x_right = float(date_h["x0"]) - 1.0
    else:
        x_right = float(rev_h["x1"]) + 40.0
    if x_right <= x_left:
        x_right = float(rev_h["x1"]) + 40.0

    # Group words into rows below the header by top coordinate
    data_words = [w for w in words if float(w["top"]) >= header_bottom - 1.0]
    if not data_words:
        return None, "not_read"
    data_words = sorted(data_words, key=lambda w: (float(w["top"]), float(w["x0"])))
    rows = []
    cur = [data_words[0]]
    for w in data_words[1:]:
        if _same_line(cur[0], w, tol=5.0):
            cur.append(w)
        else:
            rows.append(cur)
            cur = [w]
    rows.append(cur)

    candidates = []
    for row in rows:
        # Skip the header row itself
        if any(HEADER_REV.match(w["text"].strip()) for w in row):
            continue
        row_x = sorted(row, key=lambda w: float(w["x0"]))
        code = None
        for i, w in enumerate(row_x):
            cx = (float(w["x0"]) + float(w["x1"])) / 2.0
            if not (x_left <= cx <= x_right):
                continue
            code = _valid_rev_code(w, row_x, i)
            if code:
                break
        if not code:
            continue
        dt = _parse_date_from_row_words(row_x)
        # Distance from header (nearer = smaller positive top delta)
        dist = abs(float(row_x[0]["top"]) - float(rev_h["top"]))
        candidates.append((code, dt, dist))

    if not candidates:
        return None, "not_read"
    dated = [c for c in candidates if c[1] is not None]
    if dated:
        dated.sort(key=lambda x: x[1], reverse=True)
        return dated[0][0], "revision_table"
    # Else row nearest the header
    candidates.sort(key=lambda x: x[2])
    return candidates[0][0], "revision_table"


def _crop_title_block(page, right_frac=0.32):
    w, h = float(page.width), float(page.height)
    return page.crop((w * (1.0 - right_frac), 0, w, h))


def revision_from_pdf_text(pdf_path, page_index=0):
    """Return (revision_code, 'revision_table') or (None, 'not_read').

    Crops the right title-block strip and uses extract_words (R3 performance).
    """
    try:
        import pdfplumber
    except ImportError as exc:
        raise RuntimeError("pdfplumber is not installed") from exc
    path = Path(pdf_path)
    if not path.is_file():
        return None, "not_read"
    with pdfplumber.open(str(path)) as pdf:
        if page_index < 0 or page_index >= len(pdf.pages):
            return None, "not_read"
        page = pdf.pages[page_index]
        crop = _crop_title_block(page)
        words = crop.extract_words() or []
        code, src = revision_from_words(words)
        if code:
            return code, src
        # Fallback: slightly wider strip if headers were clipped
        crop2 = _crop_title_block(page, right_frac=0.45)
        words2 = crop2.extract_words() or []
        return revision_from_words(words2)


# Kept for unit tests that feed synthetic word lists / simple text fixtures
def revision_from_text(text):
    """Best-effort parse from plain text (tests only). Prefer revision_from_words / PDF path."""
    if not text:
        return None, "not_read"
    # Build pseudo-words in reading order for the table-shaped fixture
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    words = []
    y = 0.0
    for ln in lines:
        x = 0.0
        for tok in ln.split():
            words.append({
                "text": tok, "x0": x, "x1": x + len(tok) * 5,
                "top": y, "bottom": y + 8,
            })
            x += len(tok) * 5 + 8
        y += 12
    return revision_from_words(words)
