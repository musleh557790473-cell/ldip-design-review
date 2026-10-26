"""Skill 7 comparing-drawing-revisions 0.1.0 — synthetic tests T7-1…T7-6 + S31 R1–R9.

SPC-0006 Rev B §4.4; Rev M §4.15; S31-PR18 review. No real project data.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import numpy as np
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
    write(p, {"agent": "INT", "rulebook_version": "v7",
              "rules": [rule] if state != "absent" else [],
              "rules_skipped": [] if state != "absent" else [
                  {"rule_id": "PKG-010", "reason": "inactive: test"}]})
    return p


def drawing(number, revision=None, title=None, sheet=None, **extra):
    d = {"number": number, "revision": revision, "title": title, "sheet": sheet}
    d.update(extra)
    return d


def crs_book(path, rows):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["#", "Page/Para/Clause/Doc. No.", "Review Comment", "Contractor's Response",
               "Status Code", "Comment Status"])
    for r in rows:
        ws.append(r)
    wb.save(path)
    return path


def make_pdf(path, grey_level=200, text="REV 00 CONCEPT DESIGN SEPT. 2026"):
    """Minimal one-page PDF via reportlab (synthetic raster tests)."""
    from reportlab.pdfgen import canvas
    from reportlab.lib.pagesizes import A4
    path = Path(path)
    c = canvas.Canvas(str(path), pagesize=A4)
    c.setFillGray(grey_level / 255.0)
    c.rect(0, 0, A4[0], A4[1], fill=1, stroke=0)
    c.setFillGray(0)
    c.drawString(40, A4[1] - 40, text)
    c.save()
    return path


def make_rev_table_pdf(path, rows, grey_level=220):
    """PDF with a Revision/Description/Date table in the right title-block strip (R3).

    Columns stay inside the A4 media box so a ~32 % right-edge crop still sees Date.
    """
    from reportlab.pdfgen import canvas
    from reportlab.lib.pagesizes import A4
    path = Path(path)
    w, h = A4
    c = canvas.Canvas(str(path), pagesize=A4)
    c.setFillGray(grey_level / 255.0)
    c.rect(0, 0, w, h, fill=1, stroke=0)
    c.setFillGray(0)
    # Right strip (~32 %): Revision | Description | Date — all within page width
    x0 = w * 0.68
    x_desc = x0 + 48
    x_date = min(w - 55.0, x0 + 155.0)
    y = h - 60
    c.setFont("Helvetica-Bold", 9)
    c.drawString(x0, y, "Revision")
    c.drawString(x_desc, y, "Description")
    c.drawString(x_date, y, "Date")
    c.setFont("Helvetica", 9)
    for code, desc, date in rows:
        y -= 14
        # Keep description short so it does not collide with the Date column
        c.drawString(x0, y, code)
        c.drawString(x_desc, y, (desc or "")[:18])
        c.drawString(x_date, y, date)
    # Decoy tokens that must NOT be read as revision (SHEET / OF / 30%)
    c.drawString(40, h - 40, "SHEET-01  01 OF 05  30% CONCEPT")
    c.save()
    return path


# ------------------------------------------------------------------ T7-1
def test_t7_1_same_number_moved_polyline(tmp_path):
    prev = write(tmp_path / "prev.json", {"drawings": [
        drawing("LDIP-RCRC-SAP-03-CC-CIV-DWG-00-11000-00", "00", "SITE PLAN", "01 OF 02"),
    ]})
    curr = write(tmp_path / "curr.json", {"drawings": [
        drawing("LDIP-RCRC-SAP-03-CC-CIV-DWG-00-11000-01", "01", "SITE PLAN", "01 OF 02"),
    ]})
    pairs = tmp_path / "pairs.json"
    rc, msg = run("match_revisions.py", "--previous", prev, "--current", curr, "--out", pairs)
    assert rc == 0, msg
    assert msg["pairs"] == 1 and msg["normalised"] == 1
    pdata = load(pairs)
    pdata["pairs"][0]["previous_entities"] = [
        {"kind": "polyline", "layer": "C-BLDG", "vertices": [[0, 0], [10, 0], [10, 5], [0, 0]]},
        {"kind": "polyline", "layer": "C-ROAD", "vertices": [[0, 0], [20, 0]]},
    ]
    pdata["pairs"][0]["current_entities"] = [
        {"kind": "polyline", "layer": "C-BLDG", "vertices": [[1, 1], [11, 1], [11, 6], [1, 1]]},
        {"kind": "polyline", "layer": "C-ROAD", "vertices": [[0, 0], [20, 0]]},
    ]
    write(pairs, pdata)
    out = tmp_path / "rev_diff.json"
    rc, msg = run("diff_entities.py", "--pairs", pairs, "--out", out)
    assert rc == 0, msg
    ed = load(out)["entity_diffs"][0]
    assert ed["changed"] is True and ed["moved"] is True
    assert "handle" not in json.dumps(ed)


# ------------------------------------------------------------------ T7-2
def test_t7_2_revision_not_advanced(tmp_path):
    prev = write(tmp_path / "prev.json", {"drawings": [
        drawing("LDIP-RCRC-SAP-03-CC-CIV-DWG-00-11000-00", "00", title_block_revision="00"),
    ]})
    curr = write(tmp_path / "curr.json", {"drawings": [
        drawing("LDIP-RCRC-SAP-03-CC-CIV-DWG-00-11000-00", "00", title_block_revision="00"),
    ]})
    pairs = tmp_path / "pairs.json"
    assert run("match_revisions.py", "--previous", prev, "--current", curr, "--out", pairs)[0] == 0
    out = tmp_path / "rev_diff.json"
    rc, msg = run("check_revision.py", "--pairs", pairs, "--rules", pkg010_rules(tmp_path), "--out", out)
    assert rc == 0, msg
    assert load(out)["revision_checks"][0]["result"] == "fail"


# ------------------------------------------------------------------ T7-3
def test_t7_3_renumbered_map_observation(tmp_path):
    prev = write(tmp_path / "prev.json", {"drawings": [
        drawing("LDIP-RCRC-SAP-03-CC-CIV-DWG-00-11000-00", "00", "OLD SHEET", "01 OF 01"),
    ]})
    curr = write(tmp_path / "curr.json", {"drawings": [
        drawing("LDIP-RCRC-SAP-03-CC-CIV-DWG-00-22000-00", "00", "NEW SHEET", "01 OF 01"),
    ]})
    nmap = tmp_path / "map.csv"
    nmap.write_text(
        "old,new\nLDIP-RCRC-SAP-03-CC-CIV-DWG-00-11000,LDIP-RCRC-SAP-03-CC-CIV-DWG-00-22000\n",
        encoding="utf-8")
    pairs = tmp_path / "pairs.json"
    rc, msg = run("match_revisions.py", "--previous", prev, "--current", curr,
                  "--number-map", nmap, "--out", pairs)
    assert rc == 0, msg
    assert msg["number_map"] == 1
    out = tmp_path / "rev_diff.json"
    rc, msg = run("check_revision.py", "--pairs", pairs, "--rules", pkg010_rules(tmp_path),
                  "--content-changed", "0", "--out", out)
    assert rc == 0, msg
    chk = load(out)["revision_checks"][0]
    assert chk["result"] == "observation"
    assert chk.get("rule_id") is None  # R3b: never PKG-010 on renumbered
    assert load(out)["counts"]["observations_dm3"] == 1
    assert load(out)["counts"]["pkg010_fail"] == 0


# ------------------------------------------------------------------ T7-4 (R4 / R5)
def test_t7_4_crs_l_d_g_rows(tmp_path):
    """T7-4: one L, one D, one G; without evidence → Cannot tell (R4); never Closed/Open."""
    crs = crs_book(tmp_path / "crs.xlsx", [
        [1, "LDIP-RCRC-SAP-03-CC-CIV-DWG-00-11000 sheet 01",
         "Polyline wrong at grid A1 on LDIP-RCRC-SAP-03-CC-CIV-DWG-00-11000 sheet 01",
         "Revised and corrected on the DWG", "A", "Closed"],
        [2, "Doc list",
         "Please provide the missing LandXML deliverable LDIP-RCRC-SAP-03-CC-CIV-MOD-00-90000",
         "Will not change — no native file", "B", "Open"],
        [3, "General", "Design intent is acceptable as guidance for information",
         "Noted and accepted", "C", "Open"],
        ["Reviewed Action: A-No Objection — footer", "", "", "", "", ""],
    ])
    out = tmp_path / "closure.json"
    rc, msg = run("check_closed_comments.py", "--crs", crs, "--out", out, "--xlsx", tmp_path / "c.xlsx")
    assert rc == 0, msg
    doc = load(out)
    assert doc["counts"]["rows"] == 3  # R7: footer not a row
    types = {r["no"]: r["row_type"] for r in doc["rows"]}
    assert types == {"1": "L", "2": "D", "3": "G"}
    # R4: no package / rev-diff → all Cannot tell (response does not set outcome)
    assert all(r["outcome"] == "Cannot tell" for r in doc["rows"])
    assert next(r for r in doc["rows"] if r["no"] == "3")["outcome"] == "Cannot tell"
    Draft202012Validator(json.loads((TPL / "closure.schema.json").read_text(encoding="utf-8"))).validate(doc)


def test_t7_4_with_evidence_sets_l_d_outcomes(tmp_path):
    crs = crs_book(tmp_path / "crs.xlsx", [
        [1, "LDIP-RCRC-SAP-03-CC-CIV-DWG-00-11000 sheet 01",
         "Fix grid A1 on LDIP-RCRC-SAP-03-CC-CIV-DWG-00-11000 sheet 01", "Done", "A", "Open"],
        [2, "Doc", "Please provide deliverable LDIP-RCRC-SAP-03-CC-CIV-MOD-00-90000", "Submitted", "B", "Open"],
        [3, "Doc", "Please provide missing LandXML LDIP-RCRC-SAP-03-CC-CIV-MOD-00-90099", "No", "C", "Open"],
    ])
    pkg = write(tmp_path / "pkg.json", {
        "sheet_register": [
            {"base": "LDIP-RCRC-SAP-03-CC-CIV-MOD-00-90000", "formats_received": ["IFC"],
             "numbers": {}, "revisions": {}, "carriers": {}, "files": []},
        ]
    })
    rev = write(tmp_path / "rev.json", {
        "pairs": [{"previous": {"number": "LDIP-RCRC-SAP-03-CC-CIV-DWG-00-11000-00"},
                   "current": {"number": "LDIP-RCRC-SAP-03-CC-CIV-DWG-00-11000-01"}}],
        "entity_diffs": [{"pair_index": 0, "changed": True, "moved": True}],
        "raster_diffs": [],
    })
    out = tmp_path / "closure.json"
    rc, msg = run("check_closed_comments.py", "--crs", crs, "--out", out,
                  "--package-n", pkg, "--rev-diff", rev)
    assert rc == 0, msg
    rows = {r["no"]: r for r in load(out)["rows"]}
    assert rows["1"]["row_type"] == "L" and rows["1"]["outcome"] == "Addressed"
    assert rows["2"]["row_type"] == "D" and rows["2"]["outcome"] == "Addressed"
    assert rows["3"]["row_type"] == "D" and rows["3"]["outcome"] == "Not addressed"


# ------------------------------------------------------------------ T7-5
def test_t7_5_deliverable_added(tmp_path):
    prev_pkg = write(tmp_path / "prev_pkg.json", {
        "sheet_register": [
            {"base": "LDIP-RCRC-SAP-03-CC-CIV-DWG-00-11000", "formats_received": ["DWG", "PDF"],
             "numbers": {"file_name": "LDIP-RCRC-SAP-03-CC-CIV-DWG-00-11000-00"},
             "revisions": {"file_name": "00"}, "carriers": {}, "files": []},
        ]
    })
    curr_pkg = write(tmp_path / "curr_pkg.json", {
        "sheet_register": [
            {"base": "LDIP-RCRC-SAP-03-CC-CIV-DWG-00-11000", "formats_received": ["DWG", "PDF"],
             "numbers": {"file_name": "LDIP-RCRC-SAP-03-CC-CIV-DWG-00-11000-01"},
             "revisions": {"file_name": "01"}, "carriers": {}, "files": []},
            {"base": "LDIP-RCRC-SAP-03-CC-CIV-DWG-00-12000", "formats_received": ["DWG", "PDF"],
             "numbers": {"file_name": "LDIP-RCRC-SAP-03-CC-CIV-DWG-00-12000-00"},
             "revisions": {"file_name": "00"}, "carriers": {}, "files": []},
        ]
    })
    pairs = tmp_path / "pairs.json"
    assert run("match_revisions.py", "--previous", prev_pkg, "--current", curr_pkg, "--out", pairs)[0] == 0
    out = tmp_path / "rev_diff.json"
    rc, msg = run("diff_entities.py", "--pairs", pairs, "--previous-package", prev_pkg,
                  "--current-package", curr_pkg, "--out", out)
    assert rc == 0, msg
    assert load(out)["inventory_diff"]["deliverables_added"] == ["LDIP-RCRC-SAP-03-CC-CIV-DWG-00-12000"]


# ------------------------------------------------------------------ T7-6 (R6)
def test_t7_6_deferred_response(tmp_path):
    crs = crs_book(tmp_path / "crs.xlsx", [
        [1, "General", "Design criteria adequacy for information",
         "Will be part of a separate IFC submission at a later stage", "A", "Open"],
    ])
    out = tmp_path / "closure.json"
    rc, msg = run("check_closed_comments.py", "--crs", crs, "--out", out)
    assert rc == 0, msg
    row = load(out)["rows"][0]
    assert row["row_type"] == "G"
    assert row["deferred_by_response"] is True
    assert row["outcome"] == "Cannot tell"
    assert msg["deferred_by_response"] == 1


# ------------------------------------------------------------------ R2 CSV
def test_r2_number_map_fixed_comma_dialect(tmp_path):
    prev = write(tmp_path / "prev.json", {"drawings": [
        drawing("LDIP-RCRC-SAP-03-DN-ROD-DWG-00-11000", "00"),
    ]})
    curr = write(tmp_path / "curr.json", {"drawings": [
        drawing("LDIP-RCRC-SAP-03-DN-ROD-DWG-00-10200-000", "00"),
    ]})
    # Long hyphenated numbers that used to crash csv.Sniffer
    nmap = tmp_path / "map.csv"
    nmap.write_text(
        "old,new\n"
        "LDIP-RCRC-SAP-03-DN-ROD-DWG-00-11000,LDIP-RCRC-SAP-03-DN-ROD-DWG-00-10200-000\n",
        encoding="utf-8")
    pairs = tmp_path / "pairs.json"
    rc, msg = run("match_revisions.py", "--previous", prev, "--current", curr,
                  "--number-map", nmap, "--out", pairs)
    assert rc == 0, msg
    assert msg["number_map"] == 1


def test_r2_bad_csv_exits_2(tmp_path):
    prev = write(tmp_path / "prev.json", {"drawings": [drawing("A-B-C-D-E-F-DWG-00-1-00", "00")]})
    curr = write(tmp_path / "curr.json", {"drawings": [drawing("A-B-C-D-E-F-DWG-00-2-00", "00")]})
    nmap = tmp_path / "map.csv"
    nmap.write_text('old,new\n"unclosed\n', encoding="utf-8")
    rc, msg = run("match_revisions.py", "--previous", prev, "--current", curr,
                  "--number-map", nmap, "--out", tmp_path / "pairs.json")
    assert rc == 2
    assert msg.get("status") == "error"


# ------------------------------------------------------------------ R1 raster MAD
def test_r1_raster_mad_one_to_one(tmp_path):
    """Synthetic PDFs: similar greys pair; one weak margin possible."""
    p1 = make_pdf(tmp_path / "p1.pdf", 180, "00 · 30% CONCEPT DESIGN · SEPT. 2026")
    p2 = make_pdf(tmp_path / "p2.pdf", 40, "00 · OTHER")
    c1 = make_pdf(tmp_path / "c1.pdf", 185, "00 · 30% CONCEPT DESIGN · SEPT. 2026")
    c2 = make_pdf(tmp_path / "c2.pdf", 45, "00 · OTHER")
    prev = write(tmp_path / "prev.json", {"drawings": [
        drawing("OLD-AA-BB-CC-DD-EE-DWG-00-10000", None, pdf=str(p1)),
        drawing("OLD-AA-BB-CC-DD-EE-DWG-00-20000", None, pdf=str(p2)),
    ]})
    curr = write(tmp_path / "curr.json", {"drawings": [
        drawing("NEW-AA-BB-CC-DD-EE-DWG-00-10000", None, pdf=str(c1)),
        drawing("NEW-AA-BB-CC-DD-EE-DWG-00-20000", None, pdf=str(c2)),
    ]})
    pairs = tmp_path / "pairs.json"
    rc, msg = run("match_revisions.py", "--previous", prev, "--current", curr, "--out", pairs)
    assert rc == 0, msg
    assert msg["raster_mad"] == 2
    assert msg["proposed"] == 2
    assert msg["unmatched_current"] == 0
    doc = load(pairs)
    assert all(p["status"] == "proposed" and p["verification_status"] == "Probable" for p in doc["pairs"])
    assert all("mad" in p for p in doc["pairs"])


def test_r1_assign_unit_margin():
    sys.path.insert(0, str(SK))
    from raster_match import assign_one_to_one, margins_for_assignment, mad_cost, mask_title_block
    a = np.zeros((10, 10), dtype=np.uint8)
    b = np.zeros((10, 10), dtype=np.uint8)
    b[:, :] = 10
    assert mad_cost(a, a) == 0.0
    assert mad_cost(a, b) == 10.0
    masked = mask_title_block(np.ones((10, 100), dtype=np.uint8) * 255, 0.18)
    assert masked[:, 82:].sum() == 0
    cost = np.array([[1.0, 50.0], [50.0, 2.0]])
    asg = assign_one_to_one(cost)
    assert {(i, j) for i, j, _ in asg} == {(0, 0), (1, 1)}
    m = margins_for_assignment(cost, asg)
    assert m[(0, 0)] == 50.0 / 1.0


# ------------------------------------------------------------------ R3 revision table
def test_r3_revision_rejects_percent_sheet_of():
    """R3: never take 30 from 30%, SHEET-01, or 01 OF 05; table column only."""
    sys.path.insert(0, str(SK))
    from revision_table import revision_from_words
    # Fake header + row: Revision=00, Description starts with 30%, Date
    words = [
        {"text": "Revision", "x0": 10, "x1": 50, "top": 10, "bottom": 18},
        {"text": "Description", "x0": 60, "x1": 120, "top": 10, "bottom": 18},
        {"text": "Date", "x0": 200, "x1": 230, "top": 10, "bottom": 18},
        {"text": "00", "x0": 12, "x1": 28, "top": 30, "bottom": 38},
        {"text": "30%", "x0": 62, "x1": 90, "top": 30, "bottom": 38},
        {"text": "CONCEPT", "x0": 95, "x1": 140, "top": 30, "bottom": 38},
        {"text": "SEPT.", "x0": 200, "x1": 230, "top": 30, "bottom": 38},
        {"text": "2026", "x0": 232, "x1": 260, "top": 30, "bottom": 38},
        # Decoys outside the revision column
        {"text": "SHEET-01", "x0": 300, "x1": 360, "top": 50, "bottom": 58},
        {"text": "01", "x0": 300, "x1": 320, "top": 70, "bottom": 78},
        {"text": "OF", "x0": 325, "x1": 340, "top": 70, "bottom": 78},
        {"text": "05", "x0": 345, "x1": 360, "top": 70, "bottom": 78},
    ]
    code, src = revision_from_words(words)
    assert code == "00" and src == "revision_table"


def test_r3_newest_by_date():
    sys.path.insert(0, str(SK))
    from revision_table import revision_from_words
    words = [
        {"text": "Revision", "x0": 10, "x1": 50, "top": 10, "bottom": 18},
        {"text": "Description", "x0": 60, "x1": 120, "top": 10, "bottom": 18},
        {"text": "Date", "x0": 200, "x1": 230, "top": 10, "bottom": 18},
        {"text": "00", "x0": 12, "x1": 28, "top": 30, "bottom": 38},
        {"text": "Code-C", "x0": 62, "x1": 100, "top": 30, "bottom": 38},
        {"text": "17-08-2026", "x0": 200, "x1": 270, "top": 30, "bottom": 38},
        {"text": "01", "x0": 12, "x1": 28, "top": 50, "bottom": 58},
        {"text": "Issued", "x0": 62, "x1": 100, "top": 50, "bottom": 58},
        {"text": "08-10-2026", "x0": 200, "x1": 270, "top": 50, "bottom": 58},
    ]
    code, src = revision_from_words(words)
    assert code == "01" and src == "revision_table"


def test_r3_check_revision_reads_pdf_table(tmp_path):
    pdf_old = make_rev_table_pdf(tmp_path / "old.pdf", [
        ("00", "30% CONCEPT DESIGN", "SEPT. 2026"),
    ])
    pdf_new = make_rev_table_pdf(tmp_path / "new.pdf", [
        ("00", "30% CONCEPT DESIGN", "SEPT. 2026"),
    ], grey_level=210)
    prev = write(tmp_path / "prev.json", {"drawings": [
        drawing("LDIP-RCRC-SAP-03-DN-ROD-DWG-00-11000", None, pdf=str(pdf_old)),
    ]})
    curr = write(tmp_path / "curr.json", {"drawings": [
        drawing("LDIP-RCRC-SAP-03-DN-ROD-DWG-00-10200-000", None, pdf=str(pdf_new)),
    ]})
    nmap = tmp_path / "map.csv"
    nmap.write_text(
        "old,new\nLDIP-RCRC-SAP-03-DN-ROD-DWG-00-11000,LDIP-RCRC-SAP-03-DN-ROD-DWG-00-10200-000\n",
        encoding="utf-8")
    pairs = tmp_path / "pairs.json"
    assert run("match_revisions.py", "--previous", prev, "--current", curr,
               "--number-map", nmap, "--out", pairs)[0] == 0
    pdata = load(pairs)
    pdata["pairs"][0]["mad"] = 12.0
    write(pairs, pdata)
    out = tmp_path / "rev_diff.json"
    rc, msg = run("check_revision.py", "--pairs", pairs, "--rules", pkg010_rules(tmp_path), "--out", out)
    assert rc == 0, msg
    chk = load(out)["revision_checks"][0]
    assert chk["previous_revision"] == "00"
    assert chk["current_revision"] == "00"
    assert chk["previous_revision_source"] == "revision_table"
    assert chk["result"] == "observation"
    assert chk.get("rule_id") is None


def test_r3b_renumbered_never_pkg010_fail(tmp_path):
    """R3b: renumbered pair with differing/wrong rev codes → info, never PKG-010 fail."""
    prev = write(tmp_path / "prev.json", {"drawings": [
        drawing("LDIP-RCRC-SAP-03-CC-CIV-DWG-00-11000-00", "00"),
    ]})
    curr = write(tmp_path / "curr.json", {"drawings": [
        drawing("LDIP-RCRC-SAP-03-CC-CIV-DWG-00-22000-00", "00"),
    ]})
    nmap = tmp_path / "map.csv"
    nmap.write_text(
        "old,new\nLDIP-RCRC-SAP-03-CC-CIV-DWG-00-11000,LDIP-RCRC-SAP-03-CC-CIV-DWG-00-22000\n",
        encoding="utf-8")
    pairs = tmp_path / "pairs.json"
    assert run("match_revisions.py", "--previous", prev, "--current", curr,
               "--number-map", nmap, "--out", pairs)[0] == 0
    # No content change → info (not observation, not PKG-010)
    out = tmp_path / "rev_diff.json"
    assert run("check_revision.py", "--pairs", pairs, "--rules", pkg010_rules(tmp_path), "--out", out)[0] == 0
    chk = load(out)["revision_checks"][0]
    assert chk["renumbered"] is True
    assert chk["result"] == "info"
    assert chk.get("rule_id") is None
    assert load(out)["counts"]["pkg010_fail"] == 0
    assert load(out)["counts"]["pkg010_pass"] == 0


def test_r5_resubmit_is_d(tmp_path):
    crs = crs_book(tmp_path / "crs.xlsx", [
        [20, "Doc", "Resubmit the complete coordinated grading package for the site",
         "Will be submitted", "A", "Open"],
    ])
    out = tmp_path / "closure.json"
    assert run("check_closed_comments.py", "--crs", crs, "--out", out)[0] == 0
    row = load(out)["rows"][0]
    assert row["row_type"] == "D"
    assert row["deferred_by_response"] is True


# ------------------------------------------------------------------ R5 typing
def test_r5_bare_plan_is_g_not_l(tmp_path):
    crs = crs_book(tmp_path / "crs.xlsx", [
        [1, "General", "The site plan layout detail and elevation boundary need review",
         "Noted", "A", "Open"],
        [4, "Doc", "Please provide the IFC model package for the roads", "Will be submitted", "B", "Open"],
    ])
    out = tmp_path / "closure.json"
    assert run("check_closed_comments.py", "--crs", crs, "--out", out)[0] == 0
    rows = {r["no"]: r for r in load(out)["rows"]}
    assert rows["1"]["row_type"] == "G"
    assert rows["4"]["row_type"] == "D"
    assert rows["4"]["deferred_by_response"] is True


# ------------------------------------------------------------------ R7 footer
def test_r7_skips_non_integer_footer(tmp_path):
    crs = crs_book(tmp_path / "crs.xlsx", [
        [1, "G", "Design statement for information", "OK", "A", "Open"],
        [2, "G", "Another design note", "OK", "A", "Open"],
        ["Reviewed Action: A-No Objection by PMC", "x", "y", "z", "", ""],
    ])
    out = tmp_path / "closure.json"
    assert run("check_closed_comments.py", "--crs", crs, "--out", out)[0] == 0
    assert load(out)["counts"]["rows"] == 2


# ------------------------------------------------------------------ R9 params
def test_r9_proposed_raster_params():
    params = load(REF / "params.json")
    r = params["raster"]
    assert "PROPOSED" in r["tag"] and "DM-9" in r["tag"]
    assert r["dpi"] == 25
    assert r["width"] == 400 and r["height"] == 283
    assert r["mask_right_pct"] == 0.18
    assert r["weak_margin"] == 1.5


def test_normalise_strips_space_stray_letter_and_revision(tmp_path):
    prev = write(tmp_path / "prev.json", {"drawings": [
        drawing("LDIP-RCRC-SAP-03-CC-CIV-G-DWG-00-11000-00", "00"),
    ]})
    curr = write(tmp_path / "curr.json", {"drawings": [
        drawing("LDIP-RCRC-SAP-03-CC- CIV-DWG-00-11000-01", "01"),
    ]})
    pairs = tmp_path / "pairs.json"
    rc, msg = run("match_revisions.py", "--previous", prev, "--current", curr, "--out", pairs)
    assert rc == 0 and msg["normalised"] == 1


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
    assert run("check_revision.py", "--pairs", pairs, "--rules", pkg010_rules(tmp_path), "--out", out)[0] == 0
    assert load(out)["revision_checks"][0]["result"] == "pass"


def test_pkg010_absent_not_run(tmp_path):
    prev = write(tmp_path / "prev.json", {"drawings": [drawing("A-B-C-D-E-F-DWG-00-11000-00", "00")]})
    curr = write(tmp_path / "curr.json", {"drawings": [drawing("A-B-C-D-E-F-DWG-00-11000-00", "00")]})
    pairs = tmp_path / "pairs.json"
    assert run("match_revisions.py", "--previous", prev, "--current", curr, "--out", pairs)[0] == 0
    out = tmp_path / "rev_diff.json"
    assert run("check_revision.py", "--pairs", pairs, "--rules", pkg010_rules(tmp_path, "absent"),
               "--out", out)[0] == 0
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
    assert rc == 0 and msg["proposed"] == 1
    assert load(pairs)["pairs"][0]["status"] == "proposed"


def test_types_override_cannot_tell_without_evidence(tmp_path):
    crs = crs_book(tmp_path / "crs.xlsx", [
        [1, "General", "For information only", "Addressed in full", "A", "Closed"],
    ])
    types = tmp_path / "types.csv"
    types.write_text("no,type\n1,L\n", encoding="utf-8")
    out = tmp_path / "closure.json"
    assert run("check_closed_comments.py", "--crs", crs, "--out", out, "--types", types)[0] == 0
    row = load(out)["rows"][0]
    assert row["row_type"] == "L"
    assert row["outcome"] == "Cannot tell"  # R4


def test_skill_layout_and_version():
    root = PLUGIN / "skills/comparing-drawing-revisions"
    assert (root / "SKILL.md").is_file()
    text = (root / "SKILL.md").read_text(encoding="utf-8")
    assert 'version: "0.1.0"' in text
    for name in ("match_revisions.py", "check_revision.py", "diff_entities.py",
                 "check_closed_comments.py", "raster_match.py", "revision_table.py"):
        assert (root / "scripts" / name).is_file()
    assert 3 <= len(load(root / "evals" / "evals.json")["evals"]) <= 5
