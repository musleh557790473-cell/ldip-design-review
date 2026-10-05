"""Cross-sheet rules — GIS-003 (one Level 1 per Level 2), GIS-012 (Overall Summary = DLUS roll-up)
and GIS-015 (site parameters agree across Community Facilities, Overall Summary and DLUS).
SPC-0004 §5.2, §5.3, §5.7, §7; SPC-0006 §4.6.3.

Parameters come from the rules file written by applying-ldip-rulebook/get_rules.py. A rule absent
from that file is reported as not_run. Level 1 and Level 2 are compared trimmed and case-insensitive.

Library used by check_schedule.py (run that script; do not run this module).
No network, no shell calls.
"""
__version__ = "0.2.0"

import itertools
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from sched_io import EXIT_INPUT, SchedError, cell_ref, find_label, header_map, load_grid, norm, to_num  # noqa: E402

MAX_SAMPLES = 10
MAX_SUBSET = 12          # reconciliation: subsets of up to this many missing groups are tried


def param(rules, rid, name):
    p = (rules.get(rid) or {}).get("parameters", {}).get(name)
    return None if p is None else p.get("value")


def add(patterns, key, msg, sample, source=None):
    p = patterns.setdefault(key, {"pattern": key, "count": 0, "samples": [], "message": msg})
    if source:
        p["source"] = source
    p["count"] += 1
    if len(p["samples"]) < MAX_SAMPLES:
        p["samples"].append(sample)


def finish(rid, patterns, extra=None):
    pats = sorted(patterns.values(), key=lambda p: p["pattern"])
    res = {"rule_id": rid, "result": "fail" if pats else "pass", "patterns": pats}
    res.update(extra or {})
    return res


def is_villa(level1, level2):
    return "villa" in norm(level2) and "residential" in norm(level1)


# ------------------------------------------------------------------ readers
def read_summary(path, sheet):
    """Overall Summary: rows between the header and TOTAL, then labelled reconciliation lines
    (a number in the Land Area column and a text label) up to the first blank row."""
    grid, source = load_grid(path, sheet)
    loc = find_label(grid, "Level 1")
    if loc is None:
        raise SchedError(EXIT_INPUT, "Overall Summary: 'Level 1' header not found")
    hr, c1 = loc
    top = hr - 1 if hr > 0 else hr
    hmap = header_map(grid, top, rows=2)
    cols = {"level1": c1, "level2": hmap.get("level 2", hmap.get("land use/level 2")),
            "land_area": hmap.get("land area"), "plots": hmap.get("plots"),
            "height": hmap.get("height"), "far": hmap.get("far")}
    for k in ("level2", "land_area", "plots"):
        if cols[k] is None:
            raise SchedError(EXIT_INPUT, f"Overall Summary: column '{k}' not found")
    rows, total, recon, r = [], None, [], hr + 1
    while r < len(grid):
        row = grid[r]
        l1, l2 = row[cols["level1"]], row[cols["level2"]]
        if norm(l2) == "total" or norm(l1) == "total":
            total = {"row": r + 1, "land_area": to_num(row[cols["land_area"]]), "plots": to_num(row[cols["plots"]]),
                     "cells": {"land_area": cell_ref(r + 1, cols["land_area"]), "plots": cell_ref(r + 1, cols["plots"])}}
            r += 1
            break
        if l1 not in (None, "") or l2 not in (None, ""):
            rec = {"row": r + 1, "level1": str(l1 or "").strip(), "level2": str(l2 or "").strip(), "cells": {}}
            for k, c in cols.items():
                if c is None:
                    continue
                rec["cells"][k] = cell_ref(r + 1, c)
                if k in ("land_area", "plots", "far"):
                    rec[k] = to_num(row[c])
                elif k == "height":
                    rec[k] = row[c]
            rows.append(rec)
        r += 1
    while total is not None and r < len(grid):
        row = grid[r]
        if all(v in (None, "") for v in row):
            break
        val = to_num(row[cols["land_area"]])
        labels = [str(v).strip() for j, v in enumerate(row)
                  if j != cols["land_area"] and isinstance(v, str) and v.strip() and to_num(v) is None]
        if val is not None and labels and not any("check" in norm(x) for x in labels):
            recon.append({"row": r + 1, "value": val, "label": "; ".join(labels),
                          "cell": cell_ref(r + 1, cols["land_area"])})
        r += 1
    return {"source": source, "rows": rows, "total": total, "reconciliation": recon}


def read_cf(path, sheet):
    """Community Facilities header block: Site Area (m2), Residents, Density (pr/ha)."""
    grid, source = load_grid(path, sheet)
    out = {"source": source}
    for key, label in (("site_area", "Site Area (m2)"), ("residents", "Residents"), ("density", "Density (pr/ha)")):
        loc = find_label(grid, label, max_rows=15)
        if loc is None:
            continue
        r, c = loc
        for j in range(c + 1, len(grid[r])):
            v = to_num(grid[r][j])
            if v is not None:
                out[key] = {"value": v, "cell": cell_ref(r + 1, j), "label_cell": cell_ref(r + 1, c)}
                break
    return out


# ------------------------------------------------------------------ DLUS roll-up
def rollup(dlus):
    g = {}
    for r in dlus["rows"]:
        k = norm(r.get("level2"))
        x = g.setdefault(k, {"level2": str(r.get("level2") or "").strip(), "level1": set(), "plots": 0, "area": 0.0,
                             "rows": [], "far": {}, "height": {}})
        x["level1"].add(str(r.get("level1") or "").strip())
        x["plots"] += 0 if r["street"] else 1
        x["area"] += r.get("land_area") or 0.0
        x["rows"].append(r["row"])
        if r.get("far") not in (None, 0.0):
            x["far"].setdefault(r["far"], []).append(r)
        if r.get("height") not in (None, ""):
            x["height"].setdefault(str(r["height"]).strip(), []).append(r)
    return g


def row_span(rows):
    return f"{min(rows)}-{max(rows)}" if len(rows) > 1 else str(rows[0])


def dlus_agg(dlus, x):
    """Re-computable DLUS aggregate for one Level 2 (P-S3-24): column ranges and the key, e.g.
    'dlus_sum DLUS sum(D4:D878 if C=Amenity Green)=35,995.52; dlus_count DLUS count(C4:C878 = Amenity Green)=81'.
    Empty when the DLUS column letters are not known."""
    cols = dlus.get("columns") or {}
    a, l2 = cols.get("land_area"), cols.get("level2")
    if not (a and l2):
        return ""
    r0, r1 = min(x["rows"]), max(x["rows"])
    return (f"dlus_sum DLUS sum({a}{r0}:{a}{r1} if {l2}={x['level2']})={x['area']:,.2f}; "
            f"dlus_count DLUS count({l2}{r0}:{l2}{r1} = {x['level2']})={x['plots']}")


# ------------------------------------------------------------------ GIS-003
def gis003(dlus, rules, summ):
    if "GIS-003" not in rules:
        return {"rule_id": "GIS-003", "result": "not_run", "reason": "rule not in the rules file (inactive or not assigned)"}
    if summ is None:
        return {"rule_id": "GIS-003", "result": "not_run", "reason": "Overall Summary not given (--summary-file)"}
    by_l2 = {}
    for r in dlus["rows"]:
        if r["street"]:
            continue
        e = by_l2.setdefault(norm(r.get("level2")), {"name": str(r.get("level2") or "").strip(), "dlus": {}, "summary": {}})
        e["dlus"].setdefault(norm(r.get("level1")), (str(r.get("level1") or "").strip(), r["cells"]["level1"]))
    for s in summ["rows"]:
        if norm(s["level2"]) == "street":
            continue
        e = by_l2.setdefault(norm(s["level2"]), {"name": s["level2"], "dlus": {}, "summary": {}})
        e["summary"].setdefault(norm(s["level1"]), (s["level1"], s["cells"]["level1"]))
    pats = {}
    for k, e in sorted(by_l2.items()):
        l1s = set(e["dlus"]) | set(e["summary"])
        if len(l1s) > 1:
            d = "; ".join(f"DLUS {v[1]}={v[0]}" for v in e["dlus"].values())
            s = "; ".join(f"Summary {v[1]}={v[0]}" for v in e["summary"].values())
            add(pats, "level2_under_several_level1",
                "A Level 2 land use sits under more than one Level 1 class",
                {"plot_id": None, "level2": e["name"], "level1_dlus": sorted(v[0] for v in e["dlus"].values()),
                 "level1_summary": sorted(v[0] for v in e["summary"].values()),
                 "cells": [v[1] for v in e["summary"].values()], "extract": f"{e['name']}: {d}; {s}"},
                source=summ["source"])
    return finish("GIS-003", pats, {"level2_checked": len(by_l2),
                                    "subchecks_skipped": ["controlled_vocabulary (list not received) [GAP]"]})


# ------------------------------------------------------------------ GIS-012
def gis012(dlus, rules, summ):
    if "GIS-012" not in rules:
        return {"rule_id": "GIS-012", "result": "not_run", "reason": "rule not in the rules file (inactive or not assigned)"}
    if summ is None:
        return {"rule_id": "GIS-012", "result": "not_run", "reason": "Overall Summary not given (--summary-file)"}
    tol = param(rules, "GIS-012", "area_tol")
    if tol is None:
        return {"rule_id": "GIS-012", "result": "not_run", "reason": "tolerance 'area_tol' missing in the rulebook [GAP]"}
    g, pats, src = rollup(dlus), {}, summ["source"]
    srows = {}
    for s in summ["rows"]:
        srows.setdefault(norm(s["level2"]), []).append(s)
    recon = list(summ["reconciliation"])
    used = set()

    def reconciled(amount):
        for i, line in enumerate(recon):
            if i not in used and abs(abs(line["value"]) - abs(amount)) <= tol:
                used.add(i)
                return line
        return None

    # DLUS -> Summary: groups missing from the summary (a labelled line may cover a subset of them)
    missing = [k for k in g if k not in srows]
    covered, explained_by = set(), []
    if missing and recon and len(missing) <= MAX_SUBSET:
        for n in range(len(missing), 0, -1):
            hit = None
            for combo in itertools.combinations(missing, n):
                line = next((ln for i, ln in enumerate(recon) if i not in used
                             and abs(abs(ln["value"]) - sum(g[k]["area"] for k in combo)) <= tol), None)
                if line:
                    hit = (combo, line)
                    break
            if hit:
                covered |= set(hit[0])
                used.add(recon.index(hit[1]))
                explained_by.append({"level2": [g[k]["level2"] for k in hit[0]], "line": hit[1]})
                break
    tot = summ.get("total")
    for k in missing:
        if k in covered:
            continue
        x = g[k]
        add(pats, "level2_missing_from_summary",
            "A DLUS Level 2 land use is not in the Overall Summary and no labelled line explains it",
            {"plot_id": None, "level2": x["level2"], "dlus_plots": x["plots"], "dlus_area": round(x["area"], 2),
             "cells": [tot["cells"]["land_area"]] if tot else [],
             "extract": f"DLUS rows {row_span(x['rows'])}: '{x['level2']}' {x['plots']} plots, {x['area']:,.2f} m2; "
                        f"not in Overall Summary" + (f"; total Summary {tot['cells']['land_area']}={tot['land_area']:,.2f}"
                                                     if tot and tot.get("land_area") is not None else "")
                        + (f"; {dlus_agg(dlus, x)}" if dlus_agg(dlus, x) else "")},
            source=src)
    # both directions on the groups present in both, and summary-only groups
    for k, lst in srows.items():
        s_area = sum(s.get("land_area") or 0.0 for s in lst)
        s_plots = sum(s.get("plots") or 0.0 for s in lst)
        s = lst[0]
        if k not in g:
            if abs(s_area) > tol or s_plots:
                add(pats, "summary_level2_not_in_dlus", "An Overall Summary Level 2 land use has no DLUS rows",
                    {"plot_id": None, "level2": s["level2"], "cells": [s["cells"]["land_area"], s["cells"]["plots"]],
                     "extract": f"Summary {s['cells']['land_area']}={s_area:,.2f}; {s['cells']['plots']}={s_plots:g}; no DLUS rows"},
                    source=src)
            continue
        x = g[k]
        d_area = s_area - x["area"]
        if abs(d_area) > tol and reconciled(d_area) is None:
            add(pats, "area_ne_dlus", "Overall Summary area differs from the DLUS total for the Level 2 land use",
                {"plot_id": None, "level2": s["level2"], "cells": [s["cells"]["land_area"]],
                 "summary": round(s_area, 2), "dlus": round(x["area"], 2),
                 "extract": f"area Summary {s['cells']['land_area']}={s_area:,.2f}; DLUS rows {row_span(x['rows'])} sum={x['area']:,.2f}"
                            + (f"; {dlus_agg(dlus, x)}" if dlus_agg(dlus, x) and len(lst) == 1 else "")},
                source=src)
        if norm(s["level2"]) != "street" and int(round(s_plots)) != x["plots"]:
            add(pats, "plots_ne_dlus", "Overall Summary plot count differs from the DLUS count for the Level 2 land use",
                {"plot_id": None, "level2": s["level2"], "cells": [s["cells"]["plots"]],
                 "summary": int(round(s_plots)), "dlus": x["plots"],
                 "extract": f"plots Summary {s['cells']['plots']}={s_plots:g}; DLUS rows {row_span(x['rows'])} = {x['plots']} plots"
                            + (f"; {dlus_agg(dlus, x)}" if dlus_agg(dlus, x) and len(lst) == 1 else "")},
                source=src)
    return finish("GIS-012", pats, {"level2_dlus": len(g), "level2_summary": len(srows), "area_tol_m2": tol,
                                    "reconciled": explained_by, "reconciliation_lines": summ["reconciliation"]})


# ------------------------------------------------------------------ GIS-015
def rel_diff(a, b):
    base = max(abs(a), abs(b))
    return 0.0 if base == 0 else abs(a - b) / base * 100.0


def gis015(dlus, rules, summ, cf):
    if "GIS-015" not in rules:
        return {"rule_id": "GIS-015", "result": "not_run", "reason": "rule not in the rules file (inactive or not assigned)"}
    tol = param(rules, "GIS-015", "tol_rel")
    if tol is None:
        return {"rule_id": "GIS-015", "result": "not_run", "reason": "tolerance 'tol_rel' missing in the rulebook [GAP]"}
    if summ is None and cf is None:
        return {"rule_id": "GIS-015", "result": "not_run", "reason": "neither Overall Summary nor Community Facilities given"}
    pats, skipped = {}, []
    dlus_area = sum(r.get("land_area") or 0.0 for r in dlus["rows"])
    # site area (and the density that depends on it)
    if cf is not None and "site_area" in cf:
        sa = cf["site_area"]
        if rel_diff(sa["value"], dlus_area) > tol:
            cells, extra = [sa["cell"]], ""
            if "residents" in cf and "density" in cf:
                implied = cf["residents"]["value"] / (dlus_area / 10000.0) if dlus_area else 0.0
                cells += [cf["residents"]["cell"], cf["density"]["cell"]]
                extra = (f"; {cf['residents']['cell']} residents={cf['residents']['value']:g}; "
                         f"{cf['density']['cell']} density={cf['density']['value']:.1f} pr/ha vs {implied:.1f} pr/ha on the DLUS area")
            add(pats, "site_area_ne_dlus",
                "Community Facilities site area differs from the DLUS total area (density uses the same site area)",
                {"plot_id": None, "cells": cells, "cf_site_area": round(sa["value"], 2), "dlus_area": round(dlus_area, 2),
                 "extract": f"Community Facilities {sa['cell']}={sa['value']:,.2f} m2; DLUS total={dlus_area:,.2f} m2{extra}"},
                source=cf["source"])
    elif cf is not None:
        skipped.append("site area: 'Site Area (m2)' not found on Community Facilities")
    else:
        skipped.append("site area: Community Facilities not given")
    # FAR and height per Level 2, DLUS vs Overall Summary
    if summ is not None:
        g = rollup(dlus)
        for s in summ["rows"]:
            k = norm(s["level2"])
            if k not in g or is_villa(s["level1"], s["level2"]):
                continue            # villas: GFA fixed per unit, FAR not a design parameter (SPC-0004 §7.2)
            x = g[k]
            if s.get("far") not in (None, 0.0):
                for far, rows in sorted(x["far"].items()):
                    if rel_diff(far, s["far"]) > tol:
                        add(pats, "far_ne_summary", "FAR for a Level 2 land use differs between the DLUS and the Overall Summary",
                            {"plot_id": None, "level2": s["level2"], "plot_ids": [r["plot_id"] for r in rows][:10],
                             "cells": [s["cells"]["far"]], "dlus_cells": [r["cells"]["far"] for r in rows][:10],
                             "summary": round(s["far"], 4), "dlus": far,
                             "extract": f"Summary {s['cells']['far']}={s['far']:.2f}; DLUS FAR {far:g} on {len(rows)} row(s) "
                                        f"({', '.join(r['cells']['far'] for r in rows[:5])})"},
                            source=summ["source"])
            sh = s.get("height")
            if sh not in (None, "") and x["height"]:
                sh_n = norm(sh).replace(" ", "")
                for h, rows in sorted(x["height"].items()):
                    h_n = norm(h).replace(" ", "")
                    if h_n.startswith("g") != sh_n.startswith("g"):
                        skipped.append(f"height '{h}' (DLUS) vs '{sh}' (Summary) for {s['level2']}: formats differ, not compared [GAP]")
                        continue
                    if h_n != sh_n:
                        add(pats, "height_ne_summary", "Height for a Level 2 land use differs between the DLUS and the Overall Summary",
                            {"plot_id": None, "level2": s["level2"], "cells": [s["cells"]["height"]],
                             "dlus_cells": [r["cells"]["height"] for r in rows][:10],
                             "extract": f"Summary {s['cells']['height']}={sh}; DLUS height {h} on {len(rows)} row(s)"},
                            source=summ["source"])
    else:
        skipped.append("FAR / height: Overall Summary not given")
    return finish("GIS-015", pats, {"tol_rel_pct": tol, "dlus_total_area": round(dlus_area, 2),
                                    "subchecks_skipped": sorted(set(skipped))})
