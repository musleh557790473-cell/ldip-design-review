"""Skill 1 Amendment O-a synthetic tests (T-Oa1, T-Oa4, entmode placements).

Scope: extracting-dwg-data convert chain 0.3.0, nested-content check, insert_owner_chain.
No real project data in Git.
"""
from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path
from unittest import mock

import pytest

ezdxf = pytest.importorskip("ezdxf")

REPO = Path(__file__).resolve().parents[1]
SK1 = REPO / "plugin/ldip-design-review/skills/extracting-dwg-data/scripts"
PY = sys.executable

sys.path.insert(0, str(SK1))
import dwg_common  # noqa: E402
import convert as convert_mod  # noqa: E402


def run(script, *args, env=None):
    p = subprocess.run(
        [PY, str(SK1 / script), *map(str, args)],
        capture_output=True, text=True,
        env={**os.environ, **(env or {})},
    )
    out = p.stdout.strip().splitlines()
    return p.returncode, (json.loads(out[-1]) if out else {"stderr": p.stderr[-2000:]})


def load(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def fake_dwg(path, body=b"\x00" * 64):
    path.write_bytes(b"AC1032" + body)
    return path


def make_dxf(path, inserts=1, texts=1, layer="C-UTIL"):
    doc = ezdxf.new("R2018", setup=True)
    doc.layers.add(layer)
    msp = doc.modelspace()
    for i in range(texts):
        msp.add_text(f"T{i}", dxfattribs={"layer": layer, "height": 1, "insert": (i, 0)})
    if inserts:
        blk = doc.blocks.new("BAND")
        blk.add_line((0, 0), (1, 0))
        for i in range(inserts):
            msp.add_blockref("BAND", (i * 2, 1), dxfattribs={"layer": layer})
    doc.saveas(path)
    return path


def fake_oda(tmp_path, dxf):
    exe = tmp_path / "fake_oda.py"
    exe.write_text(
        f"#!{PY}\nimport shutil, sys, pathlib\n"
        f"src, out, filt = sys.argv[1], sys.argv[2], sys.argv[7]\n"
        f"shutil.copy({str(dxf)!r}, pathlib.Path(out) / (pathlib.Path(filt).stem + '.dxf'))\n"
    )
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    return exe


def fake_reader(path, dxf_src=None):
    body = (
        "import shutil, sys\nargs = sys.argv[1:]\n"
        "if args and args[0] == '--version':\n    print('dwg2dxf 0.0-test'); sys.exit(0)\n"
        + (
            f"shutil.copy({str(dxf_src)!r}, args[args.index('-o') + 1]); sys.exit(0)\n"
            if dxf_src
            else "print('cannot read', file=sys.stderr); sys.exit(1)\n"
        )
    )
    script = path.with_suffix(".py")
    script.write_text(body)
    path.write_text(f"#!/bin/sh\nexec {PY} {script} \"$@\"\n")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return path


# ------------------------------------------------------------------ T-Oa1: chain order with ODA
@pytest.mark.skipif(os.name == "nt", reason="shell stand-in for converters")
def test_toa1_chain_reaches_oda(tmp_path):
    """T-Oa1: ezdwg fails → libredwg skipped → oda converts."""
    pytest.importorskip("ezdwg")
    dwg = fake_dwg(tmp_path / "bad.dwg", os.urandom(4000))
    good = make_dxf(tmp_path / "good.dxf")
    oda = fake_oda(tmp_path, good)
    rc, out = run(
        "convert.py", "--engine", "chain", "--dwg", dwg,
        "--out", tmp_path / "a.dxf", "--log", tmp_path / "a.convert.json",
        "--exe", oda,
        env={"LDIP_LIBREDWG_EXE": "", "LDIP_ODA_EXE": str(oda)},
    )
    assert rc == 0, out
    log = load(tmp_path / "a.convert.json")
    assert log["engine"] == "oda" and log["requested_engine"] == "chain"
    assert log["engine_version"] == "27.9.0"
    assert [s["engine"] for s in log["chain"]] == ["ezdwg", "libredwg", "oda"]
    assert log["chain"][0]["result"] == "failed"
    assert log["chain"][1]["result"] == "skipped"
    assert log["chain"][2]["result"] == "ok"
    assert log["fallback_from"].startswith("ezdwg:")
    assert "ENGINE_FALLBACK" in [w["code"] for w in log["warnings"]]
    assert (tmp_path / "a.dxf").is_file()


# ------------------------------------------------------------------ T-Oa4: nested-content loss
def test_toa4_nested_content_diff_unit():
    """T-Oa4 unit: raw > DXF by layer → loss; equal counts → ok."""
    raw = {"A": {"INSERT": 5, "TEXT": 2, "MTEXT": 0}, "B": {"INSERT": 1, "TEXT": 0, "MTEXT": 1}}
    dxf = {"A": {"INSERT": 3, "TEXT": 2, "MTEXT": 0}, "B": {"INSERT": 1, "TEXT": 0, "MTEXT": 1}}
    diff = dwg_common.nested_content_diff(raw, dxf)
    assert not diff["ok"] and diff["layers_with_loss"] == 1 and diff["entities_lost"] == 2
    assert diff["by_layer"]["A"] == {"INSERT": 2}

    ok = dwg_common.nested_content_diff(raw, raw)
    assert ok["ok"] and ok["entities_lost"] == 0 and ok["layers_with_loss"] == 0


@pytest.mark.skipif(os.name == "nt", reason="shell stand-in for converters")
def test_toa4_nested_loss_tries_next_keeps_smallest(tmp_path):
    """T-Oa4: NESTED_CONTENT_LOSS on ezdwg → try next; all lose → keep smallest loss."""
    pytest.importorskip("ezdwg")
    dwg = fake_dwg(tmp_path / "site.dwg", os.urandom(200))
    worse = make_dxf(tmp_path / "worse.dxf", inserts=1, texts=0)
    better = make_dxf(tmp_path / "better.dxf", inserts=2, texts=1)

    def fake_ezdwg(dwg_path, out_tmp, log):
        log["engine_version"] = "0.16.0"
        log["returncode"] = 0
        Path(out_tmp).write_bytes(worse.read_bytes())

    def fake_check(dwg_path, dxf_path):
        data = Path(dxf_path).read_bytes()
        if data == worse.read_bytes():
            return {
                "ok": False, "checked": True, "layers_with_loss": 28, "entities_lost": 218,
                "by_layer": {"X": {"INSERT": 218}},
                "warning": {
                    "code": "NESTED_CONTENT_LOSS",
                    "detail": "DXF lost nested INSERT/TEXT/MTEXT vs raw DWG: 28 layer(s), 218 entit(y/ies)",
                    "layers_with_loss": 28, "entities_lost": 218,
                },
            }
        if data == better.read_bytes():
            return {
                "ok": False, "checked": True, "layers_with_loss": 2, "entities_lost": 4,
                "by_layer": {"Y": {"INSERT": 4}},
                "warning": {
                    "code": "NESTED_CONTENT_LOSS",
                    "detail": "DXF lost nested INSERT/TEXT/MTEXT vs raw DWG: 2 layer(s), 4 entit(y/ies)",
                    "layers_with_loss": 2, "entities_lost": 4,
                },
            }
        return {"ok": True, "checked": True, "layers_with_loss": 0, "entities_lost": 0, "by_layer": {}}

    libre = fake_reader(tmp_path / "ok.sh", better)
    log = {
        "script": "convert.py 0.3.0", "engine": "chain", "requested_engine": "chain",
        "engine_version": None, "warnings": [],
        "input": {"path": str(dwg), "sha256": "x", "bytes": 1, "acadver": "AC1032", "release": "R2018"},
    }
    out_tmp = tmp_path / "out.dxf.tmp"
    a = mock.Mock(libredwg_exe=str(libre), exe=None, timeout=60)
    with mock.patch.object(convert_mod, "run_ezdwg", side_effect=fake_ezdwg), \
         mock.patch.object(convert_mod, "check_nested_content", side_effect=fake_check), \
         mock.patch.dict(os.environ, {"LDIP_LIBREDWG_EXE": str(libre), "LDIP_ODA_EXE": ""}, clear=False):
        convert_mod.run_chain(dwg, out_tmp, log, a)
    assert log["engine"] == "libredwg"
    assert log["nested_content_kept"]["engine"] == "libredwg"
    assert log["nested_content_kept"]["entities_lost"] == 4
    assert any(w["code"] == "NESTED_CONTENT_LOSS" for w in log["warnings"])
    assert out_tmp.read_bytes() == better.read_bytes()
    assert [s["result"] for s in log["chain"] if s["engine"] in ("ezdwg", "libredwg")] == [
        "nested_content_loss", "nested_content_loss",
    ]


# ------------------------------------------------------------------ entmode / insert_owner_chain
def test_entmode_insert_owner_chain_top_level():
    """entmode 1/2 = top-level (QRWN handle 18 → entmode 2, owner 0)."""
    placements = [
        (18, 2, 0),       # top-level model-space INSERT
        (137, 0, 19),     # nested in block header 19
        (1, 0, 19),
        (19, 0, 148),     # would cycle if walked from bad owner
    ]
    insert_handles = [18, 137, 1]
    out = dwg_common._owner_chain_from_placements(placements, insert_handles)
    assert out[18]["top_level"] is True and out[18]["chain"] == [] and out[18]["entmode"] == 2
    assert out[137]["top_level"] is False and out[137]["chain"][0] == 19


def test_entmode_cycle_fallback():
    """When entmode unavailable, cycle rule truncates and warns OWNER_CYCLE."""
    inserts = [(1, 0, 0, 0, 1, 1, 1, 0, "BAND"), (18, 0, 0, 0, 1, 1, 1, 0, "XREF")]
    # 1 → 19 → 148 → 19 cycle via owner map that points insert owners only;
    # walk owner_of on headers using insert handles as stand-ins for the fallback helper
    owners = [(1, 19), (18, 148), (19, 148), (148, 19)]
    out = dwg_common._owner_chain_cycle_fallback(inserts, owners)
    # walking from 1: 19 → 148 → 19 cycle
    assert 19 in out[1]["chain"]
    assert any(w["code"] == "OWNER_CYCLE" for w in out[1]["warnings"])


def test_libredwg_dxf_rejected_by_ezdxf(tmp_path):
    """LibreDWG DXF that ezdxf cannot load → error, next step (O-a.2)."""
    bad_dxf = tmp_path / "bad.dxf"
    bad_dxf.write_text("NOT A DXF\n")
    ok, err = dwg_common.dxf_loadable(bad_dxf)
    assert ok is False and err

    dwg = fake_dwg(tmp_path / "q.dwg")
    # fake dwg2dxf writes unloadable DXF
    libre = fake_reader(tmp_path / "badlib.sh", bad_dxf)
    rc, out = run(
        "convert.py", "--engine", "libredwg", "--dwg", dwg,
        "--out", tmp_path / "q.dxf", "--log", tmp_path / "q.convert.json",
        "--libredwg-exe", libre,
    )
    assert rc == 2 and "ezdxf" in out["detail"].lower()
