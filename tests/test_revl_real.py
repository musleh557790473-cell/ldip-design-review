"""Skill 13 real-data tests TL8 / TL9 / TL11 — skipped without LDIP_REVIEW_ROOT.

TL8/TL9 EWK counts: Amendment L-b §8 (DL-21/DL-22 revised), tolerance ±5 %.
TL11: EWK-001 within 5 %; EWK-002 counts within 6 %; largest ten heights within 1 m.
Face counts still from S29 §5.4 / §5.6.
Site-specific globs: NMAR `*-DN-ROD-MOD-*`, QRWN `*-AQ-*`, NRJS `*NRJS*` / `*-AN-*`.
Real packages stay outside Git.
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
SK6 = REPO / "plugin/ldip-design-review/skills/controlling-submittal-package/scripts"
PY = sys.executable


def run(script, *args, timeout=600, skill=SK13):
    p = subprocess.run([PY, str(skill / script), *map(str, args)],
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


def within_pct(got, exp, pct=5.0):
    """Amendment L-b §8 (DL-21/DL-22 revised): |got − exp| / exp ≤ pct/100.

    If counts differ beyond the limit, STOP (do not retune). Default limit is 5 %.
    """
    if exp == 0:
        return got == 0
    return abs(got - exp) / abs(exp) * 100.0 <= pct


def assert_within_pct(got, exp, label, pct=5.0):
    ok = within_pct(got, exp, pct=pct)
    assert ok, (
        f"L-b DL-22 STOP: {label} got {got}, expected {exp} (±{pct}%). "
        f"Do not change the grouping method to fit — report and halt."
    )


def _find_ifc(root: Path, patterns):
    hits = []
    for pat in patterns:
        hits.extend(root.rglob(pat))
    return sorted({p for p in hits if p.suffix.lower() == ".ifc"}, key=lambda p: p.name)


def _pick(ifcs, *needles):
    for n in needles:
        for p in ifcs:
            if n in p.name:
                return p
    return None


def _rules_ewk_active(tmp: Path):
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


def _find_midp_json(root: Path):
    """Prefer skill 6 read_midp output; else a .xlsm for on-the-fly read."""
    cands = list(root.rglob("*midp*.json")) + list(root.rglob("*MIDP*.json"))
    for p in cands:
        try:
            doc = load(p)
        except Exception:
            continue
        if isinstance(doc, dict) and (doc.get("rows") or doc.get("tool", {}).get("name") == "read_midp.py"):
            return p
    xlsm = list(root.rglob("*GEN-LST*.xlsm")) + list(root.rglob("*MIDP*.xlsm"))
    return xlsm[0] if xlsm else None


def _adapt_real_midp(root: Path, tmp: Path, site: str):
    """R8: real MIDP via skill 6 + adapt_midp --site."""
    src = _find_midp_json(root)
    if src is None:
        return None
    midp_json = src
    if src.suffix.lower() in (".xlsm", ".xlsx"):
        midp_json = tmp / "midp_raw.json"
        run("read_midp.py", "--file", src, "--out", midp_json, skill=SK6, timeout=120)
    out = tmp / f"midp_{site}.json"
    run("adapt_midp.py", "--midp", midp_json, "--site", site, "--out", out)
    return out


def _find_inventory(root: Path, tmp: Path, site: str):
    for p in list(root.rglob("files.json")) + list(root.rglob("package.json")):
        try:
            doc = load(p)
        except Exception:
            continue
        if doc.get("files") or doc.get("sheet_register"):
            return p
    # plain list from IFC/DWG under root (lightweight)
    files = []
    for p in root.rglob("*"):
        if p.suffix.lower() in (".ifc", ".dwg", ".nwc", ".dwfx", ".pdf", ".xml"):
            files.append({"path": str(p.relative_to(root)), "name": p.name,
                          "format": p.suffix.lstrip(".").upper(), "number": p.stem})
    if not files:
        return None
    out = tmp / f"inv_{site}.json"
    write(out, {"files": files})
    return out


# --------------------------------------------------------------------------- TL8 NMAR (R8 / R9)
@pytest.fixture(scope="module")
def nmar_bundle(tmp_path_factory):
    root = Path(ROOT)
    # R9: site-specific globs — skip (not fail) when NMAR absent
    ifcs = _find_ifc(root, ["*-DN-ROD-MOD-90000-*.ifc", "*DN*ROD-MOD-90000*.ifc"])
    m001 = _pick(ifcs, "90000-001")
    m002 = _pick(ifcs, "90000-002")
    m003 = _pick(ifcs, "90000-003")
    if not (m001 and m002 and m003):
        pytest.skip("NMAR *-DN-ROD-MOD-90000-001/002/003 IFC not found under LDIP_REVIEW_ROOT")
    o = tmp_path_factory.mktemp("tl8")
    faces, tins, choices = {}, {}, {}
    for key, path in (("001", m001), ("002", m002), ("003", m003)):
        msg = run("read_ifc_surface.py", "--ifc", path, "--out-dir", o / f"ifc_{key}",
                  "--prefix", f"nmar{key}", timeout=180)
        assert len(msg["surfaces"]) == 1, msg
        faces[key] = msg["surfaces"][0]["faces"]
        tins[key] = msg["surfaces"][0]["path"]
        choices[key] = msg["layer_choice"]
    return {"root": root, "out": o, "faces": faces, "tins": tins, "choices": choices,
            "paths": {"001": m001, "002": m002, "003": m003}}


def test_tl8_nmar_surface_faces(nmar_bundle):
    """TL8 §5.4: faces 190,553 / 59,533 / 98,573; layer_choice single / single / thin_solid_split."""
    f, c = nmar_bundle["faces"], nmar_bundle["choices"]
    assert f["001"] == 190_553
    assert f["002"] == 59_533
    assert f["003"] == 98_573
    assert c["001"] == "single_layer"
    assert c["002"] == "single_layer"
    assert c["003"] == "thin_solid_split"


def test_tl8_nmar_ewk_and_midp(nmar_bundle, tmp_path):
    """TL8 §5.4: EWK counts + real MIDP (R8) — XML missing for …-90000-001; one MI."""
    tins = nmar_bundle["tins"]
    root = nmar_bundle["root"]
    rules = _rules_ewk_active(tmp_path)

    midp = _adapt_real_midp(root, tmp_path, "NMAR")
    if midp is None:
        # Fallback compact MIDP mirroring real NMAR schedule (XML on 001, not 003)
        models = []
        for key, role, title, fmts in (
            ("001", "existing", "Roads NGL Model", ["IFC", "DWG", "NWC", "DWFx", "XML"]),
            ("002", "design_grading", "Site Grading Model", ["IFC", "DWG", "NWC", "DWFx"]),
            ("003", "road_top", "Roads Surface Model", ["IFC", "DWG", "NWC", "DWFx"]),
        ):
            tin = load(tins[key])
            models.append({
                "number": tin.get("document_no") or nmar_bundle["paths"][key].stem,
                "title": title, "role": role, "formats": fmts,
                "stage": "30% Concept Design", "milestone": 30,
            })
        midp = write(tmp_path / "midp.json", {"models": models})

    inv = _find_inventory(root, tmp_path, "NMAR")
    surf_args = [
        "--surface", f"existing={tins['001']}",
        "--surface", f"design_grading={tins['002']}",
        "--surface", f"road_top={tins['003']}",
        "--midp", midp, "--site", "NMAR", "--out", tmp_path / "surfaces.json",
    ]
    if inv:
        surf_args.extend(["--inventory", inv])
    run("build_surfaces.py", *surf_args)

    surf_doc = load(tmp_path / "surfaces.json")
    assert surf_doc["midp_schedules_carriers_at_30"] is True
    assert not [m for m in surf_doc["midp_missing"] if m.get("reason") == "model not received"]
    xml_miss = [m for m in surf_doc["midp_missing"]
                if m.get("reason") == "format missing"
                and any(f.lower() == "xml" for f in m.get("formats_missing") or [])]
    # R8: XML missing for …-90000-001 (NGL), not 003
    assert xml_miss, surf_doc["midp_missing"]
    assert any("90000-001" in (m.get("deliverable") or "") for m in xml_miss), xml_miss
    assert not any("90000-003" in (m.get("deliverable") or "") for m in xml_miss)

    run("check_slopes.py", "--surfaces", tmp_path / "surfaces.json", "--rules", rules,
        "--stage", "30% Concept Design", "--site", "NMAR", "--out", tmp_path / "sl.json",
        timeout=120)
    sl = load(tmp_path / "sl.json")
    # Amendment L-b §8 (DL-21/DL-22 revised): NMAR 18 · 313 / 131 · EWK-102 130 (±5 %)
    g001 = sl.get("ewk001_groups") or []
    g002 = sl.get("ewk002_groups") or []
    n_ge1 = sum(1 for g in g002 if g.get("height_m", 0) >= 1.0)
    assert_within_pct(len(g001), 18, "NMAR EWK-001")
    assert_within_pct(len(g002), 313, "NMAR EWK-002 ≥0.5 m")
    assert_within_pct(n_ge1, 131, "NMAR EWK-002 ≥1.0 m")

    plots = list(root.rglob("*DN*ROD-DWG-00003*.dwg.json")) + list(root.rglob("*ROD-DWG-00003-001*.dwg.json"))
    dr_args = ["--surfaces", tmp_path / "surfaces.json", "--rules", rules,
               "--stage", "30% Concept Design", "--site", "NMAR", "--out", tmp_path / "dr.json"]
    if plots:
        dr_args.extend(["--plots", plots[0]])
    run("check_drainage.py", *dr_args, timeout=180)
    dr = load(tmp_path / "dr.json")
    assert dr.get("n_depressions", 0) == 13
    assert sum(1 for t in (dr.get("traps") or []) if t.get("area_m2", 0) >= 10) == 0

    run("check_presence.py", "--slopes", tmp_path / "sl.json", "--rules", rules,
        "--stage", "30% Concept Design", "--surfaces", tmp_path / "surfaces.json",
        "--out", tmp_path / "pr.json", "--required-out", tmp_path / "req.json")
    pr = load(tmp_path / "pr.json")
    assert_within_pct(pr.get("ewk102_locations") or 0, 130, "NMAR EWK-102 locations")
    req = load(tmp_path / "req.json")["items"]
    # R5: at most one MIDP MI item
    midp_items = [i for i in req if i.get("key") == "midp:earthworks"]
    assert len(midp_items) <= 1


# --------------------------------------------------------------------------- TL9 NRJS / QRWN (R9 site globs + Amendment L-b §8)
def test_tl9_nrjs_qrwn(tmp_path):
    """TL9: site-specific IFC faces + Amendment L-b §8 EWK counts (DL-22 revised, ±5 %)."""
    root = Path(ROOT)
    nrjs = _find_ifc(root, ["*NRJS-IRO-MOD*.ifc", "*-AN-ROD-MOD-90000-*.ifc", "*IRO-MOD-00-0000*.ifc"])
    nrjs = [p for p in nrjs if "NRJS" in p.name.upper() or "-AN-" in p.name.upper()
            or "IRO-MOD" in p.name.upper()]
    qrwn = _find_ifc(root, ["*-AQ-ROD-MOD-90000-*.ifc", "*AQ*ROD-MOD-90000*.ifc"])

    if not nrjs and not qrwn:
        pytest.skip("QRWN *-AQ-* / NRJS IFC not found under LDIP_REVIEW_ROOT")

    rules = _rules_ewk_active(tmp_path)

    if nrjs:
        g = _pick(nrjs, "00002", "90000-002") or nrjs[0]
        ngl = _pick(nrjs, "00001", "90000-001")
        road = _pick(nrjs, "00003", "90000-003")
        msg = run("read_ifc_surface.py", "--ifc", g, "--out-dir", tmp_path / "nrjs_g",
                  "--prefix", "njg", timeout=180)
        assert msg["surfaces"][0]["faces"] in (14_939, 14_940)
        design_tin = msg["surfaces"][0]["path"]
        road_tin = None
        if ngl:
            msg = run("read_ifc_surface.py", "--ifc", ngl, "--out-dir", tmp_path / "nrjs_n",
                      "--prefix", "njn", timeout=180)
            assert msg["surfaces"][0]["faces"] == 3_027
        if road:
            msg = run("read_ifc_surface.py", "--ifc", road, "--out-dir", tmp_path / "nrjs_r",
                      "--prefix", "njr", timeout=180)
            assert msg["surfaces"][0]["faces"] == 6_744
            road_tin = msg["surfaces"][0]["path"]
        # Amendment L-b §8 (DL-22 revised): NRJS 9 · 43 / 20 (EWK-102 20) (±5 %)
        # — needs paved footprint from road_top
        if road_tin:
            surf_args = [
                "--surface", f"design_grading={design_tin}",
                "--surface", f"road_top={road_tin}",
                "--site", "NRJS", "--out", tmp_path / "nrjs_surfaces.json",
            ]
            run("build_surfaces.py", *surf_args)
            run("check_slopes.py", "--surfaces", tmp_path / "nrjs_surfaces.json", "--rules", rules,
                "--stage", "30% Concept Design", "--site", "NRJS",
                "--out", tmp_path / "nrjs_sl.json", timeout=180)
            sl = load(tmp_path / "nrjs_sl.json")
            g001 = sl.get("ewk001_groups") or []
            g002 = sl.get("ewk002_groups") or []
            n_ge1 = sum(1 for x in g002 if x.get("height_m", 0) >= 1.0)
            assert_within_pct(len(g001), 9, "NRJS EWK-001")
            assert_within_pct(len(g002), 43, "NRJS EWK-002 ≥0.5 m")
            assert_within_pct(n_ge1, 20, "NRJS EWK-002 ≥1.0 m")

    if qrwn:
        g = _pick(qrwn, "90000-002")
        r = _pick(qrwn, "90000-003")
        n = _pick(qrwn, "90000-001")
        design_tin = road_tin = None
        if g:
            t0 = time.perf_counter()
            msg = run("read_ifc_surface.py", "--ifc", g, "--out-dir", tmp_path / "qg",
                      "--prefix", "qg", timeout=120)
            assert msg["surfaces"][0]["faces"] == 410_186
            assert time.perf_counter() - t0 < 60.0
            assert msg["layer_choice"] == "single_layer"
            design_tin = msg["surfaces"][0]["path"]
        if r:
            msg = run("read_ifc_surface.py", "--ifc", r, "--out-dir", tmp_path / "qr",
                      "--prefix", "qr", timeout=120)
            assert msg["surfaces"][0]["faces"] == 405_028
            road_tin = msg["surfaces"][0]["path"]
        if n:
            msg = run("read_ifc_surface.py", "--ifc", n, "--out-dir", tmp_path / "qn",
                      "--prefix", "qn", timeout=120)
            # §5.6: 210,521 points / 416,717 faces
            assert msg["surfaces"][0]["faces"] == 416_717
        if not g and not r:
            pytest.skip("QRWN 002/003 IFC not found (R9)")
        # Amendment L-b §8 (DL-22 revised): QRWN 227 · 151 / 118 (±5 %)
        if design_tin and road_tin:
            run("build_surfaces.py",
                "--surface", f"design_grading={design_tin}",
                "--surface", f"road_top={road_tin}",
                "--site", "QRWN", "--out", tmp_path / "qrwn_surfaces.json")
            run("check_slopes.py", "--surfaces", tmp_path / "qrwn_surfaces.json", "--rules", rules,
                "--stage", "30% Concept Design", "--site", "QRWN",
                "--out", tmp_path / "qrwn_sl.json", timeout=300)
            sl = load(tmp_path / "qrwn_sl.json")
            g001 = sl.get("ewk001_groups") or []
            g002 = sl.get("ewk002_groups") or []
            n_ge1 = sum(1 for x in g002 if x.get("height_m", 0) >= 1.0)
            assert_within_pct(len(g001), 227, "QRWN EWK-001")
            assert_within_pct(len(g002), 151, "QRWN EWK-002 ≥0.5 m")
            assert_within_pct(n_ge1, 118, "QRWN EWK-002 ≥1.0 m")


# --------------------------------------------------------------------------- TL11 format invariance (Amendment L-b §8; DL-21 revised)
def test_tl11_qrwn_ifc_vs_landxml(tmp_path):
    """TL11: QRWN IFC 002 vs LandXML — EWK-001 within 5 %; EWK-002 counts within 6 %.

    Amendment L-b §8 (DL-21 revised). Largest ten heights stay within 1 m.
    """
    root = Path(ROOT)
    qrwn = _find_ifc(root, ["*-AQ-ROD-MOD-90000-*.ifc", "*AQ*ROD-MOD-90000*.ifc"])
    g_ifc = _pick(qrwn, "90000-002")
    r_ifc = _pick(qrwn, "90000-003")
    xml_hits = sorted(root.rglob("LDIP-RCRC-LAI-LDIP-01-CIV-DWG-00002-01.xml"))
    if not xml_hits:
        xml_hits = sorted(p for p in root.rglob("*.xml")
                          if "00002" in p.name and ("CIV-DWG" in p.name or "LAI" in p.name))
    if not (g_ifc and r_ifc and xml_hits):
        pytest.skip("QRWN IFC 002/003 or LandXML 00002-01 not found under LDIP_REVIEW_ROOT")

    rules = _rules_ewk_active(tmp_path)
    msg_g = run("read_ifc_surface.py", "--ifc", g_ifc, "--out-dir", tmp_path / "tl11_g",
                "--prefix", "qg", timeout=120)
    msg_r = run("read_ifc_surface.py", "--ifc", r_ifc, "--out-dir", tmp_path / "tl11_r",
                "--prefix", "qr", timeout=120)
    ifc_design = msg_g["surfaces"][0]["path"]
    ifc_road = msg_r["surfaces"][0]["path"]

    # skill 3 LandXML → surface-N.json
    sk3 = REPO / "plugin/ldip-design-review/skills/parsing-landxml/scripts"
    xml_path = xml_hits[0]
    xml_batch = tmp_path / "tl11_xml"
    xml_batch.mkdir()
    xml_json = xml_batch / f"{xml_path.name}.json"
    parse = sk3 / "parse_landxml.py"
    if not parse.is_file():
        pytest.skip("skill 3 parse_landxml.py not available")
    run("parse_landxml.py", "--in", xml_path, "--out", xml_json,
        "--provenance", "aicc_export", skill=sk3, timeout=600)
    xml_tins = sorted(xml_batch.glob("*.surface-*.json"))
    if not xml_tins:
        pytest.skip("LandXML produced no surface-N.json for TL11")
    xml_design = xml_tins[0]

    def _counts(design, road, tag):
        surf = tmp_path / f"tl11_{tag}_surfaces.json"
        run("build_surfaces.py",
            "--surface", f"design_grading={design}",
            "--surface", f"road_top={road}",
            "--site", "QRWN", "--out", surf)
        out = tmp_path / f"tl11_{tag}_sl.json"
        run("check_slopes.py", "--surfaces", surf, "--rules", rules,
            "--stage", "30% Concept Design", "--site", "QRWN", "--out", out, timeout=300)
        sl = load(out)
        g002 = sl.get("ewk002_groups") or []
        return {
            "ewk001": len(sl.get("ewk001_groups") or []),
            "ewk002": len(g002),
            "ewk002_1m": sum(1 for x in g002 if x.get("height_m", 0) >= 1.0),
            "top10": [(round(x.get("height_m", 0), 3),
                       round(x.get("area_m2", 0), 1),
                       tuple(x.get("centroid_en") or [])) for x in g002[:10]],
        }

    ifc_c = _counts(ifc_design, ifc_road, "ifc")
    xml_c = _counts(xml_design, ifc_road, "xml")  # same paved footprint from IFC road

    # Amendment L-b §8 (DL-21 revised): 5 % on EWK-001; 6 % on EWK-002 counts
    limits = {"ewk001": 5.0, "ewk002": 6.0, "ewk002_1m": 6.0}
    for key in ("ewk001", "ewk002", "ewk002_1m"):
        a, b = ifc_c[key], xml_c[key]
        base = max(a, b)
        if base == 0:
            assert a == b == 0
            continue
        pct = abs(a - b) / base * 100.0
        limit = limits[key]
        assert pct <= limit, (
            f"TL11 DL-21 STOP: {key} IFC={a} LandXML={b} differ by {pct:.1f}% (>{limit:g}%). "
            f"Do not change the method to fit."
        )
    # largest-ten items within 1 m height (Amendment L-b §8; DL-21 revised)
    for i, ((h1, _a1, _c1), (h2, _a2, _c2)) in enumerate(zip(ifc_c["top10"], xml_c["top10"])):
        assert abs(h1 - h2) <= 1.0, f"TL11 top10[{i}] height IFC={h1} LandXML={h2}"
