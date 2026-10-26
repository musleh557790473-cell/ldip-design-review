#!/usr/bin/env python3
"""Diff paired drawings: DWG geometry hash, surfaces, PDF raster fallback, inventory (skill 7 M3).

- DWG: compare by geometry hash of skill-1 polylines / linework — never by AutoCAD handle.
- Surfaces: skill-3 side files — vertex match and ΔZ statistics.
- PDF raster: sheet-coordinate regions, capped at Probable (DM-4); parameters from
  references/params.json tagged [GAP — set from T7-R1] (DM-9).
- Inventory: deliverables / formats added or removed from skill-6 package.json.

Usage:
  python diff_entities.py --pairs <pairs.json> --out <rev_diff.json>
       [--previous-package <package.json>] [--current-package <package.json>]
       [--params <params.json>]

Exit: 0 done · 2 input error. No network, no shell calls.
"""
__version__ = "0.1.0"

import argparse
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from rev_common import (  # noqa: E402
    EXIT_INPUT, EXIT_OK, atomic_write_json, emit, fail, geometry_hash, load_linework_side,
    path_ok, polyline_hashes, read_json,
)

SKILL_DIR = Path(__file__).resolve().parents[1]
DEFAULT_PARAMS = SKILL_DIR / "references" / "params.json"


def load_params(path):
    p = Path(path) if path else DEFAULT_PARAMS
    if not p.is_file():
        return {
            "raster": {
                "tag": "[GAP — set from T7-R1]",
                "pixel_tolerance": None,
                "min_region_area_mm2": None,
                "max_regions": None,
                "verification_status_cap": "Probable",
            }
        }
    return read_json(p)


def hashes_for_side(side):
    hashes = {}
    ing_path = side.get("ingestion")
    if ing_path and Path(ing_path).is_file():
        hashes.update(polyline_hashes(read_json(ing_path)))
    lw = side.get("linework")
    if lw and Path(lw).is_file():
        for h, samples in load_linework_side(lw).items():
            hashes.setdefault(h, []).extend(samples)
    # Inline entities for synthetic tests
    for ent in side.get("entities") or []:
        h = geometry_hash(ent.get("kind") or "polyline", ent.get("layer"),
                          ent.get("vertices") or ent.get("points") or [])
        hashes.setdefault(h, []).append({"layer": ent.get("layer"), "handle_ignored": True})
    return hashes


def diff_hashes(prev_h, curr_h):
    pk, ck = set(prev_h), set(curr_h)
    removed = sorted(pk - ck)
    added = sorted(ck - pk)
    common = sorted(pk & ck)
    return {
        "unchanged": len(common),
        "added": [{"geometry_hash": h, "sample": (curr_h[h] or [None])[0]} for h in added],
        "removed": [{"geometry_hash": h, "sample": (prev_h[h] or [None])[0]} for h in removed],
        "changed": bool(added or removed),
        "moved": len(added) == 1 and len(removed) == 1,  # one-for-one → moved polyline (T7-1)
    }


def surface_points(path_or_obj):
    if isinstance(path_or_obj, (str, Path)):
        d = read_json(path_or_obj)
    else:
        d = path_or_obj
    pts = {}
    for row in d.get("points") or []:
        # [id, E, N, Z] or {id,e,n,z}
        if isinstance(row, (list, tuple)) and len(row) >= 4:
            pts[int(row[0])] = (float(row[1]), float(row[2]), float(row[3]))
        elif isinstance(row, dict):
            pts[int(row["id"])] = (float(row["E"]), float(row["N"]), float(row["Z"]))
    return pts


def diff_surfaces(prev_path, curr_path):
    if not prev_path or not curr_path:
        return None
    if not (Path(prev_path).is_file() and Path(curr_path).is_file()):
        # allow inline dicts passed via pairs JSON under surface_data — skip if paths missing
        return {"result": "not_run", "reason": "surface side file missing"}
    pp, cp = surface_points(prev_path), surface_points(curr_path)
    # Match by plan position (E,N) rounded to 0.001
    def key(e, n):
        return (round(e, 3), round(n, 3))

    prev_by = {key(e, n): (i, z) for i, (e, n, z) in pp.items()}
    curr_by = {key(e, n): (i, z) for i, (e, n, z) in cp.items()}
    common = set(prev_by) & set(curr_by)
    dz = [curr_by[k][1] - prev_by[k][1] for k in common]
    stats = {}
    if dz:
        stats = {
            "n": len(dz),
            "min": round(min(dz), 4),
            "max": round(max(dz), 4),
            "mean": round(sum(dz) / len(dz), 4),
            "rms": round(math.sqrt(sum(x * x for x in dz) / len(dz)), 4),
        }
    return {
        "matched_vertices": len(common),
        "only_previous": len(set(prev_by) - set(curr_by)),
        "only_current": len(set(curr_by) - set(prev_by)),
        "delta_z": stats,
        "changed": bool(dz and any(abs(x) > 1e-6 for x in dz))
                   or bool(set(prev_by) ^ set(curr_by)),
    }


def raster_diff(prev_side, curr_side, params):
    """PDF raster fallback. Regions in sheet coordinates; status capped at Probable (DM-4)."""
    raster = params.get("raster") or {}
    gap = raster.get("tag") or "[GAP — set from T7-R1]"
    # Synthetic / provided region lists for tests
    prev_r = prev_side.get("pdf_raster") or {}
    curr_r = curr_side.get("pdf_raster") or {}
    if not prev_r and not curr_r:
        return {
            "result": "not_run",
            "reason": f"no raster inputs; parameters {gap}",
            "verification_status_cap": raster.get("verification_status_cap") or "Probable",
            "params_tag": gap,
        }
    prev_regions = {r.get("id"): r for r in (prev_r.get("regions") or [])}
    curr_regions = {r.get("id"): r for r in (curr_r.get("regions") or [])}
    changed_regions = []
    for rid, cr in curr_regions.items():
        pr = prev_regions.get(rid)
        if pr is None:
            changed_regions.append({"id": rid, "change": "added", "bbox_sheet": cr.get("bbox_sheet")})
        elif pr.get("digest") != cr.get("digest"):
            changed_regions.append({"id": rid, "change": "modified", "bbox_sheet": cr.get("bbox_sheet")})
    for rid in set(prev_regions) - set(curr_regions):
        changed_regions.append({"id": rid, "change": "removed",
                                "bbox_sheet": prev_regions[rid].get("bbox_sheet")})
    # Cap region count if parameter set (GAP may leave it null)
    max_r = raster.get("max_regions")
    truncated = False
    if isinstance(max_r, int) and len(changed_regions) > max_r:
        changed_regions = changed_regions[:max_r]
        truncated = True
    return {
        "result": "ok",
        "changed_regions": changed_regions,
        "changed": bool(changed_regions),
        "verification_status": "Probable",  # DM-4
        "verification_status_cap": "Probable",
        "params_tag": gap,
        "truncated": truncated,
    }


def inventory_diff(prev_pkg, curr_pkg):
    if not prev_pkg or not curr_pkg:
        return {}
    def bases(pkg):
        if "sheet_register" in pkg:
            return {row.get("base"): row for row in pkg["sheet_register"] if row.get("base")}
        return {d.get("number") or d.get("base"): d for d in pkg.get("drawings") or []}

    def formats(row):
        if not row:
            return set()
        if "formats_received" in row:
            return set(row.get("formats_received") or [])
        return set(row.get("formats") or [])

    pb, cb = bases(prev_pkg), bases(curr_pkg)
    added = sorted(set(cb) - set(pb))
    removed = sorted(set(pb) - set(cb))
    format_changes = []
    for b in sorted(set(pb) & set(cb)):
        pf, cf = formats(pb[b]), formats(cb[b])
        if pf != cf:
            format_changes.append({
                "base": b,
                "formats_added": sorted(cf - pf),
                "formats_removed": sorted(pf - cf),
            })
    return {
        "deliverables_added": added,
        "deliverables_removed": removed,
        "format_changes": format_changes,
        "changed": bool(added or removed or format_changes),
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--pairs", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--previous-package")
    ap.add_argument("--current-package")
    ap.add_argument("--params")
    a = ap.parse_args(argv)
    try:
        for p in (a.pairs, a.out):
            if not path_ok(p):
                return fail(EXIT_INPUT, f"path longer than 250 characters: {p}")
        pairs_doc = read_json(a.pairs)
        # Allow pairs embedded in an existing rev_diff
        pairs = pairs_doc.get("pairs") or []
        params = load_params(a.params)
        entity_diffs, surface_diffs, raster_diffs = [], [], []

        for i, pair in enumerate(pairs):
            prev, curr = pair["previous"], pair["current"]
            # Inline test entities may live on the pair sides after match — also accept top-level
            ph = hashes_for_side(prev)
            ch = hashes_for_side(curr)
            # Synthetic entities attached on the pair for tests
            if pair.get("previous_entities") is not None:
                ph = {}
                for ent in pair["previous_entities"]:
                    h = geometry_hash(ent.get("kind") or "polyline", ent.get("layer"),
                                      ent.get("vertices") or ent.get("points") or [])
                    ph.setdefault(h, []).append({"layer": ent.get("layer")})
            if pair.get("current_entities") is not None:
                ch = {}
                for ent in pair["current_entities"]:
                    h = geometry_hash(ent.get("kind") or "polyline", ent.get("layer"),
                                      ent.get("vertices") or ent.get("points") or [])
                    ch.setdefault(h, []).append({"layer": ent.get("layer")})

            ed = diff_hashes(ph, ch)
            ed.update({"pair_index": i, "match": pair.get("match")})
            entity_diffs.append(ed)

            sd = None
            if prev.get("surface") or curr.get("surface"):
                sd = diff_surfaces(prev.get("surface"), curr.get("surface"))
            elif pair.get("previous_surface") is not None:
                # inline
                pp = {(round(p[1], 3), round(p[2], 3)): p[3]
                      for p in pair["previous_surface"].get("points", [])}
                cp = {(round(p[1], 3), round(p[2], 3)): p[3]
                      for p in pair["current_surface"].get("points", [])}
                common = set(pp) & set(cp)
                dz = [cp[k] - pp[k] for k in common]
                sd = {
                    "matched_vertices": len(common),
                    "only_previous": len(set(pp) - set(cp)),
                    "only_current": len(set(cp) - set(pp)),
                    "delta_z": {
                        "n": len(dz),
                        "min": round(min(dz), 4) if dz else None,
                        "max": round(max(dz), 4) if dz else None,
                        "mean": round(sum(dz) / len(dz), 4) if dz else None,
                    } if dz else {},
                    "changed": bool(dz and any(abs(x) > 1e-6 for x in dz)),
                }
            if sd:
                sd["pair_index"] = i
                surface_diffs.append(sd)

            rd = raster_diff(prev, curr, params)
            rd["pair_index"] = i
            raster_diffs.append(rd)

        inv = {}
        if a.previous_package and a.current_package:
            inv = inventory_diff(read_json(a.previous_package), read_json(a.current_package))

        existing = {}
        if Path(a.out).is_file():
            try:
                existing = read_json(a.out)
            except ValueError:
                existing = {}

        result = {
            "rev_diff_version": existing.get("rev_diff_version") or "1.0",
            "previous_submittal": existing.get("previous_submittal"),
            "current_submittal": existing.get("current_submittal"),
            "pairs": existing.get("pairs") or pairs,
            "unmatched_previous": existing.get("unmatched_previous") or pairs_doc.get("unmatched_previous"),
            "unmatched_current": existing.get("unmatched_current") or pairs_doc.get("unmatched_current"),
            "revision_checks": existing.get("revision_checks") or [],
            "observations": existing.get("observations") or [],
            "entity_diffs": entity_diffs,
            "surface_diffs": surface_diffs,
            "raster_diffs": raster_diffs,
            "inventory_diff": inv or existing.get("inventory_diff") or {},
            "counts": {
                **(existing.get("counts") or {}),
                "entity_diffs": len(entity_diffs),
                "entities_changed": sum(1 for e in entity_diffs if e.get("changed")),
                "surfaces_changed": sum(1 for s in surface_diffs if s.get("changed")),
                "raster_changed": sum(1 for r in raster_diffs if r.get("changed")),
                "deliverables_added": len((inv or {}).get("deliverables_added") or []),
            },
            "meta": {
                **(existing.get("meta") or {}),
                "raster_params_tag": (params.get("raster") or {}).get("tag"),
                "change_priority": (params.get("change_priority")
                                    or ["dwg_entity_hash", "surface_delta_z", "pdf_raster"]),
                "skill": "comparing-drawing-revisions",
                "skill_version": __version__,
            },
        }
        atomic_write_json(a.out, result)
        emit({"status": "ok", "out": str(a.out),
              "entity_diffs": len(entity_diffs),
              "entities_changed": result["counts"]["entities_changed"],
              "deliverables_added": result["counts"]["deliverables_added"]})
        return EXIT_OK
    except ValueError as exc:
        return fail(EXIT_INPUT, str(exc))
    except OSError as exc:
        return fail(EXIT_INPUT, str(exc))


if __name__ == "__main__":
    sys.exit(main())
