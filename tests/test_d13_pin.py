"""D13 (owner, 5 Oct 2026) and D13-2 (owner, 16 Oct 2026 20:13): ezdwg is pinned to 0.16.0 for run sessions.

- the skill 1 SKILL.md packages line and the repo README install line name ezdwg==0.16.0;
- convert.py warns ENGINE_VERSION_UNPINNED (warning only, exit 0) when the installed ezdwg differs.

A stand-in `ezdwg` package with its own dist-info is put first on PYTHONPATH, so the real engine is
never needed and the version seen by importlib.metadata is controlled. Synthetic data only."""
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

ezdxf = pytest.importorskip("ezdxf")

REPO = Path(__file__).resolve().parents[1]
SKILL = REPO / "plugin/ldip-design-review/skills/extracting-dwg-data"
SC = SKILL / "scripts"
PY = sys.executable
PIN = "0.16.0"
INSTALL = f"pip install ezdwg=={PIN} ezdxf pypdfium2 pdfplumber pytesseract shapely numpy scipy --break-system-packages"


def fake_ezdwg(root: Path, ver: str) -> Path:
    """Stand-in ezdwg <ver>: to_dxf writes a small R2018 DXF and reports one skipped HATCH."""
    site = root / f"site_{ver.replace('.', '_')}"
    pkg = site / "ezdwg"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text(textwrap.dedent("""
        from types import SimpleNamespace
        import ezdxf

        def to_dxf(src, dst, **kw):
            doc = ezdxf.new("R2018")
            doc.header["$INSUNITS"] = 6
            doc.modelspace().add_lwpolyline([(0, 0), (10, 0), (10, 10), (0, 10)], close=True)
            doc.saveas(dst)
            return SimpleNamespace(total_entities=2, written_entities=1, skipped_entities=1,
                                   skipped_by_type={"HATCH": 1})
        """))
    di = site / f"ezdwg-{ver}.dist-info"
    di.mkdir()
    (di / "METADATA").write_text(f"Metadata-Version: 2.1\nName: ezdwg\nVersion: {ver}\n")
    return site


def run(script, *args, site=None):
    env = dict(os.environ)
    if site:
        env["PYTHONPATH"] = str(site) + os.pathsep + env.get("PYTHONPATH", "")
    p = subprocess.run([PY, str(SC / script), *map(str, args)], capture_output=True, text=True, env=env)
    return p.returncode, json.loads(p.stdout.strip().splitlines()[-1])


def convert(tmp_path, ver):
    dwg = tmp_path / "in" / "T0.dwg"
    dwg.parent.mkdir(parents=True, exist_ok=True)
    dwg.write_bytes(b"AC1032" + b"\0" * 64)          # R2018 signature; content is read by the stand-in only
    b = tmp_path / "b01"
    rc, out = run("convert.py", "--engine", "ezdwg", "--dwg", dwg, "--out", b / "T0.dxf",
                  "--log", b / "T0.convert.json", site=fake_ezdwg(tmp_path, ver))
    log = json.loads((b / "T0.convert.json").read_text())
    return rc, out, log, dwg, b


def codes(warnings):
    return [w["code"] for w in warnings]


# ------------------------------------------------------------------ documents
def test_skill_md_packages_line_pins_ezdwg():
    text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    line = next(l for l in text.splitlines() if l.startswith("Packages:"))
    assert f"`ezdwg=={PIN}`" in line
    assert "ENGINE_VERSION_UNPINNED" in text


def test_readme_install_line_pins_ezdwg():
    text = (REPO / "README.md").read_text(encoding="utf-8")
    assert INSTALL in text
    assert "apt-get install -y tesseract-ocr tesseract-ocr-ara" in text


def test_convert_pin_constant_matches_docs():
    src = (SC / "convert.py").read_text(encoding="utf-8")
    assert f'EZDWG_PINNED = "{PIN}"' in src


# ------------------------------------------------------------------ convert.py warning
def test_pinned_version_no_warning(tmp_path):
    rc, out, log, _, _ = convert(tmp_path, PIN)
    assert rc == 0 and out["status"] == "ok"
    assert log["engine_version"] == PIN
    assert "ENGINE_VERSION_UNPINNED" not in codes(log["warnings"])
    assert "ENGINE_SKIPPED_ENTITIES" in codes(log["warnings"])     # stand-in really ran


@pytest.mark.parametrize("ver", ["0.12.12", "0.12.8"])
def test_other_version_warns_but_converts(tmp_path, ver):
    rc, out, log, _, b = convert(tmp_path, ver)
    assert rc == 0 and out["status"] == "ok" and log["status"] == "ok"
    assert (b / "T0.dxf").is_file()
    w = [w for w in log["warnings"] if w["code"] == "ENGINE_VERSION_UNPINNED"]
    assert len(w) == 1
    assert ver in w[0]["detail"] and PIN in w[0]["detail"]
    assert out["warnings"] == len(log["warnings"])


def test_warning_reaches_ingestion_file_and_validates(tmp_path):
    rc, _, _, dwg, b = convert(tmp_path, "0.12.12")
    assert rc == 0
    js = b / "T0.dwg.json"
    rc, out = run("extract_dxf.py", "--dxf", b / "T0.dxf", "--dwg", dwg, "--convert-log", b / "T0.convert.json",
                  "--out", js, "--source-path", dwg.name)
    assert rc == 0, out
    ing = json.loads(js.read_text())
    assert "ENGINE_VERSION_UNPINNED" in codes(ing["warnings"])
    rc, out = run("validate_ingestion.py", "--files", js)
    assert rc == 0, out
