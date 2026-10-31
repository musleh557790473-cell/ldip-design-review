"""Skill 1 Amendment O-a real-data gates — scratch only.

Env (per site DWG path):
  LDIP_SKILL1_QRWN_DWG / LDIP_SKILL1_NRJS_DWG / LDIP_SKILL1_NMAR_DWG
  LDIP_SKILL1_NMAR_PLAN_DWG  (optional UTL-10000-000 for the 2/4 loss probe)

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


def _dwg(env_name):
    p = os.environ.get(env_name)
    if not p or not Path(p).is_file():
        return None
    return Path(p)


def run_convert(dwg, out_dir, engine="ezdwg"):
    out_dir.mkdir(parents=True, exist_ok=True)
    dxf = out_dir / f"{dwg.stem}.dxf"
    log = out_dir / f"{dwg.stem}.convert.json"
    p = subprocess.run(
        [PY, str(SK1 / "convert.py"), "--engine", engine, "--dwg", str(dwg),
         "--out", str(dxf), "--log", str(log)],
        capture_output=True, text=True,
    )
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
