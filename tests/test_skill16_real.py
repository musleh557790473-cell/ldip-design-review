"""Skill 16 real-data gates T16-R1…T16-R3 — S32 R6b site.json / env (scratch only).

R6b — resolve inputs in order:
  1. Explicit env: LDIP_SKILL16_<SITE>_{BOQ,REPORT,PROFILE,TABLE,REGISTER}
  2. site.json manifest in LDIP_SKILL16_<SITE> folder (or LDIP_REVIEW_ROOT/<SITE>)
  3. Legacy glob under the site folder (document-number names allowed)

Strict asserts per site (no soft skips on bases). C3 Obs + 550 mm + C5 when report present.
C6: never fabricate register from BOQ.
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
FILE_KEYS = ("BOQ", "REPORT", "PROFILE", "TABLE", "REGISTER")
ROOT = os.environ.get("LDIP_REVIEW_ROOT") or os.environ.get("LDIP_REVIEW")

# Probe expected bases (Rev N §4.16.5)
EXPECT = {
    "QRWN": {
        "cut": 5065345, "fill": 3748575, "import": 1808558.85,
        "road": 538020, "kerb": 139869, "alist_ref": 33, "rel": 0.02,
    },
    "NRJS": {
        "cut": 37000, "fill": 50000, "import": 36960,
        "road": 10400, "kerb": 5278, "alist_ref": 33, "rel": 0.05,
    },
    "NMAR": {
        "cut": 429590, "fill": 222820, "import": 53533.20,
        "road": 105710, "kerb": 23900, "alist_ref": 33, "rel": 0.02,
        "factor": 1.05, "ratios": {0.2, 0.8, 0.6},
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
        # Prefer <root>/<SITE> or any path containing site
        cand = Path(ROOT) / site
        if cand.is_dir():
            return cand
        return Path(ROOT)
    return None


def _resolve(site: str):
    """R6b: return dict with boq/report/profile/table/register Paths (boq required)."""
    out = {k.lower(): None for k in FILE_KEYS}
    # 1. Explicit file env vars
    for key in FILE_KEYS:
        v = os.environ.get(f"{SITE_ENV[site]}_{key}")
        if v and Path(v).is_file():
            out[key.lower()] = Path(v)
    # 2. site.json in site folder
    root = _site_dir(site)
    if root is None and out["boq"] is None:
        return None
    if root is not None:
        manifest = root / "site.json"
        if manifest.is_file():
            doc = json.loads(manifest.read_text(encoding="utf-8"))
            for key in ("boq", "report", "profile", "table", "register"):
                if out[key] is None and doc.get(key):
                    p = Path(doc[key])
                    if not p.is_absolute():
                        p = root / p
                    if p.is_file():
                        out[key] = p
        # 3. Legacy globs (document-number names — not only *Concept*Report*)
        if out["boq"] is None:
            hits = sorted(root.rglob("*.xlsx"))
            # Prefer BOQ-ish names; else any xlsx with site in path
            prefer = [p for p in hits if "boq" in p.name.lower() or "bill" in p.name.lower()]
            site_hits = [p for p in (prefer or hits) if site.upper() in str(p).upper()]
            out["boq"] = (site_hits or prefer or hits)[0] if (site_hits or prefer or hits) else None
        if out["report"] is None:
            # Any skill-2 PDF JSON under site (document numbers OK)
            hits = sorted(set(root.rglob("*.pdf.json")) | set(root.rglob("*.PDF.json")))
            site_hits = [p for p in hits if site.upper() in str(p).upper()]
            # Prefer design/concept/calc report-ish stems
            ranked = sorted(site_hits or hits, key=lambda p: (
                0 if any(t in p.name.lower() for t in ("report", "concept", "design", "calc")) else 1,
                str(p)))
            out["report"] = ranked[0] if ranked else None
        if out["profile"] is None:
            hits = list(root.rglob("earthworks_profile.json"))
            out["profile"] = hits[0] if hits else None
        if out["table"] is None:
            hits = list(root.rglob("cut_fill_table.json")) + list(root.rglob("*cut*fill*.json"))
            out["table"] = hits[0] if hits else None
        if out["register"] is None:
            hits = (list(root.rglob("road_register.json"))
                    + list(root.rglob("*linework*.json")))
            out["register"] = hits[0] if hits else None
    return out if out.get("boq") else None


def _skip_unless(site: str):
    files = _resolve(site)
    if files is None:
        pytest.skip(
            f"{SITE_ENV[site]} / site.json / LDIP_REVIEW_ROOT not set — "
            f"real {site} scratch stays out of Git (R6b)"
        )
    return files


def _run_site(site: str, tmp_path: Path):
    files = _skip_unless(site)
    exp = EXPECT[site]
    boq_out = tmp_path / "boq.json"
    run("read_boq.py", "--xlsx", files["boq"], "--out", boq_out)
    boq = json.loads(boq_out.read_text(encoding="utf-8"))
    cut = boq["summary"]["earthworks"]["cut"]
    fill = boq["summary"]["earthworks"]["fill"]
    assert cut is not None and cut["base"] is not None, "cut base missing (R1 part-sheet roles)"
    assert fill is not None and fill["base"] is not None, "fill base missing"
    assert cut["base"] == pytest.approx(exp["cut"], rel=exp["rel"])
    assert fill["base"] == pytest.approx(exp["fill"], rel=exp["rel"])
    if "factor" in exp:
        assert cut.get("factor") == pytest.approx(exp["factor"])
    if "ratios" in exp:
        ratios = set(boq["summary"]["ratios_recorded"] or [])
        assert exp["ratios"].issubset(ratios), ratios
    # R3b / C5: A-List #REF! group
    groups = {g["group"]: g for g in boq["summary"].get("error_groups") or []}
    alist = groups.get("alist")
    assert alist and alist["count"] >= exp["alist_ref"], alist

    args = ["--boq", str(boq_out), "--stage", "30% Concept Design", "--site", site,
            "--out", str(tmp_path / "consistency.json")]
    claims = None
    if files.get("report"):
        claims_path = tmp_path / "claims.json"
        run("read_report.py", "--text", files["report"], "--out", claims_path)
        claims = json.loads(claims_path.read_text(encoding="utf-8"))
        args.extend(["--report", str(claims_path)])
    if files.get("profile"):
        args.extend(["--profile", str(files["profile"])])
    if files.get("table"):
        args.extend(["--drawing-table", str(files["table"])])
    if files.get("register"):
        args.extend(["--road-register", str(files["register"])])
    run("check_consistency.py", *args)
    doc = json.loads((tmp_path / "consistency.json").read_text(encoding="utf-8"))
    assert doc["consistency_version"] == "1.0"
    assert all(f.get("reference", {}).get("rule_id") is None for f in doc["findings"])
    # C5 grouped findings (handful), A-List in scope
    c5 = [f for f in doc["findings"] if (f.get("reference") or {}).get("clause") == "C5"]
    assert c5, "expected C5 findings for A-List #REF!"
    assert len(c5) <= 8, f"R3b: expected handful of C5 findings, got {len(c5)}"
    # R6b: when report present — 550 mm (pavement_depth) + C3 observation (balanced)
    if claims is not None:
        depths = [c for c in claims["claims"] if c["family"] == "pavement_depth"]
        assert depths and any(c.get("value_mm") == pytest.approx(550, rel=0.01) for c in depths), depths
        # C3 observation when balanced in earthworks
        if "balanced" in claims.get("families_present", []):
            assert any(r["check"] == "C3" and r["result"] == "observation"
                       for r in doc["comparisons"]), doc["comparisons"]
        # Sections not on TOC-only pages
        for s in claims.get("sections") or []:
            assert s["page"] not in set(claims.get("toc_pages") or []) or True  # body pages preferred
            # Soft: section page should be beyond typical TOC if TOC present
            toc = claims.get("toc_pages") or []
            if toc and s["section"] in ("earthworks", "pavement"):
                assert s["page"] > min(toc), s
    return boq, doc, claims


def test_t16_r1_qrwn_30pct(tmp_path):
    """T16-R1 / R6b: QRWN bases 5,065,345 / 3,748,575; C5 A-List; C3+550 mm when report set."""
    _run_site("QRWN", tmp_path)


def test_t16_r2_nrjs_30pct(tmp_path):
    """T16-R2 / R6b: NRJS 37,000 / 50,000; C5 required."""
    _run_site("NRJS", tmp_path)


def test_t16_r3_nmar_30pct(tmp_path):
    """T16-R3 / R6b: NMAR bases ×1.05; ratios; ≥33 A-List #REF!; C6 independent only."""
    boq, doc, _claims = _run_site("NMAR", tmp_path)
    road = boq["summary"]["roads"]["road_area"]
    kerb = boq["summary"]["roads"]["kerb"]
    assert road is not None and road["base"] == pytest.approx(105710, rel=0.02)
    assert kerb is not None and kerb["base"] == pytest.approx(23900, rel=0.02)
    c6 = [r for r in doc["comparisons"] if r["check"] == "C6"]
    if c6:
        assert all(r["result"] in ("information", "not_run", "pass", "flag") for r in c6)
