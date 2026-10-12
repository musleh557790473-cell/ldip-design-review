#!/usr/bin/env python3
"""Match the documents of a submittal to MIDP rows, and list the stage's planned deliverables
(owner decisions D3 and D5, 11 Oct 2026).

Usage: python match_midp.py --package <package.json> --midp <midp.json> --rules <rules_INT.json>
           [--stage "<MIDP stage name>"] --out <midp_match.json>
Match levels (D3) — nothing is guessed:
  exact       a received number, or its base without the revision field, equals a MIDP number;
  normalised  exactly one MIDP row has the same key (package, site group of the section code, discipline,
              type, sequence). The site group comes from the PKG-006 site map in the rules file (QN and AQ
              -> QRWN; GN -> QRWN and NRJS), so the key holds under both SRC-02 and MIDP numbering;
  ambiguous   more than one MIDP row has the key (e.g. several MIDP sheet numbers) — up to ten listed;
  unmatched   no MIDP row has the key.
Only exact and normalised matches feed PKG-008. The key is reported with every match so it can be checked.
--stage (D5): MIDP rows of the submittal's site with a planned date for that stage, and which of them were
received. This is information, not a rule: no finding is raised from it (proposed PKG-013 for rules_v1).
Exit: 0 written · 2 input error (incl. unknown stage: the available names are listed). No network.
"""
__version__ = "0.3.2"

import argparse
import collections
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pkg_common import (EXIT_INPUT, EXIT_OK, atomic_write_json, emit, fail, param, parse_number, path_ok,  # noqa: E402
                        read_json, section_group)

MAX_CAND = 10


def key_of(parse, site_map, midp_style=False):
    f = parse["fields"]
    if len(f) < 8:
        return None
    if midp_style or parse.get("layout") == "midp":   # MIDP layout: ...-type-sequence-sheet[-revision]
        seq = f[7]
    elif parse["conforming_count"]:
        seq = parse["parts"]["sequence"]
    else:
        return None
    return (f[3], section_group(f[4], site_map), f[5].upper(), f[6].upper(), seq)


def in_midp(reg, rows, site_map):
    """P-S6-8 (owner, 12 Oct 2026, numbering only): is an unmatched deliverable planned in the MIDP?
    prefix: a MIDP number starts with the received number + '-' (e.g. received DES-RPT-00001, MIDP
    DES-RPT-00001-001); same_type_unique: exactly one MIDP row with the same package, site group,
    discipline and type (e.g. received IRR-MOD-00001, MIDP IRR-MOD-90000-001)."""
    f = reg["parse"]["fields"]
    if len(f) < 7:
        return {"value": False, "basis": "number not interpretable"}
    grp = section_group(f[4], site_map)
    same = [r for r in rows if len(r["number"].split("-")) > 6
            and section_group(r["number"].split("-")[4], site_map) == grp]
    pre = [r["number"] for r in same if r["number"].startswith(reg["base"] + "-")]
    if pre:
        return {"value": True, "basis": "prefix", "midp_numbers": pre[:MAX_CAND]}
    typ = [r["number"] for r in same if r["number"].split("-")[3] == f[3]
           and r["number"].split("-")[5].upper() == f[5].upper() and r["number"].split("-")[6].upper() == f[6].upper()]
    if len(typ) == 1:
        return {"value": True, "basis": "same_type_unique", "midp_numbers": typ}
    return {"value": False, "basis": "no MIDP row with this number, prefix or unique package/site/discipline/type"
            + (f" ({len(typ)} rows of that type)" if typ else "")}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--package", required=True)
    ap.add_argument("--midp", required=True)
    ap.add_argument("--rules", required=True)
    ap.add_argument("--stage")
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    if not path_ok(a.out):
        return fail(EXIT_INPUT, "output path longer than 250 characters")
    try:
        pkg, midp, rules = read_json(a.package), read_json(a.midp), read_json(a.rules)
    except (OSError, ValueError) as exc:
        return fail(EXIT_INPUT, f"cannot read input: {exc}")
    r006 = next((r for r in rules["rules"] if r["rule_id"] == "PKG-006"), None)
    site_map = param(r006, "site_map")
    if not site_map:
        return fail(EXIT_INPUT, "PKG-006 site_map not in the rules file (needed for the match key)")
    site = pkg["site"]
    by_num, by_key = {}, collections.defaultdict(list)
    for r in midp["rows"]:
        by_num.setdefault(r["number"], r)
        k = key_of(parse_number(r["number"]), site_map, midp_style=True)
        if k:
            by_key[k].append(r)
    files = midp["files"]

    def midp_ref(r):
        f = files[r["file"]]
        ex = None
        if a.stage:
            ex = next((e for e in r["exchanges"] if e["stage"].lower() == a.stage.lower()), None)
        return {"number": r["number"], "title": r["title"], "sheet": r["sheet"], "cell": r["cell"], "row": r["row"],
                "formats": r["formats"], "format_cells": r["format_cells"], "midp_file": f["name"],
                "midp_path": f["path"], "midp_sha256": f["sha256"], "midp_number": f["number_from_name"],
                "midp_updated": f["meta"].get("Date Last Updated", {}).get("value"), "stage_exchange": ex}

    matches, received = [], set()
    for reg in pkg["sheet_register"]:
        cands = [reg["base"]] + reg["numbers"]
        hit = next((by_num[c] for c in cands if c in by_num), None)
        key = key_of(reg["parse"] | {"conforming_count": reg["parse"]["conforming_count"]}, site_map)
        rec = {"base": reg["base"], "key": list(key[:1]) + ["/".join(key[1])] + list(key[2:]) if key else None}
        if hit:
            rec.update(match="exact", midp=midp_ref(hit))
        elif key and len(by_key.get(key, [])) == 1:
            rec.update(match="normalised", midp=midp_ref(by_key[key][0]))
        elif key and by_key.get(key):
            rec.update(match="ambiguous", candidates=[x["number"] for x in by_key[key][:MAX_CAND]],
                       candidates_total=len(by_key[key]))
        else:
            rec.update(match="unmatched", reason="no MIDP row with this key" if key else
                       "number structure not interpretable (field count does not conform, SPC-0004 §5.1)")
        if rec["match"] in ("exact", "normalised", "ambiguous"):
            rec["in_midp"] = {"value": True, "basis": rec["match"]}
        else:
            rec["in_midp"] = in_midp(reg, midp["rows"], site_map)
        if rec.get("midp"):
            received.add(rec["midp"]["number"])
        elif rec["in_midp"].get("value") and len(rec["in_midp"].get("midp_numbers", [])) == 1:
            # received under a wrong number (PKG-001 reports it); not also "planned not received" (PKG-013)
            received.add(rec["in_midp"]["midp_numbers"][0])
        matches.append(rec)
    stage_info = None
    if a.stage:
        names = sorted({e["stage"] for f in files for s in f["sheets"] for e in s["exchanges"]})
        if a.stage.lower() not in [n.lower() for n in names]:
            return fail(EXIT_INPUT, f"stage '{a.stage}' not in the MIDP", available_stages=names)
        planned = []
        for r in midp["rows"]:
            if site not in section_group(parse_number(r["number"])["fields"][4] if len(r["number"].split("-")) > 4 else "",
                                         site_map):
                continue
            ex = next((e for e in r["exchanges"] if e["stage"].lower() == a.stage.lower() and e.get("planned")), None)
            if ex:
                mf = files[r["file"]]
                planned.append({"number": r["number"], "title": r["title"], "sheet": r["sheet"], "cell": r["cell"],
                                "planned": ex.get("planned"), "actual": ex.get("actual") or None,
                                "revision": ex.get("revision") or None, "received": r["number"] in received,
                                "midp_path": mf["path"], "midp_sha256": mf["sha256"],
                                "midp_number": mf["number_from_name"],
                                "midp_updated": mf["meta"].get("Date Last Updated", {}).get("value")})
        stage_info = {"stage": a.stage, "site": site, "planned": len(planned),
                      "received": sum(1 for p in planned if p["received"]),
                      "planned_not_received": [p for p in planned if not p["received"]],
                      "received_list": [p for p in planned if p["received"]],
                      "note": "Reported by PKG-013 when the rule is active (rules_v1, owner 12 Oct 2026); information only otherwise (D5)."}
    counts = collections.Counter(m["match"] for m in matches)
    doc = {"tool": {"name": "match_midp.py", "version": __version__}, "site": site,
           "midp_files": [{"name": f["name"], "sha256": f["sha256"], "number": f["number_from_name"],
                           "updated": f["meta"].get("Date Last Updated", {}).get("value")} for f in files],
           "key_definition": "package, site group of section code (PKG-006 site map), discipline, type, sequence",
           "matches": matches, "counts": dict(sorted(counts.items())), "stage": stage_info}
    atomic_write_json(a.out, doc)
    emit({"status": "ok", "out": str(a.out), "matches": dict(sorted(counts.items())),
          **({"stage_planned": stage_info["planned"], "stage_received": stage_info["received"]} if stage_info else {})})
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
