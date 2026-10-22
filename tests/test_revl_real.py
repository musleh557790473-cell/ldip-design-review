"""Skill 13 real-data tests TL8 / TL9 — skipped without LDIP_REVIEW_ROOT.

Assert values from Claude S29 review §4 (after D1–D4). Real packages stay outside Git.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = os.environ.get("LDIP_REVIEW_ROOT") or os.environ.get("LDIP_REVIEW")

pytestmark = pytest.mark.skipif(
    not ROOT or not Path(ROOT).is_dir(),
    reason="LDIP_REVIEW_ROOT not set — real NMAR/QRWN/NRJS packages stay out of Git",
)

REPO = Path(__file__).resolve().parents[1]
SK13 = REPO / "plugin/ldip-design-review/skills/reviewing-earthworks-geotech/scripts"
PY = sys.executable


def run(script, *args, timeout=600):
    p = subprocess.run([PY, str(SK13 / script), *map(str, args)],
                       capture_output=True, text=True, timeout=timeout)
    out = p.stdout.strip().splitlines()
    msg = json.loads(out[-1]) if out else {"stderr": p.stderr[-3000:]}
    assert p.returncode == 0, msg
    return msg


def load(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def write(p, obj):
    Path(p).write_text(json.dumps(obj, indent=1), encoding="utf-8")
    return Path(p)


def _find_ifc(root: Path, *needles):
    hits = []
    for pat in needles:
        hits.extend(root.rglob(pat))
    # prefer .ifc over side files
    ifcs = [p for p in hits if p.suffix.lower() == ".ifc"]
    return sorted(set(ifcs), key=lambda p: p.name)


def _rules_ewk_active(tmp: Path):
    """Test-only active EWK rules (mirror S29 scratch; never deposited rules_v8)."""
    def R(rid, title, params, sev="Minor", issue="design_error", phase="DD"):
        return {
            "rule_id": rid, "title": title, "state": "active", "cap": "Confirmed",
            "statement": f"Synthetic {rid} (TL8/TL9 harness).", "inputs": ["grading_surface"],
            "parameters": {
                k: {"value": v, "tag": "AGREED", "source_id": "SRC-T", "clause": "§T",
                    "unit": u, "cap": "Confirmed",
                    **({"tolerance_ref": "grade_pct"} if k in ("g_min", "slope_min_h_per_v") else {})}
                for k, (v, u) in params.items()
            },
            "severity_default": sev, "issue_type": issue, "check_type": "value",
            "default_status": {"dwg_vector": "Confirmed", "script_output": "Confirmed",
                               "landxml": "Confirmed", "pdf_vector_text": "Probable"},
            "owner_role": "DMR", "required_from_phase": phase,
        }

    rs = [
        R("EWK-001", "Minimum gradient — unpaved", {"g_min": (1.0, "%")}, sev="Minor"),
        R("EWK-002", "Cut and fill slopes", {
            "slope_min_h_per_v": (2.0, "H:V"),
            "fos_static": (1.5, None),
            "fos_seismic": (1.1, None),
        }, sev="Major"),
        R("EWK-003", "Plot levels vs road levels", {
            "positive_drainage": ("drain out", None),
            "floor_above_street": (0.30, "m"),
        }, sev="Major"),
        R("EWK-101", "Slope protection", {"required_element": ("slope protection", None)},
          sev="Minor", issue="missing_information"),
        R("EWK-102", "Retaining structure", {"required_element": ("retaining", None)},
          sev="Major", issue="missing_information"),
    ]
    path = tmp / "rules_RDE.json"
    write(path, {
        "agent": "RDE", "rulebook_version": "v7-test-synthetic-tl8",
        "rules": rs, "rules_skipped": [],
        "tolerances": {"grade_pct": {"value": 0.05, "unit": "%"}},
        "stage_map": {
            "30% Concept Design": {"phase": "PD", "milestone": 30},
            "60%": {"phase": "DD", "milestone": 60},
        },
    })
    return path


def _pick_by_suffix(ifcs, suffix):
    for p in ifcs:
        if suffix in p.name:
            return p
    return None


@pytest.fixture(scope="module")
def nmar_bundle(tmp_path_factory):
    root = Path(ROOT)
    ifcs = _find_ifc(root, "*ROD-MOD-90000-001*.ifc", "*ROD-MOD-90000-002*.ifc",
                     "*ROD-MOD-90000-003*.ifc", "*Site*Grading*.ifc")
    m001 = _pick_by_suffix(ifcs, "90000-001") or _pick_by_suffix(ifcs, "90000-001".upper())
    m002 = _pick_by_suffix(ifcs, "90000-002")
    m003 = _pick_by_suffix(ifcs, "90000-003")
    if not (m001 and m002 and m003):
        pytest.skip("NMAR ROD-MOD-90000-001/002/003 IFC not found under LDIP_REVIEW_ROOT")
    o = tmp_path_factory.mktemp("tl8")
    faces = {}
    tins = {}
    for key, path in (("001", m001), ("002", m002), ("003", m003)):
        msg = run("read_ifc_surface.py", "--ifc", path, "--out-dir", o / f"ifc_{key}",
                  "--prefix", f"nmar{key}", timeout=180)
        assert len(msg["surfaces"]) == 1, msg
        faces[key] = msg["surfaces"][0]["faces"]
        tins[key] = msg["surfaces"][0]["path"]
        assert msg["layer_choice"] in ("single_layer", "thin_solid_split")
    return {"root": root, "out": o, "faces": faces, "tins": tins,
            "paths": {"001": m001, "002": m002, "003": m003}}


def test_tl8_nmar_surface_faces(nmar_bundle):
    """TL8 §4: surfaces 190,553 / 59,533 / 98,573 faces (one sheet per model)."""
    f = nmar_bundle["faces"]
    assert f["001"] == 190_553
    assert f["002"] == 59_533
    assert f["003"] == 98_573


def test_tl8_nmar_ewk_counts(nmar_bundle, tmp_path):
    """TL8 §4: EWK-001 19 groups; EWK-002 368 (≥0.5 m) / 135 (≥1.0 m); EWK-102 135; EWK-003; MIDP."""
    o = nmar_bundle["out"]
    tins = nmar_bundle["tins"]
    root = nmar_bundle["root"]
    rules = _rules_ewk_active(tmp_path)

    models = []
    for key, role, title in (
        ("001", "existing", "NGL"),
        ("002", "design_grading", "Site Grading"),
        ("003", "road_top", "Roads Surface"),
    ):
        tin = load(tins[key])
        num = tin.get("document_no") or nmar_bundle["paths"][key].stem
        models.append({
            "number": num, "title": title, "role": role,
            "formats": ["IFC", "XML"], "stage": "30% Concept Design", "milestone": 30,
        })
    midp = write(tmp_path / "midp.json", {"models": models})

    surf = tmp_path / "surfaces.json"
    run("build_surfaces.py",
        "--surface", f"existing={tins['001']}",
        "--surface", f"design_grading={tins['002']}",
        "--surface", f"road_top={tins['003']}",
        "--midp", midp, "--site", "NMAR", "--out", surf)

    surf_doc = load(surf)
    assert surf_doc["midp_schedules_carriers_at_30"] is True
    model_missing = [m for m in surf_doc["midp_missing"] if m.get("reason") == "model not received"]
    assert model_missing == [], model_missing
    xml_miss = [m for m in surf_doc["midp_missing"] if m.get("reason") == "format missing"]
    # §4: XML format missing for …-90000-003 (road)
    road_num = models[2]["number"]
    assert any(
        (m.get("deliverable") == road_num or "90000-003" in (m.get("deliverable") or ""))
        and any(f.lower() == "xml" for f in m.get("formats_missing") or [])
        for m in xml_miss
    ), xml_miss

    run("check_slopes.py", "--surfaces", surf, "--rules", rules,
        "--stage", "30% Concept Design", "--site", "NMAR", "--out", tmp_path / "sl.json",
        timeout=120)
    sl = load(tmp_path / "sl.json")
    assert len(sl.get("ewk001_groups") or []) == 19
    g002 = sl.get("ewk002_groups") or []
    assert len(g002) == 368
    assert sum(1 for g in g002 if g.get("height_m", 0) >= 1.0) == 135

    plots = list(root.rglob("*ROD-DWG-00003-001*.dwg.json")) + list(root.rglob("*00003-001*.dwg.json"))
    dr_args = ["--surfaces", surf, "--rules", rules, "--stage", "30% Concept Design",
               "--site", "NMAR", "--out", tmp_path / "dr.json"]
    if plots:
        dr_args.extend(["--plots", plots[0]])
    run("check_drainage.py", *dr_args, timeout=120)
    dr = load(tmp_path / "dr.json")
    assert dr.get("n_depressions", len(dr.get("depressions") or [])) == 13
    traps = dr.get("traps") or []
    assert sum(1 for t in traps if t.get("area_m2", 0) >= 10) == 0

    run("check_presence.py", "--slopes", tmp_path / "sl.json", "--rules", rules,
        "--stage", "30% Concept Design", "--surfaces", surf,
        "--out", tmp_path / "pr.json", "--required-out", tmp_path / "req.json")
    pr = load(tmp_path / "pr.json")
    assert pr.get("ewk102_locations") == 135 or \
        sum(1 for c in pr["checks"] if c["rule_id"] == "EWK-102") and \
        (pr["checks"][0].get("total_count") == 135)


def test_tl9_nrjs_qrwn_faces(tmp_path):
    """TL9 §4: NRJS grading ~14,939–14,940 IFC faces; QRWN grading 410,186 / road 405,028 < 60 s."""
    root = Path(ROOT)
    nrjs = _find_ifc(root, "*IRO-MOD*00002*.ifc", "*IRO-MOD*Grading*.ifc", "*NRJS*Grading*.ifc")
    qrwn_g = _find_ifc(root, "*ROD-MOD-90000-002*.ifc")
    qrwn_r = _find_ifc(root, "*ROD-MOD-90000-003*.ifc", "*Roads*Surface*.ifc")
    # Disambiguate site by path
    nrjs = [p for p in nrjs if "NRJS" in str(p).upper() or "IRO-MOD" in p.name.upper()]
    qrwn_g = [p for p in qrwn_g if "QRWN" in str(p).upper() or "AQ-" in p.name.upper() or "Al_Qairawan" in str(p)]
    qrwn_r = [p for p in qrwn_r if "QRWN" in str(p).upper() or "AQ-" in p.name.upper() or "Al_Qairawan" in str(p)]

    if not nrjs and not qrwn_g:
        pytest.skip("QRWN/NRJS earthworks IFC not found under LDIP_REVIEW_ROOT")

    if nrjs:
        msg = run("read_ifc_surface.py", "--ifc", nrjs[0], "--out-dir", tmp_path / "nrjs",
                  "--prefix", "nrjs", timeout=180)
        n = msg["surfaces"][0]["faces"]
        assert 14_939 <= n <= 14_940, n

    if qrwn_g:
        t0 = time.perf_counter()
        msg = run("read_ifc_surface.py", "--ifc", qrwn_g[0], "--out-dir", tmp_path / "qg",
                  "--prefix", "qg", timeout=120)
        elapsed = time.perf_counter() - t0
        assert msg["surfaces"][0]["faces"] == 410_186
        assert elapsed < 60.0, elapsed
    if qrwn_r:
        msg = run("read_ifc_surface.py", "--ifc", qrwn_r[0], "--out-dir", tmp_path / "qr",
                  "--prefix", "qr", timeout=120)
        assert msg["surfaces"][0]["faces"] == 405_028
