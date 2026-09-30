#!/usr/bin/env python3
"""FC5 check: every finding cites a rule that exists and is not inactive, and its status does not exceed
the rule cap (active → Confirmed; advisory → Probable). Same function as format_check.py uses.

Usage: python check_cap.py --findings <candidates_or_verified.json> --rulebook <rules_vN.json>
Exit: 0 check ran (result pass|fail in the output) · 2 input error · 3 dependency · 5 refused."""
__version__ = "0.1.0"
import argparse, json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from rulebook_lib import EXIT_INPUT, EXIT_OK, RulebookError, cap_errors, load_rulebook  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--findings", required=True)
    ap.add_argument("--rulebook", required=True)
    a = ap.parse_args(argv)
    try:
        book = load_rulebook(a.rulebook)
        try:
            findings = json.loads(Path(a.findings).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RulebookError(EXIT_INPUT, f"cannot read findings: {exc}") from exc
        if not isinstance(findings, list):
            raise RulebookError(EXIT_INPUT, "findings file must be a JSON array")
        errs = cap_errors(findings, {r["rule_id"]: r for r in book["rules"]}, book["rulebook_version"])
        print(json.dumps({"status": "ok", "check": "FC5", "result": "fail" if errs else "pass",
                          "findings": len(findings), "errors": errs}, ensure_ascii=False))
        return EXIT_OK
    except RulebookError as e:
        print(json.dumps({"status": "error", "code": e.code, "detail": e.detail, "errors": e.errors[:50]}))
        return e.code


if __name__ == "__main__":
    sys.exit(main())
