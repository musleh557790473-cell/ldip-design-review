"""Skill 14 real-data gates T14-R1…T14-R3 — scratch only (DO-8). Strengthened R7.

Env (any one resolves a site folder / DWG):
  LDIP_SKILL14_QRWN / LDIP_SKILL14_NRJS / LDIP_SKILL14_NMAR
  LDIP_SKILL14_<SITE>_DWG
  LDIP_REVIEW_ROOT / LDIP_REVIEW  (looks for UTL-30000*.dwg / IUC*30001*.dwg)

No real project data in Git. Claude runs these; CI skips.
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

# R7 / SPC-0006 Rev O §4.17.5 + probe
EXPECT = {
    "QRWN": {
        "sections": ["A1", "A2", "A4", "B1", "B2", "B3"],
        "band_inserts": 110,
        "c1_missing": 0,
        "c3_flags": 6,
        "row_widths": {"A1": 15.0, "A2": 18.0, "A4": 20.0, "B1": 25.0, "B2": 30.0, "B3": 36.0},
        "c2_samples": [
            # (section, utility, expected_offset, optional side filter)
            ("B2", "sewer", [-3.40, -3.05], None),
        ],
        "c4_samples": [
            # (section, power, other, expected_cc, min_count)
            ("A2", "lv", "house_connection", 0.71, 1),
        ],
        "doc_glob": ["**/UTL-30000-001*.dwg", "**/UTL-30000*.dwg"],
    },
    "NRJS": {
        "sections": ["A1", "A2", "A3"],
        "band_inserts": 44,
        "c1_missing": 0,
        "c3_flags": 3,
        "row_widths": {"A1": 15.0, "A2": 18.0, "A3": 18.0},
        "c2_samples": [
            ("A2", "water", [2.38], None),
        ],
        "c4_samples": [
            ("A1", "lv", "telecom", 0.75, 1),
        ],
        "doc_glob": ["**/IUC*30001*.dwg", "**/IUC-DWG*30001*.dwg", "**/UTL-30000*.dwg"],
    },
    "NMAR": {
        "sections": ["A1", "A2", "A3", "A3-1", "B1", "B1-1", "B1-2"],
        "band_inserts": 123,
        "c1_missing": 0,
        "c3_flags": 7,
        "row_widths": {
            "A1": 15.0, "A2": 18.0, "A3": 18.0, "A3-1": 18.0,
            "B1": 25.0, "B1-1": 25.0, "B1-2": 25.0,
        },
        "c2_samples": [
            ("A1", "water", [-0.12], None),
            ("A1", "storm", [-1.15], None),
            ("A1", "sewer", [-2.65], None),
        ],
        "c4_samples": [
            ("B1", "lv", "water", 0.65, 2),
        ],
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


def _sec_by_label(sec_doc, label):
    hits = [s for s in sec_doc["sections"] if s.get("label") == label]
    assert hits, f"section {label} missing"
    return hits[0]


def _assert_c2_samples(doc, samples, tol=0.02):
    for section, util, expecteds, _side in samples:
        rows = [
            r for r in doc["comparisons"]
            if r["check"] == "C2"
            and r["values"].get("section") == section
            and r["values"].get("utility") == util
            and r["result"] == "information"
        ]
        assert rows, f"C2 sample missing for {section}/{util}"
        offs = [r["values"].get("offset_to_curb_m") for r in rows]
        offs = [o for o in offs if o is not None]
        assert offs, f"C2 offsets null for {section}/{util}"
        for exp in expecteds:
            assert any(abs(o - exp) <= tol for o in offs), (
                f"C2 {section}/{util}: expected ~{exp}, got {offs}"
            )


def _assert_c4_samples(doc, samples, tol=0.02):
    for section, power, other, exp_cc, min_count in samples:
        rows = [
            r for r in doc["comparisons"]
            if r["check"] == "C4"
            and r["values"].get("section") == section
            and r["values"].get("power") == power
            and r["values"].get("other") == other
        ]
        # also accept swapped naming for house_connection alias
        if not rows and other == "house_connection":
            rows = [
                r for r in doc["comparisons"]
                if r["check"] == "C4"
                and r["values"].get("section") == section
                and r["values"].get("power") == power
                and r["values"].get("other") in ("house_connection", "hc")
            ]
        close = [
            r for r in rows
            if abs(float(r["values"].get("centre_to_centre_m", 999)) - exp_cc) <= tol
        ]
        assert len(close) >= min_count, (
            f"C4 {section} {power}↔{other} ~{exp_cc}: found {len(close)} < {min_count}; "
            f"rows={[r['values'].get('centre_to_centre_m') for r in rows]}"
        )


def _run_site(site: str, tmp_path: Path):
    dwg = _require(site)
    exp = EXPECT[site]
    sec_path = tmp_path / "sections.json"
    msg = run("read_corridor_sections.py", "--dwg", dwg, "--site", site, "--out", sec_path)
    sec = json.loads(sec_path.read_text(encoding="utf-8"))
    assert sec["route"] == "dwg_raw"

    labels = [s["label"] for s in sec["sections"]]
    assert labels == exp["sections"] or set(exp["sections"]) <= set(labels), (
        f"{site}: expected sections {exp['sections']}, got {labels}"
    )
    # Prefer exact ordered set equality when counts match
    assert set(labels) == set(exp["sections"]), (
        f"{site}: section set {set(labels)} != {set(exp['sections'])}"
    )
    assert len(set(labels)) == len(exp["sections"])

    bands = (sec.get("source") or {}).get("band_insert_count") or 0
    assert bands == exp["band_inserts"], (
        f"{site}: band inserts {bands} != {exp['band_inserts']}"
    )

    for lab, width in exp["row_widths"].items():
        s = _sec_by_label(sec, lab)
        rw = s.get("row_width")
        if rw is None and s.get("row_left") is not None and s.get("row_right") is not None:
            rw = s["row_right"] - s["row_left"]
        assert rw is not None and abs(rw - width) <= 0.01, (
            f"{site} {lab}: ROW width {rw} != {width} ±0.01"
        )

    out = tmp_path / "corridors.json"
    run("check_corridors.py", "--sections", sec_path, "--site", site, "--sub", "30pct",
        "--stage", "30% Concept Design", "--out", out, "--doc", dwg.stem)
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert doc["counts"]["c1_missing"] == exp["c1_missing"]
    assert doc["counts"]["c3_flags"] == exp["c3_flags"], (
        f"{site}: C3 flags {doc['counts']['c3_flags']} != {exp['c3_flags']}"
    )
    _assert_c2_samples(doc, exp["c2_samples"])
    _assert_c4_samples(doc, exp["c4_samples"])
    assert all(f.get("report_only") and f["reference"]["rule_id"] is None for f in doc["findings"])
    return sec, doc, msg


def test_t14_r1_qrwn(tmp_path):
    """T14-R1: QRWN — 110 bands; 6 sections; ROW; C1 0; C3 6/6; C2/C4 samples."""
    _run_site("QRWN", tmp_path)


def test_t14_r2_nrjs(tmp_path):
    """T14-R2: NRJS — 44 bands; 3 sections; ROW; C1 0; C3 3/3; C2/C4 samples."""
    _run_site("NRJS", tmp_path)


def test_t14_r3_nmar(tmp_path):
    """T14-R3: NMAR — 123 bands; 7 sections; ROW; C1 0; C3 7/7; C2/C4 samples."""
    _run_site("NMAR", tmp_path)
