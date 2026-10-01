"""Tests for skill 18 verifying-review-findings (SPC-0006 §4.7.5). Synthetic data only; the real-data
chain (skill 9 candidates on the staged schedules) runs only with LDIP_REVIEW_ROOT (never in Git)."""
import copy
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_cadastral_skill as sk9          # noqa: E402
import test_rulebook_skill as sk8           # noqa: E402

REPO = Path(__file__).resolve().parents[1]
SC = REPO / "plugin/ldip-design-review/skills/verifying-review-findings/scripts"
PY = sys.executable
RUN = sk9.RUN


def run(script, *args):
    p = subprocess.run([PY, str(SC / script), *map(str, args)], capture_output=True, text=True)
    return p.returncode, json.loads(p.stdout.strip().splitlines()[-1])


def rulebook(tmp_path, advisory=(), inactive=()):
    b = sk8.book()
    ids = ["GIS-001", "GIS-002", "GIS-003", "GIS-012", "GIS-013", "GIS-014", "GIS-015", "GIS-008", "GIS-004", "PKG-001"]
    rules = []
    for rid in ids:
        state = "inactive" if rid in inactive else "advisory" if rid in advisory else "active"
        params = {"g": {"value": None, "tag": "GAP", "blocking": True}} if state == "inactive" else {}
        rules.append(sk8.rule(rid, state, params, default={"xlsx_cell": "Confirmed", "csv_cell": "Confirmed",
                                                          "pdf_colour": "Probable", "pdf_ocr": "Probable",
                                                          "script_output": "Confirmed"}))
    b["rules"] = rules
    b["code_lists"] = {}
    return sk8.write(tmp_path, b, "rules_vt.json")


def candidates(tmp_path, wb):
    d, g1, sc, rules, _ = sk9.pipeline(tmp_path, wb)
    out = tmp_path / "gis.json"
    rc, s = sk9.run("build_candidates.py", "--results", d, g1, sc, "--rules", rules, "--run", RUN, "--site", "QRWN",
                    "--sub", "TEST", "--document-no", "X", "--review-root", tmp_path, "--out", out)
    assert rc == 0, s
    f = json.loads(out.read_text())
    for x in f:                                   # synthetic rulebook version
        x["reference"]["rulebook_version"] = "v9"
    out.write_text(json.dumps(f))
    return out, f


def verify(tmp_path, cand, rb):
    rc_file, ver = tmp_path / "recheck.json", tmp_path / "verified.json"
    assert run("recheck.py", "--candidates", cand, "--root", tmp_path, "--out", rc_file)[0] == 0
    rc, out = run("set_status.py", "--candidates", cand, "--recheck", rc_file, "--rulebook", rb, "--out", ver)
    assert rc == 0, out
    return json.loads(rc_file.read_text())["results"], json.loads(ver.read_text())


# ------------------------------------------------------------------ T1: independent re-read agrees
def test_t1_reread_agrees_confirmed(tmp_path):
    wb = sk9.build_workbook(tmp_path / "s.xlsx")
    cand, f = candidates(tmp_path, wb)
    rc, ver = verify(tmp_path, cand, rulebook(tmp_path))
    assert {r["recheck_result"] for r in rc.values()} == {"agrees"}
    assert all(r["compared"] for r in rc.values())
    assert {v["verification"]["status"] for v in ver} == {"Confirmed"}
    assert run("validate_verified.py", "--verified", tmp_path / "verified.json", "--candidates", cand,
               "--rulebook", rulebook(tmp_path))[0] == 0


# ------------------------------------------------------------------ T2: seeded change → disagrees
def test_t2_altered_copy_disagrees_probable(tmp_path):
    wb = sk9.build_workbook(tmp_path / "s.xlsx")
    cand, f = candidates(tmp_path, wb)
    book = sk9.openpyxl.load_workbook(wb)
    ws = book["DLUS"]
    for row in ws.iter_rows(min_row=5):
        if row[0].value == "3" and row[2].value == "Local Masjid":
            row[8].value = 777.0                       # GFA of the GIS-013 sample
    book.save(wb)
    rc, ver = verify(tmp_path, cand, rulebook(tmp_path))
    assert {r["recheck_result"] for r in rc.values()} == {"disagrees"}       # file changed → no confirmation
    assert {v["verification"]["status"] for v in ver} == {"Probable"}
    msgs = " ".join(r["method"] for r in rc.values())
    assert "differ" in msgs


def test_csv_reader_and_value_mismatch(tmp_path):
    p = tmp_path / "DLUS.csv"
    p.write_text('a,"1,263.04",x\nb," (88.64)",y\n')
    sys.path.insert(0, str(SC))
    import verify_common as vc
    cells = vc.csv_cells(p, ["B1", "B2", "C2"])
    assert vc.values_agree("1263.04", cells["B1"]) and vc.values_agree("-88.64", cells["B2"])
    assert not vc.values_agree("1263.10", cells["B1"]) and vc.values_agree("y", cells["C2"])
    assert vc.values_agree("3.57095e+06", "3570946.73") and not vc.values_agree("3.57095e+06", "3571000")


# ------------------------------------------------------------------ T3, T4 and V-rules on hand-made candidates
def cand_base(rule_id="GIS-008", src="xlsx_cell", tier="deterministic", conf="High", issue="inconsistency",
              status="Confirmed", n=1):
    return {
        "schema_version": "1.0", "finding_id": f"{RUN}/GIS-{n:03d}", "stage": "candidate", "run_id": RUN,
        "site": "QRWN", "sub_id": "TEST", "agent": "GIS", "title": f"{rule_id} synthetic",
        "statement": "Synthetic statement for the verifier tests.", "issue_type": issue,
        "location": {"document_no": "X", "package_level": True}, "severity": "Minor", "confidence": conf,
        "evidence": [{"evidence_id": "E01", "tier": tier, "source_type": src, "extract": "no cells quoted"}],
        "reference": {"rule_id": rule_id, "rulebook_version": "v9", "source_doc": "SRC", "clause": "c"},
        "proposed_status": status, "owner": {"role": "Lead GIS", "name": None},
        "created_at": "2026-09-30T12:00:00+03:00"}


def manual_verify(tmp_path, cands, recheck, rb):
    c = tmp_path / "c.json"
    c.write_text(json.dumps(cands))
    r = tmp_path / "r.json"
    r.write_text(json.dumps({"results": recheck}))
    rc, out = run("set_status.py", "--candidates", c, "--recheck", r, "--rulebook", rb, "--out", tmp_path / "v.json")
    assert rc == 0, out
    return {v["finding_id"]: v["verification"] for v in json.loads((tmp_path / "v.json").read_text())}, c


def test_t3_advisory_capped(tmp_path):
    rb = rulebook(tmp_path, advisory=("GIS-008",))
    c = cand_base("GIS-008")
    v, _ = manual_verify(tmp_path, [c], {c["finding_id"]: {"recheck_result": "agrees", "method": "synthetic agree"}}, rb)
    assert v[c["finding_id"]]["status"] == "Probable"


def test_t4_colour_only_never_confirmed(tmp_path):
    rb = rulebook(tmp_path)
    c = cand_base("GIS-012", src="pdf_colour", tier="visual", conf="Low", status="Probable")
    v, _ = manual_verify(tmp_path, [c], {}, rb)                       # no re-check record → not_rechecked
    assert v[c["finding_id"]] ["status"] == "Probable" and v[c["finding_id"]]["recheck_result"] == "not_rechecked"


def test_v_rules_and_inactive(tmp_path):
    rb = rulebook(tmp_path, inactive=("GIS-004",))
    a = cand_base("GIS-004", n=1)                                     # inactive rule
    b = cand_base("GIS-012", n=2)
    b["reference"].pop("clause")                                      # V4 fails only if Missing information
    rbk = json.loads(Path(rb).read_text())
    for r in rbk["rules"]:
        if r["rule_id"] == "GIS-013":
            r["default_status"]["xlsx_cell"] = "Missing information"
    Path(rb).write_text(json.dumps(rbk))
    c = cand_base("GIS-013", n=3)                                     # Missing information without issue_type
    d = cand_base("GIS-013", n=4, issue="missing_information")        # valid V4
    agree = {"recheck_result": "agrees", "method": "synthetic agree"}
    v, _ = manual_verify(tmp_path, [a, b, c, d], {x["finding_id"]: agree for x in (a, b, c, d)}, rb)
    assert v[a["finding_id"]]["status"] == "Probable" and "inactive" in v[a["finding_id"]]["notes"]
    assert v[b["finding_id"]]["status"] == "Confirmed"
    assert v[c["finding_id"]]["status"] == "Probable" and "V4" in v[c["finding_id"]]["notes"]
    assert v[d["finding_id"]]["status"] == "Missing information"


def test_validate_catches_fc2_fc7(tmp_path):
    rb = rulebook(tmp_path)
    c1, c2 = cand_base("GIS-012", n=1), cand_base("GIS-013", n=2)
    agree = {"recheck_result": "agrees", "method": "synthetic agree"}
    _, cfile = manual_verify(tmp_path, [c1, c2], {c1["finding_id"]: agree, c2["finding_id"]: agree}, rb)
    ver = json.loads((tmp_path / "v.json").read_text())
    assert run("validate_verified.py", "--verified", tmp_path / "v.json", "--candidates", cfile, "--rulebook", rb)[0] == 0
    bad = copy.deepcopy(ver)
    bad[0]["severity"] = "Major"                                      # FC2
    bad.append(copy.deepcopy(bad[1]))                                 # FC7 duplicate
    (tmp_path / "bad.json").write_text(json.dumps(bad))
    rc, out = run("validate_verified.py", "--verified", tmp_path / "bad.json", "--candidates", cfile, "--rulebook", rb)
    joined = " ".join(out["errors"])
    assert rc == 4 and "FC2" in joined and "FC7" in joined


def test_refuses_draft_rulebook(tmp_path):
    b = sk8.book(status="draft")
    rb = sk8.write(tmp_path, b, "draft.json")
    c = cand_base("GIS-001")
    (tmp_path / "c.json").write_text(json.dumps([c]))
    (tmp_path / "r.json").write_text(json.dumps({"results": {}}))
    rc, out = run("set_status.py", "--candidates", tmp_path / "c.json", "--recheck", tmp_path / "r.json",
                  "--rulebook", rb, "--out", tmp_path / "v.json")
    assert rc == 5


# ------------------------------------------------------------------ real chain (local only)
REAL = os.environ.get("LDIP_REVIEW_ROOT")


@pytest.mark.skipif(not REAL, reason="LDIP_REVIEW_ROOT not set (real schedules stay out of Git)")
def test_real_chain_nmar(tmp_path):
    root = Path(REAL)
    sk8s = REPO / "plugin/ldip-design-review/skills/applying-ldip-rulebook/scripts/get_rules.py"
    rules = tmp_path / "rules.json"
    subprocess.run([PY, str(sk8s), "--rulebook", str(root / "rulebook/rules_v0.json"), "--agent", "GIS", "--out", str(rules)],
                   check=True, capture_output=True)
    wb = root / "submittals/NMAR/masterplan-schedule/LDIP-RCRC-SAP-02-NMAR-MPL-SCH-00001.xlsx"
    d, g1, sc, cand = (tmp_path / n for n in ("d.json", "g.json", "s.json", "gis.json"))
    sk9.run("read_dlus.py", "--file", wb, "--out", d)
    sk9.run("check_ids.py", "--dlus", d, "--out", g1)
    sk9.run("check_schedule.py", "--dlus", d, "--rules", rules, "--pa-file", wb, "--summary-file", wb, "--cf-file", wb, "--out", sc)
    assert sk9.run("build_candidates.py", "--results", d, g1, sc, "--rules", rules, "--run", "NMAR-TEST-20260930T1200",
                   "--site", "NMAR", "--sub", "TEST", "--document-no", "LDIP-RCRC-SAP-02-NMAR-MPL-SCH-00001",
                   "--review-root", root, "--out", cand)[0] == 0
    assert run("recheck.py", "--candidates", cand, "--root", root, "--out", tmp_path / "rc.json")[0] == 0
    assert run("set_status.py", "--candidates", cand, "--recheck", tmp_path / "rc.json",
               "--rulebook", root / "rulebook/rules_v0.json", "--out", tmp_path / "v.json")[0] == 0
    v = json.loads((tmp_path / "v.json").read_text())
    assert len(v) == 6 and {x["verification"]["status"] for x in v} == {"Confirmed"}
    assert run("validate_verified.py", "--verified", tmp_path / "v.json", "--candidates", cand,
               "--rulebook", root / "rulebook/rules_v0.json")[0] == 0
