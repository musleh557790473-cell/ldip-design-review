"""D12 (owner approved 5 Oct 2026): the plugin is enabled account-wide, so the guard must leave other
projects and other plugins' agents alone, while keeping LDIP_Review and LDIP runs closed to them."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
GUARD = REPO / "plugin/ldip-design-review/hooks/guard.py"


def guard(data, root=None, run=False):
    env = {**os.environ}
    for k in ("LDIP_REVIEW_ROOT", "CLAUDE_PLUGIN_DATA", "CLAUDE_CONFIG_DIR"):
        env.pop(k, None)
    env["CLAUDE_CONFIG_DIR"] = "/nonexistent-claude-config"          # no standard data folder (D6)
    if root:
        env["LDIP_REVIEW_ROOT"] = str(root)
        act = Path(root) / "runs/_active_run.json"
        if run:
            act.write_text(json.dumps({"run_id": "QRWN-T0001-20261005T1200", "site": "QRWN", "sub_id": "T0001", "mode": "live"}))
        elif act.exists():
            act.unlink()
    p = subprocess.run([sys.executable, str(GUARD)], input=json.dumps({"hook_event_name": "PreToolUse", **data}),
                       capture_output=True, text=True, env=env)
    assert p.returncode == 0
    return "deny" if p.stdout.strip() else "allow"


@pytest.fixture
def root(tmp_path):
    r = tmp_path / "LDIP & Review"
    (r / "runs").mkdir(parents=True)
    return r


OTHER = Path("/home/someone/other-project")


@pytest.mark.parametrize("agent", ["general-purpose", "Explore", "product-management:researcher", "ldip-a1-probe:worker"])
def test_other_agents_elsewhere_are_not_governed(root, agent):
    for tool, ti in (("Read", {"file_path": f"{OTHER}/notes.md"}), ("Write", {"file_path": f"{OTHER}/out.md"}),
                     ("Bash", {"command": "ls -la && echo done"}), ("Glob", {"pattern": "**/*.md", "path": str(OTHER)})):
        d = {"tool_name": tool, "tool_input": ti, "agent_type": agent, "cwd": str(OTHER)}
        assert guard(d) == "allow", (tool, "no root")
        assert guard(d, root) == "allow", (tool, "root configured, no run")


@pytest.mark.parametrize("tool,ti,cwd", [
    ("Read", {"file_path": "{R}/rulebook/rules_v0.json"}, str(OTHER)),
    ("Write", {"file_path": "{R}/findings/x.json"}, str(OTHER)),
    ("Grep", {"pattern": "x", "path": "{R}/submittals"}, str(OTHER)),
    ("Glob", {"pattern": "{R}/**/*.json"}, str(OTHER)),
    ("Bash", {"command": 'cat "{R}/README.md"'}, str(OTHER)),
    ("Read", {"file_path": "README.md"}, "{R}"),                      # relative path, cwd inside the root
])
def test_other_agents_kept_out_of_ldip_review(root, tool, ti, cwd):
    ti = {k: v.replace("{R}", str(root)) for k, v in ti.items()}
    d = {"tool_name": tool, "tool_input": ti, "agent_type": "general-purpose", "cwd": cwd.replace("{R}", str(root))}
    assert guard(d, root) == "deny"


def test_other_agents_elsewhere_not_governed_during_an_ldip_run(root):
    """G41-1 (0.5.1): an open run no longer reaches other projects."""
    d = {"tool_name": "Read", "tool_input": {"file_path": f"{OTHER}/notes.md"}, "agent_type": "general-purpose", "cwd": str(OTHER)}
    assert guard(d, root, run=True) == "allow"
    assert guard(d, root, run=False) == "allow"


def test_other_agents_denied_during_an_ldip_run_inside_root(root):
    d = {"tool_name": "Read", "tool_input": {"file_path": f"{OTHER}/notes.md"}, "agent_type": "general-purpose", "cwd": str(root / "findings")}
    assert guard(d, root, run=True) == "deny"


def test_answer_key_text_denied_for_everyone():
    d = {"tool_name": "Read", "tool_input": {"file_path": "/x/golden_set/answer_key/k.json"}, "agent_type": "Explore", "cwd": "/x"}
    assert guard(d) == "deny"


@pytest.mark.parametrize("agent", ["verifier", "gis-cadastral", "intake-batch", "source-locator", "orchestrator",
                                   "ldip-design-review:nope", "ldip-design-review:orchestrator", ""])
def test_spoofs_still_denied_everywhere(agent):
    d = {"tool_name": "Read", "tool_input": {"file_path": f"{OTHER}/notes.md"}, "agent_type": agent, "cwd": str(OTHER)}
    assert guard(d) == "deny"


def test_main_session_elsewhere_still_allowed(root):
    d = {"tool_name": "Bash", "tool_input": {"command": "ls && pwd"}, "cwd": str(OTHER)}
    assert guard(d, root) == "allow"


def test_main_session_elsewhere_not_limited_during_a_run(root):
    """G41-1 (0.5.1): the Bash allowlist binds the main session only inside the LDIP scope."""
    d = {"tool_name": "Bash", "tool_input": {"command": "ls && pwd"}, "cwd": str(OTHER)}
    assert guard(d, root, run=True) == "allow"


@pytest.mark.parametrize("cwd,cmd", [
    ("{R}", "ls -la"),                                   # session inside the root
    ("{R}/runs", "python3 -c 1"),                        # not a plugin script
    (str(OTHER), 'ls "{R}/runs"'),                       # outside, but names the root
    (str(OTHER), "cat ../LDIP & Review/x"),              # outside, names the root folder
])
def test_main_session_bash_limited_in_scope_during_a_run(root, cwd, cmd):
    d = {"tool_name": "Bash", "tool_input": {"command": cmd.replace("{R}", str(root))}, "cwd": cwd.replace("{R}", str(root))}
    assert guard(d, root, run=True) == "deny"


def _with_source(root, tmp_path):
    src = tmp_path / "Parsons" / "Submission"
    (src / "NRJS" / "60pct").mkdir(parents=True)
    data = tmp_path / "data"
    data.mkdir()
    (data / "guard_config.json").write_text(json.dumps({"ldip_review_root": str(root),
                                                        "submittal_source_roots": [str(src)]}))
    return src, data


def guard_cfg(data, root, data_dir, run=True):
    env = {**os.environ}
    for k in ("LDIP_REVIEW_ROOT", "CLAUDE_PLUGIN_DATA", "CLAUDE_CONFIG_DIR"):
        env.pop(k, None)
    env["CLAUDE_CONFIG_DIR"] = "/nonexistent-claude-config"
    env["CLAUDE_PLUGIN_DATA"] = str(data_dir)
    act = Path(root) / "runs/_active_run.json"
    if run:
        act.write_text(json.dumps({"run_id": "NRJS-60pct-20261122T2259", "site": "NRJS", "sub_id": "60pct", "mode": "live"}))
    elif act.exists():
        act.unlink()
    p = subprocess.run([sys.executable, str(GUARD)], input=json.dumps({"hook_event_name": "PreToolUse", **data}),
                       capture_output=True, text=True, env=env)
    assert p.returncode == 0
    return "deny" if p.stdout.strip() else "allow"


def test_source_root_is_in_scope_during_a_run(root, tmp_path):
    src, data_dir = _with_source(root, tmp_path)
    read_src = {"tool_name": "Read", "tool_input": {"file_path": f"{src}/NRJS/60pct/a.pdf"}, "agent_type": "general-purpose", "cwd": str(OTHER)}
    assert guard_cfg(read_src, root, data_dir) == "deny"
    in_src = {"tool_name": "Bash", "tool_input": {"command": "ls"}, "agent_type": "general-purpose", "cwd": str(src / "NRJS")}
    assert guard_cfg(in_src, root, data_dir) == "deny"
    main_names_src = {"tool_name": "Bash", "tool_input": {"command": f'ls "{src}"'}, "cwd": str(OTHER)}
    assert guard_cfg(main_names_src, root, data_dir) == "deny"
    elsewhere = {"tool_name": "Read", "tool_input": {"file_path": f"{OTHER}/a.md"}, "agent_type": "general-purpose", "cwd": str(OTHER)}
    assert guard_cfg(elsewhere, root, data_dir) == "allow"


def test_ldip_agents_unchanged_outside_scope(root):
    d = {"tool_name": "Read", "tool_input": {"file_path": f"{OTHER}/notes.md"},
         "agent_type": "ldip-design-review:intake-batch", "cwd": str(OTHER)}
    assert guard(d, root, run=True) == "deny"
