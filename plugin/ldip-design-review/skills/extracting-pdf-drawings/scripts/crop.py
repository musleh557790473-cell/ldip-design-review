#!/usr/bin/env python3
"""Evidence crop of one PDF page region (SPC-0006 §4.2.3; FC3).

Renders the box with pypdfium2 and writes a PNG without metadata, so the same input gives the same
bytes (and SHA-256) on every run with the same pypdfium2 build (§4.2.6 T4). The hash is printed and
written next to the image as <png>.sha256 for the finding's evidence block.

bbox: [x0, top, x1, bottom] in PDF points from the displayed page's top-left corner (same convention
as extract_pdf.py). Pages are counted from 1.

Usage: python crop.py --pdf <file.pdf> --page <n> --bbox x0 top x1 bottom [--dpi 200] --out evidence/<agent>/<id>_<n>.png
Exit: 0 written · 2 input error (page or box outside the page, path > 250, not .png) · 3 missing package.
No network, no shell calls."""
__version__ = "0.1.0"

import argparse
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pdf_common import (EXIT_INPUT, EXIT_OK, PdfError, atomic_write_bytes, check_path, emit, need,  # noqa: E402
                        sha256_bytes)

MIN_DPI, MAX_DPI = 72, 600


def crop(pdf_path, page_no, bbox, dpi):
    pdfium, = need("pypdfium2")
    need("PIL")
    pdf = check_path(pdf_path, must_exist=True)
    if not MIN_DPI <= dpi <= MAX_DPI:
        raise PdfError(EXIT_INPUT, f"--dpi must be {MIN_DPI}–{MAX_DPI}")
    try:
        doc = pdfium.PdfDocument(str(pdf))
    except Exception as exc:
        raise PdfError(EXIT_INPUT, f"cannot open PDF: {type(exc).__name__}: {str(exc)[:200]}") from exc
    try:
        if not 1 <= page_no <= len(doc):
            raise PdfError(EXIT_INPUT, f"page {page_no} outside 1–{len(doc)}")
        page = doc[page_no - 1]
        w, h = page.get_size()                        # displayed size (rotation applied)
        x0, top, x1, bottom = bbox
        if not (0 <= x0 < x1 <= w + 0.01 and 0 <= top < bottom <= h + 0.01):
            raise PdfError(EXIT_INPUT, f"bbox {bbox} outside the page ({w:.2f} x {h:.2f} pt) or empty")
        bmp = page.render(scale=dpi / 72.0, crop=(x0, max(0.0, h - bottom), max(0.0, w - x1), top))
        img = bmp.to_pil().convert("RGB")
        buf = io.BytesIO()
        img.save(buf, format="PNG", optimize=False, compress_level=6)   # no pnginfo → no time stamps
        return buf.getvalue(), img.size
    finally:
        doc.close()


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf", required=True)
    ap.add_argument("--page", type=int, required=True)
    ap.add_argument("--bbox", type=float, nargs=4, required=True, metavar=("X0", "TOP", "X1", "BOTTOM"))
    ap.add_argument("--dpi", type=int, default=200)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    try:
        out = check_path(a.out)
        if out.suffix.lower() != ".png":
            raise PdfError(EXIT_INPUT, "--out must be a .png file")
        data, size = crop(a.pdf, a.page, a.bbox, a.dpi)
        sha = sha256_bytes(data)
        atomic_write_bytes(out, data)
        atomic_write_bytes(Path(str(out) + ".sha256"), (sha + "\n").encode())
    except PdfError as e:
        emit({"status": "error", "code": e.code, "detail": e.detail})
        return e.code
    emit({"status": "ok", "out": str(out), "sha256": sha, "pixels": list(size), "dpi": a.dpi, "page": a.page,
          "bbox": a.bbox, "script": f"crop.py {__version__}"})
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
