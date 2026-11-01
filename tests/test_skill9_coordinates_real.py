"""Skill 9 0.3.0 real-data gates T9-R1…T9-R5 (SPC-0006 Rev P §4.18.5).

Env (explicit paths; same idea as skill 14):
  LDIP_SKILL9_QRWN_DWG / LDIP_SKILL9_NRJS_DWG / LDIP_SKILL9_NMAR_DWG
  LDIP_SKILL9_QRWN_LANDXML
  LDIP_SKILL9_SITES_JSON   — S7 (NMAR) / S8 (NRJS) corner polygons; owner file outside Git
  LDIP_SKILL9_<SITE>_INGESTION  — optional pre-built *.dwg.json (else convert+extract)
  LDIP_REVIEW_ROOT / LDIP_REVIEW — rulebook root; when set, a missing input **fails** (not skip)

No Parsons file or rule value is committed to Git — asserts use counts / patterns only.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SC = REPO / "plugin/ldip-design-review/skills/checking-cadastral-geometry/scripts"
SK8 = REPO / "plugin/ldip-design-review/skills/applying-ldip-rulebook/scripts"
SK1 = REPO / "plugin/ldip-design-review/skills/extracting-dwg-data/scripts"
SK3 = REPO / "plugin/ldip-design-review/skills/parsing-landxml/scripts"
PY = sys.executable

ROOT = os.environ.get("LDIP_REVIEW_ROOT") or os.environ.get("LDIP_REVIEW")
ENV = {
    "QRWN_DWG": "LDIP_SKILL9_QRWN_DWG",
    "NRJS_DWG": "LDIP_SKILL9_NRJS_DWG",
    "NMAR_DWG": "LDIP_SKILL9_NMAR_DWG",
    "QRWN_LANDXML": "LDIP_SKILL9_QRWN_LANDXML",
    "SITES_JSON": "LDIP_SKILL9_SITES_JSON",
    "QRWN_ING": "LDIP_SKILL9_QRWN_INGESTION",
    "NRJS_ING": "LDIP_SKILL9_NRJS_INGESTION",
    "NMAR_ING": "LDIP_SKILL9_NMAR_INGESTION",
}
_HAS_ANY = bool(ROOT) or any(os.environ.get(v) for v in ENV.values())

pytestmark = pytest.mark.skipif(
    not _HAS_ANY,
    reason="LDIP_SKILL9_* / LDIP_REVIEW_ROOT not set — real DWG / LandXML / rules stay out of Git",
)


def run(script, *args, skill=SC):
    p = subprocess.run([PY, str(skill / script), *map(str, args)],
                       capture_output=True, text=True, timeout=1800)
    out = p.stdout.strip().splitlines()
    msg = json.loads(out[-1]) if out else {"stderr": p.stderr[-3000:]}
    assert p.returncode == 0, msg
    return msg


def _fail_or_skip(msg: str):
    if ROOT:
        pytest.fail(msg)
    pytest.skip(msg)


def _env_file(name: str) -> Path | None:
    p = os.environ.get(name)
    if p and Path(p).is_file():
        return Path(p)
    return None


def _require_file(env_name: str) -> Path:
    p = _env_file(env_name)
    if p is not None:
        return p
    _fail_or_skip(f"{env_name} missing/invalid"
                  + (" while LDIP_REVIEW_ROOT is set" if ROOT else ""))


def _root() -> Path | None:
    return Path(ROOT) if ROOT and Path(ROOT).is_dir() else None


def _find(root: Path, patterns):
    hits = []
    for pat in patterns:
        hits.extend(root.rglob(pat))
    return sorted({p for p in hits if p.is_file()})


def _rulebook_path() -> Path:
    root = _root()
    if root is None:
        _fail_or_skip("LDIP_REVIEW_ROOT not set — need owner rulebook for real gates")
    rb_dir = root / "rulebook"
    if rb_dir.is_dir():
        pref = list(rb_dir.glob("rules_v8.json")) or list(rb_dir.glob("rules_v*.json"))
        if pref:
            return sorted(pref)[-1]
    hits = _find(root, ["**/rules_v8.json", "**/rules_v*.json"])
    if hits:
        # prefer v8 when present
        v8 = [h for h in hits if h.name == "rules_v8.json"]
        return v8[-1] if v8 else hits[-1]
    pytest.fail("rules_v8 / rules_v* not found under LDIP_REVIEW_ROOT")


def _load_sites():
    path = _require_file(ENV["SITES_JSON"])
    doc = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(doc, dict):
        pytest.fail(f"{ENV['SITES_JSON']} must be a JSON object")
    return doc, path


def _site_polygon(sites: dict, *keys):
    """Return {polygon: [[E,N],...]} from S7/S8/SITE keys (list or {polygon|corners})."""
    for key in keys:
        raw = sites.get(key)
        if raw is None:
            continue
        if isinstance(raw, dict):
            verts = raw.get("polygon") or raw.get("corners") or raw.get("vertices")
            if verts is None and isinstance(raw.get(key), list):
                verts = raw[key]
        elif isinstance(raw, list):
            verts = raw
        else:
            continue
        if verts and len(verts) >= 3:
            return {"polygon": [list(map(float, v[:2])) for v in verts]}
    return None


def _set_param(rules_doc, rule_id, name, value, tag="PROPOSED", source_id="SRC-11", clause="§4.18 test"):
    for r in rules_doc["rules"]:
        if r["rule_id"] == rule_id:
            r.setdefault("parameters", {})[name] = {
                "value": value, "tag": tag, "source_id": source_id, "clause": clause, "blocking": False,
            }
            return r
    raise KeyError(rule_id)


def _ensure_buffer(rules):
    for r in rules["rules"]:
        if r["rule_id"] == "GIS-008" and "envelope_buffer" not in r.get("parameters", {}):
            r["parameters"]["envelope_buffer"] = {
                "value": 25.0, "tag": "PROPOSED", "source_id": "SRC-11", "clause": "§2.5", "blocking": False,
            }


def _context_layers_for(site):
    common = ["G-KEYP", "G-TTLB", "sheet", "fram", "Defpoints"]
    extra = {"QRWN": ["Al_Qairawan_Parcel"], "NRJS": ["Parcels_2005"], "NMAR": []}.get(site, [])
    return extra + common


def _get_rules_gis(tmp_path, rb_path):
    out = tmp_path / "rules_GIS.json"
    run("get_rules.py", "--rulebook", rb_path, "--agent", "GIS", "--out", out, skill=SK8)
    return json.loads(out.read_text(encoding="utf-8"))


def _scratch_rulebook_gis017(tmp_path, rb_path):
    """Copy rulebook; set GIS-017 advisory + max_distance_tbm 500 (schema source_id/clause)."""
    book = json.loads(Path(rb_path).read_text(encoding="utf-8"))
    found = False
    for r in book.get("rules", []):
        if r.get("rule_id") != "GIS-017":
            continue
        found = True
        r["state"] = "advisory"
        r.setdefault("parameters", {})["max_distance_tbm"] = {
            "value": 500.0, "unit": "m", "tag": "PROPOSED",
            "source_id": "SRC-11", "clause": "§4.18 DP-8", "blocking": False,
            "note": "scratch for T9-R4; AICC Survey to confirm before rules_v9 activation",
        }
    if not found:
        pytest.fail("GIS-017 not in rulebook — cannot activate for T9-R4")
    out = tmp_path / "rulebook_gis017_scratch.json"
    out.write_text(json.dumps(book), encoding="utf-8")
    return out


def _ensure_ingestion(tmp_path, dwg_path: Path, site: str):
    """Return *.dwg.json — env ingestion, reuse beside DWG, or convert(ezdwg)+extract(--linework)."""
    ing_env = ENV.get(f"{site}_ING")
    if ing_env:
        p = _env_file(ing_env)
        if p is not None:
            return p
    cand = list(dwg_path.parent.glob(dwg_path.stem + "*.dwg.json"))
    root = _root()
    if root is not None:
        cand += _find(root, [f"**/{dwg_path.stem}.dwg.json", f"**/*{dwg_path.stem}*.dwg.json"])
    if cand:
        return cand[0]
    convert = SK1 / "convert.py"
    extract = SK1 / "extract_dxf.py"
    if not convert.is_file() or not extract.is_file():
        _fail_or_skip(f"no ingestion for {dwg_path.name} and skill 1 convert/extract missing")
    work = tmp_path / f"ing_{site}_{dwg_path.stem}"
    work.mkdir(parents=True, exist_ok=True)
    dxf = work / f"{dwg_path.stem}.dxf"
    log = work / f"{dwg_path.stem}.convert.json"
    out = work / f"{dwg_path.stem}.dwg.json"
    p = subprocess.run(
        [PY, str(convert), "--engine", "ezdwg", "--dwg", str(dwg_path),
         "--out", str(dxf), "--log", str(log)],
        capture_output=True, text=True, timeout=1800)
    if p.returncode != 0:
        pytest.fail(f"convert.py ezdwg failed for {dwg_path.name}: {p.stderr[-800:] or p.stdout[-800:]}")
    p = subprocess.run(
        [PY, str(extract), "--dxf", str(dxf), "--dwg", str(dwg_path),
         "--convert-log", str(log), "--out", str(out), "--source-path", str(dwg_path), "--linework"],
        capture_output=True, text=True, timeout=1800)
    if p.returncode != 0:
        pytest.fail(f"extract_dxf.py failed for {dwg_path.name}: {p.stderr[-800:] or p.stdout[-800:]}")
    return out


def _sum_context_prefix(profile: dict, prefix: str) -> int:
    """Sum profile_context_outside counts whose key equals or starts with prefix (xref chain)."""
    pref = prefix.casefold()
    total = 0
    for k, n in (profile or {}).items():
        kk = str(k).casefold()
        if kk == pref or kk.startswith(pref):
            total += int(n)
    return total


def _pattern_count_contains(patterns, prefix: str, needle: str) -> int:
    """Sum counts for patterns starting with prefix whose remainder contains needle."""
    total = 0
    for p in patterns:
        name = p.get("pattern") or ""
        if not name.startswith(prefix):
            continue
        rest = name[len(prefix):]
        if needle in rest:
            total += int(p.get("count") or 0)
    return total


def _official_nmar_area(rules) -> float | None:
    for r in rules["rules"]:
        if r["rule_id"] != "GIS-011":
            continue
        p = (r.get("parameters") or {}).get("official_site_area") or {}
        val = p.get("value") or {}
        nmar = val.get("NMAR") if isinstance(val, dict) else None
        if isinstance(nmar, dict) and nmar.get("nature") is not None:
            return float(nmar["nature"])
        if isinstance(nmar, (int, float)):
            return float(nmar)
    return None


def _poly_by_handle(ing_path, handle: str):
    h = str(handle).upper()
    data = json.loads(Path(ing_path).read_text(encoding="utf-8"))
    for p in data.get("content", {}).get("polylines", []):
        if str(p.get("handle", "")).upper() == h:
            return {"polygon": [list(map(float, v[:2])) for v in p["vertices"]]}
    return None


def _nmar_envelope_from_ing_or_sites(ing_path, rules, sites):
    """Closed polyline whose area ≈ official NMAR nature (±1 m²), else S7 corners from sites JSON."""
    want = _official_nmar_area(rules)
    data = json.loads(Path(ing_path).read_text(encoding="utf-8"))
    if want is not None:
        best = None
        for p in data.get("content", {}).get("polylines", []):
            a = float(p.get("area") or 0)
            if abs(a - want) <= 1.0:
                if best is None or abs(a - want) < abs(best[0] - want):
                    best = (a, p)
        if best is not None:
            return {"polygon": [list(map(float, v[:2])) for v in best[1]["vertices"]]}
    env = _site_polygon(sites, "S7", "NMAR")
    if env is not None:
        return env
    return None


def _qrwn_envelope(ing_path, sites):
    """QRWN envelope from sites JSON when present; else accepted DWG handle 6B890 (scratch only)."""
    env = _site_polygon(sites, "QRWN", "S_QRWN")
    if env is not None:
        return env
    return _poly_by_handle(ing_path, "6B890")


def _landxml_ingestion(tmp_path, xml_or_json: Path) -> Path:
    """Return LandXML ingestion JSON; parse with --in when given a raw .xml (do not alter content)."""
    p = xml_or_json
    if p.suffix.lower() == ".json" or str(p).endswith(".xml.json"):
        return p
    parse = SK3 / "parse_landxml.py"
    if not parse.is_file():
        pytest.fail("parse_landxml.py missing")
    out_dir = tmp_path / "landxml_batch"
    out_dir.mkdir(parents=True, exist_ok=True)
    # skill 3: --in + --out batch dir; provenance from the file name / employer for RCRC survey
    rc = subprocess.run(
        [PY, str(parse), "--in", str(p), "--out", str(out_dir), "--provenance", "employer"],
        capture_output=True, text=True, timeout=1800)
    if rc.returncode != 0:
        pytest.fail(f"parse_landxml failed: {rc.stderr[-800:] or rc.stdout[-800:]}")
    produced = sorted(out_dir.glob("*.xml.json")) or sorted(out_dir.rglob("*.json"))
    if not produced:
        pytest.fail("parse_landxml produced no JSON")
    return produced[0]


# ------------------------------------------------------------------ T9-R1
def test_t9_r1_qrwn_11000_context_and_envelope(tmp_path):
    """T9-R1: QRWN 11000 — Al_Qairawan_Parcel* → context (sum prefix); units pass; CRS not_declared."""
    dwg = _require_file(ENV["QRWN_DWG"])
    rb = _rulebook_path()
    sites, _ = _load_sites()
    ing = _ensure_ingestion(tmp_path, dwg, "QRWN")
    rules = _get_rules_gis(tmp_path, rb)
    env = _qrwn_envelope(ing, sites)
    if env is None:
        pytest.fail("QRWN envelope missing (sites JSON QRWN / handle 6B890)")
    _set_param(rules, "GIS-008", "site_envelope", {"QRWN": env})
    _set_param(rules, "GIS-008", "context_layers", {"QRWN": _context_layers_for("QRWN")})
    _ensure_buffer(rules)
    rules_path = tmp_path / "scratch_rules.json"
    rules_path.write_text(json.dumps(rules), encoding="utf-8")
    run("check_crs.py", "--ingestion", ing, "--rules", rules_path, "--site", "QRWN",
        "--out", tmp_path / "gis008.json")
    out = json.loads((tmp_path / "gis008.json").read_text())
    assert out["files"][0]["units"] == "pass"
    assert out["files"][0]["crs"] == "not_declared"
    ctx = out["files"][0].get("profile_context_outside") or {}
    ctx_n = _sum_context_prefix(ctx, "Al_Qairawan_Parcel")
    assert ctx_n >= 3000, f"context prefix sum {ctx_n} from {ctx}"
    for p in out.get("patterns", []):
        layer = p["pattern"].split("/", 1)[-1]
        assert not layer.casefold().startswith("al_qairawan_parcel")
        assert layer not in ("G-KEYP", "G-TTLB", "Defpoints")


# ------------------------------------------------------------------ T9-R2
def test_t9_r2_nrjs_displaced_plot_layers(tmp_path):
    """T9-R2: NRJS — S8 envelope; displaced Plot (25x12)/(30x10)/AN-Symbol; (20x15) outside."""
    dwg = _require_file(ENV["NRJS_DWG"])
    rb = _rulebook_path()
    sites, _ = _load_sites()
    env = _site_polygon(sites, "S8", "NRJS")
    if env is None:
        pytest.fail("S8 / NRJS envelope missing in LDIP_SKILL9_SITES_JSON")
    ing = _ensure_ingestion(tmp_path, dwg, "NRJS")
    rules = _get_rules_gis(tmp_path, rb)
    _set_param(rules, "GIS-008", "site_envelope", {"NRJS": env})
    _set_param(rules, "GIS-008", "known_displacements", {"NRJS": [-1107.56, 34.31]},
               source_id="SRC-11", clause="§4.6")
    _set_param(rules, "GIS-008", "displacement_min_entities", 20)
    _set_param(rules, "GIS-008", "context_layers", {"NRJS": _context_layers_for("NRJS")})
    _ensure_buffer(rules)
    rules_path = tmp_path / "scratch_rules.json"
    rules_path.write_text(json.dumps(rules), encoding="utf-8")
    run("check_crs.py", "--ingestion", ing, "--rules", rules_path, "--site", "NRJS",
        "--out", tmp_path / "gis008.json")
    out = json.loads((tmp_path / "gis008.json").read_text())
    pats = out.get("patterns") or []
    # layer names contain 2D-PR-Plot (…) / 2D-PR-AN-Symbol after $ chains
    assert _pattern_count_contains(pats, "displaced_layer_group/", "2D-PR-Plot (25x12)") == 67
    assert _pattern_count_contains(pats, "displaced_layer_group/", "2D-PR-Plot (30x10)") == 34
    assert _pattern_count_contains(pats, "displaced_layer_group/", "2D-PR-AN-Symbol") == 112
    # (20x15) has only 11 (< displacement_min_entities 20) → outside, not displaced
    assert _pattern_count_contains(pats, "displaced_layer_group/", "2D-PR-Plot (20x15)") == 0
    assert _pattern_count_contains(pats, "outside_site_envelope/", "2D-PR-Plot (20x15)") == 11


# ------------------------------------------------------------------ T9-R3
def test_t9_r3_nmar_envelope_and_tbm(tmp_path):
    """T9-R3: NMAR TEL — envelope from official area polyline or S7; site TBM inside True."""
    dwg = _require_file(ENV["NMAR_DWG"])
    rb = _rulebook_path()
    sites, _ = _load_sites()
    ing = _ensure_ingestion(tmp_path, dwg, "NMAR")
    rules = _get_rules_gis(tmp_path, rb)
    env = _nmar_envelope_from_ing_or_sites(ing, rules, sites)
    if env is None:
        pytest.fail("NMAR envelope: no polyline ≈ official_site_area.NMAR and no S7 in sites JSON")
    _set_param(rules, "GIS-008", "site_envelope", {"NMAR": env})
    _set_param(rules, "GIS-008", "context_layers", {"NMAR": _context_layers_for("NMAR")})
    _set_param(rules, "GIS-011", "site_boundary", {"NMAR": env["polygon"]})
    _ensure_buffer(rules)
    rules_path = tmp_path / "scratch_rules.json"
    rules_path.write_text(json.dumps(rules), encoding="utf-8")
    run("check_crs.py", "--ingestion", ing, "--rules", rules_path, "--site", "NMAR",
        "--out", tmp_path / "gis008.json")
    out = json.loads((tmp_path / "gis008.json").read_text())
    assert out["files"][0]["units"] == "pass"
    ctrl = out.get("control") or []
    tbm = [c for c in ctrl if c.get("role") == "site_tbm"]
    assert tbm, "T9-R3 expected site_tbm in control[]"
    assert tbm[0]["inside"] is True
    outside = [p for p in out["patterns"] if p["pattern"].startswith("outside_site_envelope/")]
    assert outside, "T9-R3 expected at least one outside_site_envelope pattern (legend/origin)"


# ------------------------------------------------------------------ T9-R4
def test_t9_r4_gis017_nrjs_fail_nmar_pass(tmp_path):
    """T9-R4: scratch rulebook GIS-017 advisory + 500 m; NRJS no_site_tbm; NMAR pass."""
    rb = _rulebook_path()
    sites, _ = _load_sites()
    scratch_rb = _scratch_rulebook_gis017(tmp_path, rb)
    rules = _get_rules_gis(tmp_path, scratch_rb)
    # envelopes from S7/S8 for boundary distance
    env_map = {}
    s8 = _site_polygon(sites, "S8", "NRJS")
    s7 = _site_polygon(sites, "S7", "NMAR")
    if s8:
        env_map["NRJS"] = s8
    if s7:
        env_map["NMAR"] = s7
    if "NRJS" not in env_map or "NMAR" not in env_map:
        pytest.fail("T9-R4 needs S8 (NRJS) and S7 (NMAR) in LDIP_SKILL9_SITES_JSON")
    _set_param(rules, "GIS-008", "site_envelope", env_map)
    _set_param(rules, "GIS-011", "site_boundary", {
        "NRJS": env_map["NRJS"]["polygon"], "NMAR": env_map["NMAR"]["polygon"],
    })
    _ensure_buffer(rules)
    # GIS-017 must be present after get_rules from the scratch rulebook
    if not any(r["rule_id"] == "GIS-017" for r in rules["rules"]):
        pytest.fail("GIS-017 missing after get_rules on scratch rulebook")
    rules_path = tmp_path / "scratch_rules.json"
    rules_path.write_text(json.dumps(rules), encoding="utf-8")

    msg = run("check_control.py", "--rules", rules_path, "--site", "NRJS", "--out", tmp_path / "nrjs.json")
    out = json.loads((tmp_path / "nrjs.json").read_text())
    assert msg["result"] == "fail", out
    assert out["patterns"][0]["pattern"] == "no_site_tbm"

    msg = run("check_control.py", "--rules", rules_path, "--site", "NMAR", "--out", tmp_path / "nmar.json")
    out = json.loads((tmp_path / "nmar.json").read_text())
    assert msg["result"] == "pass", out


# ------------------------------------------------------------------ T9-R5
def test_t9_r5_qrwn_egl_landxml_coverage(tmp_path):
    """T9-R5: QRWN RCRC LandXML — coverage pass; CRS normalised; do not alter inputs."""
    xml = _require_file(ENV["QRWN_LANDXML"])
    rb = _rulebook_path()
    sites, _ = _load_sites()
    # QRWN envelope: sites JSON or from the QRWN DWG handle (scratch rules only)
    env = _site_polygon(sites, "QRWN", "S_QRWN")
    if env is None:
        dwg = _env_file(ENV["QRWN_DWG"])
        if dwg is None:
            pytest.fail("T9-R5 needs QRWN in sites JSON or LDIP_SKILL9_QRWN_DWG for envelope handle")
        dwg_ing = _ensure_ingestion(tmp_path, dwg, "QRWN")
        env = _poly_by_handle(dwg_ing, "6B890")
    if env is None:
        pytest.fail("QRWN envelope missing for T9-R5")
    ing_path = _landxml_ingestion(tmp_path, xml)
    rules = _get_rules_gis(tmp_path, rb)
    _set_param(rules, "GIS-008", "site_envelope", {"QRWN": env})
    _set_param(rules, "GIS-011", "site_boundary", {"QRWN": env["polygon"]})
    _ensure_buffer(rules)
    rules_path = tmp_path / "scratch_rules.json"
    rules_path.write_text(json.dumps(rules), encoding="utf-8")
    msg = run("check_crs.py", "--ingestion", ing_path, "--rules", rules_path, "--site", "QRWN",
              "--out", tmp_path / "gis008.json")
    out = json.loads((tmp_path / "gis008.json").read_text())
    assert out["files"][0].get("envelope") == "pass", out
    # B2: LandXML epsg_code 32638 must match rules EPSG:32638 (no crs_declared_other)
    assert out["files"][0].get("crs") == "pass", out["files"][0]
    assert not out.get("patterns"), out.get("patterns")
    assert msg["result"] == "pass"
