"""Read the newest revision code from the PDF title-block **revision table** (S31 R3).

Title-block strip via **pypdfium2** ``get_text_bounded`` / char boxes (not pdfplumber crop).
Find header words Revision / Description / Date. Search data rows **above** the header first,
then below. In the Revision column, join adjacent chars/words on one line (gap ≤ 1.5 × char
width) before matching a code. Newest = latest parseable date; else farthest from header when
rows are above (top row), or nearest when below. Reject tokens followed by ``%``; never take
``SHEET-nn`` / ``nn OF nn``.

Returns ``(code, "revision_table")`` or ``(None, "not_read")``.

No network, no shell calls.
"""
__version__ = "0.1.2"

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
JOIN_GAP_FACTOR = 1.5


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
    return None


def _parse_date_from_row_words(row_words):
    """Try single tokens, then adjacent joins (e.g. SEPT. + 2026)."""
    texts = [w["text"] for w in row_words]
    for t in texts:
        dt = _parse_date_token(t)
        if dt:
            return dt
    for i in range(len(texts) - 1):
        for cand in (f"{texts[i]} {texts[i + 1]}", f"{texts[i]}{texts[i + 1]}"):
            dt = _parse_date_token(cand)
            if dt:
                return dt
    # Triple for "SEPT" "." "2026"
    for i in range(len(texts) - 2):
        dt = _parse_date_token(f"{texts[i]}{texts[i + 1]} {texts[i + 2]}")
        if dt:
            return dt
        dt = _parse_date_token(f"{texts[i]} {texts[i + 1]} {texts[i + 2]}")
        if dt:
            return dt
    return None


def _same_line(a, b, tol=4.0):
    return abs(float(a["top"]) - float(b["top"])) <= tol


def _char_width(w):
    width = float(w["x1"]) - float(w["x0"])
    n = max(1, len((w.get("text") or "").strip()) or 1)
    return max(width / n, 0.5)


def _join_adjacent(words, gap_factor=JOIN_GAP_FACTOR):
    """Join left-to-right words on one line when gap ≤ gap_factor × char width."""
    if not words:
        return []
    ordered = sorted(words, key=lambda w: float(w["x0"]))
    out = []
    cur = dict(ordered[0])
    cur["text"] = cur.get("text") or ""
    for w in ordered[1:]:
        gap = float(w["x0"]) - float(cur["x1"])
        cw = max(_char_width(cur), _char_width(w))
        if gap <= gap_factor * cw and _same_line(cur, w, tol=5.0):
            cur["text"] = (cur["text"] or "") + (w.get("text") or "")
            cur["x1"] = max(float(cur["x1"]), float(w["x1"]))
            cur["top"] = min(float(cur["top"]), float(w["top"]))
            cur["bottom"] = max(float(cur["bottom"]), float(w["bottom"]))
        else:
            out.append(cur)
            cur = dict(w)
            cur["text"] = cur.get("text") or ""
    out.append(cur)
    return out


def _find_headers(words):
    """Return (rev_hdr, desc_hdr, date_hdr) on one line, or None."""
    revs = [w for w in words if HEADER_REV.match(w["text"].strip())]
    descs = [w for w in words if HEADER_DESC.match(w["text"].strip())]
    dates = [w for w in words if HEADER_DATE.match(w["text"].strip())]
    for rv in revs:
        ds = [d for d in descs if _same_line(rv, d) and float(d["x0"]) > float(rv["x0"])]
        dts = [d for d in dates if _same_line(rv, d) and float(d["x0"]) > float(rv["x0"])]
        if ds and dts:
            return rv, min(ds, key=lambda w: float(w["x0"])), min(dts, key=lambda w: float(w["x0"]))
        if dts:
            return rv, None, min(dts, key=lambda w: float(w["x0"]))
        if ds:
            return rv, min(ds, key=lambda w: float(w["x0"])), None
    return None


def _valid_joined_code(token, next_token=None):
    """Accept a joined revision token; reject % / SHEET-nn / nn OF nn."""
    raw = (token or "").strip().strip("·.•,;")
    if not raw or SHEET_TOKEN.match(raw):
        return None
    if raw.endswith("%") or "%" in raw:
        return None
    if not REV_TOKEN.match(raw.upper()):
        return None
    if next_token is not None:
        nxt = next_token.strip()
        if nxt.startswith("%") or nxt.upper() == "OF":
            return None
    return raw.upper()


def _group_rows(words, line_tol=5.0):
    if not words:
        return []
    ordered = sorted(words, key=lambda w: (float(w["top"]), float(w["x0"])))
    rows = []
    cur = [ordered[0]]
    for w in ordered[1:]:
        if _same_line(cur[0], w, tol=line_tol):
            cur.append(w)
        else:
            rows.append(cur)
            cur = [w]
    rows.append(cur)
    return rows


def _rev_column_bounds(rev_h, desc_h, date_h):
    x_left = float(rev_h["x0"]) - 2.0
    if desc_h is not None:
        x_right = float(desc_h["x0"]) - 1.0
    elif date_h is not None:
        x_right = float(date_h["x0"]) - 1.0
    else:
        x_right = float(rev_h["x1"]) + 40.0
    if x_right <= x_left:
        x_right = float(rev_h["x1"]) + 40.0
    return x_left, x_right


def _code_from_row(row, x_left, x_right):
    """Join Revision-column fragments, then match a rev code (with % / OF rejection)."""
    in_col = []
    for w in row:
        cx = (float(w["x0"]) + float(w["x1"])) / 2.0
        if x_left <= cx <= x_right:
            in_col.append(w)
    if not in_col:
        return None
    joined = _join_adjacent(in_col)
    # Also consider full-row join order for next-token % / OF checks
    row_joined = _join_adjacent(row)
    for i, tok in enumerate(joined):
        # Find this token among row_joined for neighbour checks
        nxt = None
        for j, rj in enumerate(row_joined):
            if abs(float(rj["x0"]) - float(tok["x0"])) < 0.5 and rj["text"] == tok["text"]:
                if j + 1 < len(row_joined):
                    nxt = row_joined[j + 1]["text"]
                break
        # Previous SHEET on the full row
        prev_sheet = False
        for j, rj in enumerate(row_joined):
            if abs(float(rj["x0"]) - float(tok["x0"])) < 0.5 and rj["text"] == tok["text"]:
                if j > 0 and row_joined[j - 1]["text"].strip().upper().rstrip(".") in ("SHEET", "SH"):
                    prev_sheet = True
                break
        if prev_sheet:
            continue
        code = _valid_joined_code(tok["text"], nxt)
        if code:
            return code
    return None


def _candidates_from_rows(rows, rev_h, x_left, x_right, above):
    """Build (code, date, dist, above) candidates; skip the header row itself."""
    header_top = float(rev_h["top"])
    out = []
    for row in rows:
        if any(HEADER_REV.match(w["text"].strip()) for w in row):
            continue
        code = _code_from_row(row, x_left, x_right)
        if not code:
            continue
        row_x = sorted(row, key=lambda w: float(w["x0"]))
        dt = _parse_date_from_row_words(row_x)
        dist = abs(float(row_x[0]["top"]) - header_top)
        out.append((code, dt, dist, above))
    return out


def _pick_newest(candidates):
    """Newest = latest date; else farthest above header, or nearest below."""
    if not candidates:
        return None, "not_read"
    dated = [c for c in candidates if c[1] is not None]
    if dated:
        dated.sort(key=lambda x: x[1], reverse=True)
        return dated[0][0], "revision_table"
    above = [c for c in candidates if c[3]]
    if above:
        # Farthest from header = top row (largest distance above)
        above.sort(key=lambda x: x[2], reverse=True)
        return above[0][0], "revision_table"
    below = [c for c in candidates if not c[3]]
    below.sort(key=lambda x: x[2])
    return below[0][0], "revision_table"


def revision_from_words(words):
    """Parse newest revision from word dicts (``top`` = distance from page top).

    Words may be single characters; Revision-column fragments are joined before matching.
    """
    if not words:
        return None, "not_read"
    # Pre-join loosely so "Revision" split across chars still forms headers
    words = _join_adjacent(list(words), gap_factor=JOIN_GAP_FACTOR)
    headers = _find_headers(words)
    if not headers:
        return None, "not_read"
    rev_h, desc_h, date_h = headers
    header_top = float(rev_h["top"])
    header_bottom = max(
        float(rev_h["bottom"]),
        float(desc_h["bottom"]) if desc_h else 0.0,
        float(date_h["bottom"]) if date_h else 0.0,
    )
    x_left, x_right = _rev_column_bounds(rev_h, desc_h, date_h)

    # Above header first (smaller top = higher on page), then below
    above_words = [w for w in words if float(w["bottom"]) <= header_top + 1.0
                   and not (HEADER_REV.match(w["text"].strip())
                            or HEADER_DESC.match(w["text"].strip())
                            or HEADER_DATE.match(w["text"].strip()))]
    below_words = [w for w in words if float(w["top"]) >= header_bottom - 1.0
                   and not (HEADER_REV.match(w["text"].strip())
                            or HEADER_DESC.match(w["text"].strip())
                            or HEADER_DATE.match(w["text"].strip()))]

    candidates = []
    candidates.extend(_candidates_from_rows(_group_rows(above_words), rev_h, x_left, x_right, True))
    if not candidates:
        candidates.extend(_candidates_from_rows(_group_rows(below_words), rev_h, x_left, x_right, False))
    else:
        # Prefer above when present; still allow dated comparison within above-only set
        pass
    return _pick_newest(candidates)


def _chars_to_words(chars):
    """Cluster pypdfium2 chars (page-top ``top``) into words via join gap rule."""
    printable = [c for c in chars if (c.get("text") or "").strip() and c["text"] not in ("\r", "\n", "\t")]
    if not printable:
        return []
    # Group into lines, then join on each line
    lines = _group_rows(printable, line_tol=3.5)
    words = []
    for line in lines:
        words.extend(_join_adjacent(line, gap_factor=JOIN_GAP_FACTOR))
    return words


def _extract_titleblock_words_pdfium(pdf_path, page_index=0, right_frac=0.32):
    """Char boxes in the right title-block strip via pypdfium2 (PDF coords → page-top)."""
    import pypdfium2 as pdfium

    path = Path(pdf_path)
    doc = pdfium.PdfDocument(str(path))
    try:
        if page_index < 0 or page_index >= len(doc):
            return []
        page = doc[page_index]
        pw, ph = float(page.get_width()), float(page.get_height())
        left = pw * (1.0 - right_frac)
        tp = page.get_textpage()
        try:
            # Bounded probe — empty strip → not_read without walking every char
            bounded = tp.get_text_bounded(left=left, bottom=0.0, right=pw, top=ph) or ""
            if not bounded.strip():
                return []
            n = tp.count_chars()
            chars = []
            for i in range(n):
                ch = tp.get_text_range(i, 1)
                if not ch or ch in ("\r", "\n", "\t"):
                    continue
                # get_charbox → (left, bottom, right, top) in PDF space (origin bottom-left)
                box = tp.get_charbox(i)
                x0, y0, x1, y1 = float(box[0]), float(box[1]), float(box[2]), float(box[3])
                if x1 < left:
                    continue
                # Convert to page-top coordinates (match unit-test / pdfplumber convention)
                chars.append({
                    "text": ch,
                    "x0": x0,
                    "x1": x1,
                    "top": ph - y1,
                    "bottom": ph - y0,
                })
            return _chars_to_words(chars)
        finally:
            tp.close()
    finally:
        doc.close()


def revision_from_pdf_text(pdf_path, page_index=0):
    """Return (revision_code, 'revision_table') or (None, 'not_read').

    Uses pypdfium2 char boxes in the title-block rectangle (R3 speed / memory).
    """
    try:
        import pypdfium2  # noqa: F401
    except ImportError as exc:
        raise RuntimeError("pypdfium2 is not installed") from exc
    path = Path(pdf_path)
    if not path.is_file():
        return None, "not_read"
    words = _extract_titleblock_words_pdfium(path, page_index, right_frac=0.32)
    code, src = revision_from_words(words)
    if code:
        return code, src
    words2 = _extract_titleblock_words_pdfium(path, page_index, right_frac=0.45)
    return revision_from_words(words2)


def revision_from_text(text):
    """Best-effort parse from plain text (tests only). Prefer revision_from_words / PDF path."""
    if not text:
        return None, "not_read"
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
