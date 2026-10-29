"""Skill 14 0.1.0 — synthetic T14-1…T14-5 + nested INSERT raw proof (G-O1).

SPC-0006 Rev O §4.17. No real project data.
"""
from __future__ import annotations

import json
import math
import subprocess
import sys
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

REPO = Path(__file__).resolve().parents[1]
PLUGIN = REPO / "plugin/ldip-design-review"
SK = PLUGIN / "skills/coordinating-utility-corridors/scripts"
REF = PLUGIN / "skills/coordinating-utility-corridors/references"
TPL = PLUGIN / "skills/coordinating-utility-corridors/templates"
PY = sys.executable

REQUIRED = [
    "water", "sewer", "storm", "irrigation", "mv", "lv", "telecom", "street_lighting",
]


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


def _band(centre, width, layer, util, cls, curb_left, curb_right, cl_x=0.0, power=False):
    """Build a utility band dict with signed curb offset (verge + / carriageway −)."""
    half = width / 2.0
    x_min, x_max = centre - half, centre + half
    # Insert form [x − sx, x] → x = x_max, sx = width
    if centre >= cl_x:
        offset = centre - curb_right
        side = "right"
    else:
        offset = curb_left - centre  # + verge left of left curb
        side = "left"
    return {
        "x_min": x_min, "x_max": x_max, "centre": centre, "width": width,
        "offset_to_curb": round(offset, 6), "side": side,
        "layer": layer, "handle": f"H-{util}", "class": cls, "power": power,
    }


def make_section(label, utilities, curb=3.5, cl_x=0.0):
    return {
        "label": label,
        "cl_x": cl_x,
        "curb_left": -curb,
        "curb_right": curb,
        "utilities": utilities,
        "band_count": len(utilities),
    }


def full_wet_dry(curb=3.5, water_centre=None):
    """Wet on right verge, dry on left — C3 pass. Water at curb+1.5 by default."""
    cl_x = 0.0
    w_c = water_centre if water_centre is not None else curb + 1.5
    utils = {
        "water": _band(w_c, 0.85, "PWT-WATER", "water", "wet", -curb, curb, cl_x),
        "storm": _band(curb + 2.5, 0.5, "STO-STORM", "storm", "wet", -curb, curb, cl_x),
        "sewer": _band(curb + 3.0, 0.5, "SWN-SEWER", "sewer", "wet", -curb, curb, cl_x),
        "irrigation": _band(curb + 0.8, 0.4, "IRR-IRRIGATION", "irrigation", "wet", -curb, curb, cl_x),
        "mv": _band(-(curb + 1.2), 0.5, "EMV-MV", "mv", "dry", -curb, curb, cl_x, power=True),
        "lv": _band(-(curb + 1.8), 0.4, "ELV-LV", "lv", "dry", -curb, curb, cl_x, power=True),
        "telecom": _band(-(curb + 2.3), 0.3, "TEL-TELECOM", "telecom", "dry", -curb, curb, cl_x),
        "street_lighting": _band(-(curb + 2.8), 0.3, "SL-LIGHTING", "street_lighting", "dry", -curb, curb, cl_x),
    }
    return make_section("A1", utils, curb=curb, cl_x=cl_x)


def sections_doc(sections, route="synthetic", status_cap=None):
    return {
        "sections_version": "1.0",
        "route": route,
        "source": {"path": "synthetic"},
        "status_cap": status_cap,
        "sections": sections if isinstance(sections, list) else [sections],
        "warnings": [],
        "meta": {},
    }


def check(tmp_path, sec_doc, **kw):
    sec_path = write(tmp_path / "sections.json", sec_doc)
    out = tmp_path / "corridors.json"
    args = ["--sections", sec_path, "--out", out, "--site", kw.get("site", "NMAR"),
            "--sub", kw.get("sub", "SYN"), "--stage", "30% Concept Design"]
    if kw.get("plan"):
        plan_path = write(tmp_path / "plan.json", kw["plan"])
        args += ["--plan", plan_path]
    rc, msg = run("check_corridors.py", *args)
    assert rc == 0, msg
    doc = load(out)
    Draft202012Validator(load(TPL / "corridors.schema.json")).validate(doc)
    return doc, msg


# --------------------------------------------------------------------------- T14-1
def test_t14_1_wet_dry_water_offset(tmp_path):
    """T14-1: wet one side, dry the other, water at +1.5 → C3 pass; C2 row +1.5."""
    sec = full_wet_dry(curb=3.5, water_centre=3.5 + 1.5)
    assert abs(sec["utilities"]["water"]["offset_to_curb"] - 1.5) < 1e-9
    doc, _ = check(tmp_path, sections_doc(sec))
    c3 = [r for r in doc["comparisons"] if r["check"] == "C3"]
    assert c3 and all(r["result"] == "pass" for r in c3)
    c2_water = [r for r in doc["comparisons"]
                if r["check"] == "C2" and r["values"].get("utility") == "water"]
    assert c2_water and abs(c2_water[0]["values"]["offset_to_curb_m"] - 1.5) < 1e-9
    assert c2_water[0]["result"] == "information"  # DO-5: no value finding
    assert all(f.get("reference", {}).get("rule_id") is None for f in doc["findings"])
    assert all(f.get("report_only") is True for f in doc["findings"])
    assert all(f.get("agent") == "COO" for f in doc["findings"])


# --------------------------------------------------------------------------- T14-2
def test_t14_2_dry_both_verges_c3_flag(tmp_path):
    """T14-2: dry on both verges (shares wet side) → C3 flag."""
    sec = full_wet_dry(curb=3.5)
    # Add dry LV also on the wet (right) side
    sec["utilities"]["lv"] = _band(3.5 + 0.6, 0.4, "ELV-LV", "lv", "dry", -3.5, 3.5, power=True)
    doc, _ = check(tmp_path, sections_doc(sec))
    c3 = [r for r in doc["comparisons"] if r["check"] == "C3"]
    assert c3 and c3[0]["result"] == "flag"
    assert doc["counts"]["c3_flags"] == 1
    flags = [f for f in doc["findings"] if f["reference"]["clause"] == "C3"]
    assert len(flags) == 1
    assert flags[0]["issue_type"] == "design_error"
    assert flags[0]["severity"] == "Major"
    assert flags[0]["proposed_status"] == "Probable"


# --------------------------------------------------------------------------- T14-3
def test_t14_3_missing_storm(tmp_path):
    """T14-3: no storm band → C1 missing_information (storm)."""
    sec = full_wet_dry(curb=3.5)
    del sec["utilities"]["storm"]
    doc, _ = check(tmp_path, sections_doc(sec))
    missing = [r for r in doc["comparisons"]
               if r["check"] == "C1" and r["result"] == "flag"
               and r["values"].get("utility") == "storm"]
    assert missing
    finds = [f for f in doc["findings"]
             if f["issue_type"] == "missing_information" and "storm" in f["title"]]
    assert finds and finds[0]["severity"] == "Minor"
    assert finds[0]["proposed_status"] == "Missing information"
    assert finds[0]["reference"]["rule_id"] is None


# --------------------------------------------------------------------------- T14-4
def test_t14_4_lv_water_spacing_nav(tmp_path):
    """T14-4: LV 0.6 m touching water 0.85 m → c-c 0.725, max 1.45 → NAV."""
    # Water [0.6, 1.45] centre 1.025; LV [0.0, 0.6] centre 0.3; cc=0.725; far=1.45
    curb = 0.0  # place both on right of CL with curb at 0 so offsets are informational
    utils = {
        "water": _band(1.025, 0.85, "PWT-WATER", "water", "wet", -10, 0.0),
        "lv": _band(0.3, 0.6, "ELV-LV", "lv", "dry", -10, 0.0, power=True),
        # fill required so C1 is quiet enough; put dry leftovers on left
        "sewer": _band(-2.0, 0.4, "SWN-SEWER", "sewer", "wet", -10, 0.0),
        "storm": _band(-1.5, 0.4, "STO-STORM", "storm", "wet", -10, 0.0),
        "irrigation": _band(-1.0, 0.3, "IRR-IRRIGATION", "irrigation", "wet", -10, 0.0),
        "mv": _band(-3.0, 0.4, "EMV-MV", "mv", "dry", -10, 0.0, power=True),
        "telecom": _band(-3.5, 0.3, "TEL-TELECOM", "telecom", "dry", -10, 0.0),
        "street_lighting": _band(-4.0, 0.3, "SL-LIGHTING", "street_lighting", "dry", -10, 0.0),
    }
    # Fix water/lv geometry exactly (override _band rounding of edges)
    utils["water"].update({"x_min": 0.6, "x_max": 1.45, "centre": 1.025, "width": 0.85})
    utils["lv"].update({"x_min": 0.0, "x_max": 0.6, "centre": 0.3, "width": 0.6})
    sec = make_section("A1", utils, curb=0.0)
    doc, _ = check(tmp_path, sections_doc(sec))
    row = next(r for r in doc["comparisons"]
               if r["check"] == "C4" and r["values"].get("power") == "lv"
               and r["values"].get("other") == "water")
    assert abs(row["values"]["centre_to_centre_m"] - 0.725) < 1e-6
    assert abs(row["values"]["max_edge_m"] - 1.45) < 1e-6
    assert row["proposed_status"] == "Not auto-verifiable"
    nav = [f for f in doc["findings"] if f["proposed_status"] == "Not auto-verifiable"]
    assert nav and nav[0]["reference"]["clause"] == "C4"


# --------------------------------------------------------------------------- T14-5
def test_t14_5_pdf_status_capped_probable(tmp_path):
    """T14-5: PDF route → every finding status capped at Probable."""
    # Build a tiny PDF with a legend colour rect, then also force a C3 conflict
    # via sections-json with status_cap=Probable (reader sets this on --pdf).
    reportlab = pytest.importorskip("reportlab")
    from reportlab.pdfgen import canvas
    from reportlab.lib.pagesizes import A4

    pdf_path = tmp_path / "section.pdf"
    c = canvas.Canvas(str(pdf_path), pagesize=A4)
    c.setFillColorRGB(0, 0, 1)  # water
    c.rect(200, 400, 40, 20, fill=1, stroke=0)
    c.setFillColorRGB(1, 0, 0)  # mv
    c.rect(260, 400, 30, 20, fill=1, stroke=0)
    c.setFillColorRGB(0, 0, 0)
    c.drawString(100, 500, "SECTION A1")
    c.drawString(100, 480, "LEGEND Water Storm")
    c.save()

    out_sec = tmp_path / "sections_pdf.json"
    rc, msg = run("read_corridor_sections.py", "--pdf", pdf_path, "--site", "NMAR",
                  "--out", out_sec)
    assert rc == 0, msg
    sec_doc = load(out_sec)
    assert sec_doc["route"] == "pdf"
    assert sec_doc["status_cap"] == "Probable"

    # Force a C3 conflict under PDF cap
    sec = full_wet_dry()
    sec["utilities"]["lv"] = _band(3.5 + 0.6, 0.4, "ELV-LV", "lv", "dry", -3.5, 3.5, power=True)
    capped = sections_doc(sec, route="pdf", status_cap="Probable")
    doc, _ = check(tmp_path / "c", capped)
    assert doc["meta"]["status_cap"] == "Probable"
    for f in doc["findings"]:
        assert f["proposed_status"] == "Probable", f


# --------------------------------------------------------------------------- nested INSERT raw proof (G-O1)
def test_nested_insert_raw_not_todxf(tmp_path):
    """Nested band INSERTs: raw decoder sees them; to_dxf drops them (G-O1)."""
    ezdwg = pytest.importorskip("ezdwg")
    from ezdwg.raw import decode_insert_entities
    from importlib.metadata import version
    assert version("ezdwg") == "0.16.0"

    d = ezdwg.new()
    for name, color in [
        ("0", 7), ("PWT-WATER", 5), ("STO-STORM", 4), ("SWN-SEWER", 6),
        ("IRR-IRRIGATION", 3), ("EMV-MV", 1), ("ELV-LV", 2), ("TEL-TELECOM", 7),
        ("SL-LIGHTING", 8), ("LIMIT OF WORK", 9), ("Center line", 10), ("DWG_TITLE", 11),
    ]:
        d.layers[name] = {"color_index": color}

    blk = d.blocks.new("BAND")
    blk.add_line((0, 0), (1, 0), layer="0")

    curb = 3.5
    xref = d.blocks.new("XREF_SECTION")
    # water centre = curb+1.5 = 5.0, width 0.85 → insert x = 5.425, sx=0.85
    xref.add_blockref("BAND", insert=(5.425, 0.0, 0.0), scale=(0.85, 1.0, 1.0), layer="PWT-WATER")
    xref.add_blockref("BAND", insert=(6.35, 0.0, 0.0), scale=(0.5, 1.0, 1.0), layer="STO-STORM")
    xref.add_blockref("BAND", insert=(7.0, 0.0, 0.0), scale=(0.5, 1.0, 1.0), layer="SWN-SEWER")
    xref.add_blockref("BAND", insert=(4.4, 0.0, 0.0), scale=(0.4, 1.0, 1.0), layer="IRR-IRRIGATION")
    xref.add_blockref("BAND", insert=(-4.5, 0.0, 0.0), scale=(0.5, 1.0, 1.0), layer="EMV-MV")
    xref.add_blockref("BAND", insert=(-5.1, 0.0, 0.0), scale=(0.4, 1.0, 1.0), layer="ELV-LV")
    xref.add_blockref("BAND", insert=(-5.6, 0.0, 0.0), scale=(0.3, 1.0, 1.0), layer="TEL-TELECOM")
    xref.add_blockref("BAND", insert=(-6.0, 0.0, 0.0), scale=(0.3, 1.0, 1.0), layer="SL-LIGHTING")

    msp = d.modelspace()
    msp.add_line((0, -2), (0, 2), layer="Center line")
    msp.add_line((curb, -2), (curb, 2), layer="LIMIT OF WORK")
    msp.add_line((-curb, -2), (-curb, 2), layer="LIMIT OF WORK")
    msp.add_text("A1", insert=(0, 3, 0), height=0.3, layer="DWG_TITLE")
    msp.add_blockref("XREF_SECTION", insert=(0, 0, 0), scale=(1, 1, 1), layer="0")

    dwg = tmp_path / "nested.dwg"
    d.save(str(dwg))

    raw_ins = decode_insert_entities(str(dwg))
    assert len(raw_ins) >= 9  # 8 bands + container

    # to_dxf drops nested INSERTs (G-O1)
    dxf = tmp_path / "nested.dxf"
    ezdwg.to_dxf(str(dwg), str(dxf), flatten_inserts=False, explode_dimensions=False)
    import ezdxf
    doc = ezdxf.readfile(str(dxf))
    msp_ins = list(doc.modelspace().query("INSERT"))
    blk_ins = sum(1 for b in doc.blocks for e in b if e.dxftype() == "INSERT")
    assert len(msp_ins) + blk_ins == 0, "to_dxf must not keep nested band INSERTs (G-O1)"

    # Reader recovers bands via raw route
    out = tmp_path / "sections.json"
    rc, msg = run("read_corridor_sections.py", "--dwg", dwg, "--site", "NMAR", "--out", out)
    assert rc == 0, msg
    sec_doc = load(out)
    assert sec_doc["route"] == "dwg_raw"
    assert sec_doc["source"]["band_insert_count"] >= 8
    utils = sec_doc["sections"][0]["utilities"]
    assert "water" in utils
    assert abs(utils["water"]["offset_to_curb"] - 1.5) < 0.05
    # C3 pass through checker
    doc, _ = check(tmp_path / "chk", sec_doc)
    assert doc["counts"]["c3_flags"] == 0
    assert doc["counts"]["c1_missing"] == 0


# --------------------------------------------------------------------------- layout / frontmatter
def test_skill14_frontmatter_and_layout():
    skill = PLUGIN / "skills/coordinating-utility-corridors"
    assert (skill / "SKILL.md").is_file()
    text = (skill / "SKILL.md").read_text(encoding="utf-8")
    assert 'version: "0.1.0"' in text
    assert 'spec: "SPC-0006 Rev O §4.17"' in text
    assert "Use when" in text.split("---", 2)[1] or "Use when" in text
    assert (skill / "scripts/read_corridor_sections.py").is_file()
    assert (skill / "scripts/check_corridors.py").is_file()
    assert (skill / "references/utility_layers.json").is_file()
    assert (skill / "references/params.json").is_file()
    assert (skill / "templates/corridors.json").is_file()
    assert (skill / "templates/corridors.schema.json").is_file()
    assert (skill / "evals/evals.json").is_file()
    evals = load(skill / "evals/evals.json")
    assert 3 <= len(evals["evals"]) <= 5


def test_c2_never_emits_value_findings(tmp_path):
    """DO-5: C2 is information only — no findings from offsets."""
    sec = full_wet_dry()
    # Deliberately wrong water offset
    sec["utilities"]["water"]["offset_to_curb"] = 0.01
    doc, _ = check(tmp_path, sections_doc(sec))
    assert not any(f["reference"]["clause"] == "C2" for f in doc["findings"])
    assert all(r["result"] == "information" for r in doc["comparisons"] if r["check"] == "C2")
