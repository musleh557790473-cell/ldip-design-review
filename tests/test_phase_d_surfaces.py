"""Phase D first-slice tests (SPC-0006 §4.13H / DK-1…DK-4): TD1–TD8.

ROD-056 gradient, ROD-062 slope, ROD-043 crossfall/drainage. Synthetic TIN only.
ROD-043 numeric limits are [GAP] without rules_v7 — TD5/TD6 assert not_run GAP path;
optional synthetics labelled TEST-ONLY prove comparison wiring when params are injected.
"""
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("shapely")

REPO = Path(__file__).resolve().parents[1]
SK10 = REPO / "plugin/ldip-design-review/skills/reviewing-road-design/scripts"
PY = sys.executable


def run(script, *args):
    p = subprocess.run([PY, str(SK10 / script), *map(str, args)], capture_output=True, text=True)
    out = p.stdout.strip().splitlines()
    return p.returncode, (json.loads(out[-1]) if out else {"stderr": p.stderr[-2000:]})


def load(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def box(x0, y0, x1, y1):
    return [[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]


def write_tin(tmp, name, origin, size, rise_e=0.0, rise_n=0.0, z0=10.0):
    """Axis-aligned TIN rectangle; slope ≈ 100 * hypot(rise_e, rise_n)/size along edges.

    For a pure E-direction grade: rise_e = grade_frac * size_e, rise_n = 0.
    """
    sys.path.insert(0, str(SK10))
    from tin_surface import make_flat_tin
    e0, n0 = origin
    se, sn = size
    tin = make_flat_tin(e0, n0, se, sn, z0, z0 + rise_e, z0 + rise_n, z0 + rise_e + rise_n,
                        surface=name)
    path = tmp / f"{name}.surface-1.json"
    path.write_text(json.dumps(tin))
    return path


def write_landxml(tmp, tin_path, index=1, extra_surfaces=None):
    surfaces = [{"name": "FG", "index": index, "tin_file": tin_path.name,
                 "tin_sha256": hashlib.sha256(tin_path.read_bytes()).hexdigest()}]
    for s in extra_surfaces or []:
        surfaces.append(s)
    p = tmp / "design.xml.json"
    p.write_text(json.dumps({
        "source": {"path": "design.xml", "sha256": "a" * 64, "file_type": "xml"},
        "content": {"alignments": [], "profiles": [], "surfaces": surfaces}}))
    return p


def rules_d(tmp, *, rod043_params=None, rod043_state="active"):
    def R(rid, params, phase="DD", state="active", carriers=None):
        return {"rule_id": rid, "title": f"Test {rid}", "state": state, "cap": "Confirmed",
                "statement": f"Synthetic {rid}.", "inputs": carriers or ["landxml_surface"],
                "parameters": {k: {"value": v, "tag": "AGREED", "source_id": "SRC-T", "clause": "§T",
                                  "unit": u, "cap": "Confirmed", "tolerance": {"value": 0}}
                              for k, (v, u) in params.items()},
                "severity_default": "Minor", "issue_type": "design_error", "check_type": "value",
                "default_status": {"dwg_vector": "Confirmed", "script_output": "Confirmed",
                                   "landxml": "Confirmed"},
                "owner_role": "DMR", "applicability": {"street_class": ["all"]},
                "required_from_phase": phase}

    rs = [
        R("ROD-056", {"width": (2.0, "m"), "length": (10.0, "m"), "gradient": (2.0, "%")},
          carriers=["plan_geometry", "landxml_surface"]),
        R("ROD-062", {"ratio": (0.04, None), "slope": (2.0, "%")}, carriers=["plan_geometry", "landxml_surface"]),
    ]
    # ROD-043: default active in synthetic harness with NO numeric params → GAP not_run
    p043 = dict(rod043_params or {})
    rs.append(R("ROD-043", p043, state=rod043_state,
                carriers=["drainage_plan", "landxml_surface"]))
    path = tmp / "rules_RDE.json"
    path.write_text(json.dumps({
        "agent": "RDE", "rulebook_version": "v7-test", "rules": rs, "rules_skipped": [],
        "stage_map": {"30% Concept Design": {"phase": "PD", "milestone": 30},
                      "60%": {"phase": "DD", "milestone": 60}}}))
    return path


def write_net(tmp, extra=None):
    net = {"roads": {}, "junctions": [], "roundabouts": []}
    if extra:
        net.update(extra)
    p = tmp / "network.json"
    p.write_text(json.dumps(net))
    return p


def _surface_args(tmp, tin_path, net_extra, rules_kwargs=None):
    write_net(tmp, net_extra)
    rules = rules_d(tmp, **(rules_kwargs or {}))
    lx = write_landxml(tmp, tin_path)
    return rules, lx


# ------------------------------------------------------------------ TD1
def test_td1_refuge_gradient_pass_and_fail(tmp_path):
    # 1 % grade: rise 0.1 m over 10 m E → pass; 3 %: rise 0.3 m → listed
    refuge = {"id": "RF1", "handle": "H1", "polygon": box(660000, 2750000, 660010, 2750012)}
    tin_ok = write_tin(tmp_path, "ok", (659995, 2749995), (20, 20), rise_e=0.1)  # 0.1/10 ≈ 1% on 10m
    # Actually slope is over the face diagonal of the TIN rectangle of size 20 → rise_e/20 = 0.5%
    # Rebuild: size 10 m in E so rise_e=0.1 → 1 %; rise_e=0.3 → 3 %
    tin_ok = write_tin(tmp_path, "ok1", (659995, 2749995), (10, 20), rise_e=0.1)
    rules, lx = _surface_args(tmp_path, tin_ok, {"c3_refuges": [refuge]})
    rc, out = run("check_surfaces.py", "--network", tmp_path / "network.json", "--rules", rules,
                  "--stage", "60%", "--site", "NRJS", "--landxml", lx,
                  "--out", tmp_path / "s1.json")
    assert rc == 0, out
    s1 = load(tmp_path / "s1.json")
    assert any(c["rule_id"] == "ROD-056" and c["parameter"] == "gradient" and c["result"] == "pass"
               and c["observed"] <= 2.0 for c in s1["passed"])

    tin_bad = write_tin(tmp_path, "bad", (659995, 2749995), (10, 20), rise_e=0.3)
    lx2 = write_landxml(tmp_path, tin_bad)
    # overwrite network already there
    rc, out = run("check_surfaces.py", "--network", tmp_path / "network.json", "--rules", rules,
                  "--stage", "60%", "--site", "NRJS", "--landxml", lx2,
                  "--out", tmp_path / "s2.json")
    assert rc == 0, out
    s2 = load(tmp_path / "s2.json")
    fails = [c for c in s2["checks"] if c["rule_id"] == "ROD-056" and c["parameter"] == "gradient"]
    assert len(fails) == 1 and fails[0]["result"] == "fail" and fails[0]["observed"] > 2.0


# ------------------------------------------------------------------ TD2
def test_td2_refuge_no_surface_not_run(tmp_path):
    write_net(tmp_path, {"c3_refuges": [{"id": "RF1", "polygon": box(0, 0, 10, 12)}]})
    rules = rules_d(tmp_path)
    rc, out = run("check_surfaces.py", "--network", tmp_path / "network.json", "--rules", rules,
                  "--stage", "60%", "--site", "NRJS", "--out", tmp_path / "s.json")
    assert rc == 0, out
    s = load(tmp_path / "s.json")
    assert any(n.get("rule_id") == "ROD-056" and n.get("parameter") == "gradient"
               and "no design surface" in (n.get("reason") or "") for n in s["not_run"])
    assert not any(c["rule_id"] == "ROD-056" and c["parameter"] == "gradient"
                   for c in s["checks"] + s["passed"])


# ------------------------------------------------------------------ TD3
def test_td3_refuge_uncovered_not_run(tmp_path):
    # TIN far from refuge
    tin = write_tin(tmp_path, "far", (700000, 2800000), (10, 10), rise_e=0.1)
    rules, lx = _surface_args(tmp_path, tin, {
        "c3_refuges": [{"id": "RF1", "polygon": box(660000, 2750000, 660010, 2750012)}]})
    rc, out = run("check_surfaces.py", "--network", tmp_path / "network.json", "--rules", rules,
                  "--stage", "60%", "--site", "NRJS", "--landxml", lx,
                  "--out", tmp_path / "s.json")
    assert rc == 0, out
    s = load(tmp_path / "s.json")
    assert any(n.get("rule_id") == "ROD-056" and "does not cover" in (n.get("reason") or "")
               for n in s["not_run"])


# ------------------------------------------------------------------ TD4
def test_td4_bay_slope_aisle_steep_ignored(tmp_path):
    # Bay polygon over flat 1 % TIN region; aisle-only steep faces outside bay → ignored
    bay = {"id": "AB1", "handle": "B1", "polygon": box(660000, 2750000, 660005, 2750006)}
    # TIN covers bay at 1 % and extends east with a steep patch outside bay
    # Build custom TIN: west quad flat-ish 1%, east steep 10%
    tin = {
        "template": "landxml-surface-1.0", "surface": "FG", "index": 1,
        "points": [
            ["1", 660000.0, 2750000.0, 10.0],
            ["2", 660005.0, 2750000.0, 10.05],   # 1 % over 5 m
            ["3", 660000.0, 2750006.0, 10.0],
            ["4", 660005.0, 2750006.0, 10.05],
            ["5", 660015.0, 2750000.0, 11.05],   # steep east (aisle-only)
            ["6", 660015.0, 2750006.0, 11.05],
        ],
        "faces": [["1", "2", "4"], ["1", "4", "3"], ["2", "5", "6"], ["2", "6", "4"]],
        "breaklines": [],
    }
    tin_path = tmp_path / "bay.surface-1.json"
    tin_path.write_text(json.dumps(tin))
    rules, lx = _surface_args(tmp_path, tin_path, {"c3_accessible_bays": [bay]})
    rc, out = run("check_surfaces.py", "--network", tmp_path / "network.json", "--rules", rules,
                  "--stage", "60%", "--site", "NRJS", "--landxml", lx,
                  "--out", tmp_path / "s.json")
    assert rc == 0, out
    s = load(tmp_path / "s.json")
    passes = [c for c in s["passed"] if c["rule_id"] == "ROD-062" and c["parameter"] == "slope"]
    assert len(passes) == 1 and passes[0]["observed"] <= 2.0 + 1e-6

    # Steep bay → fail
    tin_bad = write_tin(tmp_path, "steep", (659995, 2749995), (10, 20), rise_e=0.3)
    lx2 = write_landxml(tmp_path, tin_bad)
    rc, out = run("check_surfaces.py", "--network", tmp_path / "network.json", "--rules", rules,
                  "--stage", "60%", "--site", "NRJS", "--landxml", lx2,
                  "--out", tmp_path / "s2.json")
    assert rc == 0, out
    s2 = load(tmp_path / "s2.json")
    assert any(c["rule_id"] == "ROD-062" and c["parameter"] == "slope" and c["result"] == "fail"
               for c in s2["checks"])


# ------------------------------------------------------------------ TD5
def test_td5_rod043_crossfall_gap_and_optional_fixture(tmp_path):
    tin = write_tin(tmp_path, "cf", (659995, 2749995), (10, 20), rise_e=0.1)
    fp = {"id": "RA1", "polygon": box(660000, 2750000, 660010, 2750012)}
    # Without params → GAP not_run (rules_v7 not deposited)
    rules, lx = _surface_args(tmp_path, tin, {"c3_crossfall_footprints": [fp]})
    rc, out = run("check_surfaces.py", "--network", tmp_path / "network.json", "--rules", rules,
                  "--stage", "60%", "--site", "NRJS", "--landxml", lx,
                  "--out", tmp_path / "gap.json")
    assert rc == 0, out
    gap = load(tmp_path / "gap.json")
    assert any(n.get("rule_id") == "ROD-043" and n.get("parameter") == "crossfall"
               and "GAP" in (n.get("reason") or "") for n in gap["not_run"])
    assert any(n.get("parameter") == "apron" for n in gap["not_run"])

    # TEST-ONLY fixture params (NOT rules_v7) — prove comparison when numbers exist
    rules2 = rules_d(tmp_path, rod043_params={"crossfall": (2.0, "%")})
    # rename rules file written into same tmp — rules_d overwrites rules_RDE.json
    rc, out = run("check_surfaces.py", "--network", tmp_path / "network.json", "--rules", rules2,
                  "--stage", "60%", "--site", "NRJS", "--landxml", lx,
                  "--out", tmp_path / "ok.json")
    assert rc == 0, out
    ok = load(tmp_path / "ok.json")
    assert any(c["rule_id"] == "ROD-043" and c["parameter"] == "crossfall" and c["result"] == "pass"
               for c in ok["passed"])

    tin_hi = write_tin(tmp_path, "cfhi", (659995, 2749995), (10, 20), rise_e=0.3)
    lx_hi = write_landxml(tmp_path, tin_hi)
    rc, out = run("check_surfaces.py", "--network", tmp_path / "network.json", "--rules", rules2,
                  "--stage", "60%", "--site", "NRJS", "--landxml", lx_hi,
                  "--out", tmp_path / "fail.json")
    assert rc == 0, out
    fail = load(tmp_path / "fail.json")
    assert any(c["rule_id"] == "ROD-043" and c["parameter"] == "crossfall" and c["result"] == "fail"
               for c in fail["checks"])


# ------------------------------------------------------------------ TD6
def test_td6_rod043_drainage_gap_and_missing_plan(tmp_path):
    tin = write_tin(tmp_path, "dr", (659995, 2749995), (10, 20), rise_e=0.05)
    rules, lx = _surface_args(tmp_path, tin, {})
    rc, out = run("check_surfaces.py", "--network", tmp_path / "network.json", "--rules", rules,
                  "--stage", "60%", "--site", "NRJS", "--landxml", lx,
                  "--out", tmp_path / "gap.json")
    assert rc == 0, out
    gap = load(tmp_path / "gap.json")
    assert any(n.get("rule_id") == "ROD-043" and n.get("parameter") == "drainage"
               and "GAP" in (n.get("reason") or "") for n in gap["not_run"])

    # TEST-ONLY presence param + missing plan → not_run MI
    rules2 = rules_d(tmp_path, rod043_params={"drainage_plan": (True, None)})
    rc, out = run("check_surfaces.py", "--network", tmp_path / "network.json", "--rules", rules2,
                  "--stage", "60%", "--site", "NRJS", "--landxml", lx,
                  "--out", tmp_path / "mi.json")
    assert rc == 0, out
    mi = load(tmp_path / "mi.json")
    assert any(n.get("rule_id") == "ROD-043" and "drainage" in (n.get("parameter") or "")
               and "missing" in (n.get("reason") or "").lower() for n in mi["not_run"])

    # With plan → pass
    plan = tmp_path / "drain.dwg.json"
    plan.write_text(json.dumps({
        "source": {"path": "d.dwg", "sha256": "b" * 64, "file_type": "dwg"},
        "content": {"texts": [], "polylines": [], "layers": []}}))
    rc, out = run("check_surfaces.py", "--network", tmp_path / "network.json", "--rules", rules2,
                  "--stage", "60%", "--site", "NRJS", "--landxml", lx,
                  "--file", f"drainage_plan={plan}", "--out", tmp_path / "ok.json")
    assert rc == 0, out
    ok = load(tmp_path / "ok.json")
    assert any(c["rule_id"] == "ROD-043" and c["result"] == "pass" for c in ok["passed"])


# ------------------------------------------------------------------ TD7
def test_td7_pd_no_false_d_findings(tmp_path):
    """Real 30 % expectation: D surface params not_run / no element — no false fails."""
    write_net(tmp_path, {})  # no refuges / bays
    rules = rules_d(tmp_path)
    rc, out = run("check_surfaces.py", "--network", tmp_path / "network.json", "--rules", rules,
                  "--stage", "30% Concept Design", "--site", "QRWN",
                  "--out", tmp_path / "pd.json")
    assert rc == 0, out
    pd = load(tmp_path / "pd.json")
    assert not any(c.get("result") == "fail" for c in pd["checks"])
    assert any(n.get("rule_id") == "ROD-056" for n in pd["not_run"])
    assert any(n.get("rule_id") == "ROD-062" for n in pd["not_run"])


# ------------------------------------------------------------------ TD8
def test_td8_determinism(tmp_path):
    refuge = {"id": "RF1", "polygon": box(660000, 2750000, 660010, 2750012)}
    tin = write_tin(tmp_path, "det", (659995, 2749995), (10, 20), rise_e=0.1)
    rules, lx = _surface_args(tmp_path, tin, {"c3_refuges": [refuge]})
    outs = []
    for name in ("a.json", "b.json"):
        rc, out = run("check_surfaces.py", "--network", tmp_path / "network.json", "--rules", rules,
                      "--stage", "60%", "--site", "NRJS", "--landxml", lx,
                      "--out", tmp_path / name)
        assert rc == 0, out
        outs.append(load(tmp_path / name))
    assert json.dumps(outs[0], sort_keys=True) == json.dumps(outs[1], sort_keys=True)


def test_multi_surface_gap_without_index(tmp_path):
    tin1 = write_tin(tmp_path, "s1", (659995, 2749995), (10, 20), rise_e=0.1)
    tin2 = write_tin(tmp_path, "s2", (659995, 2749995), (10, 20), rise_e=0.05)
    # place second next to first with distinct name
    tin2b = tmp_path / "s2.surface-2.json"
    tin2b.write_text(tin2.read_text())
    write_net(tmp_path, {"c3_refuges": [{"id": "RF1", "polygon": box(660000, 2750000, 660010, 2750012)}]})
    rules = rules_d(tmp_path)
    lx = tmp_path / "design.xml.json"
    lx.write_text(json.dumps({
        "source": {"path": "design.xml", "sha256": "a" * 64, "file_type": "xml"},
        "content": {"surfaces": [
            {"name": "A", "index": 1, "tin_file": tin1.name,
             "tin_sha256": hashlib.sha256(tin1.read_bytes()).hexdigest()},
            {"name": "B", "index": 2, "tin_file": tin2b.name,
             "tin_sha256": hashlib.sha256(tin2b.read_bytes()).hexdigest()},
        ]}}))
    rc, out = run("check_surfaces.py", "--network", tmp_path / "network.json", "--rules", rules,
                  "--stage", "60%", "--site", "NRJS", "--landxml", lx,
                  "--out", tmp_path / "ms.json")
    assert rc == 0, out
    ms = load(tmp_path / "ms.json")
    assert any("multiple design surfaces" in (n.get("reason") or "") for n in ms["not_run"])
