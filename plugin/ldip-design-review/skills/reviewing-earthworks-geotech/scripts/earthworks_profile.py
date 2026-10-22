#!/usr/bin/env python3
"""Earthworks profile — information only, no findings (SPC-0006 Rev L §4.14.12 + L-a DL-16).

D4: cut/fill and road−grading use tin_raster_z (not O(faces²) nearest-face scans).

Usage:
  python earthworks_profile.py --surfaces <surfaces.json>
      [--slopes <slopes.json>] [--drainage <drainage.json>] [--presence <presence.json>]
      [--balance-quote "<report quote>"] --out <earthworks_profile.json>
"""
__version__ = "0.1.1"

import argparse
import math
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from ewk_common import (EXIT_OK, atomic_write_json, emit, iter_faces, load_params, load_tin,  # noqa: E402
                        main_guard, read_json, tin_raster_z)


def _cut_fill_raster(design_path, existing_path, cell_m=1.0, near_zero=0.05):
    """Area-weighted cut/fill via exact TIN rasters (D4)."""
    design, existing = load_tin(design_path), load_tin(existing_path)
    egrid, e0, n0, _e1, _n1 = tin_raster_z(existing, cell_m=cell_m)
    cut_a = fill_a = zero_a = 0.0
    cut_v = fill_v = 0.0
    dzs = []
    for _ids, p1, p2, p3, _s, _nz, cent, area in iter_faces(design):
        ix = int(math.floor((cent[0] - e0) / cell_m))
        iy = int(math.floor((cent[1] - n0) / cell_m))
        z_ex = egrid.get((ix, iy))
        if z_ex is None:
            continue
        dz = cent[2] - z_ex
        dzs.append(dz)
        if dz < -near_zero:
            cut_a += area
            cut_v += (-dz) * area
        elif dz > near_zero:
            fill_a += area
            fill_v += dz * area
        else:
            zero_a += area
    dzs.sort()

    def pct(p):
        if not dzs:
            return None
        i = min(len(dzs) - 1, max(0, int(round(p / 100.0 * (len(dzs) - 1)))))
        return round(dzs[i], 3)

    return {
        "cut_area_ha": round(cut_a / 10000.0, 4),
        "fill_area_ha": round(fill_a / 10000.0, 4),
        "near_zero_area_ha": round(zero_a / 10000.0, 4),
        "cut_volume_m3": round(cut_v, 1),
        "fill_volume_m3": round(fill_v, 1),
        "dz_p1": pct(1),
        "dz_p99": pct(99),
        "dz_min": round(dzs[0], 3) if dzs else None,
        "dz_max": round(dzs[-1], 3) if dzs else None,
        "method": "tin_raster_z",
        "cell_m": cell_m,
    }


def _slope_bands(tin_path, paved=None):
    tin = load_tin(tin_path)
    bands = {"lt_1": 0.0, "1_5": 0.0, "5_33": 0.0, "33_50": 0.0, "gt_50": 0.0}
    paved_a = unpaved_a = 0.0
    paved_set = None
    if paved and paved.get("cells"):
        paved_set = {tuple(c) for c in paved["cells"]}
        e0, n0, cell = paved["e0"], paved["n0"], paved["cell_m"]
    for _ids, p1, p2, p3, slope, _nz, cent, area in iter_faces(tin):
        in_paved = False
        if paved_set is not None:
            ix = int(math.floor((cent[0] - e0) / cell))
            iy = int(math.floor((cent[1] - n0) / cell))
            in_paved = (ix, iy) in paved_set
        if in_paved:
            paved_a += area
        else:
            unpaved_a += area
            if slope < 1.0:
                bands["lt_1"] += area
            elif slope < 5.0:
                bands["1_5"] += area
            elif slope < 33.3:
                bands["5_33"] += area
            elif slope <= 50.0:
                bands["33_50"] += area
            else:
                bands["gt_50"] += area
    return {"bands_m2": {k: round(v, 2) for k, v in bands.items()},
            "paved_ha": round(paved_a / 10000.0, 4),
            "unpaved_ha": round(unpaved_a / 10000.0, 4)}


def _road_minus_grading(road_path, design_path, cell_m=1.0):
    dgrid, e0, n0, _e1, _n1 = tin_raster_z(load_tin(design_path), cell_m=cell_m)
    depths = []
    for _ids, p1, p2, p3, _s, _nz, cent, _a in iter_faces(load_tin(road_path)):
        ix = int(math.floor((cent[0] - e0) / cell_m))
        iy = int(math.floor((cent[1] - n0) / cell_m))
        zg = dgrid.get((ix, iy))
        if zg is not None:
            depths.append(cent[2] - zg)
    depths.sort()
    return {
        "n": len(depths),
        "min": round(depths[0], 3) if depths else None,
        "max": round(depths[-1], 3) if depths else None,
        "median": round(depths[len(depths) // 2], 3) if depths else None,
        "method": "tin_raster_z",
        "cell_m": cell_m,
    }


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--surfaces", required=True)
    ap.add_argument("--slopes", default=None)
    ap.add_argument("--drainage", default=None)
    ap.add_argument("--presence", default=None)
    ap.add_argument("--balance-quote", default=None)
    ap.add_argument("--site", default=None)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)

    def run():
        surf = read_json(a.surfaces)
        params, _sp, _ = load_params(a.site)
        cell_m = float(params.get("cell_m", 1.0))
        by_role = {s["role"]: s for s in surf.get("surfaces") or []}
        profile = {
            "tool": {"script": "earthworks_profile.py", "version": __version__},
            "inventory": surf.get("surfaces") or [],
            "not_run_surfaces": surf.get("not_run") or [],
            "midp_missing": surf.get("midp_missing") or [],
            "cut_fill": None,
            "slope_bands": None,
            "road_top_minus_grading": None,
            "counts": {},
            "balance_quote": a.balance_quote,
            "note": "Information only — no rule / no finding (R-L4).",
        }
        design = by_role.get("design_grading") or by_role.get("finished_grading")
        existing = by_role.get("existing")
        road = by_role.get("road_top")
        if design and existing:
            profile["cut_fill"] = _cut_fill_raster(design["path"], existing["path"], cell_m=cell_m)
        if design:
            profile["slope_bands"] = _slope_bands(design["path"], surf.get("paved_footprint"))
        if design and road:
            profile["road_top_minus_grading"] = _road_minus_grading(
                road["path"], design["path"], cell_m=cell_m)
        if a.slopes:
            s = read_json(a.slopes)
            profile["counts"]["ewk001_groups"] = len(s.get("ewk001_groups") or [])
            profile["counts"]["ewk002_groups"] = len(s.get("ewk002_groups") or [])
            profile["counts"]["wall_faces"] = s.get("wall_faces") or 0
        if a.drainage:
            d = read_json(a.drainage)
            profile["counts"]["traps"] = len(d.get("traps") or [])
            profile["counts"]["depressions"] = d.get("n_depressions")
        if a.presence:
            p = read_json(a.presence)
            profile["counts"]["presence_checks"] = len(p.get("checks") or [])

        atomic_write_json(a.out, profile)
        emit({"status": "ok", "out": a.out, "has_cut_fill": profile["cut_fill"] is not None})
        return EXIT_OK

    return main_guard(run)


if __name__ == "__main__":
    sys.exit(main())
