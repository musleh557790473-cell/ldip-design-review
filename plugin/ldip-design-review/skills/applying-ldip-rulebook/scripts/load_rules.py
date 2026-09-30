#!/usr/bin/env python3
"""Validate an LDIP rulebook (JSON Schema + SPC-0004 §4.5 lint) and report its rule states.

Usage: python load_rules.py <rules_vN.json> [--allow-draft]
Exit: 0 valid and approved · 2 input error · 3 jsonschema missing · 5 refused (invalid, or status not
'approved' — SPC-0005 §4.12). Prints one JSON object. Never edits the file."""
__version__ = "0.1.0"
import argparse, json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from rulebook_lib import EXIT_OK, RulebookError, load_rulebook, state_counts  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("rulebook")
    ap.add_argument("--allow-draft", action="store_true", help="validate a draft without refusing (owner use)")
    a = ap.parse_args(argv)
    try:
        book = load_rulebook(a.rulebook, require_approved=not a.allow_draft)
        print(json.dumps({"status": "ok", "rulebook_version": book["rulebook_version"], "rulebook_status": book["status"],
                          "rules": len(book["rules"]), "states": state_counts(book)}))
        return EXIT_OK
    except RulebookError as e:
        print(json.dumps({"status": "refused" if e.code == 5 else "error", "code": e.code, "detail": e.detail,
                          "errors": e.errors[:100]}))
        return e.code


if __name__ == "__main__":
    sys.exit(main())
