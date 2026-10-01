#!/usr/bin/env python3
"""Check verified.json before the Verifier returns (SPC-0006 §4.7.4): finding schema; FC2 — every
candidate field unchanged (only `stage` and `verification` may differ); FC7 — every candidate
appears exactly once, nothing added; FC5 — status within the rule cap (skill 8 implementation).

Usage: python validate_verified.py --verified findings/<run>/verified.json
                                   --candidates findings/<run>/candidates/*.json --rulebook <rules_vN.json>
Exit: 0 pass · 2 input error · 3 jsonschema missing · 4 a check failed · 5 rulebook refused.
No network, no shell calls.
"""
__version__ = "0.1.0"

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent.parent / "applying-ldip-rulebook" / "scripts"))
from verify_common import EXIT_OK, EXIT_SCHEMA, VerifyError, read_json  # noqa: E402
from rulebook_lib import RulebookError, cap_errors, load_rulebook  # noqa: E402

SCHEMA = HERE.parents[2] / "schemas" / "finding.schema.json"
ADDED = {"stage", "verification"}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--verified", required=True)
    ap.add_argument("--candidates", nargs="+", required=True)
    ap.add_argument("--rulebook", required=True)
    a = ap.parse_args(argv)
    try:
        try:
            book = load_rulebook(a.rulebook)
        except RulebookError as e:
            print(json.dumps({"status": "error", "code": e.code, "detail": e.detail}))
            return e.code
        try:
            from jsonschema import Draft202012Validator, FormatChecker
        except ImportError:
            print(json.dumps({"status": "error", "code": 3, "detail": "jsonschema is not installed"}))
            return 3
        ver = read_json(a.verified)
        cands = []
        for p in a.candidates:
            cands += read_json(p)
        if not isinstance(ver, list):
            raise VerifyError(2, "verified.json is a JSON array")
        errs = []
        val = Draft202012Validator(read_json(SCHEMA), format_checker=FormatChecker())
        for i, f in enumerate(ver):
            for e in val.iter_errors(f):
                errs.append(f"S2 /{i}/{'/'.join(map(str, e.absolute_path))}: {e.message[:150]}")
            if f.get("stage") != "verified":
                errs.append(f"S2 /{i}/stage: shall be 'verified'")
        by_id = {}
        for i, f in enumerate(ver):
            by_id.setdefault(f.get("finding_id"), []).append(i)
        cand_ids = [c.get("finding_id") for c in cands]
        for cid in cand_ids:
            n = len(by_id.get(cid, []))
            if n != 1:
                errs.append(f"FC7 {cid}: appears {n} times in verified.json (shall be once)")
        for vid in by_id:
            if vid not in cand_ids:
                errs.append(f"FC7 {vid}: not a candidate (the Verifier shall not add findings)")
        cmap = {c.get("finding_id"): c for c in cands}
        for vid, idx in by_id.items():
            c = cmap.get(vid)
            if c is None:
                continue
            v = ver[idx[0]]
            for k in sorted(set(c) | set(v)):
                if k in ADDED:
                    continue
                if c.get(k) != v.get(k):
                    errs.append(f"FC2 {vid}: field '{k}' changed")
        for e in cap_errors(ver, {r["rule_id"]: r for r in book["rules"]}, book.get("rulebook_version")):
            errs.append(f"FC5 {e['path']}: {e['message']}")
        print(json.dumps({"status": "ok" if not errs else "fail", "verified": len(ver), "candidates": len(cands),
                          "errors": errs[:30]}))
        return EXIT_OK if not errs else EXIT_SCHEMA
    except VerifyError as e:
        print(json.dumps({"status": "error", "code": e.code, "detail": e.detail}))
        return e.code


if __name__ == "__main__":
    sys.exit(main())
