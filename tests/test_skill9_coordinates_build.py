"""Skill 9 0.3.0 coordinates — synthetic T9-C1 / T9-C2 and unit gates (SPC-0006 Rev P §4.18.5).

No rulebook values in Git: envelopes / displacements / TBM limits used here are synthetic test
values only (brief §10.2). Real gates are in test_skill9_coordinates_real.py.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("shapely")

REPO = Path(__file__).resolve().parents[1]
PLUGIN = REPO / "plugin/ldip-design-review"
SC = PLUGIN / "skills/checking-cadastral-geometry/scripts"
SKILL = PLUGIN / "skills/checking-cadastral-geometry"
PY = sys.executable
SHA = "b" * 64
E0, N0 = 670000.0, 2760000.0


def run(script, *args):
    p = subprocess.run([PY, str(SC / script), *map(str, args)], capture_output=True, text=True)
    line = p.stdout.strip().splitlines()[-1] if p.stdout.strip() else "{}"
    return p.returncode, json.loads(line)


def dump(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj), encoding="utf-8")
    return path


def sq(x, y, s=20.0):
    return [[x, y], [x + s, y], [x + s, y + s], [x, y + s]]


def area(v):
    a = 0.0
    for (x1, y1), (x2, y2) in zip(v, v[1:] + v[:1]):
        a += x1 * y2 - x2 * y1
    return round(abs(a) / 2, 2)


def poly(h, layer, verts, via=None):
    return {"type": "LWPOLYLINE", "layer": layer, "handle": h, "vertices": verts, "n_vertices": len(verts),
            "has_bulge": False, "elevation": 0.0, "area": area(verts),
            **({"via_block": via} if via else {})}


def text(h, layer, s, x, y, space="model", via=None):
    return {"type": "TEXT", "layer": layer, "handle": h, "text": s, "insert": [x, y, 0.0], "height": 1.0,
            "rotation": 0.0, "space": space, **({"via_block": via} if via else {})}


def dwg_ing(polys, texts=(), blocks=(), insunits=6, declared=None, path="submittals/SYN/T9/plan.dwg",
            doc="LDIP-RCRC-SAP-01-SYN-MPL-DWG-00-90001"):
    return {"template_version": "1.2",
            "source": {"path": path, "sha256": SHA, "bytes": 1, "document_no": doc, "revision": None, "file_type": "dwg"},
            "extractor": {"skill": "extracting-dwg-data", "script": "extract_dxf.py", "version": "0.2.2",
                          "converter": {"name": "oda", "version": "27.1.0"}},
            "crs": {"declared": declared, "units": "m", "insunits": insunits},
            "title_block": None,
            "content": {"header": {"insunits": insunits, "extmin": [1e20] * 3, "extmax": [-1e20] * 3},
                        "polylines": list(polys), "texts": list(texts), "blocks": list(blocks),
                        "open_polylines_by_layer": {}},
            "warnings": [], "meta": {"extracted_at": "2026-11-01T12:00:00+03:00"}}


RULE_BASE = {"severity_default": "Critical", "issue_type": "design_error", "owner_role": "Lead GIS",
             "exclusions": [], "code_lists": {}, "inputs": [], "method": "", "skip_subchecks_for": [],
             "skill": "checking-cadastral-geometry",
             "default_status": {"dwg_vector": "Confirmed", "csv_cell": "Confirmed", "xlsx_cell": "Confirmed",
                                "script_output": "Confirmed", "landxml": "Confirmed"}}


def rule(rid, state, params, title="t", statement="s", severity=None, issue=None, default=None):
    return dict(RULE_BASE, rule_id=rid, state=state, cap="Confirmed" if state == "active" else "Probable",
                title=title, statement=statement,
                severity_default=severity or RULE_BASE["severity_default"],
                issue_type=issue or RULE_BASE["issue_type"],
                parameters={k: (v if isinstance(v, dict) and "tag" in v else {"value": v, "tag": "PROPOSED"})
                            for k, v in params.items()},
                default_status=default or RULE_BASE["default_status"])


def rules_coords(**extra008):
    """Synthetic GIS-008 / 011 / 017 / 007 for coordinate tests."""
    p008 = {
        "crs": "EPSG:32638", "insunits": 6, "envelope_buffer": 25.0,
        "site_envelope": {"SYN": {"bbox": [E0 - 10, N0 - 10, E0 + 200, N0 + 200]}},
        "context_layers": {"SYN": ["CTX_PARCEL", "G-KEYP", "sheet"]},
        "known_displacements": {"SYN": [-100.0, 50.0]},
        "displacement_min_entities": 3,
        "datum_reference": {"SYN": {
            "benchmark": {"id": "BM-X", "E": E0 - 5000, "N": N0, "Z": 700.0},
            "site_tbm": {"E": E0 + 50, "N": N0 + 50, "Z": 701.0},
            "class": "Synthetic",
        }},
    }
    p008.update(extra008)
    return {"rulebook_version": "v_test", "agent": "GIS", "rules_skipped": [], "rules": [
        rule("GIS-008", "advisory", p008, statement="CRS metres envelope displacement control."),
        rule("GIS-011", "active", {
            "tolerance_abs": 1.0,
            "site_boundary": {"SYN": sq(E0, N0, 100.0)},
            "official_site_area": {"SYN": {"nature": 10000.0, "deed": 10000.0}},
        }, severity="Minor", issue="inconsistency"),
        rule("GIS-017", "active", {"max_distance_tbm": 500.0},
             severity="Major", issue="missing_information",
             default={"script_output": "Missing information", "dwg_vector": "Confirmed"}),
        rule("GIS-007", "active", {"tolerance": 0.02, "reference_dataset": {"SYN": None}},
             # GAP-style null overridden per test when needed
             ),
    ]}


# ------------------------------------------------------------------ docs / layout
def test_skill_md_0_3_0_under_500_lines():
    text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    assert 'version: "0.3.0"' in text
    assert len(text.splitlines()) < 500
    assert "check_control.py" in text and "Rev P" in text
    assert (SKILL / "references/dwg_rules.md").is_file()
    dwg = (SKILL / "references/dwg_rules.md").read_text(encoding="utf-8")
    assert "displaced_layer_group" in dwg and "GIS-017" in dwg and "boundary_off_reference" in dwg
    assert (SC / "check_control.py").is_file()
    assert '__version__ = "0.2.0"' in (SC / "check_crs.py").read_text(encoding="utf-8")
    assert '__version__ = "0.1.0"' in (SC / "check_control.py").read_text(encoding="utf-8")


# ------------------------------------------------------------------ D1 — NMAR envelope: key-plan vs UTM
def test_nmar_envelope_skips_keyplan_prefers_utm(tmp_path):
    """Two equal-area closed polys: G-KEYP sheet coords skipped; UTM matching S7 chosen (D1)."""
    from test_skill9_coordinates_real import _nmar_envelope_from_ing_or_sites  # noqa: E402

    s7 = sq(E0, N0, 100.0)  # 10_000 m²
    # Key-plan first (old helper took this) — sheet coords, context layer
    key = poly("10A0", "Title Block_A1$0$G-KEYP-BNDY", sq(115.0, 2.0, 100.0))
    utm = poly("10B6", "ROAD-LAYOUT-BNDY", sq(E0, N0, 100.0))
    assert abs(key["area"] - 10000.0) <= 1.0 and abs(utm["area"] - 10000.0) <= 1.0
    ing = dump(tmp_path / "nmar.dwg.json", dwg_ing([key, utm]))
    rb = dump(tmp_path / "rules_v8.json", {
        "rules": [{
            "rule_id": "GIS-011",
            "parameters": {
                "official_site_area": {
                    "value": {"NMAR": {"nature": 10000.0, "deed": 10000.0}},
                    "tag": "PROPOSED",
                },
            },
        }],
    })
    sites = {"S7": {"polygon": [list(v) for v in s7]}}
    env = _nmar_envelope_from_ing_or_sites(ing, rb, sites)
    assert env is not None
    assert env["polygon"] == [list(map(float, v[:2])) for v in utm["vertices"]]
    # sheet key-plan must not win
    assert env["polygon"][0][0] == pytest.approx(E0)


# ------------------------------------------------------------------ T9-C1
def test_t9_c1_envelope_context_and_displacement(tmp_path):
    """T9-C1: envelope + context layer (profile only) + displaced copy + legend at origin."""
    rb = rules_coords()
    # drop GAP reference
    rb["rules"][3]["parameters"]["reference_dataset"] = {"value": None, "tag": "GAP"}
    rules = dump(tmp_path / "rules.json", rb)
    # inside plots
    inside = [poly("P1", "PLOT", sq(E0 + 10, N0 + 10))]
    # context parcel outside → profile only
    ctx = [poly("C1", "CTX_PARCEL", sq(E0 + 500, N0 + 500, 30.0))]
    # displaced group (≥3): drawn = site − vector so site = drawn + vector (−100, +50)
    dE, dN = -100.0, 50.0
    disp = [poly(f"D{i}", "2D-PR-Plot", sq(E0 + 10 - dE + i * 25, N0 + 10 - dN)) for i in range(3)]
    # legend at origin (not known displacement, small group → outside finding)
    legend_p = [poly("L1", "G-ANNO", sq(0.0, 0.0, 5.0))]
    legend_t = [text("T1", "G-ANNO", "LEGEND", 1.0, 1.0)]
    # title-block via_block chain matching context prefix "sheet"
    tb = [poly("TB1", "A-TITLE", sq(E0 + 800, N0 + 800, 2.0), via=["sheet_frame", "TB"])]
    ing = dump(tmp_path / "a.dwg.json", dwg_ing(inside + ctx + disp + legend_p + tb, legend_t))
    rc, msg = run("check_crs.py", "--ingestion", ing, "--rules", rules, "--site", "SYN",
                  "--out", tmp_path / "gis008.json")
    assert rc == 0, msg
    out = json.loads((tmp_path / "gis008.json").read_text())
    assert msg["result"] == "fail"
    pats = {p["pattern"]: p for p in out["patterns"]}
    assert "displaced_layer_group/2D-PR-Plot" in pats
    assert pats["displaced_layer_group/2D-PR-Plot"]["count"] == 3
    assert "outside_site_envelope/G-ANNO" in pats
    assert "outside_site_envelope/CTX_PARCEL" not in pats
    assert "outside_site_envelope/A-TITLE" not in pats  # via_block sheet* → context
    assert out["files"][0]["profile_context_outside"].get("CTX_PARCEL", 0) >= 1
    assert out["files"][0]["units"] == "pass" and out["files"][0]["crs"] == "not_declared"
    # C6 control info
    assert any(c["role"] == "site_tbm" and c["inside"] for c in out["control"])


# ------------------------------------------------------------------ C4 sign (asymmetric vector)
def test_c4_asymmetric_vector_wrong_sign_fails(tmp_path):
    """Asymmetric known vector: adding it matches; subtracting it (wrong sign) must not."""
    dE, dN = -100.0, 50.0  # asymmetric — −vector ≠ +vector effect
    rb = rules_coords(known_displacements={"SYN": [dE, dN]})
    rb["rules"][3]["parameters"]["reference_dataset"] = {"value": None, "tag": "GAP"}
    rules = dump(tmp_path / "rules.json", rb)
    # drawn = site − vector → drawn + vector lands inside
    disp = [poly(f"D{i}", "2D-PR-Plot", sq(E0 + 10 - dE + i * 25, N0 + 10 - dN)) for i in range(3)]
    ing = dump(tmp_path / "a.dwg.json", dwg_ing(disp))
    rc, msg = run("check_crs.py", "--ingestion", ing, "--rules", rules, "--site", "SYN",
                  "--out", tmp_path / "ok.json")
    out = json.loads((tmp_path / "ok.json").read_text())
    assert rc == 0 and any(p["pattern"] == "displaced_layer_group/2D-PR-Plot" for p in out["patterns"])
    # same drawing, negated vector in rules → must NOT match as displaced (outside finding instead)
    rb2 = rules_coords(known_displacements={"SYN": [-dE, -dN]})
    rb2["rules"][3]["parameters"]["reference_dataset"] = {"value": None, "tag": "GAP"}
    rules2 = dump(tmp_path / "rules2.json", rb2)
    rc, msg = run("check_crs.py", "--ingestion", ing, "--rules", rules2, "--site", "SYN",
                  "--out", tmp_path / "bad.json")
    out2 = json.loads((tmp_path / "bad.json").read_text())
    assert rc == 0
    assert not any(p["pattern"].startswith("displaced_layer_group/") for p in out2["patterns"])
    assert any(p["pattern"] == "outside_site_envelope/2D-PR-Plot" for p in out2["patterns"])


# ------------------------------------------------------------------ C2 EPSG normalisation
@pytest.mark.parametrize("declared", [
    "32638",
    "EPSG:32638",
    "EPSG::32638",
    "urn:ogc:def:crs:EPSG::32638",
])
def test_epsg_normalisation_landxml_forms(tmp_path, declared):
    """LandXML CoordinateSystem forms equal rules crs EPSG:32638 after normalisation."""
    rb = rules_coords()
    rb["rules"][3]["parameters"]["reference_dataset"] = {"value": None, "tag": "GAP"}
    rules = dump(tmp_path / "rules.json", rb)
    survey = dump(tmp_path / "egl.xml.json", _landxml(
        "submittals/SYN/egl.xml", "employer",
        [_surf("EGL", E0 - 50, N0 - 50, E0 + 200, N0 + 200)], epsg=declared))
    # put declared only in header.coordinate_system.epsg_code (as parse_landxml does)
    doc = json.loads(survey.read_text(encoding="utf-8"))
    doc["crs"]["declared"] = None
    doc["content"]["header"]["coordinate_system"]["epsg_code"] = declared
    doc["content"]["header"]["coordinate_system"]["desc"] = None
    dump(survey, doc)
    rc, msg = run("check_crs.py", "--ingestion", survey, "--rules", rules, "--site", "SYN",
                  "--out", tmp_path / "o.json")
    out = json.loads((tmp_path / "o.json").read_text())
    assert rc == 0, msg
    assert out["files"][0]["crs"] == "pass"
    assert not any(p["pattern"] == "crs_declared_other" for p in out["patterns"])


@pytest.mark.parametrize("declared", [
    "32638",
    "EPSG:32638",
    "EPSG::32638",
    "urn:ogc:def:crs:EPSG::32638",
])
def test_epsg_normalisation_dwg_geodata_forms(tmp_path, declared):
    """DWG GEODATA declared forms equal rules crs EPSG:32638 after normalisation."""
    rb = rules_coords()
    rb["rules"][3]["parameters"]["reference_dataset"] = {"value": None, "tag": "GAP"}
    rules = dump(tmp_path / "rules.json", rb)
    ing = dump(tmp_path / "a.dwg.json", dwg_ing([poly("P1", "PLOT", sq(E0 + 10, N0 + 10))],
                                                 declared=declared))
    rc, msg = run("check_crs.py", "--ingestion", ing, "--rules", rules, "--site", "SYN",
                  "--out", tmp_path / "o.json")
    out = json.loads((tmp_path / "o.json").read_text())
    assert rc == 0, msg
    assert out["files"][0]["crs"] == "pass"


def test_normalize_crs_helper_unit():
    sys.path.insert(0, str(SC))
    from dwg_geom import crs_equal, normalize_crs  # noqa: E402
    assert normalize_crs("32638") == "EPSG:32638"
    assert normalize_crs("EPSG:32638") == "EPSG:32638"
    assert normalize_crs("EPSG::32638") == "EPSG:32638"
    assert normalize_crs("urn:ogc:def:crs:EPSG::32638") == "EPSG:32638"
    assert crs_equal("32638", "EPSG:32638")
    assert crs_equal("EPSG::32638", "urn:ogc:def:crs:EPSG::32638")
    assert not crs_equal("32638", "EPSG:32637")


# ------------------------------------------------------------------ T9-C2
def _landxml(path, provenance, surfaces, alignments=None, epsg=None):
    return {
        "template_version": "1.1",
        "source": {"path": path, "sha256": SHA, "bytes": 1, "document_no": "SYN-LXML-1",
                   "revision": None, "file_type": "landxml", "provenance": provenance},
        "extractor": {"skill": "parsing-landxml", "script": "parse_landxml.py", "version": "0.1.0",
                      "converter": {"name": None, "version": None}},
        "crs": {"declared": epsg, "units": "m", "insunits": None},
        "title_block": None,
        "content": {
            "header": {"version": "1.2", "date": None, "time": None, "namespace_version": "1.2",
                       "units": {"system": "Metric", "linear": "meter", "area": "squareMeter",
                                 "angular": "decimal degrees", "direction": "north"},
                       "coordinate_system": ({"epsg_code": epsg, "desc": epsg, "horizontal_datum": "WGS84",
                                              "ogc_wkt_sha256": None} if epsg else None),
                       "project_name": "SYN", "application": "test"},
            "carriers": [], "alignments": alignments or [], "profiles": [],
            "surfaces": surfaces, "inventory": {"surfaces": len(surfaces)},
        },
        "warnings": [], "meta": {},
    }


def _surf(name, e0, n0, e1, n1):
    return {"name": name, "desc": None, "surf_type": "TIN",
            "declared": {"area_2d": None, "area_3d": None, "elev_min": None, "elev_max": None},
            "computed": {"area_2d": None, "area_3d": None, "elev_min": None, "elev_max": None},
            "declared_vs_computed": {"area_2d": None, "area_3d": None, "elev_min": None, "elev_max": None},
            "tin_built": True,
            "counts": {"points": 4, "faces": 2, "visible_faces": 2, "invisible_faces": 0,
                       "excluded_faces": 0, "breaklines": 0, "breakline_points": 0, "boundaries": 0,
                       "duplicate_point_ids": 0},
            "extents": {"e_min": e0, "e_max": e1, "n_min": n0, "n_max": n1},
            "tin_file": f"{name}.surface-1.json", "tin_sha256": SHA}


def test_t9_c2_landxml_design_outside_and_survey_coverage(tmp_path):
    """T9-C2: design outside → finding; survey wider → pass; survey narrower → survey_not_covering_site."""
    rb = rules_coords()
    rb["rules"][3]["parameters"]["reference_dataset"] = {"value": None, "tag": "GAP"}
    rules = dump(tmp_path / "rules.json", rb)
    # design far away
    design = dump(tmp_path / "design.xml.json", _landxml(
        "submittals/SYN/design.xml", "parsons",
        [_surf("FG", E0 + 5000, N0 + 5000, E0 + 5100, N0 + 5100)]))
    rc, msg = run("check_crs.py", "--ingestion", design, "--rules", rules, "--site", "SYN",
                  "--out", tmp_path / "d.json")
    out = json.loads((tmp_path / "d.json").read_text())
    assert rc == 0 and msg["result"] == "fail"
    assert any(p["pattern"].startswith("outside_site_envelope/") for p in out["patterns"])
    # survey wider than site boundary 100×100 at E0,N0
    survey_ok = dump(tmp_path / "surv_ok.xml.json", _landxml(
        "submittals/SYN/egl.xml", "employer",
        [_surf("EGL", E0 - 50, N0 - 50, E0 + 200, N0 + 200)]))
    rc, msg = run("check_crs.py", "--ingestion", survey_ok, "--rules", rules, "--site", "SYN",
                  "--out", tmp_path / "sok.json")
    assert rc == 0 and msg["result"] == "pass"
    # survey narrower
    survey_bad = dump(tmp_path / "surv_bad.xml.json", _landxml(
        "submittals/SYN/egl_small.xml", "employer",
        [_surf("NGL", E0 + 10, N0 + 10, E0 + 40, N0 + 40)]))
    rc, msg = run("check_crs.py", "--ingestion", survey_bad, "--rules", rules, "--site", "SYN",
                  "--out", tmp_path / "sbad.json")
    out = json.loads((tmp_path / "sbad.json").read_text())
    assert rc == 0 and msg["result"] == "fail"
    assert any(p["pattern"] == "survey_not_covering_site" for p in out["patterns"])


# ------------------------------------------------------------------ GIS-017
def test_gis017_no_tbm_and_too_far_and_pass(tmp_path):
    rb = rules_coords()
    rb["rules"][3]["parameters"]["reference_dataset"] = {"value": None, "tag": "GAP"}
    # pass: site_tbm inside
    rules = dump(tmp_path / "r_ok.json", rb)
    rc, msg = run("check_control.py", "--rules", rules, "--site", "SYN", "--out", tmp_path / "ok.json")
    assert rc == 0 and msg["result"] == "pass"
    # no TBM
    rb2 = rules_coords()
    rb2["rules"][0]["parameters"]["datum_reference"]["value"]["SYN"]["site_tbm"] = None
    rb2["rules"][3]["parameters"]["reference_dataset"] = {"value": None, "tag": "GAP"}
    rules = dump(tmp_path / "r_no.json", rb2)
    rc, msg = run("check_control.py", "--rules", rules, "--site", "SYN", "--out", tmp_path / "no.json")
    out = json.loads((tmp_path / "no.json").read_text())
    assert rc == 0 and msg["result"] == "fail"
    assert out["patterns"][0]["pattern"] == "no_site_tbm"
    # too far
    rb3 = rules_coords()
    rb3["rules"][0]["parameters"]["datum_reference"]["value"]["SYN"]["site_tbm"] = {
        "E": E0 + 50000, "N": N0 + 50000, "Z": 1.0}
    rb3["rules"][3]["parameters"]["reference_dataset"] = {"value": None, "tag": "GAP"}
    rules = dump(tmp_path / "r_far.json", rb3)
    rc, msg = run("check_control.py", "--rules", rules, "--site", "SYN", "--out", tmp_path / "far.json")
    out = json.loads((tmp_path / "far.json").read_text())
    assert rc == 0 and msg["result"] == "fail"
    assert out["patterns"][0]["pattern"] == "tbm_too_far"


def test_gis017_not_run_without_threshold(tmp_path):
    rb = rules_coords()
    rb["rules"][2]["parameters"]["max_distance_tbm"] = {"value": None, "tag": "GAP"}
    rb["rules"][3]["parameters"]["reference_dataset"] = {"value": None, "tag": "GAP"}
    rules = dump(tmp_path / "r.json", rb)
    rc, msg = run("check_control.py", "--rules", rules, "--site", "SYN", "--out", tmp_path / "o.json")
    assert rc == 0 and msg["result"] == "not_run"


# ------------------------------------------------------------------ GIS-007 / GIS-011 C8
def test_gis007_boundary_off_reference(tmp_path):
    rb = rules_coords()
    # reference = site boundary vertices exactly → pass
    bnd = sq(E0, N0, 100.0)
    rb["rules"][3]["parameters"]["reference_dataset"] = {"value": {"SYN": list(bnd)}, "tag": "PROPOSED"}
    rules = dump(tmp_path / "r.json", rb)
    ing = dump(tmp_path / "a.dwg.json", dwg_ing([poly("B1", "BND", bnd)]))
    rc, msg = run("match_plots.py", "--ingestion", ing, "--rules", rules, "--site", "SYN",
                  "--out", tmp_path / "m.json")
    out = json.loads((tmp_path / "m.json").read_text())
    r = {x["rule_id"]: x for x in out["results"]}
    assert rc == 0 and r["GIS-007"]["result"] == "pass"
    # shift one vertex by 0.05 m → fail
    bad_ref = [list(bnd[0]), list(bnd[1]), list(bnd[2]), [bnd[3][0] + 0.05, bnd[3][1]]]
    # compare boundary (from rules) to bad_ref that doesn't match vertex 3
    rb["rules"][3]["parameters"]["reference_dataset"] = {"value": {"SYN": bad_ref}, "tag": "PROPOSED"}
    # Actually: boundary verts vs nearest ref — if ref is shifted, boundary vertex 3 is 0.05 from nearest
    rules = dump(tmp_path / "r2.json", rb)
    rc, msg = run("match_plots.py", "--ingestion", ing, "--rules", rules, "--site", "SYN",
                  "--out", tmp_path / "m2.json")
    out = json.loads((tmp_path / "m2.json").read_text())
    r = {x["rule_id"]: x for x in out["results"]}
    assert r["GIS-007"]["result"] == "fail"
    assert r["GIS-007"]["patterns"][0]["pattern"] == "boundary_off_reference"


def test_gis011_c8_official_area(tmp_path):
    rb = rules_coords()
    rb["rules"][3]["parameters"]["reference_dataset"] = {"value": None, "tag": "GAP"}
    # drawn square 100×100 = 10000 matches official
    bnd = sq(E0, N0, 100.0)
    rules = dump(tmp_path / "r.json", rb)
    ing = dump(tmp_path / "a.dwg.json", dwg_ing([poly("B1", "BND", bnd)]))
    rc, msg = run("match_plots.py", "--ingestion", ing, "--rules", rules, "--site", "SYN",
                  "--out", tmp_path / "m.json")
    out = json.loads((tmp_path / "m.json").read_text())
    r = {x["rule_id"]: x for x in out["results"]}
    assert rc == 0 and r["GIS-011"]["result"] == "pass"
    # Drawn equals site_boundary (80×80) but official areas are 10000 → site_area_differs
    bad = sq(E0, N0, 80.0)  # 6400
    rb2 = rules_coords()
    rb2["rules"][1]["parameters"]["site_boundary"] = {"value": {"SYN": bad}, "tag": "PROPOSED"}
    rb2["rules"][3]["parameters"]["reference_dataset"] = {"value": None, "tag": "GAP"}
    rules = dump(tmp_path / "r2.json", rb2)
    ing = dump(tmp_path / "b.dwg.json", dwg_ing([poly("B2", "BND", bad)]))
    rc, msg = run("match_plots.py", "--ingestion", ing, "--rules", rules, "--site", "SYN",
                  "--out", tmp_path / "m2.json")
    out = json.loads((tmp_path / "m2.json").read_text())
    r = {x["rule_id"]: x for x in out["results"]}
    assert r["GIS-011"]["result"] == "fail"
    assert any(p["pattern"] == "site_area_differs" for p in r["GIS-011"]["patterns"])


# ------------------------------------------------------------------ candidates include GIS-017
def test_build_candidates_gis017_and_displacement(tmp_path):
    # finding.schema.json only allows QRWN/NMAR/NRJS — remap synthetic envelope to NRJS keys
    rb = rules_coords()
    for r in rb["rules"]:
        for _k, p in (r.get("parameters") or {}).items():
            v = p.get("value")
            if isinstance(v, dict) and "SYN" in v:
                v["NRJS"] = v.pop("SYN")
    rb["rules"][3]["parameters"]["reference_dataset"] = {"value": None, "tag": "GAP"}
    rb["rules"][0]["parameters"]["datum_reference"]["value"]["NRJS"]["site_tbm"] = None
    rules = dump(tmp_path / "rules.json", rb)
    dE, dN = -100.0, 50.0
    disp = [poly(f"D{i}", "2D-PR-Plot", sq(E0 + 10 - dE + i * 25, N0 + 10 - dN)) for i in range(3)]
    ing = dump(tmp_path / "a.dwg.json", dwg_ing(disp, path="submittals/NRJS/T9/plan.dwg",
                                                 doc="LDIP-RCRC-SAP-01-NRJS-MPL-DWG-00-10001"))
    assert run("check_crs.py", "--ingestion", ing, "--rules", rules, "--site", "NRJS",
               "--out", tmp_path / "gis008.json")[0] == 0
    assert run("check_control.py", "--rules", rules, "--site", "NRJS",
               "--out", tmp_path / "gis017.json")[0] == 0
    rc, msg = run("build_candidates.py",
                  "--results", tmp_path / "gis008.json", tmp_path / "gis017.json",
                  "--rules", rules, "--run", "NRJS-T9-20261101T1200", "--site", "NRJS", "--sub", "T9",
                  "--out", tmp_path / "c.json")
    assert rc == 0, msg
    cands = json.loads((tmp_path / "c.json").read_text())
    by = {}
    for c in cands:
        by.setdefault(c["reference"]["rule_id"], []).append(c)
    assert by["GIS-008"] and by["GIS-008"][0]["proposed_status"] == "Probable"
    assert any("displaced" in (c.get("title") or "").casefold()
               or "displaced" in (c.get("statement") or "").casefold()
               or "displacement" in (c.get("statement") or "").casefold()
               for c in by["GIS-008"]), [c.get("title") for c in by["GIS-008"]]
    assert by["GIS-017"][0]["evidence"][0]["script"]["name"].endswith("check_control.py")
    assert by["GIS-017"][0]["proposed_status"] == "Missing information"


def test_guard_allows_check_control(tmp_path):
    sys.path.insert(0, str(REPO / "tests"))
    from test_plugin_scripts import guard  # noqa: E402
    root = tmp_path / "LDIP & Review"
    for d in ("runs", "rulebook", "0.ai_context/QRWN/T0001/b01", "submittals/QRWN/T0001", "findings"):
        (root / d).mkdir(parents=True, exist_ok=True)
    S = f"{PLUGIN}/skills/checking-cadastral-geometry/scripts"
    E = "findings/QRWN-T0001-20260930T1200/evidence/gis"
    cmd = f'python "{S}/check_control.py" --rules {E}/rules_GIS.json --site QRWN --out {E}/gis017.json'
    assert guard(root, "Bash", {"command": cmd}, agent_type="ldip-design-review:gis-cadastral") == "allow"
