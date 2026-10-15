#!/usr/bin/env python3
"""Validate an RDE candidate file: finding.schema.json, agent RDE, unique ids, rules of the rules file only,
status at or below the rule / parameter cap (FC5, one implementation: skill 8 rulebook_lib.cap_errors),
and every artifact_path present with its SHA-256 (FC3) — SPC-0006 Rev D §4.13.10.

Usage: python validate_findings.py --findings <candidates.json> --rules <rules_RDE.json> [--review-root <LDIP_Review>]
Exit: 0 valid · 2 input error · 3 jsonschema missing · 4 invalid (errors listed). Writes nothing.
"""
__version__ = "0.1.0"

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from road_common import EXIT_DEPENDENCY, EXIT_OK, EXIT_SCHEMA, RoadError, emit, read_json, sha256_file  # noqa: E402

PLUGIN_ROOT = HERE.parents[2]
sys.path.insert(0, str(PLUGIN_ROOT / "skills" / "applying-ldip-rulebook" / "scripts"))
from rulebook_lib import cap_errors  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--findings", required=True)
    ap.add_argument("--rules", required=True)
    ap.add_argument("--review-root", default=".")
    a = ap.parse_args(argv)
    try:
        findings, rb = read_json(a.findings), read_json(a.rules)
    except RoadError as e:
        emit({"status": "error", "code": e.code, "detail": e.detail})
        return e.code
    try:
        from jsonschema import Draft202012Validator, FormatChecker
    except ImportError:
        emit({"status": "error", "code": EXIT_DEPENDENCY, "detail": "jsonschema is not installed"})
        return EXIT_DEPENDENCY
    v = Draft202012Validator(json.loads((PLUGIN_ROOT / "schemas" / "finding.schema.json").read_text(encoding="utf-8")),
                             format_checker=FormatChecker())
    errs = [] if isinstance(findings, list) else ["findings file must hold a JSON array"]
    rules = {r["rule_id"]: r for r in rb.get("rules", [])}
    ids = set()
    for i, f in enumerate(findings if isinstance(findings, list) else []):
        errs += [f"/{i}/{'/'.join(map(str, e.absolute_path))}: {e.message[:150]}" for e in v.iter_errors(f)]
        if f.get("agent") != "RDE":
            errs.append(f"/{i}: agent {f.get('agent')} is not RDE")
        if f.get("finding_id") in ids:
            errs.append(f"/{i}: duplicate finding_id {f.get('finding_id')}")
        ids.add(f.get("finding_id"))
        for e in f.get("evidence", []):
            if e.get("artifact_path"):
                p = Path(a.review_root) / e["artifact_path"]
                if not p.is_file():
                    errs.append(f"/{i}: artifact not found: {e['artifact_path']}")
                elif sha256_file(p) != e.get("artifact_sha256"):
                    errs.append(f"/{i}: artifact hash differs: {e['artifact_path']}")
    errs += [f"{e['path']}: {e['message']}" for e in cap_errors(findings if isinstance(findings, list) else [], rules,
                                                                  rb.get("rulebook_version"))]
    if errs:
        emit({"status": "fail", "code": EXIT_SCHEMA, "errors": errs[:30]})
        return EXIT_SCHEMA
    emit({"status": "ok", "findings": len(findings)})
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
