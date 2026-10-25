"""Skill 7 comparing-drawing-revisions 0.1.0 — synthetic tests T7-1…T7-6
(SPC-0006 Rev B §4.4; Rev M §4.15 DM-3…DM-9; S31). No real project data.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import openpyxl
import pytest
from jsonschema import Draft202012Validator

REPO = Path(__file__).resolve().parents[1]
PLUGIN = REPO / "plugin/ldip-design-review"
SK = PLUGIN / "skills/comparing-drawing-revisions/scripts"
REF = PLUGIN / "skills/comparing-drawing-revisions/references"
TPL = PLUGIN / "skills/comparing-drawing-revisions/templates"
PY = sys.executable


def run(script, *args):
    p = subprocess.run([PY, str(SK / script), *map(str, args)], capture_output=True, text=True)
    line = p.stdout.strip().splitlines()[-1] if p.stdout.strip() else "{}"
    try:
        msg = json.loads(line)
    except json.JSONDecodeError:
        msg = {"raw": p.stdout, "stderr": p.stderr}
    return p.returncode, msg


def write(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=1), encoding="utf-8")
    return path


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def pkg010_rules(tmp_path, state="active"):
    """Minimal get_rules.py-shaped file with PKG-010 (method text drives ordering)."""
    rule = {
        "rule_id": "PKG-010",
        "title": "Revision advances from the previous submittal",
        "statement": "A resubmitted document shall carry a later revision code.",
        "method": "Order revision codes within each series (0A<0B<..; 00<01<..; A0<A1<..); compare with rev_diff.json.",
        "state": state,
        "cap": "Confirmed" if state == "active" else "Probable",
        "severity_default": "Minor",
        "issue_type": "package_control",
        "owner_role": "SBM",
        "skill": "comparing-drawing-revisions",
        "default_status": {"script_output": "Confirmed", "dwg_vector": "Probable"},
        "parameters": {},
        "inputs": ["file_name", "previous_submittal", "dwg_title_block"],
        "exclusions": [],
        "skip_subchecks_for": [],
    }
    p = tmp_path / "rules_INT_skill7.json"
    write(p, {"agent": "INT", "rulebook_version": "v7", "rules": [rule] if state != "absent" else [],
              "rules_skipped": [] if state != "absent" else [
                  {"rule_id": "PKG-010", "reason": "inactive: test"}]})
    return p


def drawing(number, revision=None, title=None, sheet=None, **extra):
    d = {"number": number, "revision": revision, "title": title, "sheet": sheet}
    d.update(extra)
    return d


# ------------------------------------------------------------------ T7-1
def test_t7_1_same_number_moved_polyline(tmp_path):
    """T7-1: same-number pair with one moved polyline (geometry hash, not handle)."""
    prev = write(tmp_path / "prev.json", {"drawings": [
        drawing("LDIP-RCRC-SAP-03-CC-CIV-DWG-00-11000-00", "00", "SITE PLAN", "01 OF 02",
                entities=[{"kind": "polyline", "layer": "C-BLDG", "vertices": [[0, 0], [10, 0], [10, 5], [0, 0]]},
                          {"kind": "polyline", "layer": "C-ROAD", "vertices": [[0, 0], [20, 0]]}]),
    ]})
    # Same second polyline; first polyline translated (+1,+1) — handle would differ, hash must catch move
    curr = write(tmp_path / "curr.json", {"drawings": [
        drawing("LDIP-RCRC-SAP-03-CC-CIV-DWG-00-11000-01", "01", "SITE PLAN", "01 OF 02",
                entities=[{"kind": "polyline", "layer": "C-BLDG", "vertices": [[1, 1], [11, 1], [11, 6], [1, 1]]},
                          {"kind": "polyline", "layer": "C-ROAD", "vertices": [[0, 0], [20, 0]]}]),
    ]})
    pairs = tmp_path / "pairs.json"
    rc, msg = run("match_revisions.py", "--previous", prev, "--current", curr, "--out", pairs)
    assert rc == 0, msg
    assert msg["pairs"] == 1 and msg["normalised"] == 1

    # Attach entities onto the pair for diff_entities (sides from match don't carry inline entities)
    pdata = load(pairs)
    pdata["pairs"][0]["previous_entities"] = load(prev)["drawings"][0]["entities"]
    pdata["pairs"][0]["current_entities"] = load(curr)["drawings"][0]["entities"]
    write(pairs, pdata)

    out = tmp_path / "rev_diff.json"
    rc, msg = run("diff_entities.py", "--pairs", pairs, "--out", out)
    assert rc == 0, msg
    diff = load(out)
    ed = diff["entity_diffs"][0]
    assert ed["changed"] is True
    assert ed["moved"] is True
    assert ed["unchanged"] == 1
    assert len(ed["added"]) == 1 and len(ed["removed"]) == 1
    # Hashes must not depend on handle
    assert "handle" not in json.dumps(ed)


# ------------------------------------------------------------------ T7-2
def test_t7_2_revision_not_advanced(tmp_path):
    """T7-2: same-number pair, revision not advanced → PKG-010 fail."""
    prev = write(tmp_path / "prev.json", {"drawings": [
        drawing("LDIP-RCRC-SAP-03-CC-CIV-DWG-00-11000-00", "00", title_block_revision="00"),
    ]})
    curr = write(tmp_path / "curr.json", {"drawings": [
        drawing("LDIP-RCRC-SAP-03-CC-CIV-DWG-00-11000-00", "00", title_block_revision="00"),
    ]})
    pairs = tmp_path / "pairs.json"
    assert run("match_revisions.py", "--previous", prev, "--current", curr, "--out", pairs)[0] == 0
    rules = pkg010_rules(tmp_path)
    out = tmp_path / "rev_diff.json"
    rc, msg = run("check_revision.py", "--pairs", pairs, "--rules", rules, "--out", out)
    assert rc == 0, msg
    checks = load(out)["revision_checks"]
    assert len(checks) == 1
    assert checks[0]["result"] == "fail"
    assert checks[0]["rule_id"] == "PKG-010"
    assert checks[0]["comparison"] == "same"
    assert checks[0]["severity"] == "Minor"


# ------------------------------------------------------------------ T7-3
def test_t7_3_renumbered_map_observation(tmp_path):
    """T7-3: renumbered pair via map, same revision, changed content → Observation (DM-3)."""
    prev = write(tmp_path / "prev.json", {"drawings": [
        drawing("LDIP-RCRC-SAP-03-CC-CIV-DWG-00-11000-00", "00", "OLD SHEET", "01 OF 01"),
    ]})
    curr = write(tmp_path / "curr.json", {"drawings": [
        drawing("LDIP-RCRC-SAP-03-CC-CIV-DWG-00-22000-00", "00", "NEW SHEET", "01 OF 01"),
    ]})
    nmap = tmp_path / "map.csv"
    nmap.write_text("old,new\nLDIP-RCRC-SAP-03-CC-CIV-DWG-00-11000,LDIP-RCRC-SAP-03-CC-CIV-DWG-00-22000\n",
                    encoding="utf-8")
    pairs = tmp_path / "pairs.json"
    rc, msg = run("match_revisions.py", "--previous", prev, "--current", curr,
                  "--number-map", nmap, "--out", pairs)
    assert rc == 0, msg
    assert msg["number_map"] == 1
    assert load(pairs)["pairs"][0]["renumbered"] is True

    rules = pkg010_rules(tmp_path)
    out = tmp_path / "rev_diff.json"
    rc, msg = run("check_revision.py", "--pairs", pairs, "--rules", rules,
                  "--content-changed", "0", "--out", out)
    assert rc == 0, msg
    doc = load(out)
    assert doc["revision_checks"][0]["result"] == "observation"
    assert doc["revision_checks"][0]["severity"] == "Observation"
    assert doc["counts"]["observations_dm3"] == 1
    assert doc["counts"]["pkg010_fail"] == 0


# ------------------------------------------------------------------ helpers for CRS
def crs_book(path, rows):
    """Minimal RCRC/AICC CRS workbook."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["#", "Page/Para/Clause/Doc. No.", "Review Comment", "Contractor's Response",
               "Status Code", "Comment Status"])
    for r in rows:
        ws.append(r)
    wb.save(path)
    return path


# ------------------------------------------------------------------ T7-4
def test_t7_4_crs_l_d_g_rows(tmp_path):
    """T7-4: CRS with one L, one D and one G row; outcomes never Closed/Open."""
    crs = crs_book(tmp_path / "crs.xlsx", [
        [1, "Sheet 01", "The drawing polyline on sheet 01 is wrong",
         "Revised and corrected on the DWG", "A", "Closed"],
        [2, "Spec §4.2", "Design criteria in the specification need update",
         "Will not change the report", "B", "Open"],
        [3, "General", "General note for information only",
         "Noted", "C", "Open"],
    ])
    out = tmp_path / "closure.json"
    xlsx = tmp_path / "closure.xlsx"
    rc, msg = run("check_closed_comments.py", "--crs", crs, "--out", out, "--xlsx", xlsx)
    assert rc == 0, msg
    doc = load(out)
    types = {r["no"]: r["row_type"] for r in doc["rows"]}
    assert types["1"] == "L"
    assert types["2"] == "D"
    assert types["3"] == "G"
    assert doc["counts"]["L"] == 1 and doc["counts"]["D"] == 1 and doc["counts"]["G"] == 1
    for r in doc["rows"]:
        assert r["outcome"] in ("Addressed", "Not addressed", "Cannot tell")
        assert r["outcome"] not in ("Closed", "Open")
        assert "Closed" not in r["outcome"] and "Open" not in r["outcome"]
    # G row may have supporting_refs; never drives outcome
    g = next(r for r in doc["rows"] if r["row_type"] == "G")
    assert "supporting_refs" in g
    assert g["outcome"] == "Cannot tell"  # "Noted" is not a clear address/reject
    assert msg["Addressed"] == 1
    # Schema
    schema = json.loads((TPL / "closure.schema.json").read_text(encoding="utf-8"))
    Draft202012Validator(schema).validate(doc)
    assert xlsx.is_file()


# ------------------------------------------------------------------ T7-5
def test_t7_5_deliverable_added(tmp_path):
    """T7-5: deliverable added between packages."""
    prev_pkg = write(tmp_path / "prev_pkg.json", {
        "sheet_register": [
            {"base": "LDIP-RCRC-SAP-03-CC-CIV-DWG-00-11000", "formats_received": ["DWG", "PDF"],
             "numbers": {"file_name": "LDIP-RCRC-SAP-03-CC-CIV-DWG-00-11000-00"},
             "revisions": {"file_name": "00"}, "carriers": {}, "files": {}},
        ]
    })
    curr_pkg = write(tmp_path / "curr_pkg.json", {
        "sheet_register": [
            {"base": "LDIP-RCRC-SAP-03-CC-CIV-DWG-00-11000", "formats_received": ["DWG", "PDF"],
             "numbers": {"file_name": "LDIP-RCRC-SAP-03-CC-CIV-DWG-00-11000-01"},
             "revisions": {"file_name": "01"}, "carriers": {}, "files": {}},
            {"base": "LDIP-RCRC-SAP-03-CC-CIV-DWG-00-12000", "formats_received": ["DWG", "PDF"],
             "numbers": {"file_name": "LDIP-RCRC-SAP-03-CC-CIV-DWG-00-12000-00"},
             "revisions": {"file_name": "00"}, "carriers": {}, "files": {}},
        ]
    })
    # Pairing input can be the packages themselves
    pairs = tmp_path / "pairs.json"
    assert run("match_revisions.py", "--previous", prev_pkg, "--current", curr_pkg, "--out", pairs)[0] == 0
    out = tmp_path / "rev_diff.json"
    rc, msg = run("diff_entities.py", "--pairs", pairs, "--previous-package", prev_pkg,
                  "--current-package", curr_pkg, "--out", out)
    assert rc == 0, msg
    inv = load(out)["inventory_diff"]
    assert inv["deliverables_added"] == ["LDIP-RCRC-SAP-03-CC-CIV-DWG-00-12000"]
    assert msg["deliverables_added"] == 1


# ------------------------------------------------------------------ T7-6
def test_t7_6_deferred_response(tmp_path):
    """T7-6: deferred contractor response sets deferred_by_response (DM-8)."""
    crs = crs_book(tmp_path / "crs.xlsx", [
        [1, "Sheet 02 detail", "Fix the detail on the drawing sheet",
         "Will be addressed at the detailed design stage", "A", "Open"],
    ])
    out = tmp_path / "closure.json"
    rc, msg = run("check_closed_comments.py", "--crs", crs, "--out", out)
    assert rc == 0, msg
    row = load(out)["rows"][0]
    assert row["deferred_by_response"] is True
    assert row["outcome"] == "Cannot tell"
    assert row["row_type"] == "L"
    assert msg["deferred_by_response"] == 1


# ------------------------------------------------------------------ extras: normalisation, advance, schema, params GAP
def test_normalise_strips_space_stray_letter_and_revision(tmp_path):
    # Stray single-letter -G- and embedded spaces; revision suffix stripped for the key.
    prev = write(tmp_path / "prev.json", {"drawings": [
        drawing("LDIP-RCRC-SAP-03-CC-CIV-G-DWG-00-11000-00", "00"),
    ]})
    curr = write(tmp_path / "curr.json", {"drawings": [
        drawing("LDIP-RCRC-SAP-03-CC- CIV-DWG-00-11000-01", "01"),
    ]})
    pairs = tmp_path / "pairs.json"
    rc, msg = run("match_revisions.py", "--previous", prev, "--current", curr, "--out", pairs)
    assert rc == 0, msg
    assert msg["normalised"] == 1


def test_revision_advanced_passes(tmp_path):
    prev = write(tmp_path / "prev.json", {"drawings": [
        drawing("LDIP-RCRC-SAP-03-CC-CIV-DWG-00-11000-00", "00"),
    ]})
    curr = write(tmp_path / "curr.json", {"drawings": [
        drawing("LDIP-RCRC-SAP-03-CC-CIV-DWG-00-11000-01", "01"),
    ]})
    pairs = tmp_path / "pairs.json"
    assert run("match_revisions.py", "--previous", prev, "--current", curr, "--out", pairs)[0] == 0
    out = tmp_path / "rev_diff.json"
    rc, msg = run("check_revision.py", "--pairs", pairs, "--rules", pkg010_rules(tmp_path), "--out", out)
    assert rc == 0, msg
    assert load(out)["revision_checks"][0]["result"] == "pass"


def test_pkg010_absent_not_run(tmp_path):
    prev = write(tmp_path / "prev.json", {"drawings": [drawing("A-B-C-D-E-F-DWG-00-11000-00", "00")]})
    curr = write(tmp_path / "curr.json", {"drawings": [drawing("A-B-C-D-E-F-DWG-00-11000-00", "00")]})
    pairs = tmp_path / "pairs.json"
    assert run("match_revisions.py", "--previous", prev, "--current", curr, "--out", pairs)[0] == 0
    rules = pkg010_rules(tmp_path, state="absent")
    out = tmp_path / "rev_diff.json"
    assert run("check_revision.py", "--pairs", pairs, "--rules", rules, "--out", out)[0] == 0
    assert load(out)["revision_checks"][0]["result"] == "not_run"


def test_proposed_title_sheet_pair(tmp_path):
    prev = write(tmp_path / "prev.json", {"drawings": [
        drawing("OLD-NUM-01-AA-CIV-DWG-00-10000-00", "00", "DRAINAGE PLAN", "02 OF 04"),
    ]})
    curr = write(tmp_path / "curr.json", {"drawings": [
        drawing("NEW-NUM-01-AA-CIV-DWG-00-20000-00", "00", "Drainage Plan", "2 of 4"),
    ]})
    pairs = tmp_path / "pairs.json"
    rc, msg = run("match_revisions.py", "--previous", prev, "--current", curr, "--out", pairs)
    assert rc == 0, msg
    assert msg["proposed"] == 1
    p = load(pairs)["pairs"][0]
    assert p["status"] == "proposed"
    assert p["verification_status"] == "Probable"


def test_raster_params_gap_tag():
    params = load(REF / "params.json")
    assert "[GAP — set from T7-R1]" in params["raster"]["tag"]
    assert params["raster"]["verification_status_cap"] == "Probable"
    assert params["geometry"]["hash_uses_handle"] is False


def test_rev_diff_schema_validates_pipeline(tmp_path):
    prev = write(tmp_path / "prev.json", {"drawings": [
        drawing("LDIP-RCRC-SAP-03-CC-CIV-DWG-00-11000-00", "00", "PLAN", "01 OF 01",
                pdf_raster={"regions": [{"id": "r1", "bbox_sheet": [0, 0, 10, 10], "digest": "aaa"}]}),
    ]})
    curr = write(tmp_path / "curr.json", {"drawings": [
        drawing("LDIP-RCRC-SAP-03-CC-CIV-DWG-00-11000-01", "01", "PLAN", "01 OF 01",
                pdf_raster={"regions": [{"id": "r1", "bbox_sheet": [0, 0, 10, 10], "digest": "bbb"}]}),
    ]})
    pairs = tmp_path / "pairs.json"
    assert run("match_revisions.py", "--previous", prev, "--current", curr, "--out", pairs)[0] == 0
    # Carry raster onto pair sides (match_revisions preserves pdf_raster on drawings)
    assert load(pairs)["pairs"][0]["current"].get("pdf_raster")
    out = tmp_path / "rev_diff.json"
    assert run("diff_entities.py", "--pairs", pairs, "--out", out)[0] == 0
    assert run("check_revision.py", "--pairs", pairs, "--rules", pkg010_rules(tmp_path),
               "--entity-diff", out, "--out", out)[0] == 0
    doc = load(out)
    assert doc["raster_diffs"][0]["verification_status"] == "Probable"
    assert doc["raster_diffs"][0]["changed"] is True
    schema = json.loads((TPL / "rev_diff.schema.json").read_text(encoding="utf-8"))
    Draft202012Validator(schema).validate(doc)


def test_types_override(tmp_path):
    crs = crs_book(tmp_path / "crs.xlsx", [
        [1, "General", "General note", "Addressed in full", "A", "Closed"],
    ])
    types = tmp_path / "types.csv"
    types.write_text("no,type\n1,L\n", encoding="utf-8")
    out = tmp_path / "closure.json"
    rc, msg = run("check_closed_comments.py", "--crs", crs, "--out", out, "--types", types)
    assert rc == 0, msg
    assert load(out)["rows"][0]["row_type"] == "L"
    assert load(out)["rows"][0]["outcome"] == "Addressed"


def test_skill_layout_and_version():
    root = PLUGIN / "skills/comparing-drawing-revisions"
    assert (root / "SKILL.md").is_file()
    text = (root / "SKILL.md").read_text(encoding="utf-8")
    assert 'version: "0.1.0"' in text
    assert "SPC-0006 Rev B §4.4 + Rev M §4.15" in text
    for name in ("match_revisions.py", "check_revision.py", "diff_entities.py", "check_closed_comments.py"):
        assert (root / "scripts" / name).is_file()
    assert (root / "references" / "params.json").is_file()
    assert (root / "references" / "row_types.md").is_file()
    assert (root / "evals" / "evals.json").is_file()
    ev = load(root / "evals" / "evals.json")
    assert 3 <= len(ev["evals"]) <= 5
