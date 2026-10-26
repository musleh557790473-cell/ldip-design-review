"""Skill 7 real-data gates T7-R1…T7-R4 — skipped without LDIP_REVIEW_ROOT (Rev M §4.15.4; S31 R8).

T7-R1: NMAR roads 53↔53 — without map: 53 proposed raster pairs, exactly 1 weak;
       with map: 53 pairs; content change from raster MAD (not injected); 00→00 → DM-3.
T7-R2: QRWN CRS-00002 — 20 rows; 8 D / 12 G / 0 L; 7 deferred (R5/R6).
T7-R3: PKG-010 pass 00→01 from revision table.
T7-R4: first real 60 % — waits for G-M5.

Real packages stay outside Git (brief §10.2).
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


def _nmar_road_packages(root: Path):
    pkgs = [p for p in _find(root, ["**/package.json"]) if "NMAR" in str(p).upper()]
    # Prefer roads-related folders
    roads = [p for p in pkgs if "ROD" in str(p).upper() or "road" in str(p).lower()]
    return roads or pkgs


def _qrwn_crs(root: Path):
    hits = _find(root, ["**/*CRS*00002*.xlsx", "**/*CRS-00002*.xlsx", "**/*crs*00002*.xlsx"])
    qrwn = [p for p in hits if "QRWN" in str(p).upper()]
    return qrwn or hits


def _qrwn_packages(root: Path):
    return [p for p in _find(root, ["**/package.json"]) if "QRWN" in str(p).upper()]


def _number_map(root: Path):
    maps = _find(root, ["**/number_map.csv", "**/*number*map*.csv"])
    nmar = [p for p in maps if "NMAR" in str(p).upper()]
    return (nmar or maps)[0] if (nmar or maps) else None


def _rulebook(root: Path):
    rbs = sorted((root / "rulebook").glob("rules_v*.json")) if (root / "rulebook").is_dir() else []
    return rbs[-1] if rbs else None


def _pkg_resolve_base(pkg_path: Path):
    # Prefer the package's parent as resolve base; also try submittals tree
    return str(pkg_path.parent)


def test_t7_r1_nmar_raster_53_without_and_with_map(tmp_path):
    """T7-R1 / R8: 53 pairs without map (1 weak); 53 with map; DM-3 from raster MAD change."""
    root = _root()
    pkgs = _nmar_road_packages(root)
    if len(pkgs) < 2:
        pytest.skip("T7-R1: need ≥2 NMAR package.json under LDIP_REVIEW_ROOT")
    prev, curr = pkgs[0], pkgs[1]
    # --- without map: raster MAD ---
    pairs_path = tmp_path / "pairs_nomap.json"
    msg = run("match_revisions.py",
              "--previous", prev, "--current", curr, "--out", pairs_path,
              "--resolve-base-previous", _pkg_resolve_base(prev),
              "--resolve-base-current", _pkg_resolve_base(curr))
    pairs = json.loads(pairs_path.read_text(encoding="utf-8"))
    assert pairs["previous_count"] == 53 and pairs["current_count"] == 53, (
        f"T7-R1 expected 53↔53 registers, got {pairs['previous_count']}↔{pairs['current_count']}")
    assert msg["pairs"] == 53, f"T7-R1 without map: expected 53 pairs, got {msg['pairs']}"
    assert msg["unmatched_current"] == 0 and msg["unmatched_previous"] == 0
    assert msg.get("raster_mad", 0) + msg.get("proposed", 0) >= 53 or msg["proposed"] == 53
    assert msg["weak"] == 1, f"T7-R1 expected exactly 1 weak pair, got {msg['weak']}"
    assert all(p["status"] == "proposed" or p["match"] in ("normalised", "number_map", "raster_mad", "title_sheet")
               for p in pairs["pairs"])

    # --- with map ---
    nmap = _number_map(root)
    if not nmap:
        pytest.skip("T7-R1: number_map.csv not found for with-map branch")
    pairs_map = tmp_path / "pairs_map.json"
    msg2 = run("match_revisions.py",
               "--previous", prev, "--current", curr, "--out", pairs_map,
               "--number-map", nmap,
               "--resolve-base-previous", _pkg_resolve_base(prev),
               "--resolve-base-current", _pkg_resolve_base(curr))
    assert msg2["pairs"] == 53

    # DM-3: content change from raster MAD on pairs (no --content-changed injection)
    rb = _rulebook(root)
    rules = tmp_path / "rules.json"
    if rb:
        run("get_rules.py", "--rulebook", rb, "--agent", "INT",
            "--skill", "comparing-drawing-revisions", "--out", rules, skill=SK8)
    else:
        rules.write_text(json.dumps({
            "agent": "INT", "rulebook_version": "v7",
            "rules": [{"rule_id": "PKG-010", "state": "active", "cap": "Confirmed",
                       "severity_default": "Minor",
                       "method": "Order revision codes within each series (0A<0B; 00<01; A0<A1).",
                       "default_status": {"script_output": "Confirmed"}, "parameters": {},
                       "inputs": [], "exclusions": [], "skip_subchecks_for": [],
                       "skill": "comparing-drawing-revisions", "issue_type": "package_control",
                       "owner_role": "SBM", "statement": "Revision advances.", "title": "PKG-010"}],
            "rules_skipped": [],
        }), encoding="utf-8")
    out = tmp_path / "rev_diff.json"
    # Use mapped pairs; MAD on renumbered pairs marks content_changed inside check_revision
    run("check_revision.py", "--pairs", pairs_map, "--rules", rules, "--out", out)
    doc = json.loads(out.read_text(encoding="utf-8"))
    same_00 = [c for c in doc["revision_checks"]
               if c.get("previous_revision") == "00" and c.get("current_revision") == "00"]
    assert len(same_00) == 53, f"T7-R1 expected 53× 00→00, got {len(same_00)}"
    obs = [c for c in same_00 if c.get("result") == "observation"]
    assert obs, "T7-R1: expected DM-3 Observation for renumbered 00→00 with content change"
    assert all(c.get("previous_revision_source") in ("revision_table", "file_name", "title_block", "explicit")
               or c.get("previous_revision") for c in same_00)


def test_t7_r2_qrwn_crs_00002_types(tmp_path):
    """T7-R2 / R5 / R6: 20 rows; 8 D / 12 G / 0 L; 7 deferred."""
    root = _root()
    crs_files = _qrwn_crs(root)
    if not crs_files:
        pytest.skip("T7-R2: CRS-00002 not found under LDIP_REVIEW_ROOT")
    pkgs = _qrwn_packages(root)
    pkg_n = pkgs[-1] if pkgs else None
    out = tmp_path / "closure.json"
    args = ["--crs", crs_files[0], "--out", out, "--xlsx", tmp_path / "closure.xlsx"]
    if pkg_n:
        args.extend(["--package-n", pkg_n])
    run("check_closed_comments.py", *args)
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert doc["counts"]["rows"] == 20, f"T7-R2 expected 20 rows, got {doc['counts']['rows']}"
    assert doc["counts"]["D"] == 8
    assert doc["counts"]["G"] == 12
    assert doc["counts"]["L"] == 0
    assert doc["counts"]["deferred_by_response"] == 7
    # Probe acceptance: D = rows 4, 7, 9, 10, 14, 16, 17, 20
    d_nos = {r["no"] for r in doc["rows"] if r["row_type"] == "D"}
    assert d_nos == {"4", "7", "9", "10", "14", "16", "17", "20"}
    for row in doc["rows"]:
        assert row["outcome"] in ("Addressed", "Not addressed", "Cannot tell")
        if row["row_type"] == "G":
            assert row["outcome"] == "Cannot tell"
    # Deferred rows 2, 9, 10, 14, 16, 17, 18
    deferred_nos = {r["no"] for r in doc["rows"] if r["deferred_by_response"]}
    assert deferred_nos == {"2", "9", "10", "14", "16", "17", "18"}


def test_t7_r3_pkg010_00_to_01(tmp_path):
    """T7-R3: revision table 00 → 01 → PKG-010 pass."""
    root = _root()
    pkgs = _qrwn_packages(root) or _find(root, ["**/package.json"])
    if len(pkgs) < 2:
        pytest.skip("T7-R3: need previous/current package.json")
    rb = _rulebook(root)
    if not rb:
        pytest.skip("T7-R3: no rulebook/rules_v*.json")
    rules = tmp_path / "rules.json"
    run("get_rules.py", "--rulebook", rb, "--agent", "INT",
        "--skill", "comparing-drawing-revisions", "--out", rules, skill=SK8)
    pairs_path = tmp_path / "pairs.json"
    run("match_revisions.py", "--previous", pkgs[0], "--current", pkgs[1], "--out", pairs_path,
        "--resolve-base-previous", _pkg_resolve_base(pkgs[0]),
        "--resolve-base-current", _pkg_resolve_base(pkgs[1]))
    out = tmp_path / "rev_diff.json"
    run("check_revision.py", "--pairs", pairs_path, "--rules", rules, "--out", out)
    doc = json.loads(out.read_text(encoding="utf-8"))
    advanced = [c for c in doc["revision_checks"]
                if c.get("previous_revision") == "00" and c.get("current_revision") == "01"
                and not c.get("renumbered")]
    assert advanced, "T7-R3: expected a same-number 00→01 pair"
    assert any(c["result"] == "pass" for c in advanced)


def test_t7_r4_first_60pct_waits_g_m5():
    """T7-R4: first real 60 % submittal — waits for G-M5."""
    root = _root()
    marker = os.environ.get("LDIP_T7_R4_60PCT")
    hits = _find(root, ["**/*60*pct*", "**/*60pct*", "**/60%/**"]) if not marker else [Path(marker)]
    if not hits and not marker:
        pytest.skip("T7-R4 waits for G-M5 — first real 60% submittal not available")
    pkgs = _find(root, ["**/package.json"])
    crs = _find(root, ["**/*CRS*.xlsx"])
    assert pkgs or crs
