#!/usr/bin/env python3
"""GIS-002 — read the DLUS (Detailed Land Use Schedule) per SPC-0004 §5.2.

Header located by its labels; required columns Plot ID, Level 1, Level 2, Land Area; plot rows start
under the two header rows and end at the FIRST row with an empty Plot ID (anything below is not
plot data); a row whose Plot ID is '-' is a non-plot (street) row — counted in areas, not in plots.

Usage: python read_dlus.py --file <schedule.xlsx|DLUS.csv> [--sheet DLUS] --out <dlus.json>
Writes the normalised DLUS (every value with its cell reference) and a GIS-002 result.
Exit: 0 read (GIS-002 pass or fail is in the result) · 2 input error · 3 dependency.
No network, no shell calls.
"""
__version__ = "0.1.0"

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from sched_io import (  # noqa: E402
    EXIT_OK, SchedError, cell_ref, check_path, col_letter, find_label, header_map, is_dash, load_grid, to_num,
)

REQUIRED = {"plot_id": ["plot id"], "level1": ["level 1", "land use/level 1"],
            "level2": ["level 2", "land use/level 2"], "land_area": ["land area"]}
OPTIONAL = {
    "plots": ["plots"], "buildings": ["buildings"], "units": ["units"], "height": ["height"], "far": ["far"],
    "gfa": ["gfa"],
    "gfa_res": ["gfa breakdown (m2)/residential"],
    "gfa_com": ["gfa breakdown (m2)/retail", "gfa breakdown (m2)/commercial"],
    "gfa_cf": ["gfa breakdown (m2)/comm. fac."],
    "residents": ["population/residents", "residents"], "workers": ["population/workers", "workers"],
    "visitors": ["population/visitors", "visitors"],
    "parking_req": ["parking/required", "required"], "parking_prov": ["parking/provided", "provided"],
}
TEXT_FIELDS = ("plot_id", "level1", "level2", "height")


def pick(hmap, names):
    for n in names:
        if n in hmap:
            return hmap[n]
    return None


def read(path, sheet="DLUS"):
    grid, source = load_grid(path, sheet)
    loc = find_label(grid, "Plot ID")
    issues = []
    if loc is None:
        return {"source": source, "rows": [], "result": {
            "rule_id": "GIS-002", "result": "fail",
            "issues": [{"pattern": "header_not_found", "message": "no 'Plot ID' header found in the first 40 rows"}]}}
    hr, _ = loc
    hmap = header_map(grid, hr, rows=2)
    cols = {k: pick(hmap, v) for k, v in {**REQUIRED, **OPTIONAL}.items()}
    missing = [k for k in REQUIRED if cols[k] is None]
    for k in missing:
        issues.append({"pattern": "missing_column", "column": k, "message": f"required column '{k}' not found"})
    rows, end = [], None
    if not missing:
        for r in range(hr + 2, len(grid)):
            pid = grid[r][cols["plot_id"]]
            if pid is None or str(pid).strip() == "":
                end = r
                break
            rec = {"row": r + 1, "street": is_dash(pid), "cells": {}}
            for k, c in cols.items():
                if c is None:
                    continue
                raw = grid[r][c]
                rec["cells"][k] = cell_ref(r + 1, c)
                if k in TEXT_FIELDS:
                    rec[k] = None if raw is None else (int(raw) if isinstance(raw, float) and raw.is_integer() else raw)
                    if isinstance(rec[k], str):
                        rec[k] = rec[k].strip()
                else:
                    rec[k] = 0.0 if is_dash(raw) else to_num(raw)
            rec["plot_id"] = str(rec["plot_id"]).strip() if rec.get("plot_id") is not None else None
            if rec.get("land_area") is None:
                issues.append({"pattern": "unparseable_area", "plot_id": rec["plot_id"],
                               "cell": rec["cells"]["land_area"], "value": str(grid[r][cols["land_area"]]),
                               "message": "Land Area missing or not a number"})
            rows.append(rec)
        end = end if end is not None else len(grid)
    trailing = sum(1 for r in range(end + 1 if end is not None else len(grid), len(grid))
                   if any(v not in (None, "") for v in grid[r])) if not missing else 0
    plot_rows = sum(1 for x in rows if not x["street"])
    return {
        "reader": f"read_dlus.py {__version__}", "source": source, "header_row": hr + 1,
        "columns": {k: col_letter(c) for k, c in cols.items() if c is not None},
        "rows": rows, "plot_rows": plot_rows, "street_rows": len(rows) - plot_rows,
        "first_empty_row": (end + 1) if end is not None else None, "trailing_rows_ignored": trailing,
        "result": {"rule_id": "GIS-002", "result": "fail" if issues else "pass", "issues": issues},
    }


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", required=True)
    ap.add_argument("--sheet", default="DLUS")
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    try:
        out = check_path(a.out)
        d = read(a.file, a.sheet)
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp = out.with_name(out.name + ".tmp")
        tmp.write_text(json.dumps(d, indent=1, sort_keys=True, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, out)
        print(json.dumps({"status": "ok", "rule_id": "GIS-002", "result": d["result"]["result"],
                          "plot_rows": d.get("plot_rows", 0), "street_rows": d.get("street_rows", 0),
                          "trailing_rows_ignored": d.get("trailing_rows_ignored", 0),
                          "issues": len(d["result"]["issues"]), "out": str(out)}))
        return EXIT_OK
    except SchedError as e:
        print(json.dumps({"status": "error", "code": e.code, "detail": e.detail}))
        return e.code


if __name__ == "__main__":
    sys.exit(main())
