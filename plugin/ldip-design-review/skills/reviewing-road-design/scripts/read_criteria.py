#!/usr/bin/env python3
"""Read the road design-criteria table of a Parsons design report from its PDF ingestion file
(skill 2 `*.pdf.json`, vector words with page positions) — SPC-0006 Rev D §4.13.6.

A table is found by its header line: a word "Parameter" followed, on the same line, by two or more class
headings that map to rulebook classes (road_common.class_of). 0.2.0 (SPC-0006 Rev F §4.13.6, DF-3, K24):
- section-heading rows ("A. CLASSIFICATION, SPEED AND DESIGN VEHICLE") are skipped;
- a row label is a phrase centred left of the half-way line between the "Parameter" heading and the first
  class heading; value lines without a label (a cell printed on two lines) join the nearest labelled row;
- value columns are found from the positions of the values in the rows, not from the number of headings
  (QRWN: three value columns under two headings); each column takes the nearest heading, and its class from
  its own sub-heading in the functional-classification row when that maps to a class; ROW widths written
  in the sub-heading ("(30m & 36m)") are kept as a column attribute (`row_widths`), else the ROW row's;
- the table ends at a vertical gap larger than 1.5 x the median row spacing. A header repeated on the next
  page continues the table.
`criteria` keeps one value set per class (the first column of that class; later columns of the same class
give CLASS_COLUMNS_REPEATED); `columns` keeps every column with its own criteria. A table found but read
without any criterion gives CRITERIA_TABLE_UNREAD. Each value keeps page, bbox and the text as printed;
numbers are parsed, never corrected. The departures table of the report ("Ref. / Design Element / Standard
Requirement / Adopted Value") is listed for the reviewer when present (D10-7).

Usage: python read_criteria.py --ingestion <report.pdf.json> --out <criteria.json> [--pages 1-400]
Exit: 0 written (also when no table is found: tables = []) · 2 input error · 4 output failed its check.
No network, no shell calls.
"""
__version__ = "0.2.0"

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


SECTION_ROW = re.compile(r"^[A-H]\.\s+[A-Z][A-Z ,/&()-]{3,}$")
TOUCH = 4.0             # pt: value x-ranges closer than this belong to one column
WIDE_SHARE = 0.8        # x the smallest heading spacing: wider values do not shape the columns
ROW_WIDTHS = re.compile(r"(\d+(?:\.\d+)?)\s*m\b", re.I)


def header_of(line):
    """Header (Parameter heading and class headings) if this line is a criteria-table header, else None."""
    groups = group_words(line["spans"])
    if not groups or not groups[0]["text"].lower().startswith("parameter"):
        return None
    heads = [{"heading": g["text"], "class": class_of(g["text"]), "x0": g["bbox"][0], "x1": g["bbox"][2],
              "centre": (g["bbox"][0] + g["bbox"][2]) / 2} for g in groups[1:]]
    heads = [h for h in heads if h["class"]]
    if len(heads) < 2:
        return None
    pc = (groups[0]["bbox"][0] + groups[0]["bbox"][2]) / 2
    return {"headings": heads, "label_limit": (pc + heads[0]["centre"]) / 2, "param_x0": groups[0]["bbox"][0]}


def bbox_union(boxes):
    return [round(min(b[0] for b in boxes), 2), round(min(b[1] for b in boxes), 2),
            round(max(b[2] for b in boxes), 2), round(max(b[3] for b in boxes), 2)]


def centre(g):
    return (g["bbox"][0] + g["bbox"][2]) / 2


def collect(lines, start, hdr):
    """Labelled rows (label groups, value groups) below the header, with label-less value lines joined."""
    rows, loose, steps = [], [], []
    last_top = last_struct = lines[start]["top"]
    for ln in lines[start + 1:]:
        gap = ln["top"] - last_top
        if steps and gap > GAP_FACTOR * statistics.median(steps):
            break
        last_top = ln["top"]
        groups = group_words(ln["spans"])
        if len(groups) == 1 and SECTION_ROW.match(groups[0]["text"].strip()):
            steps.append(ln["top"] - last_struct)           # section-heading row (K24): skipped
            last_struct = ln["top"]
            continue
        label = [g for g in groups if centre(g) < hdr["label_limit"]]
        values = [g for g in groups if centre(g) >= hdr["label_limit"]]
        if label:
            steps.append(ln["top"] - last_struct)
            last_struct = ln["top"]
            rows.append({"top": ln["top"], "label": label, "values": list(values)})
        elif values:
            loose.append({"top": ln["top"], "values": values})
        else:
            break
    if rows:
        med = statistics.median(steps) if steps else 0.0
        for lv in loose:                                    # a cell printed on two or three lines
            near = min(rows, key=lambda r: abs(r["top"] - lv["top"]))
            if abs(near["top"] - lv["top"]) <= max(0.75 * med, 12.0):
                near["values"].extend(lv["values"])
    return rows


def columns_of(rows, hdr):
    """Value columns from the x-ranges of the values: ranges that overlap (or nearly touch) form one column.
    Values wider than WIDE_SHARE x the heading spacing (a cell spread over several columns) do not shape
    the columns."""
    hc = [h["centre"] for h in hdr["headings"]]
    spacing = min(b - a for a, b in zip(hc, hc[1:])) if len(hc) > 1 else 150.0
    spans = sorted((g["bbox"][0], g["bbox"][2]) for r in rows for g in r["values"]
                   if g["bbox"][2] - g["bbox"][0] <= WIDE_SHARE * spacing)
    clusters = []
    for x0, x1 in spans:
        if clusters and x0 <= clusters[-1][1] + TOUCH:
            clusters[-1][1] = max(clusters[-1][1], x1)
        else:
            clusters.append([x0, x1])
    cols = []
    for lo, hi in clusters:
        mid = (lo + hi) / 2
        h = min(hdr["headings"], key=lambda h: abs(h["centre"] - mid))
        cols.append({"heading": h["heading"], "class": h["class"], "centre": round(mid, 2), "lo": lo, "hi": hi})
    return cols


def column_index(g, cols):
    c = centre(g)
    inside = [i for i, k in enumerate(cols) if k["lo"] <= c <= k["hi"]]
    return inside[0] if inside else min(range(len(cols)), key=lambda i: abs(cols[i]["centre"] - c))


def read_rows(lines, start, hdr, page_no):
    rows = collect(lines, start, hdr)
    cols = columns_of(rows, hdr)
    out = []
    for r in rows:
        cells = []
        for i, c in enumerate(cols):
            ws = sorted((g for g in r["values"] if column_index(g, cols) == i),
                        key=lambda g: (g["bbox"][1], g["bbox"][0]))
            cells.append({"text": " ".join(w["text"] for w in ws), "page": page_no,
                          "bbox": bbox_union([w["bbox"] for w in ws])} if ws else None)
        label_text = " ".join(g["text"] for g in r["label"])
        out.append({"label": label_text, "criterion": criterion_of(label_text), "page": page_no,
                    "label_bbox": bbox_union([g["bbox"] for g in r["label"]]), "cells": cells})
    return out, cols


def finish_columns(t, warnings):
    """Class and ROW widths of each column from its sub-heading (functional classification row)."""
    for i, c in enumerate(t["columns"]):
        c["sub_heading"] = None
        c["row_widths"] = []
        for r in t["rows"]:
            cell = r["cells"][i] if i < len(r["cells"]) else None
            if r["criterion"] == "functional_class" and cell:
                c["sub_heading"] = cell["text"]
                sub = class_of(cell["text"])
                if sub and sub != c["class"]:
                    warnings.append({"code": "CLASS_HEADING_MISMATCH",
                                     "detail": f"column under '{c['heading']}' states functional class '{cell['text']}' "
                                               f"(p.{cell['page']}); class {sub} used"})
                    c["class"] = sub
                c["row_widths"] = [float(x) for x in ROW_WIDTHS.findall(cell["text"])]
        if not c["row_widths"]:
            for r in t["rows"]:
                cell = r["cells"][i] if i < len(r["cells"]) else None
                if r["criterion"] == "row" and cell:
                    c["row_widths"] = numbers(cell["text"])


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
    tables, warnings = [], []
    for p in pages:
        lines = lines_of(p.get("spans", []))
        for i, ln in enumerate(lines):
            hdr = header_of(ln)
            if not hdr:
                continue
            rows, cols = read_rows(lines, i, hdr, p["page"])
            heads = [h["heading"] for h in hdr["headings"]]
            if tables and tables[-1]["header"] == heads and tables[-1]["pages"][-1] == p["page"] - 1 \
                    and len(tables[-1]["columns"]) == len(cols):
                tables[-1]["rows"] += rows
                tables[-1]["pages"].append(p["page"])
            else:
                tables.append({"header": heads, "pages": [p["page"]], "rows": rows,
                               "columns": [{"heading": c["heading"], "class": c["class"], "centre": c["centre"]} for c in cols]})
    criteria, columns = {}, []
    for ti, t in enumerate(tables):
        finish_columns(t, warnings)
        t["classes"] = [c["class"] for c in t["columns"]]
        t["headings"] = [c["heading"] for c in t["columns"]]
        for ci, c in enumerate(t["columns"]):
            vals = {}
            for r in t["rows"]:
                cell = r["cells"][ci] if ci < len(r["cells"]) else None
                if not r["criterion"] or not cell:
                    continue
                entry = {"text": cell["text"], "numbers": numbers(cell["text"]), "page": cell["page"],
                         "bbox": cell["bbox"], "label": r["label"], "label_bbox": r["label_bbox"]}
                if r["criterion"] == "functional_class":
                    entry["class_check"] = class_of(cell["text"])
                if r["criterion"] in vals:
                    warnings.append({"code": "CRITERION_REPEATED",
                                     "detail": f"{c['class']} {r['criterion']} on p.{cell['page']}; first kept"})
                    continue
                vals[r["criterion"]] = entry
            col = {"table": ti, "column": ci, "class": c["class"], "heading": c["heading"],
                   "sub_heading": c["sub_heading"], "row_widths": c["row_widths"], "criteria": vals}
            columns.append(col)
            if not vals:
                continue
            if c["class"] in criteria:
                warnings.append({"code": "CLASS_COLUMNS_REPEATED",
                                 "detail": f"class {c['class']} has another column ('{c['sub_heading'] or c['heading']}', "
                                           f"ROW {c['row_widths']}); kept in `columns`, the first column stays in `criteria`"})
                continue
            criteria[c["class"]] = vals
    if not tables:
        warnings.append({"code": "NO_CRITERIA_TABLE", "detail": "no 'Parameter' header with two or more road-class columns found"})
    elif not criteria:
        warnings.append({"code": "CRITERIA_TABLE_UNREAD",
                         "detail": "a criteria table was found (p." + ", ".join(str(p) for t in tables for p in t["pages"])
                                   + ") but no criterion was read; the C2 check is not run as missing"})
    pdf = [p["page"] for p in pages]
    return {"tool": {"script": "read_criteria.py", "version": __version__}, "source": source_of(ing),
            "pages_searched": [min(pdf), max(pdf)] if pdf else [], "tables": tables, "criteria": criteria,
            "columns": columns, "departures": departures(pages), "warnings": warnings}


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
              "classes": {k: len(v) for k, v in res["criteria"].items()}, "columns": len(res["columns"]), "departures": len(res["departures"]),
              "warnings": len(res["warnings"])})
        return EXIT_OK
    return main_guard(run)


if __name__ == "__main__":
    sys.exit(main())
