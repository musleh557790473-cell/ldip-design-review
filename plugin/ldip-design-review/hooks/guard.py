#!/usr/bin/env python3
"""ldip-design-review guard hook — enforcement layer L2 (SPC-0002 Rev E §8; SPC-0005 Rev C §7).

PreToolUse hook for Read | Glob | Grep | Write | Edit | MultiEdit | NotebookEdit | Bash.
  * agent_type from the hook input selects the agent. Plugin sub-agents carry the
    plugin-qualified form "ldip-design-review:<agent>"; the key is absent in the main
    session, which is treated as the Orchestrator (test A1; P-S3-12). Bare names and an
    empty or non-string agent_type are denied.
  * Paths are normalised to absolute POSIX form ('..' and symlinks resolved) and matched
    against hooks/access_matrix.json with the active run's {site}/{sub}/{run} filled in.
  * Bash must match the agent's script allowlist (SPC-0005 §4.3, §7.3); shell operators
    are refused; path arguments are checked like reads/writes.
  * Fail-closed: unknown agent_type, missing configuration or an internal error -> deny.

Allow = exit 0 with no output (normal permission flow continues).
Deny  = JSON with hookSpecificOutput.permissionDecision = "deny" and a reason.

Configuration: LDIP_REVIEW_ROOT (env) or guard_config.json {"ldip_review_root": "..."} in
$CLAUDE_PLUGIN_DATA or next to this file. No network, no shell calls.
"""
__version__ = "0.3.1"

import json
import os
import re
import shlex
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "scripts"))

from ldip_common import (  # noqa: E402
    ACTIVE_RUN_FILE, any_match, configured_review_root, expand, glob_match, rel_to_root, to_posix_abs,
)

WRITE_TOOLS = {"Write": "file_path", "Edit": "file_path", "MultiEdit": "file_path",
               "NotebookEdit": "notebook_path"}
PY_LAUNCHERS = {"python", "python3", "py"}
SHELL_META = re.compile(r"[|;&<>`\n\r]|\$\(|\$\{|\$[A-Za-z_]")


class Deny(Exception):
    pass


# ------------------------------------------------------------------ config
def plugin_root():
    return to_posix_abs(os.environ.get("CLAUDE_PLUGIN_ROOT") or HERE.parent, "/")


def load_config():
    root = configured_review_root(HERE)
    matrix_path = Path(os.environ.get("LDIP_ACCESS_MATRIX") or HERE / "access_matrix.json")
    matrix = json.loads(matrix_path.read_text(encoding="utf-8"))
    return root, matrix


def active_run(root):
    if not root:
        return None
    f = Path(root) / ACTIVE_RUN_FILE
    if not f.is_file():
        return None
    data = json.loads(f.read_text(encoding="utf-8"))
    return data if data.get("run_id") else None


def resolve_agent(data, matrix):
    """Absent agent_type key -> orchestrator (main session). Otherwise the exact plugin-qualified
    string must be in matrix["agent_types"] (P-S3-12); anything else is denied (fail-closed)."""
    if "agent_type" not in data or data["agent_type"] is None:
        return "orchestrator"
    agent_type = data["agent_type"]
    types = matrix.get("agent_types", {})
    if isinstance(agent_type, str) and agent_type in types:
        return types[agent_type]
    plugin = matrix.get("plugin_name", "ldip-design-review")
    if isinstance(agent_type, str) and agent_type and ":" not in agent_type:
        raise Deny(f"bare agent_type '{agent_type}' denied; expected '{plugin}:<agent>' (P-S3-12, fail-closed)")
    raise Deny(f"unknown agent_type {agent_type!r} (fail-closed, SPC-0005 §7.2)")


# ------------------------------------------------------------------ path helpers
def subtree_may_contain(base_rel, pattern):
    """True if some path under directory base_rel could match pattern."""
    b = [s for s in base_rel.split("/") if s] if base_rel else []
    p = pattern.split("/")
    for i, seg in enumerate(b):
        if i >= len(p):
            return False
        if p[i] == "**":
            return True
        if not glob_match(seg, p[i]):
            return False
    return True


def dir_fully_covered(base_rel, patterns):
    """True if every path under base_rel is covered by one pattern of the form <prefix>/**."""
    parts = [s for s in base_rel.split("/") if s] if base_rel else []
    ancestors = ["/".join(parts[:i]) for i in range(len(parts), 0, -1)]
    for pat in patterns:
        if pat.endswith("/**"):
            prefix = pat[:-3]
            if any(glob_match(a, prefix) for a in ancestors):
                return True
    return False


def under(abs_posix, base_posix):
    return abs_posix == base_posix or abs_posix.startswith(base_posix.rstrip("/") + "/")


# ------------------------------------------------------------------ decision
class Ctx:
    def __init__(self, root, matrix, agent, run, cwd):
        self.root, self.matrix, self.agent, self.run, self.cwd = root, matrix, agent, run, cwd
        self.spec = matrix["agents"][agent]
        self.proot = plugin_root()
        vals = {"{plugin_root}": self.proot}
        if run:
            vals.update({"{site}": run.get("site"), "{sub}": run.get("sub_id"), "{run}": run.get("run_id")})
        self.vals = vals
        g = lambda key: expand(self.spec.get(key, []), vals)  # noqa: E731
        self.read = g("read") + g("write")
        if run and run.get("mode") == "test":
            self.read += g("read_test_mode")
        self.write = g("write")
        self.deny = expand(matrix.get("deny_always", []), vals)
        self.deny_write = expand(matrix.get("deny_write_always", []), vals)
        self.deny_read = list(self.deny)
        if agent != "orchestrator":
            self.deny_read += expand(matrix.get("deny_subagents", []), vals) + g("deny_read")
        elif run:
            self.deny_read += g("deny_read_during_run")

    @property
    def main(self):
        return self.agent == "orchestrator"


def check_read(ctx, abs_p, recursive_dir=False):
    rel = rel_to_root(abs_p, ctx.root) if ctx.root else None
    if "golden_set/answer_key" in abs_p:
        raise Deny("golden_set/answer_key is never readable (SPC-0002 §5.3)")
    if rel is not None:
        for pat in ctx.deny_read:
            hit = subtree_may_contain(rel, pat) if recursive_dir else glob_match(rel, pat)
            if hit:
                raise Deny(f"read of '{rel}' denied for {ctx.agent}: matches '{pat}' (SPC-0002 §5)")
    if ctx.main and not ctx.run:
        return
    if ctx.main:  # during a run the main session may read anything not denied above
        return
    if ctx.spec.get("read_anywhere"):
        return
    if under(abs_p, ctx.proot):
        return
    if rel is None:
        raise Deny(f"read outside LDIP_Review denied for {ctx.agent}: {abs_p}")
    ok = dir_fully_covered(rel, ctx.read) if recursive_dir else any_match(rel, ctx.read)
    if not ok:
        raise Deny(f"read of '{rel}' is outside the read set of {ctx.agent} (SPC-0005 §5)")


def check_write(ctx, abs_p):
    rel = rel_to_root(abs_p, ctx.root) if ctx.root else None
    if "golden_set/" in abs_p and ("answer_key" in abs_p or "results" in abs_p):
        raise Deny("golden_set answer_key/results are never writable by the plugin (SPC-0002 §5)")
    if rel is not None:
        cands = [rel] + ([rel[:-4]] if rel.endswith(".tmp") else [])
        for pat in ctx.deny_write:
            if any(glob_match(c, pat) for c in cands):
                raise Deny(f"write of '{rel}' is never allowed through tools (matches '{pat}'; "
                           "submittals are read-only, run log / manifest / active-run marker are script-only)")
    if ctx.main and not ctx.run:
        return
    if rel is None:
        if ctx.main:
            return  # scratch outside LDIP_Review; L3 covers the tree itself
        raise Deny(f"write outside LDIP_Review denied for {ctx.agent}: {abs_p}")
    candidates = [rel]
    if rel.endswith(".tmp"):
        candidates.append(rel[:-4])  # atomic write sibling (SPC-0002 §6.2)
    if not any(any_match(c, ctx.write) for c in candidates):
        raise Deny(f"write of '{rel}' is outside the write set of {ctx.agent} (SPC-0002 §5, §8 L2)")


def allowed_scripts(ctx):
    pats = expand(ctx.spec.get("bash_scripts", []), ctx.vals)
    for skill in ctx.spec.get("bash_skills", []):
        pats.append(f"{ctx.proot}/skills/{skill}/scripts/*.py")
    return pats


def check_bash(ctx, command):
    if ctx.main and not ctx.run:
        return
    cmd = command.replace("${CLAUDE_PLUGIN_ROOT}", ctx.proot).replace("$CLAUDE_PLUGIN_ROOT", ctx.proot)
    if SHELL_META.search(cmd):
        raise Deny("Bash: shell operators, redirection and variables are not allowed (SPC-0005 §7.3)")
    try:
        tokens = shlex.split(cmd)
    except ValueError as exc:
        raise Deny(f"Bash: cannot parse command ({exc})")
    if len(tokens) < 2 or Path(tokens[0]).name.lower().removesuffix(".exe") not in PY_LAUNCHERS:
        raise Deny("Bash: only 'python <plugin script> ...' is allowed (SPC-0005 §4.3)")
    script = to_posix_abs(tokens[1], ctx.cwd)
    if not any(glob_match(script, p) for p in allowed_scripts(ctx)):
        raise Deny(f"Bash: script not in the allowlist of {ctx.agent}: {script}")
    for tok in tokens[2:]:
        val = tok.split("=", 1)[1] if tok.startswith("--") and "=" in tok else tok
        if val.startswith("-") or not ("/" in val or "\\" in val or val.startswith(".")):
            continue
        p = to_posix_abs(val, ctx.cwd)
        rel = rel_to_root(p, ctx.root) if ctx.root else None
        try:
            # a sub-agent's directory argument (e.g. --resolve-base) is checked as a tree, like Glob/Grep
            check_read(ctx, p, recursive_dir=not ctx.main and os.path.isdir(p))
        except Deny:
            if rel is None or ctx.main or not any_match(rel, ctx.write):
                raise


def decide(data):
    root, matrix = load_config()
    agent = resolve_agent(data, matrix)
    if agent != "orchestrator" and not root:
        raise Deny("LDIP_REVIEW_ROOT not configured (fail-closed)")
    run = active_run(root)
    spec = matrix["agents"][agent]
    if agent != "orchestrator" and not run and not spec.get("no_run_required"):
        raise Deny(f"{agent} may act only during an active review run (runs/_active_run.json)")
    cwd = data.get("cwd") or os.getcwd()
    ctx = Ctx(root, matrix, agent, run, cwd)
    tool = data.get("tool_name", "")
    ti = data.get("tool_input") or {}

    if tool == "Read":
        check_read(ctx, to_posix_abs(ti.get("file_path", ""), cwd))
    elif tool in WRITE_TOOLS:
        check_write(ctx, to_posix_abs(ti.get(WRITE_TOOLS[tool], ""), cwd))
    elif tool in ("Glob", "Grep"):
        base = ti.get("path") or cwd
        pat = ti.get("pattern", "") if tool == "Glob" else ""
        if pat.startswith("/") or re.match(r"^[A-Za-z]:[\\/]", pat):
            lit = re.split(r"[*?\[]", pat.replace("\\", "/"), 1)[0]
            base = lit.rsplit("/", 1)[0] or "/"
        check_read(ctx, to_posix_abs(base, cwd), recursive_dir=True)
    elif tool == "Bash":
        check_bash(ctx, ti.get("command", ""))
    # other tools are not matched by hooks.json; nothing to decide


def main():
    raw = sys.stdin.read()
    try:
        data = json.loads(raw) if raw.strip() else {}
        decide(data)
        return 0
    except Deny as d:
        reason = str(d)
    except Exception as exc:  # fail-closed (SPC-0005 §7.2)
        reason = f"guard internal error, denied (fail-closed): {exc!r}"
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                             "permissionDecision": "deny",
                                             "permissionDecisionReason": f"[ldip guard {__version__}] {reason}"}}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
