#!/usr/bin/env python3
"""Pair drawings between a previous and a current submittal (skill 7 M1).

Pairing levels (SPC-0006 Rev B §4.4; Rev M §4.15):
  (a) normalised number — strip spaces, stray single-letter fields (e.g. -G-), revision suffix;
  (b) --number-map CSV (old,new) — renumbered pairs;
  (c) proposed only — same title + sheet x of y → status proposed, verification Probable.
Never silently invent a pair.

Usage:
  python match_revisions.py --previous <prev.json> --current <curr.json> --out <pairs.json>
       [--number-map <old,new.csv>]

Each input JSON is either a skill-6 package.json (sheet_register) or a list of drawing records:
  [{ "number", "revision", "title", "sheet", "path", "formats", ... }, ...]

Exit: 0 done · 2 input error. No network, no shell calls.
"""
__version__ = "0.1.0"

import argparse
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from rev_common import (  # noqa: E402
    EXIT_INPUT, EXIT_OK, atomic_write_json, emit, fail, normalise_number, path_ok,
    read_json, revision_from_number, sheet_key, title_key,
)


def load_drawings(path):
    raw = read_json(path)
    if isinstance(raw, list):
        return [_norm_rec(r, i) for i, r in enumerate(raw)]
    if isinstance(raw, dict) and "sheet_register" in raw:
        out = []
        for i, row in enumerate(raw["sheet_register"]):
            nums = row.get("numbers") or {}
            revs = row.get("revisions") or {}
            number = (nums.get("file_name") or nums.get("dwg") or nums.get("pdf")
                      or row.get("base") or "")
            revision = (revs.get("file_name") or revs.get("dwg") or revs.get("pdf")
                        or revision_from_number(number))
            title = None
            sheet = None
            for car in ("pdf", "dwg", "file_name"):
                for c in (row.get("carriers") or {}).get(car) or []:
                    title = title or c.get("title")
                    sheet = sheet or c.get("sheet")
            out.append(_norm_rec({
                "id": row.get("base") or f"row-{i}",
                "number": number,
                "revision": revision,
                "title": title,
                "sheet": sheet,
                "formats": row.get("formats_received") or list((row.get("files") or {}).keys()),
                "path": ((row.get("files") or {}).get("dwg") or (row.get("files") or {}).get("pdf")
                         or row.get("base")),
                "base": row.get("base"),
            }, i))
        return out
    if isinstance(raw, dict) and "drawings" in raw:
        return [_norm_rec(r, i) for i, r in enumerate(raw["drawings"])]
    raise ValueError(f"{path} is not a drawing list or package.json")


def _norm_rec(r, i):
    number = r.get("number") or ""
    return {
        "id": r.get("id") or r.get("path") or f"item-{i}",
        "number": str(number).strip(),
        "revision": (r.get("revision") or revision_from_number(number) or None),
        "title": r.get("title"),
        "sheet": r.get("sheet"),
        "formats": r.get("formats") or [],
        "path": r.get("path"),
        "base": r.get("base"),
        "ingestion": r.get("ingestion"),
        "linework": r.get("linework"),
        "surface": r.get("surface"),
        "pdf_raster": r.get("pdf_raster"),
        "title_block_revision": r.get("title_block_revision"),
        "norm": normalise_number(number),
        "title_key": title_key(r.get("title")),
        "sheet_key": sheet_key(r.get("sheet")),
    }


def load_number_map(path):
    """CSV with columns old,new (header optional)."""
    mapping = {}
    with open(path, newline="", encoding="utf-8") as fh:
        sample = fh.read(256)
        fh.seek(0)
        dialect = csv.Sniffer().sniff(sample) if sample.strip() else csv.excel
        rows = list(csv.reader(fh, dialect))
    if not rows:
        return mapping
    start = 0
    if rows[0] and str(rows[0][0]).strip().lower() in ("old", "previous", "from"):
        start = 1
    for row in rows[start:]:
        if len(row) < 2:
            continue
        old, new = normalise_number(row[0]), normalise_number(row[1])
        if old and new:
            mapping[old] = new
            mapping[new] = old  # bidirectional lookup aid
    return mapping


def pair_drawings(previous, current, number_map=None):
    number_map = number_map or {}
    used_prev, used_curr = set(), set()
    pairs = []

    # (a) normalised number
    by_norm_prev = {}
    for p in previous:
        if p["norm"]:
            by_norm_prev.setdefault(p["norm"], []).append(p)
    for c in current:
        if not c["norm"] or c["id"] in used_curr:
            continue
        cands = [p for p in by_norm_prev.get(c["norm"], []) if p["id"] not in used_prev]
        if len(cands) == 1:
            p = cands[0]
            pairs.append(_pair(p, c, "normalised", "confirmed"))
            used_prev.add(p["id"])
            used_curr.add(c["id"])

    # (b) number-map (renumbered)
    for c in current:
        if c["id"] in used_curr or not c["norm"]:
            continue
        old_norm = number_map.get(c["norm"])
        if not old_norm:
            continue
        cands = [p for p in previous if p["id"] not in used_prev and p["norm"] == old_norm]
        if len(cands) == 1:
            p = cands[0]
            pairs.append(_pair(p, c, "number_map", "confirmed", renumbered=True))
            used_prev.add(p["id"])
            used_curr.add(c["id"])

    # (c) proposed: title + sheet x of y (never silent)
    by_ts_prev = {}
    for p in previous:
        if p["id"] in used_prev:
            continue
        if p["title_key"] and p["sheet_key"]:
            by_ts_prev.setdefault((p["title_key"], p["sheet_key"]), []).append(p)
    for c in current:
        if c["id"] in used_curr:
            continue
        if not (c["title_key"] and c["sheet_key"]):
            continue
        cands = [p for p in by_ts_prev.get((c["title_key"], c["sheet_key"]), [])
                 if p["id"] not in used_prev]
        if len(cands) == 1:
            p = cands[0]
            pairs.append(_pair(p, c, "title_sheet", "proposed",
                               renumbered=p["norm"] != c["norm"],
                               verification_status="Probable"))
            used_prev.add(p["id"])
            used_curr.add(c["id"])

    unmatched_previous = [p for p in previous if p["id"] not in used_prev]
    unmatched_current = [c for c in current if c["id"] not in used_curr]
    return pairs, unmatched_previous, unmatched_current


def _pair(prev, curr, level, status, renumbered=False, verification_status=None):
    return {
        "previous": _pub(prev),
        "current": _pub(curr),
        "match": level,
        "status": status,
        "renumbered": bool(renumbered or (level == "number_map")
                           or (prev["norm"] and curr["norm"] and prev["norm"] != curr["norm"])),
        "verification_status": verification_status if status == "proposed" else None,
    }


def _pub(r):
    return {k: r.get(k) for k in (
        "id", "number", "revision", "title", "sheet", "formats", "path", "base",
        "ingestion", "linework", "surface", "pdf_raster", "title_block_revision", "norm",
    )}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--previous", required=True)
    ap.add_argument("--current", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--number-map")
    a = ap.parse_args(argv)
    try:
        for p in (a.previous, a.current, a.out):
            if not path_ok(p):
                return fail(EXIT_INPUT, f"path longer than 250 characters: {p}")
        if a.number_map and not path_ok(a.number_map):
            return fail(EXIT_INPUT, f"path longer than 250 characters: {a.number_map}")
        previous = load_drawings(a.previous)
        current = load_drawings(a.current)
        nmap = load_number_map(a.number_map) if a.number_map else {}
        pairs, u_prev, u_curr = pair_drawings(previous, current, nmap)
        result = {
            "pairs_version": "1.0",
            "previous_count": len(previous),
            "current_count": len(current),
            "pairs": pairs,
            "unmatched_previous": [_pub(p) for p in u_prev],
            "unmatched_current": [_pub(c) for c in u_curr],
            "counts": {
                "pairs": len(pairs),
                "normalised": sum(1 for p in pairs if p["match"] == "normalised"),
                "number_map": sum(1 for p in pairs if p["match"] == "number_map"),
                "proposed": sum(1 for p in pairs if p["status"] == "proposed"),
                "unmatched_previous": len(u_prev),
                "unmatched_current": len(u_curr),
            },
        }
        atomic_write_json(a.out, result)
        emit({"status": "ok", "out": str(a.out), **result["counts"]})
        return EXIT_OK
    except ValueError as exc:
        return fail(EXIT_INPUT, str(exc))
    except OSError as exc:
        return fail(EXIT_INPUT, str(exc))


if __name__ == "__main__":
    sys.exit(main())
