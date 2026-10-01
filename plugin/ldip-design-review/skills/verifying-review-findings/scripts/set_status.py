#!/usr/bin/env python3
"""Set each candidate's verification status and write verified.json (SPC-0006 §4.7.4; SPC-0005 §5.7.2).

Status = the lowest of
  (a) the rule's default status for the strongest evidence source (SPC-0004 §5.5),
  (b) the rule cap from the approved rulebook (active → Confirmed, advisory → Probable),
  (c) the re-check: disagrees → Probable (V2); not_rechecked → at most Probable (V1 needs agrees).
Then the SPC-0003 §4.6 rules: Low confidence never Confirmed (V5); Missing information needs
issue_type missing_information and a cited source_doc + clause, else Probable (V4); Not
auto-verifiable carries a manual_check (V3). An inactive or unknown rule → Probable with a note
(the format checker also fails it, FC5). Only `stage` and the `verification` block are written;
every other candidate field is copied unchanged (FC2).

Usage: python set_status.py --candidates findings/<run>/candidates/*.json --recheck <recheck.json>
                            --rulebook rulebook/rules_v0.json --out findings/<run>/verified.json
Exit: 0 written · 2 input error · 3 jsonschema missing · 4 output failed finding.schema.json ·
5 rulebook refused. No network, no shell calls.
"""
__version__ = "0.1.0"

import argparse
import copy
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent.parent / "applying-ldip-rulebook" / "scripts"))
from verify_common import EXIT_OK, EXIT_SCHEMA, VerifyError, atomic_write_json, read_json  # noqa: E402
from rulebook_lib import CAP, STATUS_RANK, RulebookError, load_rulebook  # noqa: E402

SCHEMA = HERE.parents[2] / "schemas" / "finding.schema.json"
RIYADH = timezone(timedelta(hours=3))
TIER_RANK = {"deterministic": 3, "extracted_text_crop": 2, "visual": 1}


def lowest(*statuses):
    s = [x for x in statuses if x]
    best = s[0]
    for x in s[1:]:
        if STATUS_RANK.get(x, 0) < STATUS_RANK.get(best, 0):
            best = x
    return best


def strongest_source(f):
    ev = [e for e in f.get("evidence", []) if isinstance(e, dict)]
    if not ev:
        return None
    return max(ev, key=lambda e: TIER_RANK.get(e.get("tier"), 0)).get("source_type")


def decide(f, rule, rc):
    notes = []
    res = rc.get("recheck_result", "not_rechecked")
    if rule is None or rule.get("state") == "inactive":
        why = "not in the run rulebook" if rule is None else "inactive"
        return "Probable", res, [f"rule {(f.get('reference') or {}).get('rule_id')} is {why}; status held at Probable"]
    src = strongest_source(f)
    default = rule.get("default_status", {}).get(src) or "Probable"
    if src not in rule.get("default_status", {}):
        notes.append(f"no default status for source {src}; Probable used")
    status = lowest(default, CAP[rule["state"]])
    if res == "disagrees":
        status = "Probable"                                    # V2, overrides even a lower default
        notes.append("re-check disagrees: Probable, not retried (brief §4.3c)")
    elif res == "not_rechecked":
        status = lowest(status, "Probable")
        notes.append("no independent re-check: at most Probable (V1)")
    if f.get("confidence") == "Low" and status in ("Confirmed", "Missing information"):
        status = "Probable"
        notes.append("Low confidence cannot be Confirmed (V5)")
    if status == "Missing information":
        ref = f.get("reference") or {}
        if f.get("issue_type") != "missing_information" or not ref.get("source_doc") or not ref.get("clause"):
            status = "Probable"
            notes.append("Missing information needs issue_type missing_information and a cited obligation (V4)")
    return status, res, notes


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--candidates", nargs="+", required=True)
    ap.add_argument("--recheck", required=True)
    ap.add_argument("--rulebook", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    try:
        try:
            book = load_rulebook(a.rulebook)
        except RulebookError as e:
            print(json.dumps({"status": "error", "code": e.code, "detail": e.detail, "errors": e.errors[:10]}))
            return e.code
        rules = {r["rule_id"]: r for r in book["rules"]}
        rc_all = read_json(a.recheck).get("results", {})
        cands = []
        for p in a.candidates:
            doc = read_json(p)
            if not isinstance(doc, list):
                raise VerifyError(2, f"{p}: a candidates file is a JSON array")
            cands += doc
        now = datetime.now(RIYADH).isoformat(timespec="seconds")
        out, counts = [], {}
        for f in cands:
            fid = f.get("finding_id")
            rc = rc_all.get(fid)
            if rc is None:
                rc = {"recheck_result": "not_rechecked", "method": "no re-check record for this finding"}
            rule = rules.get((f.get("reference") or {}).get("rule_id"))
            status, res, notes = decide(f, rule, rc)
            v = copy.deepcopy(f)
            v["stage"] = "verified"
            ver = {"status": status, "recheck_result": res, "method": rc.get("method") or "not stated",
                   "verified_at": now, "notes": "; ".join(notes) or None}
            if status == "Not auto-verifiable":
                loc = f.get("location") or {}
                ver["manual_check"] = {"what": f.get("title") or f.get("statement", "")[:200],
                                       "where": f"{loc.get('file_path')} {((loc.get('table') or {}).get('cells') or [])[:5]}"}
            v["verification"] = ver
            out.append(v)
            counts[status] = counts.get(status, 0) + 1
        try:
            from jsonschema import Draft202012Validator, FormatChecker
        except ImportError:
            print(json.dumps({"status": "error", "code": 3, "detail": "jsonschema is not installed"}))
            return 3
        val = Draft202012Validator(read_json(SCHEMA), format_checker=FormatChecker())
        errs = [f"/{i}/{'/'.join(map(str, e.absolute_path))}: {e.message[:150]}" for i, x in enumerate(out)
                for e in val.iter_errors(x)]
        if errs:
            print(json.dumps({"status": "error", "code": EXIT_SCHEMA, "detail": "verified findings failed the schema",
                              "errors": errs[:20]}))
            return EXIT_SCHEMA
        atomic_write_json(a.out, out)
        print(json.dumps({"status": "ok", "findings": len(out), "statuses": dict(sorted(counts.items())), "out": a.out}))
        return EXIT_OK
    except VerifyError as e:
        print(json.dumps({"status": "error", "code": e.code, "detail": e.detail}))
        return e.code


if __name__ == "__main__":
    sys.exit(main())
