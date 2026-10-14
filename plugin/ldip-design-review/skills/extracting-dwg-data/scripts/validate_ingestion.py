#!/usr/bin/env python3
"""Validate ingestion files against schemas/ingestion.schema.json (SPC-0006 §2.4, §4.1.5; Rev C §2.4.2).

Also checks what the schema cannot express: the file name matches the source file
(`<stem>.dwg.json` for `<stem>.dwg`, `<file>.xml.json` for `<file>.xml`), and the recorded SHA-256
equals the source file on disk when --root is given. LandXML (template 1.1, skill 3): every
`content.surfaces[].tin_file` exists next to the ingestion file, its SHA-256 equals `tin_sha256`,
and it passes schemas/landxml_surface.schema.json.

Usage: python validate_ingestion.py --files <a.dwg.json> [<b.pdf.json> <c.xml.json> ...] [--root <LDIP_Review>]
Exit: 0 all valid · 2 input error (file missing or not JSON) · 3 jsonschema missing · 4 a file failed.
No network, no shell calls.
"""
__version__ = "0.2.0"

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from dwg_common import EXIT_DEPENDENCY, EXIT_INPUT, EXIT_OK, EXIT_SCHEMA, check_path, emit, sha256_file  # noqa: E402

SCHEMA = Path(__file__).resolve().parents[3] / "schemas" / "ingestion.schema.json"
SURFACE_SCHEMA = Path(__file__).resolve().parents[3] / "schemas" / "landxml_surface.schema.json"
_surface_validator = None


def check_side_files(path, doc):
    """LandXML only: side files referenced by content.surfaces[] (Rev C §4.12.3 output 2)."""
    global _surface_validator
    errs = []
    if _surface_validator is None:
        from jsonschema import Draft202012Validator
        _surface_validator = Draft202012Validator(json.loads(SURFACE_SCHEMA.read_text(encoding="utf-8")))
    for s in doc["content"].get("surfaces", []):
        side = Path(path).parent / s["tin_file"]
        if not side.is_file():
            errs.append(f"surface side file not found: {s['tin_file']}")
            continue
        if sha256_file(side) != s["tin_sha256"]:
            errs.append(f"tin_sha256 differs from {s['tin_file']}")
            continue
        try:
            sd = json.loads(side.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            errs.append(f"{s['tin_file']}: unreadable ({exc})")
            continue
        errs += [f"{s['tin_file']}: {'/'.join(map(str, e.absolute_path)) or '(root)'}: {e.message[:120]}"
                 for e in _surface_validator.iter_errors(sd)][:5]
        if sd.get("surface") != s["name"] or len(sd.get("points", [])) != s["counts"]["points"]:
            errs.append(f"{s['tin_file']}: surface name or point count differs from the ingestion file")
    return errs


def check(path, validator, root):
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    errs = [f"{'/'.join(map(str, e.absolute_path)) or '(root)'}: {e.message[:160]}" for e in validator.iter_errors(doc)]
    if errs:
        return errs
    src = doc["source"]
    want = Path(src["path"]).name + ".json"
    if Path(path).name != want:
        errs.append(f"file name should be {want}")
    if src["file_type"] == "landxml":
        errs += check_side_files(path, doc)
    if root is not None:
        f = Path(root) / src["path"]
        if not f.is_file():
            errs.append(f"source file not found under root: {src['path']}")
        elif sha256_file(f) != src["sha256"]:
            errs.append("source.sha256 differs from the file on disk")
    return errs


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--files", nargs="+", required=True)
    ap.add_argument("--root")
    a = ap.parse_args(argv)
    try:
        from jsonschema import Draft202012Validator, FormatChecker
    except ImportError:
        emit({"status": "error", "code": EXIT_DEPENDENCY, "detail": "jsonschema is not installed"})
        return EXIT_DEPENDENCY
    v = Draft202012Validator(json.loads(SCHEMA.read_text(encoding="utf-8")), format_checker=FormatChecker())
    results = {}
    try:
        for f in a.files:
            check_path(f, must_exist=True)
            results[f] = check(f, v, a.root)
    except (OSError, json.JSONDecodeError) as exc:
        emit({"status": "error", "code": EXIT_INPUT, "detail": f"cannot read {f}: {exc}"})
        return EXIT_INPUT
    except Exception as exc:  # DwgError from check_path
        emit({"status": "error", "code": getattr(exc, "code", EXIT_INPUT), "detail": str(exc)})
        return getattr(exc, "code", EXIT_INPUT)
    bad = {f: e[:20] for f, e in results.items() if e}
    emit({"status": "ok" if not bad else "fail", "files": len(results), "failed": len(bad), "errors": bad})
    return EXIT_OK if not bad else EXIT_SCHEMA


if __name__ == "__main__":
    sys.exit(main())
