"""External submittal source (SPC-0005 Rev F §5.0.5, §6.2.4, §7.2, §7.6; D39-1 … D39-4, P39-1 … P39-8).
All fixtures are synthetic, in temp folders whose paths contain '&' and spaces (SPC-0002 R4)."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
PLUGIN = REPO / "plugin" / "ldip-design-review"
PY = sys.executable
SITE, SUB = "NMAR", "11-2026"
RUN = f"{SITE}-{SUB}-20261113T1200"
Q = "ldip-design-review:"
LOGICAL = f"submittals/{SITE}/{SUB}"

sys.path.insert(0, str(PLUGIN / "scripts"))
import ldip_common  # noqa: E402


def sh(args, env):
    p = subprocess.run([PY, *map(str, args)], capture_output=True, text=True, env=env)
    out = p.stdout.strip().splitlines()
    return p.returncode, (json.loads(out[-1]) if out else {})


@pytest.fixture
def world(tmp_path):
    base = tmp_path / "Docs & More"
    root = base / "LDIP_Review"
    for d in ["submittals/NMAR/10-2026", "rulebook", "runs", f"0.ai_context/{SITE}/{SUB}/b01",
              f"findings/{RUN}/candidates", f"findings/{RUN}/reasoning", "golden_set/answer_key"]:
        (root / d).mkdir(parents=True, exist_ok=True)
    (root / "rulebook/rules_v9.json").write_text(json.dumps({"status": "approved", "rulebook_version": "v9"}))
    (root / f"findings/{RUN}/reasoning/gis.md").write_text("notes")
    sroot = base / "03_Parsons" / "Submission"
    src = sroot / "Namar" / "01 Submission Folder 60%"
    (src / "Vol. 01_Drawings").mkdir(parents=True)
    (src / "Vol. 01_Drawings" / "A-001.dwg").write_bytes(b"dwg-bytes")
    (src / "Vol. 01_Drawings" / "A-001.pdf").write_bytes(b"pdf-bytes")
    (src / "Vol. 04_Models").mkdir()
    (src / "Vol. 04_Models" / "R-100.xml").write_bytes(b"<LandXML/>")
    other = sroot / "Narjis" / "01 Submission"
    other.mkdir(parents=True)
    (other / "B-001.dwg").write_bytes(b"other")
    outside = base / "Elsewhere"
    outside.mkdir()
    (outside / "secret.pdf").write_bytes(b"x")
    data = base / "plugin-data"
    data.mkdir()
    (data / "guard_config.json").write_text(json.dumps(
        {"ldip_review_root": str(root), "submittal_source_roots": [str(sroot)]}))
    return {"root": root, "src": src, "sroot": sroot, "other": other, "outside": outside, "data": data}


def env_of(w, **extra):
    env = {**os.environ, "CLAUDE_PLUGIN_ROOT": str(PLUGIN), "CLAUDE_PLUGIN_DATA": str(w["data"])}
    env.pop("LDIP_REVIEW_ROOT", None)
    env["CLAUDE_CONFIG_DIR"] = str(w["data"] / "no-config")   # no stray data folders
    env.update(extra)
    return env


def open_src(w, source=None, **kw):
    args = [PLUGIN / "scripts/run_manifest.py", "open", "--root", w["root"], "--site", SITE, "--sub", SUB,
            "--mode", "live", "--rulebook", "rulebook/rules_v9.json", "--now", "20261113T1200"]
    if source is not False:
        args += ["--source", source or w["src"]]
    return sh(args, env_of(w, **kw))


def close(w, status="completed"):
    return sh([PLUGIN / "scripts/run_manifest.py", "close", "--root", w["root"], "--run", RUN, "--status", status],
              env_of(w))


def guard(w, tool, tool_input, agent_type=None):
    data = {"hook_event_name": "PreToolUse", "tool_name": tool, "tool_input": tool_input, "cwd": str(w["root"])}
    if agent_type:
        data["agent_type"] = agent_type
    p = subprocess.run([PY, str(PLUGIN / "hooks/guard.py")], input=json.dumps(data),
                       capture_output=True, text=True, env=env_of(w))
    assert p.returncode == 0
    return ("allow", "") if not p.stdout.strip() else (
        "deny", json.loads(p.stdout)["hookSpecificOutput"]["permissionDecisionReason"])


# ------------------------------------------------------------------ 1. open
def test_open_with_source(world):
    rc, res = open_src(world)
    assert rc == 0, res
    assert res["inputs"] == 3 and res["source"] == ldip_common.to_posix_abs(world["src"], "/")
    m = json.loads((world["root"] / f"runs/{RUN}/manifest.json").read_text())
    assert m["source"] == {"logical": LOGICAL, "path": ldip_common.to_posix_abs(world["src"], "/")}
    paths = [i["path"] for i in m["inputs"]]
    assert paths == sorted(paths) and f"{LOGICAL}/Vol. 01_Drawings/A-001.dwg" in paths
    assert all(i["path"].startswith(LOGICAL + "/") and len(i["sha256"]) == 64 and "mtime_ns" in i
               for i in m["inputs"])
    marker = json.loads((world["root"] / "runs/_active_run.json").read_text())
    assert marker["source"] == m["source"]
    assert not (world["root"] / LOGICAL).exists()          # nothing copied into LDIP_Review


def test_open_without_source_unchanged(world):
    # Rev E behaviour: no --source and no submittals/<SITE>/<sub>/ -> input error
    assert open_src(world, source=False)[0] == 2


# ------------------------------------------------------------------ 2. refusals
def test_open_refused_outside_roots(world):
    rc, res = open_src(world, source=world["outside"])
    assert rc == 5 and "not under a configured submittal source root" in res["detail"]


def test_open_refused_link_resolving_outside(world):
    link = world["sroot"] / "Namar" / "link-out"
    link.symlink_to(world["outside"], target_is_directory=True)
    assert open_src(world, source=link)[0] == 5                      # the folder itself resolves outside
    (world["src"] / "evil.pdf").symlink_to(world["outside"] / "secret.pdf")
    rc, res = open_src(world)
    assert rc == 5 and "resolves outside" in res["detail"]           # a link inside the source


def test_open_refused_when_logical_folder_exists(world):
    (world["root"] / LOGICAL).mkdir(parents=True)
    rc, res = open_src(world)
    assert rc == 5 and "one logical folder" in res["detail"]


def test_open_refused_without_roots(world):
    (world["data"] / "guard_config.json").write_text(json.dumps({"ldip_review_root": str(world["root"])}))
    rc, res = open_src(world)
    assert rc == 5 and "submittal_source_roots" in res["detail"]


def test_open_refused_root_overlap(world):
    (world["data"] / "guard_config.json").write_text(json.dumps(
        {"ldip_review_root": str(world["root"]), "submittal_source_roots": [str(world["root"].parent)]}))
    rc, res = open_src(world)
    assert rc == 5 and "overlaps the review root" in res["detail"]


# ------------------------------------------------------------------ 3. guard
@pytest.fixture
def running(world):
    assert open_src(world)[0] == 0
    return world


def test_guard_intake_reads_source(running):
    f = str(running["src"] / "Vol. 01_Drawings" / "A-001.dwg")
    assert guard(running, "Read", {"file_path": f}, Q + "intake-batch")[0] == "allow"
    assert guard(running, "Read", {"file_path": str(running["src"] / "Vol. 01_Drawings" / "A-001.pdf")},
                 Q + "verifier")[0] == "allow"
    assert guard(running, "Glob", {"pattern": "*.dwg", "path": str(running["src"] / "Vol. 01_Drawings")},
                 Q + "gis-cadastral")[0] == "allow"


def test_guard_write_to_source_denied(running):
    f = str(running["src"] / "Vol. 01_Drawings" / "new.dwg")
    for agent in (None, Q + "intake-batch"):
        d, why = guard(running, "Write", {"file_path": f, "content": "x"}, agent)
        assert d == "deny" and "submittals are read-only" in why


def test_guard_other_source_root_denied(running):
    d, why = guard(running, "Read", {"file_path": str(running["other"] / "B-001.dwg")}, Q + "intake-batch")
    assert d == "deny" and "not the source of the active run" in why


def test_guard_outside_still_denied(running):
    d, why = guard(running, "Read", {"file_path": str(running["outside"] / "secret.pdf")}, Q + "intake-batch")
    assert d == "deny" and "outside LDIP_Review" in why


def test_guard_bash_args_mapped(running):
    sk = PLUGIN / "skills/extracting-dwg-data/scripts"
    dwg = running["src"] / "Vol. 01_Drawings" / "A-001.dwg"
    b = f"0.ai_context/{SITE}/{SUB}/b01"
    cmd = (f'python "{sk}/extract_dxf.py" --dxf "{b}/A-001.dxf" --dwg "{dwg}" --convert-log "{b}/A-001.convert.json" '
           f'--out "{b}/A-001.dwg.json" --source-path "{LOGICAL}/Vol. 01_Drawings/A-001.dwg" '
           f'--resolve-base "{dwg.parent}"')
    assert guard(running, "Bash", {"command": cmd}, Q + "intake-batch") == ("allow", "")
    bad = cmd.replace(str(dwg.parent), str(running["other"]))
    assert guard(running, "Bash", {"command": bad}, Q + "intake-batch")[0] == "deny"


def test_guard_verifier_reasoning_still_denied(running):
    assert guard(running, "Read", {"file_path": f"findings/{RUN}/reasoning/gis.md"}, Q + "verifier")[0] == "deny"


# ------------------------------------------------------------------ 4. Windows paths (P39-6)
def test_map_source_windows_case_insensitive():
    active = {"source": {"logical": LOGICAL, "path": "C:/Users/X/OneDrive - AICC/Submission/Namar/60%"}}
    got = ldip_common.map_source("c:/users/x/onedrive - aicc/submission/namar/60%/Vol. 01/A.dwg", active)
    assert got == f"{LOGICAL}/Vol. 01/A.dwg"
    assert ldip_common.map_source("C:/Users/X/OneDrive - AICC/Submission/Namar/60%-old/A.dwg", active) is None
    assert ldip_common.path_under("C:/A/B", "c:/a") and not ldip_common.path_under("/A/B", "/a")


# ------------------------------------------------------------------ 5. close-time integrity (P39-5)
def test_close_unchanged(running):
    rc, res = close(running)
    assert rc == 0 and res["run_status"] == "completed" and res["source_check"] == "unchanged"
    assert not (running["root"] / "runs/_active_run.json").exists()


def test_close_same_content_new_mtime_not_aborted(running):
    f = running["src"] / "Vol. 01_Drawings" / "A-001.dwg"
    st = f.stat()
    os.utime(f, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))
    rc, res = close(running)
    assert rc == 0 and res["run_status"] == "completed"


@pytest.mark.parametrize("change", ["added", "deleted", "size", "content"])
def test_close_changed_aborts(running, change):
    d = running["src"] / "Vol. 01_Drawings"
    f = d / "A-001.dwg"
    if change == "added":
        (d / "A-002.dwg").write_bytes(b"new")
    elif change == "deleted":
        f.unlink()
    elif change == "size":
        f.write_bytes(b"dwg-bytes-longer")
    else:
        st = f.stat()
        f.write_bytes(b"DWG-BYTES")                           # same size, new content
        os.utime(f, ns=(st.st_atime_ns, st.st_mtime_ns + 7_000_000_000))
    rc, res = close(running, "completed")
    assert rc == 0 and res["run_status"] == "aborted" and res["source_check"] == "changed"
    m = json.loads((running["root"] / f"runs/{RUN}/manifest.json").read_text())
    assert m["status"] == "aborted" and m["source_check"]["changed"]
    end = json.loads((running["root"] / f"runs/{RUN}/run.jsonl").read_text().splitlines()[-1])
    assert end["detail"]["requested"] == "completed" and end["detail"]["source_changed_count"] >= 1


# ------------------------------------------------------------------ 6. provenance on logical paths
def test_provenance_rule_on_logical_path():
    sys.path.insert(0, str(PLUGIN / "skills/parsing-landxml/scripts"))
    import landxml_common
    assert landxml_common.expected_provenance(f"{LOGICAL}/Vol. 04_Models/R-100.xml") == "parsons"
    assert landxml_common.expected_provenance(f"{LOGICAL}/Vol. 04_Models/R-100-AICC-EXPORT.xml") == "aicc_export"


def test_write_manifest_reads_source_root(world, tmp_path):
    pytest.importorskip("jsonschema")
    sk = PLUGIN / "skills/extracting-dwg-data/scripts"
    sys.path.insert(0, str(sk))
    import write_manifest
    real = write_manifest.input_file(f"{LOGICAL}/Vol. 04_Models/R-100.xml", None, str(world["src"]), SITE, SUB)
    assert real == world["src"] / "Vol. 04_Models" / "R-100.xml"
    assert write_manifest.input_file(f"{LOGICAL}/x.xml", "/r", None, SITE, SUB) == Path("/r") / LOGICAL / "x.xml"
    # a non-LandXML .xml read through --source-root is classified (skipped), not "not found"
    (world["src"] / "Vol. 04_Models" / "notes.xml").write_text("<root/>")
    b = world["root"] / f"0.ai_context/{SITE}/{SUB}/b01"
    rc, res = sh([sk / "write_manifest.py", "--out", b / "batch_manifest.json", "--run", RUN, "--site", SITE,
                  "--sub", SUB, "--file", f"{LOGICAL}/Vol. 04_Models/notes.xml", "0" * 64,
                  "--source-root", world["src"]], {**os.environ})
    assert rc == 0, res
    entry = json.loads((b / "batch_manifest.json").read_text())["files"][0]
    assert entry["path"] == f"{LOGICAL}/Vol. 04_Models/notes.xml" and entry["status"] == "skipped"


# ------------------------------------------------------------------ 7. quoted '&' in real paths (P39-9)
def test_guard_quoted_ampersand_allowed_operators_still_denied(running):
    sk = PLUGIN / "skills/extracting-dwg-data/scripts"
    b = f"0.ai_context/{SITE}/{SUB}/b01"
    dwg = running["src"] / "Vol. 01_Drawings" / "A-001.dwg"          # path contains 'Docs & More'
    ok = f'python "{sk}/convert.py" --engine chain --dwg "{dwg}" --out "{b}/A-001.dxf" --log "{b}/A-001.convert.json"'
    assert guard(running, "Bash", {"command": ok}, Q + "intake-batch") == ("allow", "")
    for bad in (ok + " && echo done", ok + " ; ls", ok + " > out.txt", ok + " | cat",
                ok.replace(str(dwg), "$HOME/x.dwg"), ok.replace(str(dwg), "`whoami`.dwg"),
                ok.replace(f'"{dwg}"', f"'{dwg}' & ls")):
        d, why = guard(running, "Bash", {"command": bad}, Q + "intake-batch")
        assert d == "deny" and "shell operators" in why, bad
    assert guard(running, "Bash", {"command": ok[:-1]}, Q + "intake-batch")[0] == "deny"   # unclosed quote
