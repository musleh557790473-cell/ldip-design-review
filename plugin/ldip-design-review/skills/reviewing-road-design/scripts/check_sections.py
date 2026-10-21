#!/usr/bin/env python3
"""Typical-section check from DWG ingestion files (skill 1) — SPC-0006 Rev D §4.13.7.

Skill 1 records text with its insert point but dimensions without position, so sections are read from
text: each section is the band between a pair of vertical "RIGHT OF WAY" labels; its title is the
road-class text just below the band (road_common.class_of; ROW from "<n> m" / "<n> METER"); values
are the numeric texts inside the band. A value is assigned to an element by the label under it
("DRIVING LANE", "SIDE WALK", "MEDIAN") — `labelled`. Without a label: the "(RIGHT OF WAY)" text or
the largest value that equals the title width is the ROW (labelled: two carriers agree); the outermost
value on each side is the sidewalk and inner values of 2.5-4.0 m are lanes — `positional`, which caps the
finding at Probable (D10-3). A value equal to a dimension measurement of the drawing is flagged
`dimension_match` (information). Percent values between the lanes are the carriageway crossfall.

0.2.0 (SPC-0006 Rev I §4.13F.6, K25 / DI-8): a second section anchor, used only for a file in which no
"RIGHT OF WAY" pair is found (QRWN layout): each class title text opens a section; its band is the text
area from the title up to the next title above it in the same column, between the "R.O.W." marks of the
band (else a fixed width around the title). Without element labels, a value on the centre line (x of the
ROW value) is the median (positional). Widths written "30.00 (R.O.W.)" count as ROW labels. Zero-height
ghost texts of nested blocks (height < 0.05) and duplicate texts are dropped. A file with "RIGHT OF WAY"
pairs (NMAR) is read exactly as in 0.1.x.

Rules: ROD-002 / 003 / 004 (ROW), 010 / 011 / 012 (lanes, incl. tier-2 observation), 014 / 016
(median), 021 (crossfall), 048 (sidewalk: land use not known -> only the lowest required width is
enforced; a width between the lowest and the highest is not run), 057 (curb height from
"CURB (150 mm)" text), 059 (parking lane width — 0.3.0, SPC-0006 Rev J §4.13G.9).

0.3.0 (SPC-0006 Rev J §4.13F.6 [AGREED] 18 Oct 2026 14:09, §4.13G.9; rebuilt on build-027 C1+C2):
- element `parking` from labels PARKING / PARKING LANE / PARALLEL PARKING (ROD-059);
  "WITHOUT PARALLEL PARKING" in the title → no parking element;
- pair-anchor marks = rotated "RIGHT OF WAY" only (as 0.2.0); "R.O.W." / "nn.nn (R.O.W.)"
  stay with the K25 title anchor (QRWN), so they do not form false pair bands;
- K25 title-anchor fallback when no "RIGHT OF WAY" pairs are found.

Usage: python check_sections.py --sections <a.dwg.json> [...] --rules <rules_RDE.json> --out <section_checks.json>
Exit: 0 written · 2 input error. No network, no shell calls.
"""
__version__ = "0.3.0"

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from road_common import (EXIT_INPUT, EXIT_OK, RoadError, applies, atomic_write_json, class_of, emit,  # noqa: E402
                         main_guard, param, read_json, rules_index, source_of, tol)

VALUE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*(%)?\s*(\((?:RIGHT OF WAY|R\.?O\.?W\.?)\))?\s*$", re.I)
TITLE_W = re.compile(r"(\d+(?:\.\d+)?)\s*(?:m\b|meter|metre)", re.I)
CURB = re.compile(r"curb\s*\(\s*(\d+(?:\.\d+)?)\s*mm\s*\)", re.I)
ROW_MARK = re.compile(r"^(?:RIGHT OF WAY|R\.?O\.?W\.?)$", re.I)
ROW_VALUE_MARK = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*\(\s*R\.?O\.?W\.?\s*\)\s*$", re.I)
NO_PARKING = re.compile(r"without\s+parallel\s+parking", re.I)
GHOST_H = 0.05          # K25: texts of zero-scale nested blocks
TITLE_MIN_H = 0.1
COLUMN_DX = 30.0        # titles closer than this in x share a column
BAND_TOP_M = 15.0       # band height above the top title of a column
BAND_X = (-15.0, 35.0)  # band x extent around the title when the band has fewer than two R.O.W. marks
LABELS = {"DRIVING LANE": "lane", "SIDE WALK": "sidewalk", "SIDEWALK": "sidewalk", "MEDIAN": "median",
          "PARKING": "parking", "PARKING LANE": "parking", "PARALLEL PARKING": "parking"}
CHECKS = {
    "row": [("ROD-002", "row_min", "ge"), ("ROD-002", "row_max", "le"), ("ROD-003", "row_min", "ge"),
            ("ROD-003", "row_max", "le"), ("ROD-004", "row_min", "ge"), ("ROD-004", "row_max", "le")],
    "lane": [("ROD-010", "lane_min", "ge"), ("ROD-010", "lane_upper", "le"), ("ROD-010", "tier2_lane", "ge"),
             ("ROD-011", "lane_min", "ge"), ("ROD-011", "lane_upper", "le"), ("ROD-011", "tier2_lane", "ge"),
             ("ROD-012", "lane_min", "ge"), ("ROD-012", "tier2_lane", "ge")],
    "median": [("ROD-014", "median_min", "ge"), ("ROD-014", "tier2_median_shc", "ge"),
               ("ROD-014", "tier2_median_momra", "ge"), ("ROD-016", "separator_min", "ge")],
    "crossfall": [("ROD-021", "crossfall", "eq")],
    "curb": [("ROD-057", "height", "eq")],
    "parking": [("ROD-059", "width", "eq"), ("ROD-059", "width_min", "ge")],
}


def xy(t):
    return t["insert"][0], t["insert"][1]


def compare(obs, req, op, t):
    if op == "ge":
        return obs >= req - t - 1e-9
    if op == "le":
        return obs <= req + t + 1e-9
    return abs(obs - req) <= t + 1e-9


def _is_row_mark(t):
    s = t["text"].strip()
    if ROW_MARK.match(s):
        return True
    if ROW_VALUE_MARK.match(s):
        return True
    return s.upper() == "RIGHT OF WAY"


def sections_of(ing):
    texts = [t for t in (ing.get("content") or {}).get("texts", [])
             if t.get("space", "model") == "model" and isinstance(t.get("text"), str) and t.get("insert")
             and str(t.get("text")).strip() and float(t.get("height") or 1) > 0]
    # drop exact duplicate texts at the same point (Rev J §4.13F.6)
    seen_txt, uniq = set(), []
    for t in texts:
        key = (round(xy(t)[0], 3), round(xy(t)[1], 3), t["text"].strip())
        if key in seen_txt:
            continue
        seen_txt.add(key)
        uniq.append(t)
    texts = uniq
    dims = {round(float(d["measurement"]), 2) for d in (ing.get("content") or {}).get("dimensions", [])
            if isinstance(d.get("measurement"), (int, float))}
    # pair anchor (NMAR): rotated "RIGHT OF WAY" only, as 0.2.0. "R.O.W." marks belong to the K25 title anchor.
    marks = [t for t in texts if t["text"].strip().upper() == "RIGHT OF WAY"
             and abs(abs(float(t.get("rotation") or 0)) % 180 - 90) < 5]
    bands, used = [], set()
    for m in sorted(marks, key=lambda t: (round(xy(t)[1], 0), xy(t)[0])):
        if m["handle"] in used:
            continue
        partners = [o for o in marks if o["handle"] not in used and o is not m
                    and abs(xy(o)[1] - xy(m)[1]) < 1.5 and xy(o)[0] > xy(m)[0]]
        if not partners:
            continue
        o = min(partners, key=lambda t: xy(t)[0])
        used |= {m["handle"], o["handle"]}
        bands.append({"left": xy(m)[0], "right": xy(o)[0], "y": (xy(m)[1] + xy(o)[1]) / 2, "marks": [m, o]})
    out = []
    for b in bands:
        # title below the band (legacy) or title below with section above (QRWN / Rev J §4.13F.6)
        below = [t for t in texts if class_of(t["text"]) and "ROAD" in t["text"].upper()
                 and b["left"] - 3 <= xy(t)[0] <= b["right"] + 3 and b["y"] - 8 <= xy(t)[1] < b["y"] - 0.5]
        above_titles = [t for t in texts if class_of(t["text"]) and "ROAD" in t["text"].upper()
                        and b["left"] - 3 <= xy(t)[0] <= b["right"] + 3 and b["y"] + 0.5 < xy(t)[1] <= b["y"] + 40]
        title = None
        y_lo, y_hi = b["y"] - 8, b["y"] + 6
        if below:
            title = max(below, key=lambda t: xy(t)[1])
            y_lo = xy(title)[1]
        elif above_titles:
            # band-above-title: the title is below the marks; find title under the band
            under = [t for t in texts if class_of(t["text"])
                     and b["left"] - 3 <= xy(t)[0] <= b["right"] + 3 and xy(t)[1] < b["y"] - 0.5]
            title = max(under, key=lambda t: xy(t)[1]) if under else None
            if title:
                # next title above in the same column bounds the top
                col = [t for t in texts if class_of(t["text"]) and abs(xy(t)[0] - xy(title)[0]) < 40
                       and xy(t)[1] > xy(title)[1] + 0.5]
                y_hi = min(xy(t)[1] for t in col) - 0.5 if col else b["y"] + 6
                y_lo = xy(title)[1]
        inside = [t for t in texts if b["left"] - 0.5 <= xy(t)[0] <= b["right"] + 0.5 and y_lo <= xy(t)[1] <= y_hi]
        if title and NO_PARKING.search(title["text"]):
            labels = [(LABELS[t["text"].strip().upper()], t) for t in inside
                      if t["text"].strip().upper() in LABELS and LABELS[t["text"].strip().upper()] != "parking"]
        else:
            labels = [(LABELS[t["text"].strip().upper()], t) for t in inside if t["text"].strip().upper() in LABELS]
        values = []
        for t in inside:
            rm = ROW_VALUE_MARK.match(t["text"].strip())
            if rm:
                values.append({"value": float(rm.group(1)), "pct": False, "row_label": True, "text": t,
                               "element": None, "assigned": None,
                               "dimension_match": round(float(rm.group(1)), 2) in dims})
                continue
            m = VALUE.match(t["text"])
            if not m:
                continue
            v = {"value": float(m.group(1)), "pct": bool(m.group(2)), "row_label": bool(m.group(3)), "text": t,
                 "element": None, "assigned": None, "dimension_match": round(float(m.group(1)), 2) in dims}
            for kind, lab in labels:
                if abs(xy(lab)[0] - xy(t)[0]) < 0.8 and 0 < xy(t)[1] - xy(lab)[1] < 1.0:
                    v["element"], v["assigned"] = kind, "labelled"
            values.append(v)
        tw = TITLE_W.search(title["text"]) if title else None
        title_w = float(tw.group(1)) if tw else None
        lengths = [v for v in values if not v["pct"]]
        row = [v for v in lengths if v["row_label"]]
        if not row and lengths:
            big = max(lengths, key=lambda v: v["value"])
            if big["value"] >= 10:
                row = [big]
        for v in row:
            v["element"] = "row"
            v["assigned"] = "labelled" if (v["row_label"] or (title_w is not None and abs(v["value"] - title_w) < 0.01)) else "positional"
        free = [v for v in lengths if v["element"] is None]
        if free and not any(v["element"] == "sidewalk" for v in values):
            for side in (min(free, key=lambda v: xy(v["text"])[0]), max(free, key=lambda v: xy(v["text"])[0])):
                if side["value"] >= 1.0 and side["element"] is None:
                    side["element"], side["assigned"] = "sidewalk", "positional"
        if not any(v["element"] == "lane" for v in values):
            for v in lengths:
                if v["element"] is None and 2.5 <= v["value"] <= 4.0:
                    v["element"], v["assigned"] = "lane", "positional"
        # centre-line value → median when unlabelled (Rev J §4.13F.6)
        if not any(v["element"] == "median" for v in values) and not labels:
            mid_x = (b["left"] + b["right"]) / 2
            for v in lengths:
                if v["element"] is None and abs(xy(v["text"])[0] - mid_x) < 1.0 and 0.5 <= v["value"] <= 8:
                    v["element"], v["assigned"] = "median", "positional"
        lanes = [v for v in values if v["element"] == "lane"]
        if lanes:
            lo = min(xy(v["text"])[0] for v in lanes) - 0.5
            hi = max(xy(v["text"])[0] for v in lanes) + 0.5
            for v in values:
                if v["pct"] and lo <= xy(v["text"])[0] <= hi:
                    v["element"], v["assigned"] = "crossfall", "positional"
        curbs = [t for t in inside if CURB.search(t["text"])]
        for t in curbs:
            values.append({"value": float(CURB.search(t["text"]).group(1)), "pct": False, "row_label": False, "text": t,
                           "element": "curb", "assigned": "labelled", "dimension_match": False})
        out.append({"title": title["text"].replace("\n", " ") if title else None,
                    "title_handle": title["handle"] if title else None, "class": class_of(title["text"]) if title else None,
                    "title_width_m": title_w, "values": values})
    if not bands:                                   # K25 (Rev I §4.13F.6): QRWN layout, title-anchored bands
        for title, inside in title_bands(texts):
            s = analyse(title, inside, dims, centre_median=True)
            s["anchor"] = "title"
            out.append(s)
    return out


def dedupe(texts):
    seen, out = set(), []
    for t in texts:
        k = (t["text"].strip(), round(xy(t)[0], 2), round(xy(t)[1], 2))
        if k not in seen:
            seen.add(k)
            out.append(t)
    return out


def title_bands(texts):
    """K25: [(title, texts of its band)] — a band runs from the title up to the next title above it in the same
    column; its x extent is given by the R.O.W. marks inside it (else BAND_X around the title)."""
    vis = dedupe([t for t in texts if t.get("height") is None or float(t["height"]) >= GHOST_H])
    titles = [t for t in vis if class_of(t["text"]) and "ROAD" in t["text"].upper() and TITLE_W.search(t["text"])
              and (t.get("height") is None or float(t["height"]) >= TITLE_MIN_H)]
    out = []
    for t in sorted(titles, key=lambda t: (-xy(t)[1], xy(t)[0])):
        x0, y0 = xy(t)
        above = [xy(o)[1] for o in titles if xy(o)[1] > y0 + 0.5 and abs(xy(o)[0] - x0) < COLUMN_DX]
        top = min(above) - 0.3 if above else y0 + BAND_TOP_M
        band = [o for o in vis if y0 <= xy(o)[1] < top and abs(xy(o)[0] - x0) < COLUMN_DX + 20]
        marks = [o for o in band if ROW_MARK.match(o["text"].strip())]
        if len(marks) >= 2:
            lo, hi = min(xy(o)[0] for o in marks) - 1.0, max(xy(o)[0] for o in marks) + 1.0
        else:
            lo, hi = x0 + BAND_X[0], x0 + BAND_X[1]
        out.append((t, [o for o in band if lo <= xy(o)[0] <= hi]))
    return out


def analyse(title, inside, dims, centre_median=False):
    """Element values of one section band (K25 title anchor). centre_median: without element labels, a value
    on the centre line (x of the ROW value) is the median, positional. Parking labels honor NO_PARKING."""
    if title and NO_PARKING.search(title["text"]):
        labels = [(LABELS[t["text"].strip().upper()], t) for t in inside
                  if t["text"].strip().upper() in LABELS and LABELS[t["text"].strip().upper()] != "parking"]
    else:
        labels = [(LABELS[t["text"].strip().upper()], t) for t in inside if t["text"].strip().upper() in LABELS]
    values = []
    for t in inside:
        rm = ROW_VALUE_MARK.match(t["text"].strip())
        if rm:
            values.append({"value": float(rm.group(1)), "pct": False, "row_label": True, "text": t,
                           "element": None, "assigned": None,
                           "dimension_match": round(float(rm.group(1)), 2) in dims})
            continue
        m = VALUE.match(t["text"])
        if not m:
            continue
        v = {"value": float(m.group(1)), "pct": bool(m.group(2)), "row_label": bool(m.group(3)), "text": t,
             "element": None, "assigned": None, "dimension_match": round(float(m.group(1)), 2) in dims}
        for kind, lab in labels:
            if abs(xy(lab)[0] - xy(t)[0]) < 0.8 and 0 < xy(t)[1] - xy(lab)[1] < 1.0:
                v["element"], v["assigned"] = kind, "labelled"
        values.append(v)
    tw = TITLE_W.search(title["text"]) if title else None
    title_w = float(tw.group(1)) if tw else None
    lengths = [v for v in values if not v["pct"]]
    row = [v for v in lengths if v["row_label"]]
    if not row and lengths:
        big = max(lengths, key=lambda v: v["value"])
        if big["value"] >= 10:
            row = [big]
    for v in row:
        v["element"] = "row"
        v["assigned"] = "labelled" if (v["row_label"] or (title_w is not None and abs(v["value"] - title_w) < 0.01)) else "positional"
    free = [v for v in lengths if v["element"] is None]
    if free and not any(v["element"] == "sidewalk" for v in values):
        for side in (min(free, key=lambda v: xy(v["text"])[0]), max(free, key=lambda v: xy(v["text"])[0])):
            if side["value"] >= 1.0 and side["element"] is None:
                side["element"], side["assigned"] = "sidewalk", "positional"
    if centre_median and row and not any(v["element"] == "median" for v in values):
        cx = xy(row[0]["text"])[0]
        for v in lengths:
            if v["element"] is None and v["value"] >= 1.0 and abs(xy(v["text"])[0] - cx) < 0.5:
                v["element"], v["assigned"] = "median", "positional"
    if not any(v["element"] == "lane" for v in values):
        for v in lengths:
            if v["element"] is None and 2.5 <= v["value"] <= 4.0:
                v["element"], v["assigned"] = "lane", "positional"
    lanes = [v for v in values if v["element"] == "lane"]
    if lanes:
        lo = min(xy(v["text"])[0] for v in lanes) - 0.5
        hi = max(xy(v["text"])[0] for v in lanes) + 0.5
        for v in values:
            if v["pct"] and lo <= xy(v["text"])[0] <= hi:
                v["element"], v["assigned"] = "crossfall", "positional"
    curbs = [t for t in inside if CURB.search(t["text"])]
    for t in curbs:
        values.append({"value": float(CURB.search(t["text"]).group(1)), "pct": False, "row_label": False, "text": t,
                       "element": "curb", "assigned": "labelled", "dimension_match": False})
    return {"title": title["text"].replace("\n", " ") if title else None,
            "title_handle": title["handle"] if title else None, "class": class_of(title["text"]) if title else None,
            "title_width_m": title_w, "values": values}


def run_checks(ings, rules):
    checks, not_run, sections = [], [], []
    for ing in ings:
        src = source_of(ing)
        for s in sections_of(ing):
            sections.append({"source": src["path"], "title": s["title"], "class": s["class"],
                             "title_width_m": s["title_width_m"], **({"anchor": s["anchor"]} if s.get("anchor") else {}),
                             "elements": [{"element": v["element"], "value": v["value"], "assigned": v["assigned"],
                                           "handle": v["text"]["handle"], "dimension_match": v["dimension_match"]}
                                          for v in s["values"] if v["element"]]})
            cls = s["class"]
            if cls is None:
                not_run.append({"rule_id": None, "reason": f"section without a recognised class title: {s['title']}"})
                continue
            if s["title_width_m"] is not None and not any(v["element"] == "row" for v in s["values"]):
                not_run.append({"rule_id": "ROD-002/003/004", "reason": f"no ROW value in section {s['title']}"})
            seen = set()
            for v in s["values"]:
                el = v["element"]
                if el == "sidewalk":
                    checks += sidewalk(v, s, cls, rules, src, not_run)
                    continue
                for rid, pname, op in CHECKS.get(el, []):
                    rule = rules.get(rid)
                    if rule is None or not applies(rule, cls):
                        continue
                    p = param(rule, pname)
                    if p is None:
                        continue
                    key = (rid, pname, el, v["value"])
                    if key in seen:          # identical value of the same element in one section: once
                        continue
                    seen.add(key)
                    ok = compare(v["value"], float(p["value"]), op, tol(p))
                    checks.append(record(rid, pname, cls, el, v, s, p, op, ok, src))
    return checks, not_run, sections


def sidewalk(v, s, cls, rules, src, not_run):
    rule = rules.get("ROD-048")
    p = param(rule, "width_min") if rule else None
    if p is None:
        return []
    table = p["value"] if isinstance(p["value"], dict) else {"all": p["value"]}
    lo, hi = min(table.values()), max(table.values())
    if v["value"] < lo - tol(p) - 1e-9:
        return [record("ROD-048", "width_min", cls, "sidewalk", v, s, dict(p, value=lo), "ge", False, src,
                       note="frontage land use not known: lowest required width enforced")]
    if v["value"] < hi - tol(p) - 1e-9:
        not_run.append({"rule_id": "ROD-048", "parameter": "width_min", "class": cls,
                        "reason": f"sidewalk {v['value']:g} m in {s['title']}: passes {lo:g} m; land use of the frontage not known (C8) for {hi:g} m"})
        return []
    return [record("ROD-048", "width_min", cls, "sidewalk", v, s, dict(p, value=hi), "ge", True, src)]


def record(rid, pname, cls, el, v, s, p, op, ok, src, note=None):
    t = v["text"]
    handles = [t["handle"]] + ([s["title_handle"]] if s["title_handle"] else [])
    r = {"rule_id": rid, "parameter": pname, "class": cls, "criterion": el,
         "subject": f"typical section '{s['title']}' — {el}", "observed": v["value"], "required": p["value"],
         "comparison": op, "tolerance": tol(p), "unit": p.get("unit"), "result": "pass" if ok else "fail",
         "assigned": v["assigned"], "dimension_match": v["dimension_match"],
         "evidence": {**src, "source_type": "dwg_vector", "handles": handles, "layer": t.get("layer"),
                      "extract": f"{s['title']}: {el} = \"{t['text'].strip()}\" (text {t['handle']}, layer {t.get('layer')})"}}
    if v["assigned"] == "positional":
        r["link_cap"] = "Probable"
    if note:
        r["note"] = note
    return r


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--sections", nargs="+", required=True)
    ap.add_argument("--rules", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)

    def run():
        ings = [read_json(p) for p in a.sections]
        for p, ing in zip(a.sections, ings):
            if (ing.get("source") or {}).get("file_type") != "dwg":
                raise RoadError(EXIT_INPUT, f"not a DWG ingestion file (skill 1): {p}")
        rules, _, _ = rules_index(read_json(a.rules))
        checks, not_run, sections = run_checks(ings, rules)
        atomic_write_json(a.out, {"tool": {"script": "check_sections.py", "version": __version__}, "mode": "sections",
                                  "sections": sections, "checks": checks, "not_run": not_run})
        emit({"status": "ok", "out": a.out, "sections": len(sections), "checks": len(checks),
              "fail": sum(1 for c in checks if c["result"] == "fail"), "not_run": len(not_run)})
        return EXIT_OK
    return main_guard(run)


if __name__ == "__main__":
    sys.exit(main())
