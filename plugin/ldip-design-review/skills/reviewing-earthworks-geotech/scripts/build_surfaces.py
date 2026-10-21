#!/usr/bin/env python3
"""Assemble surface inventory with roles for skill 13 (SPC-0006 Rev L §4.14.5 DL-2/DL-3 + L-a).

Roles are given by the caller (--role path=role), MIDP-title default, or site parameter
surface_roles. Never guessed from geometry. Duplicate roles without a choice → not_run.
MIXED_LEVELS when a check would composite formation + finished levels.

Usage:
  python build_surfaces.py --surface design_grading=<tin.json> [--surface existing=<tin.json>]
      [--surface road_top=<tin.json>] [--midp <midp_models.json>] [--site SITE]
      [--params <earthworks_params.json>] --out <surfaces.json>
Exit: 0 · 2 input.
"""
__version__ = "0.1.0"

import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from ewk_common import (EXIT_INPUT, EXIT_OK, EwkError, atomic_write_json, emit, iter_faces,  # noqa: E402
                        load_params, load_tin, main_guard, midp_model_match, point_map,
                        propose_role_from_title, read_json, sha256_file)


VALID_ROLES = ("existing", "design_grading", "road_top", "finished_grading")


def _summarize(tin_path, role, title=None, source_format=None):
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
    return {
        "role": role,
        "path": str(tin_path),
        "sha256": sha256_file(tin_path),
        "title": title or tin.get("surface"),
        "source_format": source_format or tin.get("source_format") or "landxml",
        "product_id": tin.get("product_id"),
        "product_name": tin.get("product_name"),
        "internal_name": tin.get("product_name") or tin.get("surface"),
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


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--surface", action="append", default=[],
                    help="role=path  (existing|design_grading|road_top|finished_grading)")
    ap.add_argument("--landxml", action="append", default=[],
                    help="role=landxml_ingestion.json (uses sole tin_file or fails multi)")
    ap.add_argument("--civil3d-dwg", action="append", default=[],
                    help="role=path — records not_run (Civil 3D DWG not readable)")
    ap.add_argument("--midp", default=None, help="JSON list of MIDP model entries for DL-15")
    ap.add_argument("--site", default=None)
    ap.add_argument("--params", default=None)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)

    def run():
        params, site_p, _doc = load_params(a.site)
        if a.params:
            extra = read_json(a.params)
            params.update(extra.get("detection") or extra)
        role_overrides = dict(params.get("surface_roles") or site_p.get("surface_roles") or {})
        inventory = []
        not_run = []
        # Explicit --surface role=path
        for item in a.surface:
            if "=" not in item:
                raise EwkError(EXIT_INPUT, f"--surface needs role=path, got {item!r}")
            role, path = item.split("=", 1)
            role = role.strip()
            if role not in VALID_ROLES:
                raise EwkError(EXIT_INPUT, f"unknown role {role!r}")
            inventory.append(_summarize(path.strip(), role))
        # LandXML ingestions
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
            inventory.append(_summarize(tin_path, role, title=surfaces[0].get("name"),
                                        source_format="landxml"))
        # Civil 3D DWG — not readable
        for item in a.civil3d_dwg:
            role, path = (item.split("=", 1) + [item])[:2] if "=" in item else ("design_grading", item)
            not_run.append({
                "role": role.strip(),
                "path": path.strip(),
                "reason": "surface in Civil 3D DWG not readable — LandXML or IFC needed",
            })

        # Apply title/role overrides from MIDP / site when role missing — but all above have roles.
        # Role collision check
        by_role = {}
        for s in inventory:
            by_role.setdefault(s["role"], []).append(s)
        for role, items in by_role.items():
            if len(items) > 1:
                raise EwkError(EXIT_INPUT,
                               f"two surfaces with role {role!r} and no choice given")

        # MIXED_LEVELS guard flag (consumers refuse composite of formation + finished)
        has_design = "design_grading" in by_role
        has_finished = "finished_grading" in by_role
        mixed_levels_forbidden = has_design and has_finished

        # Paved footprint
        paved = None
        if "road_top" in by_role:
            paved = paved_footprint_from_road_top(by_role["road_top"][0]["path"],
                                                  cell_m=float(params.get("cell_m", 1.0)))
        elif params.get("network_footprint"):
            paved = {"source": "network", "path": params["network_footprint"]}

        # DL-15 MIDP missing models
        midp_missing = []
        if a.midp:
            midp = read_json(a.midp)
            entries = midp if isinstance(midp, list) else midp.get("models") or midp.get("deliverables") or []
            models_present = []
            for s in inventory:
                models_present.append({
                    "number": s.get("title"),
                    "name": s.get("title"),
                    "title": s.get("title"),
                    "role": s["role"],
                    "internal_name": s.get("internal_name"),
                    "formats": [s.get("source_format")],
                })
            for entry in entries:
                if entry.get("milestone") not in (30, "30", None) and entry.get("required_from_milestone") not in (30, "30", None):
                    # still check if scheduled at 30
                    if entry.get("stage") not in ("30%", "30% Concept Design", 30, "PD"):
                        continue
                matched = next((m for m in models_present if midp_model_match(m, entry)), None)
                formats_needed = entry.get("formats") or entry.get("exchange_formats") or ["IFC"]
                if matched is None:
                    midp_missing.append({
                        "deliverable": entry.get("number") or entry.get("deliverable"),
                        "title": entry.get("title"),
                        "role": entry.get("role"),
                        "formats_missing": formats_needed,
                        "reason": "model not received",
                    })
                else:
                    have = {f.lower() for f in (matched.get("formats") or [])}
                    miss = [f for f in formats_needed if f.lower() not in have
                            and not (f.lower() in ("ifc",) and "ifc" in have)
                            and not (f.lower() in ("xml", "landxml") and matched.get("formats"))]
                    # Format XML missing while IFC present → MI for XML only (ADDENDUM §4)
                    need_xml = [f for f in formats_needed if f.lower() in ("xml", "landxml")]
                    have_xml = any(f.lower() in ("xml", "landxml") for f in (matched.get("formats") or []))
                    if need_xml and not have_xml and entry.get("require_xml"):
                        midp_missing.append({
                            "deliverable": entry.get("number") or entry.get("deliverable"),
                            "title": entry.get("title"),
                            "role": entry.get("role"),
                            "formats_missing": need_xml,
                            "reason": "format missing",
                            "matched_by": "title+role" if matched else None,
                        })

        out = {
            "tool": {"script": "build_surfaces.py", "version": __version__},
            "site": a.site,
            "surfaces": inventory,
            "not_run": not_run,
            "paved_footprint": paved,
            "mixed_levels_forbidden": mixed_levels_forbidden,
            "midp_missing": midp_missing,
            "roles_present": sorted(by_role.keys()),
        }
        atomic_write_json(a.out, out)
        emit({"status": "ok", "out": a.out, "surfaces": len(inventory), "not_run": len(not_run),
              "midp_missing": len(midp_missing), "roles": sorted(by_role.keys())})
        return EXIT_OK

    return main_guard(run)


if __name__ == "__main__":
    sys.exit(main())
