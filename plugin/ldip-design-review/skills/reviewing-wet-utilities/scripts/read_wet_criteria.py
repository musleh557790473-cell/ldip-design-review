#!/usr/bin/env python3
"""Read wet-utility design criteria from skill-2 PDF text JSON (skill 11).

Follows skill 10 ``read_criteria`` pattern: heading / keyword locator on
``*.pdf.json`` vector words → ``criteria.json`` with clause and page.
Also extracts a storm pipe table when present (W6 report table).

Usage:
  python read_wet_criteria.py --text <report.pdf.json> --out <criteria.json>
      [--pages 1-400]

Exit: 0 written (also when no table found) · 2 input · 4 schema. No network, no shell.
"""
__version__ = "0.1.0"

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from wet_common import (  # noqa: E402
    EXIT_INPUT, EXIT_OK, EXIT_SCHEMA, SKILL_ROOT, atomic_write_json, emit, fail,
    main_guard, path_ok, read_json,
)

SCHEMA_PATH = SKILL_ROOT / "templates" / "criteria.schema.json"
LINE_TOL = 2.5

SECTION_HINTS = {
    "potable_water": re.compile(r"potable\s+water|water\s+supply|fire\s+fighting|PW\b", re.I),
    "sewer": re.compile(r"sewer|wastewater|foul|gravity\s+sewer|force\s+main", re.I),
    "storm": re.compile(r"storm\s*water|stormwater|drainage\s+network|surface\s+water", re.I),
    "irrigation": re.compile(r"irrigation|TSE|treated\s+sewage", re.I),
}

CRITERION_PATTERNS = [
    ("min_diameter_mm", re.compile(r"min(?:imum)?\s+(?:pipe\s+)?diam(?:eter)?[^0-9]{0,20}(\d+(?:\.\d+)?)\s*mm", re.I)),
    ("min_slope_pct", re.compile(r"min(?:imum)?\s+(?:slope|gradient)[^0-9%]{0,20}(\d+(?:\.\d+)?)\s*%", re.I)),
    ("min_velocity_ms", re.compile(r"min(?:imum)?\s+veloc(?:ity)?[^0-9]{0,20}(\d+(?:\.\d+)?)\s*m\s*/?\s*s", re.I)),
    ("max_velocity_ms", re.compile(r"max(?:imum)?\s+veloc(?:ity)?[^0-9]{0,20}(\d+(?:\.\d+)?)\s*m\s*/?\s*s", re.I)),
    ("max_depth_m", re.compile(r"max(?:imum)?\s+depth[^0-9]{0,20}(\d+(?:\.\d+)?)\s*m\b", re.I)),
    ("cover_m", re.compile(r"(?:min(?:imum)?\s+)?cover[^0-9]{0,20}(\d+(?:\.\d+)?)\s*m\b", re.I)),
    ("mh_spacing_m", re.compile(r"(?:manhole|MH)\s+spac(?:ing|e)[^0-9]{0,30}(\d+(?:\.\d+)?)\s*m\b", re.I)),
    ("hydrant_spacing_m", re.compile(r"hydrant\s+spac(?:ing|e)[^0-9]{0,30}(\d+(?:\.\d+)?)\s*m\b", re.I)),
    ("pressure_min_bar", re.compile(r"min(?:imum)?\s+(?:residual\s+)?pressure[^0-9]{0,20}(\d+(?:\.\d+)?)\s*bar", re.I)),
    ("pressure_max_bar", re.compile(r"max(?:imum)?\s+pressure[^0-9]{0,20}(\d+(?:\.\d+)?)\s*bar", re.I)),
    ("hw_c", re.compile(r"(?:Hazen[- ]Williams|HW)\s*C[^0-9]{0,10}(\d+(?:\.\d+)?)", re.I)),
    ("return_period_yr", re.compile(r"(?:return\s+period|design\s+storm)[^0-9]{0,20}(\d+)\s*(?:[- ]?year|yr)", re.I)),
    ("d_over_d_max", re.compile(r"d\s*/\s*D[^0-9]{0,15}(\d+(?:\.\d+)?)", re.I)),
    ("total_length_km", re.compile(r"(?:total\s+)?(?:network\s+)?length[^0-9]{0,20}(\d+(?:\.\d+)?)\s*km", re.I)),
    ("manhole_count", re.compile(r"(\d+)\s*(?:manholes|MH\b)", re.I)),
    ("depth_range_m", re.compile(
        r"depth[^0-9]{0,10}(\d+(?:\.\d+)?)\s*(?:–|-|to)\s*(\d+(?:\.\d+)?)\s*m", re.I)),
]

# Gradient table row: "200 mm  0.50 %  0.75 m/s"
GRAD_ROW = re.compile(
    r"(?P<d>\d{2,4})\s*mm\s+(?P<slope>\d+(?:\.\d+)?)\s*%(?:\s+(?P<v>\d+(?:\.\d+)?)\s*m\s*/?\s*s)?",
    re.I,
)
STORM_PIPE_ROW = re.compile(
    r"(?P<d>\d{2,4})\s*mm\s+(?P<len>\d+(?:\.\d+)?)\s*(?:m|km)",
    re.I,
)


def spans_of(ingestion, page_lo=None, page_hi=None):
    """Collect word spans from skill-2 pdf.json (pages[].words or .spans)."""
    pages = ingestion.get("pages") or ingestion.get("content", {}).get("pages") or []
    out = []
    for p in pages:
        num = p.get("page") or p.get("page_no") or p.get("number")
        if num is None:
            continue
        if page_lo is not None and num < page_lo:
            continue
        if page_hi is not None and num > page_hi:
            continue
        words = p.get("words") or p.get("spans") or p.get("tokens") or []
        for w in words:
            text = w.get("text") or w.get("str") or ""
            bbox = w.get("bbox") or w.get("box") or [0, 0, 0, 0]
            if len(bbox) < 4:
                continue
            out.append({"page": num, "text": text, "bbox": list(bbox)})
        # Plain text fallback
        if not words and p.get("text"):
            out.append({"page": num, "text": p["text"], "bbox": [0, 0, 1000, 12], "plain": True})
    # Flat words list
    if not out and ingestion.get("words"):
        for w in ingestion["words"]:
            out.append({
                "page": w.get("page", 1),
                "text": w.get("text", ""),
                "bbox": w.get("bbox") or [0, 0, 0, 0],
            })
    return out


def lines_of(spans):
    # Group by page then y
    by_page = {}
    for s in spans:
        by_page.setdefault(s["page"], []).append(s)
    lines = []
    for page, items in sorted(by_page.items()):
        plain = [s for s in items if s.get("plain")]
        if plain:
            for s in plain:
                for ln in str(s["text"]).splitlines():
                    if ln.strip():
                        lines.append({"page": page, "text": ln.strip(), "top": 0.0})
            continue
        items = sorted(items, key=lambda s: (s["bbox"][1], s["bbox"][0]))
        cur = None
        for s in items:
            if cur and abs(s["bbox"][1] - cur["top"]) < LINE_TOL:
                cur["spans"].append(s)
            else:
                if cur:
                    cur["spans"].sort(key=lambda x: x["bbox"][0])
                    cur["text"] = " ".join(x["text"] for x in cur["spans"])
                    lines.append(cur)
                cur = {"page": page, "top": s["bbox"][1], "spans": [s]}
        if cur:
            cur["spans"].sort(key=lambda x: x["bbox"][0])
            cur["text"] = " ".join(x["text"] for x in cur["spans"])
            lines.append(cur)
    return lines


def section_of(text, current):
    for name, rx in SECTION_HINTS.items():
        if rx.search(text):
            return name
    return current


def parse_pages_arg(s):
    if not s:
        return None, None
    if "-" in s:
        a, b = s.split("-", 1)
        return int(a), int(b)
    n = int(s)
    return n, n


def extract(lines):
    criteria = {k: {} for k in SECTION_HINTS}
    gradient_tables = {k: [] for k in ("sewer", "storm")}
    storm_pipe_table = []
    declared_totals = {}
    current = None
    for ln in lines:
        text = ln["text"]
        current = section_of(text, current)
        page = ln["page"]
        if current is None:
            # Still try global patterns for totals
            for key, rx in CRITERION_PATTERNS:
                if key in ("total_length_km", "manhole_count", "depth_range_m"):
                    m = rx.search(text)
                    if m and key not in declared_totals:
                        if key == "depth_range_m":
                            declared_totals[key] = {
                                "min_m": float(m.group(1)), "max_m": float(m.group(2)),
                                "page": page, "text": text,
                            }
                        else:
                            declared_totals[key] = {
                                "value": float(m.group(1)), "page": page, "text": text,
                            }
            continue
        bucket = criteria[current]
        for key, rx in CRITERION_PATTERNS:
            m = rx.search(text)
            if not m:
                continue
            if key == "depth_range_m":
                bucket[key] = {
                    "min_m": float(m.group(1)), "max_m": float(m.group(2)),
                    "page": page, "text": text, "clause": current,
                }
            else:
                bucket.setdefault(key, {
                    "value": float(m.group(1)), "page": page, "text": text, "clause": current,
                })
        if current in ("sewer", "storm"):
            for m in GRAD_ROW.finditer(text):
                gradient_tables[current].append({
                    "diameter_mm": float(m.group("d")),
                    "min_slope_pct": float(m.group("slope")),
                    "velocity_ms": float(m.group("v")) if m.group("v") else None,
                    "page": page,
                    "text": m.group(0),
                })
        if current == "storm":
            for m in STORM_PIPE_ROW.finditer(text):
                length = float(m.group("len"))
                unit_km = "km" in m.group(0).casefold()
                storm_pipe_table.append({
                    "diameter_mm": float(m.group("d")),
                    "length_m": length * 1000.0 if unit_km else length,
                    "page": page,
                    "text": m.group(0),
                })
        for key, rx in CRITERION_PATTERNS:
            if key in ("total_length_km", "manhole_count", "depth_range_m"):
                m = rx.search(text)
                if m and key not in declared_totals:
                    if key == "depth_range_m":
                        declared_totals[key] = {
                            "min_m": float(m.group(1)), "max_m": float(m.group(2)),
                            "page": page, "text": text, "network": current,
                        }
                    else:
                        declared_totals[key] = {
                            "value": float(m.group(1)), "page": page, "text": text,
                            "network": current,
                        }
    return criteria, gradient_tables, storm_pipe_table, declared_totals


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--text", required=True, help="skill-2 *.pdf.json")
    ap.add_argument("--out", required=True)
    ap.add_argument("--pages", default=None)
    a = ap.parse_args(argv)

    def run():
        path = Path(a.text)
        if not path.is_file():
            return fail(EXIT_INPUT, f"text JSON not found: {path}")
        if not path_ok(path):
            return fail(EXIT_INPUT, f"path longer than 250 characters: {path}")
        ingestion = read_json(path)
        lo, hi = parse_pages_arg(a.pages)
        spans = spans_of(ingestion, lo, hi)
        lines = lines_of(spans)
        criteria, grads, storm_pipes, totals = extract(lines)
        doc = {
            "criteria_version": "1.0",
            "source": {"path": str(path), "kind": "skill2_pdf_json"},
            "criteria": criteria,
            "gradient_tables": grads,
            "storm_pipe_table": storm_pipes,
            "declared_totals": totals,
            "meta": {
                "reader": f"read_wet_criteria/{__version__}",
                "line_count": len(lines),
                "pages": [lo, hi],
            },
        }
        try:
            from jsonschema import Draft202012Validator
            import json
            schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
            errs = list(Draft202012Validator(schema).iter_errors(doc))
            if errs:
                return fail(EXIT_SCHEMA, "criteria.json failed schema",
                            errors=[e.message for e in errs[:20]])
        except ImportError:
            pass
        atomic_write_json(a.out, doc)
        n_vals = sum(len(v) for v in criteria.values())
        emit({
            "status": "ok", "code": EXIT_OK, "out": str(a.out),
            "criteria_values": n_vals,
            "gradient_rows": sum(len(v) for v in grads.values()),
            "storm_pipe_rows": len(storm_pipes),
        })
        return EXIT_OK

    return main_guard(run)


if __name__ == "__main__":
    sys.exit(main())
