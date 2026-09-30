"""Tests for format_check.py and the plugin copy of finding.schema.json (SPC-0005 §6.3; SPC-0006 §5.1).
All fixtures are synthetic (fake document numbers, no project data)."""
import copy
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
PLUGIN = REPO / "plugin" / "ldip-design-review"
PY = sys.executable
RUN = "QRWN-T0001-20260930T1200"
F = f"findings/{RUN}"


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def cand(root, i=1, rule="GIS-012", status="Confirmed", conf="High", tier="deterministic", src="xlsx_cell", code="GIS"):
    doc = root / "submittals/QRWN/T0001/A-001.pdf"
    return {
        "schema_version": "1.0", "finding_id": f"{RUN}/{code}-{i:03d}", "stage": "candidate", "run_id": RUN,
        "site": "QRWN", "sub_id": "T0001", "agent": code,
        "title": "Synthetic test finding title", "statement": "Synthetic statement for the format checker test.",
        "issue_type": "inconsistency",
        "location": {"document_no": "TEST-DOC-0001", "file_path": "submittals/QRWN/T0001/A-001.pdf",
                     "file_sha256": sha(doc), "table": {"sheet": "S", "cells": ["A1"]}},
        "severity": "Minor", "confidence": conf,
        "evidence": [{"evidence_id": "E01", "tier": tier, "source_type": src, "extract": "A1=1",
                      "script": {"name": "test.py", "version": "0.0.1", "exit_code": 0}}],
        "reference": {"rule_id": rule, "rulebook_version": "v0", "source_doc": "Rulebook v0", "clause": "test"},
        "proposed_status": status, "owner": {"role": "Lead GIS", "name": None},
        "created_at": "2026-09-30T12:00:00+03:00",
    }


def verified(c, status="Confirmed", recheck="agrees"):
    v = copy.deepcopy(c)
    v["stage"] = "verified"
    v["verification"] = {"status": status, "recheck_result": recheck, "method": "Independent re-read (test).",
                         "verified_at": "2026-09-30T12:30:00+03:00"}
    return v


def registered(v, n=1, gate=None):
    r = copy.deepcopy(v)
    r.update({"stage": "registered", "commercial": {"impact": "NOT_ASSESSED", "schedule_impact": "NOT_ASSESSED"},
              "addressee": "NOT_ASSESSED", "output_route": "CRS", "gate": gate or {"status": "PENDING"},
              "register_no": f"LDIP-QRWN-DRF-{n:04d}"})
    return r


@pytest.fixture
def root(tmp_path):
    r = tmp_path / "03 WIP & Co" / "LDIP_Review"
    for d in ["submittals/QRWN/T0001", f"runs/{RUN}", "rulebook", f"{F}/candidates", f"{F}/evidence/gis",
              f"{F}/reasoning", f"outputs/{RUN}", "0.ai_context/QRWN/T0001"]:
        (r / d).mkdir(parents=True, exist_ok=True)
    doc = r / "submittals/QRWN/T0001/A-001.pdf"
    doc.write_bytes(b"synthetic-pdf")
    (r / "rulebook/rules_v0.json").write_text(json.dumps({
        "status": "approved", "rulebook_version": "v0",
        "rules": [{"rule_id": "GIS-012", "state": "active"}, {"rule_id": "GIS-008", "state": "advisory"},
                  {"rule_id": "GIS-004", "state": "inactive"}, {"rule_id": "PKG-001", "state": "active"}]}))
    (r / f"runs/{RUN}/manifest.json").write_text(json.dumps({
        "run_id": RUN, "site": "QRWN", "sub_id": "T0001", "mode": "test", "rulebook_file": "rulebook/rules_v0.json",
        "rulebook_version": "v0", "status": "running",
        "inputs": [{"path": "submittals/QRWN/T0001/A-001.pdf", "bytes": 13, "sha256": sha(doc)}]}))
    (r / f"{F}/reasoning/gis.md").write_text("not JSON, never read by format_check")
    return r


def summary(root, agent, files, **kw):
    s = {"agent": agent, "instance": "1", "run_id": RUN, "status": "completed", "files_written": files,
         "counts": {}, "rules_skipped": [], "gaps": [], "inconsistencies": [], "errors": []}
    s.update(kw)
    p = root / f"runs/{RUN}/summary_{agent}.json"
    p.write_text(json.dumps(s))
    return p


def write(root, relpath, obj):
    p = root / relpath
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj))


def fc(root, agent, summary_path, *extra):
    p = subprocess.run([PY, str(PLUGIN / "scripts/format_check.py"), agent, RUN, "--root", str(root),
                        "--summary", str(summary_path), *extra], capture_output=True, text=True)
    out = json.loads(p.stdout.strip().splitlines()[-1])
    return p.returncode, out


def last_entry(root):
    return json.loads((root / f"runs/{RUN}/format_checks.json").read_text())[-1]


def failed(root):
    return {k for k, v in last_entry(root)["checks"].items() if v == "fail"}


# ------------------------------------------------------------------ schema fidelity
def test_schema_copy_accepts_valid_and_rejects_the_ten_spc0003_cases(root):
    from jsonschema import Draft202012Validator, FormatChecker
    schema = json.loads((PLUGIN / "schemas/finding.schema.json").read_text())
    Draft202012Validator.check_schema(schema)
    v = Draft202012Validator(schema, format_checker=FormatChecker())
    c = cand(root)
    ver = verified(c)
    reg = registered(ver)
    for good in (c, ver, reg):
        assert not list(v.iter_errors(good))
    bad = {
        "confirmed_with_low_confidence": dict(ver, confidence="Low"),
        "confirmed_but_verifier_disagrees": verified(c, "Confirmed", "disagrees"),
        "nav_without_manual_check": verified(c, "Not auto-verifiable"),
        "candidate_carrying_verification": dict(c, verification=ver["verification"]),
        "confirmed_without_rule": dict(ver, reference=dict(ver["reference"], rule_id=None)),
        "colour_marked_deterministic": dict(ver, evidence=[dict(ver["evidence"][0], source_type="pdf_colour")]),
        "gate_approved_without_approver": dict(reg, gate={"status": "APPROVED"}),
        "classified_missing_addressee": dict(ver, stage="classified"),
        "missing_info_wrong_issue_type": verified(c, "Missing information"),
        "location_without_pointer": dict(ver, location={k: x for k, x in ver["location"].items() if k != "table"}),
    }
    for name, x in bad.items():
        assert list(v.iter_errors(x)), name


# ------------------------------------------------------------------ finder step
def test_gis_pass_and_log(root):
    write(root, f"{F}/candidates/gis.json", [cand(root), cand(root, 2, rule="GIS-008", status="Probable")])
    rc, out = fc(root, "gis-cadastral", summary(root, "gis-cadastral", [f"{F}/candidates/gis.json"]))
    assert rc == 0 and out["result"] == "pass", out
    e = last_entry(root)
    assert e["checks"]["FC5"] == "pass" and f"{F}/candidates/gis.json" in e["files_written_sha256"]


@pytest.mark.parametrize("kw,check", [
    ({"status": "failed"}, "S0"), ({"run_id": "QRWN-X-20260101T0000"}, "S0"), ({"partial": True}, "S0"),
])
def test_s0_summary(root, kw, check):
    write(root, f"{F}/candidates/gis.json", [cand(root)])
    rc, out = fc(root, "gis-cadastral", summary(root, "gis-cadastral", [f"{F}/candidates/gis.json"], **kw))
    assert out["result"] == "fail" and check in failed(root)


def test_s0_invalid_json(root):
    write(root, f"{F}/candidates/gis.json", [cand(root)])
    p = root / f"runs/{RUN}/bad.json"
    p.write_text("{ partial output")
    fc(root, "gis-cadastral", p)
    assert "S0" in failed(root)


def test_s1_tmp_missing_conflict(root):
    write(root, f"{F}/candidates/gis.json", [cand(root)])
    (root / f"{F}/evidence/gis/GIS-001_1.png.tmp").write_text("x")
    (root / f"{F}/evidence/gis/GIS-001_1-AYNLAP1201.png").write_text("x")
    s = summary(root, "gis-cadastral", [f"{F}/candidates/gis.json", f"{F}/evidence/gis/none.png"])
    rc, out = fc(root, "gis-cadastral", s, "--host", "AYNLAP1201")
    msgs = [e["message"] for e in last_entry(root)["errors"] if e["check"] == "S1"]
    assert any("temporary" in m for m in msgs) and any("conflict" in m for m in msgs)
    assert any("does not exist" in m for m in msgs)


def test_s1_required_output_missing(root):
    rc, out = fc(root, "gis-cadastral", summary(root, "gis-cadastral", []))
    assert "S1" in failed(root)


def test_s2_schema_and_identity(root):
    c = cand(root)
    del c["owner"]
    write(root, f"{F}/candidates/gis.json", [c, cand(root, 2, code="INT")])
    fc(root, "gis-cadastral", summary(root, "gis-cadastral", [f"{F}/candidates/gis.json"]))
    msgs = [e["message"] for e in last_entry(root)["errors"] if e["check"] == "S2"]
    assert any("owner" in m for m in msgs) and any("agent is 'INT'" in m for m in msgs)


def test_s3_fc1(root):
    c = cand(root)
    c["evidence"][0]["script"]["exit_code"] = 1
    c2 = cand(root, 2, conf="Medium")
    write(root, f"{F}/candidates/gis.json", [c, c2])
    fc(root, "gis-cadastral", summary(root, "gis-cadastral", [f"{F}/candidates/gis.json"]))
    assert {"S3", "FC1"} <= failed(root)


def test_fc3_artifacts(root):
    art = root / f"{F}/evidence/gis/GIS-001_1.png"
    art.write_bytes(b"png")
    c = cand(root, tier="extracted_text_crop", src="pdf_vector_text", conf="Medium")
    c["evidence"][0].update({"artifact_path": f"{F}/evidence/gis/GIS-001_1.png", "artifact_sha256": sha(art)})
    write(root, f"{F}/candidates/gis.json", [c])
    s = summary(root, "gis-cadastral", [f"{F}/candidates/gis.json", f"{F}/evidence/gis/GIS-001_1.png"])
    assert fc(root, "gis-cadastral", s)[1]["result"] == "pass"
    art.write_bytes(b"changed")
    fc(root, "gis-cadastral", s)
    assert failed(root) == {"FC3"}


def test_fc4_input_hash(root):
    c = cand(root)
    c["location"]["file_sha256"] = "0" * 64
    write(root, f"{F}/candidates/gis.json", [c])
    fc(root, "gis-cadastral", summary(root, "gis-cadastral", [f"{F}/candidates/gis.json"]))
    assert failed(root) == {"FC4"}


@pytest.mark.parametrize("rule,status,ok", [
    ("GIS-012", "Confirmed", True), ("GIS-008", "Probable", True), ("GIS-008", "Confirmed", False),
    ("GIS-008", "Missing information", False), ("GIS-004", "Probable", False), ("GIS-099", "Probable", False),
    (None, "Confirmed", False), (None, "Probable", True),
])
def test_fc5_rule_cap(root, rule, status, ok):
    write(root, f"{F}/candidates/gis.json", [cand(root, rule=rule, status=status)])
    fc(root, "gis-cadastral", summary(root, "gis-cadastral", [f"{F}/candidates/gis.json"]))
    assert ("FC5" not in failed(root)) == ok


# ------------------------------------------------------------------ verifier / outputs
def setup_candidates(root):
    c1, c2 = cand(root), cand(root, 2, rule="GIS-008", status="Probable")
    write(root, f"{F}/candidates/gis.json", [c1, c2])
    return c1, c2


def test_verifier_pass_fc2_fc7(root):
    c1, c2 = setup_candidates(root)
    s = summary(root, "verifier", [f"{F}/verified.json"])
    write(root, f"{F}/verified.json", [verified(c1), verified(c2, "Probable")])
    assert fc(root, "verifier", s)[1]["result"] == "pass"
    v1 = verified(c1)
    v1["severity"] = "Critical"                                  # candidate field changed
    write(root, f"{F}/verified.json", [v1])                        # and c2 missing
    fc(root, "verifier", s)
    assert {"FC2", "FC7"} <= failed(root)
    write(root, f"{F}/verified.json", [verified(c1), verified(c2, "Confirmed")])  # above advisory cap
    fc(root, "verifier", s)
    assert "FC5" in failed(root)


def test_outputs_fc8(root):
    c1, c2 = setup_candidates(root)
    v1, v2 = verified(c1), verified(c2, "Probable")
    write(root, f"{F}/verified.json", [v1, v2])
    s = summary(root, "outputs-register", [f"outputs/{RUN}/registered.json"])
    write(root, f"outputs/{RUN}/registered.json", [registered(v1, 1), registered(v2, 2)])
    assert fc(root, "outputs-register", s)[1]["result"] == "pass"
    deferred = {"status": "DEFERRED", "decided_by": "x", "decided_at": "2026-09-30T13:00:00+03:00"}
    write(root, f"outputs/{RUN}/registered.json", [registered(v1, 1, gate=deferred)])
    fc(root, "outputs-register", s)
    assert failed(root) == {"FC8"}


# ------------------------------------------------------------------ intake-batch
def test_intake_batch_c3(root):
    b = root / "0.ai_context/QRWN/T0001/b01"
    b.mkdir(parents=True)
    (b / "batch_manifest.json").write_text(json.dumps({"files": [{"output": "A-001.pdf.json"}]}))
    (b / "A-001.pdf.json").write_text("{}")
    files = ["0.ai_context/QRWN/T0001/b01/batch_manifest.json", "0.ai_context/QRWN/T0001/b01/A-001.pdf.json"]
    rc, out = fc(root, "intake-batch", summary(root, "intake-batch", files))
    e = last_entry(root)
    assert out["result"] == "pass" and e["checks"]["S2"] == "not_run" and any("ingestion schemas" in n for n in e["notes"])
    (b / "extra.json").write_text("{}")
    fc(root, "intake-batch", summary(root, "intake-batch", files))
    assert failed(root) == {"C3"}


def test_retry_errors_and_log_accumulate(root):
    write(root, f"{F}/candidates/gis.json", [cand(root, conf="Low")])
    s = summary(root, "gis-cadastral", [f"{F}/candidates/gis.json"])
    rc, out = fc(root, "gis-cadastral", s, "--attempt", "1")
    assert out["retry_errors"] and all(r.split()[0] in {"S0", "S1", "S2", "S3", "FC1", "FC3", "FC4", "FC5"} for r in out["retry_errors"])
    fc(root, "gis-cadastral", s, "--attempt", "2")
    log = json.loads((root / f"runs/{RUN}/format_checks.json").read_text())
    assert [x["attempt"] for x in log] == [1, 2]


def test_bad_inputs(root):
    s = summary(root, "gis-cadastral", [])
    assert fc(root, "nobody", s)[0] == 2
    p = subprocess.run([PY, str(PLUGIN / "scripts/format_check.py"), "gis-cadastral", "BAD", "--root", str(root),
                        "--summary", str(s)], capture_output=True, text=True)
    assert p.returncode == 2
