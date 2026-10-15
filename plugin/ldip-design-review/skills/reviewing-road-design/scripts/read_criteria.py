#!/usr/bin/env python3
"""Read the road design-criteria table of a Parsons design report from its PDF ingestion file
(skill 2 `*.pdf.json`, vector words with page positions) — SPC-0006 Rev D §4.13.6.

A table is found by its header line: a word "Parameter" followed, on the same line, by two or more class
headings that map to rulebook classes (road_common.class_of). Columns are bounded by the heading
positions; rows are lines below the header with a label in the label column; the table ends at a vertical
gap larger than 1.5 x the median row spacing. A header repeated on the next page continues the table.
Each value keeps page, bbox and the text as printed; numbers are parsed, never corrected. The departures
table of the report ("Ref. / Design Element / Standard Requirement / Adopted Value") is listed for the
reviewer when present (D10-7).

Usage: python read_criteria.py --ingestion <report.pdf.json> --out <criteria.json> [--pages 1-400]
Exit: 0 written (also when no table is found: tables = []) · 2 input error · 4 output failed its check.
No network, no shell calls.
"""
__version__ = "0.1.0"

import argparse
import re
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from road_common import (EXIT_INPUT, EXIT_OK, RoadError, atomic_write_json, class_of, criterion_of,  # noqa: E402
                         emit, main_guard, numbers, read_json, source_of)

LINE_TOL = 2.5          # pt: words whose tops differ by less belong to one line
GAP_FACTOR = 1.5


def lines_of(spans):
    out = []
    for s in sorted(spans, key=lambda s: (s["bbox"][1], s["bbox"][0])):
        if out and abs(s["bbox"][1] - out[-1]["top"]) < LINE_TOL:
            out[-1]["spans"].append(s)
        else:
            out.append({"top": s["bbox"][1], "spans": [s]})
    for ln in out:
        ln["spans"].sort(key=lambda s: s["bbox"][0])
    return out


def group_words(spans, gap=8.0):
    """Join words of one line into phrases (gap in pt)."""
    groups = []
    for s in spans:
        if groups and s["bbox"][0] - groups[-1]["bbox"][2] < gap:
            g = groups[-1]
            g["text"] += " " + s["text"]
            g["bbox"] = [min(g["bbox"][0], s["bbox"][0]), min(g["bbox"][1], s["bbox"][1]),
                         max(g["bbox"][2], s["bbox"][2]), max(g["bbox"][3], s["bbox"][3])]
        else:
            groups.append({"text": s["text"], "bbox": list(s["bbox"])})
    return groups


def header_of(line):
    """Columns if this line is a criteria-table header, else None."""
    groups = group_words(line["spans"])
    if not groups or not groups[0]["text"].lower().startswith("parameter"):
        return None
    cols = [{"heading": g["text"], "class": class_of(g["text"]), "x0": g["bbox"][0],
             "centre": (g["bbox"][0] + g["bbox"][2]) / 2} for g in groups[1:]]
    cols = [c for c in cols if c["class"]]
    if len(cols) < 2:
        return None
    # columns are bounded half-way between heading centres; the first column reaches as far left of its
    # centre as it reaches right, but never into the "Parameter" heading
    for i, c in enumerate(cols):
        c["right"] = (c["centre"] + cols[i + 1]["centre"]) / 2 if i + 1 < len(cols) else 1e9
    half = cols[0]["right"] - cols[0]["centre"]
    for i, c in enumerate(cols):
        c["left"] = cols[i - 1]["right"] if i else max(groups[0]["bbox"][2] + 4.0, c["centre"] - half)
    return {"columns": cols, "label_right": cols[0]["left"], "page_left": groups[0]["bbox"][0]}


def bbox_union(boxes):
    return [round(min(b[0] for b in boxes), 2), round(min(b[1] for b in boxes), 2),
            round(max(b[2] for b in boxes), 2), round(max(b[3] for b in boxes), 2)]


def read_rows(lines, start, hdr, page_no):
    rows, last_top, steps = [], lines[start]["top"], []
    for ln in lines[start + 1:]:
        step = ln["top"] - last_top
        med = statistics.median(steps) if steps else step
        if steps and step > GAP_FACTOR * med:
            break
        steps.append(step)
        last_top = ln["top"]
        label = [s for s in ln["spans"] if s["bbox"][2] <= hdr["columns"][0]["left"] + 1.0]
        if not label:
            break
        cells = {}
        for c in hdr["columns"]:
            ws = [s for s in ln["spans"] if c["left"] <= (s["bbox"][0] + s["bbox"][2]) / 2 < c["right"]
                  and s not in label]
            if ws:
                cells[c["class"]] = {"text": " ".join(w["text"] for w in ws), "page": page_no,
                                     "bbox": bbox_union([w["bbox"] for w in ws])}
        label_text = " ".join(s["text"] for s in label)
        rows.append({"label": label_text, "criterion": criterion_of(label_text), "page": page_no,
                     "label_bbox": bbox_union([s["bbox"] for s in label]), "cells": cells})
    return rows


def departures(pages):
    """Rows of a departures table (Ref. DEP-nn) — listed only (D10-7)."""
    out = []
    for p in pages:
        for s in p.get("spans", []):
            if re.fullmatch(r"DEP-\d{2,3}", s["text"]):
                line = [w for w in p["spans"] if abs(w["bbox"][1] - s["bbox"][1]) < 12]
                out.append({"ref": s["text"], "page": p["page"],
                            "text": " ".join(w["text"] for w in sorted(line, key=lambda w: (w["bbox"][1], w["bbox"][0])))[:300]})
    return out


def read(ing, page_range=None):
    pages = [p for p in (ing.get("content") or {}).get("pages", [])
             if not page_range or page_range[0] <= p["page"] <= page_range[1]]
    tables = []
    for p in pages:
        lines = lines_of(p.get("spans", []))
        for i, ln in enumerate(lines):
            hdr = header_of(ln)
            if not hdr:
                continue
            rows = read_rows(lines, i, hdr, p["page"])
            classes = [c["class"] for c in hdr["columns"]]
            if tables and tables[-1]["classes"] == classes and tables[-1]["pages"][-1] == p["page"] - 1:
                tables[-1]["rows"] += rows
                tables[-1]["pages"].append(p["page"])
            else:
                tables.append({"classes": classes, "headings": [c["heading"] for c in hdr["columns"]],
                               "pages": [p["page"]], "rows": rows})
    criteria = {}
    warnings = []
    for t in tables:
        for r in t["rows"]:
            if not r["criterion"] or not r["cells"]:
                continue
            for cls, cell in r["cells"].items():
                entry = {"text": cell["text"], "numbers": numbers(cell["text"]), "page": cell["page"],
                         "bbox": cell["bbox"], "label": r["label"], "label_bbox": r["label_bbox"]}
                if r["criterion"] == "functional_class":
                    entry["class_check"] = class_of(cell["text"])
                    if entry["class_check"] and entry["class_check"] != cls:
                        warnings.append({"code": "CLASS_HEADING_MISMATCH",
                                         "detail": f"column {cls} states functional class '{cell['text']}' (p.{cell['page']})"})
                criteria.setdefault(cls, {})
                if r["criterion"] in criteria[cls]:
                    warnings.append({"code": "CRITERION_REPEATED", "detail": f"{cls} {r['criterion']} on p.{cell['page']}; first kept"})
                    continue
                criteria[cls][r["criterion"]] = entry
    if not tables:
        warnings.append({"code": "NO_CRITERIA_TABLE", "detail": "no 'Parameter' header with two or more road-class columns found"})
    pdf = [p["page"] for p in pages]
    return {"tool": {"script": "read_criteria.py", "version": __version__}, "source": source_of(ing),
            "pages_searched": [min(pdf), max(pdf)] if pdf else [], "tables": tables, "criteria": criteria,
            "departures": departures(pages), "warnings": warnings}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--ingestion", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--pages")
    a = ap.parse_args(argv)

    def run():
        ing = read_json(a.ingestion)
        if (ing.get("source") or {}).get("file_type") != "pdf":
            raise RoadError(EXIT_INPUT, "--ingestion must be a PDF ingestion file (skill 2)")
        rng = None
        if a.pages:
            m = re.fullmatch(r"(\d+)-(\d+)", a.pages)
            if not m:
                raise RoadError(EXIT_INPUT, "--pages must be <first>-<last>")
            rng = (int(m.group(1)), int(m.group(2)))
        res = read(ing, rng)
        atomic_write_json(a.out, res)
        emit({"status": "ok", "out": a.out, "tables": len(res["tables"]),
              "classes": {k: len(v) for k, v in res["criteria"].items()}, "departures": len(res["departures"]),
              "warnings": len(res["warnings"])})
        return EXIT_OK
    return main_guard(run)


if __name__ == "__main__":
    sys.exit(main())
