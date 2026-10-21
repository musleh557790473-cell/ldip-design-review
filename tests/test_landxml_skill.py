"""Tests for skill 3 parsing-landxml (SPC-0006 Rev C §2.4.2, §4.12) and its links: ingestion schema 1.1,
the shared validator's side-file check, and the data guard for .xml files.
Synthetic LandXML only (made here or by evals/make_synthetic.py). The real RCRC survey surface (T1, T2,
T4) runs only when LDIP_LANDXML_T1 points to a local copy (never in Git, brief §10.2)."""
import copy
import json
import math
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("lxml")
pytest.importorskip("jsonschema")

REPO = Path(__file__).resolve().parents[1]
PLUGIN = REPO / "plugin/ldip-design-review"
SK = PLUGIN / "skills/parsing-landxml/scripts"
EV = PLUGIN / "skills/parsing-landxml/evals"
SK1 = PLUGIN / "skills/extracting-dwg-data/scripts"
PY = sys.executable
NS = "http://www.landxml.org/schema/LandXML-1.2"
T1_SHA = "9b1d8114d96908634ebb211336d53bb822e00687a7b9e0a11c1abf2f0feb8951"


def run(script, *args, base=SK):
    p = subprocess.run([PY, str(base / script), *map(str, args)], capture_output=True, text=True)
    return p.returncode, json.loads(p.stdout.strip().splitlines()[-1])


def parse(tmp, xml_path, prov="parsons", out="out"):
    return run("parse_landxml.py", "--in", xml_path, "--out", tmp / out, "--provenance", prov)


def load(tmp, name, out="out"):
    return json.loads((tmp / out / f"{name}.json").read_text(encoding="utf-8"))


def codes(doc):
    return [w["code"] for w in doc["warnings"]]


# A 100 m x 50 m flat-and-sloped square near the LDIP sites: 4 points, 2 visible faces, 1 invisible.
PTS = {"1": (657000.0, 2747500.0, 680.0), "2": (657100.0, 2747500.0, 680.0),
       "3": (657100.0, 2747550.0, 690.0), "4": (657000.0, 2747550.0, 690.0)}


def surface_xml(pts=PTS, faces=("1 2 3", "1 3 4"), invisible=("2 3 4",), swap=False, crs='epsgCode="32638"',
                unit="meter", ns=NS, extra_top="", doctype="", faces_block=True, definition=None):
    def p(pid, e, n, z):
        a, b = (e, n) if swap else (n, e)          # LandXML text order is N E Z
        return f'<P id="{pid}">{a} {b} {z}</P>'
    pnts = "".join(p(k, *v) for k, v in pts.items())
    fs = "".join(f"<F>{f}</F>" for f in faces) + "".join(f'<F i="1">{f}</F>' for f in invisible)
    d = definition or 'surfType="TIN" area2DSurf="5000" area3DSurf="5099.02" elevMin="680" elevMax="690"'
    crs_el = f"<CoordinateSystem {crs}/>" if crs else ""
    return (f'<?xml version="1.0"?>{doctype}<LandXML xmlns="{ns}" version="1.2" date="2026-10-14" time="12:00:00">'
            f'<Units><Metric linearUnit="{unit}" areaUnit="squareMeter" angularUnit="decimal degrees" '
            f'directionUnit="decimal degrees"/></Units>{crs_el}<Application name="test" version="1"/>{extra_top}'
            f'<Surfaces><Surface name="SQ" desc="test"><SourceData><Breaklines><Breakline brkType="standard" desc="b1">'
            f'<PntList3D>2747500 657000 680 2747550 657000 690</PntList3D></Breakline></Breaklines></SourceData>'
            f'<Definition {d}><Pnts>{pnts}</Pnts>{"<Faces>" + fs + "</Faces>" if faces_block else ""}</Definition>'
            f'</Surface></Surfaces></LandXML>')


def write(tmp, name, text):
    p = tmp / name
    p.write_text(text, encoding="utf-8")
    return p


# ------------------------------------------------------------------ T5 synthetic alignment / profile
def test_t5_synthetic_alignment_profile(tmp_path):
    rc, _ = run("make_synthetic.py", "--out", tmp_path / "synthetic_t5.xml", base=EV)
    assert rc == 0
    assert (tmp_path / "synthetic_t5.xml").read_text(encoding="utf-8") == (EV / "synthetic_t5.xml").read_text(encoding="utf-8")
    rc, out = parse(tmp_path, EV / "synthetic_t5.xml")
    assert rc == 0 and out["status"] == "ok", out
    d = load(tmp_path, "synthetic_t5.xml")
    c = d["content"]
    assert d["template_version"] == "1.1" and d["warnings"] == []
    assert c["carriers"] == ["landxml_alignment", "landxml_profile"]
    a = c["alignments"][0]
    assert a["min_radius"] == 150.0 and a["continuity_gaps"] == []
    assert a["element_counts"] == {"line": 2, "arc": 1, "spiral": 1}
    assert [e["type"] for e in a["elements"]] == ["line", "arc", "spiral", "line"]
    assert a["elements"][1]["length_computed"] == round(150 * math.pi / 6, 3)
    assert a["elements"][2]["radius_end"] == "INF" and a["elements"][2]["end_check_m"] == 0.0
    assert [e["sta_start"] for e in a["elements"]] == [0.0, 100.0, 178.54, 228.54]
    design = [p for p in c["profiles"] if p["kind"] == "design"][0]
    cu = design["curves"][0]
    assert (cu["a_pct"], cu["k"], cu["curve"], cu["g_in_pct"], cu["g_out_pct"]) == (3.5, 17.14, "crest", 2.0, -1.5)
    eg = [p for p in c["profiles"] if p["kind"] == "existing"][0]
    assert eg["points"] == 4 and eg["sta_range"] == [0.0, 328.54]


def test_profile_warnings(tmp_path):
    x = (EV / "synthetic_t5.xml").read_text(encoding="utf-8")
    x = x.replace('<PVI>0.000000 680.000000</PVI>', '<PVI>-50.000000 679.000000</PVI>')
    x = x.replace("</ProfAlign>", '<ParaCurve length="40">400 680</ParaCurve></ProfAlign>')
    rc, _ = parse(tmp_path, write(tmp_path, "p.xml", x))
    d = load(tmp_path, "p.xml")
    assert rc == 0
    assert "PROFILE_STATION_OUTSIDE_ALIGNMENT" in codes(d) and "CURVE_AT_PROFILE_END" in codes(d)


def test_continuity_gap(tmp_path):
    x = (EV / "synthetic_t5.xml").read_text(encoding="utf-8")
    m = re.search(r"<Line length=\"100.000000\"><Start>[^<]+</Start><End>([^<]+)</End>", x)
    n, e = map(float, m.group(1).split())
    x = x.replace(f"<End>{m.group(1)}</End>", f"<End>{n:.6f} {e - 0.5:.6f}</End>", 1)
    rc, _ = parse(tmp_path, write(tmp_path, "g.xml", x))
    d = load(tmp_path, "g.xml")
    assert d["content"]["alignments"][0]["continuity_gaps"] == [{"after_element": 1, "gap_m": 0.5}]
    assert "CONTINUITY_GAP" in codes(d) and "ELEMENT_LENGTH_MISMATCH" in codes(d)


# ------------------------------------------------------------------ surfaces (synthetic)
def test_surface_areas_order_and_side_file(tmp_path):
    rc, out = parse(tmp_path, write(tmp_path, "s.xml", surface_xml()), prov="employer")
    assert rc == 0 and out["side_files"] == ["s.surface-1.json"]
    d = load(tmp_path, "s.xml")
    s = d["content"]["surfaces"][0]
    assert d["source"]["provenance"] == "employer" and d["crs"] == {"declared": "EPSG:32638", "units": "m", "insunits": None}
    assert d["title_block"] is None and d["extractor"]["converter"] == {"name": None, "version": None}
    assert s["computed"]["area_2d"] == 5000.0
    assert s["computed"]["area_3d"] == round(100 * math.hypot(50, 10), 2)
    assert s["declared_vs_computed"]["area_2d"] == 0.0
    assert s["counts"]["faces"] == 3 and s["counts"]["faces_invisible"] == 1 and s["counts"]["faces_visible"] == 2
    assert s["extents"] == {"e_min": 657000.0, "e_max": 657100.0, "n_min": 2747500.0, "n_max": 2747550.0}
    side = json.loads((tmp_path / "out" / "s.surface-1.json").read_text(encoding="utf-8"))
    assert side["points"][1] == ["2", 657100.0, 2747500.0, 680.0]            # E, N, Z (T2 rule)
    assert side["faces"] == [["1", "2", "3"], ["1", "3", "4"]]
    assert side["breaklines"][0]["points"] == [[657000.0, 2747500.0, 680.0], [657000.0, 2747550.0, 690.0]]
    assert "AXIS_ORDER_SUSPECT" not in codes(d)
    rc, v = run("validate_ingestion.py", "--files", tmp_path / "out" / "s.xml.json", base=SK1)
    assert rc == 0, v


def test_surface_edge_cases(tmp_path):
    pts = dict(PTS)
    rc, _ = parse(tmp_path, write(tmp_path, "m.xml", surface_xml(faces=("1 2 3", "1 3 9"))))
    d = load(tmp_path, "m.xml")
    assert codes(d).count("FACE_MISSING_POINT") == 1 and d["content"]["surfaces"][0]["computed"]["area_2d"] == 2500.0
    rc, _ = parse(tmp_path, write(tmp_path, "w.xml", surface_xml(swap=True)))
    assert "AXIS_ORDER_SUSPECT" in codes(load(tmp_path, "w.xml"))
    rc, _ = parse(tmp_path, write(tmp_path, "q.xml", surface_xml(faces=("1 2 3 4",), invisible=())))
    d = load(tmp_path, "q.xml")
    assert "FACE_NOT_TRIANGLE" in codes(d) and d["content"]["surfaces"][0]["computed"]["area_2d"] == 5000.0
    rc, _ = parse(tmp_path, write(tmp_path, "n.xml", surface_xml(faces_block=False)))
    d = load(tmp_path, "n.xml")
    assert d["content"]["surfaces"][0]["tin_built"] is False and d["content"]["surfaces"][0]["computed"]["area_2d"] is None
    assert "TIN_NOT_BUILT" in codes(d)
    x = surface_xml(pts=pts).replace('<P id="2">', '<P id="1">2747000 657000 1</P><P id="2">')
    rc, _ = parse(tmp_path, write(tmp_path, "dup.xml", x))
    assert "DUPLICATE_POINT_ID" in codes(load(tmp_path, "dup.xml"))


def test_crs_units_inventory_provenance(tmp_path):
    rc, _ = parse(tmp_path, write(tmp_path, "c0.xml", surface_xml(crs="")))
    d = load(tmp_path, "c0.xml")
    assert d["crs"]["declared"] is None and "CRS_MISSING" in codes(d)
    rc, _ = parse(tmp_path, write(tmp_path, "c1.xml", surface_xml(crs='epsgCode="4326"')))
    assert "CRS_NOT_32638" in codes(load(tmp_path, "c1.xml"))
    rc, _ = parse(tmp_path, write(tmp_path, "u.xml", surface_xml(unit="USSurveyFoot")))
    d = load(tmp_path, "u.xml")
    assert d["crs"]["units"] == "USSurveyFoot" and "UNITS_NOT_METRES" in codes(d)
    rc, _ = parse(tmp_path, write(tmp_path, "i.xml", surface_xml(extra_top="<CgPoints/><PipeNetworks/>")))
    d = load(tmp_path, "i.xml")
    assert d["content"]["inventory"] == {"CgPoints": 1, "PipeNetworks": 1} and codes(d).count("NOT_EXTRACTED") == 2
    name = "LDIP-RCRC-SAP-01-QRWN-ROD-MOD-90000-001-P01-AICC-EXPORT.xml"
    rc, _ = parse(tmp_path, write(tmp_path, name, surface_xml()), prov="parsons")
    d = load(tmp_path, name)
    assert "PROVENANCE_NAME_MISMATCH" in codes(d)
    assert d["source"]["document_no"] == "LDIP-RCRC-SAP-01-QRWN-ROD-MOD-90000-001-P01" and d["source"]["revision"] == "P01"


# ------------------------------------------------------------------ refused / skipped input
@pytest.mark.parametrize("case", ["external_entity", "internal_entity", "malformed", "unknown_ns"])
def test_refused_inputs(tmp_path, case):
    if case == "external_entity":
        x = surface_xml(doctype='<!DOCTYPE LandXML [<!ENTITY x SYSTEM "file:///etc/passwd">]>')
    elif case == "internal_entity":
        x = surface_xml(doctype='<!DOCTYPE LandXML [<!ENTITY a "aaaa"><!ENTITY b "&a;&a;&a;">]>')
    elif case == "malformed":
        x = surface_xml()[:-30]
    else:
        x = surface_xml(ns="http://www.landxml.org/schema/LandXML-9.9")
    rc, out = parse(tmp_path, write(tmp_path, f"{case}.xml", x))
    assert rc == 2 and out["status"] == "error"
    assert not (tmp_path / "out").exists() or not list((tmp_path / "out").iterdir())


def test_size_cap(tmp_path):
    p = tmp_path / "big.xml"
    with open(p, "wb") as fh:
        fh.truncate(500 * 1024 * 1024 + 1)
    rc, out = parse(tmp_path, p)
    assert rc == 2 and "500 MB" in out["detail"]


def test_skipped_inputs(tmp_path):
    rc, out = parse(tmp_path, write(tmp_path, "o.xml", '<?xml version="1.0"?><Other/>'))
    assert rc == 0 and out["status"] == "skipped"
    rc, out = parse(tmp_path, write(tmp_path, "s.xml.aecc.pnt", "x"))
    assert rc == 0 and out["status"] == "skipped"
    assert not (tmp_path / "out").exists() or not list((tmp_path / "out").iterdir())


# ------------------------------------------------------------------ schema 1.1 and shared validator
def test_validator_side_file_and_schema(tmp_path):
    parse(tmp_path, write(tmp_path, "s.xml", surface_xml()))
    main = tmp_path / "out" / "s.xml.json"
    side = tmp_path / "out" / "s.surface-1.json"
    doc = json.loads(main.read_text(encoding="utf-8"))
    bad = {"no_provenance": lambda d: d["source"].pop("provenance"),
           "title_block_object": lambda d: d.__setitem__("title_block", {"document_no": None, "revision": None, "title": None,
                                                                        "date": None, "status": None, "source_ref": "x"}),
           "template_1_0": lambda d: d.__setitem__("template_version", "1.0"),
           "bad_carrier": lambda d: d["content"].__setitem__("carriers", ["landxml_pipe"]),
           "converter_named": lambda d: d["extractor"]["converter"].__setitem__("name", "oda")}
    for k, f in bad.items():
        d = copy.deepcopy(doc)
        f(d)
        main.write_text(json.dumps(d), encoding="utf-8")
        rc, _ = run("validate_ingestion.py", "--files", main, base=SK1)
        assert rc == 4, k
    main.write_text(json.dumps(doc), encoding="utf-8")
    side.write_text(side.read_text(encoding="utf-8").replace("680.0", "680.001", 1), encoding="utf-8")
    rc, out = run("validate_ingestion.py", "--files", main, base=SK1)
    assert rc == 4 and "tin_sha256" in json.dumps(out)
    side.unlink()
    rc, out = run("validate_ingestion.py", "--files", main, base=SK1)
    assert rc == 4 and "not found" in json.dumps(out)


def test_dwg_branch_unchanged():
    from jsonschema import Draft202012Validator
    s = json.loads((PLUGIN / "schemas/ingestion.schema.json").read_text(encoding="utf-8"))
    v = Draft202012Validator(s)
    tpl = json.loads((PLUGIN / "skills/extracting-dwg-data/templates/ingestion.json").read_text(encoding="utf-8"))
    tpl["template_version"] = "1.1"
    assert any("1.0" in e.message for e in v.iter_errors(tpl))
    tpl["template_version"] = "1.0"
    tpl["crs"]["units"] = "meter"
    assert any("units" in "/".join(map(str, e.absolute_path)) for e in v.iter_errors(tpl))


def test_geom_unit():
    sys.path.insert(0, str(SK))
    import landxml_geom as g
    assert g.ne_text_to_en("2747252.764 657082.514 680.765") == (657082.514, 2747252.764, 680.765)
    assert g.axis_check(657082.5, 2747252.7) == "ok" and g.axis_check(2747252.7, 657082.5) == "swapped"
    assert g.curve_k(60, 2.0, -1.5) == (3.5, 60 / 3.5, "crest")
    assert g.curve_k(40, -1.0, 1.0)[2] == "sag"
    assert abs(g.to_degrees(math.pi, "radians") - 180) < 1e-12 and g.to_degrees(100, "grads") == 90
    assert abs(g.to_degrees("45.3015", "decimal dd.mm.ss") - (45 + 30 / 60 + 15 / 3600)) < 1e-9


def test_data_guard_blocks_xml(tmp_path):
    sys.path.insert(0, str(REPO / "tools"))
    import check_no_data as c
    assert ".xml" in c.BLOCK_EXT
    assert c.SYNTHETIC_OK.match("plugin/ldip-design-review/skills/parsing-landxml/evals/synthetic_t5.xml")
    assert not c.SYNTHETIC_OK.match("plugin/ldip-design-review/skills/parsing-landxml/evals/RCRC-survey.xml")
    # Content fingerprints: deposited ROD-043 numbers/text blocked; synthetic CI fixture allowed
    fake = tmp_path / "rod043_realish.json"
    fake.write_text(json.dumps({
        "rule_id": "ROD-043",
        "parameters": {
            "circulatory_crossfall": {"value": 2.0, "unit": "%"},
            "drainage_g_min": {"value": 0.5, "unit": "%"},
            "apron_width": {"value": [1.5, 2.0], "unit": "m"},
        },
    }))
    assert any("deposited rules_v7" in h or "ROD-043" in h for h in c.content_hits(str(fake), fake))
    synth = Path(__file__).resolve().parents[1] / "tests/fixtures/rod043_excerpt.json"
    assert synth.is_file() and c.content_hits("tests/fixtures/rod043_excerpt.json", synth) == []
    assert not c.SYNTHETIC_OK.match("golden_set/inputs/synthetic_t5.xml")


# ------------------------------------------------------------------ real file (local only)
REAL = os.environ.get("LDIP_LANDXML_T1")


@pytest.mark.skipif(not REAL, reason="real RCRC survey LandXML is local-only (brief §10.2)")
def test_t1_t2_t4_real_rcrc_surface(tmp_path):
    import hashlib
    src = Path(REAL)
    assert hashlib.sha256(src.read_bytes()).hexdigest() == T1_SHA
    rc, _ = parse(tmp_path, src, prov="employer")
    assert rc == 0
    d = load(tmp_path, src.name)
    c, s = d["content"], d["content"]["surfaces"][0]
    assert (c["header"]["version"], c["header"]["application"]["version"], d["crs"]["declared"], d["crs"]["units"]) == ("1.2", "2026", "EPSG:32638", "m")
    assert c["carriers"] == ["landxml_surface"] and c["alignments"] == [] and c["profiles"] == [] and d["warnings"] == []
    k = s["counts"]
    assert (s["name"], k["points"], k["faces"], k["faces_invisible"], k["breaklines"], k["breakline_points"]) == ("EGL", 57283, 114430, 797, 1044, 20498)
    assert s["extents"] == {"e_min": 656967.841, "e_max": 660416.117, "n_min": 2747067.941, "n_max": 2749178.134}
    assert (s["computed"]["elev_min"], s["computed"]["elev_max"]) == (674.101, 701.508)
    assert (s["computed"]["area_2d"], s["computed"]["area_3d"]) == (4167656.17, 4198207.89)
    assert (s["declared_vs_computed"]["area_2d"], s["declared_vs_computed"]["area_3d"]) == (0.0, 0.0)
    side = json.loads((tmp_path / "out" / s["tin_file"]).read_text(encoding="utf-8"))
    assert [p for p in side["points"] if p[0] == "2"][0] == ["2", 657082.514, 2747252.764, 680.765]     # T2
    raw = src.read_text(encoding="utf-8")
    a = raw.replace('<?xml version="1.0"?>', '<?xml version="1.0"?><!DOCTYPE LandXML [<!ENTITY x SYSTEM "file:///etc/passwd">]>', 1)
    rc, _ = parse(tmp_path, write(tmp_path, "a.xml", a), out="oa")                                           # T4 a
    assert rc == 2 and not (tmp_path / "oa").exists()
    m = re.search(r'<F n="[^"]*">(\d+) \d+ \d+</F>', raw)
    rc, _ = parse(tmp_path, write(tmp_path, "b.xml", raw[:m.start(1)] + "999999" + raw[m.end(1):]), out="ob")  # T4 b
    db = json.loads((tmp_path / "ob" / "b.xml.json").read_text(encoding="utf-8"))
    assert codes(db).count("FACE_MISSING_POINT") == 1 and db["content"]["surfaces"][0]["computed"]["area_2d"] == 4167625.75
    sw = lambda t: " ".join(" ".join([v[i + 1], v[i], v[i + 2]]) for v in [t.split()] for i in range(0, len(v), 3))
    cx = re.sub(r'(<P id="[^"]+">)([^<]+)(</P>)', lambda q: q.group(1) + sw(q.group(2)) + q.group(3), raw)
    rc, _ = parse(tmp_path, write(tmp_path, "c.xml", cx), out="oc")                                          # T4 c
    assert "AXIS_ORDER_SUSPECT" in codes(json.loads((tmp_path / "oc" / "c.xml.json").read_text(encoding="utf-8")))
