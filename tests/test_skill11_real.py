"""Skill 11 real-data gates T11-R1…T11-R5 + criteria (R5 / R7) — scratch only.

Env (one directory per site holding models, DXFs and report text):
  LDIP_SKILL11_NMAR / LDIP_SKILL11_QRWN / LDIP_SKILL11_NRJS
  LDIP_SKILL11_<SITE>_SQLITE / LDIP_SKILL11_<SITE>_DXF / LDIP_SKILL11_<SITE>_REPORT
  LDIP_REVIEW_ROOT / LDIP_REVIEW

No real project data in Git. CI skips when env unset.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SK = REPO / "plugin/ldip-design-review/skills/reviewing-wet-utilities/scripts"
REF = REPO / "plugin/ldip-design-review/skills/reviewing-wet-utilities/references"
PY = sys.executable

SITE_ENV = {
    "QRWN": "LDIP_SKILL11_QRWN",
    "NRJS": "LDIP_SKILL11_NRJS",
    "NMAR": "LDIP_SKILL11_NMAR",
}
ROOT = os.environ.get("LDIP_REVIEW_ROOT") or os.environ.get("LDIP_REVIEW")

# Fixed numbers from S35-PR28-skill11-review.md R5 / R4 / R7
EXPECT = {
    "NMAR": {
        "sewer": {
            "globs": ["**/SWN-MOD*.stsw.sqlite", "**/*SWN*.stsw.sqlite", "**/*.stsw.sqlite"],
            "conduits": 164,
            "length_km": 9.26,
            "length_tol_km": 0.05,
            "diameter_counts": {"200": 149, "250": 15},
            "w3_gradient_flags": 22,
            "w3_spacing_flags": 38,  # ER MH spacing max 80 m
            "w3_cover_flags": 0,
            "links_over_80_m": 38,
            "report_km": 9.70,
            "report_mh": 173,
            "report_depth": (1.40, 3.50),
            "model_depth": (1.70, 3.80),
            "plan_labels_identical_ls": 149,
            "plan_node_labels": 164,
            "plan_pipe_labels": 164,
            "force_main_d1000": 1,
            "plan_globs": ["**/SWN-10000*.dxf", "**/SWN-10000*.dwg"],
        },
        "water": {
            "globs": ["**/PWT-MOD*.wtg.sqlite", "**/*PWT*.wtg.sqlite", "**/*.wtg.sqlite"],
            "pipes": 121,
            "length_km": 10.87,
            "length_tol_km": 0.1,
            "inside_diameters_mm": [131, 164, 205],  # S4: rounded 130.8 / 163.6 / 204.6
            "hazen_williams_c": 140,
            "plan_globs": ["**/PWT-10000*.dxf", "**/PWT-10000*.dwg"],
        },
        "storm_plan": {
            "globs": ["**/STO-10000*.dxf", "**/STO-10000*.dwg"],
            "length_km_by_plan": 5.91,
            "length_m_by_d": {
                400: 3269, 500: 1160, 600: 662, 700: 562, 800: 84, 1000: 171,
            },
        },
        "report": {
            "globs": [
                "**/DES-RPT-00001*.pdf.json",
                "**/DES-RPT*.pdf.json",
                "**/*DES-RPT*.json",
                "**/report.pdf.json",
            ],
            "pw_km": 11.0,
            "pw_km_tol": 0.5,
            "pw_pressure_bar": (2.76, 5.27),
            "sewer_km": 9.70,
            "sewer_mh": 173,
            "sewer_depth": (1.40, 3.50),
            "storm_109_cover_m": {
                "P-055": 0.18, "P-118": 0.63, "P-119": 1.15,
            },
            "storm_109_length_m": {
                "P-092": 110.6, "P-088": 100.7,
            },
            "storm_km": 6.14,
            "storm_km_tol": 0.15,
            "declared_clauses": ["§5.3", "§6.3", "Table 6.1", "Table 6.3", "§10.2", "Table 10.9"],
            "pw_criteria": {
                "pressure_bar": (1.50, 6.0),
                # T3: 110 distribution mains / 160 fire-flow
                "distribution_mains_mm": 110,
                "fire_flow_mm": 160,
                "velocity_ms": (0.2, 2.0),
                "hw_c": (140, 130),
                "valve_spacing_m": 1000,
                "cover_m": (1.2, 1.0),
                "separation_m_mm": (3.0, 500),
                "head_loss_m_per_km": (20, 10),
            },
            "sewer_min_diameter_mm": 200,
        },
    },
    "QRWN": {
        "sewer": {
            "globs": ["**/*Sewer*.stsw.sqlite", "**/*.stsw.sqlite"],
            "conduits": 902,
            "length_km": 63.16,
            "length_tol_km": 0.2,
            "w3_gradient_flags": 241,
            "links_over_80_m": 310,  # S1: L > 80 + 0.01
            "plan_globs": ["**/SWN-10000*.dxf", "**/*SWN*10000*.dxf", "**/*.dxf"],
        },
    },
    "NRJS": {
        "sewer": {
            "globs": ["**/*Hydraulic*.stsw.sqlite", "**/*.stsw.sqlite"],
            "conduits": 20,
            "length_km": 1.25,
            "length_tol_km": 0.05,
            "all_slope_below_pct": 0.50,  # all at 3.7 ‰ = 0.37 % < 0.50
            "links_over_80_m": 3,
            "plan_globs": ["**/SWN-10000*.dxf", "**/*SWN*10000*.dxf", "**/*.dxf"],
        },
    },
}

# S1: MH spacing / links-over use w3_threshold_tol (0.01 m)
MH_SPACING_MAX_M = 80.0
MH_SPACING_TOL_M = 0.01



def run(script, *args):
    p = subprocess.run([PY, str(SK / script), *map(str, args)],
                       capture_output=True, text=True, timeout=1800)
    out = p.stdout.strip().splitlines()
    msg = json.loads(out[-1]) if out else {"stderr": p.stderr[-3000:]}
    assert p.returncode == 0, msg
    return msg


def _site_dir(site: str):
    env = SITE_ENV[site]
    p = os.environ.get(env)
    if p and Path(p).is_dir():
        return Path(p)
    if p and Path(p).is_file():
        return Path(p).parent
    if ROOT and Path(ROOT).is_dir():
        for cand in (Path(ROOT) / site, Path(ROOT) / "scratch" / site):
            if cand.is_dir():
                return cand
    return None


def _resolve(site: str, kind: str, globs):
    explicit = os.environ.get(f"{SITE_ENV[site]}_{kind.upper()}")
    if explicit and Path(explicit).is_file():
        return Path(explicit)
    root = _site_dir(site)
    if root is None:
        return None
    for g in globs:
        hits = sorted(root.glob(g))
        if hits:
            return hits[0]
    return None


def _skip_unless(path, label):
    if path is None:
        pytest.skip(f"real {label} not configured (set LDIP_SKILL11_* / LDIP_REVIEW_ROOT)")


def _write_presence(tmp_path, storm_model=False, from_root=None):
    """Build presence.json; prefer a real presence file under the site root when present."""
    if from_root is not None:
        for g in ("**/wet_presence.json", "**/presence.json"):
            hits = sorted(Path(from_root).glob(g))
            if hits:
                return hits[0]
    p = {
        "networks": {
            "sewer": {"plan": True, "model": True},
            "water": {"plan": True, "model": True},
            "irrigation": {"plan": True, "model": True},
            "storm": {"plan": True, "model": storm_model},
        },
        "hydraulic_calculations": False,
    }
    path = tmp_path / "presence.json"
    path.write_text(json.dumps(p), encoding="utf-8")
    return path


def _list_wet_layers(dxf_path):
    """List short layer names that match wet_layers.json (exact or pattern)."""
    import ezdxf
    from ezdxf import recover
    sys.path.insert(0, str(SK))
    from wet_common import layer_short, load_wet_layers, resolve_layer
    layers_map = load_wet_layers(site="NMAR")
    try:
        doc, _ = recover.readfile(str(dxf_path))
    except Exception:
        doc = ezdxf.readfile(str(dxf_path))
    found = []
    for layer in doc.layers:
        name = layer.dxf.name
        short = layer_short(name)
        meta = resolve_layer(name, layers_map)
        if meta:
            found.append({"layer": name, "short": short, "meta": meta})
    return found


@pytest.mark.parametrize("site", ["NMAR"])
def test_t11_r1_nmar_sewer(site, tmp_path):
    """T11-R1: NMAR sewer fixed numbers — 164 / 9.26 km; W3 spacing 38; plan labels 164."""
    exp = EXPECT[site]["sewer"]
    db = _resolve(site, "sqlite", exp["globs"])
    _skip_unless(db, "NMAR sewer sqlite")
    out = tmp_path / "networks.json"
    msg = run("read_bentley_sqlite.py", "--sqlite", db, "--network", "sewer",
              "--site", site, "--out", out)
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert doc["source"]["schema"] == "openflows_conduit"
    assert doc["summary"]["conduit_count"] == exp["conduits"]
    assert abs(doc["summary"]["length_km"] - exp["length_km"]) <= exp["length_tol_km"]
    dc = {str(k): v for k, v in (doc["summary"].get("diameter_counts") or {}).items()}
    for d, n in exp["diameter_counts"].items():
        assert dc.get(d) == n, (d, dc)

    # S1: flag / count when L > 80 + 0.01 m
    over80 = [c for c in doc["conduits"]
              if (c.get("length_m") or 0) > MH_SPACING_MAX_M + MH_SPACING_TOL_M]
    assert len(over80) == exp["links_over_80_m"]

    # Depth span from cover + D
    depths = []
    for c in doc["conduits"]:
        if c.get("cover_m") is not None and c.get("diameter_mm") is not None:
            depths.append(c["cover_m"] + c["diameter_mm"] / 1000.0)
    if depths:
        assert abs(min(depths) - exp["model_depth"][0]) <= 0.15
        assert abs(max(depths) - exp["model_depth"][1]) <= 0.15

    # W3
    cons = tmp_path / "consistency.json"
    cand = tmp_path / "candidates.json"
    criteria = {
        "declared_totals": {
            "total_length_km": {"value": exp["report_km"], "page": 1},
            "manhole_count": {"value": exp["report_mh"], "page": 1},
            "depth_range_m": {
                "min_m": exp["report_depth"][0], "max_m": exp["report_depth"][1], "page": 1,
            },
        },
        "gradient_tables": {"sewer": []},
        "storm_pipe_table": [],
    }
    crit_path = tmp_path / "criteria.json"
    crit_path.write_text(json.dumps(criteria), encoding="utf-8")
    run("check_wet.py", "--networks", out, "--criteria", crit_path,
        "--site", site, "--sub", "REAL",
        "--out-consistency", cons, "--out-candidates", cand,
        "--presence", _write_presence(tmp_path, storm_model=False, from_root=_site_dir(site)))
    cdoc = json.loads(cons.read_text(encoding="utf-8"))
    grad = [r for r in cdoc["comparisons"]
            if r["check"] == "W3" and str(r["quantity"]).startswith("min_slope")
            and r["result"] == "flag"]
    assert len(grad) == exp["w3_gradient_flags"]
    spacing = [r for r in cdoc["comparisons"]
               if r["check"] == "W3" and str(r["quantity"]).startswith("mh_spacing")
               and r["result"] == "flag"]
    assert len(spacing) == exp["w3_spacing_flags"]
    cover = [r for r in cdoc["comparisons"]
             if r["check"] == "W3" and str(r["quantity"]).startswith("cover")
             and r["result"] == "flag"]
    assert len(cover) == exp["w3_cover_flags"]

    # Plan labels (R4): 164 node / 164 pipe; 149 identical L,S vs model D200 set
    dxf = _resolve(site, "dxf", exp["plan_globs"])
    if dxf is None:
        pytest.skip("NMAR SWN-10000 DXF not configured")
    plan_out = tmp_path / "plan.json"
    run("read_network_plan.py", "--dxf", dxf, "--site", site, "--network", "sewer",
        "--out", plan_out)
    plan = json.loads(plan_out.read_text(encoding="utf-8"))
    assert plan["counts"]["nodes"] == exp["plan_node_labels"]
    pipe_labels = [p for p in plan["pipes"] if p.get("kind") == "pipe_label"]
    assert len(pipe_labels) == exp["plan_pipe_labels"]

    # Match plan L/S to model within DQ-9 tol; expect 149 identical
    tol_l, tol_s = 0.05, 0.01
    identical = 0
    for c in doc["conduits"]:
        if c.get("diameter_mm") is None or abs(c["diameter_mm"] - 200) > 1:
            continue
        for p in pipe_labels:
            if p.get("_used"):
                continue
            if p.get("length_m") is None or p.get("slope_pct") is None:
                continue
            if (abs(p["length_m"] - (c.get("length_m") or 0)) <= tol_l
                    and abs(p["slope_pct"] - (c.get("slope_pct") or 0)) <= tol_s):
                identical += 1
                p["_used"] = True
                break
    assert identical == exp["plan_labels_identical_ls"]

    # Force-main Ø1000 = 1 (label or diameter)
    fm = [p for p in pipe_labels
          if (p.get("diameter_mm") and abs(p["diameter_mm"] - 1000) < 1)
          or (p.get("raw") and re.search(r"(?:Ø|⌀|%%c)?\s*1000", str(p["raw"])))]
    # Also accept geometry / model force-main of 1000 mm
    fm_model = [c for c in doc["conduits"]
                if c.get("diameter_mm") and abs(c["diameter_mm"] - 1000) < 1]
    assert len(fm) + len(fm_model) >= exp["force_main_d1000"] or len(fm) == exp["force_main_d1000"]


@pytest.mark.parametrize("site", ["QRWN"])
def test_t11_r2_qrwn_sewer(site, tmp_path):
    """T11-R2: QRWN sewer 902 / 63.16 km; slope below ER 241; links > 80 m = 310."""
    exp = EXPECT[site]["sewer"]
    db = _resolve(site, "sqlite", exp["globs"])
    _skip_unless(db, "QRWN sewer sqlite")
    out = tmp_path / "networks.json"
    msg = run("read_bentley_sqlite.py", "--sqlite", db, "--network", "sewer",
              "--site", site, "--out", out)
    assert msg["conduit_count"] == exp["conduits"]
    assert abs(msg["length_km"] - exp["length_km"]) <= exp["length_tol_km"]
    doc = json.loads(out.read_text(encoding="utf-8"))
    over80 = [c for c in doc["conduits"]
              if (c.get("length_m") or 0) > MH_SPACING_MAX_M + MH_SPACING_TOL_M]
    assert len(over80) == exp["links_over_80_m"]
    cons = tmp_path / "consistency.json"
    cand = tmp_path / "candidates.json"
    run("check_wet.py", "--networks", out, "--site", site, "--sub", "REAL",
        "--out-consistency", cons, "--out-candidates", cand,
        "--presence", _write_presence(tmp_path, storm_model=False))
    cdoc = json.loads(cons.read_text(encoding="utf-8"))
    grad = [r for r in cdoc["comparisons"]
            if r["check"] == "W3" and str(r["quantity"]).startswith("min_slope")
            and r["result"] == "flag"]
    assert len(grad) == exp["w3_gradient_flags"]


@pytest.mark.parametrize("site", ["NRJS"])
def test_t11_r3_nrjs_sewer(site, tmp_path):
    """T11-R3: NRJS 20 / 1.25 km; all slopes < 0.50 %; links > 80 m = 3."""
    exp = EXPECT[site]["sewer"]
    db = _resolve(site, "sqlite", exp["globs"])
    _skip_unless(db, "NRJS sewer sqlite")
    out = tmp_path / "networks.json"
    msg = run("read_bentley_sqlite.py", "--sqlite", db, "--network", "sewer",
              "--site", site, "--out", out)
    assert msg["conduit_count"] == exp["conduits"]
    assert abs(msg["length_km"] - exp["length_km"]) <= exp["length_tol_km"]
    doc = json.loads(out.read_text(encoding="utf-8"))
    slopes = [c["slope_pct"] for c in doc["conduits"] if c.get("slope_pct") is not None]
    assert slopes and all(s < exp["all_slope_below_pct"] - 1e-9 for s in slopes)
    over80 = [c for c in doc["conduits"]
              if (c.get("length_m") or 0) > MH_SPACING_MAX_M + MH_SPACING_TOL_M]
    assert len(over80) == exp["links_over_80_m"]


@pytest.mark.parametrize("site", ["NMAR"])
def test_t11_r4_nmar_pw(site, tmp_path):
    """T11-R4: NMAR PW 121 / 10.87 km; ID set {131, 164, 205} mm (S4); C 140."""
    exp = EXPECT[site]["water"]
    db = _resolve(site, "sqlite", exp["globs"])
    _skip_unless(db, "NMAR PW wtg.sqlite")
    out = tmp_path / "networks.json"
    msg = run("read_bentley_sqlite.py", "--sqlite", db, "--network", "water",
              "--site", site, "--out", out)
    assert msg["conduit_count"] == exp["pipes"]
    assert abs(msg["length_km"] - exp["length_km"]) <= exp["length_tol_km"]
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert doc["source"]["schema"] == "openflows_idaho_pipe"
    diams = {int(round(c["diameter_mm"])) for c in doc["conduits"] if c.get("diameter_mm")}
    expected_ids = set(exp["inside_diameters_mm"])
    assert expected_ids <= diams, (expected_ids, sorted(diams))
    cs = [c["roughness"] for c in doc["conduits"] if c.get("roughness") is not None]
    assert cs and all(abs(v - exp["hazen_williams_c"]) < 1.0 for v in cs)


@pytest.mark.parametrize("site", ["NMAR"])
def test_t11_r5_storm_model_missing_and_report_table(site, tmp_path):
    """T11-R5: real presence (no storm model); Table 10.9 fixed values; plan lengths by D."""
    root = _site_dir(site)
    if root is None and not os.environ.get(SITE_ENV[site]):
        pytest.skip("real NMAR root not configured (set LDIP_SKILL11_NMAR / LDIP_REVIEW_ROOT)")

    # Real presence when available
    presence_path = _write_presence(tmp_path, storm_model=False, from_root=root)
    presence = json.loads(Path(presence_path).read_text(encoding="utf-8"))
    assert presence["networks"]["storm"]["model"] is False

    networks = {
        "networks_version": "1.0",
        "network": "storm",
        "source": {"path": "absent", "sha256": "f" * 64, "kind": "placeholder"},
        "conduits": [],
        "summary": {"conduit_count": 0, "length_km": 0},
        "plan": [],
    }
    net_path = tmp_path / "networks.json"
    net_path.write_text(json.dumps(networks), encoding="utf-8")
    cons = tmp_path / "consistency.json"
    cand = tmp_path / "candidates.json"
    run("check_wet.py", "--networks", net_path, "--presence", presence_path,
        "--site", site, "--sub", "REAL",
        "--out-consistency", cons, "--out-candidates", cand)
    findings = json.loads(cand.read_text(encoding="utf-8"))["findings"]
    storm = [f for f in findings
             if "storm" in f["title"].casefold() and "model" in f["title"].casefold()]
    assert storm and storm[0]["issue_type"] == "missing_information"

    # Storm Table 10.9 from real report (S2): cover_m / length_m columns
    rep = _resolve(site, "report", EXPECT[site]["report"]["globs"])
    if rep is None:
        pytest.skip("NMAR DES-RPT skill-2 text not configured")
    crit_out = tmp_path / "criteria.json"
    run("read_wet_criteria.py", "--text", rep, "--out", crit_out)
    crit = json.loads(crit_out.read_text(encoding="utf-8"))
    by_id = {}
    for row in crit.get("storm_table_109") or []:
        by_id.setdefault(row["id"], row)
    # Unique by label
    ids = [r["id"] for r in crit.get("storm_table_109") or []]
    assert len(ids) == len(set(ids)), "Table 10.9 rows must be unique by label"
    for pid, val in EXPECT[site]["report"]["storm_109_cover_m"].items():
        assert pid in by_id, (pid, sorted(by_id))
        assert abs(by_id[pid]["cover_m"] - val) < 0.05, (pid, by_id[pid], val)
        # Must not confuse invert with cover
        assert by_id[pid].get("invert_start_m") is None or abs(
            by_id[pid]["invert_start_m"] - val) > 1.0
    for pid, val in EXPECT[site]["report"]["storm_109_length_m"].items():
        assert pid in by_id, (pid, sorted(by_id))
        assert abs(by_id[pid]["length_m"] - val) < 0.05, (pid, by_id[pid], val)

    # Plan lengths by diameter (R4)
    dxf = _resolve(site, "dxf", EXPECT[site]["storm_plan"]["globs"])
    if dxf is None:
        pytest.skip("NMAR STO-10000 DXF not configured")
    plan_out = tmp_path / "storm_plan.json"
    run("read_network_plan.py", "--dxf", dxf, "--site", site, "--network", "storm",
        "--out", plan_out)
    plan = json.loads(plan_out.read_text(encoding="utf-8"))
    by_d = defaultdict(float)
    for p in plan["pipes"]:
        if p.get("kind") != "pipe_geometry":
            continue
        if p.get("diameter_mm") is None or p.get("length_m") is None:
            continue
        by_d[int(round(p["diameter_mm"]))] += float(p["length_m"])
    for d, exp_m in EXPECT[site]["storm_plan"]["length_m_by_d"].items():
        assert abs(by_d.get(d, 0.0) - exp_m) <= max(5.0, 0.02 * exp_m), (d, by_d.get(d), exp_m)
    total_km = sum(by_d.values()) / 1000.0
    assert abs(total_km - EXPECT[site]["storm_plan"]["length_km_by_plan"]) <= 0.05


@pytest.mark.parametrize("site", ["NMAR"])
def test_t11_r7_criteria_nmar_report(site, tmp_path):
    """S3 / R7: gated criteria reader — PW §5.3, Tables 6.1/6.3, config totals."""
    exp = EXPECT[site]["report"]
    rep = _resolve(site, "report", exp["globs"])
    _skip_unless(rep, "NMAR DES-RPT-00001 skill-2 text")
    out = tmp_path / "criteria.json"
    msg = run("read_wet_criteria.py", "--text", rep, "--out", out)
    doc = json.loads(out.read_text(encoding="utf-8"))
    clauses = {t["clause"] for t in doc.get("declared_tables") or []}
    for c in exp["declared_clauses"]:
        assert c in clauses, (c, clauses)
    # Totals anchored away from TOC
    sewer_len = (doc.get("declared_totals") or {}).get("sewer:total_length_km") or (
        doc.get("declared_totals") or {}).get("total_length_km")
    assert sewer_len and abs(float(sewer_len["value"]) - exp["sewer_km"]) < 0.05
    mh = (doc.get("declared_totals") or {}).get("sewer:manhole_count") or (
        doc.get("declared_totals") or {}).get("manhole_count")
    assert mh and int(mh["value"]) == exp["sewer_mh"]
    depth = (doc.get("declared_totals") or {}).get("sewer:depth_range_m") or (
        doc.get("declared_totals") or {}).get("depth_range_m")
    assert depth and abs(depth["min_m"] - exp["sewer_depth"][0]) < 0.05
    assert abs(depth["max_m"] - exp["sewer_depth"][1]) < 0.05
    pw = doc.get("criteria", {}).get("potable_water") or {}
    pw_len = (doc.get("declared_totals") or {}).get("potable_water:total_length_km")
    assert pw_len and abs(float(pw_len["value"]) - exp["pw_km"]) <= exp["pw_km_tol"]
    # Residual operating pressures from configuration (§5.5.1)
    pair = pw.get("pressure_pair_bar")
    assert pair, "PW pressure pair not read"
    # Accept either design criteria (1.50/6.0) or residual totals (2.76/5.27)
    assert (
        (abs(pair["min_bar"] - exp["pw_pressure_bar"][0]) < 0.05
         and abs(pair["max_bar"] - exp["pw_pressure_bar"][1]) < 0.05)
        or (abs(pair["min_bar"] - exp["pw_criteria"]["pressure_bar"][0]) < 0.05
            and abs(pair["max_bar"] - exp["pw_criteria"]["pressure_bar"][1]) < 0.05)
    ), pair
    # T3: PW §5.3 criteria present (distribution mains / fire-flow labels)
    pc = exp["pw_criteria"]
    assert pw.get("min_diameter_mm") or pw.get("min_diameter_mm_pair")
    dpair = pw.get("min_diameter_mm_pair") or {}
    if dpair:
        dist = dpair.get("distribution_mains_mm", dpair.get("service_mm"))
        fire = dpair.get("fire_flow_mm", dpair.get("distribution_mm"))
        assert abs(dist - pc["distribution_mains_mm"]) < 0.5
        assert abs(fire - pc["fire_flow_mm"]) < 0.5
    assert pw.get("hw_c") or pw.get("hw_c_pair")
    assert pw.get("valve_spacing_m")
    assert abs(float(pw["valve_spacing_m"]["value"]) - pc["valve_spacing_m"]) < 1.0
    hl = pw.get("head_loss_table") or {}
    assert hl, "Table 5.3 head-loss not read"
    if hl.get("lt_600_mm_m_per_km") is not None:
        assert abs(hl["lt_600_mm_m_per_km"] - pc["head_loss_m_per_km"][0]) < 0.5
        assert abs(hl["d_600_to_1000_m_per_km"] - pc["head_loss_m_per_km"][1]) < 0.5
    # T3: Tables 6.1 / 6.3 have rows; gradients store mm/m
    assert len(doc.get("mh_spacing_table") or []) >= 1, "Table 6.1 empty"
    grads = (doc.get("gradient_tables") or {}).get("sewer") or []
    assert len(grads) >= 1, "Table 6.3 empty"
    assert any(r.get("min_slope_mm_per_m") is not None or r.get("min_slope_pct") is not None
               for r in grads)
    # Gravity min D 200, not house-connection 160
    sewer_min = (doc.get("criteria") or {}).get("sewer", {}).get("min_diameter_mm")
    if sewer_min:
        assert abs(float(sewer_min["value"]) - exp["sewer_min_diameter_mm"]) < 0.5
    # Storm total ≈ 6.14 km
    storm_len = (doc.get("declared_totals") or {}).get("storm:total_length_km")
    if storm_len:
        assert abs(float(storm_len["value"]) - exp["storm_km"]) <= exp["storm_km_tol"]
    by_id = {r["id"]: r for r in doc.get("storm_table_109") or []}
    for pid, val in exp["storm_109_cover_m"].items():
        assert pid in by_id and abs(by_id[pid]["cover_m"] - val) < 0.05
    for pid, val in exp["storm_109_length_m"].items():
        assert pid in by_id and abs(by_id[pid]["length_m"] - val) < 0.05


@pytest.mark.parametrize("site,plan_key", [
    ("QRWN", "sewer"),
    ("NRJS", "sewer"),
    ("NMAR", "water"),
])
def test_t11_real_plan_wet_layers(site, plan_key, tmp_path):
    """R5: list wet layers found; assert reader finds pipes on each plan."""
    exp = EXPECT[site].get(plan_key) or EXPECT[site].get("sewer")
    globs = exp.get("plan_globs") or ["**/*.dxf"]
    dxf = _resolve(site, "dxf", globs)
    _skip_unless(dxf, f"{site} plan DXF")
    found = _list_wet_layers(dxf)
    assert found, f"no wet layers matched on {dxf}"
    # Reader must find at least one pipe geometry or pipe label
    out = tmp_path / "plan.json"
    network = "water" if plan_key == "water" else "sewer"
    run("read_network_plan.py", "--dxf", dxf, "--site", site, "--network", network,
        "--out", out)
    plan = json.loads(out.read_text(encoding="utf-8"))
    pipes = plan.get("pipes") or []
    assert pipes, f"reader found no pipes on {site} plan; wet layers: {[f['short'] for f in found]}"
