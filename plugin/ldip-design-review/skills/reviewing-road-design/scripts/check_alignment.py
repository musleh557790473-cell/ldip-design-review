#!/usr/bin/env python3
"""Alignment and profile check from LandXML ingestion files (skill 3) — SPC-0006 Rev D §4.13.7.

Per alignment, with class and design speed from the road register: ROD-017 / 018 (arc radius), ROD-024 /
025 (tangent grade, class), ROD-026 (tangent grade minimum, every road), ROD-028 / 029 (K of crest / sag
curves) and ROD-030 (vertical curve length) at the stated speed and, where it is below the ER minimum for
the class, also at that minimum (D10-5). Class-dependent checks of an alignment whose class came only
from its name, or that has no class, are capped at Probable or not run (D10-3). ROD-019 / 020 stay
inactive until T7 (D10-6) and are run only if the rules file holds them.

Usage: python check_alignment.py --register <road_register.json> --landxml <x.xml.json> [...] --rules <rules_RDE.json>
           --out <alignment_checks.json>
Exit: 0 written · 2 input error. No network, no shell calls.
"""
__version__ = "0.1.0"

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from road_common import (EXIT_INPUT, EXIT_OK, RoadError, applies, atomic_write_json, emit, main_guard,  # noqa: E402
                         param, read_json, rules_index, source_of, tol)

SPEED_MIN_RULE = {"local_access": "ROD-006", "sub_collector": "ROD-007", "arterial": "ROD-009"}


def speeds_for(cls, stated, rules):
    out = []
    if stated:
        out.append(("stated", float(stated)))
        r = rules.get(SPEED_MIN_RULE.get(cls or "", ""))
        p = param(r, "vd_min") if r else None
        if p and float(stated) < float(p["value"]):
            out.append(("ER minimum for the class", float(p["value"])))
    return out


def keyed(p, v):
    t = p["value"]
    if isinstance(t, dict):
        return float(t[str(int(v))]) if str(int(v)) in t else None
    return float(t)


def ev(src, extract, xy):
    return {**src, "source_type": "landxml", "xy": xy, "extract": extract}


def check(reg, ings, rules):
    checks, not_run = [], []
    by_name = {a["name"]: a for a in reg.get("alignments", [])}
    for ing in ings:
        src = source_of(ing)
        content = ing.get("content") or {}
        profiles = [p for p in content.get("profiles", []) if p.get("kind") == "design"]
        for al in content.get("alignments", []):
            info = by_name.get(al.get("name"), {})
            cls, stated = info.get("class"), info.get("design_speed_kmh")
            cap = "Probable" if cls else None
            base = {"class": cls or "unknown", "alignment": al.get("name")}

            def add(rid, pname, crit, subject, obs, req, op, p, ok, extract, xy, extra=None, dep=True):
                r = {"rule_id": rid, "parameter": pname, "criterion": crit, "subject": subject, "observed": obs,
                     "required": req, "comparison": op, "tolerance": tol(p), "unit": p.get("unit"),
                     "result": "pass" if ok else "fail", "evidence": ev(src, extract, xy), **base, **(extra or {})}
                if dep and cap:
                    r["link_cap"] = cap
                checks.append(r)

            for rid in ("ROD-017", "ROD-018"):
                rule = rules.get(rid)
                if not rule:
                    continue
                if not cls:
                    not_run.append({"rule_id": rid, "alignment": al.get("name"), "reason": "class of the alignment not known (register)"})
                    continue
                if not applies(rule, cls):
                    continue
                p = param(rule, "r_min")
                for e in al.get("elements", []):
                    if e.get("type") == "arc" and e.get("radius") is not None:
                        ok = float(e["radius"]) >= float(p["value"]) - tol(p) - 1e-9
                        add(rid, "r_min", "radius", f"alignment {al['name']} arc {e['index']}", e["radius"], p["value"], "ge",
                            p, ok, f"{al['name']} element {e['index']} arc R = {e['radius']} m, sta {e.get('sta_start')}",
                            {"crs": "EPSG:32638", "e": e["start"][0], "n": e["start"][1]})
            for prof in [p for p in profiles if p.get("alignment") == al.get("name")]:
                tangents = prof.get("tangents") or []
                for t in tangents:
                    g = abs(float(t["grade_pct"]))
                    xy = {"crs": "EPSG:32638", "e": al["elements"][0]["start"][0], "n": al["elements"][0]["start"][1]} if al.get("elements") else None
                    extract = f"{prof['name']} tangent sta {t['sta_from']}–{t['sta_to']} grade {t['grade_pct']} %"
                    r026 = rules.get("ROD-026")
                    if r026:
                        p = param(r026, "g_min")
                        add("ROD-026", "g_min", "grade", f"profile {prof['name']}", g, p["value"], "ge", p,
                            g >= float(p["value"]) - tol(p) - 1e-9, extract, xy, dep=False)
                    for rid in ("ROD-024", "ROD-025"):
                        rule = rules.get(rid)
                        if not rule or not cls or not applies(rule, cls):
                            continue
                        p = param(rule, "g_max")
                        add(rid, "g_max", "grade", f"profile {prof['name']}", g, p["value"], "le", p,
                            g <= float(p["value"]) + tol(p) + 1e-9, extract, xy)
                speeds = speeds_for(cls, stated, rules)
                for c in prof.get("curves") or []:
                    xy = {"crs": "EPSG:32638", "e": al["elements"][0]["start"][0], "n": al["elements"][0]["start"][1]} if al.get("elements") else None
                    extract = f"{prof['name']} {c.get('curve')} curve at PVI sta {c.get('pvi_sta')}: L = {c.get('length')} m, A = {c.get('a_pct')} %, K = {c.get('k')}"
                    todo = [("ROD-028" if c.get("curve") == "crest" else "ROD-029", "k", c.get("k")), ("ROD-030", "length", c.get("length"))]
                    for rid, crit, obs in todo:
                        rule = rules.get(rid)
                        if not rule or obs is None:
                            continue
                        if not speeds:
                            not_run.append({"rule_id": rid, "alignment": al.get("name"), "reason": "no design speed for the alignment (class not known)"})
                            continue
                        for basis, v in speeds:
                            pn = {"ROD-028": "k_crest_momra", "ROD-029": "k_sag", "ROD-030": "l_min"}[rid]
                            if rid == "ROD-028" and int(v) == 50 and param(rule, "k_crest_50_derived"):
                                pn = "k_crest_50_derived"
                            p = param(rule, pn)
                            req = keyed(p, v) if p else None
                            if req is None:
                                not_run.append({"rule_id": rid, "alignment": al.get("name"),
                                                "reason": f"{basis} speed {v:g} km/h is not a key of the rule table"})
                                continue
                            add(rid, pn, crit, f"profile {prof['name']} at {v:g} km/h ({basis})", float(obs), req, "ge", p,
                                float(obs) >= req - tol(p) - 1e-9, extract, xy, {"speed_kmh": v, "speed_basis": basis})
    return checks, not_run


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--register", required=True)
    ap.add_argument("--landxml", nargs="+", required=True)
    ap.add_argument("--rules", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)

    def run():
        ings = [read_json(p) for p in a.landxml]
        for p, ing in zip(a.landxml, ings):
            if (ing.get("source") or {}).get("file_type") != "landxml":
                raise RoadError(EXIT_INPUT, f"not a LandXML ingestion file (skill 3): {p}")
        rules, _, _ = rules_index(read_json(a.rules))
        checks, not_run = check(read_json(a.register), ings, rules)
        atomic_write_json(a.out, {"tool": {"script": "check_alignment.py", "version": __version__}, "mode": "alignment",
                                  "checks": checks, "not_run": not_run})
        emit({"status": "ok", "out": a.out, "checks": len(checks), "fail": sum(1 for c in checks if c["result"] == "fail"),
              "not_run": len(not_run)})
        return EXIT_OK
    return main_guard(run)


if __name__ == "__main__":
    sys.exit(main())
