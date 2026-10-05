"""Shared helpers for skill 2 extracting-pdf-drawings (SPC-0006 §2.3, §4.2). No network, no shell calls.

Backends (owner approved 5 Oct 2026, roles swapped after the T1 test the same day): pypdfium2
(PDFium, Apache-2.0 / BSD) reads text, images and colours and renders crops; pdfplumber (MIT) is the
Verifier's independent second reader. PyMuPDF is not used until its AGPL licence review is closed
(SPC-0006 A.3)."""
__version__ = "0.1.0"

import hashlib
import json
import os
import re
from pathlib import Path

EXIT_OK, EXIT_INPUT, EXIT_DEPENDENCY, EXIT_SCHEMA, EXIT_REFUSED = 0, 2, 3, 4, 5
MAX_PATH = 250                                   # SPC-0002 R1
DOCNO_RE = re.compile(r"^[A-Z0-9]{2,6}(-[A-Z0-9]+){8,9}$")

# Scanned-page rule (owner approved 5 Oct 2026; calibrated on SPC-0006 §4.2.6 T3, whose pages are seven
# image strips of ~14 % each: the rule uses all images together, not one image)
SCAN_MAX_CHARS = 20          # fewer vector characters than this …
SCAN_MIN_RASTER = 0.90       # … and images together covering at least this share of the page → scanned

# Pages with more drawing objects than this: colours are not read (cost), and the Verifier's second
# reader (pdfplumber, pure Python) is not used (memory) — owner approved 5 Oct 2026
LARGE_PAGE_OBJECTS = 200_000

# OCR tiling (owner approved 5 Oct 2026). Language order to be calibrated on T3: with "ara+eng" Tesseract
# 5.3 read the Latin number "3496" as "6" on a synthetic page, "eng+ara" read it correctly [GAP].
OCR_DPI, OCR_TILE_PX, OCR_OVERLAP_PX, OCR_LANG = 300, 2000, 100, "ara+eng"
OCR_EDGE_PX = 3   # a word touching an inner tile edge is cut; it is taken from the neighbouring tile

# Images inside vector pages (owner approved 5 Oct 2026: OCR of partial images). Touching image pieces
# (e.g. strips of one picture) are merged into regions; a region is OCR'd when both values are met
# [PROPOSED values]. OCR words lying on vector text are dropped (the vector text is already read).
REGION_JOIN_PT = 1.0         # pieces closer than this are one region
REGION_MIN_SIDE_PT = 72.0    # shorter side ≥ 1 inch …
REGION_MIN_SHARE = 0.005     # … and area ≥ 0.5 % of the page
MAX_REGIONS = 50             # regions kept per page (largest first)


class PdfError(Exception):
    def __init__(self, code, detail):
        super().__init__(detail)
        self.code, self.detail = code, detail


def check_path(path, must_exist=False):
    p = Path(path)
    if len(str(p.resolve())) > MAX_PATH:
        raise PdfError(EXIT_INPUT, f"path longer than {MAX_PATH} characters: {path}")
    if must_exist and not p.is_file():
        raise PdfError(EXIT_INPUT, f"file not found: {path}")
    return p


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_bytes(b):
    return hashlib.sha256(b).hexdigest()


def atomic_write_json(path, obj):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(obj, indent=1, sort_keys=True, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, p)


def atomic_write_bytes(path, data):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, p)


def emit(obj):
    print(json.dumps(obj, ensure_ascii=False))


def r2(x):
    """PDF points to 0.01 pt (SPC-0006 §2.3.6)."""
    return None if x is None else round(float(x), 2)


def to_display(x, y, crop, rot):
    """PDF user-space point (origin bottom-left, unrotated) → displayed page, origin top-left, points."""
    cx0, cy0, cx1, cy1 = crop
    x, y, w0, h0 = x - cx0, y - cy0, cx1 - cx0, cy1 - cy0
    if rot == 90:
        return y, x
    if rot == 180:
        return w0 - x, y
    if rot == 270:
        return h0 - y, w0 - x
    return x, h0 - y


def box_to_display(l, b, r, t, crop, rot):
    pts = [to_display(x, y, crop, rot) for x, y in ((l, b), (r, t))]
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return min(xs), min(ys), max(xs), max(ys)


def merge_regions(boxes, join=REGION_JOIN_PT):
    """Union of touching / overlapping boxes [x0, top, x1, bottom] → regions with piece counts."""
    regs = [[list(b), 1] for b in boxes]
    changed = True
    while changed:
        changed = False
        out = []
        while regs:
            (b, n) = regs.pop()
            i = 0
            while i < len(regs):
                c, m = regs[i]
                if b[0] - join <= c[2] and c[0] - join <= b[2] and b[1] - join <= c[3] and c[1] - join <= b[3]:
                    b = [min(b[0], c[0]), min(b[1], c[1]), max(b[2], c[2]), max(b[3], c[3])]
                    n += m
                    regs.pop(i)
                    changed = True
                else:
                    i += 1
            out.append([b, n])
        regs = out
    return sorted(regs, key=lambda r: -((r[0][2] - r[0][0]) * (r[0][3] - r[0][1])))


def need(*mods):
    """Import the named modules or raise exit 3."""
    import importlib
    out = []
    for m in mods:
        try:
            out.append(importlib.import_module(m))
        except ImportError as exc:
            raise PdfError(EXIT_DEPENDENCY, f"{m} is not installed ({exc})") from exc
    return out
