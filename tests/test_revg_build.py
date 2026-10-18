"""Tests for the SPC-0006 Rev G build (phase B2, part 2; DG-1 ... DG-8): LandXML position source (TD1), low
points (TD2), parking areas (TD3), calming devices (TD4), school frontages (TD5) and determinism (TD6).
Synthetic DD data only: the 30 % submissions hold none of these elements (Rev G §4.13D.2); the real test waits
for the first 60 % submission (R37)."""
import hashlib
import json
import math
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("shapely")
pytest.importorskip("lxml")
pytest.importorskip("jsonschema")

REPO = Path(__file__).resolve().parents[1]
PLUGIN = REPO / "plugin/ldip-design-review"
SK3 = PLUGIN / "skills/parsing-landxml/scripts"
EV3 = PLUGIN / "skills/parsing-landxml/evals"
SK10 = PLUGIN / "skills/reviewing-road-design/scripts"
PY = sys.executable
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_revf_build import sheet, tables_world  # noqa: E402

NS = "http://www.landxml.org/schema/LandXML-1.2"


def run(sk, script, *args):
    p = subprocess.run([PY, str(sk / script), *map(str, args)], capture_output=True, text=True)
    out = p.stdout.strip().splitlines()
    return p.returncode, (json.loads(out[-1]) if out else {"stderr": p.stderr[-2000:]})


def load(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


# ------------------------------------------------------------------ synthetic DD package
def ne(e, n):
    return f"{n} {e}"                                   # LandXML text order is N E


def line(a, b):
    return f"<Line><Start>{ne(*a)}</Start><End>{ne(*b)}</End></Line>"


def landxml(c_shift=2.0):
    """The tables world of test_revf_build (ROAD-A, B, C, RA-1) as LandXML alignments with design profiles;
    ROAD-C is moved `c_shift` m north (the cross-check must report it)."""
    yc = 2750000 + c_shift
    ra = (f'<Curve rot="ccw" radius="20.2"><Start>{ne(660100, 2750100)}</Start><Center>{ne(660100, 2750120.2)}</Center>'
          f'<End>{ne(660100, 2750140.4)}</End></Curve>'
          f'<Curve rot="ccw" radius="20.2"><Start>{ne(660100, 2750140.4)}</Start><Center>{ne(660100, 2750120.2)}</Center>'
          f'<End>{ne(660100, 2750100)}</End></Curve>')
    al = [("ROAD-A (15m R.O.W)", 200, line((660000, 2750000), (660200, 2750000)),
           '<PVI>0 10</PVI><ParaCurve length="40">100 9</ParaCurve><PVI>200 10</PVI>'),
          ("ROAD-B (18m R.O.W)", 100, line((660100, 2750000), (660100, 2750100)),
           '<PVI>0 10</PVI><PVI>60 9.4</PVI><PVI>90 10</PVI><PVI>100 9.9</PVI>'),
          ("ROAD-C (15m R.O.W)", 98, line((660202, yc), (660300, yc)), '<PVI>0 10</PVI><PVI>98 9</PVI>'),
          ("RA-1", 126.92, ra, None)]
    body = ""
    for name, L, geom, prof in al:
        p = f'<Profile><ProfAlign name="FG">{prof}</ProfAlign></Profile>' if prof else ""
        body += f'<Alignment name="{name}" length="{L}" staStart="0"><CoordGeom>{geom}</CoordGeom>{p}</Alignment>'
    return (f'<?xml version="1.0"?><LandXML xmlns="{NS}" version="1.2" date="2026-10-16" time="21:30:00">'
            '<Units><Metric linearUnit="meter" areaUnit="squareMeter" angularUnit="decimal degrees" '
            'directionUnit="decimal degrees"/></Units><CoordinateSystem epsgCode="32638"/>'
            f'<Application name="test" version="1"/><Alignments name="roads">{body}</Alignments></LandXML>')


def parse_xml(tmp, text, name="NRJS-DD-ROADS.xml"):
    x = tmp / name
    x.write_text(text, encoding="utf-8")
    rc, out = run(SK3, "parse_landxml.py", "--in", x, "--out", tmp / "lx", "--provenance", "parsons")
    assert rc == 0, out
    return tmp / "lx" / f"{name}.json"


def dwg(tmp, name, texts=(), polys=()):
    ing = {"template_version": "1.2", "source": {"path": f"submittals/NRJS/T/{name}.dwg",
                                                  "sha256": hashlib.sha256(name.encode()).hexdigest(),
                                                  "bytes": 1, "document_no": None, "revision": None, "file_type": "dwg"},
           "content": {"texts": [{"type": "TEXT", "text": t, "layer": layer, "insert": [x, y, 0], "handle": f"{name}{i:X}",
                                  "space": "model"} for i, (t, x, y, layer) in enumerate(texts)],
                       "polylines": [{"type": "LWPOLYLINE", "layer": layer, "handle": f"{name}P{i:X}", "closed": True,
                                      "n_vertices": len(v), "vertices": v,
                                      "area": abs(sum(v[k][0] * v[k - 1][1] - v[k - 1][0] * v[k][1] for k in range(len(v)))) / 2}
                                     for i, (layer, v) in enumerate(polys)],
                       "blocks": [], "layers": []}}
    p = tmp / f"{name}.dwg.json"
    p.write_text(json.dumps(ing))
    return p


def box(x0, y0, x1, y1):
    return [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]


def rules(tmp):
    def PR(rid, carriers):
        return {"rule_id": rid, "title": f"Test rule {rid}", "state": "active", "cap": "Confirmed", "statement": f"Synthetic {rid}.",
                "inputs": carriers, "parameters": {"required_element": {"value": f"element of {rid}", "tag": "AGREED",
                                                                         "source_id": "SRC-T", "clause": "§T", "cap": "Confirmed"}},
                "severity_default": "Minor", "issue_type": "missing_information", "check_type": "presence",
                "default_status": {"script_output": "Missing information", "dwg_vector": "Missing information"},
                "owner_role": "DMR", "applicability": {"street_class": ["all"]}, "required_from_phase": "DD",
                "required_inputs": [{"item": f"element of {rid}", "carrier": carriers, "required_from_phase": "DD"}]}
    rs = [PR("ROD-107", ["drainage_plan"]), PR("ROD-108", ["plan_geometry"]), PR("ROD-111", ["signing_marking_plan"]),
          PR("ROD-113", ["plan_geometry"])]
    p = tmp / "rules_RDE.json"
    p.write_text(json.dumps({"agent": "RDE", "rulebook_version": "v99", "rules": rs, "rules_skipped": [],
                             "stage_map": {"60%": {"phase": "DD", "milestone": 60}}}))
    return p


ROADS = ("ROAD-A (15m R.O.W)", "ROAD-B (18m R.O.W)", "ROAD-C (15m R.O.W)")


def package(tmp, hump_at_school=False, label_source=True):
    """Network from LandXML, low points, and the DD drawings of TD2 ... TD5."""
    x = parse_xml(tmp, landxml())
    assert run(SK10, "read_landxml_network.py", "--landxml", x, "--out", tmp / "lxnet.json")[0] == 0
    assert run(SK10, "build_network.py", "--setout", tmp / "lxnet.json", "--site", "NRJS", "--out", tmp / "network.json")[0] == 0
    assert run(SK10, "find_low_points.py", "--landxml", x, "--network", tmp / "network.json", "--out", tmp / "low.json")[0] == 0
    (tmp / "class.json").write_text(json.dumps({"roads": {ROADS[0]: {"width": 15.0, "class": "local_access"},
                                                          ROADS[1]: {"width": 18.0, "class": "collector"},
                                                          ROADS[2]: {"width": None, "class": None}}}))
    drain = dwg(tmp, "DR", texts=[("CATCH BASIN CB-1", 660105, 2750005, "DR_TEXT")])
    plan = dwg(tmp, "PL", texts=[("ACCESSIBLE PARKING", 660060, 2750025, "PK_TEXT"),
                                 ("SPEED HUMP", 660050, 2749999, "TC"), ("SPEED HUMP", 660054, 2749999, "TC")]
               + ([("SPEED HUMP", 660150, 2750001, "TC")] if hump_at_school else []),
               polys=[("PARKING", box(660050, 2750020, 660070, 2750030)),        # 200 m², bay inside -> pass
                      ("PARKING", box(660052, 2750021, 660060, 2750029)),        # inside the first -> ignored
                      ("PARKING", box(660150, 2750020, 660170, 2750030)),        # 200 m², no bay -> listed
                      ("PARKING", box(660250, 2750020, 660254, 2750024))])       # 16 m² -> ignored
    sign = dwg(tmp, "SG", texts=[("W2-1", 660077, 2750000, "SIGNS")])
    cad = dwg(tmp, "CAD", texts=[("P-001", 660150, 2750030, "PLOT_ID"), ("P-002", 660120, 2750075, "PLOT_ID"),
                                 ("P-003", 660250, 2750020, "PLOT_ID"), ("P-004", 660030, 2750030, "PLOT_ID")],
              polys=[("PLOTS", box(660120, 2750010, 660180, 2750050)),          # school, fronts ROAD-A (local)
                     ("PLOTS", box(660108, 2750060, 660140, 2750090)),          # playground, fronts ROAD-B (collector)
                     ("PLOTS", box(660230, 2750010, 660270, 2750040)),          # kindergarten, fronts ROAD-C (no class)
                     ("PLOTS", box(660010, 2750010, 660045, 2750050))])         # residential
    (tmp / "dlus.json").write_text(json.dumps({"rows": [
        {"plot_id": "P-001", "street": False, "level1": "Education", "level2": "Primary School"},
        {"plot_id": "P-002", "street": False, "level1": "Open Space", "level2": "Playground"},
        {"plot_id": "P-003", "street": False, "level1": "Education", "level2": "Kindergarten"},
        {"plot_id": "P-004", "street": False, "level1": "Residential", "level2": "Villa"}]}))
    (tmp / "pls.json").write_text(json.dumps({"NRJS": {"polygon_layers": ["PLOTS"], "text_layers": ["PLOT_ID"]}}))
    args = ["--network", tmp / "network.json", "--class", tmp / "class.json", "--rules", rules(tmp), "--stage", "60%",
            "--file", f"drainage_plan={drain}", "--file", f"plan_geometry={plan}", "--file", f"signing_marking_plan={sign}",
            "--low-points", tmp / "low.json", "--plots", cad, "--dlus", tmp / "dlus.json", "--out", tmp / "loc.json"]
    if label_source:
        args += ["--plot-label-source", tmp / "pls.json"]
    rc, out = run(SK10, "check_locations.py", *args)
    assert rc == 0, out
    return load(tmp / "loc.json")


def fails(loc, rid):
    return [c["subject"] for c in loc["checks"] if c["rule_id"] == rid]


# ------------------------------------------------------------------ TD1 LandXML position source (DG-1)
def test_td1_landxml_network_and_cross_check(tmp_path):
    s = sheet(tmp_path, "S1", tables_world(tmp_path))
    assert run(SK10, "read_setout.py", "--ingestion", s, "--out", tmp_path / "so.json")[0] == 0
    assert run(SK10, "build_network.py", "--setout", tmp_path / "so.json", "--site", "NRJS", "--out", tmp_path / "net_t.json")[0] == 0
    x = parse_xml(tmp_path, landxml())
    rc, out = run(SK10, "read_landxml_network.py", "--landxml", x, "--out", tmp_path / "lxnet.json")
    assert rc == 0 and out["alignments"] == 4 and out["curves"] == 4 and out["warnings"] == 0, out
    assert run(SK10, "build_network.py", "--setout", tmp_path / "lxnet.json", "--site", "NRJS", "--out", tmp_path / "net_x.json")[0] == 0
    t, xn = load(tmp_path / "net_t.json"), load(tmp_path / "net_x.json")
    assert t["position_source"] == "tables" and xn["position_source"] == "landxml"
    assert sorted(t["roads"]) == sorted(xn["roads"])
    assert [j["roads"] for j in t["junctions"]] == [j["roads"] for j in xn["junctions"]] == [list(ROADS[:2])]
    assert math.dist(t["junctions"][0]["xy"], xn["junctions"][0]["xy"]) < 0.5
    assert xn["roundabouts"][0]["centre"] == [660100.0, 2750120.2]
    assert [a_["road"] for a_ in xn["roundabouts"][0]["approaches"]] == [ROADS[1]]
    # tables are the cross-check source at DD: the moved ROAD-C is reported, nothing else
    rc, out = run(SK10, "cross_check.py", "--network", tmp_path / "net_x.json", "--site", "NRJS", "--setout",
                  tmp_path / "so.json", "--out", tmp_path / "xc.json")
    assert rc == 0, out
    xc = load(tmp_path / "xc.json")
    assert [c["subject"] for c in xc["checks"]] == [f"{ROADS[2]}: position on the LandXML alignment vs setting-out tables"]
    assert xc["checks"][0]["evidence"]["source_type"] == "landxml" and xc["sources"]["position_source"] == "landxml"
    # exactly one reference
    assert run(SK10, "cross_check.py", "--network", tmp_path / "net_x.json", "--site", "NRJS", "--out", tmp_path / "e.json")[0] == 2


def test_td1_spiral_and_arc_rebuilt(tmp_path):
    """Skill 3 synthetic T5 alignment (line, 30° arc R 150, clothoid, line): pieces join and the spiral closes
    on the recorded End without a correction warning."""
    rc, _ = run(SK3, "parse_landxml.py", "--in", EV3 / "synthetic_t5.xml", "--out", tmp_path / "lx", "--provenance", "parsons")
    assert rc == 0
    rc, out = run(SK10, "read_landxml_network.py", "--landxml", tmp_path / "lx" / "synthetic_t5.xml.json", "--out", tmp_path / "n.json")
    assert rc == 0 and out["spirals"] == 1 and out["warnings"] == 0, out
    al = next(iter(load(tmp_path / "n.json")["alignments"].values()))
    segs = al["segments"]
    assert [s_["type"] for s_ in segs] == ["tangent", "curve", "spiral", "tangent"]
    for a_, b_ in zip(segs, segs[1:]):
        assert math.dist(a_["points"][-1], b_["points"][0]) < 0.01
    sp = segs[2]["points"]
    assert all(0.9 <= math.dist(p, q) <= 1.1 for p, q in zip(sp, sp[1:-1]))
    on = segs[1]["points"][1]                                 # the mid point lies on the arc
    ing = load(tmp_path / "lx" / "synthetic_t5.xml.json")["content"]["alignments"][0]["elements"][1]
    assert abs(math.dist(on, ing["center"]) - 150.0) < 0.01


def test_td1_station_equations():
    sys.path.insert(0, str(SK10))
    from find_low_points import to_internal
    al = {"sta_start": 0.0, "station_equations": [{"sta_internal": 50.0, "sta_back": 50.0, "sta_ahead": 1050.0}]}
    assert to_internal(40.0, al) == 40.0 and to_internal(1060.0, al) == 60.0 and to_internal(500.0, al) is None


# ------------------------------------------------------------------ TD2 ... TD5
def test_td2_low_points(tmp_path):
    loc = package(tmp_path)
    low = load(tmp_path / "low.json")
    got = {(r["alignment"], r["kind"]): (r["station"], r["xy"]) for r in low["low_points"]}
    assert got[(ROADS[0], "sag_curve")] == (100.0, [660100.0, 2750000.0])         # BVC 80 + (1 x 40) / 2
    assert got[(ROADS[1], "pvi")] == (60.0, [660100.0, 2750060.0])
    assert got[(ROADS[2], "dead_end")][1] == [660300.0, 2750002.0]
    assert len(low["low_points"]) == 3 and low["counts"]["ends_not_used"] == 1           # ROAD-B end falls to the roundabout: not a dead end
    f = fails(loc, "ROD-107")
    assert len(f) == 2 and f[0].startswith("LP002 sta 60.000 (ROAD-B") and f[1].startswith("LP003 sta 98.000 (ROAD-C")   # LP001 has CB-1 at 7 m
    c = next(c for c in loc["checks"] if c["rule_id"] == "ROD-107")
    assert c["link_cap"] is None and "sidewalk low points not checked" in c["evidence"]["extract"]


def test_td3_parking_areas(tmp_path):
    loc = package(tmp_path)
    assert fails(loc, "ROD-108") == ["PA002 at E 660160.0 N 2750025.0"]
    p = next(x for x in loc["passed"] if x["rule_id"] == "ROD-108")
    assert (p["parking_areas"], p["parking_areas_ok"], p["small_ignored"], p["inside_larger"]) == (2, 1, 1, 1)


def test_td4_calming_devices(tmp_path):
    loc = package(tmp_path)
    p = next(x for x in loc["passed"] if x["rule_id"] == "ROD-111")
    assert (p["devices"], p["device_hits"], p["devices_ok"]) == (1, 2, 1)             # two labels 4 m apart, sign at 25 m
    (tmp_path / "h").mkdir()
    loc2 = package(tmp_path / "h", hump_at_school=True)
    assert fails(loc2, "ROD-111") == ["CD002 at E 660150.0 N 2750001.0"]


def test_td5_school_frontages(tmp_path):
    loc = package(tmp_path)
    f = fails(loc, "ROD-113")
    assert len(f) == 1 and f[0].startswith("P-001 frontage (ROAD-A")                          # local road, no hump
    p = next(x for x in loc["passed"] if x["rule_id"] == "ROD-113")
    assert (p["school_plots"], p["plots"], p["frontages_other_class"]) == (3, 4, 1)     # P-002 on a collector
    nr = [x for x in loc["not_run"] if x["rule_id"] == "ROD-113"]
    assert len(nr) == 1 and "P-003 / ROAD-C (15m R.O.W) (width unknown" in nr[0]["reason"]                         # road without a class
    (tmp_path / "h").mkdir()
    loc2 = package(tmp_path / "h", hump_at_school=True)
    assert fails(loc2, "ROD-113") == []
    (tmp_path / "n").mkdir()
    loc3 = package(tmp_path / "n", label_source=False)
    nr3 = [x for x in loc3["not_run"] if x["rule_id"] == "ROD-113"]
    assert len(nr3) == 1 and "plot_label_source not set" in nr3[0]["reason"]


def test_td5_findings_and_caps(tmp_path):
    package(tmp_path)
    rc, out = run(SK10, "build_candidates.py", "--results", tmp_path / "loc.json", "--rules", tmp_path / "rules_RDE.json",
                  "--run", "NRJS-T-20261016T2130", "--site", "NRJS", "--sub", "T", "--evidence-dir", tmp_path / "ev",
                  "--out", tmp_path / "cand.json")
    assert rc == 0, out
    by = {f["reference"]["rule_id"]: f for f in load(tmp_path / "cand.json")}
    assert sorted(by) == ["ROD-107", "ROD-108", "ROD-113"]
    assert by["ROD-107"]["proposed_status"] == "Missing information"                    # LandXML low points keep the rule cap
    assert by["ROD-108"]["proposed_status"] == "Probable" and by["ROD-113"]["proposed_status"] == "Probable"
    assert by["ROD-107"]["statement"].startswith("2 occurrence(s)") and by["ROD-107"]["location"]["xy"]["crs"] == "EPSG:32638"
    assert by["ROD-107"]["agent_versions"] == {"reviewing-road-design": "0.6.0"}
    assert run(SK10, "validate_findings.py", "--findings", tmp_path / "cand.json", "--rules", tmp_path / "rules_RDE.json")[0] == 0


# ------------------------------------------------------------------ TD6 determinism
def test_td6_deterministic(tmp_path):
    (tmp_path / "one").mkdir()
    (tmp_path / "two").mkdir()
    package(tmp_path / "one", hump_at_school=True)
    package(tmp_path / "two", hump_at_school=True)
    for f in ("lxnet.json", "network.json", "low.json", "loc.json"):
        a_, b_ = load(tmp_path / "one" / f), load(tmp_path / "two" / f)
        for x in (a_, b_):
            x.pop("inputs", None)
            x.pop("sources", None)
        assert json.dumps(a_, sort_keys=True).replace("/one/", "/X/") == json.dumps(b_, sort_keys=True).replace("/two/", "/X/"), f
