#!/usr/bin/env python3
"""Generate hooks/access_matrix.json from the machine copy of SPC-0002 §5
(specs-machine/access_matrix.source.json), so the matrix is not hand-typed (SPC-0006 §5).

Usage: python build_access_matrix.py --source <source.json> --out <access_matrix.json>

agent_type keys are plugin-qualified only ("<plugin_name>:<agent>"), per test A1 and
P-S3-12 (owner, 30 Sep 2026). The main session has no agent_type and no key here.
Exit codes per SPC-0006 §2.3.3."""
__version__ = "0.2.0"

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ldip_common import EXIT_INPUT, EXIT_OK, EXIT_SCHEMA, ScriptError, atomic_write_json, read_json  # noqa: E402

ALLOWED_KEYS = {"main_session", "read", "read_test_mode", "deny_read", "deny_read_during_run", "write",
                "bash_scripts", "bash_skills", "outside_run", "no_run_required", "read_anywhere"}
PLACEHOLDERS = {"{site}", "{sub}", "{run}", "{plugin_root}"}


def validate(src):
    errs = []
    agents = src.get("agents", {})
    if "orchestrator" not in agents:
        errs.append("orchestrator missing")
    if src.get("agent_type_form") != "qualified":
        errs.append("agent_type_form must be 'qualified' (P-S3-12)")
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", src.get("plugin_name", "")):
        errs.append("plugin_name must be kebab-case")
    for name, spec in agents.items():
        if ":" in name or not re.fullmatch(r"[a-z0-9][a-z0-9-]*", name):
            errs.append(f"{name}: agent names are bare kebab-case in the source; the plugin prefix is added here")
        bad = set(spec) - ALLOWED_KEYS
        if bad:
            errs.append(f"{name}: unknown keys {sorted(bad)}")
        for key in ("read", "write", "deny_read", "read_test_mode", "deny_read_during_run", "bash_scripts"):
            for pat in spec.get(key, []):
                for tok in [t for t in pat.split("/") if "{" in t]:
                    for ph in [p for p in PLACEHOLDERS if p in tok]:
                        tok = tok.replace(ph, "")
                    if "{" in tok or "}" in tok:
                        errs.append(f"{name}.{key}: unknown placeholder in '{pat}'")
                if ".." in pat.split("/"):
                    errs.append(f"{name}.{key}: '..' not allowed in '{pat}'")
        for w in spec.get("write", []):
            if any(w.startswith(p) for p in ("submittals/", "rulebook/", "golden_set/")):
                errs.append(f"{name}: write into read-only area '{w}' (SPC-0002 §5, §9.1)")
    return errs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    try:
        src = read_json(a.source)
        errs = validate(src)
        if errs:
            print(json.dumps({"status": "error", "code": EXIT_SCHEMA, "errors": errs}))
            return EXIT_SCHEMA
        plugin = src["plugin_name"]
        types = {}
        for name, spec in sorted(src["agents"].items()):
            if spec.get("main_session"):
                continue
            types[f"{plugin}:{name}"] = name
        out = {
            "generated_by": f"build_access_matrix.py {__version__}",
            "source": src["source"], "source_version": src["matrix_source_version"],
            "status": src.get("status"), "plugin_name": plugin, "agent_type_form": "qualified",
            "agent_types": types,
            "deny_always": src.get("deny_always", []), "deny_subagents": src.get("deny_subagents", []),
            "agents": src["agents"],
        }
        atomic_write_json(a.out, out)
        print(json.dumps({"status": "ok", "out": a.out, "agents": len(src["agents"]), "agent_types": len(types)}))
        return EXIT_OK
    except ScriptError as e:
        print(json.dumps({"status": "error", "code": e.code, "detail": e.detail}))
        return e.code


if __name__ == "__main__":
    sys.exit(main())
