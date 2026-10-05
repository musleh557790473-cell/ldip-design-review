#!/usr/bin/env python3
"""Schedule rules — GIS-003 (one Level 1 per Level 2), GIS-012 (Overall Summary = DLUS roll-up),
GIS-013 (GFA per plot), GIS-014 (population and parking vs Planning Assumptions) and GIS-015 (site
parameters across sheets). SPC-0004 §7, §5.3, §5.7; SPC-0006 §4.6.3. Cross-sheet rules live in
summary_rules.py.

Parameters (tolerances, fixed villa GFA) are read from the rules file written by
applying-ldip-rulebook/get_rules.py — never hard-coded. A rule absent from that file (inactive, or
not given to this agent) is reported as not_run.

Usage: python check_schedule.py --dlus <dlus.json> --rules <rules_GIS.json>
                                [--pa-file <workbook.xlsx|Planning Assumptions.csv>] [--pa-sheet "Planning Assumptions"]
                                [--summary-file <workbook.xlsx|Overall Summary.csv>] [--summary-sheet "Overall Summary"]
                                [--cf-file <workbook.xlsx|Community Facilities.csv>] [--cf-sheet "Community Facilities"]
                                --out <results.json>
A rule whose sheet is not given is reported as not_run with the reason.
One result per rule; one pattern per (rule, pattern) with count and up to ten samples (SPC-0004 §5.7).
Exit: 0 ran · 2 input error · 3 dependency. No network, no shell calls.
"""
__version__ = "0.2.0"

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from sched_io import (  # noqa: E402
    EXIT_INPUT, EXIT_OK, SchedError, cell_ref, check_path, find_label, header_map, load_grid, norm, to_num,
)
import summary_rules  # noqa: E402

MAX_SAMPLES = 10


def param(rules, rid, name):
    r = rules.get(rid)
    if r is None:
        return None
    p = r["parameters"].get(name)
    return None if p is None else p.get("value")


def is_villa(r):
    return "villa" in norm(r.get("level2")) and "residential" in norm(r.get("level1"))


def add(patterns, key, msg, sample):
    p = patterns.setdefault(key, {"pattern": key, "count": 0, "samples": [], "message": msg})
    p["count"] += 1
    if len(p["samples"]) < MAX_SAMPLES:
        p["samples"].append(sample)


def q(role, cell, value):
    """Quote one value with its role and cell (P-S3-43 / P-S3-24): 'role CELL=value', or 'role=value'
    when the column is absent. Each quoted value is the cell's own value, never a composite."""
    return f"{role} {cell}={value:g}" if cell else f"{role}={value:g}"


def finish(rid, patterns, extra=None):
    pats = sorted(patterns.values(), key=lambda p: p["pattern"])
    res = {"rule_id": rid, "result": "fail" if pats else "pass", "patterns": pats}
    res.update(extra or {})
    return res


# ------------------------------------------------------------------ GIS-013
def gis013(dlus, rules):
    if "GIS-013" not in rules:
        return {"rule_id": "GIS-013", "result": "not_run", "reason": "rule not in the rules file (inactive or not assigned)"}
    tol, villa_gfa = param(rules, "GIS-013", "tol"), param(rules, "GIS-013", "fixed_villa_gfa")
    if tol is None:
        return {"rule_id": "GIS-013", "result": "not_run", "reason": "tolerance 'tol' missing in the rulebook [GAP]"}
    pats, checked = {}, 0
    for r in dlus["rows"]:
        gfa = r.get("gfa")
        if r["street"] or not gfa:
            continue
        checked += 1
        c = r["cells"]
        if is_villa(r) and villa_gfa is not None:
            units = r.get("units") or 1.0
            exp = villa_gfa * units
            if abs(gfa - exp) > tol:
                add(pats, "villa_gfa_not_fixed",
                    f"Villa GFA shall be {villa_gfa:g} m2 per unit (GIS-013 fixed_villa_gfa)",
                    {"plot_id": r["plot_id"], "level2": r["level2"], "cells": [c["gfa"], c.get("units")],
                     "measured": round(gfa, 2), "required": round(exp, 2),
                     "extract": f"{q('gfa', c['gfa'], gfa)}; {q('units', c.get('units'), units)}; {q('area', c['land_area'], r['land_area'])}"})
        elif r.get("far"):
            exp = r["land_area"] * r["far"]
            if abs(gfa - exp) > tol:
                impl = gfa / r["land_area"] if r["land_area"] else 0.0
                add(pats, f"gfa_ne_area_x_far/FAR {r['far']:g} vs implied {impl:.2f}",
                    f"GFA shall equal Land Area x FAR (stated FAR {r['far']:g}; GFA implies {impl:.2f})",
                    {"plot_id": r["plot_id"], "level2": r["level2"], "cells": [c["land_area"], c["far"], c["gfa"]],
                     "measured": round(gfa, 2), "required": round(exp, 2),
                     "implied_far": round(gfa / r["land_area"], 3) if r["land_area"] else None,
                     "extract": f"{q('area', c['land_area'], r['land_area'])}; {q('far', c['far'], r['far'])}; {q('gfa', c['gfa'], gfa)}"})
        br = [r.get(k) for k in ("gfa_res", "gfa_com", "gfa_cf")]
        if all(x in (None, 0.0) for x in br):
            add(pats, f"gfa_without_breakdown/{r['level2']}",
                "GFA shall equal the sum of its breakdown (Residential + Commercial + Community Facilities)",
                {"plot_id": r["plot_id"], "level2": r["level2"], "cells": [c["gfa"]] + [c[k] for k in ("gfa_res", "gfa_com", "gfa_cf") if k in c],
                 "measured": round(gfa, 2), "required": 0.0, "extract": f"{c['gfa']}={gfa:g}; breakdown columns empty"})
        else:
            s = sum(x or 0.0 for x in br)
            if abs(gfa - s) > tol:
                add(pats, f"gfa_ne_breakdown/{r['level2']}",
                    "GFA shall equal the sum of its breakdown (Residential + Commercial + Community Facilities)",
                    {"plot_id": r["plot_id"], "level2": r["level2"], "cells": [c["gfa"]] + [c[k] for k in ("gfa_res", "gfa_com", "gfa_cf") if k in c],
                     "measured": round(gfa, 2), "required": round(s, 2),
                     "extract": "; ".join([q("gfa", c["gfa"], gfa)] + [q(k[4:], c.get(k), r.get(k) or 0.0)
                                                                       for k in ("gfa_res", "gfa_com", "gfa_cf") if c.get(k)])})
    return finish("GIS-013", pats, {"rows_checked": checked, "tolerance_m2": tol})


# ------------------------------------------------------------------ Planning Assumptions + GIS-014
def read_pa(path, sheet):
    grid, source = load_grid(path, sheet)
    loc = find_label(grid, "Level 1")
    if loc is None:
        raise SchedError(EXIT_INPUT, "Planning Assumptions: 'Level 1' header not found")
    hr, c1 = loc
    top = hr - 1 if hr > 0 else hr
    hmap = header_map(grid, top, rows=2)
    groups = {}
    for g in ("residents", "workers", "visitors", "parking"):
        rc, uc = hmap.get(f"{g}/rate"), hmap.get(f"{g}/unit")
        if rc is not None and uc is not None:
            groups[g] = (rc, uc)
    rows = []
    for r in range(hr + 1, len(grid)):
        l1, l2 = grid[r][c1], grid[r][c1 + 1] if c1 + 1 < len(grid[r]) else None
        if l1 in (None, "") and l2 in (None, ""):
            continue
        rec = {"row": r + 1, "level1": str(l1 or "").strip(), "level2": str(l2 or "").strip(), "rates": {}}
        for g, (rc, uc) in groups.items():
            rate, den = to_num(grid[r][rc]), to_num(grid[r][uc])
            basis = grid[r][uc + 1] if uc + 1 < len(grid[r]) else None
            if rate is not None and den:
                rec["rates"][g] = {"rate": rate, "per": den, "basis": str(basis or "").strip(),
                                   "cells": [cell_ref(r + 1, rc), cell_ref(r + 1, uc)]}
        rows.append(rec)
    return {"source": source, "rows": rows}


def gis014(dlus, rules, pa):
    if "GIS-014" not in rules:
        return {"rule_id": "GIS-014", "result": "not_run", "reason": "rule not in the rules file (inactive or not assigned)"}
    if pa is None:
        return {"rule_id": "GIS-014", "result": "not_run", "reason": "Planning Assumptions sheet not given (--pa-file)"}
    tol_p, tol_s = param(rules, "GIS-014", "tol_persons"), param(rules, "GIS-014", "tol_spaces")
    villa_pa = [x for x in pa["rows"] if "villa" in norm(x["level2"])]
    if not villa_pa:
        return {"rule_id": "GIS-014", "result": "not_run", "reason": "no Villas row in Planning Assumptions"}
    vp = villa_pa[0]["rates"]
    pats, checked, not_checked = {}, 0, 0
    for r in dlus["rows"]:
        if r["street"]:
            continue
        has_values = any(r.get(k) for k in ("residents", "workers", "visitors", "parking_req"))
        if not is_villa(r):
            not_checked += 1 if has_values else 0
            continue
        units = r.get("units")
        if not units:
            continue
        checked += 1
        c = r["cells"]
        for field, grp, tol, label in (("residents", "residents", tol_p, "residents"), ("parking_req", "parking", tol_s, "spaces")):
            rate = vp.get(grp)
            if rate is None or tol is None or r.get(field) is None:
                continue
            exp = rate["rate"] * units / rate["per"]
            if abs(r[field] - exp) > tol:
                add(pats, f"{field}_ne_planning_assumption/villas {r[field] / units:g} per unit",
                    f"{label} shall follow Planning Assumptions ({rate['rate']:g} per {rate['per']:g} {rate['basis']})",
                    {"plot_id": r["plot_id"], "level2": r["level2"], "cells": [c[field], c.get("units")], "pa_cells": rate["cells"],
                     "measured": round(r[field], 3), "required": round(exp, 3),
                     "measured_rate_per_unit": round(r[field] / units, 3),
                     "extract": f"{q(field, c[field], r[field])}; {q('units', c.get('units'), units)}; "
                                f"rate PA {rate['cells'][0]}={rate['rate']:g}; per PA {rate['cells'][1]}={rate['per']:g}"})
    return finish("GIS-014", pats, {"rows_checked": checked, "rows_not_checked_no_mapping": not_checked,
                                    "note": "Only villa rows are checked; other land uses need an agreed DLUS Level 2 -> "
                                            "Planning Assumptions mapping [GAP]",
                                    "planning_assumptions": pa["source"]})


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--dlus", required=True)
    ap.add_argument("--rules", required=True, help="rules file from applying-ldip-rulebook/get_rules.py")
    ap.add_argument("--pa-file")
    ap.add_argument("--pa-sheet", default="Planning Assumptions")
    ap.add_argument("--summary-file")
    ap.add_argument("--summary-sheet", default="Overall Summary")
    ap.add_argument("--cf-file")
    ap.add_argument("--cf-sheet", default="Community Facilities")
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    try:
        try:
            dlus = json.loads(Path(a.dlus).read_text(encoding="utf-8"))
            rb = json.loads(Path(a.rules).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SchedError(EXIT_INPUT, f"cannot read input json: {exc}") from exc
        rules = {r["rule_id"]: r for r in rb.get("rules", [])}
        check_path(a.out)
        gate = dlus.get("result", {})
        if any(i.get("pattern") in ("header_not_found", "missing_column") for i in gate.get("issues", [])):
            results = [{"rule_id": rid, "result": "not_run", "reason": "GIS-002 failed: DLUS not readable"}
                       for rid in ("GIS-003", "GIS-012", "GIS-013", "GIS-014", "GIS-015")]
        else:
            pa = read_pa(a.pa_file, a.pa_sheet) if a.pa_file else None
            summ = summary_rules.read_summary(a.summary_file, a.summary_sheet) if a.summary_file else None
            cf = summary_rules.read_cf(a.cf_file, a.cf_sheet) if a.cf_file else None
            results = [summary_rules.gis003(dlus, rules, summ), summary_rules.gis012(dlus, rules, summ),
                       gis013(dlus, rules), gis014(dlus, rules, pa), summary_rules.gis015(dlus, rules, summ, cf)]
        doc = {"script": f"check_schedule.py {__version__}", "rulebook_version": rb.get("rulebook_version"),
               "dlus_source": dlus.get("source"), "results": results}
        out = Path(a.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp = out.with_name(out.name + ".tmp")
        tmp.write_text(json.dumps(doc, indent=1, sort_keys=True, ensure_ascii=False, default=sorted), encoding="utf-8")
        os.replace(tmp, out)
        print(json.dumps({"status": "ok", "results": {r["rule_id"]: r["result"] for r in results}, "out": str(out)}))
        return EXIT_OK
    except SchedError as e:
        print(json.dumps({"status": "error", "code": e.code, "detail": e.detail}))
        return e.code


if __name__ == "__main__":
    sys.exit(main())
