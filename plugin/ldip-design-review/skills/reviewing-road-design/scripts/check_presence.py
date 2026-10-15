#!/usr/bin/env python3
"""Phase-B1 decision per presence rule, stage and condition (SPC-0006 Rev E §4.13B.5 - 4.13B.7).

Reads presence.json (find_presence.py), the rules file (skill 8, agent RDE), the stage name and optionally
section_checks.json (check_sections.py) and the DLUS land uses. For every active / advisory ROD presence
rule:
1. the rule's phase is later than the submission phase -> not_run "not required at stage" (M2);
2. accepted carriers = the element carriers of the required inputs that apply at the submission phase
   (the latest such phase); a design-report statement counts only where the rule lists `design_report`
   for that phase (DB-3, PD only);
3. a condition (roundabout, transit, school / playground frontage, dead-end streets) not shown -> not_run
   "not applicable" or "not decidable in phase B1" (M2-2 / M2-3);
4. element shown (not negated) in an accepted carrier -> pass;
5. element only negated ("WITHOUT PARALLEL PARKING") -> fail, the negating texts as evidence;
6. element absent from every accepted carrier -> fail, evidence = what was searched (M5), carriers not in the
   submittal named; status capped at Probable when an undecodable text record sits on a layer of the
   element's terms (DB-4, K18);
7. carriers disagree (negated in an accepted carrier, shown elsewhere) -> one inconsistency check;
8. partial coverage stated (DB-5, ROD-101 "both sides for at least 85 %") -> fail, Probable;
9. ROD-101 per typical section: fewer than two sidewalks in a section -> fail.
Location-level checks (per junction, crossing, road end, low point) are phase B2 and are not made here.

Usage: python check_presence.py --presence <presence.json> --rules <rules_RDE.json> --stage "<stage name>"
           [--sections <section_checks.json>] [--land-uses "<use>" ...] --out <presence_checks.json>
Exit: 0 written · 2 input error (unknown stage). No network, no shell calls.
"""
__version__ = "0.1.0"

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from road_common import (EXIT_INPUT, EXIT_OK, PRESENCE_ELEMENTS, RoadError, atomic_write_json, class_of,  # noqa: E402
                         emit, main_guard, read_json, term_hits)

PHASE_ORDER = {"PD": 1, "DD": 2, "FD": 3}
ELEMENT_OF = {v["rule"]: k for k, v in PRESENCE_ELEMENTS.items()}
CONDITION_TEXT = {"ROD-112": "a roundabout", "ROD-114": "public transit anticipated",
                  "ROD-113": "a school or playground frontage", "ROD-115": "dead-end streets"}
SCHOOL = re.compile(r"school|playground|play\s*ground|kindergarten|nursery", re.I)


def short(p):
    return Path(p or "").name


def searched_summary(searched, carriers):
    parts = []
    for s in searched:
        if s["carrier"] not in carriers:
            continue
        if s["kind"] == "pdf":
            parts.append(f"{short(s['path'])} ({s['carrier']}; {s['pages']} pages, {s['spans']} words)")
        else:
            parts.append(f"{short(s['path'])} ({s['carrier']}; {s['layers']} layers, {s['blocks']} blocks, "
                         f"{s['texts']} texts, {s['undecodable_texts']} undecodable)")
    return parts


def hit_evidence(f, h, rid):
    """Evidence block of one hit (DWG text / layer / block or PDF line)."""
    if h["kind"] == "pdf_text":
        return {"source_type": "pdf_vector_text", "path": f["path"], "sha256": f["sha256"], "page": h["page"],
                "bbox": h["bbox"], "extract": h["text"]}
    return {"source_type": "dwg_vector", "path": f["path"], "sha256": f["sha256"], "layer": h.get("layer"),
            "handles": [h["handle"]] if h.get("handle") else [], "extract": f"{h['kind']}: {h['text']}"}


def element_carriers(rule, phase):
    """(carriers, phase used) for the element of a presence rule at the submission phase."""
    entries = [e for e in rule.get("required_inputs") or [] if not re.match(r"C\d+\b", e.get("item", ""))]
    if not entries:
        entries = [{"carrier": [c for c in rule.get("inputs", []) if c != "dlus"],
                    "required_from_phase": rule.get("required_from_phase")}]
    ok = [e for e in entries if PHASE_ORDER.get(e.get("required_from_phase") or "PD", 1) <= PHASE_ORDER[phase]]
    if not ok:
        return set(), None
    top = max(PHASE_ORDER.get(e.get("required_from_phase") or "PD", 1) for e in ok)
    carriers = set()
    for e in ok:
        if PHASE_ORDER.get(e.get("required_from_phase") or "PD", 1) == top:
            carriers |= set(e.get("carrier") or [])
    return carriers, [k for k, v in PHASE_ORDER.items() if v == top][0]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--presence", required=True)
    ap.add_argument("--rules", required=True)
    ap.add_argument("--stage", required=True)
    ap.add_argument("--sections")
    ap.add_argument("--land-uses", nargs="*")
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)

    def run():
        rb = read_json(a.rules)
        pres = read_json(a.presence)
        smap = rb.get("stage_map") or {}
        if a.stage not in smap:
            raise RoadError(EXIT_INPUT, f"stage '{a.stage}' not in the rulebook stage_map ({', '.join(smap)})")
        phase = smap[a.stage]["phase"] if isinstance(smap[a.stage], dict) else smap[a.stage]
        secs = read_json(a.sections) if a.sections else None
        searched = pres.get("searched", [])
        given = {s["carrier"] for s in searched}
        sha_of = {s["path"]: s.get("sha256") for s in searched}
        checks, not_run, passed = [], [], []
        for rule in rb.get("rules", []):
            rid = rule["rule_id"]
            if rule.get("check_type") != "presence" or not rid.startswith("ROD-") or rid not in ELEMENT_OF:
                continue
            el = ELEMENT_OF[rid]
            terms = PRESENCE_ELEMENTS[el]["terms"]
            req_value = ((rule.get("parameters") or {}).get("required_element") or {}).get("value") or rule["title"]
            base = {"rule_id": rid, "parameter": "required_element", "class": None, "required": req_value,
                    "comparison": "present", "unit": None}
            if PHASE_ORDER.get(rule.get("required_from_phase") or "PD", 1) > PHASE_ORDER[phase]:
                not_run.append({"rule_id": rid, "reason": "not required at stage"})
                continue
            carriers, used = element_carriers(rule, phase)
            if not carriers:
                not_run.append({"rule_id": rid, "reason": "not required at stage"})
                continue
            files = pres.get("elements", {}).get(el, [])
            acc = [f for f in files if f["carrier"] in carriers]
            shown = [(f, h) for f in acc for h in f["hits"] if not h.get("negated")]
            negated = [(f, h) for f in acc for h in f["hits"] if h.get("negated")]
            # 3. conditions
            if rid in ("ROD-112", "ROD-114") and not shown:
                cond = pres.get("conditions", {}).get(rid, [])
                if not cond:
                    not_run.append({"rule_id": rid, "reason": f"not applicable: no evidence of {CONDITION_TEXT[rid]} in the searched files",
                                    "status_hint": "Not auto-verifiable"})
                    continue
            if rid == "ROD-113":
                if a.land_uses is None:
                    not_run.append({"rule_id": rid, "reason": "condition needs the DLUS land uses (C8); not given",
                                    "status_hint": "Not auto-verifiable"})
                    continue
                if not any(SCHOOL.search(u) for u in a.land_uses):
                    not_run.append({"rule_id": rid, "reason": "not applicable: no school or playground land use in the DLUS"})
                    continue
            if rid == "ROD-115" and not shown:
                not_run.append({"rule_id": rid, "reason": "dead-end streets need road-end positions, not read in phase B1 (K17 / K19)",
                                "status_hint": "Not auto-verifiable"})
                continue
            # 8. partial coverage (DB-5)
            for f in pres.get("partial_coverage", {}).get(rid, []):
                for h in f["hits"]:
                    checks.append(dict(base, result="fail", subject=f"{short(f['path'])} p.{h['page']}",
                                       observed=f"stated coverage {h['coverage_pct']:g} % of the network",
                                       required=f"{req_value} (100 % of the network, except where ER allows)",
                                       evidence=hit_evidence(f, h, rid), issue_type="missing_information",
                                       link_cap="Probable", mode_tag="presence_partial"))
            # 9. ROD-101 per typical section
            if rid == "ROD-101" and secs and "typical_section" in carriers:
                for s in secs.get("sections", []):
                    walks = [e for e in s.get("elements", []) if e.get("element") == "sidewalk"]
                    if len(walks) < 2:
                        checks.append(dict(base, result="fail", class_=None, subject=s.get("title") or "typical section",
                                           observed=f"{len(walks)} sidewalk(s) in the section",
                                           evidence={"source_type": "dwg_vector", "path": s.get("source"), "sha256": sha_of.get(s.get("source")),
                                                     "handles": [e["handle"] for e in s.get("elements", []) if e.get("handle")][:10],
                                                     "extract": f"section '{s.get('title')}': elements "
                                                                + ", ".join(f"{e['element']} {e['value']:g}" for e in s.get("elements", []))},
                                           issue_type="missing_information",
                                           link_cap="Probable" if any(w.get("assigned") == "positional" for w in walks) else None,
                                           mode_tag="presence_section"))
            if shown:
                passed.append({"rule_id": rid, "found_in": sorted({short(f["path"]) for f, _ in shown}),
                               "carriers": sorted({f["carrier"] for f, _ in shown}),
                               "intent_only": all(f["carrier"] == "design_report" for f, _ in shown)})
                continue
            if negated:
                for f, h in negated:
                    cls = class_of(h.get("text"))
                    if cls and not ("all" in ((rule.get("applicability") or {}).get("street_class") or ["all"])
                                    or cls in (rule.get("applicability") or {}).get("street_class", [])):
                        continue
                    checks.append(dict(base, result="fail", subject=h["text"][:80], observed="stated: not provided",
                                       evidence=hit_evidence(f, h, rid), issue_type="missing_information", mode_tag="presence"))
                others = [(f, h) for f in files if f["carrier"] not in carriers for h in f["hits"] if not h.get("negated")]
                if others:
                    f0, h0 = negated[0]
                    checks.append(dict(base, result="fail", subject="carriers disagree",
                                       observed=f"not provided in {short(f0['path'])} ('{h0['text'][:60]}'); shown in "
                                                + "; ".join(f"{short(f['path'])} ('{h['text'][:60]}')" for f, h in others[:3]),
                                       required="one consistent design", comparison="consistent",
                                       evidence=hit_evidence(f0, h0, rid), issue_type="inconsistency", link_cap="Probable",
                                       mode_tag="presence_inconsistency"))
                    for f, h in others[:4]:
                        checks.append(dict(checks[-1], subject=short(f["path"]), observed=h["text"][:120],
                                           evidence=hit_evidence(f, h, rid)))
                continue
            # 6. absent everywhere
            missing_carriers = sorted(carriers - given)
            summary = searched_summary(searched, carriers)
            und_on_layers = sum(n for s in searched if s["carrier"] in carriers and s["kind"] == "dwg"
                                for lay, n in (s.get("undecodable_by_layer") or {}).items()
                                if term_hits(lay.split("$")[-1], terms))
            report_rule = any("design_report" in (e.get("carrier") or []) and not re.match(r"C\d+\b", e.get("item", ""))
                              for e in rule.get("required_inputs") or [])
            intent = [(f, h) for f in files if report_rule and f["carrier"] == "design_report"
                      for h in f["hits"] if not h.get("negated")]
            cond = [(f, h) for f in pres.get("conditions", {}).get(rid, []) for h in f["hits"]][:2]
            extract = ("searched: " + "; ".join(summary) if summary else "no file of the accepted carriers was given") \
                + (f". Carriers not in the submittal: {', '.join(missing_carriers)}" if missing_carriers else "") \
                + f". Terms: {', '.join(terms)}"
            checks.append(dict(base, result="fail", subject=f"package (accepted carriers at {used}: {', '.join(sorted(carriers))})",
                               observed="not shown in any searched file",
                               evidence={"source_type": "script_output", "path": None, "sha256": None, "extract": extract},
                               issue_type="missing_information", link_cap="Probable" if und_on_layers else None,
                               mode_tag="presence", searched=summary, missing_carriers=missing_carriers,
                               undecodable_on_element_layers=und_on_layers))
            for f, h in intent[:2]:      # report statement quoted (DB-3: not presence from DD)
                checks.append(dict(checks[-1], subject=f"design report {short(f['path'])} p.{h.get('page')}",
                                   observed=f"stated in the report only: '{h['text'][:120]}'", evidence=hit_evidence(f, h, rid)))
            for f, h in cond:            # why the rule applies (condition evidence)
                checks.append(dict(checks[-1], subject=f"condition: {CONDITION_TEXT[rid]} ({short(f['path'])})",
                                   observed=f"'{h['text'][:120]}'", evidence=hit_evidence(f, h, rid)))
        for c in checks:
            c.pop("class_", None)
        out = {"tool": {"script": "check_presence.py", "version": __version__}, "mode": "presence", "stage": a.stage,
               "phase": phase, "checks": checks, "passed": passed, "not_run": not_run}
        atomic_write_json(a.out, out)
        emit({"status": "ok", "out": a.out, "phase": phase, "fail": len(checks),
              "rules_failed": sorted({c["rule_id"] for c in checks}), "passed": [p["rule_id"] for p in passed],
              "not_run": len(not_run)})
        return EXIT_OK
    return main_guard(run)


if __name__ == "__main__":
    sys.exit(main())
