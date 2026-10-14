#!/usr/bin/env python3
"""Return the rules an LDIP agent may run, each with its state and highest allowed status (cap).

Usage: python get_rules.py --rulebook <rules_vN.json> --agent INT|GIS|RDE|COO [--states active,advisory]
                           [--skill <skill-name>] [--out <file.json>]
Inactive rules are listed under "rules_skipped" (the Orchestrator logs them as rule_skipped).
Exit: 0 · 2 input error · 3 dependency · 5 refused (rulebook invalid or not approved)."""
__version__ = "0.2.0"
import argparse, json, os, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from rulebook_lib import EXIT_INPUT, EXIT_OK, RulebookError, load_rulebook, rules_for_agent  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--rulebook", required=True)
    ap.add_argument("--agent", required=True, choices=["INT", "GIS", "RDE", "COO"])
    ap.add_argument("--states", default="active,advisory")
    ap.add_argument("--skill")
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    try:
        states = tuple(s.strip() for s in a.states.split(",") if s.strip())
        if not states or any(s not in ("active", "advisory") for s in states):
            raise RulebookError(EXIT_INPUT, "--states accepts active and/or advisory only (inactive rules never run)")
        res = rules_for_agent(load_rulebook(a.rulebook), a.agent, states, a.skill)
        if a.out:
            out = Path(a.out)
            out.parent.mkdir(parents=True, exist_ok=True)
            tmp = out.with_name(out.name + ".tmp")
            tmp.write_text(json.dumps(res, indent=2, sort_keys=True, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, out)
            print(json.dumps({"status": "ok", "out": str(out), "rules": len(res["rules"]),
                              "rules_skipped": len(res["rules_skipped"])}))
        else:
            print(json.dumps(res, sort_keys=True, ensure_ascii=False))
        return EXIT_OK
    except RulebookError as e:
        print(json.dumps({"status": "error", "code": e.code, "detail": e.detail, "errors": e.errors[:50]}))
        return e.code


if __name__ == "__main__":
    sys.exit(main())
