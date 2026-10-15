#!/usr/bin/env python3
"""Presence search for the phase-B1 rules ROD-101 ... 116 (SPC-0006 Rev E §4.13B.4).

For every element of road_common.PRESENCE_ELEMENTS and every ingestion file the caller lists with its
carrier type, the script searches:
- DWG ingestion (skill 1): layer names with entities, block names, text records;
- PDF ingestion (skill 2): vector text, assembled into lines per page.
A term preceded by a negation in the same text ("WITHOUT PARALLEL PARKING") is a statement of absence
(`stated_absent`), not presence. Text records skill 1 could not decode are counted per file and layer (K18;
DB-4). Condition terms (road_common.PRESENCE_CONDITIONS) are searched the same way. The result keeps, for
each element and file, the hits with their location and the full list of what was searched (M5).

Usage: python find_presence.py --file <carrier>=<ingestion.json> [...] --out <presence.json>
  carrier: typical_section, plan_geometry, road_hierarchy_plan, details, signing_marking_plan, drainage_plan,
           landscape_plan, parking_schedule, design_report, sections
Exit: 0 written · 2 input error. No network, no shell calls.
"""
__version__ = "0.1.0"

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from road_common import (EXIT_INPUT, EXIT_OK, PARTIAL_COVERAGE, PRESENCE_CONDITIONS, PRESENCE_ELEMENTS, RoadError,  # noqa: E402
                         atomic_write_json, emit, main_guard, read_json, source_of, term_hits, undecodable)

CARRIERS = {"typical_section", "plan_geometry", "road_hierarchy_plan", "details", "signing_marking_plan",
            "drainage_plan", "landscape_plan", "parking_schedule", "design_report", "sections"}
MAX_HITS = 25


def pdf_lines(content):
    """Lines of vector text per page: spans grouped by vertical position, sorted left to right."""
    for p in content.get("pages", []):
        rows = []
        for s in sorted(p.get("spans", []), key=lambda s: (s["bbox"][1], s["bbox"][0])):
            if rows and abs(rows[-1][0] - s["bbox"][1]) <= 2.0:
                rows[-1][1].append(s)
            else:
                rows.append([s["bbox"][1], [s]])
        for _, spans in rows:
            spans.sort(key=lambda s: s["bbox"][0])
            yield p["page"], spans


def span_text(spans):
    """Line text and the character start of each span."""
    text, starts = "", []
    for s in spans:
        if text:
            text += " "
        starts.append(len(text))
        text += s["text"]
    return text, starts


def bbox_of(spans, starts, a, b):
    sel = [s for s, st in zip(spans, starts) if st < b and st + len(s["text"]) > a] or spans
    return [min(s["bbox"][0] for s in sel), min(s["bbox"][1] for s in sel),
            max(s["bbox"][2] for s in sel), max(s["bbox"][3] for s in sel)]


def search_dwg(content, terms):
    hits = []
    for lay in content.get("layers", []):
        if lay.get("entity_count"):
            name = lay["name"].split("$")[-1]
            for t, a, b, neg in term_hits(name, terms):
                hits.append({"kind": "layer", "text": name, "layer": lay["name"], "count": lay["entity_count"],
                             "negated": neg})
    for blk in content.get("blocks", []):
        name = blk["name"].split("$")[-1]
        if name.startswith("*"):
            continue
        for t, a, b, neg in term_hits(name, terms):
            hits.append({"kind": "block", "text": name, "layer": blk.get("layer"), "handle": blk.get("handle"),
                         "negated": neg})
    for tx in content.get("texts", []):
        for t, a, b, neg in term_hits(tx.get("text", ""), terms):
            hits.append({"kind": "text", "text": tx["text"][:200], "layer": tx.get("layer"), "handle": tx.get("handle"),
                         "xy": tx.get("insert", [None, None])[:2], "negated": neg})
    return hits


def search_pdf(content, terms):
    hits = []
    for page, spans in pdf_lines(content):
        text, starts = span_text(spans)
        for t, a, b, neg in term_hits(text, terms):
            hits.append({"kind": "pdf_text", "text": text[:300], "page": page, "bbox": bbox_of(spans, starts, a, b),
                         "negated": neg})
    return hits


def search_partial(content, spec):
    """PDF lines that state an element is provided for less than 100 % (DB-5)."""
    hits = []
    for page, spans in pdf_lines(content):
        text, starts = span_text(spans)
        m = re.search(spec["coverage"], text, re.I)
        e = re.search(spec["element"], text, re.I)
        if m and e and float(m.group(1)) < 100:
            a, b = min(e.start(), m.start()), max(e.end(), m.end())
            hits.append({"kind": "pdf_text", "text": text[:300], "page": page, "coverage_pct": float(m.group(1)),
                         "bbox": bbox_of(spans, starts, a, b)})
    return hits


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", action="append", required=True, help="<carrier>=<ingestion.json>")
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)

    def run():
        files = []
        for spec in a.file:
            carrier, _, path = spec.partition("=")
            if carrier not in CARRIERS or not path:
                raise RoadError(EXIT_INPUT, f"--file needs <carrier>=<path> with carrier in {sorted(CARRIERS)}: {spec}")
            files.append((carrier, path, read_json(path)))
        searched, elements, conditions, partial = [], {}, {}, {}
        for carrier, path, ing in files:
            c = ing.get("content") or {}
            src = source_of(ing)
            ftype = (src.get("file_type") or "").lower()
            is_pdf = "pages" in c
            if is_pdf:
                info = {"pages": len(c.get("pages", [])), "spans": sum(len(p.get("spans", [])) for p in c.get("pages", [])),
                        "scanned_pages": [p["page"] for p in c.get("pages", []) if p.get("scanned")]}
            else:
                und = [t for t in c.get("texts", []) if undecodable(t.get("text"))]
                lay = {}
                for t in und:
                    lay[t.get("layer", "")] = lay.get(t.get("layer", ""), 0) + 1
                info = {"layers": sum(1 for x in c.get("layers", []) if x.get("entity_count")), "blocks": len(c.get("blocks", [])),
                        "texts": len(c.get("texts", [])), "undecodable_texts": len(und), "undecodable_by_layer": lay}
            searched.append({"carrier": carrier, "path": src.get("path") or path, "sha256": src.get("sha256"),
                             "document_no": src.get("document_no"), "kind": "pdf" if is_pdf else "dwg", "file_type": ftype, **info})
            for key, el in PRESENCE_ELEMENTS.items():
                hits = search_pdf(c, el["terms"]) if is_pdf else search_dwg(c, el["terms"])
                if hits:
                    elements.setdefault(key, []).append({"carrier": carrier, "path": src.get("path") or path,
                                                         "sha256": src.get("sha256"), "hits": hits[:MAX_HITS],
                                                         "hit_count": len(hits)})
            if is_pdf:
                for rid, spec in PARTIAL_COVERAGE.items():
                    hits = search_partial(c, spec)
                    if hits:
                        partial.setdefault(rid, []).append({"carrier": carrier, "path": src.get("path") or path,
                                                            "sha256": src.get("sha256"), "hits": hits[:5]})
            for rid, terms in PRESENCE_CONDITIONS.items():
                hits = search_pdf(c, terms) if is_pdf else search_dwg(c, terms)
                hits = [h for h in hits if not h["negated"]]
                if hits:
                    conditions.setdefault(rid, []).append({"carrier": carrier, "path": src.get("path") or path,
                                                           "sha256": src.get("sha256"), "hits": hits[:5]})
        out = {"tool": {"script": "find_presence.py", "version": __version__}, "mode": "presence_search",
               "searched": searched, "elements": elements, "conditions": conditions,
               "partial_coverage": partial}
        atomic_write_json(a.out, out)
        emit({"status": "ok", "out": a.out, "files": len(searched),
              "elements_found": {k: sum(x["hit_count"] for x in v) for k, v in sorted(elements.items())}})
        return EXIT_OK
    return main_guard(run)


if __name__ == "__main__":
    sys.exit(main())
