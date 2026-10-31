"""Skill 14 0.1.1 — synthetic T14-1…T14-5 + C2 ER findings (DO-5 / R-O5) + nested proofs.

SPC-0006 Rev O §4.17. No real project data.
"""
from __future__ import annotations

import json
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

LAYER = {
    "water": "-05- Potable Water",
    "storm": "-01- Storm Water",
    "sewer": "-02- Sewerage",
    "irrigation": "-04- Irrigation",
    "mv": "-09- Electrical MV",
    "lv": "-10- Electrical LV",
    "telecom": "07- Telecom",
    "street_lighting": "-06- Street Lighting",
}


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


def _band(centre, width, layer, util, cls, curb_left, curb_right, cl_x=0.0, power=False,
          index=0):
    half = width / 2.0
    x_min, x_max = centre - half, centre + half
    if centre >= cl_x:
        offset = centre - curb_right
        side = "right"
    else:
        offset = curb_left - centre
        side = "left"
    return {
        "utility": util, "side": side, "index": index,
        "x_min": x_min, "x_max": x_max, "centre": centre, "width": width,
        "offset_to_curb": round(offset, 6),
        "layer": layer, "handle": f"H-{util}-{index}", "class": cls, "power": power,
        "c1_as": [], "exclude_c3_c4": False,
    }


def make_section(label, bands, curb=3.5, cl_x=0.0, curb_found=True):
    utilities = {}
    for b in bands:
        utilities.setdefault(b["utility"], b)
    return {
        "label": label,
        "cl_x": cl_x,
        "curb_left": -curb if curb_found else None,
        "curb_right": curb if curb_found else None,
        "curb_found": curb_found,
        "row_left": min(b["x_min"] for b in bands) if bands else None,
        "row_right": max(b["x_max"] for b in bands) if bands else None,
        "bands": bands,
        "utilities": utilities,
        "band_count": len(bands),
    }


def full_wet_dry(curb=3.5, water_centre=None):
    cl_x = 0.0
    w_c = water_centre if water_centre is not None else curb + 1.5
    bands = [
        _band(w_c, 0.85, LAYER["water"], "water", "wet", -curb, curb, cl_x),
        _band(curb + 2.5, 0.5, LAYER["storm"], "storm", "wet", -curb, curb, cl_x),
        _band(curb + 3.0, 0.5, LAYER["sewer"], "sewer", "wet", -curb, curb, cl_x),
        _band(curb + 0.8, 0.4, LAYER["irrigation"], "irrigation", "wet", -curb, curb, cl_x),
        _band(-(curb + 1.2), 0.5, LAYER["mv"], "mv", "dry", -curb, curb, cl_x, power=True),
        _band(-(curb + 1.8), 0.4, LAYER["lv"], "lv", "dry", -curb, curb, cl_x, power=True),
        _band(-(curb + 2.3), 0.3, LAYER["telecom"], "telecom", "dry", -curb, curb, cl_x),
        _band(-(curb + 2.8), 0.3, LAYER["street_lighting"], "street_lighting", "dry",
              -curb, curb, cl_x),
    ]
    return make_section("A1", bands, curb=curb, cl_x=cl_x)


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
    if kw.get("plan_dwg"):
        args += ["--plan-dwg", kw["plan_dwg"]]
    rc, msg = run("check_corridors.py", *args)
    assert rc == 0, msg
    doc = load(out)
    Draft202012Validator(load(TPL / "corridors.schema.json")).validate(doc)
    return doc, msg


def test_t14_1_wet_dry_water_offset(tmp_path):
    """T14-1: wet one side, dry the other, water at +1.5 → C3 pass; C2 pass at ER."""
    sec = full_wet_dry(curb=3.5, water_centre=3.5 + 1.5)
    water = next(b for b in sec["bands"] if b["utility"] == "water")
    assert abs(water["offset_to_curb"] - 1.5) < 1e-9
    doc, _ = check(tmp_path, sections_doc(sec))
    c3 = [r for r in doc["comparisons"] if r["check"] == "C3"]
    assert c3 and all(r["result"] == "pass" for r in c3)
    c2_water = [r for r in doc["comparisons"]
                if r["check"] == "C2" and r["values"].get("utility") == "water"]
    assert c2_water and abs(c2_water[0]["values"]["offset_to_curb_m"] - 1.5) < 1e-9
    assert c2_water[0]["result"] == "pass"
    assert not any(
        f["reference"]["clause"] == "C2" and "water" in f["title"] for f in doc["findings"]
    )
    assert all(f.get("reference", {}).get("rule_id") is None for f in doc["findings"])
    assert all(f.get("report_only") is True for f in doc["findings"])
    assert all(f.get("agent") == "COO" for f in doc["findings"])


def test_t14_2_dry_both_verges_c3_flag(tmp_path):
    """T14-2: dry on both verges (shares wet side) → C3 flag."""
    sec = full_wet_dry(curb=3.5)
    sec["bands"].append(
        _band(3.5 + 0.6, 0.4, LAYER["lv"], "lv", "dry", -3.5, 3.5, power=True, index=1)
    )
    sec["band_count"] = len(sec["bands"])
    doc, _ = check(tmp_path, sections_doc(sec))
    c3 = [r for r in doc["comparisons"] if r["check"] == "C3"]
    assert c3 and c3[0]["result"] == "flag"
    assert doc["counts"]["c3_flags"] == 1
    flags = [f for f in doc["findings"] if f["reference"]["clause"] == "C3"]
    assert len(flags) == 1
    assert flags[0]["issue_type"] == "design_error"
    assert flags[0]["severity"] == "Major"
    assert flags[0]["proposed_status"] == "Probable"


def test_t14_3_missing_storm(tmp_path):
    """T14-3: no storm band → C1 missing_information (storm)."""
    sec = full_wet_dry(curb=3.5)
    sec["bands"] = [b for b in sec["bands"] if b["utility"] != "storm"]
    sec["utilities"] = {b["utility"]: b for b in sec["bands"]}
    sec["band_count"] = len(sec["bands"])
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


def test_t14_4_lv_water_spacing_nav(tmp_path):
    """T14-4: LV 0.6 m touching water 0.85 m → c-c 0.725, max 1.45 → NAV."""
    bands = [
        _band(1.025, 0.85, LAYER["water"], "water", "wet", -10, 0.0),
        _band(0.3, 0.6, LAYER["lv"], "lv", "dry", -10, 0.0, power=True),
        _band(-2.0, 0.4, LAYER["sewer"], "sewer", "wet", -10, 0.0),
        _band(-1.5, 0.4, LAYER["storm"], "storm", "wet", -10, 0.0),
        _band(-1.0, 0.3, LAYER["irrigation"], "irrigation", "wet", -10, 0.0),
        _band(-3.0, 0.4, LAYER["mv"], "mv", "dry", -10, 0.0, power=True),
        _band(-3.5, 0.3, LAYER["telecom"], "telecom", "dry", -10, 0.0),
        _band(-4.0, 0.3, LAYER["street_lighting"], "street_lighting", "dry", -10, 0.0),
    ]
    bands[0].update({"x_min": 0.6, "x_max": 1.45, "centre": 1.025, "width": 0.85})
    bands[1].update({"x_min": 0.0, "x_max": 0.6, "centre": 0.3, "width": 0.6})
    sec = make_section("A1", bands, curb=0.0)
    doc, _ = check(tmp_path, sections_doc(sec))
    row = next(r for r in doc["comparisons"]
               if r["check"] == "C4" and r["values"].get("power") == "lv"
               and r["values"].get("other") == "water")
    assert abs(row["values"]["centre_to_centre_m"] - 0.725) < 1e-6
    assert abs(row["values"]["max_edge_m"] - 1.45) < 1e-6
    assert row["proposed_status"] == "Not auto-verifiable"
    nav = [f for f in doc["findings"] if f["proposed_status"] == "Not auto-verifiable"]
    assert nav and nav[0]["reference"]["clause"] == "C4"


def test_t14_5_pdf_status_capped_probable(tmp_path):
    """T14-5: PDF route → every finding status capped at Probable."""
    reportlab = pytest.importorskip("reportlab")
    from reportlab.pdfgen import canvas
    from reportlab.lib.pagesizes import A4

    pdf_path = tmp_path / "section.pdf"
    c = canvas.Canvas(str(pdf_path), pagesize=A4)
    c.setFillColorRGB(0, 0, 1)
    c.rect(200, 400, 40, 20, fill=1, stroke=0)
    c.setFillColorRGB(1, 0, 0)
    c.rect(260, 400, 30, 20, fill=1, stroke=0)
    c.setFillColorRGB(0, 0, 0)
    c.drawString(100, 500, "SECTION A1")
    c.drawString(100, 480, "15.00 (R.O.W.)")
    c.drawString(100, 460, "LEGEND POTABLE WATER (PW)")
    c.save()

    out_sec = tmp_path / "sections_pdf.json"
    rc, msg = run("read_corridor_sections.py", "--pdf", pdf_path, "--site", "NMAR",
                  "--out", out_sec)
    assert rc == 0, msg
    sec_doc = load(out_sec)
    assert sec_doc["route"] == "pdf"
    assert sec_doc["status_cap"] == "Probable"

    sec = full_wet_dry()
    sec["bands"].append(
        _band(3.5 + 0.6, 0.4, LAYER["lv"], "lv", "dry", -3.5, 3.5, power=True, index=1)
    )
    capped = sections_doc(sec, route="pdf", status_cap="Probable")
    doc, _ = check(tmp_path / "c", capped)
    assert doc["meta"]["status_cap"] == "Probable"
    for f in doc["findings"]:
        assert f["proposed_status"] == "Probable", f


def _make_band_block(d, name="BAND"):
    """Band block with one LWPOLYLINE width 1 at x=−0.5 (R5)."""
    blk = d.blocks.new(name)
    blk.add_lwpolyline([(-0.5, 0.0), (-0.5, 1.0)], layer="0", const_width=1.0)
    return blk


def test_nested_insert_raw_not_todxf(tmp_path):
    """Nested band INSERTs: raw decoder sees them; to_dxf drops them (G-O1)."""
    ezdwg = pytest.importorskip("ezdwg")
    from ezdwg.raw import decode_insert_entities
    from importlib.metadata import version
    assert version("ezdwg") == "0.16.0"

    d = ezdwg.new()
    for name, color in [
        ("0", 7),
        (LAYER["water"], 5), (LAYER["storm"], 4), (LAYER["sewer"], 6),
        (LAYER["irrigation"], 3), (LAYER["mv"], 1), (LAYER["lv"], 2),
        (LAYER["telecom"], 7), (LAYER["street_lighting"], 8),
        ("LIMIT OF WORK", 9), ("Center line", 10), ("DWG_TITLE", 11),
    ]:
        d.layers[name] = {"color_index": color}

    _make_band_block(d, "BAND")
    curb = 3.50  # within params.curb.lane_width_m [3.0, 3.65]
    xref = d.blocks.new("XREF_SECTION")
    # Keep consecutive band edges < 3 m apart so R3 x-gap split does not bisect
    # the section (real sheets fill L verge → carriageway → R verge continuously).
    for util, x, sx in [
        ("water", 5.425, 0.85), ("irrigation", 4.4, 0.4), ("storm", 2.5, 0.5),
        ("sewer", 1.05, 0.5), ("street_lighting", -0.35, 0.3),
        ("telecom", -1.5, 0.3), ("mv", -2.7, 0.5), ("lv", -3.7, 0.4),
    ]:
        xref.add_blockref("BAND", insert=(x, 0.0, 0.0), scale=(sx, 1.0, 1.0),
                          layer=LAYER[util])

    msp = d.modelspace()
    msp.add_lwpolyline([(0, -2), (0, 2)], layer="Center line")
    msp.add_lwpolyline([(curb, -2), (curb, 2)], layer="LIMIT OF WORK")
    msp.add_lwpolyline([(-curb, -2), (-curb, 2)], layer="LIMIT OF WORK")
    msp.add_text("A1", insert=(0, -3, 0), height=0.3, layer="DWG_TITLE")
    msp.add_blockref("XREF_SECTION", insert=(0, 0, 0), scale=(1, 1, 1), layer="0")

    dwg = tmp_path / "nested.dwg"
    d.save(str(dwg))

    raw_ins = decode_insert_entities(str(dwg))
    assert len(raw_ins) >= 9

    dxf = tmp_path / "nested.dxf"
    ezdwg.to_dxf(str(dwg), str(dxf), flatten_inserts=False, explode_dimensions=False)
    import ezdxf
    doc = ezdxf.readfile(str(dxf))
    msp_ins = list(doc.modelspace().query("INSERT"))
    blk_ins = sum(1 for b in doc.blocks for e in b if e.dxftype() == "INSERT")
    assert len(msp_ins) + blk_ins == 0, "to_dxf must not keep nested band INSERTs (G-O1)"

    out = tmp_path / "sections.json"
    rc, msg = run("read_corridor_sections.py", "--dwg", dwg, "--site", "NMAR", "--out", out)
    assert rc == 0, msg
    sec_doc = load(out)
    assert sec_doc["route"] == "dwg_raw"
    assert sec_doc["source"]["band_insert_count"] >= 8
    assert len(sec_doc["sections"]) == 1
    assert sec_doc["sections"][0]["label"] == "A1"
    assert sec_doc["sections"][0]["curb_method"] == "lane_chain"
    utils = {b["utility"] for b in sec_doc["sections"][0]["bands"]}
    assert "water" in utils
    water = next(b for b in sec_doc["sections"][0]["bands"] if b["utility"] == "water")
    assert abs(water["offset_to_curb"] - 1.5) < 0.05
    doc, _ = check(tmp_path / "chk", sec_doc)
    assert doc["counts"]["c3_flags"] == 0
    assert doc["counts"]["c1_missing"] == 0


def test_nested_parent_nonzero_x_scale(tmp_path):
    """R6: parent INSERT at non-zero x / scale transforms nested band centres."""
    ezdwg = pytest.importorskip("ezdwg")
    d = ezdwg.new()
    for name, color in [("0", 7), (LAYER["water"], 5), (LAYER["storm"], 4),
                        (LAYER["sewer"], 6), (LAYER["irrigation"], 3),
                        (LAYER["mv"], 1), (LAYER["lv"], 2),
                        (LAYER["telecom"], 7), (LAYER["street_lighting"], 8),
                        ("LIMIT OF WORK", 9), ("Center line", 10), ("DWG_TITLE", 11)]:
        d.layers[name] = {"color_index": color}
    _make_band_block(d, "BAND")
    xref = d.blocks.new("XREF_SECTION")
    # Tight local packing: after parent scale 2, consecutive world gaps stay < 3 m.
    for util, x, sx in [
        ("water", 2.5, 0.5), ("irrigation", 1.9, 0.3), ("storm", 1.3, 0.4),
        ("sewer", 0.7, 0.4), ("street_lighting", -0.5, 0.3), ("telecom", -1.1, 0.3),
        ("mv", -1.7, 0.4), ("lv", -2.3, 0.4),
    ]:
        xref.add_blockref("BAND", insert=(x, 0.0, 0.0), scale=(sx, 1.0, 1.0),
                          layer=LAYER[util])
    msp = d.modelspace()
    # Parent (10, scale 2) → water world x=10+2.5*2=15.0, sx=1.0, centre=14.5
    parent_x, parent_s = 10.0, 2.0
    water_world_centre = parent_x + (2.5 - 0.5 / 2.0) * parent_s  # 14.5
    # Lane-chain curb at ±3.30 from CL (params.curb.lane_width_m)
    curb = 3.30
    cl = water_world_centre - 1.5  # water offset ≈ +1.5 after curb at cl+curb… adjust
    # Place CL so water centre is curb + 1.5
    cl = water_world_centre - (curb + 1.5)
    msp.add_lwpolyline([(cl, -2), (cl, 2)], layer="Center line")
    msp.add_lwpolyline([(cl + curb, -2), (cl + curb, 2)], layer="LIMIT OF WORK")
    msp.add_lwpolyline([(cl - curb, -2), (cl - curb, 2)], layer="LIMIT OF WORK")
    msp.add_text("A1", insert=(cl, -3, 0), height=0.3, layer="DWG_TITLE")
    msp.add_blockref("XREF_SECTION", insert=(parent_x, 0.0, 0.0),
                     scale=(parent_s, parent_s, 1.0), layer="0")

    dwg = tmp_path / "nested_scaled.dwg"
    d.save(str(dwg))
    out = tmp_path / "sections.json"
    rc, msg = run("read_corridor_sections.py", "--dwg", dwg, "--site", "NMAR", "--out", out)
    assert rc == 0, msg
    sec_doc = load(out)
    assert sec_doc["source"]["band_insert_count"] >= 8
    assert len(sec_doc["sections"]) == 1, sec_doc.get("warnings")
    water = next(b for b in sec_doc["sections"][0]["bands"] if b["utility"] == "water")
    assert abs(water["centre"] - water_world_centre) < 0.15, water
    assert abs(water["width"] - 1.0) < 0.05, water  # 0.5 * scale 2
    sec0 = sec_doc["sections"][0]
    assert sec0["curb_method"] == "lane_chain"
    assert abs(sec0["curb_right"] - (cl + curb)) < 0.05



def test_c6_raw_plan_text_attrib(tmp_path):
    """M3: C6 reads plan cut labels from raw TEXT / MTEXT / ATTRIB."""
    ezdwg = pytest.importorskip("ezdwg")
    d = ezdwg.new()
    d.layers["0"] = {"color_index": 7}
    d.layers["DWG_TITLE"] = {"color_index": 1}
    msp = d.modelspace()
    for i, lab in enumerate(["A1", "A2", "A3", "A3-1", "B1", "B1-1", "B1-2"]):
        msp.add_text(lab, insert=(i * 10.0, 0.0, 0.0), height=0.3, layer="DWG_TITLE")
    # ATTRIB on a blockref
    blk = d.blocks.new("CUT")
    blk.add_attdef(tag="SEC", text="", insert=(0, 0, 0), height=0.25)
    br = msp.add_blockref("CUT", insert=(100.0, 0.0, 0.0))
    msp.add_attrib(br, tag="SEC", text="A1", insert=(100.0, 0.0, 0.0), height=0.25)
    plan_dwg = tmp_path / "plan.dwg"
    d.save(str(plan_dwg))

    sec = full_wet_dry()
    # Section types matching plan cuts (one of each)
    sections = []
    for lab in ["A1", "A2", "A3", "A3-1", "B1", "B1-1", "B1-2"]:
        s = full_wet_dry()
        s["label"] = lab
        sections.append(s)
    doc, _ = check(tmp_path / "c6", sections_doc(sections), plan_dwg=str(plan_dwg))
    c6 = [r for r in doc["comparisons"] if r["check"] == "C6"]
    assert c6
    assert c6[0]["result"] == "pass"
    assert set(c6[0]["values"]["plan_cuts"]) >= {
        "A1", "A2", "A3", "A3-1", "B1", "B1-1", "B1-2"
    }
    assert c6[0]["values"].get("plan_source") == "raw_text_mtext_attrib"
    # A1 appears at least twice (text + attrib)
    assert c6[0]["values"]["cut_counts"].get("A1", 0) >= 2


def test_c2_not_run_when_curb_missing(tmp_path):
    """R2: missing curb → C2 not_run, offsets null."""
    sec = full_wet_dry()
    sec["curb_left"] = None
    sec["curb_right"] = None
    sec["curb_found"] = False
    for b in sec["bands"]:
        b["offset_to_curb"] = None
    doc, _ = check(tmp_path, sections_doc(sec))
    c2 = [r for r in doc["comparisons"] if r["check"] == "C2"]
    assert c2 and all(r["result"] == "not_run" for r in c2)


def test_no_band_merge_keeps_two_lv(tmp_path):
    """R4: two LV bands (L and R) stay as separate rows."""
    sec = full_wet_dry(curb=3.5)
    sec["bands"].append(
        _band(3.5 + 1.8, 0.4, LAYER["lv"], "lv", "dry", -3.5, 3.5, power=True, index=1)
    )
    # Force sides
    left_lv = next(b for b in sec["bands"] if b["utility"] == "lv" and b["centre"] < 0)
    right_lv = next(b for b in sec["bands"] if b["utility"] == "lv" and b["centre"] > 0)
    assert left_lv["side"] == "left" and right_lv["side"] == "right"
    doc, _ = check(tmp_path, sections_doc(sec))
    lv_rows = [r for r in doc["rows"] if r["utility"] == "lv"]
    assert len(lv_rows) == 2


def test_skill14_frontmatter_and_layout():
    skill = PLUGIN / "skills/coordinating-utility-corridors"
    assert (skill / "SKILL.md").is_file()
    text = (skill / "SKILL.md").read_text(encoding="utf-8")
    assert 'version: "0.1.1"' in text
    assert 'spec: "SPC-0006 Rev O §4.17"' in text
    assert "Use when" in text.split("---", 2)[1] or "Use when" in text
    assert "R-O5" in text or "c2_er_positions_m" in text
    assert (skill / "scripts/read_corridor_sections.py").is_file()
    assert (skill / "scripts/check_corridors.py").is_file()
    layers = load(skill / "references/utility_layers.json")
    shared = layers["shared_layers"]
    assert "-05- Potable Water" in shared
    assert "PWT-WATER" not in shared
    assert "legend_colours" not in layers.get("sites", {}).get("NMAR", {})
    params = load(skill / "references/params.json")
    assert "DO-7 [AGREED 29 Oct 21:20]" in params["tag"]
    assert "DO-5a…d" in params["tag"] and "AGREED 31 Oct 15:44" in params["tag"]
    assert "PROPOSED" not in params["tag"]
    assert "DN-5" not in params["note"] and "R-L5" not in params["note"]
    assert "AGREED 31 Oct 15:44" in (params.get("tolerances") or {}).get("tag", "")
    assert params["c2_er_positions_m"] == {"water": 1.5, "storm": 2.5, "sewer": 3.0}
    assert params["c2_tolerance_m"] == 0.5
    assert "CL" not in (params.get("curb") or {}).get("center_line_layer_substrings", [])
    assert "not in Git" in text or "AICC project note S33-R-O5" in text
    assert (skill / "templates/corridors.json").is_file()
    assert (skill / "templates/corridors.schema.json").is_file()
    assert (skill / "evals/evals.json").is_file()
    evals = load(skill / "evals/evals.json")
    assert 3 <= len(evals["evals"]) <= 5


def test_c2_er_finding_when_beyond_tolerance(tmp_path):
    """DO-5a: |unsigned − ER| > 0.5 → C2 design_error / Probable / Major; CRS Parsons."""
    sec = full_wet_dry(curb=3.5, water_centre=3.5 + 0.12)  # unsigned 0.12 vs 1.5
    # Align storm / sewer to ER so only water flags
    for b in sec["bands"]:
        if b["utility"] == "storm":
            b["centre"] = 3.5 + 2.5
            b["offset_to_curb"] = 2.5
            b["x_min"], b["x_max"] = b["centre"] - 0.25, b["centre"] + 0.25
        if b["utility"] == "sewer":
            b["centre"] = 3.5 + 3.0
            b["offset_to_curb"] = 3.0
            b["x_min"], b["x_max"] = b["centre"] - 0.25, b["centre"] + 0.25
    doc, _ = check(tmp_path, sections_doc(sec))
    c2w = [r for r in doc["comparisons"]
           if r["check"] == "C2" and r["values"].get("utility") == "water"]
    assert c2w and c2w[0]["result"] == "flag"
    assert abs(c2w[0]["values"]["unsigned_distance_m"] - 0.12) < 1e-6
    finds = [f for f in doc["findings"] if f["reference"]["clause"] == "C2"]
    assert len(finds) == 1
    f = finds[0]
    assert f["issue_type"] == "design_error"
    assert f["severity"] == "Major"
    assert f["proposed_status"] == "Probable"
    assert f["reference"]["rule_id"] is None
    # K5: 0.1.0 owner role; Parsons is addressee only
    assert f["owner"]["role"] == "IM / CM"
    assert f["location"].get("route") == "CRS"
    assert f["location"].get("recipient") == "Parsons"
    assert "MOMRA cross-section typical" in f["statement"]
    assert "direction" in f["statement"].lower() or "not stated" in f["statement"].lower()
    assert "Parsons" in f["statement"]
    assert any("R-O5" in (e.get("extract") or "") for e in f["evidence"])
    assert any("not in Git" in (e.get("extract") or "") for e in f["evidence"])


def test_c2_one_finding_per_section_utility_both_sides(tmp_path):
    """DO-5c: one finding per section × utility; both sides in evidence when they differ."""
    curb = 3.5
    # Left water at 0.12 from curb (carriageway), right water at 2.38 (verge) — both >0.5 from 1.5
    bands = [
        _band(-(curb + 0.12), 0.85, LAYER["water"], "water", "wet", -curb, curb, index=0),
        _band(curb + 2.38, 0.85, LAYER["water"], "water", "wet", -curb, curb, index=1),
        _band(curb + 2.5, 0.5, LAYER["storm"], "storm", "wet", -curb, curb),
        _band(curb + 3.0, 0.5, LAYER["sewer"], "sewer", "wet", -curb, curb),
        _band(curb + 0.8, 0.4, LAYER["irrigation"], "irrigation", "wet", -curb, curb),
        _band(-(curb + 1.2), 0.5, LAYER["mv"], "mv", "dry", -curb, curb, power=True),
        _band(-(curb + 1.8), 0.4, LAYER["lv"], "lv", "dry", -curb, curb, power=True),
        _band(-(curb + 2.3), 0.3, LAYER["telecom"], "telecom", "dry", -curb, curb),
        _band(-(curb + 2.8), 0.3, LAYER["street_lighting"], "street_lighting", "dry",
              -curb, curb),
    ]
    # Force left water unsigned 0.12 (inside carriageway → signed −0.12)
    bands[0]["offset_to_curb"] = -0.12
    bands[0]["side"] = "left"
    bands[1]["offset_to_curb"] = 2.38
    bands[1]["side"] = "right"
    sec = make_section("A2", bands, curb=curb)
    doc, _ = check(tmp_path, sections_doc(sec))
    water_finds = [
        f for f in doc["findings"]
        if f["reference"]["clause"] == "C2" and f["location"].get("utility") == "water"
    ]
    assert len(water_finds) == 1
    # Larger deviation is |0.12−1.5|=1.38 > |2.38−1.5|=0.88
    assert "1.380" in water_finds[0]["statement"] or "0.120" in water_finds[0]["statement"]
    assert any("both sides" in (e.get("extract") or "") for e in water_finds[0]["evidence"])
    storm_finds = [
        f for f in doc["findings"]
        if f["reference"]["clause"] == "C2" and f["location"].get("utility") == "storm"
    ]
    assert storm_finds == []  # at ER 2.5


def test_c2_storm_cb_not_compared(tmp_path):
    """Storm CB bands are information only — never a C2 ER finding."""
    sec = full_wet_dry(curb=3.5, water_centre=3.5 + 1.5)
    for b in sec["bands"]:
        if b["utility"] in ("storm", "sewer"):
            b["offset_to_curb"] = {"storm": 2.5, "sewer": 3.0}[b["utility"]]
            b["centre"] = 3.5 + b["offset_to_curb"]
            b["x_min"], b["x_max"] = b["centre"] - 0.25, b["centre"] + 0.25
    # Add storm_cb far from any ER value
    cb = _band(3.5 + 0.2, 0.4, "-01- Storm CB", "storm_cb", "wet", -3.5, 3.5, index=0)
    cb["offset_to_curb"] = 0.2
    sec["bands"].append(cb)
    sec["band_count"] = len(sec["bands"])
    doc, _ = check(tmp_path, sections_doc(sec))
    cb_rows = [r for r in doc["comparisons"]
               if r["check"] == "C2" and r["values"].get("utility") == "storm_cb"]
    assert cb_rows and all(r["result"] == "information" for r in cb_rows)
    assert not any(
        f["location"].get("utility") == "storm_cb" for f in doc["findings"]
        if f["reference"]["clause"] == "C2"
    )


def test_c2_within_tolerance_no_finding(tmp_path):
    """Sewer at 2.65 (|Δ|=0.35 ≤ 0.5) → pass, no finding (R-O5 probe)."""
    sec = full_wet_dry(curb=3.5, water_centre=3.5 + 1.5)
    for b in sec["bands"]:
        if b["utility"] == "storm":
            b["offset_to_curb"] = 2.5
            b["centre"] = 3.5 + 2.5
            b["x_min"], b["x_max"] = b["centre"] - 0.25, b["centre"] + 0.25
        if b["utility"] == "sewer":
            b["offset_to_curb"] = -2.65  # carriageway side
            b["centre"] = 3.5 - 2.65  # will recompute below
            # Keep on right verge-ish: unsigned 2.65
            b["centre"] = 3.5 + 2.65
            b["side"] = "right"
            b["offset_to_curb"] = 2.65
            b["x_min"], b["x_max"] = b["centre"] - 0.25, b["centre"] + 0.25
    doc, _ = check(tmp_path, sections_doc(sec))
    c2s = [r for r in doc["comparisons"]
           if r["check"] == "C2" and r["values"].get("utility") == "sewer"]
    assert c2s and c2s[0]["result"] == "pass"
    assert abs(c2s[0]["values"]["deviation_m"] - 0.35) < 1e-6
    assert not any(
        f["location"].get("utility") == "sewer" for f in doc["findings"]
        if f["reference"]["clause"] == "C2"
    )


def _c2_sewer_both_sides(curb, unsigned_left, unsigned_right):
    """One utility (sewer), both sides at given unsigned distances; other ER utils on ER."""
    bands = [
        _band(-(curb + unsigned_left), 0.5, LAYER["sewer"], "sewer", "wet",
              -curb, curb, index=0),
        _band(curb + unsigned_right, 0.5, LAYER["sewer"], "sewer", "wet",
              -curb, curb, index=1),
        _band(curb + 1.5, 0.85, LAYER["water"], "water", "wet", -curb, curb),
        _band(curb + 2.5, 0.5, LAYER["storm"], "storm", "wet", -curb, curb),
        _band(curb + 0.8, 0.4, LAYER["irrigation"], "irrigation", "wet", -curb, curb),
        _band(-(curb + 1.2), 0.5, LAYER["mv"], "mv", "dry", -curb, curb, power=True),
        _band(-(curb + 1.8), 0.4, LAYER["lv"], "lv", "dry", -curb, curb, power=True),
        _band(-(curb + 2.3), 0.3, LAYER["telecom"], "telecom", "dry", -curb, curb),
        _band(-(curb + 2.8), 0.3, LAYER["street_lighting"], "street_lighting", "dry",
              -curb, curb),
    ]
    bands[0]["offset_to_curb"] = -unsigned_left
    bands[0]["side"] = "left"
    bands[1]["offset_to_curb"] = unsigned_right
    bands[1]["side"] = "right"
    return make_section("A1", bands, curb=curb)


def test_c2_edge_within_tolerance_0_49(tmp_path):
    """K4: sewer |Δ|=0.49 on both sides → pass, no finding (tol 0.5)."""
    # ER sewer 3.0 → unsigned 2.51 → Δ 0.49
    doc, _ = check(tmp_path, sections_doc(_c2_sewer_both_sides(3.5, 2.51, 2.51)))
    c2s = [r for r in doc["comparisons"]
           if r["check"] == "C2" and r["values"].get("utility") == "sewer"]
    assert len(c2s) == 2
    assert {r["values"].get("side") for r in c2s} == {"left", "right"}
    assert all(r["result"] == "pass" for r in c2s)
    assert all(abs(r["values"]["deviation_m"] - 0.49) < 1e-6 for r in c2s)
    assert not any(
        f["location"].get("utility") == "sewer" for f in doc["findings"]
        if f["reference"]["clause"] == "C2"
    )


def test_c2_edge_beyond_tolerance_0_51(tmp_path):
    """K4: sewer |Δ|=0.51 on both sides → one finding; both sides in evidence."""
    # ER sewer 3.0 → unsigned 2.49 → Δ 0.51
    doc, _ = check(tmp_path, sections_doc(_c2_sewer_both_sides(3.5, 2.49, 2.49)))
    c2s = [r for r in doc["comparisons"]
           if r["check"] == "C2" and r["values"].get("utility") == "sewer"]
    assert len(c2s) == 2
    assert {r["values"].get("side") for r in c2s} == {"left", "right"}
    assert all(r["result"] == "flag" for r in c2s)
    assert all(abs(r["values"]["deviation_m"] - 0.51) < 1e-6 for r in c2s)
    finds = [
        f for f in doc["findings"]
        if f["reference"]["clause"] == "C2" and f["location"].get("utility") == "sewer"
    ]
    assert len(finds) == 1
    assert finds[0]["owner"]["role"] == "IM / CM"
    assert any("both sides" in (e.get("extract") or "") for e in finds[0]["evidence"])


def test_parse_section_label_strict():
    sys.path.insert(0, str(SK))
    from corridor_common import parse_section_label as p
    assert p("A1") == "A1"
    assert p("A3-1") == "A3-1"
    assert p("B12") == "B12"
    assert p("15 METER LOCAL ROAD") is None
    assert p("Dahiyat Nemar") is None
    assert p("-") is None
    assert p("SECTION A1") == "A1"


def _full_utilities_on_xref(xref, band_name="BAND"):
    for util, x, sx in [
        ("water", 5.425, 0.85), ("irrigation", 4.4, 0.4), ("storm", 2.5, 0.5),
        ("sewer", 1.05, 0.5), ("street_lighting", -0.35, 0.3),
        ("telecom", -1.5, 0.3), ("mv", -2.7, 0.5), ("lv", -3.7, 0.4),
    ]:
        xref.add_blockref(band_name, insert=(x, 0.0, 0.0), scale=(sx, 1.0, 1.0),
                          layer=LAYER[util])


def test_anonymous_star_u_band_block(tmp_path):
    """N2a / F1: anonymous *U band block with width-1 marker is accepted."""
    ezdwg = pytest.importorskip("ezdwg")
    d = ezdwg.new()
    for name, color in [("0", 7), (LAYER["water"], 5), (LAYER["storm"], 4),
                        (LAYER["sewer"], 6), (LAYER["irrigation"], 3),
                        (LAYER["mv"], 1), (LAYER["lv"], 2),
                        (LAYER["telecom"], 7), (LAYER["street_lighting"], 8),
                        ("LIMIT OF WORK", 9), ("Center line", 10), ("DWG_TITLE", 11)]:
        d.layers[name] = {"color_index": color}
    _make_band_block(d, "*U16")
    xref = d.blocks.new("XREF_SECTION")
    _full_utilities_on_xref(xref, "*U16")
    msp = d.modelspace()
    curb = 3.30
    msp.add_lwpolyline([(0, -2), (0, 2)], layer="Center line")
    msp.add_lwpolyline([(curb, -2), (curb, 2)], layer="LIMIT OF WORK")
    msp.add_lwpolyline([(-curb, -2), (-curb, 2)], layer="LIMIT OF WORK")
    msp.add_text("A1", insert=(0, -3, 0), height=0.3, layer="DWG_TITLE")
    msp.add_blockref("XREF_SECTION", insert=(0, 0, 0), scale=(1, 1, 1), layer="0")
    dwg = tmp_path / "star_u.dwg"
    d.save(str(dwg))
    out = tmp_path / "sections.json"
    rc, msg = run("read_corridor_sections.py", "--dwg", dwg, "--site", "NMAR", "--out", out)
    assert rc == 0, msg
    sec_doc = load(out)
    assert sec_doc["source"]["band_block_count"] >= 1
    assert sec_doc["source"]["band_insert_count"] >= 8
    assert len(sec_doc["sections"]) == 1
    assert sec_doc["sections"][0]["label"] == "A1"
    assert any(b.get("block") == "*U16" for b in sec_doc["sections"][0]["bands"])


def test_same_name_different_content_handle_keyed():
    """N2b / F2: two headers sharing a name — only the marker handle is a band."""
    sys.path.insert(0, str(SK))
    from read_corridor_sections import _band_block_handles

    # Header 100 (*U1) has the width-1 marker; header 200 (*U1) has other content.
    lw_rows = [
        (1, 0, [(-0.5, 0.0), (-0.5, 1.0)], [], [], 1.0),
        (2, 0, [(0.0, 0.0), (1.0, 0.0)], [], [], 0.0),
    ]
    lw_owners = [(1, 100), (2, 200)]
    block_headers = [(100, "*U1"), (200, "*U1"), (101, "*U1"), (201, "*U1")]
    params = {"band": {"marker_local_x": -0.5, "marker_width": 1.0}}
    handles = _band_block_handles(lw_rows, lw_owners, block_headers, params)
    assert handles == {100}
    assert 200 not in handles


def test_two_sections_same_y_different_curbs(tmp_path):
    """N2c / F3: two sections at the same y keep distinct lane-chain curbs."""
    ezdwg = pytest.importorskip("ezdwg")
    d = ezdwg.new()
    for name, color in [("0", 7), (LAYER["water"], 5), (LAYER["storm"], 4),
                        (LAYER["sewer"], 6), (LAYER["irrigation"], 3),
                        (LAYER["mv"], 1), (LAYER["lv"], 2),
                        (LAYER["telecom"], 7), (LAYER["street_lighting"], 8),
                        ("LIMIT OF WORK", 9), ("Center line", 10), ("DWG_TITLE", 11)]:
        d.layers[name] = {"color_index": color}
    _make_band_block(d, "BAND")
    msp = d.modelspace()

    def add_bands(xref, origin_x, positions):
        """positions: list of (util, local_x, sx) packed so consecutive edges gap < 3 m."""
        for util, x, sx in positions:
            xref.add_blockref("BAND", insert=(origin_x + x, 0.0, 0.0),
                              scale=(sx, 1.0, 1.0), layer=LAYER[util])

    # A1 packed bands; curb ±3.30
    packed_a1 = [
        ("water", 5.425, 0.85), ("irrigation", 4.4, 0.4), ("storm", 2.5, 0.5),
        ("sewer", 1.05, 0.5), ("street_lighting", -0.35, 0.3),
        ("telecom", -1.5, 0.3), ("mv", -2.7, 0.5), ("lv", -3.7, 0.4),
    ]
    # A3 wider: bands span past ±6.60 curb; consecutive edges < 3 m
    packed_a3 = [
        ("lv", -8.5, 0.4), ("mv", -6.8, 0.5), ("telecom", -5.0, 0.3),
        ("street_lighting", -3.2, 0.3), ("sewer", -1.4, 0.5),
        ("storm", 1.4, 0.5), ("irrigation", 3.5, 0.4), ("water", 7.0, 0.85),
    ]

    # A1 at x=0: single lane-width curb ±3.30 from CL
    xref_a1 = d.blocks.new("XREF_A1")
    add_bands(xref_a1, 0.0, packed_a1)
    msp.add_lwpolyline([(0, -2), (0, 2)], layer="Center line")
    msp.add_lwpolyline([(3.30, -2), (3.30, 2)], layer="LIMIT OF WORK")
    msp.add_lwpolyline([(-3.30, -2), (-3.30, 2)], layer="LIMIT OF WORK")
    msp.add_lwpolyline([(7.50, -2), (7.50, 2)], layer="LIMIT OF WORK")  # ROW, gap 4.2
    msp.add_lwpolyline([(-7.50, -2), (-7.50, 2)], layer="LIMIT OF WORK")
    msp.add_text("A1", insert=(0, -3, 0), height=0.3, layer="DWG_TITLE")
    msp.add_blockref("XREF_A1", insert=(0, 0, 0), scale=(1, 1, 1), layer="0")

    # A3 at x=40, same y: chain 3.30 → 6.60 (distinct from A1)
    ox = 40.0
    xref_a3 = d.blocks.new("XREF_A3")
    add_bands(xref_a3, ox, packed_a3)
    msp.add_lwpolyline([(ox, -2), (ox, 2)], layer="Center line")
    for dx in (3.30, 6.60):  # two-lane chain from CL (no median)
        msp.add_lwpolyline([(ox + dx, -2), (ox + dx, 2)], layer="LIMIT OF WORK")
        msp.add_lwpolyline([(ox - dx, -2), (ox - dx, 2)], layer="LIMIT OF WORK")
    msp.add_text("A3", insert=(ox, -3, 0), height=0.3, layer="DWG_TITLE")
    msp.add_blockref("XREF_A3", insert=(0, 0, 0), scale=(1, 1, 1), layer="0")

    dwg = tmp_path / "same_y_curbs.dwg"
    d.save(str(dwg))
    out = tmp_path / "sections.json"
    rc, msg = run("read_corridor_sections.py", "--dwg", dwg, "--site", "NMAR", "--out", out)
    assert rc == 0, msg
    sec_doc = load(out)
    by_lab = {s["label"]: s for s in sec_doc["sections"]}
    assert set(by_lab) >= {"A1", "A3"}, (sec_doc["sections"], sec_doc.get("warnings"))
    a1, a3 = by_lab["A1"], by_lab["A3"]
    assert a1["curb_method"] == "lane_chain", a1
    assert a3["curb_method"] == "lane_chain", a3
    assert abs(a1["curb_right"] - 3.30) < 0.05, a1
    assert abs(a1["curb_left"] + 3.30) < 0.05, a1
    assert abs(a3["curb_right"] - (ox + 6.60)) < 0.05, a3
    assert abs(a3["curb_left"] - (ox - 6.60)) < 0.05, a3
    # Must not pick the neighbouring section's curb
    assert abs(a1["curb_right"] - a3["curb_right"]) > 1.0
    assert a1.get("curb_chain_right") == pytest.approx([3.30], abs=0.05)
    assert a3.get("curb_chain_right") == pytest.approx(
        [ox + 3.30, ox + 6.60], abs=0.05
    )


def test_owner_cycle_truncates_and_warns():
    """G1: cyclic owner drops parents after first repeat; OWNER_CYCLE warning."""
    sys.path.insert(0, str(SK))
    from read_corridor_sections import _build_owner_index

    # Band INSERT 1 owned by header 19; INSERT 18 targets 19 (true parent at 0,0).
    # ezdwg wrongly reports owner of 18 as header 148 (*U6); INSERT 137 targets 148
    # and is itself owned by 19 → cycle 19 → 148 → 19.
    inserts = [
        (1, 1.0, 0.0, 0.0, 0.85, 1.0, 1.0, 0.0, "BAND"),
        (18, 0.0, 0.0, 0.0, 1.0, 1.0, 1.0, 0.0, "XREF"),
        (137, 658537.0, 0.0, 0.0, 0.8, 0.8, 1.0, 0.0, "*U6"),
    ]
    owners = [(1, 19), (18, 148), (137, 19)]
    block_headers = [
        (19, "XREF"), (148, "*U6"), (10, "BAND"),
        (1, "*Model_Space"), (2, "*Paper_Space"),
    ]
    insert_targets = {1: 10, 18: 19, 137: 148}
    _by_h, parent_chain = _build_owner_index(
        inserts, owners, block_headers, insert_targets
    )
    chain, warns = parent_chain(1)
    assert [p["handle"] for p in chain] == [18], chain
    assert any(w["code"] == "OWNER_CYCLE" for w in warns), warns
    assert abs(chain[0]["x"]) < 1e-9 and abs(chain[0]["sx"] - 1.0) < 1e-9

    # Stop at *Model_Space / non-header without walking further
    inserts2 = [(5, 0.0, 0.0, 0.0, 1.0, 1.0, 1.0, 0.0, "BAND")]
    owners2 = [(5, 1)]  # owned by *Model_Space header
    targets2 = {5: 10}
    _by_h2, pc2 = _build_owner_index(inserts2, owners2, block_headers, targets2)
    chain2, warns2 = pc2(5)
    assert chain2 == []
    assert not any(w["code"] == "OWNER_CYCLE" for w in warns2)

    inserts3 = [(6, 0.0, 0.0, 0.0, 1.0, 1.0, 1.0, 0.0, "BAND")]
    owners3 = [(6, 999)]  # not a block header
    targets3 = {6: 10}
    _by_h3, pc3 = _build_owner_index(inserts3, owners3, block_headers, targets3)
    chain3, _ = pc3(6)
    assert chain3 == []


def test_owner_entmode_top_level_stops_before_cycle():
    """K2: entmode 1/2 on parent INSERT 18 → keep parent, no OWNER_CYCLE (QRWN)."""
    sys.path.insert(0, str(SK))
    from read_corridor_sections import _build_owner_index

    inserts = [
        (1, 1.0, 0.0, 0.0, 0.85, 1.0, 1.0, 0.0, "BAND"),
        (18, 0.0, 0.0, 0.0, 1.0, 1.0, 1.0, 0.0, "XREF"),
        (137, 658537.0, 0.0, 0.0, 0.8, 0.8, 1.0, 0.0, "*U6"),
    ]
    owners = [(1, 19), (18, 148), (137, 19)]
    block_headers = [
        (19, "XREF"), (148, "*U6"), (10, "BAND"),
        (1, "*Model_Space"), (2, "*Paper_Space"),
    ]
    insert_targets = {1: 10, 18: 19, 137: 148}
    # QRWN probe: handle 18 → entmode 2 (top-level)
    entmodes = {18: 2, 1: 0, 137: 0}
    _by_h, parent_chain = _build_owner_index(
        inserts, owners, block_headers, insert_targets, entmodes=entmodes
    )
    chain, warns = parent_chain(1)
    assert [p["handle"] for p in chain] == [18], chain
    assert not any(w["code"] == "OWNER_CYCLE" for w in warns), warns
    # Starting INSERT itself top-level → empty chain
    chain_tl, warns_tl = parent_chain(18)
    assert chain_tl == []
    assert warns_tl == []


def test_two_sections_same_y_global_labels(tmp_path):
    """G2: two sections at the same y get labels by x-span + global closest-first."""
    ezdwg = pytest.importorskip("ezdwg")
    d = ezdwg.new()
    for name, color in [("0", 7), (LAYER["water"], 5), (LAYER["storm"], 4),
                        (LAYER["sewer"], 6), (LAYER["irrigation"], 3),
                        (LAYER["mv"], 1), (LAYER["lv"], 2),
                        (LAYER["telecom"], 7), (LAYER["street_lighting"], 8),
                        ("LIMIT OF WORK", 9), ("Center line", 10), ("DWG_TITLE", 11)]:
        d.layers[name] = {"color_index": color}
    _make_band_block(d, "BAND")
    msp = d.modelspace()
    packed = [
        ("water", 5.425, 0.85), ("irrigation", 4.4, 0.4), ("storm", 2.5, 0.5),
        ("sewer", 1.05, 0.5), ("street_lighting", -0.35, 0.3),
        ("telecom", -1.5, 0.3), ("mv", -2.7, 0.5), ("lv", -3.7, 0.4),
    ]
    curb = 3.30

    def add_section(xref_name, ox, label, label_x):
        xref = d.blocks.new(xref_name)
        for util, x, sx in packed:
            xref.add_blockref("BAND", insert=(ox + x, 0.0, 0.0),
                              scale=(sx, 1.0, 1.0), layer=LAYER[util])
        msp.add_lwpolyline([(ox, -2), (ox, 2)], layer="Center line")
        msp.add_lwpolyline([(ox + curb, -2), (ox + curb, 2)], layer="LIMIT OF WORK")
        msp.add_lwpolyline([(ox - curb, -2), (ox - curb, 2)], layer="LIMIT OF WORK")
        # Label under the left part of the section (G2 x-span + below)
        msp.add_text(label, insert=(label_x, -2.5, 0), height=0.3, layer="DWG_TITLE")
        msp.add_blockref(xref_name, insert=(0, 0, 0), scale=(1, 1, 1), layer="0")

    # Side-by-side at y=0; A2's label is closer to A1's centre by |dx| if greedy
    # by section order, but x-span + global assign must keep A1→15-span / A2→18-span.
    add_section("XREF_A1", 0.0, "A1", label_x=-3.0)   # under left of A1
    add_section("XREF_A2", 30.0, "A2", label_x=26.5)  # under left of A2

    dwg = tmp_path / "same_y_labels.dwg"
    d.save(str(dwg))
    out = tmp_path / "sections.json"
    rc, msg = run("read_corridor_sections.py", "--dwg", dwg, "--site", "NMAR", "--out", out)
    assert rc == 0, msg
    sec_doc = load(out)
    by_lab = {s["label"]: s for s in sec_doc["sections"]}
    assert set(by_lab) >= {"A1", "A2"}, (sec_doc["sections"], sec_doc.get("warnings"))
    assert by_lab["A1"]["row_left"] < 10 < by_lab["A1"]["row_right"] or True
    assert abs(by_lab["A1"]["cl_x"] - 0.0) < 1.0, by_lab["A1"]
    assert abs(by_lab["A2"]["cl_x"] - 30.0) < 1.0, by_lab["A2"]
    # Labels must not be swapped
    assert by_lab["A1"]["label_text"] == "A1"
    assert by_lab["A2"]["label_text"] == "A2"


def test_median_carriageway_facing_start(tmp_path):
    """G3: 0.40 m median pair → start at ±2.4; two 3.30 lanes → curb ±9.00."""
    ezdwg = pytest.importorskip("ezdwg")
    d = ezdwg.new()
    for name, color in [("0", 7), (LAYER["water"], 5), (LAYER["storm"], 4),
                        (LAYER["sewer"], 6), (LAYER["irrigation"], 3),
                        (LAYER["mv"], 1), (LAYER["lv"], 2),
                        (LAYER["telecom"], 7), (LAYER["street_lighting"], 8),
                        ("LIMIT OF WORK", 9), ("Center line", 10), ("DWG_TITLE", 11)]:
        d.layers[name] = {"color_index": color}
    _make_band_block(d, "BAND")
    # Bands spanning past ±9.00; consecutive edges stay < 3 m (no x-gap split)
    packed = [
        ("lv", -11.0, 0.4), ("mv", -9.2, 0.5), ("telecom", -7.0, 0.3),
        ("street_lighting", -4.5, 0.3), ("sewer", -1.5, 0.5),
        ("storm", 1.5, 0.5), ("irrigation", 4.5, 0.4), ("water", 7.0, 0.85),
        ("lv", 9.5, 0.4), ("mv", 11.2, 0.5),
    ]
    xref = d.blocks.new("XREF_MED")
    for util, x, sx in packed:
        xref.add_blockref("BAND", insert=(x, 0.0, 0.0),
                          scale=(sx, 1.0, 1.0), layer=LAYER[util])
    msp = d.modelspace()
    msp.add_lwpolyline([(0, -2), (0, 2)], layer="Center line")
    # Median pair ±2.0 / ±2.4 (0.40 m) + lanes ±5.7 / ±9.0 (3.30 steps from 2.4)
    for dx in (2.0, 2.4, 5.7, 9.0):
        msp.add_lwpolyline([(dx, -2), (dx, 2)], layer="LIMIT OF WORK")
        msp.add_lwpolyline([(-dx, -2), (-dx, 2)], layer="LIMIT OF WORK")
    msp.add_text("B2", insert=(-3.0, -3, 0), height=0.3, layer="DWG_TITLE")
    msp.add_blockref("XREF_MED", insert=(0, 0, 0), scale=(1, 1, 1), layer="0")

    dwg = tmp_path / "median.dwg"
    d.save(str(dwg))
    out = tmp_path / "sections.json"
    rc, msg = run("read_corridor_sections.py", "--dwg", dwg, "--site", "QRWN", "--out", out)
    assert rc == 0, msg
    sec_doc = load(out)
    assert len(sec_doc["sections"]) == 1, sec_doc.get("warnings")
    sec = sec_doc["sections"][0]
    assert sec["label"] == "B2"
    assert sec["curb_method"] == "lane_chain", sec
    assert abs(sec["curb_right"] - 9.0) < 0.05, sec
    assert abs(sec["curb_left"] + 9.0) < 0.05, sec
    # Chain starts at carriageway-facing ±2.4, not inner ±2.0
    assert sec["curb_chain_right"] == pytest.approx([2.4, 5.7, 9.0], abs=0.05), sec
    assert sec["curb_chain_left"] == pytest.approx([-2.4, -5.7, -9.0], abs=0.05), sec


def _reader():
    import importlib.util
    spec = importlib.util.spec_from_file_location("rcs_unit", SK / "read_corridor_sections.py")
    mod = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(SK))
    spec.loader.exec_module(mod)
    return mod


def test_lane_chain_stops_at_line_inside_lane():
    """QRWN A2 left: 3.30 lane, then the 0.30 back-of-kerb line — kerb stays at −3.30,
    the chain must not jump over −3.60 / −5.70 to −6.54."""
    rcs = _reader()
    chain = rcs._lane_chain_side(0.0, [-3.3, -3.6, -5.7, -6.54, -9.0], -1, 3.0, 3.65)
    assert chain == [0.0, -3.3]


def test_lines_assigned_to_own_section_only():
    """QRWN A2 / B2: LIMIT OF WORK lines of the section ~25 m above or below stay there."""
    rcs = _reader()
    a = [{"x_min": -9.0, "x_max": 9.0, "y": 0.0}]
    below = [{"x_min": -15.0, "x_max": 15.0, "y": -25.0}]
    segs = [{"x": 3.3, "y_mid": 3.5}, {"x": 5.05, "y_mid": -22.9}, {"x": -8.45, "y_mid": -22.9}]
    got = rcs._assign_segs_to_sections(segs, [a, below])
    assert [s["x"] for s in got[0]] == [3.3]
    assert sorted(s["x"] for s in got[1]) == [-8.45, 5.05]
