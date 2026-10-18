"""Tests for the SPC-0006 Rev J build (phase C3; DJ-1 … DJ-10): details (TJ1–TJ2), calming placement
(TJ3), mini-roundabout / chicane (TJ4), crosswalks (TJ5), refuges (TJ6), parking bays (TJ7), counts
(TJ9). Synthetic DD data only — no C3 element at 30 % (Rev J §4.13G.2); real TJ8 is local-only."""
import hashlib
import json
import math
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("shapely")
pytest.importorskip("jsonschema")

REPO = Path(__file__).resolve().parents[1]
PLUGIN = REPO / "plugin/ldip-design-review"
SK10 = PLUGIN / "skills/reviewing-road-design/scripts"
PY = sys.executable


def run(script, *args):
    p = subprocess.run([PY, str(SK10 / script), *map(str, args)], capture_output=True, text=True)
    out = p.stdout.strip().splitlines()
    return p.returncode, (json.loads(out[-1]) if out else {"stderr": p.stderr[-2000:]})


def load(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def dwg(tmp, name, texts=(), polys=(), blocks=()):
    tlist = []
    for i, item in enumerate(texts):
        t, x, y, layer = item[0], item[1], item[2], item[3]
        rot = item[4] if len(item) > 4 else 0
        tlist.append({"type": "TEXT", "text": t, "layer": layer, "insert": [x, y, 0],
                      "handle": f"{name}T{i:X}", "space": "model", "height": 0.25, "rotation": rot})
    ing = {"template_version": "1.2",
           "source": {"path": f"submittals/NRJS/T/{name}.dwg",
                      "sha256": hashlib.sha256(name.encode()).hexdigest(),
                      "bytes": 1, "document_no": None, "revision": None, "file_type": "dwg"},
           "content": {
               "texts": tlist,
               "polylines": [{"type": "LWPOLYLINE", "layer": layer, "handle": f"{name}P{i:X}",
                              "closed": True, "n_vertices": len(v), "vertices": v,
                              "area": abs(sum(v[k][0] * v[k - 1][1] - v[k - 1][0] * v[k][1]
                                              for k in range(len(v)))) / 2}
                             for i, (layer, v) in enumerate(polys)],
               "blocks": [{"name": n, "insert": [x, y, 0], "handle": f"{name}B{i:X}", "layer": layer}
                          for i, (n, x, y, layer) in enumerate(blocks)],
               "layers": []}}
    p = tmp / f"{name}.dwg.json"
    p.write_text(json.dumps(ing))
    return p


def box(x0, y0, x1, y1):
    return [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]


def rules_c3(tmp, stage_phase="DD"):
    def R(rid, params, phase="DD", state="active", carriers=None):
        return {"rule_id": rid, "title": f"Test {rid}", "state": state, "cap": "Confirmed",
                "statement": f"Synthetic {rid}.", "inputs": carriers or ["details"],
                "parameters": {k: {"value": v, "tag": "AGREED", "source_id": "SRC-T", "clause": "§T",
                                  "unit": u, "cap": "Confirmed", "tolerance": {"value": 0}}
                              for k, (v, u) in params.items()},
                "severity_default": "Minor", "issue_type": "design_error", "check_type": "value",
                "default_status": {"dwg_vector": "Confirmed", "script_output": "Confirmed",
                                   "landxml": "Confirmed"},
                "owner_role": "DMR", "applicability": {"street_class": ["all"]},
                "required_from_phase": phase}

    rs = [
        R("ROD-044", {"length": (3.7, "m"), "height": (75, "mm")}),
        R("ROD-045", {"length": (6.0, "m"), "flat_top": (3.0, "m"), "height": (75, "mm")}),
        R("ROD-046", {"height": (75, "mm"), "ramp": ("1:20", "ratio")}),
        R("ROD-047", {"diameter_min": (12, "m"), "diameter_max": (20, "m"),
                      "chicane_min": (0.6, "m"), "chicane_max": (0.8, "m")}),
        R("ROD-055", {"width": (4.0, "m")}, carriers=["signing_marking_plan"]),
        R("ROD-056", {"width": (2.0, "m"), "length": (10.0, "m")}, carriers=["plan_geometry"]),
        R("ROD-059", {"width": (2.5, "m"), "width_min": (2.5, "m")}, phase="PD",
          carriers=["typical_section", "plan_geometry"]),
        R("ROD-060", {"width": (2.6, "m")}, carriers=["plan_geometry"]),
        R("ROD-061", {"width": (4.2, "m")}, carriers=["plan_geometry"]),
        R("ROD-062", {"ratio": (0.04, None)}, carriers=["plan_geometry"]),
    ]
    p = tmp / "rules_RDE.json"
    p.write_text(json.dumps({
        "agent": "RDE", "rulebook_version": "v7", "rules": rs, "rules_skipped": [],
        "stage_map": {"30% Concept Design": {"phase": "PD", "milestone": 30},
                      "60%": {"phase": "DD", "milestone": 60}}}))
    return p


def base_net(extra=None):
    net = {
        "position_source": "tables",
        "roads": {
            "ROAD-A (15m R.O.W)": {
                "length": 200,
                "geometry": [[660000, 2750000], [660200, 2750000]],
                "locations": [],
            }
        },
        "junctions": [{"id": "J001", "xy": [660100.0, 2750000.0], "roads": ["ROAD-A (15m R.O.W)"],
                       "legs": [], "angles": []}],
        "roundabouts": [],
    }
    if extra:
        net.update(extra)
    return net


def write_net(tmp, extra=None):
    p = tmp / "network.json"
    p.write_text(json.dumps(base_net(extra)))
    (tmp / "class.json").write_text(json.dumps({
        "roads": {"ROAD-A (15m R.O.W)": {"width": 15.0, "class": "local_access"}}}))
    return p


# ------------------------------------------------------------------ TJ1 details
def test_tj1_detail_dimensions(tmp_path):
    # hump 3.70 / 75 labelled → pass; separate sheet with 3.50 → fail
    good = dwg(tmp_path, "DET_OK", texts=[
        ("SPEED HUMP", 100, 100, "TITLE"),
        ("LENGTH", 100, 130, "LAB"), ("3.70 m", 100, 140, "DIM"),
        ("HEIGHT", 120, 130, "LAB"), ("75 mm", 120, 140, "DIM"),
        ("SPEED TABLE", 300, 100, "TITLE"),
        ("LENGTH", 300, 140, "LAB"), ("6.0 m", 300, 150, "DIM"),
        ("FLAT TOP", 320, 140, "LAB"), ("3.0 m", 320, 150, "DIM"),
        ("HEIGHT", 340, 140, "LAB"), ("75mm", 340, 150, "DIM"),
        ("RAISED INTERSECTION", 500, 100, "TITLE"),
        ("HEIGHT", 500, 130, "LAB"), ("75 mm", 500, 140, "DIM"),
        ("RAMP", 520, 130, "LAB"), ("1:20", 520, 140, "DIM"),
    ])
    bad = dwg(tmp_path, "DET_BAD", texts=[
        ("SPEED HUMP", 100, 100, "TITLE"),
        ("LENGTH", 100, 130, "LAB"), ("3.50 m", 100, 140, "DIM"),
        ("HEIGHT", 120, 130, "LAB"), ("75 mm", 120, 140, "DIM"),
        ("SPEED TABLE", 300, 100, "TITLE"),
        ("6.0", 300, 150, "DIM"), ("2.5", 320, 150, "DIM"), ("75", 340, 150, "DIM"),  # positional; flat 2.5
        ("RAISED INTERSECTION", 500, 100, "TITLE"),
        ("HEIGHT", 500, 130, "LAB"), ("75 mm", 500, 140, "DIM"),
        ("RAMP", 520, 130, "LAB"), ("1:15", 520, 140, "DIM"),
        ("SPEED HUMP", 700, 100, "TITLE"),
        ("3700", 700, 140, "DIM"),  # mm digits → 3.7 m positional
        ("75 mm", 720, 140, "DIM"),
    ])
    assert run("read_details.py", "--file", f"details={good}", "--file", f"details={bad}",
               "--out", tmp_path / "details.json")[0] == 0
    det = load(tmp_path / "details.json")
    assert any(d["kind"] == "speed_hump" for d in det["details"])
    write_net(tmp_path)
    rc, out = run("check_calming.py", "--details", tmp_path / "details.json",
                  "--network", tmp_path / "network.json", "--class", tmp_path / "class.json",
                  "--rules", rules_c3(tmp_path), "--stage", "60%", "--site", "NRJS",
                  "--out", tmp_path / "cal.json")
    assert rc == 0, out
    cal = load(tmp_path / "cal.json")
    fails = [(c["rule_id"], c["parameter"], c["observed"]) for c in cal["checks"] if c["result"] == "fail"]
    assert ("ROD-044", "length", 3.5) in fails
    assert any(c["rule_id"] == "ROD-045" and c["parameter"] == "flat_top" and c["result"] == "fail"
               for c in cal["checks"])
    assert any(c["rule_id"] == "ROD-046" and c["parameter"] == "ramp" and c["result"] == "fail"
               for c in cal["checks"])
    # positional → Probable
    assert any(c.get("link_cap") == "Probable" and c.get("assigned") == "positional" for c in cal["checks"] + cal["passed"])
    # 3700 → 3.7 m pass
    assert any(c["rule_id"] == "ROD-044" and c["parameter"] == "length" and c["observed"] == 3.7 and c["result"] == "pass"
               for c in cal["passed"])


def test_tj2_device_without_detail(tmp_path):
    empty = dwg(tmp_path, "DET0", texts=[])
    assert run("read_details.py", "--file", f"details={empty}", "--out", tmp_path / "details.json")[0] == 0
    plan = dwg(tmp_path, "PL", texts=[("SPEED HUMP", 660050, 2750000, "TC")])
    write_net(tmp_path)
    # PD → not run
    rc, _ = run("check_calming.py", "--details", tmp_path / "details.json",
                "--network", tmp_path / "network.json", "--class", tmp_path / "class.json",
                "--rules", rules_c3(tmp_path), "--stage", "30% Concept Design", "--site", "NRJS",
                "--file", f"plan_geometry={plan}", "--out", tmp_path / "pd.json")
    assert rc == 0
    pd = load(tmp_path / "pd.json")
    assert any("not run at PD" in (n.get("reason") or "") or n.get("reason") == "not required at stage"
               for n in pd["not_run"] if n.get("rule_id") == "ROD-044")
    assert not any(c["rule_id"] == "ROD-044" and c.get("status_override") == "Missing information"
                   for c in pd["checks"])
    # DD → Missing information
    rc, _ = run("check_calming.py", "--details", tmp_path / "details.json",
                "--network", tmp_path / "network.json", "--class", tmp_path / "class.json",
                "--rules", rules_c3(tmp_path), "--stage", "60%", "--site", "NRJS",
                "--file", f"plan_geometry={plan}", "--out", tmp_path / "dd.json")
    assert rc == 0
    dd = load(tmp_path / "dd.json")
    miss = [c for c in dd["checks"] if c.get("status_override") == "Missing information"]
    assert len(miss) == 1 and miss[0]["rule_id"] == "ROD-044"
    assert "searched" in miss[0]["evidence"]["extract"].lower() or "DET0" in miss[0]["evidence"]["extract"]


def test_tj3_hump_placement(tmp_path):
    det = dwg(tmp_path, "DET", texts=[
        ("SPEED HUMP", 100, 100, "TITLE"), ("LENGTH", 100, 130, "LAB"), ("3.70 m", 100, 140, "DIM"),
        ("HEIGHT", 120, 130, "LAB"), ("75 mm", 120, 140, "DIM")])
    assert run("read_details.py", "--file", f"details={det}", "--out", tmp_path / "details.json")[0] == 0
    plan = dwg(tmp_path, "PL", texts=[
        ("SPEED HUMP", 660040, 2750000, "TC"),
        ("SPEED HUMP", 660080, 2750000, "TC"),   # 40 m — fail at 30 km/h
        ("SPEED HUMP", 660140, 2750000, "TC"),   # 60 m from previous — pass pair
    ])
    write_net(tmp_path, {
        "curves": [{"road": "ROAD-A (15m R.O.W)", "radius": 50, "pc": [660135, 2750000], "pt": [660145, 2750000],
                    "contains": ["CD003"]}],
        "c3_devices": [],
    })
    # rebuild devices from plan; inject grade via landxml hook
    lx = tmp_path / "lx.json"
    lx.write_text(json.dumps({"path": "x.xml", "sha256": "a" * 64, "hump_grades": [{"id": "CD001", "grade_pct": 7.0}]}))
    crit = tmp_path / "crit.json"
    crit.write_text(json.dumps({"rows": [
        {"criterion": "design_speed", "by_class": {"local_access": {"values": [30]}}},
        {"criterion": "posted_speed", "by_class": {"local_access": {"values": [30]}}},
    ]}))
    rc, out = run("check_calming.py", "--details", tmp_path / "details.json",
                  "--network", tmp_path / "network.json", "--class", tmp_path / "class.json",
                  "--rules", rules_c3(tmp_path), "--stage", "60%", "--site", "NRJS",
                  "--file", f"plan_geometry={plan}", "--landxml", lx, "--criteria", crit,
                  "--out", tmp_path / "cal.json")
    assert rc == 0, out
    cal = load(tmp_path / "cal.json")
    space_fail = [c for c in cal["checks"] if c["parameter"] == "tier2_spacing"]
    space_pass = [c for c in cal["passed"] if isinstance(c, dict) and c.get("parameter") == "tier2_spacing"]
    assert any(abs(c["observed"] - 40) < 0.1 for c in space_fail)
    assert any(abs(c["observed"] - 60) < 0.1 for c in space_pass)
    assert any(c["parameter"] == "tier2_on_curve" for c in cal["checks"])
    assert any(c["parameter"] == "tier2_grade" and c["observed"] == 7.0 for c in cal["checks"])


def test_tj4_mini_rb_and_chicane(tmp_path):
    det = dwg(tmp_path, "DET", texts=[])
    assert run("read_details.py", "--file", f"details={det}", "--out", tmp_path / "details.json")[0] == 0
    # circle D=16 at junction; circle D=24 listed; network roundabout ignored
    r16 = 8.0
    circ16 = [[660100 + r16 * math.cos(a), 2750000 + r16 * math.sin(a)]
              for a in [i * math.pi / 8 for i in range(17)]]
    r24 = 12.0
    circ24 = [[660050 + r24 * math.cos(a), 2750100 + r24 * math.sin(a)]
              for a in [i * math.pi / 8 for i in range(17)]]
    # network RB ring (should be ignored) at 660100, 2750120
    r_net = 20.0
    circ_net = [[660100 + r_net * math.cos(a), 2750120 + r_net * math.sin(a)]
                for a in [i * math.pi / 8 for i in range(17)]]
    # chicane curbs: left shift 0.7, right none
    curb_l = [[660160 + 0.7, 2750000 + y] for y in range(-40, 41)]
    # bend for shift: straight reference 20-40m away, offset in middle
    curb_l = ([[660160, 2749960], [660160, 2749980], [660160.7, 2750000], [660160, 2750020], [660160, 2750040]])
    curb_r = ([[660140, 2749960], [660140, 2750040]])  # straight — shift ~0
    plan = dwg(tmp_path, "PL", texts=[("CHICANE", 660150, 2750000, "TC")],
               polys=[("CURB_MARK", circ16), ("CURB_MARK", circ24), ("CURB_MARK", circ_net),
                      ("CURB_L", curb_l), ("CURB_R", curb_r)])
    # open curb polys (not closed) — rewrite as open by patching
    ing = load(plan)
    for p in ing["content"]["polylines"]:
        if p["layer"] in ("CURB_L", "CURB_R"):
            p["closed"] = False
            p["n_vertices"] = len(p["vertices"])
    plan.write_text(json.dumps(ing))

    write_net(tmp_path, {
        "junctions": [
            {"id": "J001", "xy": [660100.0, 2750000.0], "roads": ["ROAD-A (15m R.O.W)"]},
            {"id": "J002", "xy": [660050.0, 2750100.0], "roads": ["ROAD-A (15m R.O.W)"]},
        ],
        "roundabouts": [{"name": "RA-1", "centre": [660100.0, 2750120.0], "radius": 20.0, "approaches": []}],
        "roads": {
            "ROAD-A (15m R.O.W)": {
                "length": 200,
                "geometry": [[660000, 2750000], [660200, 2750000]],
            }
        },
        "c3_devices": [{"id": "CD099", "xy": [660150.0, 2750000.0], "type": "chicane", "labels": ["CHICANE"],
                        "chicane_shift_m": 0.7, "hits": 1, "handles": []},
                       {"id": "CD098", "xy": [660170.0, 2750000.0], "type": "chicane", "labels": ["CHICANE"],
                        "chicane_shift_m": 1.2, "hits": 1, "handles": []},
                       {"id": "CD097", "xy": [660180.0, 2750000.0], "type": "chicane", "labels": ["CHICANE"],
                        "chicane_shift_m": 0.3, "hits": 1, "handles": []}],
    })
    (tmp_path / "class.json").write_text(json.dumps({
        "roads": {"ROAD-A (15m R.O.W)": {"width": 15.0, "class": "local_access"}}}))
    rc, out = run("check_calming.py", "--details", tmp_path / "details.json",
                  "--network", tmp_path / "network.json", "--class", tmp_path / "class.json",
                  "--rules", rules_c3(tmp_path), "--stage", "60%", "--site", "NRJS",
                  "--file", f"plan_geometry={plan}", "--out", tmp_path / "cal.json")
    assert rc == 0, out
    cal = load(tmp_path / "cal.json")
    dias = [(c["observed"], c["result"]) for c in cal["checks"] + cal["passed"]
            if c.get("parameter") == "diameter"]
    assert any(abs(o - 16) < 1.5 and r == "pass" for o, r in dias)
    assert any(abs(o - 24) < 1.5 and r == "fail" for o, r in dias)
    shifts = {round(c["observed"], 1): c["result"] for c in cal["checks"] + cal["passed"]
              if c.get("parameter") == "chicane_shift"}
    assert shifts.get(0.7) == "pass" and shifts.get(1.2) == "fail" and shifts.get(0.3) == "fail"


def test_tj5_crosswalks(tmp_path):
    # 8 zebra bars spanning 4.0 m along road → pass; 3.0 m → fail; commercial 4.0 → fail (need 6)
    def bars(x0, y0, width_along, n=8):
        # bars parallel to Y (across road along X), spaced along X over `width_along`
        out = []
        for i in range(n):
            x = x0 + (width_along * i / (n - 1) if n > 1 else 0)
            out.append(("MARK", [[x, y0 - 1.5], [x, y0 + 1.5]]))
        return out

    mark = dwg(tmp_path, "MK", polys=bars(660098, 2750000, 4.0) + bars(660048, 2750000, 3.0))
    ing = load(mark)
    for p in ing["content"]["polylines"]:
        p["closed"] = False
    mark.write_text(json.dumps(ing))
    write_net(tmp_path, {
        "junctions": [
            {"id": "J001", "xy": [660100.0, 2750000.0], "roads": ["ROAD-A (15m R.O.W)"]},
            {"id": "J002", "xy": [660050.0, 2750000.0], "roads": ["ROAD-A (15m R.O.W)"]},
        ],
        "corner_returns": [
            # J001 bars 660098–660102: return 2.5 m east of near edge → pass
            {"junction": "J001", "tangent_point": [660104.5, 2750000.0]},
            # J002 bars 660048–660051: return 5.0 m west of near edge → fail
            {"junction": "J002", "tangent_point": [660043.0, 2750000.0]},
        ],
    })
    (tmp_path / "class.json").write_text(json.dumps({
        "roads": {"ROAD-A (15m R.O.W)": {"width": 15.0, "class": "local_access"}}}))
    cad = dwg(tmp_path, "CAD", texts=[("P-001", 660100, 2750030, "PLOT_ID")],
              polys=[("PLOTS", box(660080, 2750010, 660120, 2750050))])
    (tmp_path / "dlus.json").write_text(json.dumps({"rows": [
        {"plot_id": "P-001", "street": False, "level1": "Commercial", "level2": "Retail"}]}))
    (tmp_path / "pls.json").write_text(json.dumps({"NRJS": {"polygon_layers": ["PLOTS"], "text_layers": ["PLOT_ID"]}}))
    rc, out = run("check_pedestrian.py", "--network", tmp_path / "network.json",
                  "--rules", rules_c3(tmp_path), "--stage", "60%", "--site", "NRJS",
                  "--file", f"signing_marking_plan={mark}", "--class", tmp_path / "class.json",
                  "--plots", cad, "--dlus", tmp_path / "dlus.json", "--plot-label-source", tmp_path / "pls.json",
                  "--out", tmp_path / "ped.json")
    assert rc == 0, out
    ped = load(tmp_path / "ped.json")
    widths = [(c["observed"], c["required"], c["result"]) for c in ped["checks"] + ped["passed"]
              if c.get("parameter") == "width"]
    assert any(abs(o - 4.0) < 0.2 and r == "fail" and req == 6.0 for o, req, r in widths)  # commercial
    assert any(abs(o - 3.0) < 0.2 and r == "fail" for o, req, r in widths)
    setbacks = [(c["observed"], c["result"]) for c in ped["checks"] + ped["passed"] if c.get("parameter") == "setback"]
    assert any(abs(o - 2.5) < 0.2 and r == "pass" for o, r in setbacks)
    assert any(abs(o - 5.0) < 0.2 and r == "fail" for o, r in setbacks)


def test_tj6_refuges(tmp_path):
    plan = dwg(tmp_path, "PL", texts=[("REFUGE", 660100, 2750005, "TXT")],
               polys=[("REFUGE", box(660098.8, 2749994, 660101.2, 2750006)),   # 2.4 × 12
                      ("REFUGE", box(660148.25, 2749994, 660149.75, 2750006))])  # 1.5 × 12
    write_net(tmp_path)
    (tmp_path / "class.json").write_text(json.dumps({
        "roads": {"ROAD-A (15m R.O.W)": {"width": 15.0, "class": "local_access"}}}))
    rc, out = run("check_pedestrian.py", "--network", tmp_path / "network.json",
                  "--rules", rules_c3(tmp_path), "--stage", "60%", "--site", "NRJS",
                  "--file", f"plan_geometry={plan}", "--out", tmp_path / "ped.json")
    assert rc == 0, out
    ped = load(tmp_path / "ped.json")
    assert any(c["parameter"] == "size" and c["result"] == "pass" for c in ped["passed"])
    assert any(c["parameter"] == "size" and c["result"] == "fail" for c in ped["checks"])
    assert sum(1 for c in ped["checks"] if c["parameter"] == "tier2_width") == 1
    assert any(n.get("parameter") == "gradient" for n in ped["not_run"])


def test_tj7_parking_bays_and_section(tmp_path):
    write_net(tmp_path, {"c3_bays": [
        {"id": "B1", "kind": "parallel", "width": 2.5, "length": 6.0, "accessible": False},
        {"id": "B2", "kind": "parallel", "width": 2.3, "length": 5.5, "accessible": False},
        {"id": "B3", "kind": "perpendicular", "width": 2.6, "length": 5.5, "accessible": False,
         "on_service_or_commercial": False},
        {"id": "B4", "kind": "perpendicular", "width": 4.2, "length": 6.1, "accessible": True, "aisle_m": 1.5},
        {"id": "B5", "kind": "perpendicular", "width": 3.6, "length": 5.0, "accessible": True, "aisle_m": 1.0},
    ], "c3_parking_areas": []})
    (tmp_path / "class.json").write_text(json.dumps({
        "roads": {"ROAD-A (15m R.O.W)": {"width": 15.0, "class": "local_access"}}}))
    plan = dwg(tmp_path, "PL", texts=[])
    rc, out = run("check_parking.py", "--network", tmp_path / "network.json", "--class", tmp_path / "class.json",
                  "--rules", rules_c3(tmp_path), "--stage", "60%", "--site", "NRJS",
                  "--file", f"plan_geometry={plan}", "--out", tmp_path / "pk.json")
    assert rc == 0, out
    pk = load(tmp_path / "pk.json")
    assert any(c["rule_id"] == "ROD-059" and c["result"] == "pass" for c in pk["passed"])
    assert any(c["rule_id"] == "ROD-059" and c["result"] == "fail" for c in pk["checks"])
    assert any(c["parameter"] == "tier2_length" for c in pk["checks"])
    assert any(c["parameter"] == "tier2_parallel_only_advisory" for c in pk["checks"])
    assert any(c["rule_id"] == "ROD-061" and c["result"] == "pass" for c in pk["passed"])
    assert any(c["rule_id"] == "ROD-061" and c["result"] == "fail" for c in pk["checks"])
    assert sum(1 for c in pk["checks"] if c["parameter"] == "tier2_er_ambiguity") == 1

    # section PARKING LANE 2.50 / 2.20
    sec = dwg(tmp_path, "SEC", texts=[
        ("RIGHT OF WAY", 0, 10, "ROW", 90), ("RIGHT OF WAY", 15, 10, "ROW", 90),
        ("15 m LOCAL ACCESS ROAD", 7, 4, "TITLE"),
        ("2.50", 2, 8, "DIM"), ("PARKING LANE", 2, 7.2, "LAB"),
        ("3.50", 7, 8, "DIM"), ("DRIVING LANE", 7, 7.2, "LAB"),
        ("2.50", 12, 8, "DIM"), ("SIDE WALK", 12, 7.2, "LAB"),
        ("15", 7.5, 9.5, "DIM"),
    ])
    sec2 = dwg(tmp_path, "SEC2", texts=[
        ("RIGHT OF WAY", 0, 10, "ROW", 90), ("RIGHT OF WAY", 15, 10, "ROW", 90),
        ("15 m LOCAL ACCESS ROAD", 7, 4, "TITLE"),
        ("2.20", 2, 8, "DIM"), ("PARKING LANE", 2, 7.2, "LAB"),
        ("3.50", 7, 8, "DIM"), ("DRIVING LANE", 7, 7.2, "LAB"),
        ("15", 7.5, 9.5, "DIM"),
    ])
    rc, out = run("check_sections.py", "--sections", sec, sec2, "--rules", rules_c3(tmp_path),
                  "--out", tmp_path / "sec.json")
    assert rc == 0, out
    secj = load(tmp_path / "sec.json")
    park = [c for c in secj["checks"] if c["criterion"] == "parking"]
    assert any(c["observed"] == 2.5 and c["result"] == "pass" for c in park)
    assert any(c["observed"] == 2.2 and c["result"] == "fail" for c in park)


def test_tj9_accessible_counts_and_determinism(tmp_path):
    write_net(tmp_path, {"c3_bays": [], "c3_parking_areas": [
        {"id": "PA1", "total": 40, "accessible": 1, "land_use": "residential", "xy": [1, 1]},
        {"id": "PA2", "total": 120, "accessible": 4, "land_use": "commercial", "xy": [2, 2]},
    ]})
    (tmp_path / "class.json").write_text(json.dumps({"roads": {}}))
    plan = dwg(tmp_path, "PL", texts=[])
    rc, out = run("check_parking.py", "--network", tmp_path / "network.json", "--class", tmp_path / "class.json",
                  "--rules", rules_c3(tmp_path), "--stage", "60%", "--site", "NRJS",
                  "--file", f"plan_geometry={plan}", "--out", tmp_path / "a.json")
    assert rc == 0, out
    a = load(tmp_path / "a.json")
    counts = {c["subject"]: (c["observed"]["required"], c["result"]) for c in a["checks"] + a["passed"]
              if c.get("parameter") == "accessible_count"}
    assert counts["parking area PA1"] == (2, "fail")
    assert counts["parking area PA2"] == (4, "pass")
    # determinism
    rc, _ = run("check_parking.py", "--network", tmp_path / "network.json", "--class", tmp_path / "class.json",
                "--rules", rules_c3(tmp_path), "--stage", "60%", "--site", "NRJS",
                "--file", f"plan_geometry={plan}", "--out", tmp_path / "b.json")
    assert rc == 0
    for x in (a, load(tmp_path / "b.json")):
        x.pop("inputs", None)
    assert json.dumps(a, sort_keys=True) == json.dumps(load(tmp_path / "b.json"), sort_keys=True)


def test_build_candidates_reads_c3(tmp_path):
    """Skill version 0.7.0; C3 fail checks become findings."""
    write_net(tmp_path, {"c3_parking_areas": [
        {"id": "PA1", "total": 40, "accessible": 1, "land_use": "residential", "xy": [660000, 2750000]}]})
    (tmp_path / "class.json").write_text(json.dumps({"roads": {}}))
    plan = dwg(tmp_path, "PL", texts=[])
    rules = rules_c3(tmp_path)
    assert run("check_parking.py", "--network", tmp_path / "network.json", "--class", tmp_path / "class.json",
               "--rules", rules, "--stage", "60%", "--site", "NRJS",
               "--file", f"plan_geometry={plan}", "--out", tmp_path / "pk.json")[0] == 0
    rc, out = run("build_candidates.py", "--results", tmp_path / "pk.json", "--rules", rules,
                  "--run", "NRJS-T-20261018T1440", "--site", "NRJS", "--sub", "T",
                  "--evidence-dir", tmp_path / "ev", "--out", tmp_path / "cand.json")
    assert rc == 0, out
    cands = load(tmp_path / "cand.json")
    assert cands and cands[0]["agent_versions"] == {"reviewing-road-design": "0.7.0"}
