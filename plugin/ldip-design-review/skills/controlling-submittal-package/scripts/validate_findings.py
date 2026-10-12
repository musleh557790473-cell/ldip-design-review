#!/usr/bin/env python3
"""Validate an INT candidate file: finding.schema.json, agent INT, unique ids, PKG rules only, and each
proposed status at or below its rule cap (SPC-0006 §4.3.3; cap per SPC-0004 §4.3).

Usage: python validate_findings.py --findings <candidates/intake.json> --rules <rules_INT.json>
Exit: 0 valid · 2 input error · 3 jsonschema missing · 4 invalid (errors listed). Writes nothing.
"""
__version__ = "0.1.0"

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_candidates import STATUS_RANK, validate  # noqa: E402
from pkg_common import EXIT_DEP, EXIT_INPUT, EXIT_OK, EXIT_SCHEMA, emit, fail, read_json  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--findings", required=True)
    ap.add_argument("--rules", required=True)
    a = ap.parse_args(argv)
    try:
        findings, rb = read_json(a.findings), read_json(a.rules)
    except (OSError, ValueError) as exc:
        return fail(EXIT_INPUT, f"cannot read input: {exc}")
    if not isinstance(findings, list):
        return fail(EXIT_INPUT, "findings file must hold a JSON array")
    errs = validate(findings)
    if errs is None:
        return fail(EXIT_DEP, "jsonschema is not installed")
    caps = {r["rule_id"]: r["cap"] for r in rb["rules"]}
    ids = set()
    for i, f in enumerate(findings):
        rid = (f.get("reference") or {}).get("rule_id")
        if f.get("agent") != "INT":
            errs.append(f"/{i}: agent {f.get('agent')} is not INT")
        if f.get("finding_id") in ids:
            errs.append(f"/{i}: duplicate finding_id {f.get('finding_id')}")
        ids.add(f.get("finding_id"))
        if rid not in caps:
            errs.append(f"/{i}: rule {rid} is not an active or advisory PKG rule in the rules file")
        elif STATUS_RANK.get(f.get("proposed_status"), 0) > STATUS_RANK[caps[rid]]:
            errs.append(f"/{i}: proposed_status {f.get('proposed_status')} above the {rid} cap {caps[rid]} (FC5)")
    if errs:
        return fail(EXIT_SCHEMA, "candidates invalid", errors=errs[:30])
    emit({"status": "ok", "findings": len(findings)})
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
