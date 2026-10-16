#!/usr/bin/env python3
"""Design-criteria check (SPC-0006 Rev D §4.13.6; D10-4, D10-5): each criterion stated per road class in
the design report (read_criteria.py) is compared with the rule parameter for that class.

Only rules present in the rules file (active or advisory) are run, and only those that list
`design_report` in `inputs` (D10-4: rules_v3 adds it where missing); the others are listed as not run
with the reason. Speed-keyed rules (ROD-028 crest K, ROD-029 sag K, ROD-030 curve length) are checked at
the stated design speed and, where it is below the ER minimum for the class (vd_min of ROD-006 / 007 /
009), also at that minimum; a speed that is not a key of the rule table is not interpolated (D10-5).
ROD-028 at 50 km/h uses k_crest_50_derived (D-16). Tolerances come with the parameters (skill 8).
0.2.0 (SPC-0006 Rev F §4.13.6, K24): when read_criteria.py 0.2.0 gives `columns`, every column is checked;
a later column of the same class checks only the criteria whose printed value differs from an earlier
column of that class (no duplicate results), and its subject names the column's sub-heading.

Usage: python check_criteria.py --criteria <criteria.json> --rules <rules_RDE.json> --out <criteria_checks.json>
Exit: 0 written · 2 input error. No network, no shell calls.
"""
__version__ = "0.2.0"

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from road_common import (EXIT_OK, applies, atomic_write_json, emit, main_guard, param, read_json,  # noqa: E402
                         rules_index, tol)

# criterion -> [(rule_id, parameter, comparison)]; comparison: ge | le | eq
PLAIN = {
    "design_speed": [("ROD-006", "vd_min", "ge"), ("ROD-006", "vd_max", "le"), ("ROD-007", "vd_min", "ge"),
                     ("ROD-007", "vd_max", "le"), ("ROD-008", "vd_max", "le"), ("ROD-009", "vd_min", "ge"),
                     ("ROD-009", "vd_max", "le")],
    "row": [("ROD-002", "row_min", "ge"), ("ROD-002", "row_max", "le"), ("ROD-003", "row_min", "ge"),
            ("ROD-003", "row_max", "le"), ("ROD-004", "row_min", "ge"), ("ROD-004", "row_max", "le")],
    "lane_width": [("ROD-010", "lane_min", "ge"), ("ROD-010", "lane_upper", "le"), ("ROD-010", "tier2_lane", "ge"),
                   ("ROD-011", "lane_min", "ge"), ("ROD-011", "lane_upper", "le"), ("ROD-011", "tier2_lane", "ge"),
                   ("ROD-012", "lane_min", "ge"), ("ROD-012", "tier2_lane", "ge")],
    "median": [("ROD-014", "median_min", "ge"), ("ROD-014", "tier2_median_shc", "ge"),
               ("ROD-014", "tier2_median_momra", "ge"), ("ROD-016", "separator_min", "ge")],
    "crossfall": [("ROD-021", "crossfall", "eq")],
    "e_max": [("ROD-022", "e_max_abs", "le"), ("ROD-022", "e_max_recommended", "le")],
    "r_min": [("ROD-017", "r_min", "ge"), ("ROD-018", "r_min", "ge")],
    "g_max": [("ROD-024", "g_max", "le"), ("ROD-025", "g_max", "le")],
    "g_min": [("ROD-026", "g_min", "ge")],
    "clearance": [("ROD-031", "clearance_min", "ge")],
    "curb": [("ROD-057", "height", "eq")],
}
KEYED = {"k_crest": ("ROD-028", "k_crest_momra"), "k_sag": ("ROD-029", "k_sag"), "l_min": ("ROD-030", "l_min")}
SPEED_MIN_RULE = {"local_access": "ROD-006", "sub_collector": "ROD-007", "arterial": "ROD-009"}
OPTIONAL_ELEMENT = {"median", "e_max"}          # "None" = element not provided -> no check (where provided)


def compare(obs, req, op, t):
    if op == "ge":
        return obs >= req - t - 1e-9
    if op == "le":
        return obs <= req + t + 1e-9
    return abs(obs - req) <= t + 1e-9


def ev(cell, src, cls):
    bbox = [min(cell["bbox"][0], cell["label_bbox"][0]), min(cell["bbox"][1], cell["label_bbox"][1]),
            max(cell["bbox"][2], cell["label_bbox"][2]), max(cell["bbox"][3], cell["label_bbox"][3])]
    return {**src, "source_type": "pdf_vector_text", "page": cell["page"], "bbox": bbox,
            "extract": f"{cell['label']} — {cls} column: \"{cell['text']}\" (p.{cell['page']})"}


def value_sets(crit):
    """[(class, criteria, column note)] — every column (read_criteria 0.2.0) or the per-class criteria."""
    cols = [c for c in crit.get("columns") or [] if c.get("criteria")]
    if not cols:
        return [(cls, vals, "") for cls, vals in sorted(crit.get("criteria", {}).items())]
    out, seen, n = [], {}, {}
    for c in cols:
        n[c["class"]] = n.get(c["class"], 0) + 1
    for c in cols:
        prev = seen.setdefault(c["class"], {})
        vals = {k: v for k, v in c["criteria"].items() if prev.get(k) != v["text"]}
        for k, v in c["criteria"].items():
            prev.setdefault(k, v["text"])
        note = f", column '{c.get('sub_heading') or c.get('heading')}'" if n[c["class"]] > 1 else ""
        out.append((c["class"], vals, note))
    return out


def run_checks(crit, rules):
    out, not_run = [], []
    src = crit["source"]
    def needs_report(rid):
        r = rules.get(rid)
        if r is None:
            return "rule not active or advisory in the rules file"
        if "design_report" not in (r.get("inputs") or []):
            return "design_report is not an input of the rule (rules_v3, SPC-0006 Rev D Annex A.3)"
        return None

    for cls, values, note in value_sets(crit):
        for key, checks in PLAIN.items():
            cell = values.get(key)
            if cell is None:
                continue
            nums = cell["numbers"]
            for rid, pname, op in checks:
                if rid in rules and not applies(rules[rid], cls):
                    continue
                why = needs_report(rid)
                if why:
                    not_run.append({"rule_id": rid, "parameter": pname, "class": cls, "reason": why})
                    continue
                rule = rules[rid]
                p = param(rule, pname)
                if p is None:
                    continue
                if not nums:
                    if key in OPTIONAL_ELEMENT:
                        continue
                    not_run.append({"rule_id": rid, "parameter": pname, "class": cls,
                                    "reason": f"value not a number: '{cell['text']}'"})
                    continue
                for obs in nums:
                    ok = compare(obs, float(p["value"]), op, tol(p))
                    out.append({"rule_id": rid, "parameter": pname, "class": cls, "criterion": key,
                                "subject": f"design criterion '{cell['label']}' ({cls}{note})", "observed": obs,
                                "required": p["value"], "comparison": op, "tolerance": tol(p), "unit": p.get("unit"),
                                "result": "pass" if ok else "fail", "evidence": ev(cell, src, cls)})
        stated = (values.get("design_speed") or {}).get("numbers") or []
        speeds = []
        if stated:
            speeds.append(("stated", stated[0]))
            mrule = rules.get(SPEED_MIN_RULE.get(cls, ""))
            vmin = param(mrule, "vd_min") if mrule else None
            if vmin and stated[0] < float(vmin["value"]):
                speeds.append(("ER minimum for the class", float(vmin["value"])))
        for key, (rid, pname) in KEYED.items():
            cell = values.get(key)
            if cell is None:
                continue
            if rid in rules and not applies(rules[rid], cls):
                continue
            why = needs_report(rid)
            if why:
                not_run.append({"rule_id": rid, "parameter": pname, "class": cls, "reason": why})
                continue
            rule = rules[rid]
            if not cell["numbers"]:
                continue
            if not speeds:
                not_run.append({"rule_id": rid, "parameter": pname, "class": cls, "reason": "no stated design speed for the class"})
                continue
            for basis, v in speeds:
                pn = pname
                if rid == "ROD-028" and int(v) == 50 and param(rule, "k_crest_50_derived"):
                    pn = "k_crest_50_derived"
                p = param(rule, pn)
                if p is None:
                    continue
                table = p["value"]
                if isinstance(table, dict):
                    if str(int(v)) not in table:
                        not_run.append({"rule_id": rid, "parameter": pn, "class": cls,
                                        "reason": f"{basis} speed {v:g} km/h is not a key of the rule table (no interpolation)"})
                        continue
                    req = float(table[str(int(v))])
                else:
                    req = float(table)
                obs = cell["numbers"][0]
                ok = compare(obs, req, "ge", tol(p))
                out.append({"rule_id": rid, "parameter": pn, "class": cls, "criterion": key,
                            "subject": f"design criterion '{cell['label']}' ({cls}{note}) at {v:g} km/h ({basis})",
                            "observed": obs, "required": req, "comparison": "ge", "tolerance": tol(p),
                            "unit": p.get("unit"), "speed_kmh": v, "speed_basis": basis,
                            "result": "pass" if ok else "fail", "evidence": ev(cell, src, cls)})
    return out, not_run


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--criteria", required=True)
    ap.add_argument("--rules", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)

    def run():
        crit = read_json(a.criteria)
        rules, skipped, _ = rules_index(read_json(a.rules))
        checks, not_run = run_checks(crit, rules)
        res = {"tool": {"script": "check_criteria.py", "version": __version__}, "mode": "criteria",
               "checks": checks, "not_run": not_run, "departures": crit.get("departures", [])}
        atomic_write_json(a.out, res)
        emit({"status": "ok", "out": a.out, "checks": len(checks),
              "fail": sum(1 for c in checks if c["result"] == "fail"), "not_run": len(not_run)})
        return EXIT_OK
    return main_guard(run)


if __name__ == "__main__":
    sys.exit(main())
