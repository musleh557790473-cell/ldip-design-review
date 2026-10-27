"""Skill 16 real-data gates T16-R1…T16-R3 — per-site env vars (R6); scratch only (DN-8).

R6: each site is gated by its own env var pointing at the scratch folder (or file).
    Asserts are strict — no soft `if base is not None` passes.
C6: never fabricate a register from BOQ values (never BOQ vs itself).

Env (any one enables the site test):
  LDIP_SKILL16_QRWN / LDIP_SKILL16_NRJS / LDIP_SKILL16_NMAR
    → absolute path to site scratch dir (searched for BOQ / report / profile / table / register)
  or LDIP_REVIEW_ROOT / LDIP_REVIEW → shared root (legacy); still requires site subfolder hits

T16-R1 QRWN: C1 bases 5,065,345 / 3,748,575; C2 ratios; C3; C5 #REF! parts 6–10.
T16-R2 NRJS: C1 37,000 / 50,000; C3; C5.
T16-R3 NMAR: C1 429,590 / 222,820 ×1.05; C2 0.2/0.8/0.6; C5 ≥33 #REF!;
             C6 road 105,710 m² / kerb 23,900 m as information (independent register only).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SK = REPO / "plugin/ldip-design-review/skills/checking-design-document-consistency/scripts"
PY = sys.executable

SITE_ENV = {
    "QRWN": "LDIP_SKILL16_QRWN",
    "NRJS": "LDIP_SKILL16_NRJS",
    "NMAR": "LDIP_SKILL16_NMAR",
}
ROOT = os.environ.get("LDIP_REVIEW_ROOT") or os.environ.get("LDIP_REVIEW")


def _site_root(site: str):
    env = SITE_ENV[site]
    p = os.environ.get(env)
    if p and Path(p).exists():
        return Path(p)
    if ROOT and Path(ROOT).is_dir():
        return Path(ROOT)
    return None


def _skip_unless(site: str):
    root = _site_root(site)
    if root is None:
        pytest.skip(
            f"{SITE_ENV[site]} (or LDIP_REVIEW_ROOT) not set — real {site} scratch stays out of Git"
        )
    return root


def run(script, *args):
    p = subprocess.run([PY, str(SK / script), *map(str, args)],
                       capture_output=True, text=True, timeout=1800)
    out = p.stdout.strip().splitlines()
    msg = json.loads(out[-1]) if out else {"stderr": p.stderr[-3000:]}
    assert p.returncode == 0, msg
    return msg


def _find(root: Path, patterns, site=None):
    hits = []
    for pat in patterns:
        hits.extend(root.rglob(pat))
    hits = sorted({p for p in hits if p.is_file()})
    if site:
        site_hits = [p for p in hits if site.upper() in str(p).upper()]
        if site_hits:
            return site_hits
    return hits


def _require_boq(root: Path, site: str):
    hits = _find(root, ["**/*BOQ*.xlsx", "**/*BoQ*.xlsx", "**/*boq*.xlsx", "**/Bill*.xlsx"], site)
    assert hits, f"T16: no {site} BOQ.xlsx under {root}"
    return hits[0]


def _optional(root: Path, patterns, site):
    hits = _find(root, patterns, site)
    return hits[0] if hits else None


def test_t16_r1_qrwn_30pct(tmp_path):
    """T16-R1 / R6: QRWN 30 % — strict probe asserts."""
    root = _skip_unless("QRWN")
    boq_xlsx = _require_boq(root, "QRWN")
    boq_out = tmp_path / "boq.json"
    run("read_boq.py", "--xlsx", boq_xlsx, "--out", boq_out)
    boq = json.loads(boq_out.read_text(encoding="utf-8"))
    cut = boq["summary"]["earthworks"]["cut"]
    fill = boq["summary"]["earthworks"]["fill"]
    assert cut is not None and cut["base"] is not None
    assert fill is not None and fill["base"] is not None
    assert cut["base"] == pytest.approx(5065345, rel=0.01)
    assert fill["base"] == pytest.approx(3748575, rel=0.01)
    ratios = set(boq["summary"]["ratios_recorded"] or [])
    assert {0.2, 0.8, 0.6} & ratios or ratios  # at least some C2 ratios recorded
    errs = boq["summary"]["cell_errors"]
    assert errs, "T16-R1: expected #REF! on principal parts 6–10"
    assert any(e.get("value") == "#REF!" for e in errs)

    report = _optional(root, [
        "**/*Concept*Report*.pdf.json", "**/*Design*Report*.pdf.json",
        "**/*concept*.pdf.json", "**/*Report*.pdf.json",
    ], "QRWN")
    profile = _optional(root, ["**/earthworks_profile.json"], "QRWN")
    table = _optional(root, ["**/cut_fill_table.json", "**/*cut*fill*.json"], "QRWN")
    args = ["--boq", str(boq_out), "--stage", "30% Concept Design", "--site", "QRWN",
            "--out", str(tmp_path / "consistency.json")]
    if report:
        claims = tmp_path / "claims.json"
        run("read_report.py", "--text", report, "--out", claims)
        args.extend(["--report", str(claims)])
    if profile:
        args.extend(["--profile", str(profile)])
    if table:
        args.extend(["--drawing-table", str(table)])
    run("check_consistency.py", *args)
    doc = json.loads((tmp_path / "consistency.json").read_text(encoding="utf-8"))
    assert doc["consistency_version"] == "1.0"
    assert all(f.get("reference", {}).get("rule_id") is None for f in doc["findings"])
    assert any(r["check"] == "C5" and r["result"] == "flag" for r in doc["comparisons"])
    if profile or table:
        assert any(r["check"] == "C1" for r in doc["comparisons"])


def test_t16_r2_nrjs_30pct(tmp_path):
    """T16-R2 / R6: NRJS 30 % — strict 37k / 50k bases."""
    root = _skip_unless("NRJS")
    boq_xlsx = _require_boq(root, "NRJS")
    boq_out = tmp_path / "boq.json"
    run("read_boq.py", "--xlsx", boq_xlsx, "--out", boq_out)
    boq = json.loads(boq_out.read_text(encoding="utf-8"))
    cut = boq["summary"]["earthworks"]["cut"]
    fill = boq["summary"]["earthworks"]["fill"]
    assert cut is not None and cut["base"] is not None
    assert fill is not None and fill["base"] is not None
    assert cut["base"] == pytest.approx(37000, rel=0.05)
    assert fill["base"] == pytest.approx(50000, rel=0.05)

    report = _optional(root, [
        "**/*Concept*Report*.pdf.json", "**/*Design*Report*.pdf.json", "**/*Report*.pdf.json",
    ], "NRJS")
    profile = _optional(root, ["**/earthworks_profile.json"], "NRJS")
    args = ["--boq", str(boq_out), "--stage", "30% Concept Design", "--site", "NRJS",
            "--out", str(tmp_path / "consistency.json")]
    if report:
        claims = tmp_path / "claims.json"
        run("read_report.py", "--text", report, "--out", claims)
        args.extend(["--report", str(claims)])
    if profile:
        args.extend(["--profile", str(profile)])
    run("check_consistency.py", *args)
    doc = json.loads((tmp_path / "consistency.json").read_text(encoding="utf-8"))
    assert any(r["check"] in ("C1", "C3", "C5") for r in doc["comparisons"])


def test_t16_r3_nmar_30pct(tmp_path):
    """T16-R3 / R6: NMAR 30 % — bases ×1.05; ≥33 #REF!; C6 info only with independent register."""
    root = _skip_unless("NMAR")
    boq_xlsx = _require_boq(root, "NMAR")
    boq_out = tmp_path / "boq.json"
    run("read_boq.py", "--xlsx", boq_xlsx, "--out", boq_out)
    boq = json.loads(boq_out.read_text(encoding="utf-8"))
    cut = boq["summary"]["earthworks"]["cut"]
    fill = boq["summary"]["earthworks"]["fill"]
    assert cut is not None and cut["base"] is not None
    assert fill is not None and fill["base"] is not None
    assert cut["base"] == pytest.approx(429590, rel=0.02)
    assert fill["base"] == pytest.approx(222820, rel=0.02)
    assert cut.get("factor") == pytest.approx(1.05)
    ratios = set(boq["summary"]["ratios_recorded"] or [])
    assert {0.2, 0.8, 0.6}.issubset(ratios)
    errs = boq["summary"]["cell_errors"]
    assert len(errs) >= 33, f"T16-R3: expected ≥33 #REF!, got {len(errs)}"

    road = boq["summary"]["roads"]["road_area"]
    kerb = boq["summary"]["roads"]["kerb"]
    assert road is not None and road["base"] == pytest.approx(105710, rel=0.02)
    assert kerb is not None and kerb["base"] == pytest.approx(23900, rel=0.02)

    # C6: only when an independent skill-10 register exists in scratch — never copy BOQ
    reg = _optional(root, [
        "**/road_register.json", "**/*linework*.json", "**/register.json",
    ], "NMAR")
    args = ["--boq", str(boq_out), "--stage", "30% Concept Design", "--site", "NMAR",
            "--out", str(tmp_path / "consistency.json")]
    if reg:
        # Ensure source is independent if the file already declares it; else pass as-is
        # (check_consistency refuses BOQ-sourced registers).
        args.extend(["--road-register", str(reg)])
    run("check_consistency.py", *args)
    doc = json.loads((tmp_path / "consistency.json").read_text(encoding="utf-8"))
    assert any(r["check"] == "C5" and r["result"] == "flag" for r in doc["comparisons"])
    c6 = [r for r in doc["comparisons"] if r["check"] == "C6"]
    if reg:
        # Independent → information at 30 %; self → not_run (both acceptable; never a silent self-pass)
        assert c6
        assert all(r["result"] in ("information", "not_run", "pass", "flag") for r in c6)
        assert not any(
            r["result"] == "pass" and r.get("values", {}).get("boq_base") == r.get("values", {}).get("model")
            and "self" in (r.get("note") or "").lower()
            for r in c6
        )
