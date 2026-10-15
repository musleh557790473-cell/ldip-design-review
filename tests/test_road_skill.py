"""Tests for skill 10 reviewing-road-design, phase A (SPC-0006 Rev D §4.13). Synthetic data only: rule values
below are made up for the tests (real values live only in the local rulebook, never in Git)."""
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("jsonschema")
pdfium = pytest.importorskip("pypdfium2")

REPO = Path(__file__).resolve().parents[1]
PLUGIN = REPO / "plugin/ldip-design-review"
SK = PLUGIN / "skills/reviewing-road-design/scripts"
EV = PLUGIN / "skills/reviewing-road-design/evals"
SK3 = PLUGIN / "skills/parsing-landxml/scripts"
PY = sys.executable
RUN = "NMAR-T0001-20261015T1200"
REPORT = "submittals/NMAR/T0001/X-RCRC-SAP-03-DN-DES-RPT-00001.pdf"


def run(script, *args, cwd=None):
    p = subprocess.run([PY, str(SK / script), *map(str, args)], capture_output=True, text=True, cwd=cwd)
    return p.returncode, json.loads(p.stdout.strip().splitlines()[-1])


def load(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


# ------------------------------------------------------------------ synthetic inputs
def P(value, unit=None, cap="Confirmed", enforcement=None, t=None):
    p = {"value": value, "unit": unit, "tag": "AGREED", "source_id": "SRC-T", "clause": "§T", "blocking": False, "cap": cap}
    if enforcement:
        p["enforcement"] = enforcement
    if t is not None:
        p["tolerance"] = {"value": t, "unit": unit, "tag": "AGREED"}
    return p


def R(rid, params, classes=("all",), inputs=("design_report", "typical_section", "landxml_alignment", "landxml_profile"),
      sev="Minor", state="active", req=()):
    return {"rule_id": rid, "title": f"Test rule {rid}", "skill": "reviewing-road-design", "state": state,
            "cap": "Confirmed" if state == "active" else "Probable", "statement": f"Synthetic requirement {rid}.",
            "method": "test", "inputs": list(inputs), "parameters": params, "skip_subchecks_for": [],
            "advisory_parameters": [k for k, v in params.items() if v.get("enforcement") == "advisory"],
            "code_lists": {}, "severity_default": sev, "issue_type": "design_error",
            "default_status": {"landxml": "Confirmed", "dwg_vector": "Confirmed", "script_output": "Confirmed",
                               "pdf_vector_text": "Probable", "pdf_ocr": "Probable"},
            "exclusions": [], "owner_role": "DMR", "applicability": {"street_class": list(classes)},
            "required_inputs": list(req), "check_type": "value"}


C1 = {"item": "C1 road class of every road", "carrier": ["road_hierarchy_plan", "design_report"], "required_from_phase": "PD",
      "on_missing": "finding", "source_id": "SRC-T", "clause": "§C1"}
C2 = {"item": "C2 design speed of every road", "carrier": ["design_report"], "required_from_phase": "PD",
      "on_missing": "finding", "source_id": "SRC-T", "clause": "§C2"}
C4 = {"item": "C4 alignment data", "carrier": ["landxml_alignment", "landxml_profile", "plan_geometry"],
      "required_from_phase": "DD", "on_missing": "finding", "source_id": "SRC-T", "clause": "§C4"}


def rules_file(tmp):
    """Made-up values (not the ER): local speed 35-45, collector 55-65; ROW local >= 17; etc."""
    rules = [
        R("ROD-001", {"classes": P(["arterial", "sub_collector", "local_access", "service_road"])}, req=[C1]),
        R("ROD-006", {"vd_min": P(35, "km/h"), "vd_max": P(45, "km/h")}, ["local_access"], req=[C1, C2]),
        R("ROD-007", {"vd_min": P(55, "km/h"), "vd_max": P(65, "km/h")}, ["sub_collector"], req=[C1, C2]),
        R("ROD-002", {"row_min": P(17.0, "m", t=0.05), "row_max": P(21.0, "m", "Probable", "advisory", 0.05)}, ["local_access"]),
        R("ROD-003", {"row_min": P(23.0, "m", t=0.05), "row_max": P(31.0, "m", "Probable", "advisory", 0.05)}, ["sub_collector"]),
        R("ROD-010", {"lane_min": P(3.1, "m", t=0.05)}, ["local_access"]),
        R("ROD-011", {"lane_min": P(3.2, "m", t=0.05), "lane_upper": P(3.6, "m", "Probable", "advisory", 0.05),
                      "tier2_lane": P(3.7, "m", "Probable", "advisory")}, ["sub_collector"]),
        R("ROD-014", {"median_min": P(1.6, "m", t=0.05), "tier2_median_shc": P(3.1, "m", "Probable", "advisory")}, ["sub_collector"]),
        R("ROD-016", {"separator_min": P(1.1, "m")}, ["service_road"], inputs=("typical_section",)),
        R("ROD-017", {"r_min": P(160.0, "m")}, ["sub_collector"], sev="Major"),
        R("ROD-024", {"g_max": P(5.5, "%", t=0.05)}, ["sub_collector"], sev="Major"),
        R("ROD-026", {"g_min": P(0.6, "%", t=0.05)}, sev="Minor"),
        R("ROD-028", {"k_crest_momra": P({"30": 3, "40": 5, "50": 8, "60": 12}, "m/%"), "k_crest_50_derived": P(8.5, "m/%")},
          sev="Major", req=[C2, C4]),
        R("ROD-030", {"l_min": P({"30": 55, "40": 55, "50": 55, "60": 110}, "m")}, sev="Major"),
    ]
    doc = {"rulebook_version": "v0", "agent": "RDE", "rules": rules, "rules_skipped": [],
           "stage_map": {"30% Concept Design": {"phase": "PD", "milestone": None}, "60%": {"phase": "DD", "milestone": 60}}}
    p = tmp / "rules_RDE.json"
    p.write_text(json.dumps(doc))
    return p


def span(text, x0, top, w=None):
    w = w or 4.5 * len(text)
    return {"text": text, "bbox": [x0, top, x0 + w, top + 6], "font": None, "size": None}


def row(label, values, top):
    out = [span(w, 58 + 40 * i, top) for i, w in enumerate(label.split())][:1]
    out = [span(label, 58, top, 180)]
    for x, v in zip((320, 460), values):
        if v is not None:
            out.append(span(v, x, top))
    return out


def report(tmp, with_table=True):
    """A two-page PDF (blank) and its skill-2-like ingestion with a criteria table split over the pages."""
    root = tmp / "root"
    pdfp = root / REPORT
    pdfp.parent.mkdir(parents=True, exist_ok=True)
    doc = pdfium.PdfDocument.new()
    doc.new_page(595, 842)
    doc.new_page(595, 842)
    doc.save(str(pdfp))
    doc.close()
    hdr = lambda top: [span("Parameter", 139, top, 36), span("Collector", 315, top, 31), span("Local", 446, top, 18),  # noqa: E731
                       span("/", 466, top, 4), span("Access", 472, top, 24)]
    p1 = hdr(100) + row("Functional classification", ["Urban collector", "Local / access"], 116) + \
        row("Design speed (km/h)", ["40", "30"], 132) + row("Lane width (m)", ["3.3", "3.3"], 148) + \
        [span("Report footer page 1", 280, 800, 120)]
    p2 = hdr(70) + row("Median type / width (m)", ["Raised / 1.2", "None"], 86) + \
        row("Indicative right-of-way (m)", ["25", "15/18"], 102) + row("Minimum horizontal radius (m)", ["50", "10"], 118) + \
        row("Maximum longitudinal grade", ["8%", "10%"], 134) + row("Minimum K crest values", ["6", "4"], 150) + \
        row("Minimum vertical curve length (m)", ["40", "60"], 166) + [span("1.11 Cross Sections", 54, 200, 80)] + \
        [span("DEP-01", 60, 300, 30), span("Access spacing", 120, 300, 60)]
    if not with_table:
        p1 = [span("No table on this page", 58, 100, 100)]
        p2 = [span("Nothing here", 58, 100, 60)]
    ing = {"template_version": "1.0", "source": {"path": REPORT, "sha256": hashlib.sha256(pdfp.read_bytes()).hexdigest(),
                                                  "bytes": pdfp.stat().st_size, "document_no": None, "revision": None, "file_type": "pdf"},
           "content": {"pages": [{"page": 1, "width_pt": 595, "height_pt": 842, "spans": p1},
                                 {"page": 2, "width_pt": 595, "height_pt": 842, "spans": p2}]}}
    ip = tmp / "report.pdf.json"
    ip.write_text(json.dumps(ing))
    return root, ip


def T(text, x, y, h, rot=0, layer="SC-RD-DIM"):
    return {"handle": h, "insert": [x, y, 0.0], "layer": layer, "rotation": rot, "space": "model", "text": text, "type": "TEXT"}


def sections_dwg(tmp):
    texts = [
        # section 1: 16 m local road, no element labels
        T("RIGHT OF WAY", 70, 27, "A1", 90), T("RIGHT OF WAY", 86, 27, "A2", 90),
        T("16 METER LOCAL ROAD", 72, 22, "A3", 0, "DWG_TITLE"),
        T("3.80", 74, 28.5, "A4"), T("3.20", 78, 28.5, "A5"), T("16.00", 80, 29.4, "A6"), T("3.20", 81.4, 28.5, "A7"),
        T("3.80", 85, 28.5, "A8"), T("2.5%", 79, 24.2, "A9"),
        # section 2: 24 m collector with labelled elements
        T("RIGHT OF WAY", 96, 26.2, "B1", 90), T("RIGHT OF WAY", 122, 26.2, "B2", 90),
        T("24m NEIGHBOURHOOD COLLECTOR ROAD", 103, 22, "B3", 0, "DWG_TITLE"),
        T("24.00 (RIGHT OF WAY)", 109, 31.6, "B4"), T("3.30", 103, 29.2, "B5"), T("DRIVING LANE", 103, 28.8, "B6"),
        T("1.20", 109, 28.5, "B7"), T("MEDIAN", 109, 28.1, "B8"), T("3.30", 112, 29.2, "B9"), T("DRIVING LANE", 112, 28.8, "B10"),
        T("4.60", 99, 29.2, "B11"), T("SIDE WALK", 99, 28.8, "B12"),
    ]
    ing = {"template_version": "1.0", "source": {"path": "submittals/NMAR/T0001/X-RCRC-SAP-03-DN-ROD-DWG-30000-001-00.dwg",
                                                  "sha256": "a" * 64, "bytes": 1, "document_no": "X-RCRC-SAP-03-DN-ROD-DWG-30000-001-00",
                                                  "revision": None, "file_type": "dwg"},
           "content": {"texts": texts, "dimensions": [{"handle": "D1", "layer": "DIM", "measurement": 16.0, "override": False}],
                       "layers": [], "open_polylines_by_layer": {}}}
    p = tmp / "sections.dwg.json"
    p.write_text(json.dumps(ing))
    return p


def hierarchy_dwg(tmp):
    texts = [T("RD-01", 657050.0, 2747502.0, "H1", 0, "0-Alignment Name"), T("RD-02", 658000.0, 2748000.0, "H2", 0, "0-Alignment Name"),
             T("RA-01", 658100.0, 2748100.0, "H3", 0, "0-Alignment Name")]
    ing = {"template_version": "1.0", "source": {"path": "submittals/NMAR/T0001/X-RCRC-SAP-03-DN-ROD-DWG-00003-001-00.dwg",
                                                  "sha256": "b" * 64, "bytes": 1, "document_no": None, "revision": None, "file_type": "dwg"},
           "content": {"texts": texts, "dimensions": [], "layers": [{"name": "Plan$0$16 m ROW"}, {"name": "Plan$0$24m ROW"}, {"name": "Plan$0$40m ROW"}],
                       "open_polylines_by_layer": {"Plan$0$16 m ROW": 12, "Plan$0$24m ROW": 3}}}
    p = tmp / "hierarchy.dwg.json"
    p.write_text(json.dumps(ing))
    return p


def landxml(tmp):
    x = tmp / "synthetic_t4.xml"
    subprocess.run([PY, str(EV / "make_synthetic.py"), "--out", str(x)], check=True, capture_output=True)
    out = tmp / "synthetic_t4.xml.json"
    p = subprocess.run([PY, str(SK3 / "parse_landxml.py"), "--in", str(x), "--out", str(out), "--provenance", "parsons",
                        "--source-path", "submittals/NMAR/T0001/synthetic_t4.xml"], capture_output=True, text=True)
    assert p.returncode == 0, p.stdout
    return out


@pytest.fixture
def world(tmp_path):
    root, rep = report(tmp_path)
    return {"tmp": tmp_path, "root": root, "report": rep, "rules": rules_file(tmp_path), "sections": sections_dwg(tmp_path),
            "hierarchy": hierarchy_dwg(tmp_path), "landxml": landxml(tmp_path)}


def pipeline(w, stage="30% Concept Design", with_landxml=True):
    t = w["tmp"]
    assert run("read_criteria.py", "--ingestion", w["report"], "--out", t / "criteria.json")[0] == 0
    lx = ["--landxml", w["landxml"]] if with_landxml else []
    assert run("build_register.py", "--criteria", t / "criteria.json", "--rules", w["rules"], "--hierarchy", w["hierarchy"],
               "--sections", w["sections"], *lx, "--out", t / "register.json")[0] == 0
    assert run("check_criteria.py", "--criteria", t / "criteria.json", "--rules", w["rules"], "--out", t / "cc.json")[0] == 0
    assert run("check_sections.py", "--sections", w["sections"], "--rules", w["rules"], "--out", t / "sc.json")[0] == 0
    res = [t / "cc.json", t / "sc.json", t / "register.json"]
    if with_landxml:
        assert run("check_alignment.py", "--register", t / "register.json", "--landxml", w["landxml"], "--rules", w["rules"],
                   "--out", t / "ac.json")[0] == 0
        res.append(t / "ac.json")
    assert run("check_required.py", "--stage", stage, "--rules", w["rules"], "--register", t / "register.json",
               "--criteria", t / "criteria.json", "--sections", t / "sc.json", *lx, "--out", t / "req.json")[0] == 0
    res.append(t / "req.json")
    return res


def fails(path):
    return {(c["rule_id"], c["parameter"], c.get("class")) for c in load(path)["checks"] if c["result"] == "fail"}


# ------------------------------------------------------------------ tests
def test_read_criteria_two_pages(world):
    t = world["tmp"]
    rc, out = run("read_criteria.py", "--ingestion", world["report"], "--out", t / "c.json")
    assert rc == 0 and out["tables"] == 1
    d = load(t / "c.json")
    assert d["tables"][0]["pages"] == [1, 2]
    sc, la = d["criteria"]["sub_collector"], d["criteria"]["local_access"]
    assert sc["design_speed"]["numbers"] == [40.0] and la["design_speed"]["numbers"] == [30.0]
    assert la["row"]["numbers"] == [15.0, 18.0] and sc["median"]["numbers"] == [1.2] and la["median"]["numbers"] == []
    assert "footer" not in json.dumps(d["tables"][0]["rows"]) and "Cross Sections" not in json.dumps(d["tables"][0]["rows"])
    assert [x["ref"] for x in d["departures"]] == ["DEP-01"]


def test_read_criteria_no_table(tmp_path):
    _, rep = report(tmp_path, with_table=False)
    rc, out = run("read_criteria.py", "--ingestion", rep, "--out", tmp_path / "c.json")
    assert rc == 0 and out["tables"] == 0
    assert load(tmp_path / "c.json")["warnings"][0]["code"] == "NO_CRITERIA_TABLE"


def test_criteria_check(world):
    pipeline(world)
    f = fails(world["tmp"] / "cc.json")
    assert ("ROD-006", "vd_min", "local_access") in f and ("ROD-007", "vd_min", "sub_collector") in f
    assert ("ROD-002", "row_min", "local_access") in f            # 15 < 17 (18 passes)
    assert ("ROD-014", "median_min", "sub_collector") in f and ("ROD-014", "tier2_median_shc", "sub_collector") in f
    assert ("ROD-017", "r_min", "sub_collector") in f and ("ROD-024", "g_max", "sub_collector") in f
    # D10-5: collector stated 40 < ER minimum 55 -> checked at 40 and at 55; 55 is not a key -> not run, noted
    cc = load(world["tmp"] / "cc.json")
    k = [c for c in cc["checks"] if c["rule_id"] == "ROD-028" and c["class"] == "sub_collector"]
    assert [c["speed_kmh"] for c in k] == [40.0] and k[0]["result"] == "pass"          # 6 >= 5
    assert any(n["rule_id"] == "ROD-028" and "55" in n["reason"] for n in cc["not_run"])
    # local stated 30 < 35: 30 and 35 checked for L (55 both) -> 60 passes; 35 not a key -> not run
    assert ("ROD-030", "l_min", "local_access") not in f and ("ROD-030", "l_min", "sub_collector") in f
    # ROD-016 lists no design_report and does not apply to these classes -> not listed at all
    assert not any(n["rule_id"] == "ROD-016" for n in cc["not_run"])


def test_design_report_input_gate(world, tmp_path):
    rb = load(world["rules"])
    for r in rb["rules"]:
        if r["rule_id"] == "ROD-017":
            r["inputs"] = ["landxml_alignment"]
    p = tmp_path / "rules2.json"
    p.write_text(json.dumps(rb))
    pipeline(world)
    run("check_criteria.py", "--criteria", world["tmp"] / "criteria.json", "--rules", p, "--out", tmp_path / "cc2.json")
    d = load(tmp_path / "cc2.json")
    assert not any(c["rule_id"] == "ROD-017" for c in d["checks"])
    assert any(n["rule_id"] == "ROD-017" and "rules_v3" in n["reason"] for n in d["not_run"])


def test_sections(world):
    pipeline(world)
    sc = load(world["tmp"] / "sc.json")
    by = {s["title"]: s for s in sc["sections"]}
    assert set(by) == {"16 METER LOCAL ROAD", "24m NEIGHBOURHOOD COLLECTOR ROAD"}
    loc = {(e["element"], e["value"], e["assigned"]) for e in by["16 METER LOCAL ROAD"]["elements"]}
    assert ("row", 16.0, "labelled") in loc and ("lane", 3.2, "positional") in loc and ("sidewalk", 3.8, "positional") in loc
    col = {(e["element"], e["value"], e["assigned"]) for e in by["24m NEIGHBOURHOOD COLLECTOR ROAD"]["elements"]}
    assert ("median", 1.2, "labelled") in col and ("lane", 3.3, "labelled") in col and ("row", 24.0, "labelled") in col
    f = fails(world["tmp"] / "sc.json")
    assert ("ROD-002", "row_min", "local_access") in f and ("ROD-014", "median_min", "sub_collector") in f
    assert ("ROD-011", "tier2_lane", "sub_collector") in f and ("ROD-003", "row_min", "sub_collector") not in f
    pos = [c for c in sc["checks"] if c["assigned"] == "positional"]
    assert pos and all(c["link_cap"] == "Probable" for c in pos)


def test_register(world):
    pipeline(world)
    reg = load(world["tmp"] / "register.json")
    assert sorted(reg["classes"]) == ["local_access", "sub_collector"]
    assert sorted(g["width_m"] for g in reg["row_groups"]) == [16.0, 24.0, 40.0]
    assert sorted(reg["roads"]) == ["RA-01", "RD-01", "RD-02"]
    al = reg["alignments"][0]
    assert al["class"] == "sub_collector" and al["design_speed_kmh"] == 40.0
    assert al["road"] == "RD-01" and al["link_method"] == "position"          # label 2 m off the first tangent
    assert all(c["result"] == "pass" for c in reg["checks"] if c["rule_id"] == "ROD-001")


def test_register_unknown_term_and_conflict(world, tmp_path):
    pipeline(world)
    crit = load(world["tmp"] / "criteria.json")
    crit["tables"][0]["headings"].append("Boulevard")
    crit["tables"][0]["classes"].append("boulevard")
    crit["criteria"]["local_access"]["row"]["numbers"] = [24.0]            # conflicts with the 24 m collector section
    p = tmp_path / "crit2.json"
    p.write_text(json.dumps(crit))
    run("build_register.py", "--criteria", p, "--rules", world["rules"], "--sections", world["sections"], "--out", tmp_path / "r2.json")
    chk = load(tmp_path / "r2.json")["checks"]
    assert any(c["class"] == "boulevard" and c["result"] == "fail" for c in chk)
    assert any(c["criterion"] == "class_by_row" and c["result"] == "fail" and c["issue_type"] == "inconsistency" for c in chk)


def test_alignment(world):
    pipeline(world)
    ac = load(world["tmp"] / "ac.json")
    f = {(c["rule_id"], c["parameter"], c.get("speed_kmh")) for c in ac["checks"] if c["result"] == "fail"}
    assert ("ROD-017", "r_min", None) in f and ("ROD-024", "g_max", None) in f
    assert ("ROD-028", "k_crest_momra", 40.0) not in f        # K 5 >= 5 at 40
    assert ("ROD-030", "l_min", 40.0) in f                    # 40 < 55
    assert all(c.get("link_cap") == "Probable" for c in ac["checks"] if c["rule_id"] != "ROD-026")
    assert not any(c.get("link_cap") for c in ac["checks"] if c["rule_id"] == "ROD-026")


def test_required_stage_gate(world, tmp_path):
    pipeline(world, with_landxml=False)
    items = {i["key"]: i for i in load(world["tmp"] / "req.json")["items"]}
    assert items["C1"]["result"] == "pass" and items["C2"]["result"] == "pass"
    assert items["C4"]["result"] == "not_run" and items["C4"]["reason"] == "not required at stage"
    run("check_required.py", "--stage", "60%", "--rules", world["rules"], "--register", world["tmp"] / "register.json",
        "--out", tmp_path / "r60.json")
    it = {i["key"]: i for i in load(tmp_path / "r60.json")["items"]}
    assert it["C4"]["result"] == "not_run" and "plan_geometry" in it["C4"]["reason"]
    assert it["C2"]["result"] == "pass"          # derived from the register
    # no criteria table anywhere -> C2 Missing information
    _, rep = report(tmp_path / "empty", with_table=False)
    run("read_criteria.py", "--ingestion", rep, "--out", tmp_path / "c0.json")
    run("build_register.py", "--criteria", tmp_path / "c0.json", "--rules", world["rules"], "--out", tmp_path / "reg0.json")
    rc, out = run("check_required.py", "--stage", "30% Concept Design", "--rules", world["rules"], "--register",
                  tmp_path / "reg0.json", "--criteria", tmp_path / "c0.json", "--out", tmp_path / "r0.json")
    assert out["items"]["C2"] == "fail" and out["items"]["C1"] == "fail"
    assert run("check_required.py", "--stage", "45%", "--rules", world["rules"], "--register", tmp_path / "reg0.json",
               "--out", tmp_path / "x.json")[0] == 2


def test_candidates_end_to_end(world, tmp_path):
    res = pipeline(world)
    out = world["root"] / "rde/candidates.json"
    rc, o = run("build_candidates.py", "--results", *res, "--rules", world["rules"], "--run", RUN, "--site", "NMAR",
                "--sub", "T0001", "--evidence-dir", world["root"] / "rde/evidence", "--review-root", world["root"], "--out", out)
    assert rc == 0, o
    fs = load(out)
    assert all(f["agent"] == "RDE" and f["finding_id"].startswith(RUN + "/RDE-") for f in fs)
    by = {}
    for f in fs:
        by.setdefault((f["reference"]["rule_id"], f["reference"]["parameter"]), []).append(f)
    crit = [f for f in by[("ROD-002", "row_min")] if f["evidence"][0]["source_type"] == "pdf_vector_text"][0]
    assert crit["proposed_status"] == "Probable" and crit["evidence"][0]["artifact_path"].startswith("rde/evidence/")
    sec = [f for f in by[("ROD-014", "median_min")] if f["evidence"][0]["source_type"] == "dwg_vector"][0]
    assert sec["proposed_status"] == "Confirmed" and sec["location"]["dwg"]["handles"][0] == "B7"
    t2 = by[("ROD-014", "tier2_median_shc")]
    assert len(t2) == 1 and t2[0]["severity"] == "Observation" and t2[0]["proposed_status"] == "Probable"
    l30 = [f for f in fs if f["reference"]["rule_id"] == "ROD-030" and f["evidence"][0]["source_type"] == "pdf_vector_text"]
    assert len(l30) == 1 and "40 km/h (stated)" in l30[0]["statement"] and "55" not in l30[0]["title"]   # one finding (D10-5)
    al = [f for f in by[("ROD-017", "r_min")] if f["evidence"][0]["source_type"] == "landxml"][0]
    assert al["proposed_status"] == "Probable" and al["location"]["xy"]["crs"] == "EPSG:32638"
    rc, o = run("validate_findings.py", "--findings", out, "--rules", world["rules"], "--review-root", world["root"])
    assert rc == 0, o
    # FC5: raise a Probable (advisory) finding to Confirmed -> refused
    bad = json.loads(json.dumps(fs))
    for f in bad:
        if f["reference"]["parameter"] == "tier2_median_shc":
            f["proposed_status"] = "Confirmed"
    (tmp_path / "bad.json").write_text(json.dumps(bad))
    assert run("validate_findings.py", "--findings", tmp_path / "bad.json", "--rules", world["rules"],
               "--review-root", world["root"])[0] == 4


def test_missing_information_finding(world, tmp_path):
    _, rep = report(tmp_path / "e", with_table=False)
    run("read_criteria.py", "--ingestion", rep, "--out", tmp_path / "c0.json")
    run("build_register.py", "--criteria", tmp_path / "c0.json", "--rules", world["rules"], "--out", tmp_path / "reg0.json")
    run("check_required.py", "--stage", "30% Concept Design", "--rules", world["rules"], "--register", tmp_path / "reg0.json",
        "--criteria", tmp_path / "c0.json", "--searched", REPORT, "--out", tmp_path / "r0.json")
    rc, o = run("build_candidates.py", "--results", tmp_path / "r0.json", "--rules", world["rules"], "--run", RUN, "--site", "NMAR",
                "--sub", "T0001", "--evidence-dir", tmp_path / "ev", "--review-root", world["root"], "--out", tmp_path / "mi.json")
    assert rc == 0, o
    fs = load(tmp_path / "mi.json")
    assert {f["issue_type"] for f in fs} == {"missing_information"} and {f["proposed_status"] for f in fs} == {"Missing information"}
    assert all(REPORT in f["statement"] for f in fs)


def test_synthetic_t4_reproducible(tmp_path):
    subprocess.run([PY, str(EV / "make_synthetic.py"), "--out", str(tmp_path / "t4.xml")], check=True, capture_output=True)
    assert (tmp_path / "t4.xml").read_text(encoding="utf-8") == (EV / "synthetic_t4.xml").read_text(encoding="utf-8")


def test_skill_layout():
    skill = PLUGIN / "skills/reviewing-road-design"
    text = (skill / "SKILL.md").read_text(encoding="utf-8")
    fm = text.split("---", 2)[1]
    assert "name: reviewing-road-design" in fm and len(text.splitlines()) < 500
    for f in ("references/road_checks.md", "templates/finding.json", "evals/evals.json"):
        assert (skill / f).is_file()
    src = "".join(p.read_text(encoding="utf-8") for p in (skill / "scripts").glob("*.py"))
    for bad in ("import subprocess", "import socket", "import requests", "urllib", "os.system", "eval(", "exec("):
        assert bad not in src
