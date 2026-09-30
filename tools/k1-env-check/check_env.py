#!/usr/bin/env python3
"""LDIP K1 environment check (SPC-0006 K1, §2.3.7; SPC-0002 R5 / F5; brief Rev F §11 item 17).

Read-only inventory of the machine that will run the LDIP skill scripts:
Python, the brief §9.4 libraries, and the external programs (Ghostscript,
Tesseract + Arabic data, ODA File Converter, AutoCAD Core Console, git).

It reads no project data and writes one JSON file only (--out). Network is
not touched unless --egress is given (one TCP connect, 3 s timeout, no data
sent), which tests SPC-0005 §4.4c.

Usage (PowerShell, Windows):
    python check_env.py --out "<LDIP_Review>\\_env\\k1_env_<machine>.json"
Exit codes: 0 completed; 2 bad output path.
"""
__version__ = "0.1.0"

import argparse
import glob
import importlib
import importlib.metadata as md
import json
import os
import platform
import shutil
import socket
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

MIN_PYTHON = (3, 11)

# (import name, distribution name, required in Stage 3?)
PACKAGES = [
    ("ezdxf", "ezdxf", True),
    ("fitz", "PyMuPDF", True),
    ("pdfplumber", "pdfplumber", True),
    ("camelot", "camelot-py", True),
    ("shapely", "shapely", True),
    ("geopandas", "geopandas", True),
    ("pyproj", "pyproj", True),
    ("pandas", "pandas", True),
    ("openpyxl", "openpyxl", True),
    ("lxml", "lxml", True),
    ("jsonschema", "jsonschema", True),
    ("pytesseract", "pytesseract", True),
    ("PIL", "pillow", True),
    ("docx", "python-docx", False),
    ("yaml", "PyYAML", False),
    ("cv2", "opencv-python", False),
    ("ezdwg", "ezdwg", False),
    ("ifcopenshell", "ifcopenshell", False),
    ("osgeo", "GDAL", False),
]


def run(cmd, timeout=15):
    """Run a fixed command (no shell). Return (rc, first 400 chars of output)."""
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        out = (p.stdout or "") + (p.stderr or "")
        return p.returncode, out.strip()[:400]
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, repr(exc)


def check_packages():
    res = []
    for mod, dist, required in PACKAGES:
        item = {"import": mod, "dist": dist, "required_stage3": required}
        try:
            item["version"] = md.version(dist)
        except md.PackageNotFoundError:
            item["version"] = None
        try:
            importlib.import_module(mod)
            item["importable"] = True
        except Exception as exc:  # noqa: BLE001 - report any import failure
            item["importable"] = False
            item["error"] = repr(exc)[:200]
        res.append(item)
    return res


def find_first(candidates):
    for c in candidates:
        for hit in sorted(glob.glob(c)):
            if os.path.isfile(hit):
                return hit
    return None


def check_tools():
    win = os.name == "nt"
    pf = [os.environ.get("ProgramFiles", r"C:\Program Files"),
          os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")]
    tools = {}

    gs = shutil.which("gswin64c") or shutil.which("gs") or (
        find_first([os.path.join(p, "gs", "*", "bin", "gswin64c.exe") for p in pf]) if win else None)
    tools["ghostscript"] = {"path": gs}
    if gs:
        tools["ghostscript"]["rc"], tools["ghostscript"]["version"] = run([gs, "--version"])

    tess = shutil.which("tesseract") or (
        find_first([os.path.join(p, "Tesseract-OCR", "tesseract.exe") for p in pf]) if win else None)
    tools["tesseract"] = {"path": tess}
    if tess:
        _, v = run([tess, "--version"])
        tools["tesseract"]["version"] = v.splitlines()[0] if v else None
        _, langs = run([tess, "--list-langs"])
        lang_list = [l.strip() for l in langs.splitlines()[1:] if l.strip()]
        tools["tesseract"]["langs"] = lang_list
        tools["tesseract"]["arabic"] = "ara" in lang_list

    oda = shutil.which("ODAFileConverter") or (
        find_first([os.path.join(p, "ODA", "*", "ODAFileConverter.exe") for p in pf]) if win else None)
    tools["oda_file_converter"] = {"path": oda}

    acc = shutil.which("accoreconsole") or (
        find_first([os.path.join(p, "Autodesk", "AutoCAD*", "accoreconsole.exe") for p in pf]) if win else None)
    tools["autocad_core_console"] = {"path": acc}
    if win:
        tools["autocad_core_console"]["all_found"] = sorted(
            glob.glob(os.path.join(pf[0], "Autodesk", "*", "accoreconsole.exe")))

    for name in ("git", "gh", "dwgread"):
        p = shutil.which(name)
        tools[name] = {"path": p}
        if p:
            tools[name]["rc"], tools[name]["version"] = run([p, "--version"])
    return tools


def check_egress(host="example.com", port=443):
    try:
        with socket.create_connection((host, port), timeout=3):
            return {"tested": True, "target": f"{host}:{port}", "open": True}
    except OSError as exc:
        return {"tested": True, "target": f"{host}:{port}", "open": False, "error": repr(exc)[:200]}


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", required=True, help="output JSON path")
    ap.add_argument("--egress", action="store_true", help="also test outbound TCP 443")
    a = ap.parse_args()

    out = Path(a.out).expanduser().resolve()
    if len(str(out)) > 250 or not out.parent.exists():
        print(json.dumps({"status": "error", "code": 2, "detail": f"bad output path: {out}"}))
        return 2

    pkgs = check_packages()
    tools = check_tools()
    py_ok = sys.version_info[:2] >= MIN_PYTHON
    missing = [p["dist"] for p in pkgs if p["required_stage3"] and not p["importable"]]

    result = {
        "check": "LDIP K1 environment", "script_version": __version__,
        "machine": {
            "host": socket.gethostname(), "os": platform.platform(),
            "machine": platform.machine(), "user_home": str(Path.home()),
        },
        "python": {
            "version": platform.python_version(), "executable": sys.executable,
            "meets_min_3_11": py_ok, "implementation": platform.python_implementation(),
            "in_venv": sys.prefix != getattr(sys, "base_prefix", sys.prefix),
        },
        "packages": pkgs,
        "tools": tools,
        "egress": check_egress() if a.egress else {"tested": False},
        "summary": {
            "python_ok": py_ok,
            "required_missing": missing,
            "tesseract_arabic": tools.get("tesseract", {}).get("arabic", False),
            "dwg_converter_found": [k for k in ("oda_file_converter", "autocad_core_console")
                                    if tools[k].get("path")],
        },
        "meta": {"checked_at": datetime.now(timezone.utc).isoformat()},
    }

    tmp = out.with_suffix(out.suffix + ".tmp")
    tmp.write_text(json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, out)
    print(json.dumps({"status": "ok", "out": str(out), **result["summary"]}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
