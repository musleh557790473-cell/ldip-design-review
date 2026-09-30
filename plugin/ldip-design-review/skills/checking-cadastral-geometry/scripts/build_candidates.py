#!/usr/bin/env python3
"""Turn GIS check results into SPC-0003 candidate findings and validate them (SPC-0006 §2.5, §4.6).

One finding per (rule, pattern) — count + up to ten samples (SPC-0004 §5.7). proposed_status = the
lower of the rule's default status for the evidence source and the rule cap (SPC-0005 §8).
Confidence follows the evidence tier (deterministic cell reads → High). The statement carries the
observed facts and the requirement only; no reasoning (SPC-0002 C5).

Usage: python build_candidates.py --results <r1.json> [<r2.json> ...] --rules <rules_GIS.json>
           --run <run-id> --site <SITE> --sub <sub-id> --document-no <number> [--review-root <LDIP_Review>]
           [--start 1] --out findings/<run>/candidates/gis.json
Results files: read_dlus.py (GIS-002), check_ids.py (GIS-001), check_schedule.py (GIS-003, GIS-012 to
GIS-015) outputs. Each finding is located in the sheet and file its pattern
was read from (location.file_path is relative to --review-root, default the working directory, which
is the LDIP_Review folder in a run; file_sha256 is that file's hash).
Exit: 0 written · 2 input error · 3 dependency · 4 candidates failed the finding schema (nothing written).
No network, no shell calls.
"""
__version__ = "0.1.0"

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCHEMA = HERE.parent.parent.parent / "schemas" / "finding.schema.json"
STATUS_RANK = {"Confirmed": 3, "Missing information": 3, "Probable": 2, "Not auto-verifiable": 1}
RIYADH = timezone(timedelta(hours=3))


def lower(a, b):
    return a if STATUS_RANK.get(a, 0) <= STATUS_RANK.get(b, 0) else b


def collect(results_docs):
    """Normalise the three result shapes into a list of rule results."""
    out = []
    for d in results_docs:
        if "results" in d:
            out += [dict(r, source=r.get("source") or d.get("dlus_source")) for r in d["results"]]
        elif d.get("rule_id") == "GIS-001":
            out.append(dict(d, source=d.get("source") or d.get("dlus_source")))
        elif "result" in d and isinstance(d["result"], dict) and d["result"].get("rule_id") == "GIS-002":
            res = d["result"]
            pats = {}
            for i in res["issues"]:
                p = pats.setdefault(i["pattern"], {"pattern": i["pattern"], "count": 0, "samples": [], "message": i["message"]})
                p["count"] += 1
                if len(p["samples"]) < 10:
                    p["samples"].append({"plot_id": i.get("plot_id"), "cells": [i["cell"]] if i.get("cell") else [],
                                         "extract": f"{i.get('cell', '')}={i.get('value', '')}".strip("=") or i["message"]})
            out.append({"rule_id": "GIS-002", "result": res["result"], "patterns": list(pats.values()),
                        "source": d.get("source")})
    return out


def finding(n, rr, pat, rule, a, rb_version, now):
    src = pat.get("source") or rr.get("source") or a.default_source
    source_type = src["source_type"]
    samples = pat.get("samples", [])
    count = pat.get("count", pat.get("count_rows", len(samples)))
    cells = []
    for s in samples:
        for c in s.get("cells", []) or []:
            if c and c not in cells:
                cells.append(c)
    plot_ids = [str(s["plot_id"]) for s in samples if s.get("plot_id") not in (None, "")]
    for s in samples:
        plot_ids += [str(x) for x in s.get("plot_ids", []) if str(x) not in plot_ids]
    extract = " | ".join(s.get("extract", "") for s in samples)[:1500] or pat["message"]
    ids = ", ".join(plot_ids[:10])
    l2 = ", ".join(dict.fromkeys(str(s["level2"]) for s in samples if s.get("level2") and not s.get("plot_id")))[:300]
    observed = " | ".join(s.get("extract", "") for s in samples[:3] if s.get("extract"))[:700]
    statement = (f"{pat['message']}. {count} occurrence(s)"
                 + (f" ({l2})" if l2 else "")
                 + (f" (IDs / rows counted: {pat['count_ids']} IDs, {pat['count_rows']} rows)" if "count_ids" in pat else "")
                 + (f"; samples: {ids}" if ids else "")
                 + (f". Observed: {observed}" if observed else "")
                 + f". Requirement ({rule['rule_id']}): {rule['statement']}")[:1500]
    default = rule["default_status"].get(source_type) or rule["default_status"].get("script_output") or "Probable"
    loc = {"document_no": a.document_no, "file_path": rel_path(src["path"], a.review_root), "file_sha256": src["sha256"]}
    if cells:
        loc["table"] = {"sheet": src.get("sheet") or Path(src["path"]).stem, "cells": cells[:40]}
    if plot_ids:
        loc["plot_ids"] = plot_ids
    if not cells and not plot_ids:
        loc["package_level"] = True
    tol = next((p.get("value") for k, p in rule["parameters"].items() if k.startswith("tol") or k.endswith("_tol")), None)
    return {
        "schema_version": "1.0", "finding_id": f"{a.run}/GIS-{n:03d}", "stage": "candidate", "run_id": a.run,
        "site": a.site, "sub_id": a.sub, "agent": "GIS",
        "agent_versions": {"checking-cadastral-geometry": __version__},
        "title": f"{rule['rule_id']} {rule['title']}: {pat['pattern'].split('/')[0].replace('_', ' ')}"[:120],
        "statement": statement, "issue_type": rule["issue_type"], "location": loc,
        "severity": rule["severity_default"], "confidence": "High",
        "evidence": [{"evidence_id": "E01", "tier": "deterministic", "source_type": source_type, "extract": extract,
                      "measured_value": count, "unit": "occurrences",
                      "script": {"name": f"checking-cadastral-geometry/scripts/{a.script_of.get(rule['rule_id'], 'check_schedule.py')}",
                                 "version": __version__, "exit_code": 0}}],
        "reference": {"rule_id": rule["rule_id"], "rulebook_version": rb_version,
                      "source_doc": f"LDIP Rulebook {rb_version} (AICC-BIM-SPC-0004)", "clause": f"{rule['rule_id']}: {rule['title']}",
                      "tolerance": tol},
        "proposed_status": lower(default, rule["cap"]),
        "owner": {"role": rule["owner_role"], "name": None},
        "created_at": now,
    }


def rel_path(path, root):
    p = Path(path)
    if not p.is_absolute():
        return p.as_posix()
    try:
        return p.resolve().relative_to(Path(root).resolve()).as_posix()
    except ValueError:
        raise ValueError(f"source file {p} is outside the review root {root}")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", nargs="+", required=True)
    ap.add_argument("--rules", required=True)
    ap.add_argument("--run", required=True)
    ap.add_argument("--site", required=True)
    ap.add_argument("--sub", required=True)
    ap.add_argument("--document-no", required=True)
    ap.add_argument("--review-root", default=".",
                    help="LDIP_Review folder (default: the working directory); file paths are written relative to it")
    ap.add_argument("--start", type=int, default=1)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    if len(str(Path(a.out).resolve())) > 250:
        print(json.dumps({"status": "error", "code": 2, "detail": "output path longer than 250 characters"}))
        return 2
    try:
        docs = [json.loads(Path(p).read_text(encoding="utf-8")) for p in a.results]
        rb = json.loads(Path(a.rules).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "error", "code": 2, "detail": f"cannot read input: {exc}"}))
        return 2
    rules = {r["rule_id"]: r for r in rb["rules"]}
    a.default_source = next((d.get("source") or d.get("dlus_source") for d in docs if d.get("source") or d.get("dlus_source")), None)
    if not a.default_source:
        print(json.dumps({"status": "error", "code": 2, "detail": "no source block in the results (run read_dlus.py first)"}))
        return 2
    a.script_of = {"GIS-001": "check_ids.py", "GIS-002": "read_dlus.py"}
    now = datetime.now(RIYADH).isoformat(timespec="seconds")
    findings, skipped, n = [], [], a.start
    for rr in collect(docs):
        if rr.get("result") != "fail":
            if rr.get("result") == "not_run":
                skipped.append({"rule_id": rr["rule_id"], "reason": rr.get("reason")})
            continue
        rule = rules.get(rr["rule_id"])
        if rule is None:
            skipped.append({"rule_id": rr["rule_id"], "reason": "rule not in the rules file"})
            continue
        for pat in rr.get("patterns", []):
            try:
                findings.append(finding(n, rr, pat, rule, a, rb.get("rulebook_version"), now))
            except ValueError as exc:
                print(json.dumps({"status": "error", "code": 2, "detail": str(exc)}))
                return 2
            n += 1
    try:
        from jsonschema import Draft202012Validator, FormatChecker
    except ImportError:
        print(json.dumps({"status": "error", "code": 3, "detail": "jsonschema is not installed"}))
        return 3
    v = Draft202012Validator(json.loads(SCHEMA.read_text(encoding="utf-8")), format_checker=FormatChecker())
    errs = [f"/{i}/{'/'.join(map(str, e.absolute_path))}: {e.message[:150]}" for i, f in enumerate(findings) for e in v.iter_errors(f)]
    if errs:
        print(json.dumps({"status": "error", "code": 4, "detail": "candidates failed finding.schema.json", "errors": errs[:30]}))
        return 4
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.name + ".tmp")
    tmp.write_text(json.dumps(findings, indent=1, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, out)
    print(json.dumps({"status": "ok", "findings": len(findings), "by_rule": {r: sum(1 for f in findings if f["reference"]["rule_id"] == r)
                      for r in sorted({f["reference"]["rule_id"] for f in findings})},
                      "not_run": skipped, "out": str(out)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
