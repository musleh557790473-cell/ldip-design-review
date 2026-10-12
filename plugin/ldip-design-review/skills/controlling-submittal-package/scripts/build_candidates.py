#!/usr/bin/env python3
"""Turn PKG check results into SPC-0003 candidate findings (agent INT) and validate them (SPC-0006 §4.3).

Usage: python build_candidates.py --results <pkg_results.json> --rules <rules_INT.json> --run <run-id>
           --site <SITE> --sub <sub-id> [--package <package.json>] [--start 1] --out <candidates/intake.json>
One finding per pattern (rule + field + reason, SPC-0004 §5.7) with the count and up to ten samples.
proposed_status = the lower of the rule's default status for the evidence source (or the pattern's
own status, e.g. a received-but-unlisted file) and the rule cap. Severity Observation for legacy
patterns (§6.2B). Location:
  MIDP patterns      the MIDP workbook, sheet and cells (re-readable by the Verifier, xlsx_cell)
  PKG-008 vs MIDP    the deliverable's number; the MIDP flag cells as evidence; reference = the MIDP
  PKG-007 missing    the list file, sheet and cells; reference = the list (listed = owed)
  other file rules   the document's number and file when there is one sample, else package level
The statement holds the observation and the requirement only (SPC-0002 C5).
Exit: 0 written · 2 input error · 3 jsonschema missing · 4 candidates failed finding.schema.json (nothing
written). No network, no shell calls.
"""
__version__ = "0.1.1"

import argparse
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pkg_common import (EXIT_DEP, EXIT_INPUT, EXIT_OK, EXIT_SCHEMA, atomic_write_json, emit, fail,  # noqa: E402
                        path_ok, read_json)

SCHEMA = Path(__file__).resolve().parents[3] / "schemas" / "finding.schema.json"
STATUS_RANK = {"Confirmed": 3, "Missing information": 3, "Probable": 2, "Not auto-verifiable": 1}
RIYADH = timezone(timedelta(hours=3))
RUN_RE = re.compile(r"^(QRWN|NMAR|NRJS)-[A-Za-z0-9._-]+-[0-9]{8}T[0-9]{4}$")
CELL_RE = re.compile(r"^[A-Z]{1,3}[0-9]+$")
SCRIPT_OF = {"PKG-007": "build_register.py", "PKG-008": "check_package.py"}


def lower(a, b):
    return a if STATUS_RANK.get(a, 0) <= STATUS_RANK.get(b, 0) else b


def finding(n, rr, pat, rule, a, pkg, rb_version, now):
    src = pat.get("source") or {}
    samples = pat.get("samples", [])
    count = pat.get("count", len(samples))
    source_type = src.get("source_type", "script_output")
    severity = pat.get("severity") or rule["severity_default"]
    issue_type = pat.get("issue_type") or rule["issue_type"]
    nums = [s["number"] for s in samples if s.get("number")]
    loc, reference_doc, clause = {}, f"LDIP Rulebook {rb_version} (AICC-BIM-SPC-0004)", f"{rule['rule_id']}: {rule['title']}"
    pkg_doc = ((pkg or {}).get("transmittal") or {}).get("number") or f"PACKAGE:{a.site}/{a.sub}"
    if pat.get("carrier") == "midp":
        cells = [s["cell"] for s in samples if s.get("cell") and CELL_RE.match(s["cell"])]
        loc = {"document_no": src["document_no"], "file_path": Path(src["path"]).as_posix(), "file_sha256": src["sha256"],
               "table": {"sheet": src["sheet"], "cells": cells[:40] or ["B1"]}, "package_level": True}
    elif pat.get("midp"):                      # PKG-008 against the MIDP
        m = pat["midp"]
        cells = [s["cell"] for s in samples if s.get("cell")]
        loc = {"document_no": nums[0] if count == 1 and nums else pkg_doc, "file_path": Path(m["midp_path"]).as_posix(),
               "file_sha256": m["midp_sha256"], "table": {"sheet": m["sheet"], "cells": cells[:40]}}
        if count > 1:
            loc["package_level"] = True
        source_type = "xlsx_cell"
        reference_doc = f"Parsons MIDP {m['midp_number']} (Date Last Updated {m['midp_updated']}) — SRC-14"
        clause = pat.get("midp_clause") or f"EXCHANGE FORMAT columns, sheet '{m['sheet']}' (PKG-008)"
    elif pat.get("list_source"):               # PKG-007 listed but not received
        t = pat["list_source"]
        cells = [s["cell"] for s in samples if s.get("cell")]
        loc = {"document_no": t.get("number") or Path(t["path"]).stem, "file_path": t["path"], "file_sha256": t["sha256"],
               "table": {"sheet": t.get("sheet") or Path(t["path"]).stem, "cells": cells[:40]}}
        source_type = "csv_cell" if t["path"].lower().endswith(".csv") else "xlsx_cell"
        reference_doc = f"Drawing / transmittal list {t.get('number') or Path(t['path']).name}"
        clause = "Listed documents are owed (PKG-007)"
    else:
        s0 = samples[0] if samples else {}
        loc = {"document_no": (s0.get("number") if count == 1 and s0.get("number") else pkg_doc), "package_level": True}
        if count == 1 and s0.get("path"):
            loc["file_path"] = s0["path"]
            if s0.get("sha256"):
                loc["file_sha256"] = s0["sha256"]
    default = (pat.get("status") or rule["default_status"].get(source_type)
               or rule["default_status"].get("script_output") or "Probable")
    shown = ", ".join(nums[:10])
    statement = (f"{pat['message']}. {count} occurrence(s)" + (f"; samples: {shown}" if shown else "")
                 + f". Requirement ({rule['rule_id']}): {rule['statement']}")[:1500]
    extract = " | ".join(s.get("extract", "") for s in samples)[:1500] or pat["message"]
    script = SCRIPT_OF.get(rule["rule_id"], "check_package.py")
    return {
        "schema_version": "1.0", "finding_id": f"{a.run}/INT-{n:03d}", "stage": "candidate", "run_id": a.run,
        "site": a.site, "sub_id": a.sub, "agent": "INT",
        "agent_versions": {"controlling-submittal-package": __version__},
        "title": f"{rule['rule_id']} {rule['title']}: {pat['pattern'].replace('_', ' ')}"[:120],
        "statement": statement, "issue_type": issue_type, "location": loc, "severity": severity,
        "confidence": "High",
        "evidence": [{"evidence_id": "E01", "tier": "deterministic", "source_type": source_type, "extract": extract,
                      "measured_value": count, "unit": "occurrences",
                      "script": {"name": f"controlling-submittal-package/scripts/{script}", "version": __version__,
                                 "exit_code": 0}}],
        "reference": {"rule_id": rule["rule_id"], "rulebook_version": rb_version, "source_doc": reference_doc,
                      "clause": clause},
        "proposed_status": lower(default, rule["cap"]),
        "owner": {"role": rule["owner_role"], "name": None},
        "created_at": now,
    }


def validate(findings):
    try:
        import json
        from jsonschema import Draft202012Validator, FormatChecker
    except ImportError:
        return None
    v = Draft202012Validator(json.loads(SCHEMA.read_text(encoding="utf-8")), format_checker=FormatChecker())
    return [f"/{i}/{'/'.join(map(str, e.absolute_path))}: {e.message[:150]}" for i, f in enumerate(findings)
            for e in v.iter_errors(f)]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", required=True)
    ap.add_argument("--rules", required=True)
    ap.add_argument("--run", required=True)
    ap.add_argument("--site", required=True, choices=["QRWN", "NRJS", "NMAR"])
    ap.add_argument("--sub", required=True)
    ap.add_argument("--package")
    ap.add_argument("--start", type=int, default=1)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    if not path_ok(a.out):
        return fail(EXIT_INPUT, "output path longer than 250 characters")
    if not RUN_RE.match(a.run) or not a.run.startswith(a.site + "-"):
        return fail(EXIT_INPUT, "--run must look like <SITE>-<sub>-YYYYMMDDTHHMM and start with --site")
    try:
        res, rb = read_json(a.results), read_json(a.rules)
        pkg = read_json(a.package) if a.package else None
    except (OSError, ValueError) as exc:
        return fail(EXIT_INPUT, f"cannot read input: {exc}")
    rules = {r["rule_id"]: r for r in rb["rules"]}
    now = datetime.now(RIYADH).isoformat(timespec="seconds")
    findings, skipped, n = [], [], a.start
    for rr in res["results"]:
        for sc in rr.get("subchecks_not_run", []) or []:
            skipped.append({"rule_id": rr["rule_id"], "carrier": rr["carrier"], "subcheck": sc["subcheck"],
                            "reason": sc["reason"]})
        if rr["result"] == "not_run":
            skipped.append({"rule_id": rr["rule_id"], "carrier": rr["carrier"], "reason": rr.get("reason")})
        if rr["result"] != "fail":
            continue
        rule = rules.get(rr["rule_id"])
        if rule is None:
            skipped.append({"rule_id": rr["rule_id"], "reason": "rule not in the rules file"})
            continue
        for pat in rr["patterns"]:
            try:
                findings.append(finding(n, rr, pat, rule, a, pkg, rb.get("rulebook_version"), now))
            except (KeyError, ValueError) as exc:
                return fail(EXIT_INPUT, f"{rr['rule_id']} {pat.get('pattern')}: missing {exc}")
            n += 1
    errs = validate(findings)
    if errs is None:
        return fail(EXIT_DEP, "jsonschema is not installed")
    if errs:
        return fail(EXIT_SCHEMA, "candidates failed finding.schema.json", errors=errs[:30])
    atomic_write_json(a.out, findings)
    by_rule = {}
    for f in findings:
        by_rule[f["reference"]["rule_id"]] = by_rule.get(f["reference"]["rule_id"], 0) + 1
    emit({"status": "ok", "out": str(a.out), "findings": len(findings), "by_rule": by_rule,
          "rules_skipped": res.get("rules_skipped", []), "not_run": skipped})
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
