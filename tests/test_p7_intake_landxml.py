"""P7 — intake wiring of skill 3 parsing-landxml (owner scope 15 Oct 2026 09:03, all six items):
write_manifest.py .xml branch · batch_manifest type landxml + side_files · intake-batch skills and
provenance rule · format_check.py S2 / C3 for *.xml.json and side files · /review-submittal text ·
guard allowlist (access matrix). Synthetic LandXML only; no project data."""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("lxml")
pytest.importorskip("jsonschema")

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_landxml_skill import surface_xml  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
PLUGIN = REPO / "plugin/ldip-design-review"
SK1 = PLUGIN / "skills/extracting-dwg-data/scripts"
SK3 = PLUGIN / "skills/parsing-landxml/scripts"
EV3 = PLUGIN / "skills/parsing-landxml/evals"
PY = sys.executable
RUN = "QRWN-T0001-20261015T0900"
SUB = "submittals/QRWN/T0001"
B = "0.ai_context/QRWN/T0001/b01"
Q = "ldip-design-review:"


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def run(script, *args, cwd=None):
    p = subprocess.run([PY, str(script), *map(str, args)], capture_output=True, text=True, cwd=cwd)
    return p.returncode, json.loads(p.stdout.strip().splitlines()[-1])


@pytest.fixture
def root(tmp_path):
    r = tmp_path / "03 WIP & Co" / "LDIP_Review"
    for d in (SUB, f"runs/{RUN}", "rulebook", "golden_set/inputs", "golden_set/answer_key"):   # no b01: the first parse creates it
        (r / d).mkdir(parents=True, exist_ok=True)
    (r / "rulebook/rules_v0.json").write_text(json.dumps({"status": "approved", "rulebook_version": "v0", "rules": []}))
    return r


def put(root, name, text):
    p = root / SUB / name
    p.write_text(text, encoding="utf-8")
    return f"{SUB}/{name}"


def parse(root, rel, prov):
    out = f"{B}/{Path(rel).name}.json"               # file form, as intake-batch calls it (P7)
    return run(SK3 / "parse_landxml.py", "--in", rel, "--out", out, "--provenance", prov, "--source-path", rel, cwd=root)


def test_out_file_form(tmp_path):
    x = tmp_path / "in" / "A.xml"
    x.parent.mkdir()
    x.write_text(surface_xml(), encoding="utf-8")
    rc, out = run(SK3 / "parse_landxml.py", "--in", x, "--out", tmp_path / "new/b01/A.xml.json", "--provenance", "parsons")
    assert rc == 0 and sorted(p.name for p in (tmp_path / "new/b01").iterdir()) == ["A.surface-1.json", "A.xml.json"]
    rc, out = run(SK3 / "parse_landxml.py", "--in", x, "--out", tmp_path / "new/b01/B.xml.json", "--provenance", "parsons")
    assert rc == 2 and "must be named A.xml.json" in out["detail"]
    rc, _ = run(SK3 / "parse_landxml.py", "--in", x, "--out", tmp_path / "dirform", "--provenance", "parsons")
    assert rc == 0 and (tmp_path / "dirform/A.xml.json").is_file()      # folder form still works standalone


def manifest(root, rels):
    # as the agent calls it: from the LDIP_Review folder, without --root (a '.' argument is the whole
    # tree, which the guard denies to a sub-agent — case batch-manifest-root-dot)
    args = ["--out", f"{B}/batch_manifest.json", "--run", RUN, "--site", "QRWN", "--sub", "T0001"]   # G19-1 form
    for rel in rels:
        args += ["--file", rel, sha(root / rel)]
    return run(SK1 / "write_manifest.py", *args, cwd=root)


def entries(root):
    m = json.loads((root / B / "batch_manifest.json").read_text())
    return m, {Path(f["path"]).name: f for f in m["files"]}


def mixed_batch(root):
    """Surface (parsons), T5 alignment (aicc_export), a non-LandXML .xml and a Civil 3D side file."""
    s = put(root, "X-SURF.xml", surface_xml())
    a = put(root, "Y-ALIGN-AICC-EXPORT.xml", (EV3 / "synthetic_t5.xml").read_text(encoding="utf-8"))
    o = put(root, "other.xml", '<?xml version="1.0"?><Other/>')
    pnt = put(root, "X-SURF.xml.aecc.pnt", "binary-ish side file")
    assert parse(root, s, "parsons")[0] == 0
    assert parse(root, a, "aicc_export")[0] == 0
    rc, out = parse(root, o, "parsons")
    assert rc == 0 and out["status"] == "skipped"
    return [s, a, o, pnt]


# ------------------------------------------------------------------ item 1 + 2: write_manifest, batch manifest
def test_manifest_mixed_batch(root):
    rels = mixed_batch(root)
    rc, out = manifest(root, rels)
    assert rc == 0, out
    m, e = entries(root)
    assert m["counts"] == {"extracted": 2, "failed": 0, "skipped": 2}
    assert e["X-SURF.xml"]["type"] == "landxml" and e["X-SURF.xml"]["status"] == "extracted"
    assert e["X-SURF.xml"]["output"] == "X-SURF.xml.json" and e["X-SURF.xml"]["side_files"] == ["X-SURF.surface-1.json"]
    assert e["Y-ALIGN-AICC-EXPORT.xml"]["status"] == "extracted" and "side_files" not in e["Y-ALIGN-AICC-EXPORT.xml"]
    assert e["other.xml"]["type"] == "other" and e["other.xml"]["status"] == "skipped"
    assert "root element Other" in e["other.xml"]["error"]["detail"]
    assert e["X-SURF.xml.aecc.pnt"]["type"] == "other" and e["X-SURF.xml.aecc.pnt"]["status"] == "skipped"
    assert m["converter"] == {"name": None, "version": None}


@pytest.mark.parametrize("case", ["employer", "parsons_on_export", "aicc_on_plain"])
def test_manifest_provenance_rule(root, case):
    name, prov = {"employer": ("Z.xml", "employer"), "parsons_on_export": ("Z-AICC-EXPORT.xml", "parsons"),
                  "aicc_on_plain": ("Z.xml", "aicc_export")}[case]
    rel = put(root, name, surface_xml())
    assert parse(root, rel, prov)[0] == 0
    rc, _ = manifest(root, [rel])
    assert rc == 0
    m, e = entries(root)
    f = e[name]
    assert f["status"] == "failed" and f["error"]["code"] == 2 and "folder rule" in f["error"]["detail"]
    assert set(f["intermediates"]) == {f"{name}.json", f"{name[:-4]}.surface-1.json"}   # still owned (C3)
    assert run(PLUGIN / "scripts/check_batches.py", "--root", root, "--site", "QRWN", "--sub", "T0001")[1]["result"] == "pass"


def test_manifest_failures(root):
    ent = put(root, "E.xml", surface_xml(doctype='<!DOCTYPE LandXML [<!ENTITY x SYSTEM "file:///etc/passwd">]>'))
    notrun = put(root, "N.xml", surface_xml())
    bad_ns = put(root, "U.xml", surface_xml(ns="http://www.landxml.org/schema/LandXML-9.9"))
    tamper = put(root, "T.xml", surface_xml())
    orphan = put(root, "O.xml", surface_xml())
    assert parse(root, ent, "parsons")[0] == 2
    assert parse(root, bad_ns, "parsons")[0] == 2
    for rel in (tamper, orphan):
        assert parse(root, rel, "parsons")[0] == 0
    side = root / B / "T.surface-1.json"
    side.write_bytes(side.read_bytes().replace(b"657000.0", b"657000.001", 1).replace(b"657000,", b"657000.001,", 1))
    (root / B / "O.xml.json").unlink()                          # side file left, main file missing
    rc, _ = manifest(root, [ent, notrun, bad_ns, tamper, orphan])
    assert rc == 0
    m, e = entries(root)
    assert m["counts"] == {"extracted": 0, "failed": 5, "skipped": 0}
    assert "DOCTYPE" in e["E.xml"]["error"]["detail"] and e["E.xml"]["type"] == "landxml"
    assert "not run or failed" in e["N.xml"]["error"]["detail"]
    assert "namespace" in e["U.xml"]["error"]["detail"]
    assert e["T.xml"]["error"]["code"] == 4 and "tin_sha256" in e["T.xml"]["error"]["detail"]
    assert e["O.xml"]["intermediates"] == ["O.surface-1.json"]
    assert run(PLUGIN / "scripts/check_batches.py", "--root", root, "--site", "QRWN", "--sub", "T0001")[1]["result"] == "pass"


def test_batch_manifest_schema_side_files():
    from jsonschema import Draft202012Validator
    v = Draft202012Validator(json.loads((PLUGIN / "schemas/batch_manifest.schema.json").read_text()))
    base = {"run_id": RUN, "site": "QRWN", "sub_id": "T0001", "batch": "b01", "converter": {"name": None, "version": None},
            "counts": {"extracted": 1, "failed": 0, "skipped": 0}}
    ok = {"path": f"{SUB}/X.xml", "sha256": "a" * 64, "type": "landxml", "status": "extracted",
          "output": "X.xml.json", "side_files": ["X.surface-1.json"], "error": None}
    assert not list(v.iter_errors({**base, "files": [ok]}))
    bad = [dict(ok, type="dwg"), dict(ok, status="failed", output=None, error={"code": 2, "detail": "x"}),
           dict(ok, side_files=["../X.surface-1.json"]), dict(ok, side_files=["X.surface-0.json"]),
           dict(ok, side_files=[]), dict(ok, type="xml")]
    for b in bad:
        assert list(v.iter_errors({**base, "files": [b]})), b


# ------------------------------------------------------------------ item 4: format_check S2 / C3
def run_manifest_file(root, rels):
    (root / f"runs/{RUN}/manifest.json").write_text(json.dumps({
        "run_id": RUN, "site": "QRWN", "sub_id": "T0001", "mode": "test", "rulebook_file": "rulebook/rules_v0.json",
        "rulebook_version": "v0", "status": "running",
        "inputs": [{"path": r, "bytes": (root / r).stat().st_size, "sha256": sha(root / r)} for r in rels]}))


def fc(root):
    files = sorted(f"{B}/{p.name}" for p in (root / B).iterdir() if p.is_file())
    s = {"agent": "intake-batch", "instance": "01", "run_id": RUN, "status": "completed", "files_written": files,
         "counts": {}, "rules_skipped": [], "gaps": [], "inconsistencies": [], "errors": []}
    sp = root / f"runs/{RUN}/returns_s1a_01.json"
    sp.write_text(json.dumps(s))
    rc, out = run(PLUGIN / "scripts/format_check.py", "intake-batch", RUN, "--root", root, "--summary", sp)
    entry = json.loads((root / f"runs/{RUN}/format_checks.json").read_text())[-1]
    return out, entry


def test_format_check_landxml_pass_and_rejects(root):
    rels = mixed_batch(root)
    run_manifest_file(root, rels)
    assert manifest(root, rels)[0] == 0
    out, e = fc(root)
    assert out["result"] == "pass", e["errors"]
    assert e["checks"]["S2"] == "pass" and e["checks"]["C3"] == "pass"
    mp = root / B / "batch_manifest.json"
    good = mp.read_text()

    # (a) side file altered after the manifest was written
    side = root / B / "X-SURF.surface-1.json"
    orig = side.read_bytes()
    side.write_bytes(orig.replace(b"680", b"681", 1))
    out, e = fc(root)
    assert out["result"] == "fail" and {x["check"] for x in e["errors"]} == {"S2"}
    assert any("tin_sha256" in x["message"] for x in e["errors"])
    side.write_bytes(orig)

    # (b) side file dropped from the manifest -> S2 (list differs) and C3 (file not listed)
    m = json.loads(good)
    for f in m["files"]:
        f.pop("side_files", None)
    mp.write_text(json.dumps(m))
    out, e = fc(root)
    assert {x["check"] for x in e["errors"]} == {"S2", "C3"}

    # (c) entry type differs from the ingestion file
    m = json.loads(good)
    for f in m["files"]:
        if f["path"].endswith("Y-ALIGN-AICC-EXPORT.xml"):
            f["type"] = "pdf"
    mp.write_text(json.dumps(m))
    out, e = fc(root)
    assert any("differs from source.file_type" in x["message"] for x in e["errors"])

    # (d) provenance in the ingestion file changed by hand (schema-valid) -> S2 folder rule
    mp.write_text(good)
    ip = root / B / "Y-ALIGN-AICC-EXPORT.xml.json"
    ing = json.loads(ip.read_text())
    ing["source"]["provenance"] = "parsons"
    ip.write_text(json.dumps(ing))
    out, e = fc(root)
    assert any("folder rule" in x["message"] for x in e["errors"]) and out["result"] == "fail"


def test_check_batches_unlisted_side_file(root):
    rels = mixed_batch(root)
    assert manifest(root, rels)[0] == 0
    shutil.copy(root / B / "X-SURF.surface-1.json", root / B / "X-SURF.surface-2.json")
    r = run(PLUGIN / "scripts/check_batches.py", "--root", root, "--site", "QRWN", "--sub", "T0001")[1]
    assert r["result"] == "fail" and r["violations"] == 1


# ------------------------------------------------------------------ item 6: guard
def guard(root, tool, tool_input, agent_type=None, mode="live"):
    (root / "runs/_active_run.json").write_text(json.dumps({"run_id": RUN, "site": "QRWN", "sub_id": "T0001", "mode": mode}))
    env = {**os.environ, "CLAUDE_PLUGIN_ROOT": str(PLUGIN), "LDIP_REVIEW_ROOT": str(root)}
    env.pop("CLAUDE_PLUGIN_DATA", None)
    data = {"hook_event_name": "PreToolUse", "tool_name": tool, "tool_input": tool_input, "cwd": str(root)}
    if agent_type:
        data["agent_type"] = agent_type
    p = subprocess.run([PY, str(PLUGIN / "hooks/guard.py")], input=json.dumps(data), capture_output=True, text=True, env=env)
    assert p.returncode == 0
    return "allow" if not p.stdout.strip() else "deny"


S3 = '"${CLAUDE_PLUGIN_ROOT}/skills/parsing-landxml/scripts/parse_landxml.py"'
OK_PARSE = f'python {S3} --in "{SUB}/X.xml" --out "{B}/X.xml.json" --provenance parsons --source-path "{SUB}/X.xml"'
GUARD_CASES = [
    ("batch-parse", Q + "intake-batch", OK_PARSE, "live", "allow"),
    ("batch-parse-aicc", Q + "intake-batch", OK_PARSE.replace("X.xml", "X-AICC-EXPORT.xml").replace("parsons", "aicc_export"), "live", "allow"),
    ("batch-parse-out-findings", Q + "intake-batch", OK_PARSE.replace(f'"{B}/', f'"findings/{RUN}/candidates/'), "live", "deny"),
    ("batch-parse-out-other-sub", Q + "intake-batch", OK_PARSE.replace(f'"{B}/', '"0.ai_context/QRWN/T0002/b01/'), "live", "deny"),
    ("batch-parse-out-new-batch-file", Q + "intake-batch", OK_PARSE.replace("/b01/", "/b07/"), "live", "allow"),
    ("batch-parse-out-new-batch-dir", Q + "intake-batch", OK_PARSE.replace("/b01/X.xml.json", "/b07"), "live", "deny"),
    ("batch-parse-in-golden-live", Q + "intake-batch", OK_PARSE.replace(f'"{SUB}/X.xml"', '"golden_set/inputs/X.xml"'), "live", "deny"),
    ("batch-parse-in-answer-key", Q + "intake-batch", OK_PARSE.replace(f'"{SUB}/X.xml"', '"golden_set/answer_key/X.xml"'), "test", "deny"),
    ("batch-parse-in-other-site", Q + "intake-batch", OK_PARSE.replace(SUB, "submittals/NMAR/T0001", 1), "live", "deny"),
    ("batch-parse-pipe", Q + "intake-batch", OK_PARSE + " | tee x", "live", "deny"),
    ("batch-common-module", Q + "intake-batch", 'python "${CLAUDE_PLUGIN_ROOT}/skills/parsing-landxml/scripts/landxml_common.py"', "live", "allow"),
    ("batch-make-synthetic", Q + "intake-batch", 'python "${CLAUDE_PLUGIN_ROOT}/skills/parsing-landxml/evals/make_synthetic.py" --out x.xml', "live", "deny"),
    ("batch-manifest-xml", Q + "intake-batch",
     f'python "${{CLAUDE_PLUGIN_ROOT}}/skills/extracting-dwg-data/scripts/write_manifest.py" --batch-dir {B} --run {RUN} '
     f'--site QRWN --sub T0001 --file {SUB}/X.xml {"a" * 64}', "live", "allow"),
    ("batch-manifest-root-dot", Q + "intake-batch",
     f'python "${{CLAUDE_PLUGIN_ROOT}}/skills/extracting-dwg-data/scripts/write_manifest.py" --batch-dir {B} --run {RUN} '
     f'--site QRWN --sub T0001 --root . --file {SUB}/X.xml {"a" * 64}', "live", "deny"),
    ("batch-validate-xml", Q + "intake-batch",
     f'python "${{CLAUDE_PLUGIN_ROOT}}/skills/extracting-dwg-data/scripts/validate_ingestion.py" --files {B}/X.xml.json', "live", "allow"),
    ("gis-parse", Q + "gis-cadastral", OK_PARSE, "live", "deny"),
    ("verifier-parse", Q + "verifier", OK_PARSE, "live", "deny"),
    ("package-parse", Q + "intake-package", OK_PARSE, "live", "deny"),
    ("main-run-parse", None, OK_PARSE, "live", "deny"),
]


@pytest.mark.parametrize("cid,agent,cmd,mode,want", GUARD_CASES, ids=[c[0] for c in GUARD_CASES])
def test_guard_landxml(root, cid, agent, cmd, mode, want):
    (root / B).mkdir(parents=True, exist_ok=True)      # b01 exists once the first file of the batch is written
    assert guard(root, "Bash", {"command": cmd}, agent, mode) == want


def test_g19_1_batch_with_every_file_refused(root):
    """G19-1 (fixed in 0.8.0): every file of the batch refused before anything is written -> b<nn>/ does not
    exist. write_manifest.py --out <B>/batch_manifest.json is allowed by the guard (a file inside the write
    set), creates the folder and records every entry as failed; the format check passes (failures are recorded,
    not judged). --batch-dir on the missing folder stays denied, as before."""
    ent = put(root, "E.xml", surface_xml(doctype='<!DOCTYPE LandXML [<!ENTITY x SYSTEM "file:///etc/passwd">]>'))
    bad = put(root, "U.xml", surface_xml(ns="http://www.landxml.org/schema/LandXML-9.9"))
    assert parse(root, ent, "parsons")[0] == 2 and parse(root, bad, "parsons")[0] == 2
    assert not (root / B).exists()
    files = f"--file {ent} {sha(root / ent)} --file {bad} {sha(root / bad)}"
    W = '"${CLAUDE_PLUGIN_ROOT}/skills/extracting-dwg-data/scripts/write_manifest.py"'
    ok = f"python {W} --out {B}/batch_manifest.json --run {RUN} --site QRWN --sub T0001 {files}"
    old = f"python {W} --batch-dir {B} --run {RUN} --site QRWN --sub T0001 {files}"
    assert guard(root, "Bash", {"command": ok}, Q + "intake-batch") == "allow"
    assert guard(root, "Bash", {"command": old}, Q + "intake-batch") == "deny"
    (root / "runs/_active_run.json").unlink()
    rc, out = manifest(root, [ent, bad])
    assert rc == 0 and out["counts"] == {"extracted": 0, "failed": 2, "skipped": 0}, out
    assert sorted(p.name for p in (root / B).iterdir()) == ["batch_manifest.json"]
    run_manifest_file(root, [ent, bad])
    res, e = fc(root)
    assert res["result"] == "pass", e["errors"]


def test_write_manifest_out_argument(root):
    W = SK1 / "write_manifest.py"
    rel = put(root, "N.xml", surface_xml())
    base = ["--run", RUN, "--site", "QRWN", "--sub", "T0001", "--file", rel, sha(root / rel)]
    assert run(W, "--out", f"{B}/manifest.json", *base, cwd=root)[0] == 2            # wrong file name
    assert run(W, "--out", "0.ai_context/QRWN/T0001/x1/batch_manifest.json", *base, cwd=root)[0] == 2   # not b<nn>
    p = subprocess.run([PY, str(W), "--out", f"{B}/batch_manifest.json", "--batch-dir", B, *base],
                       capture_output=True, text=True, cwd=root)
    assert p.returncode == 2                                                           # one form only
    assert run(W, "--out", f"{B}/batch_manifest.json", *base, cwd=root)[0] == 0


def test_guard_write_side_file(root):
    assert guard(root, "Write", {"file_path": f"{B}/X.surface-1.json"}, Q + "intake-batch") == "allow"
    assert guard(root, "Write", {"file_path": f"{SUB}/X.surface-1.json"}, Q + "intake-batch") == "deny"


def test_matrix_generated_from_source(tmp_path):
    out = tmp_path / "m.json"
    rc, _ = run(PLUGIN / "scripts/build_access_matrix.py", "--source", REPO / "specs-machine/access_matrix.source.json", "--out", out)
    assert rc == 0
    assert json.loads(out.read_text()) == json.loads((PLUGIN / "hooks/access_matrix.json").read_text())
    m = json.loads(out.read_text())
    assert "parsing-landxml" in m["agents"]["intake-batch"]["bash_skills"]
    assert all("parsing-landxml" not in a.get("bash_skills", []) for k, a in m["agents"].items() if k != "intake-batch")


# ------------------------------------------------------------------ items 3 and 5: agent and command text
def frontmatter(path):
    text = path.read_text(encoding="utf-8")
    return text.split("---", 2)[1], text


def test_intake_batch_agent_text():
    fm, text = frontmatter(PLUGIN / "agents/intake-batch.md")
    assert re.findall(r"^- (\S+)$", fm, re.M) == ["extracting-dwg-data", "extracting-pdf-drawings", "parsing-landxml"]
    assert "tools: Read, Glob, Bash, Write" in fm and "disallowedTools: Agent, Skill, WebFetch, WebSearch, mcp__*" in fm
    body = text.split("---", 2)[2]
    for s in ("LandXML provenance rule", "`parsons`", "`aicc_export`", "-AICC-EXPORT", "Never use `employer`",
              "item 26", "`<file>.xml.json`", "<stem>.surface-<n>.json"):
        assert s in body, s
    assert len(body.splitlines()) < 200


def test_review_submittal_text():
    text = (PLUGIN / "commands/review-submittal.md").read_text(encoding="utf-8")
    assert "`.dwg`, `.pdf` and `.xml`" in text and "*.xml.aecc.pnt" in text
    assert "do not add provenance to the prompt" in text
    assert "type: dwg" in text          # GIS still receives DWG ingestion files only


# ------------------------------------------------------------------ impact on skill 6 (downstream reader of batch manifests)
def test_skill6_merge_and_register_with_landxml(root):
    """merge_batches.py and build_register.py (skill 6) read batch manifests with LandXML entries without
    error: .xml files enter the register as format XML (a MIDP format), with no title-block carrier."""
    rels = mixed_batch(root)
    assert manifest(root, rels)[0] == 0
    SK6 = PLUGIN / "skills/controlling-submittal-package/scripts"
    rc, out = run(SK6 / "merge_batches.py", "--context", "0.ai_context/QRWN/T0001", "--run", RUN,
                  "--out", "files.json", cwd=root)
    assert rc == 0 and out["files"] == 4, out
    merged = {f["name"]: f for f in json.loads((root / "files.json").read_text())["files"]}
    assert merged["X-SURF.xml"]["format"] == "XML"
    assert merged["X-SURF.xml"]["extraction"] == {"status": "extracted", "ingestion": "X-SURF.xml.json", "error": None}
    rc, out = run(SK6 / "build_register.py", "--files", "files.json", "--site", "QRWN", "--sub", "T0001",
                  "--run", RUN, "--list-not-received", "--out", "register.json", cwd=root)
    assert rc == 0, out
    reg = json.loads((root / "register.json").read_text())
    text = json.dumps(reg)
    assert "X-SURF.xml" in text and "Y-ALIGN-AICC-EXPORT.xml" in text
    assert reg["dwg_origin"] == []                       # LandXML never enters the DWG-origin list
    for r in reg["sheet_register"]:
        for k in ("dwg_title_block", "pdf_title_block"):
            assert not any(c["path"].endswith(".xml") for c in r["carriers"].get(k, []))
