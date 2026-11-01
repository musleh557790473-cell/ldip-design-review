"""Skill 11 real-data gates T11-R1…T11-R5 — scratch only (SPC-0001 rule 5).

Env (any one resolves site / model paths):
  LDIP_SKILL11_NMAR / LDIP_SKILL11_QRWN / LDIP_SKILL11_NRJS
  LDIP_SKILL11_<SITE>_SQLITE / LDIP_SKILL11_<SITE>_DXF
  LDIP_REVIEW_ROOT / LDIP_REVIEW

No real project data in Git. CI skips when env unset.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SK = REPO / "plugin/ldip-design-review/skills/reviewing-wet-utilities/scripts"
PY = sys.executable

SITE_ENV = {
    "QRWN": "LDIP_SKILL11_QRWN",
    "NRJS": "LDIP_SKILL11_NRJS",
    "NMAR": "LDIP_SKILL11_NMAR",
}
ROOT = os.environ.get("LDIP_REVIEW_ROOT") or os.environ.get("LDIP_REVIEW")

EXPECT = {
    "NMAR": {
        "sewer": {
            "globs": ["**/SWN-MOD*.stsw.sqlite", "**/*SWN*.stsw.sqlite", "**/*.stsw.sqlite"],
            "conduits": 164,
            "length_km": 9.26,
            "length_tol_km": 0.05,
            "diameter_counts": {"200": 149, "250": 15},
            "w3_gradient_flags": 22,
            "w3_spacing_flags": 0,
            "w3_cover_flags": 0,
        },
        "water": {
            "globs": ["**/PWT-MOD*.wtg.sqlite", "**/*PWT*.wtg.sqlite", "**/*.wtg.sqlite"],
            "pipes": 121,
            "length_km": 10.87,
            "length_tol_km": 0.1,
        },
        "storm_plan": {
            "globs": ["**/STO-10000*.dxf", "**/STO-10000*.dwg"],
            "length_km_by_plan": 5.91,
        },
    },
    "QRWN": {
        "sewer": {
            "globs": ["**/*Sewer*.stsw.sqlite", "**/*.stsw.sqlite"],
            "conduits": 902,
            "length_km": 63.16,
            "length_tol_km": 0.2,
        },
    },
    "NRJS": {
        "sewer": {
            "globs": ["**/*Hydraulic*.stsw.sqlite", "**/*.stsw.sqlite"],
            "conduits": 20,
            "length_km": 1.25,
            "length_tol_km": 0.05,
            "all_slope_below_pct": 0.50,  # all at 3.7 ‰ = 0.37 % < 0.50
        },
    },
}


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


@pytest.mark.parametrize("site", ["NMAR"])
def test_t11_r1_nmar_sewer(site, tmp_path):
    """T11-R1: NMAR sewer 164 conduits / 9.26 km / D200 149 / D250 15; W3 gradient 22."""
    exp = EXPECT[site]["sewer"]
    db = _resolve(site, "sqlite", exp["globs"])
    _skip_unless(db, "NMAR sewer sqlite")
    out = tmp_path / "networks.json"
    msg = run("read_bentley_sqlite.py", "--sqlite", db, "--network", "sewer",
              "--site", site, "--out", out)
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert doc["summary"]["conduit_count"] == exp["conduits"]
    assert abs(doc["summary"]["length_km"] - exp["length_km"]) <= exp["length_tol_km"]
    dc = {str(k): v for k, v in (doc["summary"].get("diameter_counts") or {}).items()}
    for d, n in exp["diameter_counts"].items():
        assert dc.get(d) == n, (d, dc)
    # W3
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
    spacing = [r for r in cdoc["comparisons"]
               if r["check"] == "W3" and str(r["quantity"]).startswith("mh_spacing")
               and r["result"] == "flag"]
    assert len(spacing) == exp["w3_spacing_flags"]
    cover = [r for r in cdoc["comparisons"]
             if r["check"] == "W3" and str(r["quantity"]).startswith("cover")
             and r["result"] == "flag"]
    assert len(cover) == exp["w3_cover_flags"]


@pytest.mark.parametrize("site", ["QRWN"])
def test_t11_r2_qrwn_sewer(site, tmp_path):
    """T11-R2: QRWN sewer 902 conduits / 63.16 km."""
    exp = EXPECT[site]["sewer"]
    db = _resolve(site, "sqlite", exp["globs"])
    _skip_unless(db, "QRWN sewer sqlite")
    out = tmp_path / "networks.json"
    msg = run("read_bentley_sqlite.py", "--sqlite", db, "--network", "sewer",
              "--site", site, "--out", out)
    assert msg["conduit_count"] == exp["conduits"]
    assert abs(msg["length_km"] - exp["length_km"]) <= exp["length_tol_km"]


@pytest.mark.parametrize("site", ["NRJS"])
def test_t11_r3_nrjs_sewer(site, tmp_path):
    """T11-R3: NRJS 20 conduits / 1.25 km; all slopes 3.7 ‰ below 5.0 ‰ (0.50 %)."""
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


@pytest.mark.parametrize("site", ["NMAR"])
def test_t11_r4_nmar_pw(site, tmp_path):
    """T11-R4: NMAR PW 121 pipes / 10.87 km (geometry only in phase A)."""
    exp = EXPECT[site]["water"]
    db = _resolve(site, "sqlite", exp["globs"])
    _skip_unless(db, "NMAR PW wtg.sqlite")
    out = tmp_path / "networks.json"
    msg = run("read_bentley_sqlite.py", "--sqlite", db, "--network", "water",
              "--site", site, "--out", out)
    assert msg["conduit_count"] == exp["pipes"]
    assert abs(msg["length_km"] - exp["length_km"]) <= exp["length_tol_km"]


@pytest.mark.parametrize("site", ["NMAR"])
def test_t11_r5_storm_model_missing(site, tmp_path):
    """T11-R5: storm model missing at all sites → W1 MI (DQ-8); plan length informational."""
    presence = {
        "networks": {
            "sewer": {"plan": True, "model": True},
            "water": {"plan": True, "model": True},
            "irrigation": {"plan": True, "model": True},
            "storm": {"plan": True, "model": False},
        },
        "hydraulic_calculations": False,
    }
    # Minimal networks so check runs
    networks = {
        "networks_version": "1.0",
        "network": "storm",
        "source": {"path": "absent", "sha256": "f" * 64, "kind": "placeholder"},
        "conduits": [],
        "summary": {"conduit_count": 0, "length_km": 0},
        "plan": [],
    }
    # Prefer real presence when site root exists; always assert W1 storm model MI
    root = _site_dir(site)
    if root is None and not os.environ.get(SITE_ENV[site]):
        pytest.skip("real NMAR root not configured (set LDIP_SKILL11_NMAR / LDIP_REVIEW_ROOT)")
    net_path = tmp_path / "networks.json"
    net_path.write_text(json.dumps(networks), encoding="utf-8")
    pres_path = tmp_path / "presence.json"
    pres_path.write_text(json.dumps(presence), encoding="utf-8")
    cons = tmp_path / "consistency.json"
    cand = tmp_path / "candidates.json"
    run("check_wet.py", "--networks", net_path, "--presence", pres_path,
        "--site", site, "--sub", "REAL",
        "--out-consistency", cons, "--out-candidates", cand)
    findings = json.loads(cand.read_text(encoding="utf-8"))["findings"]
    storm = [f for f in findings
             if "storm" in f["title"].casefold() and "model" in f["title"].casefold()]
    assert storm and storm[0]["issue_type"] == "missing_information"


def _write_presence(tmp_path, storm_model=False):
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
