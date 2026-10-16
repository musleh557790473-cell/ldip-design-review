#!/usr/bin/env python3
"""Required inputs C1-C4 with the stage gate (SPC-0006 Rev D §4.13.8; SPC-0004 Rev E §5.11, §5.14, §5.14A;
missing-information study M1-M7).

The items and their accepted carriers come from the `required_inputs` of the rules in the rules file.
For each item (grouped over rules by its C-number) the script decides:
- the submission phase (stage name -> `stage_map`) is before `required_from_phase` -> not run,
  "not required at stage" (M2);
- one listed carrier holds the item -> present;
- a listed carrier that phase A does not read (plan_geometry, details, sections ...) was not searched ->
  not run, "carrier not read in phase A" (Not auto-verifiable, internal, M2-3);
- every listed carrier was searched and none holds it -> fail: one Missing information finding per item,
  listing what was searched (M1, M4, M5).
DD milestone exceptions from the MIDP (R34) are not read in phase A: a DD item counts from 60 % [GAP].

Usage: python check_required.py --stage "<MIDP stage name>" --rules <rules_RDE.json> --register <road_register.json>
           [--criteria <criteria.json>] [--sections <section_checks.json>] [--landxml <x.xml.json> ...]
           [--searched <file> ...] --out <required_inputs.json>
Exit: 0 written · 2 input error (unknown stage). No network, no shell calls.
"""
__version__ = "0.2.0"

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from road_common import EXIT_INPUT, EXIT_OK, RoadError, atomic_write_json, emit, main_guard, read_json  # noqa: E402

PHASE_ORDER = {"PD": 1, "DD": 2, "FD": 3}
READ_IN_PHASE_A = {"design_report", "road_hierarchy_plan", "typical_section", "landxml_alignment", "landxml_profile",
                   "landxml_surface"}


def carriers_found(reg, crit, sections, landxml):
    found = {}
    classes = reg.get("classes") or {}
    if any(c.get("design_speed") for c in classes.values()):
        found.setdefault("C2", set()).add("design_report")
    if classes:
        found.setdefault("C1", set()).add("design_report")
    if reg.get("row_groups"):
        found.setdefault("C1", set()).add("road_hierarchy_plan")
    if any(s.get("class") for s in reg.get("sections", [])):
        found.setdefault("C1", set()).add("typical_section")
    if sections and any(s.get("elements") for s in sections.get("sections", [])):
        found.setdefault("C3", set()).add("typical_section")
    if crit and any(v.get("row") or v.get("lane_width") for v in crit.get("criteria", {}).values()):
        found.setdefault("C3", set()).add("design_report")
    for ing in landxml:
        for c in (ing.get("content") or {}).get("carriers", []):
            found.setdefault("C4", set()).add(c)
    return found


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", required=True)
    ap.add_argument("--rules", required=True)
    ap.add_argument("--register", required=True)
    ap.add_argument("--criteria")
    ap.add_argument("--sections")
    ap.add_argument("--landxml", nargs="*", default=[])
    ap.add_argument("--searched", nargs="*", default=[])
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)

    def run():
        rb = read_json(a.rules)
        smap = rb.get("stage_map") or {}
        if a.stage not in smap:
            raise RoadError(EXIT_INPUT, f"stage '{a.stage}' not in the rulebook stage_map ({', '.join(smap)})")
        phase = smap[a.stage]["phase"] if isinstance(smap[a.stage], dict) else smap[a.stage]
        reg = read_json(a.register)
        crit = read_json(a.criteria) if a.criteria else None
        secs = read_json(a.sections) if a.sections else None
        lx = [read_json(p) for p in a.landxml]
        found = carriers_found(reg, crit, secs, lx)
        items = {}
        for r in rb.get("rules", []):
            for ri in r.get("required_inputs") or []:
                m = re.match(r"(C\d+)\b", ri.get("item", ""))
                if not m or m.group(1) not in ("C1", "C2", "C3", "C4"):
                    continue
                it = items.setdefault(m.group(1), {"item": ri["item"], "carriers": set(), "phase": ri.get("required_from_phase"),
                                                   "rules": [], "source_id": ri.get("source_id"), "clause": ri.get("clause")})
                it["carriers"] |= set(ri.get("carrier") or [])
                it["rules"].append(r["rule_id"])
        results = []
        searched = sorted(set(a.searched) | {s for s in (a.criteria, a.sections) if s} | set(a.landxml))
        for key, it in sorted(items.items()):
            res = {"item": it["item"], "key": key, "rules": sorted(set(it["rules"])), "carriers": sorted(it["carriers"]),
                   "required_from_phase": it["phase"], "submission_phase": phase, "source_id": it["source_id"],
                   "clause": it["clause"], "searched": searched}
            if it["phase"] and PHASE_ORDER.get(phase, 0) < PHASE_ORDER.get(it["phase"], 0):
                res.update(result="not_run", reason="not required at stage")
            elif key == "C2" and crit and crit.get("tables") and not crit.get("criteria") \
                    and "design_report" in it["carriers"]:
                # K24 / DF-3 (SPC-0006 Rev F §4.13.6): a criteria table was found but not read -> not a missing C2
                res.update(result="not_run", reason="criteria table found but not read (CRITERIA_TABLE_UNREAD)",
                           status_hint="Not auto-verifiable")
            elif found.get(key, set()) & it["carriers"]:
                res.update(result="pass", found_in=sorted(found[key] & it["carriers"]))
            elif it["carriers"] - READ_IN_PHASE_A:
                res.update(result="not_run", reason="carrier not read in phase A: " + ", ".join(sorted(it["carriers"] - READ_IN_PHASE_A)),
                           status_hint="Not auto-verifiable")
            else:
                res.update(result="fail", status_hint="Missing information")
            results.append(res)
        out = {"tool": {"script": "check_required.py", "version": __version__}, "mode": "required", "stage": a.stage,
               "phase": phase, "items": results,
               "notes": ["DD milestone exceptions from the MIDP (SPC-0004 Rev E §5.14A) not read in phase A"] if phase == "DD" else []}
        atomic_write_json(a.out, out)
        emit({"status": "ok", "out": a.out, "phase": phase,
              "items": {r["key"]: r["result"] for r in results}})
        return EXIT_OK
    return main_guard(run)


if __name__ == "__main__":
    sys.exit(main())
