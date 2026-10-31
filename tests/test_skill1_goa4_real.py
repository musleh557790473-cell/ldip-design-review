"""Skill 1 G-Oa4 / DOa4 real-data gates — scratch only.

Env:
  LDIP_SKILL1_NRJS_DWG / LDIP_SKILL1_NMAR_DWG / LDIP_SKILL1_NMAR_PLAN_DWG
  LDIP_LIBREDWG_EXE

Convert with --engine libredwg, then extract:
  NRJS IUC-30001          → dimensions 66
  NMAR UTL-30000-001      → dimensions 195
  NMAR UTL-10000-000      → 58 texts \"A1\" with source attrib

Also extract from the ezdwg DXF of the same files (run must pass; no engine equality).
Claude runs these; CI skips when env absent.
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


def _dwg(env_name):
    p = os.environ.get(env_name)
    if not p or not Path(p).is_file():
        return None
    return Path(p)


def _libredwg_ready():
    libre = os.environ.get("LDIP_LIBREDWG_EXE")
    return bool(libre and Path(libre).is_file())


def run_convert(dwg, out_dir, engine):
    out_dir.mkdir(parents=True, exist_ok=True)
    dxf = out_dir / f"{dwg.stem}.dxf"
    log = out_dir / f"{dwg.stem}.convert.json"
    cmd = [PY, str(SK1 / "convert.py"), "--engine", engine, "--dwg", str(dwg),
           "--out", str(dxf), "--log", str(log)]
    p = subprocess.run(cmd, capture_output=True, text=True)
    line = p.stdout.strip().splitlines()[-1] if p.stdout.strip() else "{}"
    return p.returncode, json.loads(line), dxf, log


def run_extract(dwg, dxf, log, out):
    cmd = [PY, str(SK1 / "extract_dxf.py"), "--dxf", str(dxf), "--dwg", str(dwg),
           "--convert-log", str(log), "--out", str(out), "--source-path", str(dwg)]
    p = subprocess.run(cmd, capture_output=True, text=True)
    line = p.stdout.strip().splitlines()[-1] if p.stdout.strip() else "{}"
    return p.returncode, json.loads(line)


def _require_libredwg_and_dwg(env_name):
    if not _libredwg_ready():
        pytest.skip("LDIP_LIBREDWG_EXE not set")
    dwg = _dwg(env_name)
    if dwg is None:
        pytest.skip(f"{env_name} not set (real DWG scratch only)")
    pytest.importorskip("ezdwg")
    return dwg


def _libredwg_then_extract(dwg, tmp_path, label):
    rc, out, dxf, log = run_convert(dwg, tmp_path / f"{label}_libre", engine="libredwg")
    assert rc == 0, out
    ing_path = tmp_path / f"{label}_libre" / f"{dwg.stem}.dwg.json"
    rc2, out2 = run_extract(dwg, dxf, log, ing_path)
    assert rc2 == 0, out2
    return json.loads(ing_path.read_text(encoding="utf-8"))


def _ezdwg_extract_passes(dwg, tmp_path, label):
    rc, out, dxf, log = run_convert(dwg, tmp_path / f"{label}_ez", engine="ezdwg")
    assert rc == 0, out
    ing_path = tmp_path / f"{label}_ez" / f"{dwg.stem}.dwg.json"
    rc2, out2 = run_extract(dwg, dxf, log, ing_path)
    assert rc2 == 0, out2
    return json.loads(ing_path.read_text(encoding="utf-8"))


def test_doa4_real_nrjs_libredwg_dimensions_66(tmp_path):
    dwg = _require_libredwg_and_dwg("LDIP_SKILL1_NRJS_DWG")
    libre = _libredwg_then_extract(dwg, tmp_path, "nrjs")
    assert len(libre["content"]["dimensions"]) == 66
    _ezdwg_extract_passes(dwg, tmp_path, "nrjs")  # pass only; no equality


def test_doa4_real_nmar_libredwg_dimensions_195(tmp_path):
    dwg = _require_libredwg_and_dwg("LDIP_SKILL1_NMAR_DWG")
    libre = _libredwg_then_extract(dwg, tmp_path, "nmar")
    assert len(libre["content"]["dimensions"]) == 195
    _ezdwg_extract_passes(dwg, tmp_path, "nmar")


def test_doa4_real_nmar_plan_attrib_a1_58(tmp_path):
    """L1 + L2 LibreDWG + L3: 58 attrib A1 texts (filter source/text, not tag); handles unique|null."""
    dwg = _require_libredwg_and_dwg("LDIP_SKILL1_NMAR_PLAN_DWG")
    libre = _libredwg_then_extract(dwg, tmp_path, "nmar_plan")
    # L1: select by source == "attrib" and text == "A1" (real tag is "TEXT")
    a1 = [t for t in libre["content"]["texts"]
          if t.get("source") == "attrib" and t.get("text") == "A1"]
    assert len(a1) == 58, f"got {len(a1)} A1 attrib texts"
    assert all(t.get("tag") == "TEXT" for t in a1), {t.get("tag") for t in a1}
    # L2 LibreDWG: still 58, all with source "attrib"
    assert all(t.get("source") == "attrib" for t in a1)
    # L3: attribute handles are unique or null
    handles = [t.get("handle") for t in a1]
    non_null = [h for h in handles if h is not None]
    assert len(non_null) == len(set(non_null)), "duplicate non-null attrib handles"
    _ezdwg_extract_passes(dwg, tmp_path, "nmar_plan")


def test_l2_real_nmar_plan_ezdwg_a1_58_total(tmp_path):
    """L2 ezdwg route: TEXT copies of ATTRIBs must not double-count — exactly 58 'A1' texts total."""
    dwg = _dwg("LDIP_SKILL1_NMAR_PLAN_DWG")
    if dwg is None:
        pytest.skip("LDIP_SKILL1_NMAR_PLAN_DWG not set (real DWG scratch only)")
    pytest.importorskip("ezdwg")
    ez = _ezdwg_extract_passes(dwg, tmp_path, "nmar_plan_l2")
    a1 = [t for t in ez["content"]["texts"] if t.get("text") == "A1"]
    assert len(a1) == 58, f"got {len(a1)} A1 texts total (expected 58 after L2 de-dupe)"
    assert ez["content"]["block_expansion"].get("attribs_duplicate", 0) >= 1
