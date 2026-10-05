#!/usr/bin/env python3
"""Scanned-page decision per page, for review and calibration (SPC-0006 §4.2.3).

A page is scanned when its vector character count is below SCAN_MAX_CHARS and its images together
cover at least SCAN_MIN_RASTER of it (pdf_common; approved 5 Oct 2026, calibrated on T3).
extract_pdf.py already applies the rule; this script lists the evidence per page so the values can
be checked. --max-chars / --min-cover show what other values would decide; they never change the
ingestion file.

Usage: python detect_scanned.py --ingestion <file.pdf.json> [--max-chars N] [--min-cover F] [--out <file.json>]
Exit: 0 · 2 input error. No network, no shell calls."""
__version__ = "0.1.0"

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pdf_common import (EXIT_INPUT, EXIT_OK, SCAN_MAX_CHARS, SCAN_MIN_RASTER, PdfError, atomic_write_json,  # noqa: E402
                        check_path, emit)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--ingestion", required=True)
    ap.add_argument("--max-chars", type=int, default=SCAN_MAX_CHARS)
    ap.add_argument("--min-cover", type=float, default=SCAN_MIN_RASTER)
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    try:
        doc = json.loads(check_path(a.ingestion, must_exist=True).read_text(encoding="utf-8"))
        pages = doc["content"]["pages"]
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
        emit({"status": "error", "code": EXIT_INPUT, "detail": f"not a PDF ingestion file: {exc}"})
        return EXIT_INPUT
    except PdfError as e:
        emit({"status": "error", "code": e.code, "detail": e.detail})
        return e.code
    rows = [{"page": p["page"], "char_count": p["char_count"], "image_cover": p["image_cover"],
             "raster_ratio": p["raster_ratio"],
             "scanned": p["char_count"] < a.max_chars and p["raster_ratio"] >= a.min_cover,
             "recorded": p["scanned"]} for p in pages]
    res = {"rule": {"max_chars": a.max_chars, "min_raster_ratio": a.min_cover},
           "default_rule": a.max_chars == SCAN_MAX_CHARS and a.min_cover == SCAN_MIN_RASTER, "pages": rows}
    if a.out:
        atomic_write_json(a.out, res)
    emit({"status": "ok", "pages": len(rows), "scanned": [r["page"] for r in rows if r["scanned"]],
          "differs_from_recorded": [r["page"] for r in rows if r["scanned"] != r["recorded"]]})
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
