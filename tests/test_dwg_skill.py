"""Tests for skill 1 extracting-dwg-data (SPC-0006 §4.1). Synthetic DXF only; a fake converter
stands in for ODA. Real DWGs (SPC-0006 §4.1.8 T1–T4) run only when LDIP_DWG_SAMPLES points to a
local folder of DWGs (never in Git, brief §10.2)."""
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

ezdxf = pytest.importorskip("ezdxf")

REPO = Path(__file__).resolve().parents[1]
SC = REPO / "plugin/ldip-design-review/skills/extracting-dwg-data/scripts"
PY = sys.executable


def run(script, *args, env=None):
    p = subprocess.run([PY, str(SC / script), *map(str, args)], capture_output=True, text=True,
                       env={**os.environ, **(env or {})})
    return p.returncode, json.loads(p.stdout.strip().splitlines()[-1])


def make_dxf(path, units=6, geodata=True, aecc=False, override_text="25.00"):
    doc = ezdxf.new("R2018", setup=True)
    doc.header["$INSUNITS"] = units
    doc.layers.add("C-PLOT", color=3)
    doc.layers.add("C-TEXT")
    doc.layers.add("A-DIMS")
    doc.layers.add("FROZEN").freeze()
    msp = doc.modelspace()
    msp.add_lwpolyline([(0, 0), (20, 0), (20, 15), (0, 15)], close=True, dxfattribs={"layer": "C-PLOT"})
    msp.add_lwpolyline([(30, 0), (40, 0), (40, 10)], dxfattribs={"layer": "C-PLOT"})
    msp.add_lwpolyline([(50, 0, 0), (60, 0, 1), (60, 10, 0), (50, 10, 0)], format="xyb", close=True,
                       dxfattribs={"layer": "C-PLOT"})
    msp.add_text("PLOT 118", dxfattribs={"layer": "C-TEXT", "height": 2.5, "insert": (5, 5)})
    msp.add_mtext("Local\\PMasjid", dxfattribs={"layer": "C-TEXT", "char_height": 2.0, "insert": (5, 10)})
    msp.add_linear_dim(base=(0, -5), p1=(0, 0), p2=(20, 0), dxfattribs={"layer": "A-DIMS"}).render()
    msp.add_linear_dim(base=(0, -10), p1=(0, 0), p2=(20, 0), text=override_text, dxfattribs={"layer": "A-DIMS"}).render()
    msp.add_linear_dim(base=(0, -15), p1=(0, 0), p2=(20, 0), text="<> m", dxfattribs={"layer": "A-DIMS"}).render()
    blk = doc.blocks.new("PLOT_TAG")
    blk.add_attdef("PLOT_ID", (0, 0), dxfattribs={"height": 1})
    msp.add_blockref("PLOT_TAG", (10, 7)).add_auto_attribs({"PLOT_ID": "118"})
    tb = doc.blocks.new("TB_A1")
    for t in ("DWG_NO", "REV", "TITLE1", "TITLE2", "DATE", "STATUS", "SCALE"):
        tb.add_attdef(t, (0, 0), dxfattribs={"height": 1})
    doc.layout("Layout1").add_blockref("TB_A1", (0, 0)).add_auto_attribs(
        {"DWG_NO": "LDIP-RCRC-SAP-01-QN-CIV-DWG-00-10001", "REV": "0A", "TITLE1": "PLOT LAYOUT",
         "TITLE2": "SHEET 1", "DATE": "2026-09-30", "STATUS": "FOR REVIEW", "SCALE": "1:500"})
    doc.add_xref_def("C:/xrefs/base_survey.dwg", "BASE_SURVEY")
    doc.styles.add("ARIALS", font="arial.ttf")
    if geodata:
        msp.new_geodata().coordinate_system_definition = (
            '<Dictionary><ProjectedCoordinateSystem id="UTM84-38N"><Alias id="32638" type="CoordinateSystem">'
            '<ObjectId>32638</ObjectId><Namespace>EPSG Code</Namespace></Alias></ProjectedCoordinateSystem></Dictionary>')
    if aecc:
        from ezdxf.entities.dxfclass import DXFClass
        c = DXFClass.new(doc=doc)
        c.update_dxf_attribs({"name": "AECC_ALIGNMENT", "cpp_class_name": "AeccDbAlignment",
                              "app_name": "AeccDbAlignment|Autodesk Civil 3D", "flags": 0,
                              "was_a_proxy": 0, "is_an_entity": 1})
        doc.classes.register(c)
    doc.saveas(path)
    return path


def fake_dwg(path, sig=b"AC1032"):
    path.write_bytes(sig + b"\x00" * 64)
    return path


def fake_oda(tmp_path, dxf):
    """A stand-in for ODAFileConverter: copies a prepared DXF to <out dir>/<stem>.dxf."""
    exe = tmp_path / "fake_oda.py"
    exe.write_text(f"#!{PY}\nimport shutil, sys, pathlib\n"
                   f"src, out, filt = sys.argv[1], sys.argv[2], sys.argv[7]\n"
                   f"shutil.copy({str(dxf)!r}, pathlib.Path(out) / (pathlib.Path(filt).stem + '.dxf'))\n")
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    return exe


def pipeline(tmp_path, **kw):
    dxf_src = make_dxf(tmp_path / "prep.dxf", **kw)
    dwg = fake_dwg(tmp_path / "LDIP-RCRC-SAP-01-QN-CIV-DWG-00-10001.dwg")
    b = tmp_path / "b01"
    rc, out = run("convert.py", "--engine", "oda", "--dwg", dwg, "--out", b / f"{dwg.stem}.dxf",
                  "--log", b / f"{dwg.stem}.convert.json", "--exe", fake_oda(tmp_path, dxf_src))
    assert rc == 0, out
    js = b / f"{dwg.stem}.dwg.json"
    rc, out = run("extract_dxf.py", "--dxf", b / f"{dwg.stem}.dxf", "--dwg", dwg, "--convert-log",
                  b / f"{dwg.stem}.convert.json", "--out", js, "--source-path", dwg.name)
    assert rc == 0, out
    return dwg, js, json.loads(js.read_text()), out


# ------------------------------------------------------------------ convert.py
def test_convert_external_engine_and_log(tmp_path):
    dwg, js, ing, _ = pipeline(tmp_path)
    log = json.loads((tmp_path / "b01" / f"{dwg.stem}.convert.json").read_text())
    assert log["status"] == "ok" and log["engine"] == "oda" and log["input"]["acadver"] == "AC1032"
    assert log["input"]["release"] == "R2018" and len(log["output"]["sha256"]) == 64
    assert not list((tmp_path / "b01").glob("*.tmp"))


def test_convert_errors(tmp_path):
    dwg = fake_dwg(tmp_path / "a.dwg")
    args = ("--dwg", dwg, "--out", tmp_path / "a.dxf", "--log", tmp_path / "a.convert.json")
    assert run("convert.py", "--engine", "oda", *args, env={"LDIP_ODA_EXE": ""})[0] == 3        # engine missing
    assert run("convert.py", "--engine", "accoreconsole", *args, env={"LDIP_ACCORE_EXE": ""})[0] == 3
    old = fake_dwg(tmp_path / "old.dwg", b"AC1009")
    rc, out = run("convert.py", "--engine", "ezdwg", "--dwg", old, "--out", tmp_path / "o.dxf", "--log", tmp_path / "o.json")
    assert rc == 3 and "not supported" in out["detail"]                                          # §4.1.7 too new/old
    (tmp_path / "x.dwg").write_bytes(b"%PDF-1.7")
    assert run("convert.py", "--engine", "ezdwg", "--dwg", tmp_path / "x.dwg", "--out", tmp_path / "x.dxf",
               "--log", tmp_path / "x.json")[0] == 2                                              # not a DWG
    assert run("convert.py", "--engine", "ezdwg", "--dwg", tmp_path / "none.dwg", "--out", tmp_path / "n.dxf",
               "--log", tmp_path / "n.json")[0] == 2
    assert json.loads((tmp_path / "o.json").read_text())["status"] == "error"                     # log kept on failure


def test_convert_engine_without_output(tmp_path):
    exe = tmp_path / "noop.py"
    exe.write_text(f"#!{PY}\nimport sys\nsys.exit(1)\n")
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    dwg = fake_dwg(tmp_path / "a.dwg")
    rc, out = run("convert.py", "--engine", "oda", "--dwg", dwg, "--out", tmp_path / "a.dxf",
                  "--log", tmp_path / "a.json", "--exe", exe)
    assert rc == 2 and "no DXF" in out["detail"]


# ------------------------------------------------------------------ extract_dxf.py
def test_extract_content(tmp_path):
    _, _, ing, out = pipeline(tmp_path)
    c = ing["content"]
    assert ing["source"]["document_no"] == "LDIP-RCRC-SAP-01-QN-CIV-DWG-00-10001"
    assert ing["crs"] == {"declared": "EPSG:32638", "units": "m", "insunits": 6}
    assert ing["title_block"]["document_no"] == "LDIP-RCRC-SAP-01-QN-CIV-DWG-00-10001"
    assert ing["title_block"]["revision"] == "0A" and ing["title_block"]["title"] == "PLOT LAYOUT SHEET 1"
    dims = {d["override_kind"]: d for d in c["dimensions"]}
    assert dims["text_override"]["text_differs"] is True and dims["text_override"]["measurement"] == 20.0
    assert dims["measured"]["override"] is False and dims["measured_with_prefix_suffix"]["override"] is False
    areas = sorted(p["area"] for p in c["polylines"])
    assert areas == [139.27, 300.0] or areas == [139.28, 300.0]                  # square + semicircle bulge
    assert c["open_polylines_by_layer"] == {"C-PLOT": 1}
    # DOa4-2: PLOT_TAG (attributed, not a title block) is expanded → ATTRIB "118" as text
    assert {t["text"] for t in c["texts"]} == {"PLOT 118", "Local\nMasjid", "118"}
    assert any(t.get("source") == "attrib" and t.get("tag") == "PLOT_ID" and t["text"] == "118"
               for t in c["texts"])
    assert any(b["attributes"] == {"PLOT_ID": "118"} for b in c["blocks"])
    assert c["xrefs"][0]["saved_path"] == "C:/xrefs/base_survey.dwg" and c["xrefs"][0]["resolved"] is None
    assert next(ly for ly in c["layers"] if ly["name"] == "FROZEN")["frozen"] is True
    assert c["aecc"]["civil3d_native"] is False and c["origin_signals"]["confidence"] == "Probable"
    assert "TITLE_BLOCK_HEURISTIC" in out["warnings"]


def test_units_crs_and_aecc_signals(tmp_path):
    _, _, ing, out = pipeline(tmp_path, units=4, geodata=False, aecc=True)
    codes = {w["code"] for w in ing["warnings"]}
    assert {"UNITS_NOT_METRES", "CRS_NOT_DECLARED"} <= codes
    assert ing["crs"] == {"declared": None, "units": "mm", "insunits": 4}
    a = ing["content"]["aecc"]      # classes only → saved in Civil 3D, not native (P-S3-22)
    assert a["aecc_class_count"] == 1 and a["civil3d_native"] is False and a["saved_in_civil3d"] is True


def test_extract_refuses_mismatched_dwg(tmp_path):
    dwg, js, _, _ = pipeline(tmp_path)
    other = fake_dwg(tmp_path / "other.dwg", b"AC1027")
    b = tmp_path / "b01"
    rc, out = run("extract_dxf.py", "--dxf", b / f"{dwg.stem}.dxf", "--dwg", other, "--convert-log",
                  b / f"{dwg.stem}.convert.json", "--out", tmp_path / "other.dwg.json")
    assert rc == 2 and "hash differs" in out["detail"]


def test_deterministic(tmp_path):
    dwg, js, ing1, _ = pipeline(tmp_path)
    b = tmp_path / "b01"
    run("extract_dxf.py", "--dxf", b / f"{dwg.stem}.dxf", "--dwg", dwg, "--convert-log", b / f"{dwg.stem}.convert.json",
        "--out", tmp_path / "again" / js.name, "--source-path", dwg.name)
    ing2 = json.loads((tmp_path / "again" / js.name).read_text())
    for d in (ing1, ing2):
        d["meta"].pop("extracted_at")
    assert ing1 == ing2


# ------------------------------------------------------------------ validate_ingestion.py
def test_validate_ok_and_root_hash(tmp_path):
    dwg, js, _, _ = pipeline(tmp_path)
    assert run("validate_ingestion.py", "--files", js)[0] == 0
    assert run("validate_ingestion.py", "--files", js, "--root", tmp_path)[0] == 0
    dwg.write_bytes(b"AC1032changed")
    rc, out = run("validate_ingestion.py", "--files", js, "--root", tmp_path)
    assert rc == 4 and "differs" in json.dumps(out)


def test_validate_rejects(tmp_path):
    _, js, ing, _ = pipeline(tmp_path)
    for mutate, why in ((lambda d: d["content"].pop("aecc"), "aecc"),
                        (lambda d: d["crs"].update(declared="32638"), "declared"),
                        (lambda d: d["content"]["origin_signals"].update(confidence="Confirmed"), "confidence"),
                        (lambda d: d["source"].update(sha256="xyz"), "sha256")):
        d = json.loads(js.read_text())
        mutate(d)
        bad = tmp_path / "bad" / js.name
        bad.parent.mkdir(exist_ok=True)
        bad.write_text(json.dumps(d))
        rc, out = run("validate_ingestion.py", "--files", bad)
        assert rc == 4 and why in json.dumps(out), why
    wrong = tmp_path / "wrong.dwg.json"
    wrong.write_text(js.read_text())
    rc, out = run("validate_ingestion.py", "--files", wrong)
    assert rc == 4 and "file name" in json.dumps(out)


# ------------------------------------------------------------------ real DWGs (local only)
REAL = os.environ.get("LDIP_DWG_SAMPLES")


@pytest.mark.skipif(not REAL, reason="LDIP_DWG_SAMPLES not set (real DWGs stay out of Git)")
def test_real_dwgs_ezdwg(tmp_path):
    files = sorted(Path(REAL).glob("*.dwg"))
    assert files
    for dwg in files:
        rc, out = run("convert.py", "--engine", "ezdwg", "--dwg", dwg, "--out", tmp_path / f"{dwg.stem}.dxf",
                      "--log", tmp_path / f"{dwg.stem}.convert.json")
        if rc != 0:
            continue                                   # recorded, not fatal (batch_manifest status failed)
        js = tmp_path / f"{dwg.name}.json"
        assert run("extract_dxf.py", "--dxf", tmp_path / f"{dwg.stem}.dxf", "--dwg", dwg, "--convert-log",
                   tmp_path / f"{dwg.stem}.convert.json", "--out", js, "--source-path", dwg.name)[0] == 0
        assert run("validate_ingestion.py", "--files", js)[0] == 0


def test_proxy_object_counted_not_crash(tmp_path):
    """Real Parsons DWGs carry ACAD_PROXY_OBJECT; class_id is not an ezdxf attribute (found on musleh, 30 Sep 2026)."""
    src = make_dxf(tmp_path / "prep.dxf", aecc=True)
    txt = src.read_text(encoding="utf-8")
    doc = ezdxf.readfile(src)
    aecc_id = 500 + [c.dxf.name for c in doc.classes.classes.values()].index("AECC_ALIGNMENT")
    owner = doc.rootdict.dxf.handle          # an orphan (owner 0) would be dropped by recover
    proxy = (f"  0\nACAD_PROXY_OBJECT\n  5\nFFFF1\n330\n{owner}\n100\nAcDbProxyObject\n 90\n499\n"
             f" 91\n{aecc_id}\n 95\n0\n 70\n0\n")
    head, sep, tail = txt.rpartition("  0\nENDSEC\n  0\nEOF")
    src.write_text(head + proxy + sep + tail, encoding="utf-8")
    dwg = fake_dwg(tmp_path / "p.dwg")
    b = tmp_path / "b01"
    assert run("convert.py", "--engine", "oda", "--dwg", dwg, "--out", b / "p.dxf", "--log", b / "p.convert.json",
               "--exe", fake_oda(tmp_path, src))[0] == 0
    rc, out = run("extract_dxf.py", "--dxf", b / "p.dxf", "--dwg", dwg, "--convert-log", b / "p.convert.json",
                  "--out", b / "p.dwg.json")
    assert rc == 0, out
    a = json.loads((b / "p.dwg.json").read_text())["content"]["aecc"]
    assert a["proxy_count"] == 1 and a["proxy_objects_by_class"] == {"AECC_ALIGNMENT": 1}
    assert a["civil3d_native"] is False and a["saved_in_civil3d"] is True     # an object, not a drawn entity


def test_unexpected_content_is_exit_2_not_crash(tmp_path, monkeypatch, capsys):
    sys.path.insert(0, str(SC))
    import extract_dxf
    def boom(a):
        raise KeyError("something new in a real drawing")
    monkeypatch.setattr(extract_dxf, "extract", boom)
    rc = extract_dxf.main(["--dxf", "x", "--dwg", "y", "--convert-log", "z", "--out", str(tmp_path / "o.json")])
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert rc == 2 and "KeyError" in out["detail"] and "line" in out["detail"]


def test_aecc_proxy_entity_makes_native(tmp_path):
    src = make_dxf(tmp_path / "prep.dxf", aecc=True)
    doc = ezdxf.readfile(src)
    aecc_id = 500 + [c.dxf.name for c in doc.classes.classes.values()].index("AECC_ALIGNMENT")
    owner = doc.modelspace().block_record_handle if hasattr(doc.modelspace(), "block_record_handle") \
        else doc.block_records.get("*Model_Space").dxf.handle
    ent = (f"  0\nACAD_PROXY_ENTITY\n  5\nFFFF2\n330\n{owner}\n100\nAcDbEntity\n  8\nC-ROAD\n"
           f"100\nAcDbProxyEntity\n 90\n498\n 91\n{aecc_id}\n 95\n0\n 70\n0\n")
    txt = src.read_text(encoding="utf-8")
    marker = "  0\nENDSEC\n  0\nSECTION\n  2\nOBJECTS"
    assert marker in txt
    src.write_text(txt.replace(marker, ent + marker, 1), encoding="utf-8")
    dwg = fake_dwg(tmp_path / "e.dwg")
    b = tmp_path / "b01"
    assert run("convert.py", "--engine", "oda", "--dwg", dwg, "--out", b / "e.dxf", "--log", b / "e.convert.json",
               "--exe", fake_oda(tmp_path, src))[0] == 0
    rc, out = run("extract_dxf.py", "--dxf", b / "e.dxf", "--dwg", dwg, "--convert-log", b / "e.convert.json",
                  "--out", b / "e.dwg.json")
    assert rc == 0, out
    a = json.loads((b / "e.dwg.json").read_text())["content"]["aecc"]
    assert a["proxy_entities_by_class"] == {"AECC_ALIGNMENT": 1} and a["aecc_proxy_entity_count"] == 1
    assert a["civil3d_native"] is True and a["saved_in_civil3d"] is False
    assert run("validate_ingestion.py", "--files", b / "e.dwg.json")[0] == 0


# ------------------------------------------------------------------ K5-2 attribute check
def _fake_ezdwg(rows, inserts):
    import types

    class E:
        def __init__(self, h, n):
            self.dxftype, self.handle, self.dxf = "INSERT", h, {"name": n}

    class Sp:
        def __init__(self, es):
            self.es = es

        def iter_entities(self):
            return iter(self.es)

    doc = types.SimpleNamespace(modelspace=lambda: Sp([]), paperspace=lambda: Sp([E(h, n) for h, n in inserts.items()]))
    return types.SimpleNamespace(raw=types.SimpleNamespace(decode_attrib_entities=lambda p: rows),
                                 read=lambda p: doc)


@pytest.mark.parametrize("rows,inserts,warn", [
    ([(1, "x", "Āࢀ", None, None, None, None, None, None, 0, False, (5, 10))], {10: "Ladun - Qairwan ATT"}, True),
    ([], {10: "Ladun - Qairwan ATT"}, True),
    ([(1, "LDIP-1", "DWG_NO", None, None, None, None, None, None, 0, False, (5, 10))], {10: "Ladun - Qairwan ATT"}, False),
    ([], {10: "Some Logo"}, False),
])
def test_attrib_check(rows, inserts, warn):
    sys.path.insert(0, str(SC))
    import convert
    log = {"warnings": []}
    convert.attrib_check(_fake_ezdwg(rows, inserts), "x.dwg", log)
    assert log["attributes"]["checked"]
    assert any(w["code"] == "TITLE_BLOCK_ATTRIBS_UNDECODED" for w in log["warnings"]) is warn
    if warn:
        assert log["attributes"]["blocks"] == ["Ladun - Qairwan ATT"]


def test_autocad_builtin_fonts_are_not_missing(tmp_path):
    sys.path.insert(0, str(SC))
    import extract_dxf
    assert extract_dxf.font_resolved("txt", tmp_path) is True
    assert extract_dxf.font_resolved("ROMANS.SHX", tmp_path) is True
    assert extract_dxf.font_resolved("parsons_custom.shx", tmp_path) is False
    assert extract_dxf.font_resolved("txt", None) is True
