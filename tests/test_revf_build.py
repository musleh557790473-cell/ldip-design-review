"""Tests for the SPC-0006 Rev F build (session 23): skill 1 reader chain, block expansion and linework
(§4.1A, T-F6 ... T-F8), skill 2 --paths (DF-5), skill 10 criteria reader K24 (T-F9) and phase B2 location
register (§4.13C, TC10, TC11). Synthetic data only; the real-data checks (T-F1 ... T-F4, TC1 ... TC9) are in
test_revf_real.py and run only on a local folder (brief §10.2)."""
import hashlib
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

ezdxf = pytest.importorskip("ezdxf")
pytest.importorskip("jsonschema")
pytest.importorskip("shapely")

REPO = Path(__file__).resolve().parents[1]
PLUGIN = REPO / "plugin/ldip-design-review"
SK1 = PLUGIN / "skills/extracting-dwg-data/scripts"
SK2 = PLUGIN / "skills/extracting-pdf-drawings/scripts"
SK10 = PLUGIN / "skills/reviewing-road-design/scripts"
PY = sys.executable


def run(sk, script, *args, env=None, cwd=None):
    p = subprocess.run([PY, str(sk / script), *map(str, args)], capture_output=True, text=True,
                       env={**os.environ, **(env or {})}, cwd=cwd)
    out = p.stdout.strip().splitlines()
    return p.returncode, (json.loads(out[-1]) if out else {"stderr": p.stderr[-2000:]})


def load(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def fake_dwg(path, body=b"\x00" * 64):
    path.write_bytes(b"AC1032" + body)
    return path


def convert_log(path, dwg, dxf, engine="ezdwg"):
    path.write_text(json.dumps({"status": "ok", "engine": engine, "engine_version": "test", "warnings": [],
                                "input": {"sha256": sha(dwg), "acadver": "AC1032"},
                                "output": {"sha256": sha(dxf)}}))
    return path


# ------------------------------------------------------------------ skill 1: block expansion (T-F6)
def nested_dxf(path):
    doc = ezdxf.new("R2018")
    doc.header["$INSUNITS"] = 6
    inner = doc.blocks.new("INNER")
    inner.add_line((0, 0), (10, 0), dxfattribs={"layer": "RD_CL"})
    inner.add_text("R.1.B.1", dxfattribs={"layer": "0-Alignment Name", "insert": (1, 1), "height": 1})
    inner.add_lwpolyline([(0, 0), (2, 0), (2, 2), (0, 2)], close=True, dxfattribs={"layer": "0"})
    outer = doc.blocks.new("XR_Layout")
    outer.add_blockref("INNER", (100, 0), dxfattribs={"rotation": 90, "xscale": 2, "yscale": 2})
    outer.add_arc((0, 0), 5, 0, 90, dxfattribs={"layer": "XR_Layout$0$15 m ROW"})
    tb = doc.blocks.new("TB_A1")
    tb.add_attdef("DWG_NO", (0, 0), dxfattribs={"height": 1})
    tb.add_line((0, 0), (1, 1), dxfattribs={"layer": "TB_LINE"})
    msp = doc.modelspace()
    msp.add_blockref("XR_Layout", (1000, 2000), dxfattribs={"layer": "XREF_LAYER"})
    msp.add_blockref("TB_A1", (0, 0)).add_auto_attribs({"DWG_NO": "X"})
    msp.add_line((0, 0), (3, 4), dxfattribs={"layer": "RD_CL"})
    doc.saveas(path)
    return path


def test_block_expansion_and_linework(tmp_path):
    dxf = nested_dxf(tmp_path / "X.dxf")
    dwg = fake_dwg(tmp_path / "X.dwg")
    log = convert_log(tmp_path / "X.convert.json", dwg, dxf)
    rc, out = run(SK1, "extract_dxf.py", "--dxf", dxf, "--dwg", dwg, "--convert-log", log, "--out",
                  tmp_path / "X.dwg.json", "--linework")
    assert rc == 0, out
    ing = load(tmp_path / "X.dwg.json")
    assert ing["template_version"] == "1.2"
    c = ing["content"]
    txt = [t for t in c["texts"] if t.get("via_block")]
    assert len(txt) == 1 and txt[0]["via_block"] == ["XR_Layout", "INNER"] and txt[0]["layer_short"] == "0-Alignment Name"
    # insert (100,0) rotated 90 scaled 2 inside an insert at (1000, 2000): (1,1) -> (100-2, 0+2) -> (1098, 2002)
    assert txt[0]["insert"][:2] == [1098.0, 2002.0]
    poly = [p for p in c["polylines"] if p.get("via_block")]
    assert len(poly) == 1 and poly[0]["layer"] == "XREF_LAYER" and poly[0]["area"] == 16.0      # layer 0 -> insert layer
    assert c["block_expansion"]["inserts_expanded"] == 1 and c["block_expansion"]["inserts_skipped_title_block"] >= 1
    assert c["side_files"][0]["kind"] == "linework"
    lw = load(tmp_path / c["side_files"][0]["path"])
    lines = [r for r in lw["records"] if r["layer"] == "RD_CL"]
    assert len(lines) == 2
    nested = [r for r in lines if r["via_block"]][0]
    assert nested["points"][0] == [1100.0, 2000.0] and nested["points"][-1] == [1100.0, 2020.0] and nested["length"] == 20.0
    arc = [r for r in lw["records"] if r["layer_short"] == "15 m ROW"][0]
    assert arc["type"] == "ARC" and len(arc["points"]) > 5 and arc["closed"] is False
    assert not [r for r in lw["records"] if r["layer"] == "TB_LINE"]                       # title block not expanded
    assert run(SK1, "validate_ingestion.py", "--files", tmp_path / "X.dwg.json")[0] == 0
    # no expansion: the 0.1 content
    rc, _ = run(SK1, "extract_dxf.py", "--dxf", dxf, "--dwg", dwg, "--convert-log", log, "--out",
                tmp_path / "Y" / "X.dwg.json", "--no-expand")
    assert rc == 0 and not [t for t in load(tmp_path / "Y" / "X.dwg.json")["content"]["texts"] if t.get("via_block")]


def test_linework_negative_cases(tmp_path):
    """T-F8: a damaged linework file or a wrong side_files entry fails validation (exit 4)."""
    dxf = nested_dxf(tmp_path / "X.dxf")
    dwg = fake_dwg(tmp_path / "X.dwg")
    log = convert_log(tmp_path / "X.convert.json", dwg, dxf)
    assert run(SK1, "extract_dxf.py", "--dxf", dxf, "--dwg", dwg, "--convert-log", log, "--out",
               tmp_path / "X.dwg.json", "--linework")[0] == 0
    ing_p, lw_p = tmp_path / "X.dwg.json", tmp_path / "X.linework.json"
    good = lw_p.read_text()
    lw = json.loads(good)
    del lw["records"][0]["points"]
    lw_p.write_text(json.dumps(lw))
    ing = load(ing_p)
    ing["content"]["side_files"][0]["sha256"] = sha(lw_p)
    ing_p.write_text(json.dumps(ing))
    rc, out = run(SK1, "validate_ingestion.py", "--files", ing_p)
    assert rc == 4 and "points" in json.dumps(out)
    lw_p.write_text(good)
    ing["content"]["side_files"][0].update(sha256=sha(lw_p), records=1)
    ing_p.write_text(json.dumps(ing))
    rc, out = run(SK1, "validate_ingestion.py", "--files", ing_p)
    assert rc == 4 and "record count" in json.dumps(out)
    ing["content"]["side_files"][0]["path"] = "../X.linework.json"
    ing_p.write_text(json.dumps(ing))
    assert run(SK1, "validate_ingestion.py", "--files", ing_p)[0] == 4


def test_manifest_lists_linework(tmp_path):
    b = tmp_path / "0.ai_context/QRWN/T0001/b01"
    b.mkdir(parents=True)
    dxf = nested_dxf(b / "X.dxf")
    src = tmp_path / "submittals/QRWN/T0001"
    src.mkdir(parents=True)
    dwg = fake_dwg(src / "X.dwg")
    log = convert_log(b / "X.convert.json", dwg, dxf)
    assert run(SK1, "extract_dxf.py", "--dxf", dxf, "--dwg", dwg, "--convert-log", log, "--out", b / "X.dwg.json",
               "--source-path", "submittals/QRWN/T0001/X.dwg", "--linework")[0] == 0
    rc, out = run(SK1, "write_manifest.py", "--out", b / "batch_manifest.json", "--run", "QRWN-T0001-20261016T0100",
                  "--site", "QRWN", "--sub", "T0001", "--file", "submittals/QRWN/T0001/X.dwg", sha(dwg))
    assert rc == 0, out
    e = load(b / "batch_manifest.json")["files"][0]
    assert e["status"] == "extracted" and e["side_files"] == ["X.linework.json"] and "X.linework.json" not in e["intermediates"]


# ------------------------------------------------------------------ skill 1: reader chain (T-F7)
def fake_reader(path, dxf_src=None):
    """A stand-in for dwg2dxf: copies a DXF to the -o target, or fails."""
    body = ("import shutil, sys\nargs = sys.argv[1:]\n"
            "if args and args[0] == '--version':\n    print('dwg2dxf 0.0-test'); sys.exit(0)\n"
            + (f"shutil.copy({str(dxf_src)!r}, args[args.index('-o') + 1]); sys.exit(0)\n" if dxf_src
               else "print('cannot read', file=sys.stderr); sys.exit(1)\n"))
    script = path.with_suffix(".py")
    script.write_text(body)
    path.write_text(f"#!/bin/sh\nexec {PY} {script} \"$@\"\n")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return path


@pytest.mark.skipif(os.name == "nt", reason="shell stand-in for dwg2dxf")
def test_reader_chain(tmp_path):
    pytest.importorskip("ezdwg")
    dwg = fake_dwg(tmp_path / "bad.dwg", os.urandom(4000))
    env = {"LDIP_LIBREDWG_EXE": ""}
    rc, out = run(SK1, "convert.py", "--engine", "chain", "--dwg", dwg, "--out", tmp_path / "a.dxf",
                  "--log", tmp_path / "a.convert.json", env=env)
    assert rc == 3 and out["next"] == "local_autocad"
    log = load(tmp_path / "a.convert.json")
    assert [s["engine"] for s in log["chain"]] == ["ezdwg", "libredwg"]
    assert log["chain"][0]["result"] == "failed" and log["chain"][1]["result"] == "not_enabled"
    # LibreDWG enabled but also failing: both errors kept
    rc, out = run(SK1, "convert.py", "--engine", "chain", "--dwg", dwg, "--out", tmp_path / "b.dxf",
                  "--log", tmp_path / "b.convert.json", env={"LDIP_LIBREDWG_EXE": str(fake_reader(tmp_path / "fail.sh"))})
    log = load(tmp_path / "b.convert.json")
    assert rc == 3 and [s["result"] for s in log["chain"]] == ["failed", "failed"] and log["next"] == "local_autocad"
    # LibreDWG converts after ezdwg fails: engine libredwg, fallback_from, ENGINE_FALLBACK
    good = nested_dxf(tmp_path / "good.dxf")
    rc, out = run(SK1, "convert.py", "--engine", "chain", "--dwg", dwg, "--out", tmp_path / "c.dxf",
                  "--log", tmp_path / "c.convert.json",
                  env={"LDIP_LIBREDWG_EXE": str(fake_reader(tmp_path / "ok.sh", good))})
    log = load(tmp_path / "c.convert.json")
    assert rc == 0 and log["engine"] == "libredwg" and log["requested_engine"] == "chain"
    assert log["fallback_from"].startswith("ezdwg:") and "ENGINE_FALLBACK" in [w["code"] for w in log["warnings"]]
    assert log["engine_version"] == "dwg2dxf 0.0-test"
    # the extraction accepts the fallback log, and --reuse keeps it
    assert run(SK1, "extract_dxf.py", "--dxf", tmp_path / "c.dxf", "--dwg", dwg, "--convert-log", tmp_path / "c.convert.json",
               "--out", tmp_path / "bad.dwg.json")[0] == 0
    rc, out = run(SK1, "convert.py", "--engine", "chain", "--dwg", dwg, "--out", tmp_path / "c.dxf",
                  "--log", tmp_path / "c.convert.json", "--reuse", env={"LDIP_LIBREDWG_EXE": ""})
    assert rc == 0 and out["reused"] and out["engine"] == "libredwg"


# ------------------------------------------------------------------ skill 2: --paths (DF-5)
def coloured_pdf(path):
    """Minimal one-page PDF: a red line, a magenta line, a grey line and a black text."""
    content = (b"1 0 0 RG 2 w 50 50 m 200 50 l S\n1 0.498 1 RG 1 w 50 100 m 200 150 l S\n"
               b"0.5 0.5 0.5 RG 50 200 m 200 200 l S\nBT /F1 12 Tf 60 300 Td (ROAD) Tj ET\n")
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 400 400] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
            b"<< /Length %d >>\nstream\n" % len(content) + content + b"endstream",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out, offs = b"%PDF-1.4\n", []
    for i, o in enumerate(objs, 1):
        offs.append(len(out))
        out += b"%d 0 obj\n" % i + o + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1) + b"".join(b"%010d 00000 n \n" % o for o in offs)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
    path.write_bytes(out)
    return path


def test_pdf_paths(tmp_path):
    pytest.importorskip("pdfplumber")
    pytest.importorskip("pypdfium2")
    pdf = coloured_pdf(tmp_path / "H.pdf")
    rc, out = run(SK2, "extract_pdf.py", "--pdf", pdf, "--out", tmp_path / "H.pdf.json", "--paths")
    assert rc == 0, out
    ing = load(tmp_path / "H.pdf.json")
    assert ing["template_version"] == "1.2" and ing["content"]["side_files"][0]["path"] == "H.paths.json"
    pj = load(tmp_path / "H.paths.json")
    cols = pj["pages"][0]["colours"]
    assert set(cols) == {"(1.000,0.000,0.000)", "(1.000,0.498,1.000)"}                  # grey counted only
    assert pj["pages"][0]["colour_counts"]["(0.500,0.500,0.500)"] == 1
    red = cols["(1.000,0.000,0.000)"]["paths"][0]
    assert red["kind"] == "stroke" and red["points"] == [[50.0, 350.0], [200.0, 350.0]]   # top-left origin
    assert run(SK1, "validate_ingestion.py", "--files", tmp_path / "H.pdf.json")[0] == 0
    rc, _ = run(SK2, "extract_pdf.py", "--pdf", pdf, "--out", tmp_path / "n" / "H.pdf.json")
    assert rc == 0 and "side_files" not in load(tmp_path / "n" / "H.pdf.json")["content"]


# ------------------------------------------------------------------ skill 10: criteria reader K24 (T-F9)
def span(text, x0, top, w=None):
    return {"text": text, "bbox": [x0, top, x0 + (w or 4.5 * len(text)), top + 6], "font": None, "size": None}


def qrwn_like_report(tmp, unread=False):
    sp = [span("Parameter", 118, 76, 36), span("Collector", 308, 76, 31), span("Local / Access", 461, 76, 49),
          span("A. CLASSIFICATION, SPEED AND DESIGN VEHICLE", 215, 91, 165),
          span("Urban collector (30m &", 229, 107, 76), span("Urban collector", 348, 107, 50),
          span("Functional classification", 96, 113.5, 79), span("Local / access", 462, 113.5, 47),
          span("36m)", 258, 118.6, 17), span("(25m)", 363, 118.6, 19)]
    rows = [("Design speed (km/h)", ["40", "40", "20"]), ("Lane width (m)", ["3.3", "3.3", "3.3"]),
            ("Median type / width (m)", ["Raised / 4m", "None", "None"]), ("Indicative right-of-way (m)", ["36/30", "25", "15/18"]),
            ("Minimum horizontal radius (m)", ["50", "50", "10"])]
    top = 135.7
    for i, (lab, vals) in enumerate(rows):
        if i == 2:
            sp.append(span("B. CROSS SECTION ELEMENTS", 247, top, 102))
            top += 15.7
        sp.append(span(lab, 90, top, 90))
        for x, v in zip((262, 368, 481), vals):
            sp.append(span(v, x - 2 * len(v), top, 4.5 * len(v)))
        top += 15.7
    sp.append(span("9.9 Cross Sections", 55, top + 12, 127))
    if unread:
        sp = [s for s in sp[:3]] + [span("A. CLASSIFICATION, SPEED AND DESIGN VEHICLE", 215, 91, 165)]
    ing = {"template_version": "1.0", "source": {"path": "submittals/QRWN/T/R.pdf", "sha256": "c" * 64, "bytes": 1,
                                                  "document_no": None, "revision": None, "file_type": "pdf"},
           "content": {"pages": [{"page": 141, "width_pt": 595, "height_pt": 842, "spans": sp}]}}
    p = tmp / ("report_unread.pdf.json" if unread else "report.pdf.json")
    p.write_text(json.dumps(ing))
    return p


def test_criteria_three_columns_two_headings(tmp_path):
    rc, out = run(SK10, "read_criteria.py", "--ingestion", qrwn_like_report(tmp_path), "--out", tmp_path / "c.json")
    assert rc == 0 and out["columns"] == 3
    d = load(tmp_path / "c.json")
    cols = d["columns"]
    assert [c["class"] for c in cols] == ["sub_collector", "sub_collector", "local_access"]
    assert [c["row_widths"] for c in cols] == [[30.0, 36.0], [25.0], [15.0, 18.0]]
    assert cols[0]["sub_heading"] == "Urban collector (30m & 36m)"
    assert [c["criteria"]["design_speed"]["numbers"] for c in cols] == [[40.0], [40.0], [20.0]]
    assert cols[0]["criteria"]["median"]["text"] == "Raised / 4m" and cols[1]["criteria"]["median"]["text"] == "None"
    assert d["criteria"]["sub_collector"]["median"]["text"] == "Raised / 4m"
    assert "CLASS_COLUMNS_REPEATED" in [w["code"] for w in d["warnings"]]
    assert "Cross Sections" not in json.dumps(d["tables"])


def test_criteria_columns_checked_once(tmp_path):
    rules = {"agent": "RDE", "rulebook_version": "v99", "rules_skipped": [], "stage_map": {},
             "rules": [{"rule_id": "ROD-014", "applicability": {"street_class": ["sub_collector"]}, "inputs": ["design_report"],
                        "parameters": {"median_min": {"value": 5.0, "unit": "m", "tag": "AGREED"}}},
                       {"rule_id": "ROD-007", "applicability": {"street_class": ["sub_collector"]}, "inputs": ["design_report"],
                        "parameters": {"vd_min": {"value": 50, "unit": "km/h", "tag": "AGREED"}}}]}
    (tmp_path / "r.json").write_text(json.dumps(rules))
    run(SK10, "read_criteria.py", "--ingestion", qrwn_like_report(tmp_path), "--out", tmp_path / "c.json")
    rc, _ = run(SK10, "check_criteria.py", "--criteria", tmp_path / "c.json", "--rules", tmp_path / "r.json", "--out", tmp_path / "cc.json")
    cc = load(tmp_path / "cc.json")["checks"]
    speed = [c for c in cc if c["rule_id"] == "ROD-007"]
    assert rc == 0 and len(speed) == 1                       # same printed speed in both collector columns: once
    med = [c for c in cc if c["rule_id"] == "ROD-014"]
    assert len(med) == 1 and med[0]["observed"] == 4.0       # 'None' in the 25 m column is no median


def test_criteria_table_unread(tmp_path):
    rc, out = run(SK10, "read_criteria.py", "--ingestion", qrwn_like_report(tmp_path, unread=True), "--out", tmp_path / "c.json")
    d = load(tmp_path / "c.json")
    assert rc == 0 and out["tables"] == 1 and d["criteria"] == {} and d["warnings"][-1]["code"] == "CRITERIA_TABLE_UNREAD"
    rules = {"agent": "RDE", "rulebook_version": "v99", "rules_skipped": [],
             "stage_map": {"30% Concept Design": {"phase": "PD", "milestone": None}},
             "rules": [{"rule_id": "ROD-006", "required_inputs": [{"item": "C2 design criteria", "carrier": ["design_report"],
                                                                   "required_from_phase": "PD"}]}]}
    (tmp_path / "r.json").write_text(json.dumps(rules))
    (tmp_path / "reg.json").write_text(json.dumps({"classes": {}, "sections": [], "row_groups": []}))
    run(SK10, "check_required.py", "--stage", "30% Concept Design", "--rules", tmp_path / "r.json", "--register",
        tmp_path / "reg.json", "--criteria", tmp_path / "c.json", "--out", tmp_path / "req.json")
    item = load(tmp_path / "req.json")["items"][0]
    assert item["key"] == "C2" and item["result"] == "not_run" and "CRITERIA_TABLE_UNREAD" in item["reason"]


# ------------------------------------------------------------------ skill 10: setting-out tables (DF-4)
def T(text, x, y, h=2.5, handle=None):
    return {"type": "MTEXT", "text": text, "layer": "RD_C_TEXT", "insert": [x, y, 0.0], "height": h,
            "rotation": 0.0, "handle": handle or f"{abs(hash((text, x, y))) % 0xFFFFF:X}", "space": "model"}


HEAD = ["TAG ID", "CONTROL \nPOINT", "STATION \n(m)", "NORTHING \n(m)", "EASTING \n(m)", "RADIUS\n(m)", "DELTA \nΔ", "L\n(m)"]


def table(x0, y0, name, rows, garble_northing=False):
    out = [T(f"ALIGNMENT NAME: {name}", x0 + 130, y0, 5.0)]
    for i, hd in enumerate(HEAD):
        if garble_northing and hd.startswith("NORTHING"):
            hd = "一伀刀吀"
        out.append(T(hd, x0 + 25 * i, y0 - 15, 3.5))
    y = y0 - 28
    for tag, ctrl, n, e, r in rows:
        out += [T(tag, x0 + 2, y), T(ctrl, x0 + 25, y), T("0+000.000", x0 + 50, y), T(n, x0 + 75, y), T(e, x0 + 100, y),
                T(r or "-", x0 + 125, y), T("-", x0 + 150, y), T("100.000", x0 + 175, y)]
        y -= 14
    return out


def sheet(tmp, name, texts):
    ing = {"template_version": "1.2", "source": {"path": f"submittals/NRJS/T/{name}.dwg", "sha256": hashlib.sha256(name.encode()).hexdigest(),
                                                  "bytes": 1, "document_no": None, "revision": None, "file_type": "dwg"},
           "content": {"texts": texts}}
    p = tmp / f"{name}.dwg.json"
    p.write_text(json.dumps(ing))
    return p


def tables_world(tmp, variant=False):
    """Three roads: A along E (0-200), B north from A at E 100 (a T junction), C east of A's end with a
    2 m drafting gap; plus a roundabout RA-1 (two half circles) where B ends."""
    A = [("L1", "POB\nPOE", "2750000.000\n2750000.000", "660000.000\n660200.000", None)]
    B = [("L2", "POB\nPOE", "2750000.000\n2750100.000", "660100.000\n660100.000", None)]
    C = [("L3", "POB\nPOE", "2750000.000\n2750000.000", "660202.000\n660300.000" if not variant else "660202.000\n660310.000", None)]
    RA = [("C-1", "-\nPI\n-", "2750120.200\n2750120.200", "660100.000\n660100.000", "20.2")]
    RA2 = [("C-1", "-\nPI\n-", "2750100.000\n2750140.400", "660100.000\n660100.000", "20.2"),
           ("C-2", "-\nPI\n-", "2750140.400\n2750100.000", "660100.000\n660100.000", "20.2")]
    t = table(0, 1000, "ROAD-A (15m R.O.W)", A, garble_northing=True) + table(260, 1000, "ROAD-B (18m R.O.W)", B) \
        + table(0, 900, "ROAD-C (15m R.O.W)", C) + table(260, 900, "RA-1", RA2)
    del RA
    return t


def test_read_setout_tables(tmp_path):
    s1 = sheet(tmp_path, "S1", tables_world(tmp_path))
    s2 = sheet(tmp_path, "S2", tables_world(tmp_path))
    s3 = sheet(tmp_path, "S3", tables_world(tmp_path, variant=True))
    rc, out = run(SK10, "read_setout.py", "--ingestion", s1, s2, s3, "--out", tmp_path / "so.json")
    assert rc == 0 and out["alignments"] == 4 and out["segments"] == 5
    d = load(tmp_path / "so.json")
    a = d["alignments"]["ROAD-A (15m R.O.W)"]
    assert a["segments"][0]["points"] == [[660000.0, 2750000.0], [660200.0, 2750000.0]]      # garbled header placed
    assert len(a["sheets"]) == 3 and len(d["alignments"]["ROAD-C (15m R.O.W)"]["sheets"]) == 2
    assert [w["code"] for w in d["warnings"]] == ["SETOUT_SHEETS_DIFFER"]
    assert d["alignments"]["RA-1"]["segments"][0]["radius"] == 20.2


# ------------------------------------------------------------------ skill 10: B2 (TC10, TC11)
def linework_ing(tmp, recs, name="H"):
    lw = {"schema_version": "1.0", "source": {"path": f"submittals/NRJS/T/{name}.dwg", "sha256": "d" * 64},
          "extractor": {"script": "extract_dxf.py", "version": "t", "flatten_m": 0.05, "block_expansion": True},
          "units": "m", "layers": {}, "records": recs, "warnings": []}
    (tmp / f"{name}.linework.json").write_text(json.dumps(lw))
    ing = {"template_version": "1.2", "source": {"path": f"submittals/NRJS/T/{name}.dwg", "sha256": "d" * 64, "bytes": 1,
                                                  "document_no": None, "revision": None, "file_type": "dwg"},
           "content": {"texts": [], "blocks": [], "layers": [], "side_files": [{"path": f"{name}.linework.json", "kind": "linework",
                                                                                  "sha256": "0" * 64, "records": len(recs)}]}}
    p = tmp / f"{name}.dwg.json"
    p.write_text(json.dumps(ing))
    return p


def L(layer, pts, closed=False):
    return {"type": "LWPOLYLINE", "layer": layer, "layer_short": layer.split("$")[-1], "handle": "1", "via_block": [],
            "closed": closed, "length": 1.0, "points": pts}


def b2_world(tmp):
    s = sheet(tmp, "S1", tables_world(tmp))
    run(SK10, "read_setout.py", "--ingestion", s, "--out", tmp / "so.json")
    recs = [L("RD_CL", [[660000, 2750000], [660200, 2750000]]), L("RD_CL", [[660100, 2750000], [660100, 2750100]]),
            L("RD_CL", [[660202, 2750000], [660300, 2750000]]),
            # ROW boundaries 7.5 m either side of A and C (15 m), 9 m either side of B (18 m)
            L("__ROW", [[660000, 2750007.5], [660290, 2750007.5]]), L("__ROW", [[660000, 2749992.5], [660290, 2749992.5]]),
            L("__ROW", [[660091, 2750010], [660091, 2750100]]), L("__ROW", [[660109, 2750010], [660109, 2750100]]),
            # a cul-de-sac bulb at the east end of C
            L("__ROW", [[660290, 2750015], [660305, 2750015], [660305, 2749985], [660290, 2749985]]),
            # sidewalk line work along A on both sides
            L("SIDEWALK", [[660000, 2750006], [660200, 2750006]]), L("SIDEWALK", [[660000, 2749994], [660200, 2749994]])]
    return linework_ing(tmp, recs)


def dd_rules(tmp):
    def PR(rid, carriers):
        return {"rule_id": rid, "title": f"Test rule {rid}", "state": "active", "cap": "Confirmed", "statement": f"Synthetic {rid}.",
                "inputs": carriers, "parameters": {"required_element": {"value": f"element of {rid}", "tag": "AGREED", "source_id": "SRC-T",
                                                                         "clause": "§T", "cap": "Confirmed"}},
                "severity_default": "Minor", "issue_type": "missing_information", "check_type": "presence",
                "default_status": {"script_output": "Missing information", "dwg_vector": "Missing information"},
                "owner_role": "DMR", "applicability": {"street_class": ["all"]}, "required_from_phase": "DD",
                "required_inputs": [{"item": f"element of {rid}", "carrier": carriers, "required_from_phase": "DD"}]}
    r001 = {"rule_id": "ROD-001", "title": "Road class", "state": "active", "cap": "Confirmed", "statement": "Class.",
            "inputs": ["road_hierarchy_plan"], "parameters": {"classes": {"value": ["local_access", "sub_collector"], "tag": "AGREED"}},
            "severity_default": "Minor", "issue_type": "design_error", "check_type": "value",
            "default_status": {"dwg_vector": "Confirmed", "script_output": "Confirmed"}, "owner_role": "DMR",
            "applicability": {"street_class": ["local_access", "sub_collector"]}}
    rules = [PR(r, ["plan_geometry"]) for r in ("ROD-101", "ROD-102", "ROD-103", "ROD-104", "ROD-105", "ROD-109", "ROD-112", "ROD-115")]
    p = tmp / "rules_RDE.json"
    p.write_text(json.dumps({"agent": "RDE", "rulebook_version": "v99", "rules": rules + [r001], "rules_skipped": [],
                             "stage_map": {"60%": {"phase": "DD", "milestone": 60}}}))
    return p


def plan_dwg(tmp, texts):
    ing = {"template_version": "1.2", "source": {"path": "submittals/NRJS/T/P.dwg", "sha256": "e" * 64, "bytes": 1,
                                                  "document_no": None, "revision": None, "file_type": "dwg"},
           "content": {"texts": [{"type": "TEXT", "text": t, "layer": "ANNO", "insert": [x, y, 0], "handle": f"{i + 10:X}",
                                  "space": "model"} for i, (t, x, y) in enumerate(texts)], "blocks": [], "layers": []}}
    p = tmp / "P.dwg.json"
    p.write_text(json.dumps(ing))
    return p


def b2_pipeline(tmp, out_dir):
    out_dir.mkdir(exist_ok=True)
    h = b2_world(tmp)
    assert run(SK10, "build_network.py", "--setout", tmp / "so.json", "--site", "NRJS", "--linework", h,
               "--out", out_dir / "network.json")[0] == 0
    assert run(SK10, "cross_check.py", "--network", out_dir / "network.json", "--site", "NRJS", "--linework", h,
               "--out", out_dir / "xc.json")[0] == 0
    secs = {"sections": [{"title": "15 METER LOCAL ROAD", "title_width_m": 15.0, "class": "local_access",
                          "elements": [{"element": "sidewalk"}]},
                         {"title": "18 METER LOCAL ROAD", "title_width_m": 18.0, "class": "local_access",
                          "elements": [{"element": "sidewalk"}, {"element": "sidewalk"}]}]}
    (tmp / "sc.json").write_text(json.dumps(secs))
    assert run(SK10, "read_class.py", "--network", out_dir / "network.json", "--site", "NRJS", "--linework", h,
               "--sections", tmp / "sc.json", "--out", out_dir / "class.json")[0] == 0
    p = plan_dwg(tmp, [("CURB RAMP", 660101, 2750003), ("SPLITTER ISLAND", 660100, 2750105), ("TACTILE", 660100, 2750001)])
    rc, out = run(SK10, "check_locations.py", "--network", out_dir / "network.json", "--class", out_dir / "class.json",
                  "--rules", dd_rules(tmp), "--stage", "60%", "--file", f"plan_geometry={p}", "--file", f"plan_geometry={h}",
                  "--sections", tmp / "sc.json", "--cross-check", out_dir / "xc.json", "--out", out_dir / "loc.json")
    assert rc == 0, out
    return out_dir


def test_b2_synthetic_dd(tmp_path):
    d = b2_pipeline(tmp_path, tmp_path / "run1")
    net = load(d / "network.json")
    assert net["counts"]["junctions"] == 1 and net["junctions"][0]["roads"] == ["ROAD-A (15m R.O.W)", "ROAD-B (18m R.O.W)"]
    assert net["roundabouts"][0]["centre"] == [660100.0, 2750120.2]
    assert [a["road"] for a in net["roundabouts"][0]["approaches"]] == ["ROAD-B (18m R.O.W)"]
    ends = {e["road"]: e["type"] for e in net["ends"]}
    assert net["counts"]["ends"]["gap"] == 2 and ends["ROAD-C (15m R.O.W)"] in ("gap", "dead_end")
    cls = load(d / "class.json")
    assert cls["roads"]["ROAD-A (15m R.O.W)"]["width"] == 15.0 and cls["roads"]["ROAD-A (15m R.O.W)"]["agreement"] == 2
    assert cls["roads"]["ROAD-B (18m R.O.W)"]["class"] == "local_access"
    loc = load(d / "loc.json")
    fails = {}
    for c in loc["checks"]:
        fails.setdefault(c["rule_id"], []).append(c["subject"].split(" ")[0])
    assert "ROD-102" not in fails and "ROD-103" not in fails and "ROD-112" not in fails      # found within the radius
    assert fails["ROD-105"] == ["J001"] and fails["ROD-109"] == ["J001"] and fails["ROD-104"] == ["J001"]
    assert fails["ROD-101"] == ["ROAD-C"]                    # A has line work both sides; B's section has two
    dead = [e for e in net["ends"] if e["type"] == "dead_end"]
    assert sorted(fails.get("ROD-115", [])) == sorted(e["id"] for e in dead if not e["road"].startswith("ROAD-C"))
    # findings: one per rule, count and locations; schema and caps
    rc, out = run(SK10, "build_candidates.py", "--results", d / "xc.json", d / "class.json", d / "loc.json",
                  "--rules", tmp_path / "rules_RDE.json", "--run", "NRJS-T-20261016T0100", "--site", "NRJS", "--sub", "T",
                  "--evidence-dir", tmp_path / "ev", "--out", tmp_path / "cand.json")
    assert rc == 0, out
    fs = load(tmp_path / "cand.json")
    by = {f["reference"]["rule_id"]: f for f in fs}
    assert by["ROD-105"]["proposed_status"] == "Probable" and by["ROD-105"]["location"]["xy"]["crs"] == "EPSG:32638"
    assert by["ROD-105"]["statement"].startswith("1 occurrence(s)")
    assert run(SK10, "validate_findings.py", "--findings", tmp_path / "cand.json", "--rules", tmp_path / "rules_RDE.json")[0] == 0


def test_b2_class_disagreement_and_unknown(tmp_path):
    d = b2_pipeline(tmp_path, tmp_path / "r")
    net = load(d / "network.json")
    h = tmp_path / "H.dwg.json"
    lw = load(tmp_path / "H.linework.json")
    lw["records"].append(L("XR$0$25m ROW", [[660000, 2750000], [660200, 2750000]]))       # M1 25 m on ROAD-A vs M2 / M5 15 m
    (tmp_path / "H.linework.json").write_text(json.dumps(lw))
    run(SK10, "read_class.py", "--network", d / "network.json", "--site", "NRJS", "--linework", h, "--sections",
        tmp_path / "sc.json", "--out", d / "class2.json")
    c = load(d / "class2.json")
    a = c["roads"]["ROAD-A (15m R.O.W)"]
    assert a["width"] is None and a["disagreement"] == {"M1": 25.0, "M2": 15.0, "M5": 15.0}
    kinds = sorted(x["mode_tag"] for x in c["checks"])
    assert kinds.count("class_inconsistency") == 1
    # a width without a section of its class -> class unknown (K23 route), Missing information
    secs = {"sections": [{"title": "18 METER LOCAL ROAD", "title_width_m": 18.0, "class": "local_access", "elements": []}]}
    (tmp_path / "sc2.json").write_text(json.dumps(secs))
    run(SK10, "read_class.py", "--network", d / "network.json", "--site", "NRJS", "--linework", h, "--sections",
        tmp_path / "sc2.json", "--out", d / "class3.json")
    c3 = load(d / "class3.json")
    unk = [x for x in c3["checks"] if x["mode_tag"] == "class_unknown"]
    assert [x["subject"] for x in unk] == ["ROAD-C (15m R.O.W) (15 m ROW)"]
    assert unk[0]["issue_type"] == "missing_information" and unk[0]["status_override"] == "Missing information"
    assert net["roads"]


def test_b2_deterministic(tmp_path):
    """TC11: the same inputs give byte-identical network, class and location files."""
    d1 = b2_pipeline(tmp_path, tmp_path / "one")
    d2 = b2_pipeline(tmp_path, tmp_path / "two")
    for f in ("network.json", "class.json", "loc.json", "xc.json"):
        a, b = load(d1 / f), load(d2 / f)
        for x in (a, b):
            x.pop("inputs", None)
            x.pop("sources", None)
        assert a == b, f
