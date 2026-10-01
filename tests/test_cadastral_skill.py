"""Tests for skill 9 checking-cadastral-geometry, schedule rules (SPC-0006 §4.6; SPC-0004 §5.2, §7).
Synthetic workbooks only. The real-data expectations of SPC-0004 §8.1 run only when LDIP_REVIEW_ROOT
points to a local LDIP_Review folder (never in Git, brief §10.2)."""
import csv
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

openpyxl = pytest.importorskip("openpyxl")

REPO = Path(__file__).resolve().parents[1]
SC = REPO / "plugin/ldip-design-review/skills/checking-cadastral-geometry/scripts"
PY = sys.executable
RUN = "QRWN-TEST-20260930T1200"


def run(script, *args):
    p = subprocess.run([PY, str(SC / script), *map(str, args)], capture_output=True, text=True)
    return p.returncode, json.loads(p.stdout.strip().splitlines()[-1])


# ------------------------------------------------------------------ synthetic inputs
H1 = ["Plot ID", "Land Use", None, "Land Area", "Plots", "Units", "Height", "FAR", "GFA",
      "GFA Breakdown (m2)", None, None, "Population", None, None, "Parking", None]
H2 = [None, "Level 1", "Level 2", "m2", "#", "#", "Floors", None, "m2",
      "Residential", "Retail", "Comm. Fac.", "Residents", "Workers", "Visitors", "Required", "Provided"]


def dlus_rows():
    #        id    L1                     L2               area   plots units h      far  gfa    res  ret  cf    resid wk vis park
    return [
        ["1", "Residential", "Villa - Duplex", 300.0, 1, 1, "G+2", 1.2, 360.0, 360.0, 0, 0, 4.8, 0, 0, 1, None],
        ["2", "Residential", "Villa - Duplex", 320.0, 1, 1, "G+2", 1.2, 384.0, 384.0, 0, 0, 4.8, 0, 0, 1, None],  # GIS-013 villa
        ["3", "Residential", "Villa - Duplex", 310.0, 1, 1, "G+2", 1.2, 360.0, 360.0, 0, 0, 4.8, 0, 0, 2, None],  # GIS-014 parking
        ["3", "Community Facilities", "Local Masjid", 1000.0, 1, 0, "G+1", 0.5, 1000.0, 0, 0, 1000.0, 0, 10, 50, 5, None],  # dup id + FAR
        ["5", "Community Facilities", "Civil defense", 800.0, 1, 0, "G", 0.5, 400.0, 0, 0, 400.0, 0, 5, 1, 2, None],
        ["6", "Open Space", "Amenity Green", 200.0, 1, None, None, None, None, None, None, None, None, None, None, None, None],
        ["7", "Open Space", "Amenity Green", 150.0, 1, None, None, None, None, None, None, None, None, None, None, None, None],
        ["8", "Residential", "Villa - Effected by boundary", 55.0, 1, 1, "G+2", 1.2, 360.0, 360.0, 0, 0, 4.8, 0, 0, 1, None],
        ["-", "street", "street", 1000.0, None, None, None, None, None, None, None, None, None, None, None, None, None],
    ]


def summary_rows(amenity=True, label=True):
    rows = [
        [None, "Residential", "Villa - Duplex", 930.0, 0.2, 3, 3, "G+2", 1.19, 1104.0],
        [None, "Community Facilities", "Local Masjid", 1000.0, 0.2, 1, 0, "G+1", 1.0, 1000.0],
        [None, "Civil defense", "Civil defense", 800.0, 0.2, 1, 0, "G", 0.5, 400.0],        # GIS-003: L1 differs
    ]
    if amenity:
        rows.append([None, "Open Space", "Amenity Green", 350.0, 0.1, 1])                     # GIS-012: 1 vs 2 plots
    rows.append([None, "street", "street", 1000.0, 0.3])
    rows.append([None, None, "TOTAL", 4080.0, 1.0, 6])
    rows.append([None, None, "check", 4135.0])
    if label:
        rows.append([None, None, None, 55.0, None, "Total area effected by boundary"])
    return rows


def build_workbook(path, amenity=True, label=True, site_area=4135.0):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "DLUS"
    ws.append(["Detailed Land Use Schedule"])
    ws.append([])
    ws.append(H1)
    ws.append(H2)
    for r in dlus_rows():
        ws.append(r)
    ws.append([])
    ws.append(["CMP check", None, None, 4135.0])          # trailing block: not plot data
    s = wb.create_sheet("Overall Summary")
    s.append([])
    s.append([None, "LAND USE SUMMARY - OVERALL"])
    s.append([])
    s.append([None, "Land Use", None, "Land Area", None, "Plots", "Units", "Height", "FAR", "GFA"])
    s.append([None, "Level 1", "Level 2", "m2", "%", "#", "#", "Floors", None, "m2"])
    for r in summary_rows(amenity, label):
        s.append(r)
    pa = wb.create_sheet("Planning Assumptions")
    pa.append([None, "Land Use", None, "Residents", None, None, "Parking", None, None])
    pa.append([None, "Level 1", "Level 2", "Rate", "Unit", "Basis", "Rate", "Unit", "Basis"])
    pa.append([None, "Residential", "Villas", 4.8, 1, "unit", 1, 1, "unit"])
    cf = wb.create_sheet("Community Facilities")
    cf.append([])
    cf.append([None, "Site Area (m2)", site_area])
    cf.append([None, "Site Area (ha)", site_area / 10000])
    cf.append([None, "Residents", 19.2])
    cf.append([None, "Density (pr/ha)", 19.2 / (site_area / 10000)])
    wb.save(path)
    return path


def param(value, unit=None):
    return {"value": value, "tag": "AGREED", "blocking": False, "source_id": "SRC-09", "clause": "synthetic", "unit": unit}


def gis_rule(rid, state="active", params=None, severity="Minor"):
    return {"rule_id": rid, "title": f"Synthetic {rid}", "skill": "checking-cadastral-geometry", "state": state,
            "cap": "Confirmed" if state == "active" else "Probable", "statement": f"{rid} requirement shall hold.",
            "method": "synthetic", "inputs": ["dlus"], "parameters": params or {}, "skip_subchecks_for": [],
            "code_lists": {}, "severity_default": severity, "issue_type": "inconsistency",
            "default_status": {"xlsx_cell": "Confirmed", "csv_cell": "Confirmed", "script_output": "Confirmed"},
            "exclusions": [], "owner_role": "Lead GIS"}


def rules_file(tmp_path, drop=(), gis002_state="advisory"):
    rules = [
        gis_rule("GIS-001", severity="Major"),
        gis_rule("GIS-002", state=gis002_state, severity="Critical"),
        gis_rule("GIS-003", params={"controlled_vocabulary": {"value": None, "tag": "GAP", "blocking": False}}),
        gis_rule("GIS-012", params={"area_tol": param(1.0, "m2")}),
        gis_rule("GIS-013", params={"tol": param(1.0, "m2"), "fixed_villa_gfa": param(360.0, "m2")}),
        gis_rule("GIS-014", params={"tol_persons": param(0.05), "tol_spaces": param(0.01)}),
        gis_rule("GIS-015", params={"tol_rel": param(0.5, "%")}),
    ]
    rules = [r for r in rules if r["rule_id"] not in drop]
    p = tmp_path / "rules_GIS.json"
    p.write_text(json.dumps({"rulebook_version": "v9", "agent": "GIS", "rules": rules, "rules_skipped": []}))
    return p


def pipeline(tmp_path, wb, **kw):
    ev = tmp_path / "findings" / "evidence" / "gis"          # not created: scripts make it (live trial)
    d, g1, sc = ev / "dlus.json", ev / "g001.json", ev / "sched.json"
    rules = rules_file(tmp_path, **kw)
    assert run("read_dlus.py", "--file", wb, "--out", d)[0] == 0
    assert run("check_ids.py", "--dlus", d, "--out", g1)[0] == 0
    rc, out = run("check_schedule.py", "--dlus", d, "--rules", rules, "--pa-file", wb,
                  "--summary-file", wb, "--cf-file", wb, "--out", sc)
    assert rc == 0, out
    res = {r["rule_id"]: r for r in json.loads(sc.read_text())["results"]}
    return d, g1, sc, rules, res


# ------------------------------------------------------------------ GIS-002 / GIS-001
def test_read_dlus_block_and_street(tmp_path):
    wb = build_workbook(tmp_path / "s.xlsx")
    rc, out = run("read_dlus.py", "--file", wb, "--out", tmp_path / "d.json")
    assert rc == 0 and out["result"] == "pass"
    assert out["plot_rows"] == 8 and out["street_rows"] == 1 and out["trailing_rows_ignored"] == 1


def test_read_dlus_missing_column_fails_gis002(tmp_path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "DLUS"
    ws.append(["Plot ID", "Land Use", None, "Area"])
    ws.append([None, "Level 1", "Level 2", "m2"])
    ws.append(["1", "Residential", "Villa", 300])
    wb.save(tmp_path / "bad.xlsx")
    rc, out = run("read_dlus.py", "--file", tmp_path / "bad.xlsx", "--out", tmp_path / "d.json")
    assert rc == 0 and out["result"] == "fail"
    rc, out = run("check_schedule.py", "--dlus", tmp_path / "d.json", "--rules", rules_file(tmp_path),
                  "--out", tmp_path / "s.json")
    res = json.loads((tmp_path / "s.json").read_text())["results"]
    assert rc == 0 and {r["result"] for r in res} == {"not_run"}          # SPC-0006 §4.6.4


def test_duplicate_plot_id(tmp_path):
    wb = build_workbook(tmp_path / "s.xlsx")
    run("read_dlus.py", "--file", wb, "--out", tmp_path / "d.json")
    rc, out = run("check_ids.py", "--dlus", tmp_path / "d.json", "--out", tmp_path / "g.json")
    g = json.loads((tmp_path / "g.json").read_text())
    assert rc == 0 and g["result"] == "fail" and g["excluded_plot_ids"] == ["3"]
    assert g["patterns"][0]["count_rows"] == 2


def test_input_errors(tmp_path):
    assert run("read_dlus.py", "--file", tmp_path / "none.xlsx", "--out", tmp_path / "d.json")[0] == 2
    wb = build_workbook(tmp_path / "s.xlsx")
    assert run("read_dlus.py", "--file", wb, "--sheet", "NOPE", "--out", tmp_path / "d.json")[0] == 2
    long_out = tmp_path / ("x" * 240) / "d.json"
    assert run("read_dlus.py", "--file", wb, "--out", long_out)[0] == 2


# ------------------------------------------------------------------ schedule rules
def test_schedule_rules_on_seeded_workbook(tmp_path):
    wb = build_workbook(tmp_path / "s.xlsx")
    _, _, _, _, res = pipeline(tmp_path, wb)
    pats = {rid: {p["pattern"].split("/")[0]: p for p in r.get("patterns", [])} for rid, r in res.items()}
    assert res["GIS-003"]["result"] == "fail" and pats["GIS-003"]["level2_under_several_level1"]["count"] == 1
    # labelled 55 m2 line explains the missing 'Villa - Effected by boundary'; Amenity Green 1 vs 2 plots
    assert res["GIS-012"]["reconciled"][0]["level2"] == ["Villa - Effected by boundary"]
    assert set(pats["GIS-012"]) == {"plots_ne_dlus"}
    assert pats["GIS-013"]["villa_gfa_not_fixed"]["count"] == 1
    assert pats["GIS-013"]["gfa_ne_area_x_far"]["count"] == 1         # Local Masjid FAR 0.5, GFA = area
    assert pats["GIS-014"]["parking_req_ne_planning_assumption"]["count"] == 1
    assert set(pats["GIS-015"]) == {"far_ne_summary"}                   # site area equals the DLUS total


def test_unlabelled_difference_and_site_area(tmp_path):
    wb = build_workbook(tmp_path / "s.xlsx", amenity=False, label=False, site_area=10000.0)
    _, _, _, _, res = pipeline(tmp_path, wb)
    p12 = {p["pattern"]: p for p in res["GIS-012"]["patterns"]}
    assert p12["level2_missing_from_summary"]["count"] == 2            # Amenity Green + boundary villas
    p15 = {p["pattern"]: p for p in res["GIS-015"]["patterns"]}
    assert "site_area_ne_dlus" in p15 and "density=" in p15["site_area_ne_dlus"]["samples"][0]["extract"]


def test_csv_export_gives_same_results(tmp_path):
    wb = build_workbook(tmp_path / "s.xlsx")
    book = openpyxl.load_workbook(wb, data_only=True)
    folder = tmp_path / "csv"
    folder.mkdir()
    for name in ("DLUS", "Overall Summary", "Planning Assumptions", "Community Facilities"):
        with open(folder / f"{name}.csv", "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            for row in book[name].iter_rows(values_only=True):
                w.writerow(["" if v is None else (f" {v:,} " if isinstance(v, float) else v) for v in row])
    _, _, _, _, x = pipeline(tmp_path, wb)
    d, sc, rules = tmp_path / "dc.json", tmp_path / "sc.json", rules_file(tmp_path)
    run("read_dlus.py", "--file", folder / "DLUS.csv", "--out", d)
    run("check_schedule.py", "--dlus", d, "--rules", rules, "--pa-file", folder / "Planning Assumptions.csv",
        "--summary-file", folder / "Overall Summary.csv", "--cf-file", folder / "Community Facilities.csv", "--out", sc)
    c = {r["rule_id"]: r for r in json.loads(sc.read_text())["results"]}
    for rid in x:
        assert x[rid]["result"] == c[rid]["result"], rid
        assert [p["count"] for p in x[rid].get("patterns", [])] == [p["count"] for p in c[rid].get("patterns", [])], rid


def test_rule_absent_is_not_run(tmp_path):
    wb = build_workbook(tmp_path / "s.xlsx")
    _, _, _, _, res = pipeline(tmp_path, wb, drop=("GIS-013", "GIS-015"))
    assert res["GIS-013"]["result"] == "not_run" and res["GIS-015"]["result"] == "not_run"


def test_missing_sheet_is_not_run(tmp_path):
    wb = build_workbook(tmp_path / "s.xlsx")
    d = tmp_path / "d.json"
    run("read_dlus.py", "--file", wb, "--out", d)
    rc, _ = run("check_schedule.py", "--dlus", d, "--rules", rules_file(tmp_path), "--out", tmp_path / "s.json")
    res = {r["rule_id"]: r for r in json.loads((tmp_path / "s.json").read_text())["results"]}
    assert rc == 0 and res["GIS-012"]["result"] == "not_run" and res["GIS-014"]["result"] == "not_run"


# ------------------------------------------------------------------ candidates
def test_candidates_valid_and_capped(tmp_path):
    wb = build_workbook(tmp_path / "s.xlsx")
    d, g1, sc, rules, _ = pipeline(tmp_path, wb)
    out = tmp_path / "gis.json"
    rc, s = run("build_candidates.py", "--results", d, g1, sc, "--rules", rules, "--run", RUN, "--site", "QRWN",
                "--sub", "TEST", "--document-no", "LDIP-RCRC-SAP-01-QRWN-MPL-SCH-00001", "--review-root", tmp_path,
                "--out", out)
    assert rc == 0, s
    f = json.loads(out.read_text())
    assert s["by_rule"] == {"GIS-001": 1, "GIS-003": 1, "GIS-012": 1, "GIS-013": 2, "GIS-014": 1, "GIS-015": 1}
    assert [x["finding_id"] for x in f] == [f"{RUN}/GIS-{i:03d}" for i in range(1, len(f) + 1)]
    assert all(x["location"]["file_path"] == "s.xlsx" and x["confidence"] == "High" for x in f)
    sheets = {x["reference"]["rule_id"]: x["location"]["table"]["sheet"] for x in f}
    assert sheets["GIS-001"] == "DLUS" and sheets["GIS-012"] == "Overall Summary"
    sys.path.insert(0, str(REPO / "plugin/ldip-design-review/skills/applying-ldip-rulebook/scripts"))
    from rulebook_lib import cap_errors
    rb = {r["rule_id"]: r for r in json.loads(rules.read_text())["rules"]}
    assert cap_errors(f, rb, "v9") == []


def test_advisory_rule_capped_at_probable(tmp_path):
    bad = openpyxl.Workbook()
    ws = bad.active
    ws.title = "DLUS"
    ws.append(["Plot ID", "Land Use", None, "Area"])
    ws.append([None, "Level 1", "Level 2", "m2"])
    bad.save(tmp_path / "bad.xlsx")
    d = tmp_path / "d.json"
    run("read_dlus.py", "--file", tmp_path / "bad.xlsx", "--out", d)
    out = tmp_path / "gis.json"
    rc, _ = run("build_candidates.py", "--results", d, "--rules", rules_file(tmp_path), "--run", RUN, "--site", "QRWN",
                "--sub", "TEST", "--document-no", "X", "--review-root", tmp_path, "--out", out)
    f = json.loads(out.read_text())
    assert rc == 0 and f[0]["reference"]["rule_id"] == "GIS-002" and f[0]["proposed_status"] == "Probable"


# ------------------------------------------------------------------ real data (local only)
REAL = os.environ.get("LDIP_REVIEW_ROOT")


@pytest.mark.skipif(not REAL, reason="LDIP_REVIEW_ROOT not set (real schedules stay out of Git)")
def test_real_schedules_spc0004_8_1(tmp_path):
    root = Path(REAL)
    sk8 = REPO / "plugin/ldip-design-review/skills/applying-ldip-rulebook/scripts/get_rules.py"
    rules = tmp_path / "rules.json"
    subprocess.run([PY, str(sk8), "--rulebook", str(root / "rulebook/rules_v0.json"), "--agent", "GIS", "--out", str(rules)],
                   check=True, capture_output=True)
    ms = root / "submittals"
    cases = {
        "QRWN": (ms / "QRWN/masterplan-schedule/LDIP-RCRC-SAP-01-QRWN-MPL-SCH-00001.xlsx",) * 4,
        "NMAR": (ms / "NMAR/masterplan-schedule/LDIP-RCRC-SAP-02-NMAR-MPL-SCH-00001.xlsx",) * 4,
        "NRJS": tuple(ms / "NRJS/masterplan-schedule" / f"{n}.csv"
                      for n in ("DLUS", "Planning Assumptions", "Overall Summary", "Community Facilities")),
    }
    got = {}
    for site, (dl, pa, su, cf) in cases.items():
        d, g1, sc = tmp_path / f"{site}_d.json", tmp_path / f"{site}_g.json", tmp_path / f"{site}_s.json"
        rc, dsum = run("read_dlus.py", "--file", dl, "--out", d)
        run("check_ids.py", "--dlus", d, "--out", g1)
        run("check_schedule.py", "--dlus", d, "--rules", rules, "--pa-file", pa, "--summary-file", su, "--cf-file", cf, "--out", sc)
        res = {r["rule_id"]: r for r in json.loads(sc.read_text())["results"]}
        res["GIS-001"] = json.loads(g1.read_text())
        got[site] = (dsum, res)
    q, n, j = got["QRWN"][1], got["NMAR"][1], got["NRJS"][1]
    assert got["QRWN"][0]["plot_rows"] == 6542 and got["NMAR"][0]["plot_rows"] == 874 and got["NRJS"][0]["plot_rows"] == 167
    assert q["GIS-001"]["patterns"][0]["count_ids"] == 7 and q["GIS-001"]["patterns"][0]["count_rows"] == 14
    assert n["GIS-001"]["result"] == "pass" and j["GIS-001"]["result"] == "pass"
    assert q["GIS-003"]["patterns"][0]["count"] == 3 and n["GIS-003"]["result"] == "pass"
    assert [p["pattern"] for p in q["GIS-012"]["patterns"]] == ["plots_ne_dlus"]
    assert n["GIS-012"]["patterns"][0]["samples"][0]["dlus_plots"] == 81
    assert sum(p["count"] for p in q["GIS-013"]["patterns"]) == 29
    assert {p["pattern"]: p["count"] for p in n["GIS-013"]["patterns"]}["villa_gfa_not_fixed"] == 97
    assert q["GIS-014"]["patterns"][0]["count"] == 5713 and n["GIS-014"]["result"] == "pass"
    assert "site_area_ne_dlus" in {p["pattern"] for p in n["GIS-015"]["patterns"]}
    assert "density=5.4" in {p["pattern"]: p for p in j["GIS-015"]["patterns"]}["site_area_ne_dlus"]["samples"][0]["extract"]
