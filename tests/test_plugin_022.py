"""Tests for plugin 0.2.2 (session 6 fixes): D5 guard Glob base, D6 guard-root lookup for scripts,
D7/D10 return-summary parsing, D8 write_manifest.py, D9 convert.py --reuse.
Synthetic data only (project rule 5): a fake DWG signature, a DXF drawn with ezdxf and a stand-in ODA
converter. No Parsons file, DLUS or Golden Set item is used."""
import hashlib
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
PLUGIN = REPO / "plugin" / "ldip-design-review"
SCRIPTS = PLUGIN / "scripts"
SK1 = PLUGIN / "skills" / "extracting-dwg-data" / "scripts"
GUARD = PLUGIN / "hooks" / "guard.py"
RUN = "QRWN-T0001-20261001T1200"

sys.path.insert(0, str(SCRIPTS))
import ldip_common  # noqa: E402


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def clean_env(**extra):
    env = {k: v for k, v in os.environ.items()
           if k not in ("LDIP_REVIEW_ROOT", "CLAUDE_PLUGIN_DATA", "CLAUDE_CONFIG_DIR", "CLAUDE_PLUGIN_ROOT")}
    env.update({k: str(v) for k, v in extra.items()})
    return env


def run(args, cwd, env, timeout=120):
    p = subprocess.run([sys.executable, *map(str, args)], cwd=cwd, env=env, capture_output=True, text=True,
                       timeout=timeout)
    out = p.stdout.strip().splitlines()
    return p.returncode, (json.loads(out[-1]) if out else {}), p.stderr


@pytest.fixture
def root(tmp_path):
    r = tmp_path / "LDIP & Review"            # R4: spaces and '&' in the root
    for d in ("rulebook", "runs", "submittals/QRWN/T0001", "0.ai_context/QRWN/T0001/b01",
              "golden_set/answer_key", "findings/" + RUN + "/candidates", "findings/" + RUN + "/reasoning"):
        (r / d).mkdir(parents=True)
    (r / "rulebook" / "rules_v0.json").write_text(json.dumps({"status": "approved", "rulebook_version": "v0"}))
    (r / "runs" / "README.md").write_text("runs\n")
    return r


def write_cfg(cfg_dir, name, root_path):
    d = cfg_dir / "plugins" / "data" / name
    d.mkdir(parents=True)
    (d / "guard_config.json").write_text(json.dumps({"ldip_review_root": str(root_path)}))
    return d


# ------------------------------------------------------------------ D6
def test_d6_root_found_in_standard_data_dir(tmp_path, root, monkeypatch):
    for k in ("LDIP_REVIEW_ROOT", "CLAUDE_PLUGIN_DATA"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "cfg"))
    write_cfg(tmp_path / "cfg", "ldip-design-review-synced", root)
    assert ldip_common.configured_review_root() == ldip_common.to_posix_abs(root, "/")


def test_d6_conflicting_data_dirs_fail_closed(tmp_path, root, monkeypatch):
    for k in ("LDIP_REVIEW_ROOT", "CLAUDE_PLUGIN_DATA"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "cfg"))
    write_cfg(tmp_path / "cfg", "ldip-design-review-synced", root)
    write_cfg(tmp_path / "cfg", "ldip-design-review-local", tmp_path / "other")
    assert ldip_common.configured_review_root() is None


def test_d6_plugin_data_env_wins_and_env_root_wins(tmp_path, root, monkeypatch):
    monkeypatch.delenv("LDIP_REVIEW_ROOT", raising=False)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "cfg"))
    mine = write_cfg(tmp_path / "cfg", "ldip-design-review-synced", root)
    write_cfg(tmp_path / "cfg", "ldip-design-review-local", tmp_path / "other")
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(mine))
    assert ldip_common.configured_review_root() == ldip_common.to_posix_abs(root, "/")
    monkeypatch.setenv("LDIP_REVIEW_ROOT", str(tmp_path / "x"))
    assert ldip_common.configured_review_root() == ldip_common.to_posix_abs(tmp_path / "x", "/")


def test_d6_open_works_without_plugin_data_env(tmp_path, root):
    """Session 6: open refused with exit 5 although guard_config.json was in the data folder."""
    (root / "submittals/QRWN/T0001/a.dwg").write_bytes(b"AC1032" + b"\0" * 64)
    cfg = tmp_path / "cfg"
    rc, out, err = run([SCRIPTS / "run_manifest.py", "open", "--root", ".", "--site", "QRWN", "--sub", "T0001",
                        "--mode", "test", "--rulebook", "rulebook/rules_v0.json", "--now", "20261001T1200"],
                       root, clean_env(CLAUDE_CONFIG_DIR=cfg))
    assert rc == 5 and "guard root not configured" in out["detail"]       # still refuses with no config
    write_cfg(cfg, "ldip-design-review-synced", root)
    rc, out, err = run([SCRIPTS / "run_manifest.py", "open", "--root", ".", "--site", "QRWN", "--sub", "T0001",
                        "--mode", "test", "--rulebook", "rulebook/rules_v0.json", "--now", "20261001T1200"],
                       root, clean_env(CLAUDE_CONFIG_DIR=cfg))
    assert rc == 0 and out["run_id"] == RUN, (out, err)
    assert (root / "runs/_active_run.json").is_file()


# ------------------------------------------------------------------ D5 (guard Glob base)
def guard(root, tool, tool_input, agent=None, cwd=None):
    data = {"tool_name": tool, "tool_input": tool_input, "cwd": str(cwd or root)}
    if agent:
        data["agent_type"] = f"ldip-design-review:{agent}"
    p = subprocess.run([sys.executable, str(GUARD)], input=json.dumps(data), capture_output=True, text=True,
                       env=clean_env(LDIP_REVIEW_ROOT=root, CLAUDE_PLUGIN_ROOT=PLUGIN), timeout=30)
    assert p.returncode == 0, p.stderr
    if not p.stdout.strip():
        return "allow", ""
    o = json.loads(p.stdout)["hookSpecificOutput"]
    return o["permissionDecision"], o["permissionDecisionReason"]


def activate(root):
    (root / "runs/_active_run.json").write_text(json.dumps({"run_id": RUN, "site": "QRWN", "sub_id": "T0001",
                                                             "mode": "test"}))


@pytest.mark.parametrize("pattern,path,want", [
    ("*", "rulebook", "allow"),                       # command precondition 2 (0.2.2 wording)
    ("rulebook/*", None, "allow"),                    # literal folder narrows the base (D5)
    ("{rulebook/*,runs/*}", None, "deny"),            # brace at start: whole root -> answer_key
    ("**/*.json", None, "deny"),
    ("golden_set/answer_key/*", None, "deny"),
    ("golden_set/*", None, "deny"),
])
def test_d5_main_session_glob(root, pattern, path, want):
    ti = {"pattern": pattern}
    if path:
        ti["path"] = str(root / path)
    decision, reason = guard(root, "Glob", ti)
    assert decision == want, reason


@pytest.mark.parametrize("pattern,want", [
    (f"findings/{RUN}/candidates/*.json", "allow"),   # the verifier's plan step in session 6
    (f"findings/{RUN}/reasoning/*.md", "deny"),       # isolation still holds
    ("../*/candidates/*.json", "deny"),               # '..' keeps the root as base
    ("*.json", "deny"),
])
def test_d5_verifier_glob_during_run(root, pattern, want):
    activate(root)
    decision, reason = guard(root, "Glob", {"pattern": pattern}, agent="verifier")
    assert decision == want, reason


def test_i7c_agent_denied_outside_run(root):
    decision, reason = guard(root, "Read", {"file_path": str(root / "rulebook/rules_v0.json")}, agent="verifier")
    assert decision == "deny" and "active review run" in reason


def test_d8_d9_intake_commands_pass_guard(root):
    activate(root)
    sk = "${CLAUDE_PLUGIN_ROOT}/skills/extracting-dwg-data/scripts"
    f = "submittals/QRWN/T0001/a.dwg"
    (root / f).write_bytes(b"AC1032")
    for cmd in (f'python "{sk}/convert.py" --engine ezdwg --dwg "{f}" --out "0.ai_context/QRWN/T0001/b01/a.dxf" '
                f'--log "0.ai_context/QRWN/T0001/b01/a.convert.json" --reuse',
                f'python "{sk}/write_manifest.py" --batch-dir "0.ai_context/QRWN/T0001/b01" --run {RUN} '
                f'--site QRWN --sub T0001 --file "{f}" {"a" * 64}'):
        decision, reason = guard(root, "Bash", {"command": cmd}, agent="intake-batch")
        assert decision == "allow", reason
    decision, _ = guard(root, "Bash", {"command": f'python "{sk}/write_manifest.py" --batch-dir "0.ai_context/QRWN/T0001/b01" '
                                                   f'--run {RUN} --site QRWN --sub T0001 --file "{f}" x; ls'},
                        agent="intake-batch")
    assert decision == "deny"


# ------------------------------------------------------------------ D7 / D10
@pytest.mark.parametrize("text,ok,fenced", [
    ('{"a": 1}', True, False),
    ('  {"a": 1}\n', True, False),
    ('```json\n{"a": 1}\n```', True, True),
    ('```\n{"a": 1}\n```\n', True, True),
    ('```json\n{"a": 1}\n```\n\nWarnings: none', False, None),
    ('Result:\n```json\n{"a": 1}\n```', False, None),
    ('```json\n{"a": 1}\n```\n```json\n{"b": 2}\n```', False, None),
    ('{"a": 1} trailing', False, None),
])
def test_d7_parse_summary(text, ok, fenced):
    if ok:
        assert ldip_common.parse_summary(text) == ({"a": 1}, fenced)
    else:
        with pytest.raises(json.JSONDecodeError):
            ldip_common.parse_summary(text)


def test_d10_rule_skipped_from_fenced_summary(root):
    (root / "submittals/QRWN/T0001/a.dwg").write_bytes(b"AC1032")
    env = clean_env(LDIP_REVIEW_ROOT=root)
    rc, out, err = run([SCRIPTS / "run_manifest.py", "open", "--root", ".", "--site", "QRWN", "--sub", "T0001",
                        "--mode", "test", "--rulebook", "rulebook/rules_v0.json", "--now", "20261001T1200"], root, env)
    assert rc == 0, (out, err)
    ret = root / "runs" / RUN / "returns"
    ret.mkdir()
    (ret / "2_1.json").write_text('```json\n{"rules_skipped": [{"rule_id": "GIS-004", "reason": "inactive; GAP"}]}\n```')
    rc, out, err = run([SCRIPTS / "run_manifest.py", "event", "--root", ".", "--run", RUN, "--agent", "gis-cadastral",
                        "--event", "rule_skipped", "--instance", "1", "--from-summary", f"runs/{RUN}/returns/2_1.json"],
                       root, env)
    assert rc == 0 and out["logged"] == 1, (out, err)
    (ret / "2_1.json").write_text('```json\n{"rules_skipped": []}\n```\nextra text')
    rc, out, err = run([SCRIPTS / "run_manifest.py", "event", "--root", ".", "--run", RUN, "--agent", "gis-cadastral",
                        "--event", "rule_skipped", "--instance", "1", "--from-summary", f"runs/{RUN}/returns/2_1.json"],
                       root, env)
    assert rc == 2 and "invalid JSON" in out["detail"]


# ------------------------------------------------------------------ D8 / D9 (stand-in ODA converter)
STANDIN = """#!{py}
import os, shutil, sys
indir, outdir, name = sys.argv[1], sys.argv[2], sys.argv[-1]
with open(os.environ["STANDIN_CALLS"], "a") as fh:
    fh.write(name + "\\n")
stem = os.path.splitext(name)[0]
shutil.copy(os.environ["STANDIN_DXF"], os.path.join(outdir, stem + ".dxf"))
"""


@pytest.fixture
def batch(tmp_path, root):
    ezdxf = pytest.importorskip("ezdxf")
    doc = ezdxf.new("R2018")
    doc.header["$INSUNITS"] = 6
    msp = doc.modelspace()
    msp.add_lwpolyline([(657100, 2747500), (657130, 2747500), (657130, 2747520), (657100, 2747520)],
                       close=True, dxfattribs={"layer": "S1"})
    msp.add_text("101", dxfattribs={"layer": "T1", "insert": (657110, 2747510)})
    dxf = tmp_path / "standin.dxf"
    doc.saveas(dxf)
    exe = tmp_path / "ODAFileConverter"
    exe.write_text(STANDIN.format(py=sys.executable))
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    calls = tmp_path / "calls.txt"
    calls.write_text("")
    sub = root / "submittals/QRWN/T0001"
    files = {}
    for name, data in (("A-01.dwg", b"AC1032" + b"\1" * 200), ("B-02.dwg", b"AC1032" + b"\2" * 200),
                       ("C-03.pdf", b"%PDF-1.7\n"), ("D-04.txt", b"note\n")):
        (sub / name).write_bytes(data)
        files[name] = (f"submittals/QRWN/T0001/{name}", sha(sub / name))
    env = clean_env(LDIP_ODA_EXE=exe, STANDIN_DXF=dxf, STANDIN_CALLS=calls)
    return {"root": root, "env": env, "files": files, "calls": calls, "exe": exe, "dxf": dxf}


def convert_extract(b, name, reuse=False):
    rel, _ = b["files"][name]
    stem = Path(name).stem
    B = "0.ai_context/QRWN/T0001/b01"
    args = [SK1 / "convert.py", "--engine", "oda", "--dwg", rel, "--out", f"{B}/{stem}.dxf",
            "--log", f"{B}/{stem}.convert.json"] + (["--reuse"] if reuse else [])
    rc, out, err = run(args, b["root"], b["env"])
    assert rc == 0, (out, err)
    rc2, out2, err2 = run([SK1 / "extract_dxf.py", "--dxf", f"{B}/{stem}.dxf", "--dwg", rel, "--convert-log",
                           f"{B}/{stem}.convert.json", "--out", f"{B}/{stem}.dwg.json", "--source-path", rel,
                           "--resolve-base", "submittals/QRWN/T0001"], b["root"], b["env"])
    assert rc2 == 0, (out2, err2)
    return out


def write_manifest(b, names, extra=()):
    args = [SK1 / "write_manifest.py", "--batch-dir", "0.ai_context/QRWN/T0001/b01", "--run", RUN,
            "--site", "QRWN", "--sub", "T0001", "--root", "."]
    for n in names:
        args += ["--file", *b["files"][n]]
    return run(args + list(extra), b["root"], b["env"])


def test_d9_convert_reuse(batch):
    out1 = convert_extract(batch, "A-01.dwg")
    assert "reused" not in out1
    out2 = convert_extract(batch, "A-01.dwg", reuse=True)
    assert out2.get("reused") is True
    assert batch["calls"].read_text().split() == ["A-01.dwg"]          # converter ran once
    dxf = batch["root"] / "0.ai_context/QRWN/T0001/b01/A-01.dxf"
    dxf.write_text(dxf.read_text() + "\n")                              # DXF changed -> convert again
    out3 = convert_extract(batch, "A-01.dwg", reuse=True)
    assert "reused" not in out3 and len(batch["calls"].read_text().split()) == 2


def test_d8_manifest_from_disk_and_checkers(batch):
    convert_extract(batch, "A-01.dwg")
    B = batch["root"] / "0.ai_context/QRWN/T0001/b01"
    # B-02: converter failed (log written by convert.py with status error)
    env = dict(batch["env"], LDIP_ODA_EXE=str(batch["exe"].parent / "missing-converter"))
    rc, out, _ = run([SK1 / "convert.py", "--engine", "oda", "--dwg", batch["files"]["B-02.dwg"][0],
                      "--out", "0.ai_context/QRWN/T0001/b01/B-02.dxf", "--log", "0.ai_context/QRWN/T0001/b01/B-02.convert.json"],
                     batch["root"], env)
    assert rc == 3
    rc, out, err = write_manifest(batch, ["A-01.dwg", "B-02.dwg", "C-03.pdf", "D-04.txt"])
    assert rc == 0, (out, err)
    m = json.loads((B / "batch_manifest.json").read_text())
    by = {Path(f["path"]).name: f for f in m["files"]}
    assert by["A-01.dwg"]["status"] == "extracted" and by["A-01.dwg"]["output"] == "A-01.dwg.json"
    assert sorted(by["A-01.dwg"]["intermediates"]) == ["A-01.convert.json", "A-01.dxf"]
    assert by["B-02.dwg"]["status"] == "failed" and by["B-02.dwg"]["error"]["code"] == 3
    # skill 2 (session 8): a PDF with no ingestion file is failed, code 2 (extract_pdf.py not run)
    assert by["C-03.pdf"]["status"] == "failed" and by["C-03.pdf"]["error"]["code"] == 2
    assert by["D-04.txt"]["type"] == "other" and by["D-04.txt"]["status"] == "skipped"
    assert m["converter"] == {"name": "oda", "version": "27.9.0"}  # Amendment O-a documented ODA version
    assert m["counts"] == {"extracted": 1, "failed": 2, "skipped": 1}
    # C3 batch ownership passes on the folder the script described
    sys.path.insert(0, str(SCRIPTS))
    import check_batches
    res = check_batches.check(batch["root"], "QRWN", "T0001") if hasattr(check_batches, "check") else None
    if res is not None:
        assert res["violations"] == [], res


def test_d8_hash_mismatch_and_bad_ingestion_are_failed(batch):
    convert_extract(batch, "A-01.dwg")
    B = batch["root"] / "0.ai_context/QRWN/T0001/b01"
    rel, _ = batch["files"]["A-01.dwg"]
    rc, out, _ = run([SK1 / "write_manifest.py", "--batch-dir", "0.ai_context/QRWN/T0001/b01", "--run", RUN,
                      "--site", "QRWN", "--sub", "T0001", "--file", rel, "0" * 64], batch["root"], batch["env"])
    assert rc == 0
    f = json.loads((B / "batch_manifest.json").read_text())["files"][0]
    assert f["status"] == "failed" and "hash differs" in f["error"]["detail"]
    ing = json.loads((B / "A-01.dwg.json").read_text())
    ing["unexpected_key"] = 1
    (B / "A-01.dwg.json").write_text(json.dumps(ing))
    rc, out, _ = write_manifest(batch, ["A-01.dwg"])
    f = json.loads((B / "batch_manifest.json").read_text())["files"][0]
    assert f["status"] == "failed" and f["error"]["code"] == 4 and "A-01.dwg.json" in f["intermediates"]


def test_d8_input_errors(batch):
    rc, out, _ = run([SK1 / "write_manifest.py", "--batch-dir", "0.ai_context/QRWN/T0001/b01", "--run", RUN,
                      "--site", "QRWN", "--sub", "T0001", "--file", "submittals/QRWN/T0001/A-01.dwg", "nothex"],
                     batch["root"], batch["env"])
    assert rc == 2 and "SHA-256" in out["detail"]
    rc, out, _ = run([SK1 / "write_manifest.py", "--batch-dir", "0.ai_context/QRWN/T0001/x1", "--run", RUN,
                      "--site", "QRWN", "--sub", "T0001", "--file", "submittals/QRWN/T0001/A-01.dwg", "a" * 64],
                     batch["root"], batch["env"])
    assert rc == 2
    rc, out, _ = run([SK1 / "write_manifest.py", "--batch-dir", "0.ai_context/QRWN/T0001/b01", "--run", "bad run",
                      "--site", "QRWN", "--sub", "T0001", "--file", "submittals/QRWN/T0001/D-04.txt", "a" * 64],
                     batch["root"], batch["env"])
    assert rc == 4 and not (batch["root"] / "0.ai_context/QRWN/T0001/b01/batch_manifest.json").exists()


def test_d8_format_check_intake_batch_passes(batch):
    """End to end: open a run, convert/extract/manifest via scripts, raw JSON summary → format_check pass."""
    env = dict(batch["env"], LDIP_REVIEW_ROOT=str(batch["root"]))
    for n in ("B-02.dwg", "C-03.pdf", "D-04.txt"):
        (batch["root"] / batch["files"][n][0]).unlink()
    rc, out, err = run([SCRIPTS / "run_manifest.py", "open", "--root", ".", "--site", "QRWN", "--sub", "T0001",
                        "--mode", "test", "--rulebook", "rulebook/rules_v0.json", "--now", "20261001T1200"],
                       batch["root"], env)
    assert rc == 0, (out, err)
    convert_extract(batch, "A-01.dwg")
    rc, out, err = write_manifest(batch, ["A-01.dwg"])
    assert rc == 0, (out, err)
    ret = batch["root"] / "runs" / RUN / "returns"
    ret.mkdir()
    summary = {"agent": "intake-batch", "instance": "01", "run_id": RUN, "status": "completed",
               "files_written": [f"0.ai_context/QRWN/T0001/b01/{n}" for n in
                                 ("batch_manifest.json", "A-01.dxf", "A-01.convert.json", "A-01.dwg.json")],
               "counts": {"extracted": 1, "failed": 0, "skipped": 0},
               "rules_skipped": [], "gaps": [], "inconsistencies": [], "errors": []}
    for fenced in (False, True):
        text = json.dumps(summary)
        (ret / "1a_1.json").write_text(f"```json\n{text}\n```\n" if fenced else text)
        rc, out, err = run([SCRIPTS / "format_check.py", "intake-batch", RUN, "--root", ".", "--summary",
                            f"runs/{RUN}/returns/1a_1.json", "--instance", "1", "--attempt", str(1 + fenced)],
                           batch["root"], env)
        assert out.get("result") == "pass", (out, err)
