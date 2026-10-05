#!/usr/bin/env python3
"""OCR of the scanned pages and of the picture regions of a PDF ingestion file (SPC-0006 §4.2.3).

Each page marked `scanned` by extract_pdf.py is OCR'd whole (`region: page`). On the other pages, each
picture region marked `ocr_candidate` (pdf_common REGION_* values) is OCR'd inside its box
(`region: image`); words lying on vector text are dropped. Each image is rendered with pypdfium2 at
OCR_DPI, cut into tiles of
OCR_TILE_PX with OCR_OVERLAP_PX overlap, and read with Tesseract (OCR_LANG, Arabic + English). A word
touching an inner tile edge is cut and dropped from that tile; it is read whole in the neighbouring
tile when it is shorter than the overlap. A word read in two tiles (same text, boxes overlapping by
more than half) is kept once, with the higher confidence. Words longer than the overlap that cross a
tile edge can be lost (recorded limit). Every word goes to the page's `ocr_spans` with its bbox in points (same convention
as extract_pdf.py), Tesseract confidence and `source_type: pdf_ocr` (SPC-0003: OCR evidence is never
higher than Probable). The ingestion file is rewritten atomically; vector spans are not touched.

Usage: python ocr_tiles.py --ingestion <batch>/<file.pdf>.json --pdf <file.pdf> [--pages 3 5 …]
Exit: 0 written · 2 input error (file, hash mismatch) · 3 tesseract / language data / package missing.
No network; the only external program is the tesseract binary, called by pytesseract."""
__version__ = "0.1.0"

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pdf_common import (EXIT_DEPENDENCY, EXIT_INPUT, EXIT_OK, OCR_DPI, OCR_EDGE_PX, OCR_LANG, OCR_OVERLAP_PX,  # noqa: E402
                        OCR_TILE_PX,
                        PdfError, atomic_write_json, check_path, emit, need, r2, sha256_file)


def tesseract_ready(pytesseract):
    try:
        ver = str(pytesseract.get_tesseract_version())
        langs = set(pytesseract.get_languages(config=""))
    except Exception as exc:
        raise PdfError(EXIT_DEPENDENCY, f"tesseract not available: {type(exc).__name__}: {str(exc)[:160]}") from exc
    missing = [lg for lg in OCR_LANG.split("+") if lg not in langs]
    if missing:
        raise PdfError(EXIT_DEPENDENCY, f"tesseract language data missing: {', '.join(missing)}")
    return ver


def tiles(w, h):
    step = OCR_TILE_PX - OCR_OVERLAP_PX
    xs = list(range(0, max(1, w - OCR_OVERLAP_PX), step)) or [0]
    ys = list(range(0, max(1, h - OCR_OVERLAP_PX), step)) or [0]
    for y in ys:
        for x in xs:
            x1, y1 = min(w, x + OCR_TILE_PX), min(h, y + OCR_TILE_PX)
            yield (x, y, x1, y1), (x > 0, y > 0, x1 < w, y1 < h)   # tile, and which of its edges are cuts


def _same(a, b):
    """Same word read in two overlapping tiles: same text and boxes overlapping by more than half."""
    if a["text"] != b["text"]:
        return False
    ix = min(a["px"][2], b["px"][2]) - max(a["px"][0], b["px"][0])
    iy = min(a["px"][3], b["px"][3]) - max(a["px"][1], b["px"][1])
    if ix <= 0 or iy <= 0:
        return False
    area = lambda r: (r[2] - r[0]) * (r[3] - r[1])  # noqa: E731
    return ix * iy > 0.5 * min(area(a["px"]), area(b["px"]))


def ocr_page(pdfium_doc, page_no, pytesseract, region=None, vector_boxes=()):
    """OCR one page (region None) or one picture region [x0, top, x1, bottom] in points. Words whose
    centre lies on a vector-text box are dropped (that text is already read as vector text)."""
    page = pdfium_doc[page_no - 1]
    scale = OCR_DPI / 72.0
    if region is None:
        img, ox, oy = page.render(scale=scale).to_pil().convert("RGB"), 0.0, 0.0
    else:
        pw, ph = page.get_size()
        x0, t0, x1, b0 = region
        img = page.render(scale=scale, crop=(x0, max(0.0, ph - b0), max(0.0, pw - x1), t0)).to_pil().convert("RGB")
        ox, oy = x0, t0
    w, h = img.size
    k = 72.0 / OCR_DPI
    words = []
    for (x0, y0, x1, y1), (cl, ct, cr, cb) in tiles(w, h):
        d = pytesseract.image_to_data(img.crop((x0, y0, x1, y1)), lang=OCR_LANG, output_type=pytesseract.Output.DICT)
        for i, text in enumerate(d["text"]):
            text = (text or "").strip()
            conf = float(d["conf"][i])
            if not text or conf < 0:
                continue
            lx, ty, ww, hh = d["left"][i] + x0, d["top"][i] + y0, d["width"][i], d["height"][i]
            if ((cl and lx - x0 <= OCR_EDGE_PX) or (ct and ty - y0 <= OCR_EDGE_PX)
                    or (cr and x1 - (lx + ww) <= OCR_EDGE_PX) or (cb and y1 - (ty + hh) <= OCR_EDGE_PX)):
                continue                                # cut by a tile edge: read whole in the neighbour
            cand = {"text": text, "conf": conf, "px": (lx, ty, lx + ww, ty + hh)}
            dup = next((w_ for w_ in words if _same(w_, cand)), None)
            if dup is None:
                words.append(cand)
            elif cand["conf"] > dup["conf"]:
                dup.update(cand)
    tag = "page" if region is None else "image"
    out = []
    for w_ in words:
        bx = [ox + w_["px"][0] * k, oy + w_["px"][1] * k, ox + w_["px"][2] * k, oy + w_["px"][3] * k]
        cx, cy = (bx[0] + bx[2]) / 2, (bx[1] + bx[3]) / 2
        if any(v[0] <= cx <= v[2] and v[1] <= cy <= v[3] for v in vector_boxes):
            continue
        out.append({"text": w_["text"], "bbox": [r2(v) for v in bx], "conf": round(w_["conf"], 1),
                    "source_type": "pdf_ocr", "region": tag})
    out.sort(key=lambda s_: (s_["bbox"][1], s_["bbox"][0], s_["text"]))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--ingestion", required=True)
    ap.add_argument("--pdf", required=True)
    ap.add_argument("--pages", type=int, nargs="+", help="limit to these scanned pages")
    a = ap.parse_args(argv)
    try:
        pdfium, pytesseract = need("pypdfium2", "pytesseract")
        ing_path = check_path(a.ingestion, must_exist=True)
        pdf = check_path(a.pdf, must_exist=True)
        try:
            doc = json.loads(ing_path.read_text(encoding="utf-8"))
            pages = doc["content"]["pages"]
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            raise PdfError(EXIT_INPUT, f"not a PDF ingestion file: {exc}") from exc
        if doc["source"]["sha256"] != sha256_file(pdf):
            raise PdfError(EXIT_INPUT, "the PDF differs from the one the ingestion file was made from (sha256)")
        ver = tesseract_ready(pytesseract)
        sel = [p for p in pages if not p.get("error") and (not a.pages or p["page"] in a.pages)]
        want = [p for p in sel if p["scanned"]]
        parts = [p for p in sel if not p["scanned"] and any(r["ocr_candidate"] for r in p.get("image_regions", []))]
        n_regions = 0
        pdoc = pdfium.PdfDocument(str(pdf))
        try:
            for p in want:
                p["ocr_spans"] = ocr_page(pdoc, p["page"], pytesseract)
            for p in parts:
                vec = [s_["bbox"] for s_ in p["spans"]]
                spans = []
                for r in p["image_regions"]:
                    if r["ocr_candidate"]:
                        spans += ocr_page(pdoc, p["page"], pytesseract, region=r["bbox"], vector_boxes=vec)
                        n_regions += 1
                spans.sort(key=lambda s_: (s_["bbox"][1], s_["bbox"][0], s_["text"]))
                p["ocr_spans"] = spans
        finally:
            pdoc.close()
        want = sorted(want + parts, key=lambda p: p["page"])
        doc["content"]["ocr"] = {"engine": "tesseract", "version": ver, "lang": OCR_LANG, "dpi": OCR_DPI,
                                 "tile_px": OCR_TILE_PX, "overlap_px": OCR_OVERLAP_PX, "pages": [p["page"] for p in want],
                                 "image_regions": n_regions,
                                 "script": f"ocr_tiles.py {__version__}"}
        doc["warnings"] = [w for w in doc["warnings"] if w["code"] != "OCR_TEXT"] + (
            [{"code": "OCR_TEXT", "detail": f"OCR text on page(s) {', '.join(str(p['page']) for p in want)}; "
                                            "OCR evidence is Probable at most (SPC-0003)"}] if want else [])
        atomic_write_json(ing_path, doc)
    except PdfError as e:
        emit({"status": "error", "code": e.code, "detail": e.detail})
        return e.code
    emit({"status": "ok", "out": str(a.ingestion), "pages_ocr": [p["page"] for p in want], "image_regions": n_regions,
          "words": sum(len(p["ocr_spans"]) for p in want), "tesseract": ver, "lang": OCR_LANG})
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
