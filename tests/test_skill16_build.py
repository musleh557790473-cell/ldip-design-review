"""Skill 16 checking-design-document-consistency 0.1.0 — synthetic T16-1…T16-4 + R1–R9.

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
             ref_errors=None, ref_in_formula=False, road_area=105710, kerb=23900,
             principal_sumif=None, headers="parsons"):
    """R1 synthetic fixture — Parsons-like headers + roles; optional #REF! / SUMIF (R3)."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Earthworks"
    if headers == "parsons":
        # R1: real-ish synonyms (Item Description / Qty / Unit of Measure)
        ws.append(["Bill No.", "Item No.", "Item Description", "Unit of Measure", "Qty"])
    else:
        ws.append(["Part", "Code", "Description", "Unit", "Quantity"])
    ws.append(["1", "EWK-01", "General excavation (cut)", "m3", cut_formula])
    if fill_factor and fill_factor != 1.0:
        ws.append(["1", "EWK-02", "Suitable fill", "m3", f"={fill_base}*{fill_factor}"])
    else:
        ws.append(["1", "EWK-02", "Suitable fill", "m3", fill_base])
    ws.append(["1", "EWK-03", "Disposal off site", "m3", disposal])
    ws.append(["1", "EWK-04", "Imported fill", "m3", imp])
    ws["A10"] = 1000
    ws.append(["1", "EWK-06", "Rock portion (ratio)", "m3", f"=A10*{ratios[0]}"])
    ws.append(["1", "EWK-07", "Suitable portion (ratio)", "m3", f"=A10*{ratios[1]}"])
    ws.append(["1", "EWK-08", "Disposal portion (ratio)", "m3", f"=A10*{ratios[2]}"])

    ws2 = wb.create_sheet("Roads")
    ws2.append(["Bill No.", "Item No.", "Item Description", "Unit of Measure", "Qty"])
    ws2.append(["2", "ROD-01", "Road pavement area", "m2", f"={road_area}*1.05"])
    ws2.append(["2", "ROD-02", "Kerb length", "m", f"={kerb}*1.05"])

    ws3 = wb.create_sheet("Principal Quantities")
    ws3.append(["Bill No.", "Item No.", "Item Description", "Unit of Measure", "Qty"])
    ws3.append(["PQ", "PQ-01", "Principal excavation", "m3", cut_formula])
    if principal_sumif:
        # R3: SUMIF link — formula text only (cached value requires Excel; reader records link)
        ws3.append(["PQ", "PQ-SUM", "Principal total (SUMIF)", "m3", principal_sumif])
    if ref_errors:
        for i, addr_note in enumerate(ref_errors, start=0):
            row = 3 + i + (1 if principal_sumif else 0)
            ws3.cell(row=row, column=1, value=f"Part {addr_note}")
            ws3.cell(row=row, column=2, value=f"PQ-ERR{i}")
            ws3.cell(row=row, column=3, value="Principal quantity link")
            ws3.cell(row=row, column=4, value="m3")
            if ref_in_formula:
                # R3: #REF! inside a formula string
                ws3.cell(row=row, column=5, value="=#REF!")
            else:
                ws3.cell(row=row, column=5, value="#REF!")

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return path


def make_report_pdf_json(path, pages):
    """pages: list of (page_no, text) or (page_no, spans_list)."""
    page_objs = []
    for entry in pages:
        if len(entry) == 2 and isinstance(entry[1], str):
            page_no, text = entry
            words = text.split()
            spans = [{"text": w, "bbox": [10.0 * i, 20.0, 10.0 * i + 8, 32.0]}
                     for i, w in enumerate(words)]
        else:
            page_no, spans = entry
        page_objs.append({"page": page_no, "width_pt": 595, "height_pt": 842,
                          "spans": spans, "ocr_spans": []})
    return write(path, {"content": {"pages": page_objs}})


def schema():
    return json.loads((TPL / "consistency.schema.json").read_text(encoding="utf-8"))


def validate_consistency(doc):
    Draft202012Validator(schema()).validate(doc)
    for f in doc.get("findings") or []:
        assert f.get("reference", {}).get("rule_id") is None
        assert f.get("report_only") is True


# ------------------------------------------------------------------ T16-1 / R1 / R2
def test_t16_1_boq_base_factor_and_ratios(tmp_path):
    """T16-1 + R1/R2: Parsons headers; base 5,065,345; factor 1.05; ratios; dual_pass."""
    xlsx = make_boq(tmp_path / "boq.xlsx")
    out = tmp_path / "boq.json"
    rc, msg = run("read_boq.py", "--xlsx", xlsx, "--out", out,
                  "--document-no", "LDIP-RCRC-SAP-01-QRWN-BOQ-00001")
    assert rc == 0, msg
    assert msg.get("dual_pass") is True
    boq = load(out)
    assert boq["source"]["document_no"] == "LDIP-RCRC-SAP-01-QRWN-BOQ-00001"
    assert boq["source"]["dual_pass"] is True
    cut = boq["summary"]["earthworks"]["cut"]
    assert cut["base"] == pytest.approx(5065345)
    assert cut["factor"] == pytest.approx(1.05)
    assert "excavation" in (cut["description"] or "").lower() or "cut" in (cut["description"] or "").lower()
    # Imported fill must not also be role fill (R1)
    imp = boq["summary"]["earthworks"]["import"]
    assert imp is not None and imp["base"] == pytest.approx(120000)
    ratios = set(boq["summary"]["ratios_recorded"])
    assert {0.2, 0.8, 0.6}.issubset(ratios)

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
    assert any(r["result"] == "observation" for r in c2)
    assert any("basis not stated" in (r.get("note") or "") for r in c2)


# ------------------------------------------------------------------ T16-2 / R3
def test_t16_2_ref_error_c5(tmp_path):
    """T16-2 + R3: #REF! as value and inside formula → C5 with cell address."""
    xlsx = make_boq(tmp_path / "boq.xlsx", ref_errors=[6, 7, 8, 9, 10], ref_in_formula=True)
    out = tmp_path / "boq.json"
    rc, msg = run("read_boq.py", "--xlsx", xlsx, "--out", out)
    assert rc == 0, msg
    boq = load(out)
    assert len(boq["summary"]["cell_errors"]) >= 5
    assert any(e.get("kind") == "formula_ref_error" or e.get("formula") for e in boq["summary"]["cell_errors"])

    cons = tmp_path / "consistency.json"
    rc, msg = run("check_consistency.py", "--boq", out, "--out", cons)
    assert rc == 0, msg
    doc = load(cons)
    validate_consistency(doc)
    c5 = [r for r in doc["comparisons"] if r["check"] == "C5" and r["result"] == "flag"]
    assert c5
    assert any(r.get("proposed_status") == "Confirmed" for r in c5)
    # R8 document_no on findings
    assert all(f["location"]["document_no"] for f in doc["findings"])


def test_r3_principal_sumif_recorded(tmp_path):
    """R3: principal SUMIF/SUM link is recorded for C5 comparison."""
    xlsx = make_boq(tmp_path / "boq.xlsx", principal_sumif="=SUMIF(Earthworks!C:C,\"*excav*\",Earthworks!E:E)")
    out = tmp_path / "boq.json"
    rc, msg = run("read_boq.py", "--xlsx", xlsx, "--out", out)
    assert rc == 0, msg
    boq = load(out)
    assert boq["summary"]["sum_links"], boq["summary"]
    assert any("SUMIF" in (s.get("kind") or s.get("formula", "")).upper()
               for s in boq["summary"]["sum_links"])


# ------------------------------------------------------------------ T16-3 / R4 / R9
def test_t16_3_balanced_observation(tmp_path):
    """T16-3 + R9: balanced in earthworks section + BOQ disposal/import → C3 Observation."""
    xlsx = make_boq(tmp_path / "boq.xlsx", disposal=500000, imp=80000)
    boq_path = tmp_path / "boq.json"
    rc, msg = run("read_boq.py", "--xlsx", xlsx, "--out", boq_path)
    assert rc == 0, msg

    pdf = make_report_pdf_json(tmp_path / "report.pdf.json", [
        (1, "Contents Earthworks .............. 3 Pavement ................ 8"),
        (3, "3.1 Earthworks The cut and fill are balanced across the site."),
        (8, "8 Pavement Total pavement thickness 550 mm from wearing surface to ToS."),
    ])
    claims_path = tmp_path / "report_claims.json"
    rc, msg = run("read_report.py", "--text", pdf, "--out", claims_path)
    assert rc == 0, msg
    claims = load(claims_path)
    assert 1 in claims.get("toc_pages", []) or claims["toc_pages"] == [1]
    assert "balanced" in claims["families_present"]
    bal = [c for c in claims["claims"] if c["family"] == "balanced"]
    assert bal and bal[0].get("in_earthworks_section") is True
    assert "earthworks" in [s["section"] for s in claims["sections"]]

    cons = tmp_path / "consistency.json"
    rc, msg = run("check_consistency.py", "--boq", boq_path, "--report", claims_path, "--out", cons)
    assert rc == 0, msg
    doc = load(cons)
    validate_consistency(doc)
    assert any(r["check"] == "C3" and r["result"] == "observation" for r in doc["comparisons"])


def test_r9_balanced_outside_earthworks_ignored(tmp_path):
    """R9: 'balanced' in a non-earthworks section does not drive C3."""
    xlsx = make_boq(tmp_path / "boq.xlsx", disposal=500000, imp=80000)
    boq_path = tmp_path / "boq.json"
    run("read_boq.py", "--xlsx", xlsx, "--out", boq_path)
    pdf = make_report_pdf_json(tmp_path / "report.pdf.json", [
        (2, "2 Earthworks Quantities are summarised below."),
        (5, "5 Drainage The network is balanced for peak flow."),
    ])
    claims_path = tmp_path / "claims.json"
    rc, msg = run("read_report.py", "--text", pdf, "--out", claims_path)
    assert rc == 0, msg
    claims = load(claims_path)
    assert "balanced" not in claims["families_present"]
    cons = tmp_path / "cons.json"
    rc, msg = run("check_consistency.py", "--boq", boq_path, "--report", claims_path, "--out", cons)
    assert rc == 0, msg
    doc = load(cons)
    assert not any(r["check"] == "C3" and r["result"] == "observation" for r in doc["comparisons"])


def test_r4_basis_ratio_needs_context(tmp_path):
    """R4: bare 0.2 / 0.8 without rock/suitable/disposal context is not basis_ratio."""
    pdf = make_report_pdf_json(tmp_path / "r.pdf.json", [
        (2, "2 Earthworks Progress is 0.2 complete and 0.8 remains."),
        (3, "3 Earthworks Rock 0.2 suitable 0.8 disposal 0.6 split applied."),
    ])
    out = tmp_path / "claims.json"
    rc, msg = run("read_report.py", "--text", pdf, "--out", out)
    assert rc == 0, msg
    claims = load(out)
    basis = [c for c in claims["claims"] if c["family"] == "basis_ratio"]
    assert basis, claims["claims"]
    assert all(c["page"] == 3 for c in basis)


# ------------------------------------------------------------------ T16-4
def test_t16_4_pavement_depth_pass_and_flag(tmp_path):
    """T16-4: surfaces 0.55 m apart → pass; 0.40 m → flag (C4)."""
    xlsx = make_boq(tmp_path / "boq.xlsx", disposal=0, imp=0)
    boq_path = tmp_path / "boq.json"
    run("read_boq.py", "--xlsx", xlsx, "--out", boq_path)

    pdf = make_report_pdf_json(tmp_path / "report.pdf.json", [
        (2, "2 Earthworks Site grading overview."),
        (4, "4 Pavement design. Total pavement thickness 550 mm from wearing surface to ToS."),
    ])
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
        else:
            assert any(r["result"] == "pass" for r in c4)
            assert not any((f.get("reference") or {}).get("clause") == "C4" for f in doc["findings"])

    check_with_median(0.55, expect_flag=False)
    check_with_median(0.40, expect_flag=True)


# ------------------------------------------------------------------ R5 bbox order
def test_r5_bbox_reading_order(tmp_path):
    """R5: out-of-order spans are read top→bottom, left→right."""
    # Spans deliberately shuffled: "balanced" words placed with y coords out of sequence
    spans = [
        {"text": "fill", "bbox": [40, 40, 60, 50]},
        {"text": "3.1", "bbox": [0, 10, 20, 20]},
        {"text": "Earthworks", "bbox": [25, 10, 80, 20]},
        {"text": "are", "bbox": [70, 40, 90, 50]},
        {"text": "The", "bbox": [0, 40, 20, 50]},
        {"text": "cut", "bbox": [22, 40, 38, 50]},
        {"text": "and", "bbox": [38, 40, 52, 50]},
        {"text": "balanced", "bbox": [92, 40, 130, 50]},
    ]
    pdf = make_report_pdf_json(tmp_path / "bbox.pdf.json", [(3, spans)])
    out = tmp_path / "claims.json"
    rc, msg = run("read_report.py", "--text", pdf, "--out", out)
    assert rc == 0, msg
    claims = load(out)
    assert "balanced" in claims["families_present"]
    assert any(c.get("in_earthworks_section") for c in claims["claims"] if c["family"] == "balanced")


# ------------------------------------------------------------------ R7 C1 three pairs
def test_r7_c1_three_pairs(tmp_path):
    """R7: when BOQ + model + drawing present, emit boq↔model, boq↔drawing, model↔drawing."""
    xlsx = make_boq(tmp_path / "boq.xlsx", cut_formula="=5065345*1.05",
                    fill_base=3748575, fill_factor=1.05, disposal=0, imp=0)
    boq_path = tmp_path / "boq.json"
    run("read_boq.py", "--xlsx", xlsx, "--out", boq_path)
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
    labels = {r["quantity"] for r in c1}
    assert "cut_boq_vs_model" in labels
    assert "cut_boq_vs_drawing" in labels
    assert "cut_model_vs_drawing" in labels
    assert "fill_boq_vs_model" in labels
    assert "fill_boq_vs_drawing" in labels
    assert "fill_model_vs_drawing" in labels


# ------------------------------------------------------------------ C6 never BOQ vs itself
def test_c6_information_at_30_finding_at_60(tmp_path):
    xlsx = make_boq(tmp_path / "boq.xlsx", road_area=105710, kerb=23900)
    boq_path = tmp_path / "boq.json"
    run("read_boq.py", "--xlsx", xlsx, "--out", boq_path)
    reg = write(tmp_path / "register.json", {
        "source": "skill10",
        "independent": True,
        "roads": {"road_area_m2": 90000.0, "kerb_length_m": 20000.0},
    })
    cons30 = tmp_path / "c30.json"
    rc, msg = run("check_consistency.py", "--boq", boq_path, "--road-register", reg,
                  "--stage", "30% Concept Design", "--out", cons30)
    assert rc == 0, msg
    doc30 = load(cons30)
    c6_30 = [r for r in doc30["comparisons"] if r["check"] == "C6"]
    assert c6_30 and all(r["result"] == "information" for r in c6_30)

    cons60 = tmp_path / "c60.json"
    rc, msg = run("check_consistency.py", "--boq", boq_path, "--road-register", reg,
                  "--stage", "60% Detailed Design", "--out", cons60)
    assert rc == 0, msg
    doc60 = load(cons60)
    assert any(r["check"] == "C6" and r["result"] == "flag" for r in doc60["comparisons"])


def test_c6_refuses_boq_vs_itself(tmp_path):
    """C6: register sourced from BOQ / missing independent provenance → not_run."""
    xlsx = make_boq(tmp_path / "boq.xlsx", road_area=105710, kerb=23900)
    boq_path = tmp_path / "boq.json"
    run("read_boq.py", "--xlsx", xlsx, "--out", boq_path)
    # Self: source=boq
    reg = write(tmp_path / "self.json", {
        "source": "boq",
        "roads": {"road_area_m2": 105710.0, "kerb_length_m": 23900.0},
    })
    cons = tmp_path / "c.json"
    rc, msg = run("check_consistency.py", "--boq", boq_path, "--road-register", reg,
                  "--stage", "30% Concept Design", "--out", cons)
    assert rc == 0, msg
    doc = load(cons)
    c6 = [r for r in doc["comparisons"] if r["check"] == "C6"]
    assert c6 and all(r["result"] == "not_run" for r in c6)
    assert any("never BOQ vs itself" in (r.get("note") or "") for r in c6)


# ------------------------------------------------------------------ R8 params + layout
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


def test_params_dn5_defaults():
    """R8: DN-5 tag present on params."""
    params = load(REF / "params.json")
    assert params["tolerances"]["quantity_pct"] == 0.10
    assert params["tolerances"]["quantity_abs_m3"] == 1000.0
    assert params["tolerances"]["c4_pavement_abs_m"] == 0.05
    assert params["stage"]["c6_finding_from_pct"] == 60
    tag = params.get("tag") or params["tolerances"].get("tag")
    assert tag and "DN-5" in tag and "PROPOSED" in tag
