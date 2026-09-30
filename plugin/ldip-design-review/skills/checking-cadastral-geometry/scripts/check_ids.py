#!/usr/bin/env python3
"""GIS-001 — Plot ID unique in the DLUS (SPC-0004 §7; §5.6: duplicates are excluded from matching).

Usage: python check_ids.py --dlus <dlus.json from read_dlus.py> --out <gis001.json>
Result: one pattern 'duplicate_plot_id' (count of IDs, rows, up to ten samples with cells) and the
excluded Plot ID list used by GIS-004…007. Exit 0 ran · 2 input error. No network, no shell calls."""
__version__ = "0.1.0"
import argparse, collections, json, os, sys
from pathlib import Path

MAX_SAMPLES = 10  # SPC-0004 §5.7


def check(dlus):
    if dlus["result"]["result"] != "pass" and any(i["pattern"] in ("header_not_found", "missing_column")
                                                 for i in dlus["result"]["issues"]):
        return {"rule_id": "GIS-001", "result": "not_run", "reason": "GIS-002 failed: DLUS not readable", "patterns": []}
    plots = [r for r in dlus["rows"] if not r["street"]]
    by_id = collections.defaultdict(list)
    for r in plots:
        by_id[r["plot_id"]].append(r)
    dups = {k: v for k, v in by_id.items() if len(v) > 1}
    order = sorted(dups, key=lambda k: (len(k), k))
    samples = [{"plot_id": k, "rows": [x["row"] for x in dups[k]], "cells": [x["cells"]["plot_id"] for x in dups[k]],
                "extract": "; ".join(f"{x['cells']['plot_id']}={k} ({x['level2']}, {x['land_area']})" for x in dups[k])}
               for k in order[:MAX_SAMPLES]]
    patterns = [] if not dups else [{
        "pattern": "duplicate_plot_id", "count_ids": len(dups), "count_rows": sum(len(v) for v in dups.values()),
        "samples": samples, "message": f"{len(dups)} Plot IDs appear more than once ({sum(len(v) for v in dups.values())} rows)"}]
    return {"rule_id": "GIS-001", "result": "fail" if dups else "pass", "patterns": patterns,
            "excluded_plot_ids": order, "plot_rows_checked": len(plots), "source": dlus["source"]}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--dlus", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    try:
        dlus = json.loads(Path(a.dlus).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "error", "code": 2, "detail": f"cannot read DLUS json: {exc}"}))
        return 2
    if len(str(Path(a.out).resolve())) > 250:
        print(json.dumps({"status": "error", "code": 2, "detail": "output path longer than 250 characters"}))
        return 2
    res = check(dlus)
    out = Path(a.out)
    tmp = out.with_name(out.name + ".tmp")
    tmp.write_text(json.dumps(res, indent=1, sort_keys=True, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, out)
    print(json.dumps({"status": "ok", "rule_id": "GIS-001", "result": res["result"],
                      "duplicate_ids": len(res.get("excluded_plot_ids", [])), "out": str(out)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
