"""Skill 14 real-data gates T14-R1…T14-R3 — scratch only (DO-8).

Env (any one resolves a site folder / DWG):
  LDIP_SKILL14_QRWN / LDIP_SKILL14_NRJS / LDIP_SKILL14_NMAR
  LDIP_SKILL14_<SITE>_DWG
  LDIP_REVIEW_ROOT / LDIP_REVIEW  (looks for UTL-30000*.dwg / IUC*30001*.dwg)

No real project data in Git.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SK = REPO / "plugin/ldip-design-review/skills/coordinating-utility-corridors/scripts"
PY = sys.executable

SITE_ENV = {
    "QRWN": "LDIP_SKILL14_QRWN",
    "NRJS": "LDIP_SKILL14_NRJS",
    "NMAR": "LDIP_SKILL14_NMAR",
}
ROOT = os.environ.get("LDIP_REVIEW_ROOT") or os.environ.get("LDIP_REVIEW")

# Probe expectations (SPC-0006 Rev O §4.17.5)
EXPECT = {
    "QRWN": {
        "sections": {"A1", "A2", "A4", "B1", "B2", "B3"},
        "min_band_inserts": 110,
        "c1_missing": 0,
        "c3_flags": 6,
        "doc_glob": ["**/UTL-30000-001*.dwg", "**/UTL-30000*.dwg"],
    },
    "NRJS": {
        "sections": {"A1", "A2", "A3"},
        "min_band_inserts": 44,
        "c1_missing": 0,
        "c3_flags": 3,
        "doc_glob": ["**/IUC*30001*.dwg", "**/IUC-DWG*30001*.dwg", "**/UTL-30000*.dwg"],
    },
    "NMAR": {
        "sections": {"A1", "A2", "A3", "A3-1", "B1", "B1-1", "B1-2"},
        "min_band_inserts": 1,
        "c1_missing": 0,
        "c3_flags": 7,
        "doc_glob": ["**/UTL-30000-001*.dwg", "**/UTL-30000*.dwg"],
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
        cand = Path(ROOT) / site
        if cand.is_dir():
            return cand
        # also scratch/<SITE>
        scratch = Path(ROOT) / "scratch" / site
        if scratch.is_dir():
            return scratch
    return None


def _resolve_dwg(site: str):
    explicit = os.environ.get(f"{SITE_ENV[site]}_DWG")
    if explicit and Path(explicit).is_file():
        return Path(explicit)
    root = _site_dir(site)
    if root is None:
        return None
    manifest = root / "site.json"
    if manifest.is_file():
        doc = json.loads(manifest.read_text(encoding="utf-8"))
        for key in ("section_dwg", "dwg", "utl_30000"):
            if doc.get(key):
                p = Path(doc[key])
                if not p.is_absolute():
                    p = root / p
                if p.is_file():
                    return p
    for pattern in EXPECT[site]["doc_glob"]:
        hits = sorted(root.glob(pattern))
        if hits:
            return hits[0]
    return None


def _require(site: str):
    dwg = _resolve_dwg(site)
    if dwg is None:
        pytest.skip(
            f"{SITE_ENV[site]} / {SITE_ENV[site]}_DWG / LDIP_REVIEW_ROOT not set — "
            f"real {site} scratch stays out of Git (DO-8)"
        )
    return dwg


def _run_site(site: str, tmp_path: Path):
    dwg = _require(site)
    exp = EXPECT[site]
    sec_path = tmp_path / "sections.json"
    msg = run("read_corridor_sections.py", "--dwg", dwg, "--site", site, "--out", sec_path)
    sec = json.loads(sec_path.read_text(encoding="utf-8"))
    assert sec["route"] == "dwg_raw"
    labels = {s["label"] for s in sec["sections"]}
    assert exp["sections"] <= labels, f"{site}: expected {exp['sections']}, got {labels}"
    bands = (sec.get("source") or {}).get("band_insert_count") or 0
    assert bands >= exp["min_band_inserts"], f"{site}: band inserts {bands} < {exp['min_band_inserts']}"

    out = tmp_path / "corridors.json"
    run("check_corridors.py", "--sections", sec_path, "--site", site, "--sub", "30pct",
        "--stage", "30% Concept Design", "--out", out, "--doc", dwg.stem)
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert doc["counts"]["c1_missing"] == exp["c1_missing"]
    assert doc["counts"]["c3_flags"] == exp["c3_flags"], (
        f"{site}: C3 flags {doc['counts']['c3_flags']} != {exp['c3_flags']}"
    )
    # C2 rows present as information (DO-5)
    c2 = [r for r in doc["comparisons"] if r["check"] == "C2"]
    assert c2 and all(r["result"] == "information" for r in c2)
    assert all(f.get("report_only") and f["reference"]["rule_id"] is None for f in doc["findings"])
    return sec, doc, msg


def test_t14_r1_qrwn(tmp_path):
    """T14-R1: QRWN UTL-30000-001 — 110 band inserts; 6 sections; C1 0; C3 6/6."""
    _run_site("QRWN", tmp_path)


def test_t14_r2_nrjs(tmp_path):
    """T14-R2: NRJS IUC-30001 — 44 band inserts; 3 sections; C1 0; C3 3/3."""
    _run_site("NRJS", tmp_path)


def test_t14_r3_nmar(tmp_path):
    """T14-R3: NMAR UTL-30000-001 — 7 sections; C1 0; C3 7/7."""
    _run_site("NMAR", tmp_path)
