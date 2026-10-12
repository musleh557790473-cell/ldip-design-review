#!/usr/bin/env python3
"""Read one or more LDIP findings files, validate them and write one readable view (skill 24, P-S22-2).

Usage: python load_findings.py --findings <file> [<file> ...] --out <findings_view.json>
           [--rulebook <rules_vN.json>]
Inputs: any SPC-0003 findings file at any stage — skill 6 / skill 9 candidates, skill 18 verified.json,
or a later registered.json. A file holds a JSON list of findings (or {"findings": [...]}).
Every finding is checked against finding.schema.json; one invalid finding stops the run (exit 4) and
nothing is written. A finding_id seen twice is an input error (exit 2).
The view adds no data. It copies what the findings say and adds two labelled, derived fields:
  route        candidates: UNVERIFIED (verify with skill 18 first); verified: the SPC-0003 Rev D §6 table
               (first match), marked provisional; classified / registered: the stored output_route.
  route_basis  which table row matched, or why none did (SPC-0005 §5.9.1 -> INCONSISTENT).
No register number is assigned (standalone mode). --rulebook adds the PKG legacy_numbers lists used by
§6 row 1; without it only skill 6's own "Legacy number" statements are recognised.
Exit: 0 written · 2 input error · 3 jsonschema missing · 4 a finding failed the schema.
No network, no shell calls.
"""
__version__ = "0.1.0"

import argparse
import collections
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from report_common import (EXIT_DEP, EXIT_INPUT, EXIT_OK, EXIT_SCHEMA, LEGACY_RULES, atomic_write_json,  # noqa: E402
                           emit, fail, location_text, now_text, path_ok, plugin_version, provisional_route,
                           read_json, reference_text, sha256_file, status_of, validator)


def legacy_lists(rulebook):
    rb = read_json(rulebook)
    nums = []
    for r in rb.get("rules", []):
        if r.get("rule_id") in LEGACY_RULES:
            p = (r.get("parameters") or {}).get("legacy_numbers") or {}
            v = p.get("value") if isinstance(p, dict) else p
            nums += [str(x) for x in (v or []) if x]
    return sorted(set(nums)), rb.get("rulebook_version")


def row(f, src, legacy):
    ref = f.get("reference") or {}
    route, basis = provisional_route(f, legacy)
    v = f.get("verification") or {}
    return {
        "finding_id": f["finding_id"], "run_id": f["run_id"], "site": f["site"], "sub_id": f["sub_id"],
        "agent": f["agent"], "stage": f["stage"], "source_file": src,
        "rule_id": ref.get("rule_id"), "rulebook_version": ref.get("rulebook_version"),
        "reference": reference_text(ref), "source_doc": ref.get("source_doc"), "clause": ref.get("clause"),
        "title": f["title"], "statement": f["statement"], "issue_type": f["issue_type"],
        "severity": f["severity"], "confidence": f["confidence"],
        "status": status_of(f), "proposed_status": f.get("proposed_status"),
        "recheck_result": v.get("recheck_result"), "verification_method": v.get("method"),
        "manual_check": v.get("manual_check"),
        "route": route, "route_basis": basis,
        "route_kind": "registered" if basis == "registered" else ("none" if route == "UNVERIFIED" else "provisional"),
        "document_no": f["location"]["document_no"], "revision": f["location"].get("revision"),
        "location": location_text(f["location"]),
        "evidence": [{"id": e["evidence_id"], "source_type": e["source_type"], "tier": e["tier"],
                      "extract": e["extract"], "artifact_path": e.get("artifact_path")} for e in f["evidence"]],
        "register_no": f.get("register_no"), "gate_status": (f.get("gate") or {}).get("status"),
        "addressee": f.get("addressee"),
    }


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--findings", nargs="+", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--rulebook")
    a = ap.parse_args(argv)
    for p in [*a.findings, a.out, *([a.rulebook] if a.rulebook else [])]:
        if not path_ok(p):
            return fail(EXIT_INPUT, f"path longer than 250 characters: {p}")
    val = validator()
    if val is None:
        return fail(EXIT_DEP, "jsonschema is not installed")
    legacy, rb_version = [], None
    if a.rulebook:
        try:
            legacy, rb_version = legacy_lists(a.rulebook)
        except (OSError, ValueError, AttributeError) as exc:
            return fail(EXIT_INPUT, f"cannot read the rulebook: {exc}")
    inputs, rows, seen, errors = [], [], set(), []
    for path in a.findings:
        try:
            data = read_json(path)
        except (OSError, ValueError) as exc:
            return fail(EXIT_INPUT, f"cannot read {path}: {exc}")
        items = data.get("findings") if isinstance(data, dict) else data
        if not isinstance(items, list):
            return fail(EXIT_INPUT, f"{path}: expected a list of findings")
        stages = collections.Counter()
        for i, f in enumerate(items):
            errs = sorted(val.iter_errors(f), key=lambda e: list(e.absolute_path))
            if errs:
                errors.append({"file": path, "index": i, "finding_id": f.get("finding_id") if isinstance(f, dict)
                               else None, "error": errs[0].message[:300],
                               "at": "/".join(map(str, errs[0].absolute_path))})
                continue
            if f["finding_id"] in seen:
                return fail(EXIT_INPUT, f"finding_id {f['finding_id']} appears twice", file=path)
            seen.add(f["finding_id"])
            stages[f["stage"]] += 1
            rows.append(row(f, path, legacy))
        inputs.append({"path": path, "sha256": sha256_file(path), "findings": len(items),
                       "stages": dict(sorted(stages.items()))})
    if errors:
        return fail(EXIT_SCHEMA, f"{len(errors)} finding(s) failed finding.schema.json; nothing written",
                    errors=errors[:10])
    rows.sort(key=lambda r: (r["site"], r["run_id"], r["finding_id"]))
    view = {
        "view_version": "1.0", "mode": "standalone",
        "inputs": inputs, "rulebook": {"path": a.rulebook, "version": rb_version, "legacy_numbers": len(legacy)}
        if a.rulebook else None,
        "counts": {"findings": len(rows),
                   "stages": dict(sorted(collections.Counter(r["stage"] for r in rows).items())),
                   "routes": dict(sorted(collections.Counter(r["route"] for r in rows).items()))},
        "findings": rows,
        "meta": {"generated_at": now_text(), "script": "load_findings.py", "version": __version__,
                 "plugin_version": plugin_version(),
                 "note": "Routes of verified findings are provisional (SPC-0003 §6, standalone); "
                         "no register number is assigned; nothing here is issued."},
    }
    atomic_write_json(a.out, view)
    emit({"status": "ok", "findings": len(rows), "stages": view["counts"]["stages"],
          "routes": view["counts"]["routes"], "out": a.out})
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
