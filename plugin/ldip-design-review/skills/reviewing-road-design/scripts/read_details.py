#!/usr/bin/env python3
"""Detail-sheet reader for phase C3 (SPC-0006 Rev J §4.13G.4, DJ-3).

Finds detail titles (SPEED HUMP / SPEED TABLE / RAISED CROSSING / RAISED INTERSECTION), takes the band
above the title up to the next title in the same column (corrected QRWN section rule), and reads length,
height, flat-top and ramp values — labelled when a label sits next to the value, otherwise positional
(positional → Probable).

Usage: python read_details.py --file details=<a.dwg.json|a.pdf.json> [...] --out <details.json>
Exit: 0 written · 2 input error. No network, no shell calls.
"""
__version__ = "0.1.0"

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from c3_common import DETAIL_TERMS, VALUE_LABELS, parse_len_height, texts_of  # noqa: E402
from road_common import EXIT_INPUT, EXIT_OK, RoadError, atomic_write_json, emit, main_guard, read_json, source_of, term_hits  # noqa: E402

TITLE_KIND = [
    ("ROD-044", "speed_hump", DETAIL_TERMS["ROD-044"]),
    ("ROD-045", "speed_table", DETAIL_TERMS["ROD-045"]),
    ("ROD-046", "raised_intersection", DETAIL_TERMS["ROD-046"]),
]


def title_kind(text):
    for rid, kind, terms in TITLE_KIND:
        if term_hits(text, terms):
            return rid, kind
    return None, None


def label_kind(text):
    for kind, terms in VALUE_LABELS.items():
        if term_hits(text, terms):
            return kind
    return None


def bands_of(ing):
    texts = texts_of(ing)
    titles = []
    for t in texts:
        rid, kind = title_kind(t["text"])
        if rid:
            titles.append({"text": t, "rule_id": rid, "kind": kind, "x": t["insert"][0], "y": t["insert"][1]})
    titles.sort(key=lambda t: (-t["y"], t["x"]))
    bands = []
    for i, tit in enumerate(titles):
        # same column: x within 40 drawing units; band above title up to next title above
        above = [o for o in titles if o is not tit and abs(o["x"] - tit["x"]) < 40 and o["y"] > tit["y"] + 0.5]
        y_top = min(above, key=lambda o: o["y"])["y"] - 0.5 if above else tit["y"] + 80
        x0, x1 = tit["x"] - 60, tit["x"] + 60
        inside = [t for t in texts if x0 <= t["insert"][0] <= x1 and tit["y"] < t["insert"][1] <= y_top]
        # also include values on the same line as the title (slightly below/above)
        inside += [t for t in texts if t not in inside and x0 <= t["insert"][0] <= x1
                   and abs(t["insert"][1] - tit["y"]) < 8 and t is not tit["text"]]
        values, unused = [], []
        labels = [(label_kind(t["text"]), t) for t in inside if label_kind(t["text"])]
        parsed = []
        for t in inside:
            for p in parse_len_height(t["text"]):
                parsed.append((t, p))
        for t, p in parsed:
            kind = p["kind"]
            assigned = "positional"
            # nearest label within a tight box; prefer a label matching the parsed kind
            cands = []
            for lk, lab in labels:
                dx = abs(lab["insert"][0] - t["insert"][0])
                dy = abs(lab["insert"][1] - t["insert"][1])
                if dx < 15 and dy < 12:
                    cands.append((dx + dy, 0 if lk == p["kind"] else 1, lk))
            if cands:
                cands.sort()
                kind = cands[0][2]
                assigned = "labelled"
            values.append({"kind": kind, "value": p.get("value"), "ratio": p.get("ratio"),
                           "unit": p.get("unit"), "raw": p["raw"], "assigned": assigned,
                           "handle": t.get("handle"), "layer": t.get("layer"),
                           "xy": [t["insert"][0], t["insert"][1]]})
        # positional assignment when several lengths: longest = overall, next = flat top for tables
        lengths = [v for v in values if v["kind"] == "length" and v["assigned"] == "positional"]
        if len(lengths) >= 2:
            lengths.sort(key=lambda v: -float(v["value"]))
            lengths[0]["kind"] = "length"
            lengths[1]["kind"] = "flat_top"
        bands.append({"rule_id": tit["rule_id"], "kind": tit["kind"], "title": tit["text"]["text"].strip(),
                      "title_handle": tit["text"].get("handle"), "xy": [tit["x"], tit["y"]],
                      "values": values, "unused_units": unused})
    return bands


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", action="append", required=True, help="details=<ingestion.json>")
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)

    def run():
        details, sources = [], []
        for item in a.file:
            if "=" not in item:
                raise RoadError(EXIT_INPUT, f"--file needs carrier=path, got {item!r}")
            carrier, path = item.split("=", 1)
            if carrier.strip() != "details":
                raise RoadError(EXIT_INPUT, f"read_details.py expects carrier 'details', got {carrier!r}")
            ing = read_json(path)
            src = source_of(ing)
            ft = src.get("file_type")
            if ft == "pdf":
                src = {**src, "source_type": "pdf_vector_text"}
            else:
                src = {**src, "source_type": "dwg_vector"}
            sources.append(src)
            for b in bands_of(ing):
                details.append({**b, "source": src, "path": src.get("path")})
        atomic_write_json(a.out, {"tool": {"script": "read_details.py", "version": __version__},
                                  "mode": "details", "details": details, "sources": sources})
        emit({"status": "ok", "out": a.out, "details": len(details),
              "by_kind": {k: sum(1 for d in details if d["kind"] == k)
                          for k in sorted({d["kind"] for d in details})}})
        return EXIT_OK
    return main_guard(run)


if __name__ == "__main__":
    sys.exit(main())
