"""Skill 1 G-Oa4 / DOa4-1 + DOa4-2 synthetic tests.

DOa4-1: DIMENSION inside an expanded block is recorded once with transformed measurement / points.
DOa4-2: title-block skip narrowed; ATTRIB on a nested insert → text with source \"attrib\".
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

ezdxf = pytest.importorskip("ezdxf")

REPO = Path(__file__).resolve().parents[1]
SK1 = REPO / "plugin/ldip-design-review/skills/extracting-dwg-data/scripts"
PY = sys.executable


def run_extract(*args):
    p = subprocess.run([PY, str(SK1 / "extract_dxf.py"), *map(str, args)],
                       capture_output=True, text=True)
    out = p.stdout.strip().splitlines()
    return p.returncode, (json.loads(out[-1]) if out else {"stderr": p.stderr[-2000:]})


def validate(*files):
    p = subprocess.run([PY, str(SK1 / "validate_ingestion.py"), "--files", *map(str, files)],
                       capture_output=True, text=True)
    return p.returncode, p.stdout + p.stderr


def load(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def fake_dwg(path, body=b"\x00" * 64):
    path.write_bytes(b"AC1032" + body)
    return path


def convert_log(path, dwg, dxf, engine="ezdwg"):
    path.write_text(json.dumps({
        "status": "ok", "engine": engine, "engine_version": "test", "warnings": [],
        "input": {"sha256": sha(dwg), "acadver": "AC1032"},
        "output": {"sha256": sha(dxf)},
    }))
    return path


def doa4_dxf(path):
    """Model-space insert of a DIMENSION block; nested ATTRIB A1; a Title Block insert (skipped)."""
    doc = ezdxf.new("R2018")
    doc.header["$INSUNITS"] = 6

    dim_blk = doc.blocks.new("DIM_INNER")
    dim = dim_blk.add_linear_dim(base=(5, 2), p1=(0, 0), p2=(10, 0), dimstyle="EZDXF")
    dim.render()

    mark = doc.blocks.new("MARK")
    mark.add_attdef("A1", (0, 0), dxfattribs={"height": 1.0})
    mark.add_line((0, 0), (1, 0), dxfattribs={"layer": "MARK_LINE"})

    outer = doc.blocks.new("OUTER")
    nested = outer.add_blockref("MARK", (50, 0))
    nested.add_auto_attribs({"A1": "A1"})

    tb = doc.blocks.new("Parsons Title Block A1")
    tb.add_attdef("DWG_NO", (0, 0), dxfattribs={"height": 1})
    tb.add_line((0, 0), (1, 1), dxfattribs={"layer": "TB_LINE"})

    msp = doc.modelspace()
    # scale 2 at (1000, 2000): measurement 10 → 20; defpoints shifted
    msp.add_blockref("DIM_INNER", (1000, 2000), dxfattribs={"xscale": 2, "yscale": 2})
    msp.add_blockref("OUTER", (100, 200), dxfattribs={"xscale": 2, "yscale": 2})
    msp.add_blockref("Parsons Title Block A1", (0, 0)).add_auto_attribs({"DWG_NO": "X"})
    doc.saveas(path)
    return path


def test_doa4_dimension_in_block_expansion(tmp_path):
    """DOa4-1: DIMENSION in a block, inserted once → one dimension, transformed."""
    dxf = doa4_dxf(tmp_path / "D.dxf")
    dwg = fake_dwg(tmp_path / "D.dwg")
    log = convert_log(tmp_path / "D.convert.json", dwg, dxf)
    rc, out = run_extract("--dxf", dxf, "--dwg", dwg, "--convert-log", log, "--out", tmp_path / "D.dwg.json")
    assert rc == 0, out
    ing = load(tmp_path / "D.dwg.json")
    dims = [d for d in ing["content"]["dimensions"] if d.get("via_block")]
    assert len(dims) == 1, dims
    d = dims[0]
    assert d["via_block"] == ["DIM_INNER"]
    assert d["insert_handle"]
    assert d["measurement"] == 20.0          # 10 × scale 2
    assert d["override"] is False
    assert d["points"]["defpoint"][:2] == [1000.0, 2004.0]
    assert ing["content"]["block_expansion"]["dimensions"] == 1
    assert validate(tmp_path / "D.dwg.json")[0] == 0


def test_doa4_nested_attrib_as_text(tmp_path):
    """DOa4-2: nested insert ATTRIB A1 → text with source attrib (not skipped)."""
    dxf = doa4_dxf(tmp_path / "A.dxf")
    dwg = fake_dwg(tmp_path / "A.dwg")
    log = convert_log(tmp_path / "A.convert.json", dwg, dxf)
    rc, out = run_extract("--dxf", dxf, "--dwg", dwg, "--convert-log", log, "--out", tmp_path / "A.dwg.json")
    assert rc == 0, out
    texts = [t for t in load(tmp_path / "A.dwg.json")["content"]["texts"] if t.get("source") == "attrib"]
    assert len(texts) == 1, texts
    t = texts[0]
    assert t["tag"] == "A1" and t["text"] == "A1"
    assert t["via_block"] == ["OUTER", "MARK"]
    assert t["insert"][:2] == [200.0, 200.0]   # (50,0) ×2 + (100,200)


def test_doa4_title_block_name_still_skipped(tmp_path):
    """DOa4-2: insert whose name contains 'Title Block' is still not expanded."""
    dxf = doa4_dxf(tmp_path / "T.dxf")
    dwg = fake_dwg(tmp_path / "T.dwg")
    log = convert_log(tmp_path / "T.convert.json", dwg, dxf)
    rc, out = run_extract("--dxf", dxf, "--dwg", dwg, "--convert-log", log, "--out",
                          tmp_path / "T.dwg.json", "--linework")
    assert rc == 0, out
    ing = load(tmp_path / "T.dwg.json")
    assert ing["content"]["block_expansion"]["inserts_skipped_title_block"] >= 1
    lw = load(tmp_path / ing["content"]["side_files"][0]["path"])
    assert not [r for r in lw["records"] if r["layer"] == "TB_LINE"]
    # ATTRIB on the skipped title-block insert must not appear as source=attrib
    assert not [t for t in ing["content"]["texts"]
                if t.get("source") == "attrib" and t.get("tag") == "DWG_NO"]
