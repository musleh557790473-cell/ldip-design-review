#!/usr/bin/env python3
"""Pair drawings between a previous and a current submittal (skill 7 M1).

Pairing levels (SPC-0006 Rev B §4.4; Rev M §4.15 DM-2 / M1; S31 R1 / R2):
  (a) normalised number — strip spaces, stray single-letter fields (e.g. -G-), revision suffix;
  (b) --number-map CSV (old,new) — fixed comma dialect (R2);
  (c) proposed — PDF raster MAD, one-to-one assignment, Probable; title+sheet when no PDFs.
  No pair is assumed silently.

Usage:
  python match_revisions.py --previous <prev.json> --current <curr.json> --out <pairs.json>
       [--number-map <old,new.csv>] [--params <params.json>]
       [--resolve-base-previous <dir>] [--resolve-base-current <dir>]

Exit: 0 done · 2 input error · 3 missing package. No network, no shell calls.
"""
__version__ = "0.1.0"

import argparse
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from rev_common import (  # noqa: E402
    EXIT_DEP, EXIT_INPUT, EXIT_OK, atomic_write_json, emit, fail, normalise_number, path_ok,
    read_json, revision_from_number, sheet_key, title_key,
)
from raster_match import load_raster_params, pair_unmatched_by_raster  # noqa: E402


def _pdf_from_files(files):
    if isinstance(files, list):
        for f in files:
            p = (f.get("path") if isinstance(f, dict) else None) or ""
            if str(p).lower().endswith(".pdf"):
                return p
    elif isinstance(files, dict):
        return files.get("pdf") or files.get("PDF")
    return None


def load_drawings(path, resolve_base=None):
    raw = read_json(path)
    base = Path(resolve_base) if resolve_base else None
    if isinstance(raw, list):
        return [_norm_rec(r, i, base) for i, r in enumerate(raw)]
    if isinstance(raw, dict) and "sheet_register" in raw:
        out = []
        for i, row in enumerate(raw["sheet_register"]):
            nums = row.get("numbers") or {}
            revs = row.get("revisions") or {}
            number = (nums.get("file_name") or nums.get("dwg") or nums.get("pdf")
                      or (nums[0] if isinstance(nums, list) and nums else None)
                      or row.get("base") or "")
            if isinstance(number, list):
                number = number[0] if number else ""
            revision = (revs.get("file_name") or revs.get("dwg") or revs.get("pdf")
                        or revision_from_number(number))
            title = sheet = None
            for car in ("pdf", "dwg", "file_name"):
                for c in (row.get("carriers") or {}).get(car) or []:
                    title = title or c.get("title")
                    sheet = sheet or c.get("sheet")
            pdf_path = _pdf_from_files(row.get("files"))
            out.append(_norm_rec({
                "id": row.get("base") or f"row-{i}",
                "number": number,
                "revision": revision,
                "title": title,
                "sheet": sheet,
                "formats": row.get("formats_received") or [],
                "path": pdf_path or row.get("base"),
                "pdf": pdf_path,
                "base": row.get("base"),
                "title_block_revision": None,
            }, i, base))
        return out
    if isinstance(raw, dict) and "drawings" in raw:
        return [_norm_rec(r, i, base) for i, r in enumerate(raw["drawings"])]
    raise ValueError(f"{path} is not a drawing list or package.json")


def _resolve(p, base):
    if not p:
        return None
    path = Path(p)
    if path.is_file():
        return str(path)
    if base:
        cand = base / p
        if cand.is_file():
            return str(cand)
        cand2 = base / Path(p).name
        if cand2.is_file():
            return str(cand2)
    return str(path)


def _norm_rec(r, i, resolve_base=None):
    number = r.get("number") or ""
    pdf = r.get("pdf") or (r.get("path") if str(r.get("path") or "").lower().endswith(".pdf") else None)
    pdf = _resolve(pdf, resolve_base)
    return {
        "id": r.get("id") or r.get("path") or r.get("pdf") or f"item-{i}",
        "number": str(number).strip(),
        "revision": (r.get("revision") or revision_from_number(number) or None),
        "title": r.get("title"),
        "sheet": r.get("sheet"),
        "formats": r.get("formats") or [],
        "path": _resolve(r.get("path"), resolve_base) or pdf,
        "pdf": pdf,
        "base": r.get("base"),
        "ingestion": r.get("ingestion"),
        "linework": r.get("linework"),
        "surface": r.get("surface"),
        "pdf_raster": r.get("pdf_raster"),
        "title_block_revision": r.get("title_block_revision"),
        "revision_source": r.get("revision_source"),
        "norm": normalise_number(number),
        "title_key": title_key(r.get("title")),
        "sheet_key": sheet_key(r.get("sheet")),
    }


def load_number_map(path):
    """CSV with columns old,new (header optional). Fixed comma dialect (R2)."""
    mapping = {}
    try:
        with open(path, newline="", encoding="utf-8") as fh:
            # Fixed comma dialect (R2). strict=True so malformed quotes → exit 2, not a silent mis-parse.
            rows = list(csv.reader(fh, delimiter=",", quotechar='"', strict=True))
    except csv.Error as exc:
        raise ValueError(f"number-map CSV error: {exc}") from exc
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
            mapping[new] = old
    return mapping


def pair_drawings(previous, current, number_map=None, params=None):
    number_map = number_map or {}
    params = params or load_raster_params()
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

    # (c1) title + sheet when unique (synthetic / when text exists) — still proposed
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
                               verification_status="Probable",
                               propose_parts=["title", "sheet"]))
            used_prev.add(p["id"])
            used_curr.add(c["id"])

    # (c2) PDF raster MAD one-to-one on remaining (R1) — primary for real SHX drawings
    rem_prev = [p for p in previous if p["id"] not in used_prev]
    rem_curr = [c for c in current if c["id"] not in used_curr]

    def get_pdf(side):
        return side.get("pdf") or (side.get("path") if str(side.get("path") or "").lower().endswith(".pdf") else None)

    try:
        raster_pairs = pair_unmatched_by_raster(rem_prev, rem_curr, params, get_pdf)
    except ImportError as exc:
        raise RuntimeError(str(exc)) from exc

    for rp in raster_pairs:
        p, c = rp["previous"], rp["current"]
        if p["id"] in used_prev or c["id"] in used_curr:
            continue
        pairs.append(_pair(p, c, "raster_mad", "proposed",
                           renumbered=p["norm"] != c["norm"],
                           verification_status="Probable",
                           propose_parts=["raster_mad"],
                           mad=rp["mad"], margin=rp["margin"], weak=rp["weak"]))
        used_prev.add(p["id"])
        used_curr.add(c["id"])

    unmatched_previous = [p for p in previous if p["id"] not in used_prev]
    unmatched_current = [c for c in current if c["id"] not in used_curr]
    return pairs, unmatched_previous, unmatched_current


def _pair(prev, curr, level, status, renumbered=False, verification_status=None,
          propose_parts=None, mad=None, margin=None, weak=None):
    rec = {
        "previous": _pub(prev),
        "current": _pub(curr),
        "match": level,
        "status": status,
        "renumbered": bool(renumbered or (level == "number_map")
                           or (prev["norm"] and curr["norm"] and prev["norm"] != curr["norm"])),
        "verification_status": verification_status if status == "proposed" else None,
    }
    if propose_parts:
        rec["propose_parts"] = list(propose_parts)
    if mad is not None:
        rec["mad"] = round(float(mad), 4)
    if margin is not None:
        rec["margin"] = margin
    if weak is not None:
        rec["weak"] = bool(weak)
    return rec


def _pub(r):
    return {k: r.get(k) for k in (
        "id", "number", "revision", "title", "sheet", "formats", "path", "pdf", "base",
        "ingestion", "linework", "surface", "pdf_raster", "title_block_revision",
        "revision_source", "norm",
    )}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--previous", required=True)
    ap.add_argument("--current", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--number-map")
    ap.add_argument("--params")
    ap.add_argument("--resolve-base-previous")
    ap.add_argument("--resolve-base-current")
    a = ap.parse_args(argv)
    try:
        for p in (a.previous, a.current, a.out):
            if not path_ok(p):
                return fail(EXIT_INPUT, f"path longer than 250 characters: {p}")
        for p in (a.number_map, a.params, a.resolve_base_previous, a.resolve_base_current):
            if p and not path_ok(p):
                return fail(EXIT_INPUT, f"path longer than 250 characters: {p}")
        params = load_raster_params(a.params)
        previous = load_drawings(a.previous, a.resolve_base_previous)
        current = load_drawings(a.current, a.resolve_base_current)
        try:
            nmap = load_number_map(a.number_map) if a.number_map else {}
        except ValueError as exc:
            return fail(EXIT_INPUT, str(exc))
        pairs, u_prev, u_curr = pair_drawings(previous, current, nmap, params)
        weak_n = sum(1 for p in pairs if p.get("weak"))
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
                "raster_mad": sum(1 for p in pairs if p["match"] == "raster_mad"),
                "weak": weak_n,
                "unmatched_previous": len(u_prev),
                "unmatched_current": len(u_curr),
            },
            "meta": {"raster_params_tag": params["tag"], "skill_version": __version__},
        }
        atomic_write_json(a.out, result)
        emit({"status": "ok", "out": str(a.out), **result["counts"]})
        return EXIT_OK
    except RuntimeError as exc:
        return fail(EXIT_DEP, str(exc))
    except ValueError as exc:
        return fail(EXIT_INPUT, str(exc))
    except OSError as exc:
        return fail(EXIT_INPUT, str(exc))


if __name__ == "__main__":
    sys.exit(main())
