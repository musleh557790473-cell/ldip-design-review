#!/usr/bin/env python3
"""Assemble surface inventory with roles for skill 13 (SPC-0006 Rev L §4.14.5 + L-a DL-15).

Roles are given by the caller (--surface role=path), MIDP-title default, or site parameter
surface_roles. Never guessed from geometry. Duplicate roles without a choice → not_run.
MIXED_LEVELS when a check would composite formation + finished levels.

MIDP (R1–R7): `--midp` accepts compact earthworks JSON or skill 6 `read_midp.py` output.
Optional `--inventory` (skill 6 files.json / package.json / plain list) judges DWG/NWC/DWFx/PDF.
Without inventory: format check is `not_run`; "model not received" only when no surface
(and no inventory file). Site filter AQ/AN/DN via `--site`.

Usage:
  python build_surfaces.py --surface design_grading=<tin.json> [--surface existing=<tin.json>]
      [--surface road_top=<tin.json>] [--midp <midp.json>] [--inventory <files.json>]
      [--site SITE] [--params <earthworks_params.json>] --out <surfaces.json>
Exit: 0 · 2 input.
"""
__version__ = "0.1.3"

import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from ewk_common import (EXIT_INPUT, EXIT_OK, EwkError, INVENTORY_ONLY_FORMATS,  # noqa: E402
                        adapt_midp_rows, atomic_write_json, emit, formats_present_for,
                        inventory_number_match, iter_faces, load_package_inventory,
                        load_params, load_tin, main_guard, midp_model_match, norm_format,
                        point_map, read_json, sha256_file)


VALID_ROLES = ("existing", "design_grading", "road_top", "finished_grading")


def _summarize(tin_path, role, title=None, source_format=None, source_file=None, document_no=None):
    tin = load_tin(tin_path)
    pts = point_map(tin)
    es = [p[0] for p in pts.values()]
    ns = [p[1] for p in pts.values()]
    zs = [p[2] for p in pts.values()]
    n_faces = 0
    area = 0.0
    for _ids, _p1, _p2, _p3, _s, _nz, _c, a in iter_faces(tin):
        n_faces += 1
        area += a
    doc = document_no or tin.get("document_no")
    src = source_file or tin.get("source_file") or Path(tin_path).name
    return {
        "role": role,
        "path": str(tin_path),
        "sha256": sha256_file(tin_path),
        "title": title or tin.get("surface"),
        "source_format": source_format or tin.get("source_format") or "landxml",
        "source_file": src,
        "document_no": doc,
        "product_id": tin.get("product_id"),
        "product_name": tin.get("product_name"),
        "internal_name": tin.get("product_name") or tin.get("ifc_header", {}).get("name") or tin.get("surface"),
        "layer_choice": tin.get("layer_choice"),
        "n_points": len(pts),
        "n_faces": n_faces,
        "plan_area_m2": round(area, 3),
        "plan_area_ha": round(area / 10000.0, 4),
        "extent": {"e_min": min(es), "e_max": max(es), "n_min": min(ns), "n_max": max(ns)} if es else None,
        "z_range": {"min": min(zs), "max": max(zs)} if zs else None,
        "not_run": None,
    }


def paved_footprint_from_road_top(road_tin_path, cell_m=1.0):
    """Rasterised footprint cells of the road_top TIN (set of (ix,iy) in local grid)."""
    from ewk_common import tin_raster_z
    grid, e0, n0, e1, n1 = tin_raster_z(load_tin(road_tin_path), cell_m=cell_m)
    return {"cells": sorted(grid.keys()), "e0": e0, "n0": n0, "cell_m": cell_m,
            "n_cells": len(grid), "source": "road_top"}


def _load_midp_entries(midp_path, site=None):
    """Return (entries_at_relevant_stages, schedules_30: bool|None)."""
    midp = read_json(midp_path)
    if isinstance(midp, dict) and (midp.get("tool", {}).get("name") == "read_midp.py" or (
            "rows" in midp and "files" in midp)):
        entries = adapt_midp_rows(midp, stage_hint="30", site=site)
        return entries, bool(entries)
    if isinstance(midp, list):
        entries = midp
    else:
        entries = midp.get("models") or midp.get("deliverables") or midp.get("rows") or []
        if midp.get("rows") and not midp.get("models"):
            entries = adapt_midp_rows(midp, stage_hint="30", site=site)
            return entries, bool(entries)
    at_30 = []
    for entry in entries:
        ms = entry.get("milestone")
        stage = entry.get("stage")
        num = entry.get("number") or entry.get("deliverable") or ""
        from ewk_common import row_matches_site
        if site and not row_matches_site(num, site):
            continue
        if ms in (30, "30") or stage in ("30%", "30% Concept Design", "PD", 30):
            at_30.append(entry)
        elif ms is None and stage is None:
            at_30.append(entry)
    return at_30, bool(at_30) if entries else False


def _surface_match(surfaces, entry):
    for s in surfaces:
        ok, by, note = midp_model_match({
            "document_no": s.get("document_no"), "source_file": s.get("source_file"),
            "number": s.get("document_no"), "title": s.get("title"),
            "role": s.get("role"), "internal_name": s.get("internal_name"),
            "path": s.get("path"),
        }, entry)
        if ok:
            return s, by, note
    return None, None, None


def _inventory_has_file(inv_files, entry):
    """S1: number match in package inventory → present (matched_by inventory)."""
    for f in inv_files or []:
        if inventory_number_match(f, entry):
            return True
    return False


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--surface", action="append", default=[],
                    help="role=path  (existing|design_grading|road_top|finished_grading)")
    ap.add_argument("--landxml", action="append", default=[],
                    help="role=landxml_ingestion.json (uses sole tin_file or fails multi)")
    ap.add_argument("--civil3d-dwg", action="append", default=[],
                    help="role=path — records not_run (Civil 3D DWG not readable)")
    ap.add_argument("--midp", default=None,
                    help="earthworks MIDP JSON or skill 6 read_midp.py output")
    ap.add_argument("--inventory", default=None,
                    help="skill 6 inventory / package.json / plain file list (R1)")
    ap.add_argument("--site", default=None)
    ap.add_argument("--params", default=None)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)

    def run():
        params, site_p, _doc = load_params(a.site)
        if a.params:
            extra = read_json(a.params)
            params.update(extra.get("detection") or extra)
        inventory = []
        not_run = []
        for item in a.surface:
            if "=" not in item:
                raise EwkError(EXIT_INPUT, f"--surface needs role=path, got {item!r}")
            role, path = item.split("=", 1)
            role = role.strip()
            if role not in VALID_ROLES:
                raise EwkError(EXIT_INPUT, f"unknown role {role!r}")
            inventory.append(_summarize(path.strip(), role))
        for item in a.landxml:
            if "=" not in item:
                raise EwkError(EXIT_INPUT, f"--landxml needs role=path, got {item!r}")
            role, path = item.split("=", 1)
            role = role.strip()
            ing = read_json(path.strip())
            surfaces = (ing.get("content") or {}).get("surfaces") or []
            parent = Path(path.strip()).resolve().parent
            if not surfaces:
                not_run.append({"role": role, "reason": "no design surface in the package"})
                continue
            if len(surfaces) > 1:
                not_run.append({"role": role, "reason": "multiple surfaces; pass an explicit tin side file"})
                continue
            tin_name = surfaces[0].get("tin_file")
            tin_path = parent / tin_name
            if not tin_path.is_file():
                raise EwkError(EXIT_INPUT, f"tin_file not found: {tin_name}")
            src_name = Path((ing.get("source") or {}).get("path") or path).name
            inventory.append(_summarize(
                tin_path, role, title=surfaces[0].get("name"), source_format="landxml",
                source_file=src_name,
                document_no=(ing.get("source") or {}).get("document_no"),
            ))
        for item in a.civil3d_dwg:
            role, path = (item.split("=", 1) + [item])[:2] if "=" in item else ("design_grading", item)
            not_run.append({
                "role": role.strip(),
                "path": path.strip(),
                "reason": "surface in Civil 3D DWG not readable — LandXML or IFC needed",
            })

        by_role = {}
        for s in inventory:
            by_role.setdefault(s["role"], []).append(s)
        for role, items in by_role.items():
            if len(items) > 1:
                raise EwkError(EXIT_INPUT,
                               f"two surfaces with role {role!r} and no choice given")

        mixed_levels_forbidden = ("design_grading" in by_role) and ("finished_grading" in by_role)

        paved = None
        if "road_top" in by_role:
            paved = paved_footprint_from_road_top(by_role["road_top"][0]["path"],
                                                  cell_m=float(params.get("cell_m", 1.0)))
        elif params.get("network_footprint"):
            paved = {"source": "network", "path": params["network_footprint"]}

        inv_files = load_package_inventory(a.inventory) if a.inventory else None
        # N2: carry inventory path/count into surfaces.json for MI "Searched:" text
        inventory_meta = None
        if a.inventory:
            inventory_meta = {
                "path": str(a.inventory),
                "file_name": Path(a.inventory).name,
                "n_files": len(inv_files or []),
            }
        midp_missing = []
        midp_not_run = []
        midp_schedules_30 = None
        midp_entries = []
        midp_matches = []
        if a.midp:
            midp_entries, midp_schedules_30 = _load_midp_entries(a.midp, site=a.site)
            for entry in midp_entries:
                # S1: (1) number in surfaces → (1b) number in inventory →
                # (2) role only for -MOD- → (3) real title; never -DWG- → surface
                surf, matched_by, note = _surface_match(inventory, entry)
                has_file = _inventory_has_file(inv_files, entry) if inv_files is not None else False
                if surf is None and has_file:
                    matched_by, note = "inventory", None
                formats_needed = entry.get("formats") or entry.get("exchange_formats") or []
                role = entry.get("role")
                is_surface_role = role in VALID_ROLES

                if surf is None and not has_file:
                    # R1: model not received only when no surface and no inventory file
                    # Without inventory: raise for surface-role carriers; drawing → not_run
                    if inv_files is None and not is_surface_role:
                        midp_not_run.append({
                            "deliverable": entry.get("number") or entry.get("deliverable"),
                            "title": entry.get("title"),
                            "reason": "format/file check needs --inventory (drawing carrier)",
                        })
                        continue
                    midp_missing.append({
                        "deliverable": entry.get("number") or entry.get("deliverable"),
                        "title": entry.get("title"),
                        "role": role,
                        "formats_missing": formats_needed or (["IFC"] if is_surface_role else ["DWG"]),
                        "reason": "model not received",
                    })
                    continue

                # S2: after role match, formats via matched file's own number in inventory
                have = formats_present_for(
                    entry, inventory, inv_files,
                    matched_surface=surf, matched_by=matched_by if surf else None,
                )
                if inv_files is None:
                    # R1: without inventory — format check not_run; never DWG/NWC/DWFx/PDF missing
                    surface_fmts = {norm_format(f) for f in formats_needed
                                    if norm_format(f) not in INVENTORY_ONLY_FORMATS}
                    missing_surface = [f for f in formats_needed
                                       if norm_format(f) in surface_fmts
                                       and norm_format(f) not in have]
                    inv_only = [f for f in formats_needed
                                if norm_format(f) in INVENTORY_ONLY_FORMATS]
                    if inv_only:
                        midp_not_run.append({
                            "deliverable": entry.get("number") or entry.get("deliverable"),
                            "title": entry.get("title"),
                            "formats_unchecked": inv_only,
                            "reason": "format check needs --inventory",
                        })
                    if missing_surface:
                        midp_missing.append({
                            "deliverable": entry.get("number") or entry.get("deliverable"),
                            "title": entry.get("title"),
                            "role": role,
                            "formats_missing": missing_surface,
                            "reason": "format missing",
                            "matched_by": matched_by or "document_no/source_file",
                            "note": note,
                        })
                else:
                    missing = [f for f in formats_needed if norm_format(f) not in have]
                    if missing:
                        midp_missing.append({
                            "deliverable": entry.get("number") or entry.get("deliverable"),
                            "title": entry.get("title"),
                            "role": role,
                            "formats_missing": missing,
                            "reason": "format missing",
                            "matched_by": matched_by,
                            "note": note,
                        })
                if matched_by:
                    midp_matches.append({
                        "deliverable": entry.get("number") or entry.get("deliverable"),
                        "matched_by": matched_by,
                        "note": note,
                        "surface": (surf or {}).get("path"),
                    })

        out = {
            "tool": {"script": "build_surfaces.py", "version": __version__},
            "site": a.site,
            "surfaces": inventory,
            "not_run": not_run,
            "paved_footprint": paved,
            "mixed_levels_forbidden": mixed_levels_forbidden,
            "midp_missing": midp_missing,
            "midp_not_run": midp_not_run,
            "midp_matches": midp_matches,
            "midp_schedules_carriers_at_30": midp_schedules_30,
            "midp_entries_at_30": len(midp_entries),
            "inventory": inventory_meta,
            "inventory_used": bool(inv_files is not None),
            "inventory_files": len(inv_files or []),
            "roles_present": sorted(by_role.keys()),
        }
        atomic_write_json(a.out, out)
        emit({"status": "ok", "out": a.out, "surfaces": len(inventory), "not_run": len(not_run),
              "midp_missing": len(midp_missing), "midp_not_run": len(midp_not_run),
              "midp_schedules_carriers_at_30": midp_schedules_30,
              "roles": sorted(by_role.keys())})
        return EXIT_OK

    return main_guard(run)


if __name__ == "__main__":
    sys.exit(main())
