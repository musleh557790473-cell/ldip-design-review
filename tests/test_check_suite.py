"""tools/check_suite.py: a module skipped for a missing package fails the release check (owner 15 Oct 2026 13:07)."""
import json
import subprocess
import sys
from pathlib import Path

TOOL = Path(__file__).resolve().parents[1] / "tools/check_suite.py"


def run(tmp, *extra):
    p = subprocess.run([sys.executable, str(TOOL), "--tests", str(tmp), *extra], capture_output=True, text=True, cwd=tmp)
    return p.returncode, json.loads(p.stdout.strip().splitlines()[-1])


def test_package_skip_fails(tmp_path):
    (tmp_path / "test_a.py").write_text("import pytest\npytest.importorskip('no_such_pkg_ldip')\n"
                                        "def test_x():\n    assert True\n")
    (tmp_path / "test_b.py").write_text("def test_y():\n    assert True\n")
    code, out = run(tmp_path)
    assert code == 5 and any("package skip" in p for p in out["problems"])


def test_count_and_data_skip(tmp_path):
    (tmp_path / "test_c.py").write_text("import pytest\n@pytest.mark.skip(reason='real data not in Git')\n"
                                        "def test_r():\n    pass\ndef test_y():\n    assert True\n")
    code, out = run(tmp_path, "--expect", "2")
    assert code == 0 and out["collected"] == 2 and out["skips"]
    code, out = run(tmp_path, "--expect", "3")
    assert code == 5 and "expected 3" in out["problems"][0]
