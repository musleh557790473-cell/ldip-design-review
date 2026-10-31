"""Skill 1 Amendment O-a real-data gates — scratch only.

Env (per site DWG path):
  LDIP_SKILL1_QRWN_DWG / LDIP_SKILL1_NRJS_DWG / LDIP_SKILL1_NMAR_DWG
  LDIP_SKILL1_NMAR_PLAN_DWG  (optional UTL-10000-000 for the 2/4 loss probe)

J7 chain / cross-check also need:
  LDIP_LIBREDWG_EXE + LDIP_DWGREAD_EXE

Expected ezdwg nested-content losses (O-a.3.1 probe):
  QRWN UTL-30000-001: 28 layers, 218 entities
  NRJS IUC-30001:     23 layers,  97 entities
  NMAR UTL-30000-001:  0 layers,   0 entities
  NMAR UTL-10000-000:  2 layers,   4 entities

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
SK1 = REPO / "plugin/ldip-design-review/skills/extracting-dwg-data/scripts"
PY = sys.executable

sys.path.insert(0, str(SK1))
from dwg_common import check_nested_content, insert_owner_chain  # noqa: E402

EXPECT_LOSS = {
    "QRWN": {"layers_with_loss": 28, "entities_lost": 218, "env": "LDIP_SKILL1_QRWN_DWG"},
    "NRJS": {"layers_with_loss": 23, "entities_lost": 97, "env": "LDIP_SKILL1_NRJS_DWG"},
    "NMAR": {"layers_with_loss": 0, "entities_lost": 0, "env": "LDIP_SKILL1_NMAR_DWG"},
}
NMAR_PLAN_ENV = "LDIP_SKILL1_NMAR_PLAN_DWG"
NMAR_PLAN_EXPECT = {"layers_with_loss": 2, "entities_lost": 4}

# J7c: band INSERT matches (S33 re-review 3 §3B) + LibreDWG dwgread INSERT totals
CROSS_CHECK_EXPECT = {
    "QRWN": {"env": "LDIP_SKILL1_QRWN_DWG", "band_matched": 110, "dwgread_inserts": 219},
    "NRJS": {"env": "LDIP_SKILL1_NRJS_DWG", "band_matched": 44, "dwgread_inserts": 98},
    "NMAR": {"env": "LDIP_SKILL1_NMAR_DWG", "band_matched": 123, "dwgread_inserts": 229},
}


def _dwg(env_name):
    p = os.environ.get(env_name)
    if not p or not Path(p).is_file():
        return None
    return Path(p)


def _libredwg_ready():
    libre = os.environ.get("LDIP_LIBREDWG_EXE")
    dwgread = os.environ.get("LDIP_DWGREAD_EXE")
    if not libre or not Path(libre).is_file():
        return False
    if not dwgread or not Path(dwgread).is_file():
        return False
    return True


def run_convert(dwg, out_dir, engine="ezdwg", extra=None):
    out_dir.mkdir(parents=True, exist_ok=True)
    dxf = out_dir / f"{dwg.stem}.dxf"
    log = out_dir / f"{dwg.stem}.convert.json"
    cmd = [PY, str(SK1 / "convert.py"), "--engine", engine, "--dwg", str(dwg),
           "--out", str(dxf), "--log", str(log)]
    if extra:
        cmd.extend(extra)
    p = subprocess.run(cmd, capture_output=True, text=True)
    line = p.stdout.strip().splitlines()[-1] if p.stdout.strip() else "{}"
    return p.returncode, json.loads(line), dxf, log


@pytest.mark.parametrize("site", ["QRWN", "NRJS", "NMAR"])
def test_oa_real_nested_content_ezdwg(site, tmp_path):
    exp = EXPECT_LOSS[site]
    dwg = _dwg(exp["env"])
    if dwg is None:
        pytest.skip(f"{exp['env']} not set (real DWG scratch only)")
    pytest.importorskip("ezdwg")
    rc, out, dxf, logp = run_convert(dwg, tmp_path / site)
    assert rc == 0, out
    log = json.loads(logp.read_text(encoding="utf-8"))
    nc = log.get("nested_content") or {}
    # Prefer convert-log nested_content; fall back to direct check
    if not nc.get("checked"):
        nc = check_nested_content(dwg, dxf)
    assert nc.get("layers_with_loss") == exp["layers_with_loss"], nc
    assert nc.get("entities_lost") == exp["entities_lost"], nc
    if exp["entities_lost"]:
        assert any(w["code"] == "NESTED_CONTENT_LOSS" for w in log.get("warnings", []))


def test_oa_real_nmar_plan_loss(tmp_path):
    dwg = _dwg(NMAR_PLAN_ENV)
    if dwg is None:
        pytest.skip(f"{NMAR_PLAN_ENV} not set (UTL-10000-000 scratch only)")
    pytest.importorskip("ezdwg")
    rc, out, dxf, logp = run_convert(dwg, tmp_path / "nmar_plan")
    assert rc == 0, out
    log = json.loads(logp.read_text(encoding="utf-8"))
    nc = log.get("nested_content") or check_nested_content(dwg, dxf)
    assert nc.get("layers_with_loss") == NMAR_PLAN_EXPECT["layers_with_loss"], nc
    assert nc.get("entities_lost") == NMAR_PLAN_EXPECT["entities_lost"], nc


def test_oa_real_qrwn_handle18_top_level():
    """O-a.3.2: QRWN handle 18 → entmode 2, owner 0 → top-level."""
    dwg = _dwg("LDIP_SKILL1_QRWN_DWG")
    if dwg is None:
        pytest.skip("LDIP_SKILL1_QRWN_DWG not set")
    pytest.importorskip("ezdwg")
    result = insert_owner_chain(dwg)
    info = result["inserts"].get(18) or result["inserts"].get("18")
    # handles may be int keys
    if info is None:
        info = next((v for k, v in result["inserts"].items() if int(k) == 18), None)
    assert info is not None, f"handle 18 not in inserts (source={result['source']})"
    assert info["entmode"] == 2
    assert info.get("owner") in (0, None)
    assert info["top_level"] is True
    assert info["chain"] == []


# ------------------------------------------------------------------ J7: chain + cross-check (LibreDWG env)
def test_oa_real_chain_qrwn_keeps_ezdwg(tmp_path):
    """J7a: chain on QRWN → engine ezdwg; LibreDWG fails sort handle 331; loss 218 kept."""
    if not _libredwg_ready():
        pytest.skip("LDIP_LIBREDWG_EXE + LDIP_DWGREAD_EXE required")
    dwg = _dwg("LDIP_SKILL1_QRWN_DWG")
    if dwg is None:
        pytest.skip("LDIP_SKILL1_QRWN_DWG not set")
    pytest.importorskip("ezdwg")
    rc, out, dxf, logp = run_convert(dwg, tmp_path / "qrwn_chain", engine="chain")
    assert rc == 0, out
    log = json.loads(logp.read_text(encoding="utf-8"))
    assert log["engine"] == "ezdwg"
    libre_steps = [s for s in log.get("chain", []) if s.get("engine") == "libredwg"]
    assert libre_steps, log.get("chain")
    assert libre_steps[0].get("result") == "failed"
    assert "sort handle" in (libre_steps[0].get("detail") or "").lower() or "331" in (
        libre_steps[0].get("detail") or ""
    )
    nc = log.get("nested_content") or {}
    assert nc.get("entities_lost") == 218
    assert any(w["code"] == "NESTED_CONTENT_LOSS" for w in log.get("warnings", []))


def test_oa_real_chain_nrjs_selects_libredwg(tmp_path):
    """J7b: chain on NRJS → engine libredwg, nested loss 0."""
    if not _libredwg_ready():
        pytest.skip("LDIP_LIBREDWG_EXE + LDIP_DWGREAD_EXE required")
    dwg = _dwg("LDIP_SKILL1_NRJS_DWG")
    if dwg is None:
        pytest.skip("LDIP_SKILL1_NRJS_DWG not set")
    pytest.importorskip("ezdwg")
    rc, out, dxf, logp = run_convert(dwg, tmp_path / "nrjs_chain", engine="chain")
    assert rc == 0, out
    log = json.loads(logp.read_text(encoding="utf-8"))
    assert log["engine"] == "libredwg"
    nc = log.get("nested_content") or {}
    assert nc.get("ok") is True
    assert nc.get("entities_lost", 0) == 0
    assert any(w["code"] == "ENGINE_FALLBACK" for w in log.get("warnings", []))


@pytest.mark.parametrize("site", ["QRWN", "NRJS", "NMAR"])
def test_oa_real_cross_check_bands(site, tmp_path):
    """J7c: --cross-check band inserts matched; 0 pos/scale mismatches; record missing_in_*."""
    if not _libredwg_ready():
        pytest.skip("LDIP_LIBREDWG_EXE + LDIP_DWGREAD_EXE required")
    exp = CROSS_CHECK_EXPECT[site]
    dwg = _dwg(exp["env"])
    if dwg is None:
        pytest.skip(f"{exp['env']} not set")
    pytest.importorskip("ezdwg")
    rc, out, dxf, logp = run_convert(
        dwg, tmp_path / f"{site}_xc", engine="ezdwg", extra=["--cross-check"],
    )
    assert rc == 0, out
    log = json.loads(logp.read_text(encoding="utf-8"))
    cc = log.get("cross_check") or {}
    assert cc.get("checked") is True, cc
    assert cc.get("value_mismatches", 0) == 0, cc
    assert cc.get("dwgread_inserts") == exp["dwgread_inserts"], cc
    assert cc.get("matched", 0) >= exp["band_matched"], (
        f"{site}: matched {cc.get('matched')} < band expect {exp['band_matched']}; "
        f"missing_in_dwgread={cc.get('missing_in_dwgread')} "
        f"missing_in_ezdwg={cc.get('missing_in_ezdwg')}"
    )
    # Informational: missing_in_* counts are recorded on the report
    assert "missing_in_dwgread" in cc and "missing_in_ezdwg" in cc
