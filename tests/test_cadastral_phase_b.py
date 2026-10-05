"""Tests for skill 9 phase (b): check_crs.py (GIS-008), check_topology.py (GIS-009), match_plots.py
(GIS-004…007, 010, 011, 016) and the drawing branch of build_candidates.py (SPC-0006 §4.6).

Synthetic ingestion files only. Rule values that rules_v0 does not hold (site envelope, plot layers,
label source, tolerances) are set in a test rules file here; they are test values, not rulebook values.
The real-drawing checks (T1, T4) run only when LDIP_DWG_INGESTION points to a local folder holding
T1.dwg.json / T4.dwg.json and LDIP_RULES_GIS to a rules_GIS.json made from the approved rulebook
(never in Git, brief §10.2)."""
import copy
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("shapely")

REPO = Path(__file__).resolve().parents[1]
PLUGIN = REPO / "plugin/ldip-design-review"
SC = PLUGIN / "skills/checking-cadastral-geometry/scripts"
PY = sys.executable
RUN = "NRJS-T0001-20261005T1200"
SHA = "a" * 64

# NRJS-like synthetic coordinates (not survey values)
E0, N0 = 661000.0, 2756000.0


def run(script, *args):
    p = subprocess.run([PY, str(SC / script), *map(str, args)], capture_output=True, text=True)
    return p.returncode, json.loads(p.stdout.strip().splitlines()[-1])


def sq(x, y, s=20.0):
    return [[x, y], [x + s, y], [x + s, y + s], [x, y + s]]


def area(v):
    a = 0.0
    for (x1, y1), (x2, y2) in zip(v, v[1:] + v[:1]):
        a += x1 * y2 - x2 * y1
    return round(abs(a) / 2, 2)


def poly(h, layer, verts, bulge=False):
    return {"type": "LWPOLYLINE", "layer": layer, "handle": h, "vertices": verts, "n_vertices": len(verts),
            "has_bulge": bulge, "elevation": 0.0, "area": area(verts)}


def text(h, layer, s, x, y, space="model"):
    return {"type": "TEXT", "layer": layer, "handle": h, "text": s, "insert": [x, y, 0.0], "height": 1.0,
            "rotation": 0.0, "space": space}


def ingestion(polys, texts=(), insunits=6, declared=None, open_by_layer=None, path="submittals/NRJS/T0001/plan.dwg",
              doc="LDIP-RCRC-SAP-01-NRJS-MPL-DWG-00-10001"):
    return {"template_version": "1.0",
            "source": {"path": path, "sha256": SHA, "bytes": 1, "document_no": doc, "revision": None, "file_type": "dwg"},
            "extractor": {"skill": "extracting-dwg-data", "script": "extract_dxf.py", "version": "0.1.2",
                          "converter": {"name": "oda", "version": "27.1.0"}},
            "crs": {"declared": declared, "units": "m", "insunits": insunits},
            "title_block": None,
            "content": {"header": {"insunits": insunits, "extmin": [1e20] * 3, "extmax": [-1e20] * 3},
                        "polylines": list(polys), "texts": list(texts), "blocks": [],
                        "open_polylines_by_layer": open_by_layer or {}},
            "warnings": [], "meta": {"extracted_at": "2026-10-05T12:00:00+03:00"}}


def plan(shift=(0.0, 0.0)):
    """Three villa plots + one facility, labelled by text on layer T1; a title-block frame far away."""
    dx, dy = shift
    ps = [poly("A1", "S1", sq(E0 + dx, N0 + dy)), poly("A2", "S1", sq(E0 + 20 + dx, N0 + dy)),
          poly("A3", "S1", sq(E0 + 40 + dx, N0 + dy)), poly("A4", "POL.", sq(E0 + dx, N0 + 20 + dy, 30.0))]
    ts = [text("T01", "T1", "1", E0 + 10 + dx, N0 + 10 + dy), text("T02", "T1", "2", E0 + 30 + dx, N0 + 10 + dy),
          text("T03", "T1", "3", E0 + 50 + dx, N0 + 10 + dy), text("T04", "T1", "4", E0 + 15 + dx, N0 + 35 + dy)]
    return ps, ts


RULE_BASE = {"severity_default": "Major", "issue_type": "design_error", "owner_role": "Lead GIS", "exclusions": [],
             "code_lists": {}, "inputs": [], "method": "", "skip_subchecks_for": [], "skill": "checking-cadastral-geometry"}


def rule(rid, state, params, title="t", statement="s", default=None):
    return dict(RULE_BASE, rule_id=rid, state=state, cap="Confirmed" if state == "active" else "Probable",
                title=title, statement=statement,
                parameters={k: (v if isinstance(v, dict) and "tag" in v else {"value": v, "tag": "PROPOSED"})
                            for k, v in params.items()},
                default_status=default or {"dwg_vector": "Confirmed", "csv_cell": "Confirmed", "xlsx_cell": "Confirmed",
                                           "script_output": "Confirmed"})


GAP = {"value": None, "tag": "GAP"}


def rules_v0_like():
    """GIS-008 / GIS-009 as in rules_v0 (no envelope, no plot layers); matching rules absent (inactive)."""
    return {"rulebook_version": "v0", "agent": "GIS", "rules_skipped": [], "rules": [
        rule("GIS-008", "advisory", {"crs": "EPSG:32638", "insunits": 6, "envelope_buffer": 25.0, "base_survey_point": GAP},
             statement="Georeferenced DWGs shall be in EPSG:32638 with metre units, inside the site envelope."),
        rule("GIS-009", "advisory", {"overlap_tol": GAP, "gap_tol": GAP, "sliver_min_area": GAP}),
    ]}


def rules_full():
    rb = rules_v0_like()
    rb["rules"][0]["parameters"]["site_envelope"] = {"value": {"NRJS": {"bbox": [E0 - 10, N0 - 10, E0 + 200, N0 + 200]}},
                                                     "tag": "PROPOSED"}
    rb["rules"][1]["parameters"].update({"plot_layers": {"value": {"NRJS": ["S1", "POL."]}, "tag": "PROPOSED"},
                                         "overlap_tol": {"value": 0.5, "tag": "PROPOSED"},
                                         "sliver_min_area": {"value": 1.0, "tag": "PROPOSED"}})
    rb["rules"] += [
        rule("GIS-004", "active", {"layer_mapping": {"NRJS": {"S1": "Villa - Duplex", "POL.": "Police Station"}}}),
        rule("GIS-005", "active", {"plot_label_source": {"NRJS": {"polygon_layers": ["S1", "POL."], "text_layers": ["T1"]}}}),
        rule("GIS-006", "active", {"tolerance_abs": 0.5, "tolerance_rel": GAP}),
        rule("GIS-010", "active", {"min_area_by_land_use": {"Villa - Duplex": 300.0}}),
        rule("GIS-011", "active", {"tolerance_abs": 1.0, "site_boundary": {"NRJS": sq(E0, N0, 50.0)}}),
    ]
    return rb


def dlus(rows, path="submittals/NRJS/masterplan-schedule/DLUS.csv", result="pass"):
    recs = []
    for i, (pid, l2, a) in enumerate(rows):
        r = i + 3
        recs.append({"row": r, "street": pid == "-", "plot_id": pid, "level1": "x", "level2": l2, "land_area": a,
                     "cells": {"plot_id": f"A{r}", "level2": f"C{r}", "land_area": f"D{r}"}})
    return {"source": {"path": path, "sha256": SHA, "source_type": "csv_cell", "sheet": "DLUS"}, "rows": recs,
            "result": {"rule_id": "GIS-002", "result": result,
                       "issues": [] if result == "pass" else [{"pattern": "missing_column", "message": "x"}]}}


@pytest.fixture
def w(tmp_path):
    return tmp_path


def dump(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj), encoding="utf-8")
    return path


# ------------------------------------------------------------------ GIS-008
def test_gis008_v0_values_units_and_crs(w):
    rules = dump(w / "rules.json", rules_v0_like())
    ps, ts = plan()
    good = dump(w / "a.dwg.json", ingestion(ps, ts))
    rc, res = run("check_crs.py", "--ingestion", good, "--rules", rules, "--site", "NRJS", "--out", w / "o.json")
    assert rc == 0 and res["result"] == "pass" and res["subchecks_not_run"] == ["envelope"]
    out = json.loads((w / "o.json").read_text())
    assert out["files"][0]["crs"] == "not_declared" and out["files"][0]["units"] == "pass"
    assert "P-S3-44" in out["subchecks_not_run"][0]["reason"]
    bad = dump(w / "b.dwg.json", ingestion(ps, ts, insunits=4, declared="EPSG:4326", path="submittals/NRJS/T0001/b.dwg"))
    rc, res = run("check_crs.py", "--ingestion", good, bad, "--rules", rules, "--site", "NRJS", "--out", w / "o2.json")
    out = json.loads((w / "o2.json").read_text())
    assert rc == 0 and res["result"] == "fail"
    assert sorted(p["pattern"] for p in out["patterns"]) == ["crs_declared_other", "insunits_not_metres"]
    assert all(p["source"]["path"].endswith("b.dwg") for p in out["patterns"])


def test_gis008_envelope_seeded_shift(w):
    """SPC-0006 §4.6.5 T4: plot geometry shifted by ΔE −1,107.56 / ΔN +34.31 m lies outside the envelope."""
    rules = dump(w / "rules.json", rules_full())
    ps, ts = plan()
    rc, res = run("check_crs.py", "--ingestion", dump(w / "ok.dwg.json", ingestion(ps, ts)), "--rules", rules,
                  "--site", "NRJS", "--out", w / "ok.json")
    assert rc == 0 and res["result"] == "pass" and res["subchecks_not_run"] == []
    ps, ts = plan(shift=(-1107.56, 34.31))
    rc, res = run("check_crs.py", "--ingestion", dump(w / "sh.dwg.json", ingestion(ps, ts)), "--rules", rules,
                  "--site", "NRJS", "--out", w / "sh.json")
    out = json.loads((w / "sh.json").read_text())
    assert rc == 0 and res["result"] == "fail"
    assert {p["pattern"]: p["count"] for p in out["patterns"]} == {
        "outside_site_envelope/S1": 3, "outside_site_envelope/POL.": 1, "outside_site_envelope/T1": 4}
    assert out["files"][0]["entities_outside_by_layer"] == {"POL.": 1, "S1": 3, "T1": 4}


def test_gis008_buffer_edge_and_polygon_envelope(w):
    rb = rules_full()
    rb["rules"][0]["parameters"]["site_envelope"]["value"]["NRJS"] = {"polygon": sq(E0, N0, 100.0)}
    rules = dump(w / "rules.json", rb)
    inside = [poly("B1", "S1", [[E0 - 24.9, N0], [E0, N0], [E0, N0 + 5]])]       # within the 25 m buffer
    outside = [poly("B2", "S1", [[E0 - 25.5, N0], [E0, N0], [E0, N0 + 5]])]      # 0.5 m beyond it
    rc, res = run("check_crs.py", "--ingestion", dump(w / "i.json", ingestion(inside)), "--rules", rules, "--site", "NRJS",
                  "--out", w / "i_o.json")
    assert res["result"] == "pass"
    rc, res = run("check_crs.py", "--ingestion", dump(w / "x.json", ingestion(outside)), "--rules", rules, "--site", "NRJS",
                  "--out", w / "x_o.json")
    assert res["result"] == "fail"


def test_gis008_rule_absent_and_input_errors(w):
    rb = rules_v0_like()
    rb["rules"] = rb["rules"][1:]
    rules = dump(w / "rules.json", rb)
    ps, ts = plan()
    ing = dump(w / "a.json", ingestion(ps, ts))
    rc, res = run("check_crs.py", "--ingestion", ing, "--rules", rules, "--site", "NRJS", "--out", w / "o.json")
    assert rc == 0 and res["result"] == "not_run"
    rc, res = run("check_crs.py", "--ingestion", w / "missing.json", "--rules", rules, "--site", "NRJS", "--out", w / "o.json")
    assert rc == 2
    pdf = ingestion(ps, ts)
    pdf["source"]["file_type"] = "pdf"
    rc, res = run("check_crs.py", "--ingestion", dump(w / "p.json", pdf), "--rules", rules, "--site", "NRJS", "--out", w / "o.json")
    assert rc == 2
    rc, res = run("check_crs.py", "--ingestion", ing, "--rules", rules, "--site", "NRJS", "--out", w / ("x" * 260 + ".json"))
    assert rc == 2
    bad = rules_full()
    bad["rules"][0]["parameters"]["site_envelope"]["value"]["NRJS"] = {"bbox": [1, 2]}
    rc, res = run("check_crs.py", "--ingestion", ing, "--rules", dump(w / "bad.json", bad), "--site", "NRJS", "--out", w / "o.json")
    assert rc == 2 and "site_envelope" in res["detail"]


# ------------------------------------------------------------------ GIS-009
def topo_input():
    ps, ts = plan()
    bow = poly("C1", "S1", [[E0 + 60, N0], [E0 + 80, N0 + 20], [E0 + 80, N0], [E0 + 60, N0 + 20]])   # self-intersecting
    ovl = poly("C2", "S1", sq(E0 + 41, N0, 18.0))                                                   # overlaps A3 by 342 m2
    tiny = poly("C3", "POL.", sq(E0 + 100, N0, 0.5))                                                # 0.25 m2 sliver
    arc = poly("C4", "S1", sq(E0 + 120, N0), bulge=True)
    road = poly("C5", "ROAD", [[E0, N0 - 50], [E0 + 20, N0 - 30], [E0 + 20, N0 - 50], [E0, N0 - 30]])  # invalid, not a plot layer
    return ingestion(ps + [bow, ovl, tiny, arc, road], ts, open_by_layer={"S1": 2, "ROAD": 9})


def test_gis009_v0_not_run_with_profile(w):
    rules = dump(w / "rules.json", rules_v0_like())
    rc, res = run("check_topology.py", "--ingestion", dump(w / "a.json", topo_input()), "--rules", rules, "--site", "NRJS",
                  "--out", w / "o.json")
    out = json.loads((w / "o.json").read_text())
    assert rc == 0 and res["result"] == "not_run" and out["patterns"] == []
    assert out["files"][0]["profile_invalid_by_layer"] == {"ROAD": 1, "S1": 1}
    assert out["files"][0]["not_checked_bulge_by_layer"] == {"S1": 1}
    assert {s["subcheck"] for s in out["subchecks_not_run"]} == {"closure", "gap", "overlap", "sliver", "validity"}


def test_gis009_with_plot_layers(w):
    rules = dump(w / "rules.json", rules_full())
    rc, res = run("check_topology.py", "--ingestion", dump(w / "a.json", topo_input()), "--rules", rules, "--site", "NRJS",
                  "--out", w / "o.json")
    out = json.loads((w / "o.json").read_text())
    assert rc == 0 and res["result"] == "fail"
    pats = {p["pattern"]: p for p in out["patterns"]}
    assert set(pats) == {"invalid_polygon/S1", "open_polyline/S1", "overlap", "sliver/POL."}
    assert pats["invalid_polygon/S1"]["samples"][0]["handles"] == ["C1"]
    assert pats["overlap"]["count"] == 1 and sorted(pats["overlap"]["samples"][0]["handles"]) == ["A3", "C2"]
    assert pats["open_polyline/S1"]["count"] == 2
    assert [s["subcheck"] for s in out["subchecks_not_run"]] == ["gap"]


# ------------------------------------------------------------------ matching
def match_dlus():
    return dlus([("1", "Villa - Duplex", 400.0), ("2", "Villa - Duplex", 400.6), ("3", "Villa - Duplex", 290.0),
                 ("4", "Villa - Duplex", 900.0), ("9", "Amenity Green", 50.0), ("7", "Villa - Duplex", 300.0),
                 ("7", "Mosque", 500.0), ("-", "street", 100.0)])


def test_match_v0_all_not_run(w):
    rules = dump(w / "rules.json", rules_v0_like())
    ps, ts = plan()
    rc, res = run("match_plots.py", "--ingestion", dump(w / "a.json", ingestion(ps, ts)), "--dlus", dump(w / "d.json", match_dlus()),
                  "--rules", rules, "--site", "NRJS", "--out", w / "m.json")
    assert rc == 0 and set(res["results"].values()) == {"not_run"}
    assert sorted(res["results"]) == ["GIS-004", "GIS-005", "GIS-006", "GIS-007", "GIS-010", "GIS-011", "GIS-016"]


def test_match_full(w):
    rules = dump(w / "rules.json", rules_full())
    ps, ts = plan()
    ps.append(poly("A5", "S1", sq(E0 + 60, N0)))                                   # labelled 8: not in DLUS
    ps.append(poly("A6", "S1", sq(E0 + 80, N0)))                                   # unlabelled
    ps.append(poly("A7", "S1", sq(E0 + 100, N0)))                                  # labelled 7 = GIS-001 duplicate
    ts += [text("T05", "T1", "8", E0 + 70, N0 + 10), text("T07", "T1", "7", E0 + 110, N0 + 10)]
    g1 = dump(w / "g1.json", {"rule_id": "GIS-001", "excluded_plot_ids": ["7"]})
    rc, res = run("match_plots.py", "--ingestion", dump(w / "a.json", ingestion(ps, ts)), "--dlus", dump(w / "d.json", match_dlus()),
                  "--gis001", g1, "--rules", rules, "--site", "NRJS", "--out", w / "m.json")
    out = json.loads((w / "m.json").read_text())
    r = {x["rule_id"]: x for x in out["results"]}
    assert rc == 0
    assert {p["pattern"]: [s["plot_id"] for s in p["samples"]] for p in r["GIS-005"]["patterns"]} == {
        "in_dwg_not_dlus": ["8"], "in_dlus_not_dwg": ["9"]}
    # areas: plot 1 400 vs 400 ok; 2 400 vs 400.6 → 0.6 > 0.5; 3 400 vs 290; 4 900 vs 900
    assert [s["plot_id"] for s in r["GIS-006"]["patterns"][0]["samples"]] == ["2", "3"]
    assert r["GIS-004"]["patterns"][0]["samples"][0]["plot_id"] == "4"         # POL. drawn, DLUS says villa
    assert [p["pattern"] for p in r["GIS-010"]["patterns"]] == ["below_min_area/Villa - Duplex"]
    assert r["GIS-010"]["patterns"][0]["samples"][0]["plot_id"] == "3"
    assert r["GIS-011"]["result"] == "fail"                                     # 2,940.6 vs 2,500 m2
    assert out["plots"]["unlabelled_polygons"] == 1 and out["plots"]["excluded_ids"] == ["7"]
    assert r["GIS-007"]["result"] == r["GIS-016"]["result"] == "not_run"


def test_match_dlus_unreadable(w):
    rules = dump(w / "rules.json", rules_full())
    ps, ts = plan()
    rc, res = run("match_plots.py", "--ingestion", dump(w / "a.json", ingestion(ps, ts)),
                  "--dlus", dump(w / "d.json", dlus([], result="fail")), "--rules", rules, "--site", "NRJS", "--out", w / "m.json")
    out = json.loads((w / "m.json").read_text())
    assert rc == 0 and all(x["result"] == "not_run" for x in out["results"])
    assert any("GIS-002 failed" in (x["reason"] or "") for x in out["results"])


def test_match_profile_and_its_guard(w):
    ps, ts = plan()
    ps.append(poly("A8", "S1", sq(E0 + 140, N0)))
    ts += [text("T08", "T1", "1", E0 + 145, N0 + 5), text("T09", "T1", "48", E0 + 150, N0 + 15)]  # ambiguous
    ing = dump(w / "a.json", ingestion(ps, ts))
    rc, res = run("match_plots.py", "--profile", "--ingestion", ing, "--dlus", dump(w / "d.json", match_dlus()),
                  "--polygon-layers", "S1", "POL.", "--text-layers", "T1", "--out", w / "p.json")
    out = json.loads((w / "p.json").read_text())
    assert rc == 0 and out["mode"] == "profile" and out["plots_labelled"] == 4 and out["ambiguous_polygons"] == 1
    rc, res = run("match_plots.py", "--profile", "--ingestion", ing, "--out", w / "p2.json")
    assert rc == 2
    rc, res = run("build_candidates.py", "--results", w / "p.json", "--rules", dump(w / "r.json", rules_full()), "--run", RUN,
                  "--site", "NRJS", "--sub", "T0001", "--out", w / "c.json")
    assert rc == 2 and "profile" in res["detail"]


# ------------------------------------------------------------------ candidates
def test_build_candidates_drawing_findings(w):
    rb = rules_full()
    rules = dump(w / "rules.json", rb)
    ps, ts = plan(shift=(-1107.56, 34.31))
    ing = dump(w / "a.json", ingestion(ps, ts))
    assert run("check_crs.py", "--ingestion", ing, "--rules", rules, "--site", "NRJS", "--out", w / "gis008.json")[0] == 0
    assert run("check_topology.py", "--ingestion", dump(w / "t.json", topo_input()), "--rules", rules, "--site", "NRJS",
               "--out", w / "gis009.json")[0] == 0
    ps2, ts2 = plan()
    assert run("match_plots.py", "--ingestion", dump(w / "b.json", ingestion(ps2, ts2)), "--dlus", dump(w / "d.json", match_dlus()),
               "--rules", rules, "--site", "NRJS", "--out", w / "match.json")[0] == 0
    rc, res = run("build_candidates.py", "--results", w / "gis008.json", w / "gis009.json", w / "match.json", "--rules", rules,
                  "--run", RUN, "--site", "NRJS", "--sub", "T0001", "--document-no", "LDIP-RCRC-SAP-01-NRJS-MPL-SCH-00001",
                  "--out", w / "c.json")
    assert rc == 0, res
    cands = json.loads((w / "c.json").read_text())
    by = {}
    for c in cands:
        by.setdefault(c["reference"]["rule_id"], []).append(c)
    assert len(by["GIS-008"]) == 3 and len(by["GIS-009"]) == 4
    c8 = by["GIS-008"][0]
    assert c8["location"]["document_no"] == "LDIP-RCRC-SAP-01-NRJS-MPL-DWG-00-10001"
    assert c8["location"]["dwg"]["handles"] and c8["evidence"][0]["source_type"] == "dwg_vector"
    assert c8["proposed_status"] == "Probable"                                       # advisory cap
    assert c8["evidence"][0]["script"]["name"].endswith("check_crs.py")
    dl = [c for c in by["GIS-005"] if c["location"]["file_path"].endswith("DLUS.csv")][0]
    assert dl["location"]["document_no"] == "LDIP-RCRC-SAP-01-NRJS-MPL-SCH-00001" and dl["location"]["table"]["cells"]
    assert by["GIS-005"][0]["proposed_status"] == "Confirmed"
    assert {s["subcheck"] for s in res["not_run"] if s.get("subcheck")} >= {"gap"}
    # no schedule number and a schedule-sourced finding → input error, nothing written
    rc, res = run("build_candidates.py", "--results", w / "match.json", "--rules", rules, "--run", RUN, "--site", "NRJS",
                  "--sub", "T0001", "--out", w / "c2.json")
    assert rc == 2 and not (w / "c2.json").exists()
    # drawing-only results need no schedule number
    rc, res = run("build_candidates.py", "--results", w / "gis008.json", "--rules", rules, "--run", RUN, "--site", "NRJS",
                  "--sub", "T0001", "--out", w / "c3.json")
    assert rc == 0 and res["findings"] == 3


def test_determinism(w):
    rules = dump(w / "rules.json", rules_full())
    ing = dump(w / "a.json", topo_input())
    for name in ("check_crs.py", "check_topology.py"):
        run(name, "--ingestion", ing, "--rules", rules, "--site", "NRJS", "--out", w / "1.json")
        run(name, "--ingestion", ing, "--rules", rules, "--site", "NRJS", "--out", w / "2.json")
        assert (w / "1.json").read_bytes() == (w / "2.json").read_bytes()


# ------------------------------------------------------------------ guard: the agent may run the new scripts
def test_guard_allows_gis_drawing_commands(tmp_path):
    sys.path.insert(0, str(REPO / "tests"))
    from test_plugin_scripts import guard  # noqa: E402
    root = tmp_path / "LDIP & Review"
    for d in ("runs", "rulebook", "0.ai_context/QRWN/T0001/b01", "submittals/QRWN/T0001", "findings"):
        (root / d).mkdir(parents=True, exist_ok=True)
    R, F = "QRWN-T0001-20260930T1200", "findings/QRWN-T0001-20260930T1200"
    S, E, I = f"{PLUGIN}/skills/checking-cadastral-geometry/scripts", f"{F}/evidence/gis", "0.ai_context/QRWN/T0001/b01/A-001.dwg.json"
    cmds = [
        f'python "{S}/check_crs.py" --ingestion {I} --rules {E}/rules_GIS.json --site QRWN --out {E}/gis008.json',
        f'python "{S}/check_topology.py" --ingestion {I} --rules {E}/rules_GIS.json --site QRWN --out {E}/gis009.json',
        f'python "{S}/match_plots.py" --ingestion {I} --dlus {E}/dlus.json --gis001 {E}/gis001.json --rules {E}/rules_GIS.json '
        f'--site QRWN --out {E}/match.json',
        f'python "{S}/build_candidates.py" --results {E}/gis008.json {E}/gis009.json {E}/match.json --rules {E}/rules_GIS.json '
        f'--run {R} --site QRWN --sub T0001 --out {F}/candidates/gis.json',
    ]
    for c in cmds:
        assert guard(root, "Bash", {"command": c}, agent_type="ldip-design-review:gis-cadastral") == "allow", c
    assert guard(root, "Bash", {"command": cmds[0]}, agent_type="ldip-design-review:verifier") == "deny"
    other = cmds[0].replace("QRWN/T0001/b01", "QRWN/T0009/b01")
    assert guard(root, "Bash", {"command": other}, agent_type="ldip-design-review:gis-cadastral") == "deny"


# ------------------------------------------------------------------ real drawings (local only)
REAL = os.environ.get("LDIP_DWG_INGESTION")
REAL_RULES = os.environ.get("LDIP_RULES_GIS")


@pytest.mark.skipif(not (REAL and REAL_RULES), reason="LDIP_DWG_INGESTION / LDIP_RULES_GIS not set (real drawings stay out of Git)")
def test_real_t1_t4_with_rules_v0(tmp_path):
    rules = Path(REAL_RULES)
    for name, site in (("T1", "QRWN"), ("T4", "NRJS")):
        ing = Path(REAL) / f"{name}.dwg.json"
        rc, res = run("check_crs.py", "--ingestion", ing, "--rules", rules, "--site", site, "--out", tmp_path / "8.json")
        out = json.loads((tmp_path / "8.json").read_text())
        assert rc == 0 and res["result"] == "pass" and res["subchecks_not_run"] == ["envelope"]
        assert out["files"][0]["units"] == "pass" and out["files"][0]["crs"] == "not_declared"
        rc, res = run("check_topology.py", "--ingestion", ing, "--rules", rules, "--site", site, "--out", tmp_path / "9.json")
        assert rc == 0 and res["result"] == "not_run"
        rc, res = run("match_plots.py", "--ingestion", ing, "--rules", rules, "--site", site, "--out", tmp_path / "m.json")
        assert rc == 0 and set(res["results"].values()) == {"not_run"}
