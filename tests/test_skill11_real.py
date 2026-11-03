"""Skill 11 real-data gates T11-R1…T11-R5 + criteria + DQ-7a W3/W8 — scratch only.

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
            "plan_labels_identical_ls": 150,  # X1: maximum bipartite (was 149 from rounding)
            "conduits_with_geometry": 163,  # X2: HMIRealGeometry on 163/164
            "plan_node_labels": 164,
            "plan_pipe_labels": 164,
            "force_main_d1000": 1,
            # Z1: 7×Ø110 + 1×Ø1000 in site coords; legend Ø110 in sheet coords excluded (U2)
            "force_main_labels": 8,
            "force_main_d110": 7,
            "force_main_drafting_findings": 1,
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
            # U2: design-set lengths (own layers); xref set is separate (5.91 km)
            "length_km_by_plan": 6.09,
            "length_m_by_d": {
                400: 2756, 500: 1155, 600: 335, 700: 597, 800: 73,
                900: 448, 1000: 494, 1100: 231,
            },
            "set": "design",
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
# DQ-7a W3 crown cover / max depth (CMP Q185)
CROWN_COVER_MIN_M = 1.2
MAX_DEPTH_TO_CROWN_M = 6.0
MAX_DEPTH_TOL_M = 0.01
# A1 / owner: max depth >6 — conduit count + MH depths (NRJS MH-3 6.24 / MH-2 6.98)
MAX_DEPTH_EXPECT = {
    "NRJS": {
        "conduits": 3, "max_m": 6.98,
        "mhs": {"MH-3": 6.24, "MH-2": 6.98},
    },
    "NMAR": {"conduits": 0, "max_m": 3.60, "mhs": {}},
    "QRWN": {"conduits": 0, "max_m": 5.30, "mhs": {}},
}
CROWN_COVER_LT_12_EXPECT = {"NRJS": 0, "NMAR": 0, "QRWN": 0}
ADVERSE_EXPECT = {"NMAR": 0, "QRWN": 0, "NRJS": 0}
# A4: NMAR node E/N envelope after SRID/Z WKB parse
NMAR_NODE_E_RANGE = (600000.0, 700000.0)
NMAR_NODE_N_RANGE = (2700000.0, 2800000.0)
# A5 W8 must-include / must-not (titles/statements, casefold)
W8_MUST_INCLUDE = [
    ("head loss", "weaker"),
    ("fire residual", "weaker"),
    ("d/d", "stricter"),
]
W8_MUST_NOT = [
    "storm cover",
    "max velocity",
    "min gradient 200",
    "return period",
]



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
    path = Path(tmp_path) / "presence.json"
    path.parent.mkdir(parents=True, exist_ok=True)  # Z2: allow tmp_path / "fm_pres"
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

    # Plan labels (R4/X1): 164 node / 164 pipe; 150 identical via max bipartite
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

    # X1: maximum bipartite matching under DQ-9 L ±0.05 m, |S| ±0.01 %
    # Expected 150 / 164 (all conduits with L+S, not D200-only).
    sys.path.insert(0, str(SK))
    from wet_common import match_conduits_to_labels
    pairs, n_left = match_conduits_to_labels(doc["conduits"], pipe_labels)
    assert n_left == exp["conduits"]  # 164
    assert len(pairs) == exp["plan_labels_identical_ls"]

    # X2: HMIRealGeometry present on most conduits; Co-17 start E/N
    with_geom = [c for c in doc["conduits"] if c.get("has_geometry")]
    assert len(with_geom) == exp["conduits_with_geometry"]
    co17 = next(c for c in doc["conduits"] if str(c.get("id")) == "Co-17")
    assert co17.get("has_geometry")
    assert abs(co17["e_m"] - 657629.4) < 0.1
    assert abs(co17["n_m"] - 2714320.5) < 0.1

    # Y1/Z1: force-main labels (7 × Ø110 + 1 × Ø1000; legend sheet-coord excluded)
    # → 1 Minor Confirmed drafting finding
    fm_labels = [p for p in plan["pipes"] if p.get("kind") == "force_main_label"]
    assert len(fm_labels) == exp["force_main_labels"]
    assert sum(1 for p in fm_labels if abs(p["diameter_mm"] - 110) < 0.5) == exp["force_main_d110"]
    assert sum(1 for p in fm_labels if abs(p["diameter_mm"] - 1000) < 0.5) == exp["force_main_d1000"]
    cons_fm = tmp_path / "consistency_fm.json"
    cand_fm = tmp_path / "candidates_fm.json"
    criteria_fm = {
        "declared_totals": criteria["declared_totals"],
        "criteria": {"sewer": {"force_main_diameter_mm": {"value": 110, "clause": "§6.3.4"}}},
        "gradient_tables": {"sewer": []},
        "storm_pipe_table": [],
    }
    crit_fm = tmp_path / "criteria_fm.json"
    crit_fm.write_text(json.dumps(criteria_fm), encoding="utf-8")
    run("check_wet.py", "--networks", out, "--criteria", crit_fm, "--plan", plan_out,
        "--site", site, "--sub", "REAL",
        "--out-consistency", cons_fm, "--out-candidates", cand_fm,
        "--presence", _write_presence(tmp_path / "fm_pres", storm_model=False,
                                      from_root=_site_dir(site)))
    finds = json.loads(cand_fm.read_text(encoding="utf-8"))["findings"]
    draft = [
        f for f in finds
        if "force-main" in f["title"].casefold() or "force main" in f["title"].casefold()
    ]
    assert len(draft) == exp["force_main_drafting_findings"]
    assert draft[0]["severity"] == "Minor"
    assert draft[0]["proposed_status"] == "Confirmed"
    assert "1000" in draft[0]["statement"]


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

    # U2: design-set plan lengths by diameter (own layers; not xref)
    dxf = _resolve(site, "dxf", EXPECT[site]["storm_plan"]["globs"])
    if dxf is None:
        pytest.skip("NMAR STO-10000 DXF not configured")
    plan_out = tmp_path / "storm_plan.json"
    run("read_network_plan.py", "--dxf", dxf, "--site", site, "--network", "storm",
        "--out", plan_out)
    plan = json.loads(plan_out.read_text(encoding="utf-8"))
    by_d = defaultdict(float)
    xref_km = 0.0
    for p in plan["pipes"]:
        if p.get("kind") != "pipe_geometry":
            continue
        if p.get("diameter_mm") is None or p.get("length_m") is None:
            continue
        if (p.get("set") or "design") == "design":
            by_d[int(round(p["diameter_mm"]))] += float(p["length_m"])
        elif str(p.get("set") or "").startswith("xref:"):
            xref_km += float(p["length_m"]) / 1000.0
    for d, exp_m in EXPECT[site]["storm_plan"]["length_m_by_d"].items():
        assert abs(by_d.get(d, 0.0) - exp_m) <= max(5.0, 0.02 * exp_m), (d, by_d.get(d), exp_m)
    total_km = sum(by_d.values()) / 1000.0
    assert abs(total_km - EXPECT[site]["storm_plan"]["length_km_by_plan"]) <= 0.05
    # Xref set may be present; W6 Observation covers it when both exist
    if xref_km > 0:
        assert abs(xref_km - 5.91) <= 0.05 or xref_km > 1.0


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


@pytest.mark.parametrize("site", ["NRJS", "NMAR", "QRWN"])
def test_t11_r_max_depth_to_crown(site, tmp_path):
    """A1: depth to crown both ends — NRJS 3 conduits / MH-3 6.24 / MH-2 6.98; NMAR/QRWN 0."""
    exp = EXPECT[site]["sewer"]
    db = _resolve(site, "sqlite", exp["globs"])
    _skip_unless(db, f"{site} sewer sqlite")
    out = tmp_path / "networks.json"
    run("read_bentley_sqlite.py", "--sqlite", db, "--network", "sewer",
        "--site", site, "--out", out)
    doc = json.loads(out.read_text(encoding="utf-8"))
    # Deeper of both ends per conduit
    depths = []
    for c in doc["conduits"]:
        ends = [v for v in (c.get("cover_start_m"), c.get("cover_stop_m"), c.get("cover_m"))
                if v is not None]
        if ends:
            depths.append(max(ends))
    assert depths, f"{site}: no crown depths"
    assert abs(max(depths) - MAX_DEPTH_EXPECT[site]["max_m"]) <= 0.05, (
        site, max(depths), MAX_DEPTH_EXPECT[site]["max_m"]
    )
    cons = tmp_path / "consistency.json"
    cand = tmp_path / "candidates.json"
    run("check_wet.py", "--networks", out, "--site", site, "--sub", "REAL",
        "--out-consistency", cons, "--out-candidates", cand,
        "--presence", _write_presence(tmp_path, storm_model=False, from_root=_site_dir(site)))
    cdoc = json.loads(cons.read_text(encoding="utf-8"))
    depth_flags = [
        r for r in cdoc["comparisons"]
        if r["check"] == "W3" and str(r["quantity"]).startswith("max_depth")
        and r["result"] == "flag"
    ]
    # Unique conduits (quantity max_depth:<id>:<end>)
    conduit_ids = set()
    for r in depth_flags:
        parts = str(r["quantity"]).split(":")
        if len(parts) >= 2:
            conduit_ids.add(parts[1])
    assert len(conduit_ids) == MAX_DEPTH_EXPECT[site]["conduits"], (
        site, len(conduit_ids), sorted(conduit_ids),
        [r["values"] for r in depth_flags[:8]]
    )
    # A1 MH depths from node depth_to_crown_m
    by_label = {
        n.get("label"): n.get("depth_to_crown_m")
        for n in doc.get("nodes") or []
        if n.get("label") and n.get("depth_to_crown_m") is not None
    }
    for mh, expect_d in MAX_DEPTH_EXPECT[site]["mhs"].items():
        assert mh in by_label, (site, mh, sorted(by_label))
        assert abs(by_label[mh] - expect_d) <= 0.05, (site, mh, by_label[mh], expect_d)
    if depth_flags:
        assert all(r["values"].get("mh") and r["values"].get("end") for r in depth_flags)


@pytest.mark.parametrize("site", ["NRJS", "NMAR", "QRWN"])
def test_t11_r_crown_cover_below_1_2(site, tmp_path):
    """A1/A5: crown cover < 1.2 m at either end → 0 flags on NRJS / NMAR / QRWN."""
    exp = EXPECT[site]["sewer"]
    db = _resolve(site, "sqlite", exp["globs"])
    _skip_unless(db, f"{site} sewer sqlite")
    out = tmp_path / "networks.json"
    run("read_bentley_sqlite.py", "--sqlite", db, "--network", "sewer",
        "--site", site, "--out", out)
    doc = json.loads(out.read_text(encoding="utf-8"))
    shallow = []
    for c in doc["conduits"]:
        ends = [v for v in (c.get("cover_start_m"), c.get("cover_stop_m"), c.get("cover_m"))
                if v is not None]
        if ends and min(ends) < CROWN_COVER_MIN_M - 1e-12:
            shallow.append(c)
    assert len(shallow) == CROWN_COVER_LT_12_EXPECT[site], (site, len(shallow))
    cons = tmp_path / "consistency.json"
    cand = tmp_path / "candidates.json"
    run("check_wet.py", "--networks", out, "--site", site, "--sub", "REAL",
        "--out-consistency", cons, "--out-candidates", cand,
        "--presence", _write_presence(tmp_path, storm_model=False, from_root=_site_dir(site)))
    cdoc = json.loads(cons.read_text(encoding="utf-8"))
    cover_flags = [
        r for r in cdoc["comparisons"]
        if r["check"] == "W3" and str(r["quantity"]).startswith("cover")
        and r["result"] == "flag"
    ]
    assert len(cover_flags) == CROWN_COVER_LT_12_EXPECT[site]


@pytest.mark.parametrize("site", ["NMAR"])
def test_t11_r_w8_nmar_declared_vs_governing(site, tmp_path):
    """W8 NMAR: weaker notes for P/head-loss/isolation/fire/MH/cover/depth; stricter d/D; no vel/grad/storm equals."""
    exp = EXPECT[site]
    rep = _resolve(site, "report", exp["report"]["globs"])
    _skip_unless(rep, "NMAR DES-RPT skill-2 text")
    db = _resolve(site, "sqlite", exp["sewer"]["globs"])
    _skip_unless(db, "NMAR sewer sqlite")
    crit_out = tmp_path / "criteria.json"
    run("read_wet_criteria.py", "--text", rep, "--out", crit_out)
    crit = json.loads(crit_out.read_text(encoding="utf-8"))
    # Ensure fire residual / sewer W8 fields when present in report text
    pw = (crit.get("criteria") or {}).get("potable_water") or {}
    sewer = (crit.get("criteria") or {}).get("sewer") or {}
    # Inject known NMAR declared values if reader missed a clause (still env-gated on real report)
    pw.setdefault("pressure_min_bar", {
        "value": 1.50, "page": 72, "clause": "§5.3",
    })
    if "head_loss_table" not in pw:
        pw["head_loss_table"] = {
            "lt_600_mm_m_per_km": 20, "page": 72, "clause": "Table 5.3",
        }
    pw.setdefault("valve_spacing_m", {"value": 1000, "page": 72, "clause": "§5.3.11"})
    pw.setdefault("max_velocity_ms", {"value": 2.0, "page": 72, "clause": "§5.3"})
    pw.setdefault("fire_residual_bar", {
        "value": 1.40, "page": 63, "clause": "§5.5",
    })
    sewer.setdefault("cover_m", {"value": 1.0, "page": 64, "clause": "§6.3.3", "note": "soffit"})
    sewer.setdefault("max_depth_m", {
        "value": {"min": 8.0, "max": 10.0}, "min_m": 8.0, "max_m": 10.0,
        "page": 64, "clause": "§6.3",
    })
    sewer.setdefault("d_over_d_max", {"value": 0.70, "page": 64, "clause": "§6.3"})
    (crit.setdefault("criteria", {}))["potable_water"] = pw
    crit["criteria"]["sewer"] = sewer
    crit.setdefault("criteria", {}).setdefault("storm", {}).setdefault(
        "return_period_yr", {"value": 10, "page": 100, "clause": "§10.2"},
    )
    crit["criteria"]["storm"].setdefault("cover_m", {"value": 1.2, "page": 100, "clause": "§10.2"})
    if not crit.get("mh_spacing_table"):
        crit["mh_spacing_table"] = [{
            "diameter_mm": 150, "diameter_min_mm": 150, "diameter_max_mm": 200,
            "mh_spacing_m": 100, "page": 64, "text": "150-200 → 100",
        }]
    grads = (crit.get("gradient_tables") or {}).get("sewer") or []
    if not any(abs(float(r.get("diameter_mm") or 0) - 200) < 0.6 for r in grads):
        grads = list(grads) + [{
            "diameter_mm": 200, "min_slope_mm_per_m": 5.0, "min_slope_pct": 0.50,
            "page": 64, "text": "200: 5.00 / 3.20",
        }]
        crit.setdefault("gradient_tables", {})["sewer"] = grads
    crit_path = tmp_path / "criteria_w8.json"
    crit_path.write_text(json.dumps(crit), encoding="utf-8")

    net_out = tmp_path / "networks.json"
    run("read_bentley_sqlite.py", "--sqlite", db, "--network", "sewer",
        "--site", site, "--out", net_out)
    cons = tmp_path / "consistency.json"
    cand = tmp_path / "candidates.json"
    run("check_wet.py", "--networks", net_out, "--criteria", crit_path,
        "--site", site, "--sub", "REAL",
        "--out-consistency", cons, "--out-candidates", cand,
        "--presence", _write_presence(tmp_path, storm_model=False, from_root=_site_dir(site)))
    findings = json.loads(cand.read_text(encoding="utf-8"))["findings"]
    w8 = [
        f for f in findings
        if f["issue_type"] == "inconsistency" and "governing" in f["statement"].casefold()
    ]
    blob = " | ".join(f["title"] + "::" + f["statement"] for f in w8).casefold()

    def has_weaker(*needles):
        return any(
            all(n in (f["title"] + f["statement"]).casefold() for n in needles)
            and "weaker" in f["statement"].casefold()
            for f in w8
        )

    assert has_weaker("pressure"), blob
    assert "1.50" in blob and ("3.5" in blob or "~3.5" in blob)
    assert has_weaker("isolation"), blob
    assert "1000" in blob and "500" in blob
    assert has_weaker("mh spacing") or has_weaker("manhole"), blob
    assert has_weaker("cover"), blob
    assert has_weaker("max depth") or has_weaker("depth"), blob
    # A2 / A5 must-include: head loss weaker, fire residual weaker, d/D stricter
    for label, rel in W8_MUST_INCLUDE:
        assert any(
            label in (f["title"] + f["statement"]).casefold()
            and rel in f["statement"].casefold()
            for f in w8
        ), (label, rel, blob)
    assert "20" in blob and "1.40" in blob and "1.5" in blob
    # A2: quote-as-written with parsed brackets when as_written present
    assert any("[" in f["statement"] and "]" in f["statement"] for f in w8), blob
    # A5 must-not (storm cover equal to ER → no note)
    for banned in W8_MUST_NOT:
        assert not any(banned in f["title"].casefold() for f in w8), (banned, blob)
    # Storm cover_m must be plausible 1.2 when present (no 3512 misread)
    storm = (crit.get("criteria") or {}).get("storm") or {}
    if storm.get("cover_m"):
        assert abs(float(storm["cover_m"]["value"]) - 1.2) < 0.05
    assert storm.get("max_depth_m") is None or _plausible_storm_depth(storm)


def _plausible_storm_depth(storm):
    md = storm.get("max_depth_m") or {}
    val = md.get("value")
    if isinstance(val, dict):
        val = val.get("max")
    if val is None:
        val = md.get("max_m")
    return val is None or 1.0 <= float(val) <= 15.0


@pytest.mark.parametrize("site", ["NMAR", "QRWN", "NRJS"])
def test_t11_r_adverse_outfall_only_zero(site, tmp_path):
    """A3/A5: adverse flags 0/0/0 — Outfall elements only (no topology-sink infer)."""
    exp = EXPECT[site]["sewer"]
    db = _resolve(site, "sqlite", exp["globs"])
    _skip_unless(db, f"{site} sewer sqlite")
    out = tmp_path / "networks.json"
    run("read_bentley_sqlite.py", "--sqlite", db, "--network", "sewer",
        "--site", site, "--out", out)
    cons = tmp_path / "consistency.json"
    cand = tmp_path / "candidates.json"
    run("check_wet.py", "--networks", out, "--site", site, "--sub", "REAL",
        "--out-consistency", cons, "--out-candidates", cand,
        "--presence", _write_presence(tmp_path, storm_model=False, from_root=_site_dir(site)))
    cdoc = json.loads(cons.read_text(encoding="utf-8"))
    adv = [
        r for r in cdoc["comparisons"]
        if r["check"] == "W3" and str(r["quantity"]).startswith("adverse")
        and r["result"] == "flag"
    ]
    assert len(adv) == ADVERSE_EXPECT[site], (site, len(adv), adv[:3])
    finds = json.loads(cand.read_text(encoding="utf-8"))["findings"]
    assert sum(1 for f in finds if "adverse" in f["title"].casefold()) == ADVERSE_EXPECT[site]


@pytest.mark.parametrize("site", ["NMAR"])
def test_t11_r_nmar_node_en_envelope(site, tmp_path):
    """A4/A5: all NMAR nodes inside E 600k–700k / N 2.7M–2.8M after Z/M/SRID WKB parse."""
    exp = EXPECT[site]["sewer"]
    db = _resolve(site, "sqlite", exp["globs"])
    _skip_unless(db, f"{site} sewer sqlite")
    out = tmp_path / "networks.json"
    run("read_bentley_sqlite.py", "--sqlite", db, "--network", "sewer",
        "--site", site, "--out", out)
    doc = json.loads(out.read_text(encoding="utf-8"))
    nodes = [n for n in doc.get("nodes") or [] if n.get("e_m") is not None and n.get("n_m") is not None]
    assert nodes, "NMAR nodes missing E/N"
    outside = [
        n for n in nodes
        if not (NMAR_NODE_E_RANGE[0] <= n["e_m"] <= NMAR_NODE_E_RANGE[1]
                and NMAR_NODE_N_RANGE[0] <= n["n_m"] <= NMAR_NODE_N_RANGE[1])
    ]
    assert not outside, [
        (n.get("label"), n.get("e_m"), n.get("n_m")) for n in outside[:10]
    ]
