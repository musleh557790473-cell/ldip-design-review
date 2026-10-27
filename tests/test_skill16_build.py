"""Skill 16 checking-design-document-consistency 0.1.0 — synthetic T16-1…T16-4.

SPC-0006 Rev N §4.16 (DN-1…DN-8). No real project data.
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
SK = PLUGIN / "skills/checking-design-document-consistency/scripts"
REF = PLUGIN / "skills/checking-design-document-consistency/references"
TPL = PLUGIN / "skills/checking-design-document-consistency/templates"
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


def make_boq(path, *, cut_formula="=5065345*1.05", fill_base=3748575, fill_factor=1.05,
             disposal=800000, imp=120000, ratios=(0.2, 0.8, 0.6),
             ref_errors=None, road_area=105710, kerb=23900):
    """Synthetic roads/earthworks BOQ with base×factor, ratios, optional #REF!."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Earthworks"
    ws.append(["Part", "Code", "Description", "Unit", "Quantity"])
    ws.append(["1", "EWK-01", "Bulk excavation (cut)", "m3", cut_formula])
    # fill as base×factor formula when factor given
    if fill_factor and fill_factor != 1.0:
        ws.append(["1", "EWK-02", "Suitable fill", "m3", f"={fill_base}*{fill_factor}"])
    else:
        ws.append(["1", "EWK-02", "Suitable fill", "m3", fill_base])
    ws.append(["1", "EWK-03", "Disposal off site", "m3", disposal])
    ws.append(["1", "EWK-04", "Imported fill", "m3", imp])
    ws.append(["1", "EWK-05", "Rock excavation", "m3", f"=A10*{ratios[0]}"])  # placeholder ratio form
    # Explicit ratio formulas referencing a base cell
    ws["A10"] = 1000
    ws.append(["1", "EWK-06", "Rock portion (ratio)", "m3", f"=A10*{ratios[0]}"])
    ws.append(["1", "EWK-07", "Suitable portion (ratio)", "m3", f"=A10*{ratios[1]}"])
    ws.append(["1", "EWK-08", "Disposal portion (ratio)", "m3", f"=A10*{ratios[2]}"])

    ws2 = wb.create_sheet("Roads")
    ws2.append(["Part", "Code", "Description", "Unit", "Quantity"])
    ws2.append(["2", "ROD-01", "Road pavement area", "m2", f"={road_area}*1.05"])
    ws2.append(["2", "ROD-02", "Kerb length", "m", f"={kerb}*1.05"])

    ws3 = wb.create_sheet("Principal Quantities")
    ws3.append(["Part", "Code", "Description", "Unit", "Quantity"])
    ws3.append(["PQ", "PQ-01", "Principal excavation", "m3", cut_formula])
    if ref_errors:
        for i, addr_note in enumerate(ref_errors, start=2):
            # Write literal #REF! as cell value (openpyxl stores as string)
            ws3.cell(row=i + 1, column=1, value=f"Part {addr_note}")
            ws3.cell(row=i + 1, column=2, value=f"PQ-ERR{i}")
            ws3.cell(row=i + 1, column=3, value="Principal quantity link")
            ws3.cell(row=i + 1, column=4, value="m3")
            ws3.cell(row=i + 1, column=5, value="#REF!")

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return path


def make_report_pdf_json(path, text, page=1):
    """Minimal skill-2 shaped PDF JSON."""
    words = text.split()
    spans = [{"text": w, "bbox": [0, 0, 10, 10]} for w in words]
    doc = {
        "content": {
            "pages": [{"page": page, "width_pt": 595, "height_pt": 842, "spans": spans, "ocr_spans": []}]
        }
    }
    return write(path, doc)


def schema():
    return json.loads((TPL / "consistency.schema.json").read_text(encoding="utf-8"))


def validate_consistency(doc):
    Draft202012Validator(schema()).validate(doc)
    for f in doc.get("findings") or []:
        assert f.get("reference", {}).get("rule_id") is None
        assert f.get("report_only") is True


# ------------------------------------------------------------------ T16-1
def test_t16_1_boq_base_factor_and_ratios(tmp_path):
    """T16-1: base 5,065,345, factor 1.05, ratios 0.2/0.8/0.6 recorded (C2)."""
    xlsx = make_boq(tmp_path / "boq.xlsx")
    out = tmp_path / "boq.json"
    rc, msg = run("read_boq.py", "--xlsx", xlsx, "--out", out)
    assert rc == 0, msg
    boq = load(out)
    cut = boq["summary"]["earthworks"]["cut"]
    assert cut["base"] == pytest.approx(5065345)
    assert cut["factor"] == pytest.approx(1.05)
    assert cut["quantity"] == pytest.approx(5065345 * 1.05)
    ratios = set(boq["summary"]["ratios_recorded"])
    assert {0.2, 0.8, 0.6}.issubset(ratios)

    # C2 observation when report omits basis
    report = write(tmp_path / "report_claims.json", {
        "report_claims_version": "1.0", "sections": [], "claims": [],
        "families_present": [], "pages_read": 1,
    })
    cons = tmp_path / "consistency.json"
    rc, msg = run("check_consistency.py", "--boq", out, "--report", report,
                  "--stage", "30% Concept Design", "--out", cons)
    assert rc == 0, msg
    doc = load(cons)
    validate_consistency(doc)
    c2 = [r for r in doc["comparisons"] if r["check"] == "C2"]
    assert c2, doc["comparisons"]
    assert any(r["result"] == "observation" for r in c2)
    assert any("basis not stated" in (r.get("note") or "") for r in c2)


# ------------------------------------------------------------------ T16-2
def test_t16_2_ref_error_c5(tmp_path):
    """T16-2: #REF! principal-quantity cell → C5 row with cell address."""
    xlsx = make_boq(tmp_path / "boq.xlsx", ref_errors=[6, 7, 8, 9, 10])
    out = tmp_path / "boq.json"
    rc, msg = run("read_boq.py", "--xlsx", xlsx, "--out", out)
    assert rc == 0, msg
    boq = load(out)
    assert len(boq["summary"]["cell_errors"]) >= 5
    assert all(e["value"] == "#REF!" for e in boq["summary"]["cell_errors"])

    cons = tmp_path / "consistency.json"
    rc, msg = run("check_consistency.py", "--boq", out, "--out", cons)
    assert rc == 0, msg
    doc = load(cons)
    validate_consistency(doc)
    c5 = [r for r in doc["comparisons"] if r["check"] == "C5" and r["result"] == "flag"]
    assert c5, doc["comparisons"]
    assert any("Principal Quantities" in str(r.get("values")) or
               "Principal Quantities" in str(r.get("evidence")) for r in c5)
    # Confirmed allowed for xlsx_cell
    assert any(r.get("proposed_status") == "Confirmed" for r in c5)
    assert any(f.get("proposed_status") == "Confirmed" for f in doc["findings"]
               if "C5" in (f.get("reference") or {}).get("clause", ""))


# ------------------------------------------------------------------ T16-3
def test_t16_3_balanced_observation(tmp_path):
    """T16-3: report 'balanced' + BOQ disposal/import → C3 Observation."""
    xlsx = make_boq(tmp_path / "boq.xlsx", disposal=500000, imp=80000)
    boq_path = tmp_path / "boq.json"
    rc, msg = run("read_boq.py", "--xlsx", xlsx, "--out", boq_path)
    assert rc == 0, msg

    pdf = make_report_pdf_json(
        tmp_path / "report.pdf.json",
        "Earthworks 3.1 The cut and fill are balanced across the site. Pavement thickness 550 mm.",
    )
    claims_path = tmp_path / "report_claims.json"
    rc, msg = run("read_report.py", "--text", pdf, "--out", claims_path)
    assert rc == 0, msg
    claims = load(claims_path)
    assert "balanced" in claims["families_present"]
    assert "earthworks" in [s["section"] for s in claims["sections"]]

    cons = tmp_path / "consistency.json"
    rc, msg = run("check_consistency.py", "--boq", boq_path, "--report", claims_path,
                  "--out", cons)
    assert rc == 0, msg
    doc = load(cons)
    validate_consistency(doc)
    c3 = [r for r in doc["comparisons"] if r["check"] == "C3"]
    assert any(r["result"] == "observation" for r in c3)
    assert any(f.get("severity") == "Observation" for f in doc["findings"]
               if (f.get("reference") or {}).get("clause") == "C3")


# ------------------------------------------------------------------ T16-4
def test_t16_4_pavement_depth_pass_and_flag(tmp_path):
    """T16-4: surfaces 0.55 m apart → pass; 0.40 m → flag (C4)."""
    xlsx = make_boq(tmp_path / "boq.xlsx", disposal=0, imp=0)
    boq_path = tmp_path / "boq.json"
    run("read_boq.py", "--xlsx", xlsx, "--out", boq_path)

    pdf = make_report_pdf_json(
        tmp_path / "report.pdf.json",
        "Pavement design. Total pavement thickness 550 mm from wearing surface to ToS.",
    )
    claims_path = tmp_path / "report_claims.json"
    rc, msg = run("read_report.py", "--text", pdf, "--out", claims_path)
    assert rc == 0, msg
    assert any(c.get("value_m") == pytest.approx(0.55) for c in load(claims_path)["claims"]
               if c["family"] == "pavement_depth")

    def check_with_median(median, expect_flag):
        profile = write(tmp_path / f"profile_{median}.json", {
            "cut_fill": {"cut_volume_m3": 5.0e6, "fill_volume_m3": 3.5e6},
            "road_top_minus_grading": {
                "n": 100, "min": median - 0.02, "max": median + 0.02,
                "median": median, "p5": median - 0.02, "p95": median + 0.02,
                "method": "tin_raster_z",
            },
            "note": "synthetic — DN-3 no recompute",
        })
        cons = tmp_path / f"cons_{median}.json"
        rc, msg = run("check_consistency.py", "--boq", boq_path, "--report", claims_path,
                      "--profile", profile, "--out", cons)
        assert rc == 0, msg
        doc = load(cons)
        validate_consistency(doc)
        c4 = [r for r in doc["comparisons"] if r["check"] == "C4"]
        assert c4
        if expect_flag:
            assert any(r["result"] == "flag" for r in c4)
            assert any((f.get("reference") or {}).get("clause") == "C4" for f in doc["findings"])
        else:
            assert any(r["result"] == "pass" for r in c4)
            assert not any((f.get("reference") or {}).get("clause") == "C4" for f in doc["findings"])

    check_with_median(0.55, expect_flag=False)
    check_with_median(0.40, expect_flag=True)


# ------------------------------------------------------------------ extras (layout / DN / C1 / C6)
def test_skill_layout_and_version():
    skill = PLUGIN / "skills/checking-design-document-consistency"
    assert (skill / "SKILL.md").is_file()
    text = (skill / "SKILL.md").read_text(encoding="utf-8")
    assert 'version: "0.1.0"' in text
    assert "Rev N §4.16" in text
    assert (skill / "scripts/read_boq.py").is_file()
    assert (skill / "scripts/read_report.py").is_file()
    assert (skill / "scripts/check_consistency.py").is_file()
    assert (skill / "references/claims.md").is_file()
    assert (skill / "references/params.json").is_file()
    assert (skill / "templates/consistency.schema.json").is_file()
    assert (skill / "evals/evals.json").is_file()
    evals = load(skill / "evals/evals.json")
    assert 3 <= len(evals["evals"]) <= 5


def test_dn4_no_rule_id_and_dn7_c7_absent(tmp_path):
    xlsx = make_boq(tmp_path / "boq.xlsx", ref_errors=[1])
    boq_path = tmp_path / "boq.json"
    run("read_boq.py", "--xlsx", xlsx, "--out", boq_path)
    cons = tmp_path / "consistency.json"
    rc, msg = run("check_consistency.py", "--boq", boq_path, "--out", cons)
    assert rc == 0, msg
    doc = load(cons)
    assert doc["c7"]["built"] is False
    assert all(f["reference"]["rule_id"] is None for f in doc["findings"])
    assert "Report only" in doc["meta"]["marking"]


def test_c1_boq_base_vs_model_tolerance(tmp_path):
    """C1 flags when BOQ base vs model exceeds 10% and 1000 m³ (DN-5 / DN-6)."""
    xlsx = make_boq(tmp_path / "boq.xlsx", cut_formula="=5065345*1.05",
                    fill_base=3748575, fill_factor=1.05, disposal=0, imp=0)
    boq_path = tmp_path / "boq.json"
    run("read_boq.py", "--xlsx", xlsx, "--out", boq_path)
    # Model far from BOQ base (uses ~4.82M / 3.23M style gap)
    profile = write(tmp_path / "profile.json", {
        "cut_fill": {"cut_volume_m3": 4820000.0, "fill_volume_m3": 3230000.0},
    })
    drawing = write(tmp_path / "table.json", {"cut": 7567.55, "fill": 371513.95})
    cons = tmp_path / "consistency.json"
    rc, msg = run("check_consistency.py", "--boq", boq_path, "--profile", profile,
                  "--drawing-table", drawing, "--out", cons)
    assert rc == 0, msg
    doc = load(cons)
    validate_consistency(doc)
    c1 = [r for r in doc["comparisons"] if r["check"] == "C1"]
    assert c1
    # BOQ base vs model should flag (large absolute + pct)
    assert any(r["result"] == "flag" and "model" in r["quantity"] for r in c1)
    # Factor recorded, base used
    flagged = next(r for r in c1 if r["result"] == "flag")
    assert flagged["values"].get("boq_base") == pytest.approx(5065345) or \
        any(r["values"].get("boq_base") == pytest.approx(5065345) for r in c1)


def test_c6_information_at_30_finding_at_60(tmp_path):
    xlsx = make_boq(tmp_path / "boq.xlsx", road_area=105710, kerb=23900)
    boq_path = tmp_path / "boq.json"
    run("read_boq.py", "--xlsx", xlsx, "--out", boq_path)
    reg = write(tmp_path / "register.json", {
        "roads": {"road_area_m2": 90000.0, "kerb_length_m": 20000.0},
    })
    cons30 = tmp_path / "c30.json"
    rc, msg = run("check_consistency.py", "--boq", boq_path, "--road-register", reg,
                  "--stage", "30% Concept Design", "--out", cons30)
    assert rc == 0, msg
    doc30 = load(cons30)
    c6_30 = [r for r in doc30["comparisons"] if r["check"] == "C6"]
    assert c6_30 and all(r["result"] == "information" for r in c6_30)
    assert not any((f.get("reference") or {}).get("clause") == "C6" for f in doc30["findings"])

    cons60 = tmp_path / "c60.json"
    rc, msg = run("check_consistency.py", "--boq", boq_path, "--road-register", reg,
                  "--stage", "60% Detailed Design", "--out", cons60)
    assert rc == 0, msg
    doc60 = load(cons60)
    c6_60 = [r for r in doc60["comparisons"] if r["check"] == "C6"]
    assert any(r["result"] == "flag" for r in c6_60)
    assert any((f.get("reference") or {}).get("clause") == "C6" for f in doc60["findings"])


def test_params_dn5_defaults():
    params = load(REF / "params.json")
    assert params["tolerances"]["quantity_pct"] == 0.10
    assert params["tolerances"]["quantity_abs_m3"] == 1000.0
    assert params["tolerances"]["c4_pavement_abs_m"] == 0.05
    assert params["stage"]["c6_finding_from_pct"] == 60
