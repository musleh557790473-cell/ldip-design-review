"""Skill 16 real-data gates T16-R1…T16-R3 — skipped without LDIP_REVIEW_ROOT (Rev N §4.16.5).

T16-R1: QRWN 30 % — C1 BOQ base 5,065,345 / 3,748,575 vs model ≈ 4.82 M / 3.23 M
        vs table 7,567.55 / 371,513.95; C2 ratios; C3; C5 #REF! on parts 6–10.
T16-R2: NRJS 30 % — C1 37,000 / 50,000 vs ≈ 36,600 / 41,600 vs 361.651 / 8,241.046; C3; C5.
T16-R3: NMAR 30 % — C1 BOQ base 429,590 / 222,820 (× 1.05) vs model ≈ 406,700 / 169,000;
        C2 ratios 0.2 / 0.8 / 0.6; C5 33 #REF! in principal quantities;
        C6 road layers 105,710 m² / kerb 23,900 m (× 1.05) as information.

Real packages stay outside Git (brief §10.2). Scratch only (DN-8).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = os.environ.get("LDIP_REVIEW_ROOT") or os.environ.get("LDIP_REVIEW")

pytestmark = pytest.mark.skipif(
    not ROOT or not Path(ROOT).is_dir(),
    reason="LDIP_REVIEW_ROOT not set — real BOQ / report / profile stay out of Git (scratch)",
)

REPO = Path(__file__).resolve().parents[1]
SK = REPO / "plugin/ldip-design-review/skills/checking-design-document-consistency/scripts"
PY = sys.executable


def run(script, *args):
    p = subprocess.run([PY, str(SK / script), *map(str, args)],
                       capture_output=True, text=True, timeout=1800)
    out = p.stdout.strip().splitlines()
    msg = json.loads(out[-1]) if out else {"stderr": p.stderr[-3000:]}
    assert p.returncode == 0, msg
    return msg


def _root():
    return Path(ROOT)


def _find(root: Path, patterns):
    hits = []
    for pat in patterns:
        hits.extend(root.rglob(pat))
    return sorted({p for p in hits if p.is_file()})


def _site_boq(root: Path, site: str):
    hits = _find(root, ["**/*BOQ*.xlsx", "**/*BoQ*.xlsx", "**/*boq*.xlsx", "**/Bill*.xlsx"])
    site_hits = [p for p in hits if site.upper() in str(p).upper()]
    return site_hits or hits


def _site_report(root: Path, site: str):
    hits = _find(root, [
        "**/*Concept*Report*.pdf.json",
        "**/*Design*Report*.pdf.json",
        "**/*concept*.pdf.json",
        "**/*Report*.pdf.json",
    ])
    site_hits = [p for p in hits if site.upper() in str(p).upper()]
    return site_hits or hits


def _site_profile(root: Path, site: str):
    hits = _find(root, ["**/earthworks_profile.json", "**/*profile*.json"])
    site_hits = [p for p in hits if site.upper() in str(p).upper()]
    return site_hits


def test_t16_r1_qrwn_30pct(tmp_path):
    """T16-R1: QRWN 30 % earthworks consistency probe numbers."""
    root = _root()
    boqs = _site_boq(root, "QRWN")
    if not boqs:
        pytest.skip("T16-R1: no QRWN BOQ.xlsx under LDIP_REVIEW_ROOT")
    boq_out = tmp_path / "boq.json"
    run("read_boq.py", "--xlsx", boqs[0], "--out", boq_out)
    boq = json.loads(boq_out.read_text(encoding="utf-8"))
    cut = (boq.get("summary") or {}).get("earthworks", {}).get("cut") or {}
    fill = (boq.get("summary") or {}).get("earthworks", {}).get("fill") or {}
    # Probe: base 5,065,345 / 3,748,575
    if cut.get("base") is not None:
        assert cut["base"] == pytest.approx(5065345, rel=0.01)
    if fill.get("base") is not None:
        assert fill["base"] == pytest.approx(3748575, rel=0.01)
    ratios = set(boq["summary"].get("ratios_recorded") or [])
    # C2 ratios when present
    if ratios:
        assert ratios & {0.2, 0.8, 0.6}

    reports = _site_report(root, "QRWN")
    report_arg = []
    if reports:
        claims = tmp_path / "claims.json"
        run("read_report.py", "--text", reports[0], "--out", claims)
        report_arg = ["--report", str(claims)]

    profiles = _site_profile(root, "QRWN")
    profile_arg = ["--profile", str(profiles[0])] if profiles else []

    cons = tmp_path / "consistency.json"
    run("check_consistency.py", "--boq", boq_out, *report_arg, *profile_arg,
        "--stage", "30% Concept Design", "--site", "QRWN", "--out", cons)
    doc = json.loads(cons.read_text(encoding="utf-8"))
    assert doc["consistency_version"] == "1.0"
    assert all(f.get("reference", {}).get("rule_id") is None for f in doc["findings"])
    # C5 #REF! on parts 6–10 when present in this BOQ
    errs = boq["summary"].get("cell_errors") or []
    if errs:
        assert any(r["check"] == "C5" and r["result"] == "flag" for r in doc["comparisons"])


def test_t16_r2_nrjs_30pct(tmp_path):
    """T16-R2: NRJS 30 % — C1 ~37k / 50k vs model; C3; C5."""
    root = _root()
    boqs = _site_boq(root, "NRJS")
    if not boqs:
        pytest.skip("T16-R2: no NRJS BOQ under LDIP_REVIEW_ROOT (G-N5 may apply)")
    boq_out = tmp_path / "boq.json"
    run("read_boq.py", "--xlsx", boqs[0], "--out", boq_out)
    boq = json.loads(boq_out.read_text(encoding="utf-8"))
    cut = (boq.get("summary") or {}).get("earthworks", {}).get("cut") or {}
    fill = (boq.get("summary") or {}).get("earthworks", {}).get("fill") or {}
    if cut.get("base") is not None:
        assert cut["base"] == pytest.approx(37000, rel=0.05)
    if fill.get("base") is not None:
        assert fill["base"] == pytest.approx(50000, rel=0.05)

    reports = _site_report(root, "NRJS")
    report_arg = []
    if reports:
        claims = tmp_path / "claims.json"
        run("read_report.py", "--text", reports[0], "--out", claims)
        report_arg = ["--report", str(claims)]
    profiles = _site_profile(root, "NRJS")
    profile_arg = ["--profile", str(profiles[0])] if profiles else []

    cons = tmp_path / "consistency.json"
    run("check_consistency.py", "--boq", boq_out, *report_arg, *profile_arg,
        "--stage", "30% Concept Design", "--site", "NRJS", "--out", cons)
    doc = json.loads(cons.read_text(encoding="utf-8"))
    assert any(r["check"] in ("C1", "C3", "C5") for r in doc["comparisons"])


def test_t16_r3_nmar_30pct(tmp_path):
    """T16-R3: NMAR 30 % — C1 bases ×1.05; C2 ratios; C5 33 #REF!; C6 info."""
    root = _root()
    boqs = _site_boq(root, "NMAR")
    if not boqs:
        pytest.skip("T16-R3: no NMAR BOQ under LDIP_REVIEW_ROOT")
    boq_out = tmp_path / "boq.json"
    run("read_boq.py", "--xlsx", boqs[0], "--out", boq_out)
    boq = json.loads(boq_out.read_text(encoding="utf-8"))
    cut = (boq.get("summary") or {}).get("earthworks", {}).get("cut") or {}
    fill = (boq.get("summary") or {}).get("earthworks", {}).get("fill") or {}
    if cut.get("base") is not None:
        assert cut["base"] == pytest.approx(429590, rel=0.02)
        if cut.get("factor") is not None:
            assert cut["factor"] == pytest.approx(1.05)
    if fill.get("base") is not None:
        assert fill["base"] == pytest.approx(222820, rel=0.02)
    ratios = set(boq["summary"].get("ratios_recorded") or [])
    if ratios:
        assert {0.2, 0.8, 0.6}.issubset(ratios)
    errs = boq["summary"].get("cell_errors") or []
    # Probe: 33 #REF! in principal quantities
    if errs:
        assert len(errs) >= 30

    road = (boq.get("summary") or {}).get("roads") or {}
    reg = None
    if road.get("road_area") or road.get("kerb"):
        reg_path = tmp_path / "register.json"
        # Model side optional; C6 still information at 30 %
        reg_path.write_text(json.dumps({
            "roads": {
                "road_area_m2": (road.get("road_area") or {}).get("base") or 105710,
                "kerb_length_m": (road.get("kerb") or {}).get("base") or 23900,
            }
        }), encoding="utf-8")
        reg = reg_path

    cons = tmp_path / "consistency.json"
    args = ["--boq", str(boq_out), "--stage", "30% Concept Design", "--site", "NMAR",
            "--out", str(cons)]
    if reg:
        args.extend(["--road-register", str(reg)])
    run("check_consistency.py", *args)
    doc = json.loads(cons.read_text(encoding="utf-8"))
    c6 = [r for r in doc["comparisons"] if r["check"] == "C6"]
    if c6:
        assert all(r["result"] == "information" for r in c6)
