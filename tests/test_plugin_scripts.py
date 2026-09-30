"""Tests for the ldip-design-review plugin scripts and guard hook (SPC-0006 §5.1).
All fixtures are synthetic and built in a temp folder whose path contains '&' and spaces (SPC-0002 R4).
No project data is used or committed."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
PLUGIN = REPO / "plugin" / "ldip-design-review"
PY = sys.executable
RUN = "QRWN-T0001-20260930T1200"


def sh(args, env=None, stdin=None):
    p = subprocess.run([PY, *map(str, args)], capture_output=True, text=True, input=stdin, env=env)
    out = p.stdout.strip().splitlines()
    return p.returncode, (json.loads(out[-1]) if out else {})


@pytest.fixture
def root(tmp_path):
    r = tmp_path / "03 WIP & Co" / "LDIP_Review"
    for d in ["submittals/QRWN/T0001", "submittals/QRWN/masterplan-schedule", "rulebook", "runs",
              "0.ai_context/QRWN/T0001/b01", f"findings/{RUN}/candidates", f"findings/{RUN}/reasoning",
              f"findings/{RUN}/evidence/gis", "outputs", "golden_set/answer_key", "golden_set/inputs", "_index"]:
        (r / d).mkdir(parents=True, exist_ok=True)
    (r / "submittals/QRWN/T0001/A-001.dwg").write_bytes(b"dwg-bytes")
    (r / "submittals/QRWN/T0001/A-001.pdf").write_bytes(b"pdf-bytes")
    (r / "rulebook/rules_v0.json").write_text(json.dumps({"status": "approved", "rulebook_version": "v0"}))
    (r / "rulebook/rules_v9.json").write_text(json.dumps({"status": "draft", "rulebook_version": "v9"}))
    (r / f"findings/{RUN}/reasoning/gis.md").write_text("notes")
    (r / "golden_set/answer_key/key.json").write_text("{}")
    return r


# ------------------------------------------------------------------ run_manifest
def open_run(root, env_root="same", **kw):
    env = {**os.environ}
    env.pop("CLAUDE_PLUGIN_DATA", None)
    env.pop("LDIP_REVIEW_ROOT", None)
    if env_root == "same":
        env["LDIP_REVIEW_ROOT"] = str(root)
    elif env_root:
        env["LDIP_REVIEW_ROOT"] = env_root
    args = [PLUGIN / "scripts/run_manifest.py", "open", "--root", root, "--site", kw.get("site", "QRWN"),
            "--sub", "T0001", "--mode", kw.get("mode", "live"), "--rulebook", kw.get("rb", "rulebook/rules_v0.json"),
            "--now", "20260930T1200"]
    return sh(args, env=env)


def test_manifest_open_event_close(root):
    rc, res = open_run(root)
    assert rc == 0 and res["run_id"] == RUN and res["inputs"] == 2
    m = json.loads((root / f"runs/{RUN}/manifest.json").read_text())
    assert m["status"] == "running" and m["rulebook_version"] == "v0" and len(m["inputs"][0]["sha256"]) == 64
    assert m["plugin_name"] == "ldip-design-review" and m["script_versions"]["hooks/guard.py"]
    assert (root / "runs/_active_run.json").exists()
    assert open_run(root)[0] == 5                      # second run refused while one is active
    rc, _ = sh([PLUGIN / "scripts/run_manifest.py", "event", "--root", root, "--run", RUN,
                "--agent", Q + "gis-cadastral", "--event", "rule_skipped", "--detail", '{"rule_id":"GIS-004"}'])
    assert rc == 0
    assert sh([PLUGIN / "scripts/run_manifest.py", "event", "--root", root, "--run", RUN,
               "--agent", "x", "--event", "bogus"])[0] == 2
    rc, _ = sh([PLUGIN / "scripts/run_manifest.py", "close", "--root", root, "--run", RUN, "--status", "completed"])
    assert rc == 0 and not (root / "runs/_active_run.json").exists()
    events = [json.loads(l)["event"] for l in (root / f"runs/{RUN}/run.jsonl").read_text().splitlines()]
    assert events == ["start", "rule_skipped", "end"]
    assert sh([PLUGIN / "scripts/run_manifest.py", "close", "--root", root, "--run", RUN, "--status", "failed"])[0] == 5
    assert open_run(root)[0] == 5                      # same run-id never overwritten


def test_manifest_refusals(root):
    assert open_run(root, rb="rulebook/rules_v9.json")[0] == 5   # draft rulebook (SPC-0005 §4.12)
    assert open_run(root, site="XXXX")[0] == 2
    assert open_run(root, mode="prod")[0] == 2
    # P-S3-8: guard root must be configured and equal to --root
    assert open_run(root, env_root=None)[0] == 5
    assert open_run(root, env_root="/some/other/LDIP_Review")[0] == 5
    assert not (root / "runs/_active_run.json").exists()


def test_emergency_close_lifts_run_restrictions(root):
    """P-S3-1: a crashed run is ended with close --status aborted; the main session is free again."""
    assert open_run(root)[0] == 0
    cat = {"command": f"cat {root}/rulebook/rules_v0.json"}
    assert guard_raw(root, {"tool_name": "Bash", "tool_input": cat}, run=None) == "deny"
    rc, _ = sh([PLUGIN / "scripts/run_manifest.py", "close", "--root", root, "--run", RUN, "--status", "aborted"])
    assert rc == 0 and not (root / "runs/_active_run.json").exists()
    assert guard_raw(root, {"tool_name": "Bash", "tool_input": cat}, run=None) == "allow"


# ------------------------------------------------------------------ snapshot / L3
def test_snapshot_l3(root, tmp_path):
    snap = PLUGIN / "scripts/snapshot.py"
    b = tmp_path / "before.json"
    a = tmp_path / "after.json"
    assert sh([snap, "take", "--root", root, "--out", b])[0] == 0
    assert not any("answer_key" in k for k in json.loads(b.read_text())["files"])
    (root / f"findings/{RUN}/candidates/gis.json").write_text("[]")         # inside write set
    (root / f"findings/{RUN}/evidence/gis/GIS-001_1.png.tmp").write_text("x")  # tmp sibling, in set
    rc, res = sh([snap, "take", "--root", root, "--out", a, "--reuse", b])
    assert rc == 0 and res["reused_hashes"] == 2        # P-S3-9: only the 2 submittal files reuse hashes
    common = ["--matrix", PLUGIN / "hooks/access_matrix.json", "--site", "QRWN", "--sub", "T0001", "--run", RUN]
    rc, res = sh([snap, "diff", "--before", b, "--after", a, "--agent", "gis-cadastral", *common])
    assert rc == 0 and res["l3"] == "pass" and res["added"] == 2
    (root / "submittals/QRWN/T0001/A-001.pdf").write_bytes(b"changed")     # outside write set
    (root / f"findings/{RUN}/reasoning/gis.md").unlink()                   # own file deleted: allowed
    sh([snap, "take", "--root", root, "--out", a, "--reuse", b])
    out = tmp_path / "l3.json"
    rc, res = sh([snap, "diff", "--before", b, "--after", a, "--agent", "gis-cadastral", *common, "--out", out])
    assert res["l3"] == "fail" and res["violations"] == 1
    assert json.loads(out.read_text())["violations"][0]["path"] == "submittals/QRWN/T0001/A-001.pdf"
    rc, res = sh([snap, "diff", "--before", b, "--after", a, "--agent", "nobody", *common])
    assert rc == 2


def test_snapshot_detects_same_size_same_mtime_edit(root, tmp_path):
    """P-S3-9: outside submittals/ a content change that keeps size and mtime is still detected."""
    snap = PLUGIN / "scripts/snapshot.py"
    f = root / "rulebook/rules_v0.json"
    b, a = tmp_path / "b.json", tmp_path / "a.json"
    sh([snap, "take", "--root", root, "--out", b])
    st = f.stat()
    data = f.read_bytes()
    f.write_bytes(data.replace(b"approved", b"APPROVED"))
    os.utime(f, ns=(st.st_atime_ns, st.st_mtime_ns))
    sh([snap, "take", "--root", root, "--out", a, "--reuse", b])
    common = ["--matrix", PLUGIN / "hooks/access_matrix.json", "--site", "QRWN", "--sub", "T0001", "--run", RUN]
    rc, res = sh([snap, "diff", "--before", b, "--after", a, "--agent", "gis-cadastral", *common])
    assert res["modified"] == 1 and res["l3"] == "fail"


# ------------------------------------------------------------------ batch ownership (P-S3-4)
def write_batch(root, name, outputs):
    d = root / "0.ai_context/QRWN/T0001" / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "batch_manifest.json").write_text(json.dumps({"files": [{"output": o} for o in outputs]}))
    return d


def test_check_batches(root, tmp_path):
    cb = PLUGIN / "scripts/check_batches.py"
    args = [cb, "--root", root, "--site", "QRWN", "--sub", "T0001"]
    d1 = write_batch(root, "b01", ["A-001.dwg.json"])
    (d1 / "A-001.dwg.json").write_text("{}")
    d2 = write_batch(root, "b02", ["0.ai_context/QRWN/T0001/b02/A-001.pdf.json"])
    (d2 / "A-001.pdf.json").write_text("{}")
    rc, res = sh(args)
    assert rc == 0 and res["result"] == "pass" and res["batches"] == 2
    (d1 / "stray.json").write_text("{}")                      # file written by nobody's manifest
    rc, res = sh(args)
    assert res["result"] == "fail" and res["violations"] == 1
    (d1 / "stray.json").unlink()
    write_batch(root, "b02", ["0.ai_context/QRWN/T0001/b01/A-001.dwg.json", "A-001.pdf.json"])  # cross-batch claim
    out = tmp_path / "cb.json"
    rc, res = sh(args + ["--out", out])
    assert res["result"] == "fail"
    assert any("another folder" in v["issue"] for v in json.loads(out.read_text())["violations"])
    (d2 / "batch_manifest.json").unlink()                     # missing manifest
    rc, res = sh(args)
    assert res["result"] == "fail"
    assert sh([cb, "--root", root, "--site", "QRWN", "--sub", "NOPE"])[0] == 2


# ------------------------------------------------------------------ guard (L2)
def guard(root, tool, tool_input, agent_type=None, run=True, mode="live", configured=True):
    active = root / "runs/_active_run.json"
    if run:
        active.write_text(json.dumps({"run_id": RUN, "site": "QRWN", "sub_id": "T0001", "mode": mode}))
    elif active.exists():
        active.unlink()
    env = {**os.environ, "CLAUDE_PLUGIN_ROOT": str(PLUGIN)}
    env.pop("LDIP_REVIEW_ROOT", None)
    env.pop("CLAUDE_PLUGIN_DATA", None)
    if configured:
        env["LDIP_REVIEW_ROOT"] = str(root)
    data = {"hook_event_name": "PreToolUse", "tool_name": tool, "tool_input": tool_input, "cwd": str(root)}
    if agent_type:
        data["agent_type"] = agent_type
    p = subprocess.run([PY, str(PLUGIN / "hooks/guard.py")], input=json.dumps(data),
                       capture_output=True, text=True, env=env)
    assert p.returncode == 0
    if not p.stdout.strip():
        return "allow"
    return "deny"


F = f"findings/{RUN}"
SK = f"{PLUGIN}/skills"
Q = "ldip-design-review:"  # plugin-qualified agent_type (A1-04, P-S3-12)
CASES = [
    # (id, agent_type, tool, input, run, mode, expected)
    ("main-norun-read-any", None, "Read", {"file_path": "rulebook/rules_v0.json"}, False, "live", "allow"),
    ("main-norun-answer-key", None, "Read", {"file_path": "golden_set/answer_key/key.json"}, False, "live", "deny"),
    ("main-run-reasoning", None, "Read", {"file_path": f"{F}/reasoning/gis.md"}, True, "live", "deny"),
    ("main-run-grep-findings", None, "Grep", {"pattern": "x", "path": "findings"}, True, "live", "deny"),
    ("main-run-grep-candidates", None, "Grep", {"pattern": "x", "path": f"{F}/candidates"}, True, "live", "allow"),
    ("main-run-write-runs", None, "Write", {"file_path": f"runs/{RUN}/format_checks.json"}, True, "live", "allow"),
    ("main-run-write-findings", None, "Write", {"file_path": f"{F}/verified.json"}, True, "live", "deny"),
    ("main-run-bash-script", None, "Bash", {"command": f'python3 "{PLUGIN}/scripts/snapshot.py" take --root . --out runs/{RUN}/s.json'}, True, "live", "allow"),
    ("main-run-bash-cat", None, "Bash", {"command": f"cat {F}/reasoning/gis.md"}, True, "live", "deny"),
    ("ver-read-candidate", Q + "verifier", "Read", {"file_path": f"{F}/candidates/gis.json"}, True, "live", "allow"),
    ("ver-read-reasoning", Q + "verifier", "Read", {"file_path": f"{F}/reasoning/gis.md"}, True, "live", "deny"),
    ("ver-read-dotdot", Q + "verifier", "Read", {"file_path": f"{F}/candidates/../reasoning/gis.md"}, True, "live", "deny"),
    ("ver-grep-run-folder", Q + "verifier", "Grep", {"pattern": "x", "path": F}, True, "live", "deny"),
    ("ver-grep-candidates", Q + "verifier", "Grep", {"pattern": "x", "path": f"{F}/candidates"}, True, "live", "allow"),
    ("ver-glob-cwd", Q + "verifier", "Glob", {"pattern": "**/*.md"}, True, "live", "deny"),
    ("ver-bash-cat", Q + "verifier", "Bash", {"command": f"cat {F}/reasoning/gis.md"}, True, "live", "deny"),
    ("ver-bash-script-reasoning-arg", Q + "verifier", "Bash", {"command": f'python "{SK}/verifying-review-findings/scripts/recheck.py" --in {F}/reasoning/gis.md'}, True, "live", "deny"),
    ("ver-bash-script-ok", Q + "verifier", "Bash", {"command": f'python "${{CLAUDE_PLUGIN_ROOT}}/skills/verifying-review-findings/scripts/recheck.py" --in {F}/candidates/gis.json --out {F}/verified.json'}, True, "live", "allow"),
    ("ver-bash-other-skill", Q + "verifier", "Bash", {"command": f'python "{SK}/checking-cadastral-geometry/scripts/read_dlus.py"'}, True, "live", "deny"),
    ("ver-bash-pipe", Q + "verifier", "Bash", {"command": f'python "{SK}/verifying-review-findings/scripts/recheck.py" | tee x'}, True, "live", "deny"),
    ("ver-write-verified", Q + "verifier", "Write", {"file_path": f"{F}/verified.json"}, True, "live", "allow"),
    ("ver-write-verified-tmp", Q + "verifier", "Write", {"file_path": f"{F}/verified.json.tmp"}, True, "live", "allow"),
    ("ver-write-candidate", Q + "verifier", "Write", {"file_path": f"{F}/candidates/gis.json"}, True, "live", "deny"),
    ("ver-read-plugin-file", Q + "verifier", "Read", {"file_path": f"{PLUGIN}/hooks/guard.py"}, True, "live", "allow"),
    ("ver-read-outside", Q + "verifier", "Read", {"file_path": "/etc/passwd"}, True, "live", "deny"),
    ("gis-write-own", Q + "gis-cadastral", "Write", {"file_path": f"{F}/candidates/gis.json"}, True, "live", "allow"),
    ("gis-read-intake", Q + "gis-cadastral", "Read", {"file_path": f"{F}/candidates/intake.json"}, True, "live", "deny"),
    ("gis-read-schedule", Q + "gis-cadastral", "Read", {"file_path": "submittals/QRWN/masterplan-schedule/x.xlsx"}, True, "live", "allow"),
    ("gis-read-other-sub", Q + "gis-cadastral", "Read", {"file_path": "submittals/QRWN/T0000/old.pdf"}, True, "live", "deny"),
    ("gis-read-reference", Q + "gis-cadastral", "Read", {"file_path": "submittals/QRWN/_reference/DLUS.csv"}, True, "live", "allow"),
    ("gis-read-own-sub", Q + "gis-cadastral", "Read", {"file_path": "submittals/QRWN/T0001/A-001.pdf"}, True, "live", "allow"),
    # P-S3-2 / P-S3-7: never-writable paths, for every caller
    ("main-norun-write-submittal", None, "Write", {"file_path": "submittals/QRWN/T0001/A-001.pdf"}, False, "live", "deny"),
    ("main-norun-edit-submittal-tmp", None, "Edit", {"file_path": "submittals/QRWN/T0001/x.json.tmp"}, False, "live", "deny"),
    ("main-run-write-runlog", None, "Write", {"file_path": f"runs/{RUN}/run.jsonl"}, True, "live", "deny"),
    ("main-run-write-manifest", None, "Edit", {"file_path": f"runs/{RUN}/manifest.json"}, True, "live", "deny"),
    ("main-run-write-marker", None, "Write", {"file_path": "runs/_active_run.json"}, True, "live", "deny"),
    ("main-norun-write-marker", None, "Write", {"file_path": "runs/_active_run.json"}, False, "live", "deny"),
    ("main-run-bash-check-batches", None, "Bash", {"command": f'python3 "{PLUGIN}/scripts/check_batches.py" --root . --site QRWN --sub T0001'}, True, "live", "allow"),
    ("gis-read-other-site", Q + "gis-cadastral", "Read", {"file_path": "submittals/NMAR/x.xlsx"}, True, "live", "deny"),
    ("gis-golden-live", Q + "gis-cadastral", "Read", {"file_path": "golden_set/inputs/a.dwg"}, True, "live", "deny"),
    ("gis-golden-test", Q + "gis-cadastral", "Read", {"file_path": "golden_set/inputs/a.dwg"}, True, "test", "allow"),
    ("gis-answer-key-test", Q + "gis-cadastral", "Read", {"file_path": "golden_set/answer_key/key.json"}, True, "test", "deny"),
    ("batch-write-batch", Q + "intake-batch", "Write", {"file_path": "0.ai_context/QRWN/T0001/b01/A-001.dwg.json"}, True, "live", "allow"),
    ("batch-write-package", Q + "intake-batch", "Write", {"file_path": "0.ai_context/QRWN/T0001/package.json"}, True, "live", "deny"),
    ("batch-write-submittal", Q + "intake-batch", "Edit", {"file_path": "submittals/QRWN/T0001/A-001.pdf"}, True, "live", "deny"),
    ("pkg-read-prev-package", Q + "intake-package", "Read", {"file_path": "0.ai_context/QRWN/T0000/package.json"}, True, "live", "allow"),
    ("out-write-register", Q + "outputs-register", "Write", {"file_path": "outputs/register.tmp.xlsx"}, True, "live", "allow"),
    ("out-read-runlog", Q + "outputs-register", "Read", {"file_path": f"runs/{RUN}/run.jsonl"}, True, "live", "deny"),
    ("unknown-agent", Q + "some-other-agent", "Read", {"file_path": "rulebook/rules_v0.json"}, True, "live", "deny"),
    ("subagent-no-run", Q + "gis-cadastral", "Read", {"file_path": "rulebook/rules_v0.json"}, False, "live", "deny"),
    ("locator-no-run-read", Q + "source-locator", "Read", {"file_path": "/some/TAWAZUN PROGRAM/x.pdf"}, False, "live", "allow"),
    ("locator-write-index", Q + "source-locator", "Write", {"file_path": "_index/catalog_v1.json"}, False, "live", "allow"),
    ("locator-write-rulebook", Q + "source-locator", "Write", {"file_path": "rulebook/rules_v1.json"}, False, "live", "deny"),
    ("locator-answer-key", Q + "source-locator", "Read", {"file_path": "golden_set/answer_key/key.json"}, False, "live", "deny"),
    # P-S3-12 (a): bare names are no longer accepted, even for real agents
    ("bare-verifier-denied", "verifier", "Read", {"file_path": f"{F}/candidates/gis.json"}, True, "live", "deny"),
    ("bare-gis-denied", "gis-cadastral", "Write", {"file_path": f"{F}/candidates/gis.json"}, True, "live", "deny"),
    ("bare-locator-denied", "source-locator", "Read", {"file_path": "_index/catalog_v1.json"}, False, "live", "deny"),
    ("other-plugin-prefix-denied", "ldip-a1-probe:verifier", "Read", {"file_path": f"{F}/candidates/gis.json"}, True, "live", "deny"),
]


@pytest.mark.parametrize("cid,atype,tool,ti,run,mode,expected", CASES, ids=[c[0] for c in CASES])
def test_guard(root, cid, atype, tool, ti, run, mode, expected):
    assert guard(root, tool, ti, atype, run, mode) == expected


def guard_raw(root, data_extra, run=True):
    """Send hook input with full control over the agent_type key (present / absent / odd values)."""
    active = root / "runs/_active_run.json"
    if run is True:
        active.write_text(json.dumps({"run_id": RUN, "site": "QRWN", "sub_id": "T0001", "mode": "live"}))
    elif run is False and active.exists():
        active.unlink()
    env = {**os.environ, "CLAUDE_PLUGIN_ROOT": str(PLUGIN), "LDIP_REVIEW_ROOT": str(root)}
    env.pop("CLAUDE_PLUGIN_DATA", None)
    data = {"hook_event_name": "PreToolUse", "cwd": str(root), **data_extra}
    p = subprocess.run([PY, str(PLUGIN / "hooks/guard.py")], input=json.dumps(data), capture_output=True, text=True, env=env)
    assert p.returncode == 0
    return "allow" if not p.stdout.strip() else "deny"


# P-S3-12 (b): an absent agent_type key is the main session (Orchestrator rules apply)
ABSENT = [
    ("absent-norun-read", {"tool_name": "Read", "tool_input": {"file_path": "rulebook/rules_v0.json"}}, False, "allow"),
    ("absent-run-write-runs", {"tool_name": "Write", "tool_input": {"file_path": f"runs/{RUN}/format_checks.json"}}, True, "allow"),
    ("absent-run-reasoning", {"tool_name": "Read", "tool_input": {"file_path": f"{F}/reasoning/gis.md"}}, True, "deny"),
    ("absent-run-write-candidate", {"tool_name": "Write", "tool_input": {"file_path": f"{F}/candidates/gis.json"}}, True, "deny"),
    ("null-agent-type-is-main", {"agent_type": None, "tool_name": "Write", "tool_input": {"file_path": f"runs/{RUN}/x.json"}}, True, "allow"),
    ("empty-agent-type-denied", {"agent_type": "", "tool_name": "Read", "tool_input": {"file_path": "rulebook/rules_v0.json"}}, False, "deny"),
    ("nonstring-agent-type-denied", {"agent_type": 7, "tool_name": "Read", "tool_input": {"file_path": "rulebook/rules_v0.json"}}, False, "deny"),
    ("qualified-orchestrator-denied", {"agent_type": "ldip-design-review:orchestrator", "tool_name": "Read", "tool_input": {"file_path": "rulebook/rules_v0.json"}}, False, "deny"),
]


@pytest.mark.parametrize("cid,extra,run,expected", ABSENT, ids=[c[0] for c in ABSENT])
def test_guard_agent_type_key(root, cid, extra, run, expected):
    assert guard_raw(root, extra, run) == expected


def test_matrix_keys_are_qualified_only():
    m = json.loads((PLUGIN / "hooks/access_matrix.json").read_text())
    assert m["agent_type_form"] == "qualified"
    assert m["agent_types"] and all(k.startswith("ldip-design-review:") for k in m["agent_types"])
    assert "ldip-design-review:orchestrator" not in m["agent_types"]


def test_guard_unconfigured_fail_closed(root):
    assert guard(root, "Read", {"file_path": "x"}, Q + "verifier", configured=False) == "deny"


def test_guard_bad_input_fail_closed(root):
    env = {**os.environ, "CLAUDE_PLUGIN_ROOT": str(PLUGIN), "LDIP_REVIEW_ROOT": str(root)}
    p = subprocess.run([PY, str(PLUGIN / "hooks/guard.py")], input="{not json", capture_output=True, text=True, env=env)
    assert p.returncode == 0 and '"deny"' in p.stdout


def test_access_matrix_is_generated_copy(tmp_path):
    out = tmp_path / "m.json"
    rc, _ = sh([PLUGIN / "scripts/build_access_matrix.py", "--source", REPO / "specs-machine/access_matrix.source.json", "--out", out])
    assert rc == 0
    assert json.loads(out.read_text()) == json.loads((PLUGIN / "hooks/access_matrix.json").read_text())
