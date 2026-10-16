#!/usr/bin/env python3
"""PDF → common ingestion JSON (SPC-0006 §2.4, §4.2.3–4.2.4), read with pypdfium2 (PDFium).

Per page: displayed size (pt), rotation, vector text words with bbox (font and size not reported:
null), character count, object count, largest-image cover and all-images raster ratio, scanned flag
(pdf_common SCAN_* rule), colours used (recorded only — never sole evidence, brief §5.5; drawing
colours skipped on pages above LARGE_PAGE_OBJECTS). Title block: read by title_block.py from the
Parsons label → value layout (K5-1, 11 Oct 2026; per page and the first one at top level), else null
with TITLE_BLOCK_NOT_FOUND. Callouts are not read: `callouts` = [] with CALLOUT_STYLE_UNKNOWN.
A page that cannot be read is recorded with its error and a PAGE_UNREADABLE warning (not fatal).
OCR of scanned pages is a separate step (ocr_tiles.py).

bbox convention: [x0, top, x1, bottom] in PDF points from the displayed page's top-left corner (crop
box origin and /Rotate applied).

Usage: python extract_pdf.py --pdf <file.pdf> --out <batch>/<file.pdf>.json [--source-path <rel path>] [--paths]
--paths (0.4.0, SPC-0006 Rev F §4.13C.3, DF-5): also writes `<file>.paths.json` next to the ingestion file —
the coloured vector paths of each page (vector_paths.py) — and lists it in `content.side_files`
(template 1.2). Only for a road hierarchy plan named in the prompt (method M4 of skill 10).
Exit: 0 written · 2 input error (missing, not a PDF, encrypted, path > 250) · 3 missing package.
No network, no shell calls."""
__version__ = "0.4.0"

import argparse
import collections
import ctypes
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from title_block import read_document  # noqa: E402
from pdf_common import (DOCNO_RE, EXIT_INPUT, EXIT_OK, LARGE_PAGE_OBJECTS, MAX_REGIONS, REGION_MIN_SHARE,  # noqa: E402
                        REGION_MIN_SIDE_PT, SCAN_MAX_CHARS, SCAN_MIN_RASTER, PdfError, atomic_write_json,
                        box_to_display, check_path, emit, merge_regions, need, r2, sha256_file)

RIYADH = timezone(timedelta(hours=3))
MAX_COLOURS = 20
BREAK = {" ", "\t", "\r", "\n", "\x00", "\x02", "\ufffe", "\uffff"}


def colour_key(r, g, b):
    return "(" + ",".join(f"{v / 255.0:.3f}" for v in (r, g, b)) + ")"


def words_of(tp, crop, rot):
    """Group the page's characters into words: a break at whitespace / line ends, or where the next
    character is far from the previous one (handles rotated text). Boxes in displayed coordinates."""
    n = tp.count_chars()
    full = tp.get_text_range(0, n) if n else ""
    same = len(full) == n
    words, cur = [], None

    def close():
        if cur and cur["text"].strip():
            words.append(cur)

    prev = None
    for i in range(n):
        ch = full[i] if same else tp.get_text_range(i, 1)
        if ch in BREAK or ch.isspace():
            close()
            cur, prev = None, None
            continue
        l, b, r, t = tp.get_charbox(i)
        x0, y0, x1, y1 = box_to_display(l, b, r, t, crop, rot)
        if prev is not None:
            pcx, pcy, pd = prev
            cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
            dim = max(pd, x1 - x0, y1 - y0, 0.5)
            if ((cx - pcx) ** 2 + (cy - pcy) ** 2) ** 0.5 > 1.6 * dim:
                close()
                cur = None
        if cur is None:
            cur = {"text": "", "box": [x0, y0, x1, y1]}
        cur["text"] += ch
        bx = cur["box"]
        cur["box"] = [min(bx[0], x0), min(bx[1], y0), max(bx[2], x1), max(bx[3], y1)]
        prev = ((x0 + x1) / 2, (y0 + y1) / 2, max(x1 - x0, y1 - y0))
    close()
    return words, sum(1 for c in full if not c.isspace() and c not in BREAK) if same else len(
        [w for w in words for _ in w["text"]])


def page_record(page, n, raw):
    rot = int(page.get_rotation() or 0) % 360
    crop = page.get_cropbox()
    w, h = page.get_size()                                   # displayed size (rotation applied)
    tp = page.get_textpage()
    try:
        words, chars = words_of(tp, crop, rot)
        cols = collections.Counter()
        rr, gg, bb, aa = (ctypes.c_uint() for _ in range(4))
        for i in range(tp.count_chars()):
            if raw.FPDFText_GetFillColor(tp.raw, i, rr, gg, bb, aa):
                cols[colour_key(rr.value, gg.value, bb.value)] += 1
    finally:
        tp.close()
    area = (w * h) or 1.0
    best, total, nobj, paths, imgs = 0.0, 0.0, 0, [], []
    for obj in page.get_objects(max_depth=15):
        nobj += 1
        if obj.type == raw.FPDF_PAGEOBJ_IMAGE:
            x0, y0, x1, y1 = box_to_display(*obj.get_bounds(), crop, rot)
            a = max(0.0, min(w, x1) - max(0.0, x0)) * max(0.0, min(h, y1) - max(0.0, y0))
            best, total = max(best, a), total + a
            if a > 0:
                imgs.append([max(0.0, x0), max(0.0, y0), min(w, x1), min(h, y1)])
        elif obj.type == raw.FPDF_PAGEOBJ_PATH and nobj <= LARGE_PAGE_OBJECTS:
            paths.append(obj)
    large = nobj > LARGE_PAGE_OBJECTS
    if not large:
        for obj in paths:
            for get in (raw.FPDFPageObj_GetFillColor, raw.FPDFPageObj_GetStrokeColor):
                if get(obj.raw, rr, gg, bb, aa) and aa.value:
                    cols[colour_key(rr.value, gg.value, bb.value)] += 1
    paths.clear()
    cover, raster = round(min(1.0, best / area), 4), round(min(1.0, total / area), 4)
    regions = []
    imgs.sort(key=lambda b: -((b[2] - b[0]) * (b[3] - b[1])))
    for box, pieces in merge_regions(imgs[:5000])[:MAX_REGIONS]:          # bound the merge cost
        bw, bh = box[2] - box[0], box[3] - box[1]
        regions.append({"bbox": [r2(v) for v in box], "pieces": pieces, "share": round(bw * bh / area, 4),
                        "ocr_candidate": min(bw, bh) >= REGION_MIN_SIDE_PT and bw * bh / area >= REGION_MIN_SHARE})
    spans = [{"text": wd["text"], "bbox": [r2(v) for v in wd["box"]], "font": None, "size": None} for wd in words]
    spans.sort(key=lambda s_: (s_["bbox"][1], s_["bbox"][0], s_["text"]))
    return {"page": n, "width_pt": r2(w), "height_pt": r2(h), "rotation": rot, "char_count": chars,
            "image_cover": cover, "raster_ratio": raster, "scanned": chars < SCAN_MAX_CHARS and raster >= SCAN_MIN_RASTER,
            "spans": spans, "ocr_spans": [], "image_count": len(imgs), "image_regions": regions,
            "colours_used": [{"colour": k, "count": v} for k, v in sorted(cols.items(), key=lambda kv: (-kv[1], kv[0]))[:MAX_COLOURS]],
            "objects": nobj, "colours_complete": not large, "error": None}


def extract(pdf_path, source_path):
    pdfium, raw = need("pypdfium2", "pypdfium2.raw")
    pdf = check_path(pdf_path, must_exist=True)
    with open(pdf, "rb") as fh:
        if fh.read(5) != b"%PDF-":
            raise PdfError(EXIT_INPUT, f"not a PDF file: {pdf_path}")
    warnings, pages = [], []
    try:
        doc = pdfium.PdfDocument(str(pdf))
    except Exception as exc:  # encrypted, broken
        raise PdfError(EXIT_INPUT, f"cannot open PDF: {type(exc).__name__}: {str(exc)[:200]}") from exc
    try:
        for i in range(len(doc)):
            try:
                page = doc[i]
                try:
                    pages.append(page_record(page, i + 1, raw))
                finally:
                    page.close()
            except PdfError:
                raise
            except Exception as exc:  # damaged page: record and continue (§4.2.6 T2)
                pages.append({"page": i + 1, "title_block": None, "width_pt": None, "height_pt": None, "rotation": 0, "char_count": 0,
                              "image_cover": 0.0, "raster_ratio": 0.0, "scanned": False, "spans": [], "ocr_spans": [],
                              "image_count": 0, "image_regions": [],
                              "colours_used": [], "objects": 0, "colours_complete": False,
                              "error": f"{type(exc).__name__}: {str(exc)[:200]}"})
                warnings.append({"code": "PAGE_UNREADABLE", "detail": f"page {i + 1}: {type(exc).__name__}: {str(exc)[:160]}"})
    finally:
        doc.close()
    scanned = [p["page"] for p in pages if p["scanned"]]
    partial = [p["page"] for p in pages if not p["scanned"] and any(r["ocr_candidate"] for r in p["image_regions"])]
    if partial:
        warnings.append({"code": "IMAGE_REGIONS", "detail": f"picture region(s) inside vector page(s) "
                         + ", ".join(map(str, partial[:30])) + "; run ocr_tiles.py to read their text"})
    if scanned:
        warnings.append({"code": "SCANNED_PAGES", "detail": f"{len(scanned)} page(s) scanned: "
                         + ", ".join(map(str, scanned[:30])) + (" …" if len(scanned) > 30 else "") + "; run ocr_tiles.py"})
    large = [p["page"] for p in pages if not p["colours_complete"] and not p["error"]]
    if large:
        warnings.append({"code": "LARGE_PAGE", "detail": f"page(s) {', '.join(map(str, large[:30]))} hold more than "
                         f"{LARGE_PAGE_OBJECTS:,} objects: drawing colours not read (text colours are); the Verifier's "
                         "second reader is not used on them"})
    tb, tb_warn = read_document(pages)
    warnings += tb_warn
    warnings.append({"code": "CALLOUT_STYLE_UNKNOWN", "detail": "Parsons sheet / detail callout style not yet known; callouts not read"})
    stem = Path(source_path).stem
    from pypdfium2 import version as pv
    return {
        "template_version": "1.0",
        "source": {"path": Path(source_path).as_posix(), "sha256": sha256_file(pdf), "bytes": pdf.stat().st_size,
                   "document_no": stem if DOCNO_RE.match(stem) else None, "revision": None, "file_type": "pdf"},
        "extractor": {"skill": "extracting-pdf-drawings", "script": "extract_pdf.py", "version": __version__,
                      "converter": {"name": "pypdfium2", "version": f"{pv.PYPDFIUM_INFO} (PDFium {pv.PDFIUM_INFO})"}},
        "crs": {"declared": None, "units": "unknown", "insunits": None},
        "title_block": tb,
        "content": {"pages": pages, "page_count": len(pages), "callouts": [], "ocr": None,
                    "scanned_rule": {"max_chars": SCAN_MAX_CHARS, "min_raster_ratio": SCAN_MIN_RASTER}},
        "warnings": warnings,
        "meta": {"extracted_at": datetime.now(RIYADH).isoformat(timespec="seconds")},
    }


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--source-path", help="path recorded in source.path (relative to LDIP_Review); default --pdf")
    ap.add_argument("--paths", action="store_true", help="also write <file>.paths.json (coloured vector paths)")
    a = ap.parse_args(argv)
    try:
        check_path(a.out)
        src = a.source_path or a.pdf
        if not str(a.out).endswith(Path(src).name + ".json"):
            raise PdfError(EXIT_INPUT, f"--out must be named {Path(src).name}.json")
        doc = extract(a.pdf, src)
        if a.paths:
            import vector_paths
            name = Path(src).name
            side = Path(a.out).with_name(name[:-4] + ".paths.json" if name.lower().endswith(".pdf") else name + ".paths.json")
            pj = vector_paths.build(a.pdf, Path(src).as_posix(), doc["source"]["sha256"], need)
            atomic_write_json(side, pj)
            doc["template_version"] = "1.2"
            doc["content"]["side_files"] = [{"path": side.name, "kind": "paths", "sha256": sha256_file(side),
                                             "records": sum(len(g["paths"]) for p in pj["pages"] for g in p["colours"].values())}]
        atomic_write_json(a.out, doc)
    except PdfError as e:
        emit({"status": "error", "code": e.code, "detail": e.detail})
        return e.code
    pages = doc["content"]["pages"]
    emit({"status": "ok", "out": str(a.out), "pages": len(pages), "spans": sum(len(p["spans"]) for p in pages),
          "scanned_pages": [p["page"] for p in pages if p["scanned"]],
          "unreadable_pages": [p["page"] for p in pages if p["error"]],
          "large_pages": [p["page"] for p in pages if not p["colours_complete"] and not p["error"]],
          "image_region_pages": [p["page"] for p in pages if not p["scanned"] and any(r["ocr_candidate"] for r in p["image_regions"])],
          "paths": doc["content"]["side_files"][0]["records"] if doc["content"].get("side_files") else None,
          "warnings": sorted({w["code"] for w in doc["warnings"]})})
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
