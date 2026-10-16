#!/usr/bin/env python3
"""Setting-out tables -> named alignments (SPC-0006 Rev F §4.13C.2; K19-1, DF-4).

Reads the text records of skill 1 DWG ingestion files (the setting-out sheets, Civil 3D alignment tables).
A table is a header row (TAG ID / CURVE NAME / CONTROL POINT / STATION / NORTHING / EASTING / RADIUS / DELTA /
L / T / E / AHEAD DIRECTION) under a title cell "ALIGNMENT NAME: <name>". Columns are taken from the header
cell positions of each table (DF-4), never from fixed offsets; a header cell that skill 1 could not decode
(K18) is placed by its position between the decoded ones. Rows are the tag cells `L<n>` (tangent) and
`C-<n>` / `C<n>` (curve) below the header; each row keeps control points, stations, N / E (as printed,
several lines per cell), radius, delta and length, with the handles of its cells. A row belongs to the
nearest header above it in its column block; a header belongs to the nearest title above it whose position
lies within the header's span. Sheets that repeat the same table (identical rows) are de-duplicated (DF-4):
the alignment keeps the list of sheets; a repeated name with different rows gives SETOUT_SHEETS_DIFFER and
the first sheet's rows are kept.

Usage: python read_setout.py --ingestion <sheet1.dwg.json> [...] --out <setout.json>
Exit: 0 written (also with no table: alignments = {}) · 2 input error. No network, no shell calls.
"""
__version__ = "0.1.0"

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from road_common import EXIT_INPUT, EXIT_OK, RoadError, atomic_write_json, emit, main_guard, read_json, source_of  # noqa: E402

TITLE = re.compile(r"^\s*ALIGNMENT\s+NAME\s*:\s*(.+?)\s*$", re.I | re.S)
TAG = re.compile(r"^(L\d+|C-?\d+)$")
HEAD = [("tag", r"\bTAG\s*ID\b"), ("curve", r"\bCURVE\s*NAME\b"), ("control", r"\bCONTROL\b"),
        ("station", r"\bSTATION\b"), ("northing", r"\bNORTHING\b"), ("easting", r"\bEASTING\b"),
        ("radius", r"\bRADIUS\b"), ("delta", r"\bDELTA\b"), ("length", r"^\s*L\s*(\(m\))?\s*$"),
        ("tangent", r"^\s*T\s*(\(m\))?\s*$"), ("external", r"^\s*E\s*(\(m\))?\s*$"), ("direction", r"\bAHEAD\b")]
ORDER = ["tag", "curve", "control", "station", "northing", "easting", "radius", "delta", "length", "tangent",
         "external", "direction"]
COORD = re.compile(r"-?\d{5,8}\.\d+")
NUMBER = re.compile(r"-?\d+(?:\.\d+)?")
STATION = re.compile(r"\d+\+\d+(?:\.\d+)?")
ROW_TOL_FACTOR = 0.8          # x text height: cells of one row
HEADER_TOL_FACTOR = 1.0


def clean(t):
    return "\n".join(line.strip() for line in str(t or "").replace("\\P", "\n").splitlines())


def label_of(text):
    t = re.sub(r"[^\x20-\x7e\n]", "", clean(text)).replace("\n", " ")
    for key, pat in HEAD:
        if re.search(pat, t, re.I):
            return key
    return None


def headers(texts):
    """Header groups: header cells on one row, split at each TAG ID / CURVE NAME that starts a table."""
    cells = [t for t in texts if t["_h"] is not None and label_of(t["text"]) is not None]
    rows = []
    for c in sorted(cells, key=lambda c: (-c["insert"][1], c["insert"][0])):
        tol = HEADER_TOL_FACTOR * (c.get("height") or 3.0) * 1.5
        if rows and abs(rows[-1][0] - c["insert"][1]) <= tol:
            rows[-1][1].append(c)
        else:
            rows.append([c["insert"][1], [c]])
    groups = []
    for y, cs in rows:
        cs.sort(key=lambda c: c["insert"][0])
        cur = None
        for c in cs:
            k = label_of(c["text"])
            starts = k == "tag" or (k == "curve" and (cur is None or "curve" in cur["cols"] or "control" in cur["cols"]))
            if cur is None or (starts and ("control" in cur["cols"] or "station" in cur["cols"])):
                cur = {"y": y, "cols": {}, "cells": [], "height": c.get("height") or 3.0}
                groups.append(cur)
            if k not in cur["cols"]:
                cur["cols"][k] = c["insert"][0]
            cur["cells"].append(c)
    out = []
    for g in groups:
        if "control" not in g["cols"] or not ({"northing", "easting"} & set(g["cols"])):
            continue
        # undecoded header cells (K18): northing between station and easting
        if "northing" not in g["cols"] and "station" in g["cols"] and "easting" in g["cols"]:
            between = [c for c in g["cells"] if label_of(c["text"]) is None
                       and g["cols"]["station"] < c["insert"][0] < g["cols"]["easting"]]
            g["cols"]["northing"] = between[0]["insert"][0] if between else (g["cols"]["station"] + g["cols"]["easting"]) / 2
        if "tag" not in g["cols"]:
            first = min(g["cols"].values())
            g["cols"]["tag"] = first - (sorted(g["cols"].values())[1] - first)
        xs = sorted(g["cols"].values())
        g["x0"], g["x1"] = xs[0], xs[-1]
        g["pitch"] = min(b - a for a, b in zip(xs, xs[1:])) if len(xs) > 1 else 25.0
        out.append(g)
    return out


def nearest_col(x, cols, pitch):
    k = min(cols, key=lambda k: abs(cols[k] - x))
    return k if abs(cols[k] - x) <= 0.6 * pitch else None


def read_sheet(ing):
    texts = [dict(t) for t in (ing.get("content") or {}).get("texts", []) if t.get("space") == "model" and t.get("insert")]
    for t in texts:
        t["_h"] = True
        t["text"] = clean(t["text"])
    titles = []
    for t in texts:
        m = TITLE.match(t["text"])
        if m:
            titles.append({"name": re.sub(r"\s+", " ", m.group(1)).strip(), "x": t["insert"][0], "y": t["insert"][1],
                           "handle": t.get("handle")})
    hdrs = headers(texts)
    for h in hdrs:
        above = [ti for ti in titles if 0 < ti["y"] - h["y"] <= 12 * h["height"]
                 and h["x0"] - 2 * h["pitch"] <= ti["x"] <= h["x1"] + h["pitch"]]
        h["title"] = min(above, key=lambda ti: ti["y"] - h["y"]) if above else None
    tags = [t for t in texts if TAG.match(t["text"].strip())]
    rows = {}
    warnings = []
    for t in tags:
        x, y = t["insert"][0], t["insert"][1]
        cands = [h for h in hdrs if h["y"] > y and h["cols"]["tag"] - 0.6 * h["pitch"] <= x
                 <= h["cols"]["tag"] + 0.6 * h["pitch"]]
        if not cands:
            continue
        h = min(cands, key=lambda h: h["y"] - y)
        if h["title"] is None:
            continue
        tol = ROW_TOL_FACTOR * (t.get("height") or 2.5) * 1.6
        cells = {}
        handles = [t.get("handle")]
        for c in texts:
            if c is t or abs(c["insert"][1] - y) > tol or not (h["x0"] - h["pitch"] <= c["insert"][0] <= h["x1"] + h["pitch"]):
                continue
            k = nearest_col(c["insert"][0], h["cols"], h["pitch"])
            if k and k != "tag" and k not in cells:
                cells[k] = c["text"]
                handles.append(c.get("handle"))
        tag = t["text"].strip()
        control = [s.strip() for s in cells.get("control", "").split("\n") if s.strip()]
        north = [float(v) for v in COORD.findall(cells.get("northing", ""))]
        east = [float(v) for v in COORD.findall(cells.get("easting", ""))]
        if len(north) != len(east) or len(north) < 2:
            warnings.append({"code": "SETOUT_ROW_UNREAD",
                             "detail": f"{h['title']['name']} {tag}: {len(north)} northing / {len(east)} easting values"})
            continue
        num = lambda k: [float(v) for v in NUMBER.findall(cells.get(k, "").replace(",", ""))]  # noqa: E731
        seg = {"tag": tag, "type": "tangent" if tag.startswith("L") else "curve", "control": control,
               "stations": STATION.findall(cells.get("station", "")),
               "points": [[e, n] for e, n in zip(east, north)],
               "radius": (num("radius") or [None])[0] if tag.startswith("C") else None,
               "delta": cells.get("delta", "").replace("\n", " ").strip() or None,
               "length": (num("length") or [None])[0], "handles": [x for x in handles if x], "y": round(y, 3)}
        rows.setdefault(h["title"]["name"], {"title_handle": h["title"]["handle"], "segments": []})["segments"].append(seg)
    for v in rows.values():
        v["segments"].sort(key=lambda s: (-s["y"], s["tag"]))
        for s in v["segments"]:
            s.pop("y")
    return rows, warnings, len(hdrs), len(titles)


def signature(segs):
    return [(s["tag"], tuple(tuple(round(c, 3) for c in p) for p in s["points"]), s["radius"]) for s in segs]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--ingestion", nargs="+", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)

    def run():
        alignments, warnings, sources = {}, [], []
        for p in a.ingestion:
            ing = read_json(p)
            if (ing.get("source") or {}).get("file_type") != "dwg":
                raise RoadError(EXIT_INPUT, f"{p}: not a DWG ingestion file (skill 1)")
            src = source_of(ing)
            rows, w, nh, nt = read_sheet(ing)
            warnings += w
            sources.append({**src, "tables": len(rows), "headers": nh, "titles": nt})
            for name, v in rows.items():
                if name not in alignments:
                    alignments[name] = {"segments": v["segments"], "sheets": [src["path"]],
                                        "evidence": {"path": src["path"], "sha256": src["sha256"],
                                                     "title_handle": v["title_handle"]}}
                elif signature(alignments[name]["segments"]) == signature(v["segments"]):
                    alignments[name]["sheets"].append(src["path"])
                else:
                    warnings.append({"code": "SETOUT_SHEETS_DIFFER",
                                     "detail": f"{name}: rows on {Path(src['path']).name} differ from "
                                               f"{Path(alignments[name]['sheets'][0]).name}; first kept"})
        segs = [s for v in alignments.values() for s in v["segments"]]
        out = {"tool": {"script": "read_setout.py", "version": __version__}, "sources": sources,
               "alignments": dict(sorted(alignments.items())), "warnings": warnings,
               "counts": {"alignments": len(alignments), "segments": len(segs),
                          "tangents": sum(1 for s in segs if s["type"] == "tangent"),
                          "curves": sum(1 for s in segs if s["type"] == "curve")}}
        atomic_write_json(a.out, out)
        emit({"status": "ok", "out": a.out, **out["counts"], "sheets": len(sources), "warnings": len(warnings)})
        return EXIT_OK
    return main_guard(run)


if __name__ == "__main__":
    sys.exit(main())
