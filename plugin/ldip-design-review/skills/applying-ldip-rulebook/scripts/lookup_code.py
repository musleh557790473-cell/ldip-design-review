#!/usr/bin/env python3
"""Look up a code list value, or a rule parameter, in the approved LDIP rulebook.

Usage: python lookup_code.py --rulebook <rules_vN.json> --list <name> --value <v>
       python lookup_code.py --rulebook <rules_vN.json> --param <RULE-ID>.<parameter>
Example: --param PKG-006.site_map returns the site map value and its source (never hard-code it).
Exit: 0 · 2 input error (unknown list / rule / parameter) · 3 dependency · 5 refused."""
__version__ = "0.1.0"
import argparse, json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from rulebook_lib import EXIT_INPUT, EXIT_OK, RulebookError, load_rulebook, lookup_code, lookup_param  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--rulebook", required=True)
    ap.add_argument("--list")
    ap.add_argument("--value")
    ap.add_argument("--param")
    a = ap.parse_args(argv)
    try:
        book = load_rulebook(a.rulebook)
        if a.param:
            if "." not in a.param:
                raise RulebookError(EXIT_INPUT, "--param must be <RULE-ID>.<parameter>")
            rid, name = a.param.split(".", 1)
            res = lookup_param(book, rid, name)
        elif a.list and a.value is not None:
            res = lookup_code(book, a.list, a.value)
        else:
            raise RulebookError(EXIT_INPUT, "give --list and --value, or --param")
        print(json.dumps({"status": "ok", **res}, ensure_ascii=False))
        return EXIT_OK
    except RulebookError as e:
        print(json.dumps({"status": "error", "code": e.code, "detail": e.detail}))
        return e.code


if __name__ == "__main__":
    sys.exit(main())
