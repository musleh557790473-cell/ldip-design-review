"""Skill 7 real-data gates T7-R1…T7-R3 — skipped without LDIP_REVIEW_ROOT.

T7-R1: set raster params from a real PDF pair (fills references/params.json GAP).
T7-R2: PKG-010 on a real previous/current package pair.
T7-R3: CRS closure against a real previous CRS workbook.

Real packages / CRS stay outside Git (brief §10.2).
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
    reason="LDIP_REVIEW_ROOT not set — real previous/current packages and CRS stay out of Git",
)

REPO = Path(__file__).resolve().parents[1]
SK = REPO / "plugin/ldip-design-review/skills/comparing-drawing-revisions/scripts"
SK8 = REPO / "plugin/ldip-design-review/skills/applying-ldip-rulebook/scripts"
PY = sys.executable


def run(script, *args, skill=SK):
    p = subprocess.run([PY, str(skill / script), *map(str, args)],
                       capture_output=True, text=True, timeout=600)
    out = p.stdout.strip().splitlines()
    msg = json.loads(out[-1]) if out else {"stderr": p.stderr[-3000:]}
    assert p.returncode == 0, msg
    return msg


def _find_packages(root: Path):
    """Return (prev_package, curr_package) candidates under 0.ai_context if present."""
    ctx = root / "0.ai_context"
    pkgs = sorted(ctx.rglob("package.json")) if ctx.is_dir() else []
    return pkgs


def _find_rulebook(root: Path):
    for cand in (root / "rulebook").glob("rules_v*.json"):
        return cand
    return None


def _find_crs(root: Path):
    hits = list(root.rglob("*CRS*.xlsx")) + list(root.rglob("*crs*.xlsx"))
    return sorted({p for p in hits if p.is_file()})


def test_t7_r1_raster_params_gap(tmp_path):
    """T7-R1: real PDF raster pair available → params tag still documented; diff runs Probable."""
    pkgs = _find_packages(Path(ROOT))
    if len(pkgs) < 2:
        pytest.skip("need at least two package.json under 0.ai_context for T7-R1")
    prev, curr = pkgs[0], pkgs[1]
    pairs = tmp_path / "pairs.json"
    run("match_revisions.py", "--previous", prev, "--current", curr, "--out", pairs)
    out = tmp_path / "rev_diff.json"
    run("diff_entities.py", "--pairs", pairs, "--previous-package", prev,
        "--current-package", curr, "--out", out)
    doc = json.loads(out.read_text(encoding="utf-8"))
    # Until owner sets numeric params from this run, the GAP tag remains the contract.
    tag = (doc.get("meta") or {}).get("raster_params_tag") or ""
    assert "GAP" in tag or any("GAP" in str(r.get("params_tag", "")) for r in doc.get("raster_diffs") or [])


def test_t7_r2_pkg010_real_pair(tmp_path):
    """T7-R2: PKG-010 via get_rules on a real previous/current package pair."""
    rb = _find_rulebook(Path(ROOT))
    if not rb:
        pytest.skip("no rulebook/rules_v*.json under LDIP_REVIEW_ROOT")
    pkgs = _find_packages(Path(ROOT))
    if len(pkgs) < 2:
        pytest.skip("need at least two package.json under 0.ai_context")
    rules_out = tmp_path / "rules.json"
    run("get_rules.py", "--rulebook", rb, "--agent", "INT",
        "--skill", "comparing-drawing-revisions", "--out", rules_out, skill=SK8)
    pairs = tmp_path / "pairs.json"
    run("match_revisions.py", "--previous", pkgs[0], "--current", pkgs[1], "--out", pairs)
    out = tmp_path / "rev_diff.json"
    run("check_revision.py", "--pairs", pairs, "--rules", rules_out, "--out", out)
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert "revision_checks" in doc


def test_t7_r3_crs_real(tmp_path):
    """T7-R3: closure report on a real CRS workbook — no Closed/Open outcomes."""
    crs_files = _find_crs(Path(ROOT))
    if not crs_files:
        pytest.skip("no CRS .xlsx under LDIP_REVIEW_ROOT")
    out = tmp_path / "closure.json"
    run("check_closed_comments.py", "--crs", crs_files[0], "--out", out,
        "--xlsx", tmp_path / "closure.xlsx")
    doc = json.loads(out.read_text(encoding="utf-8"))
    for row in doc["rows"]:
        assert row["outcome"] in ("Addressed", "Not addressed", "Cannot tell")
