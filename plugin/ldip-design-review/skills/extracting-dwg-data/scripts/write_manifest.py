#!/usr/bin/env python3
"""Write batch_manifest.json for one intake batch from what is on disk (SPC-0006 §3.1; D8, 0.2.2).

The agent no longer writes the manifest by hand. For each input of the batch the script reads the
converter log (<stem>.convert.json) and the ingestion file (<stem>.dwg.json) in the batch folder,
validates the ingestion file (validate_ingestion.check), and sets status / output / intermediates /
error. PDFs (skill 2): the ingestion file <name>.pdf.json written by extract_pdf.py is validated →
`extracted`; missing or invalid → `failed`. Other files are `skipped` (code 2).
The manifest is validated against schemas/batch_manifest.schema.json before it is written.

Usage: python write_manifest.py --batch-dir <0.ai_context/SITE/SUB/bNN> --run <run_id> --site <SITE>
                                --sub <sub_id> --file <path> <sha256> [--file <path> <sha256> ...]
                                [--root <LDIP_Review>]
Exit: 0 written · 2 input error · 3 jsonschema missing · 4 manifest failed its schema (not written).
No network, no shell calls.
"""
__version__ = "0.2.0"

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from dwg_common import (  # noqa: E402
    EXIT_DEPENDENCY, EXIT_INPUT, EXIT_OK, EXIT_SCHEMA, DwgError, atomic_write_json, check_path, emit,
)
import validate_ingestion  # noqa: E402

PLUGIN_ROOT = Path(__file__).resolve().parents[3]
BATCH_SCHEMA = PLUGIN_ROOT / "schemas" / "batch_manifest.schema.json"
INGESTION_SCHEMA = PLUGIN_ROOT / "schemas" / "ingestion.schema.json"
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
PDF_MISSING = "no ingestion file: extract_pdf.py not run or failed"


def read_json(p):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def pdf_entry(bdir, path, sha, ing_validator, root):
    out = bdir / f"{Path(path).name}.json"
    entry = {"path": path, "sha256": sha, "type": "pdf", "status": "failed", "output": None, "error": None}
    if not out.is_file():
        entry["error"] = {"code": EXIT_INPUT, "detail": PDF_MISSING}
        return entry
    doc = read_json(out)
    if doc is None:
        entry["error"] = {"code": EXIT_SCHEMA, "detail": "ingestion file is not readable JSON"}
        entry["intermediates"] = [out.name]
        return entry
    if (doc.get("source") or {}).get("sha256") != sha:
        entry["error"] = {"code": EXIT_INPUT, "detail": "ingestion source hash differs from the file list"}
        entry["intermediates"] = [out.name]
        return entry
    try:
        errs = validate_ingestion.check(out, ing_validator, root)
    except (OSError, json.JSONDecodeError) as exc:
        errs = [f"unreadable: {exc}"]
    if errs:
        entry["error"] = {"code": EXIT_SCHEMA, "detail": "ingestion file failed validation: " + "; ".join(errs[:3])}
        entry["intermediates"] = [out.name]
        return entry
    entry.update(status="extracted", output=out.name)
    return entry


def dwg_entry(bdir, path, sha, ing_validator, root):
    stem = Path(path).stem
    logp, dxf, out = bdir / f"{stem}.convert.json", bdir / f"{stem}.dxf", bdir / f"{stem}.dwg.json"
    inter = [p.name for p in (dxf, logp) if p.is_file()]
    entry = {"path": path, "sha256": sha, "type": "dwg", "status": "failed", "output": None,
             "intermediates": inter, "error": None}
    log = read_json(logp)

    def fail(code, detail):
        entry["error"] = {"code": int(code), "detail": str(detail)[:500]}
        return entry, log

    if log is None:
        return fail(EXIT_INPUT, "no readable converter log")
    if log.get("status") != "ok":
        return fail(log.get("code", EXIT_INPUT), log.get("detail", "conversion failed"))
    if (log.get("input") or {}).get("sha256") != sha:
        return fail(EXIT_INPUT, "converter log input hash differs from the file list")
    if not out.is_file():
        return fail(EXIT_INPUT, "extraction produced no ingestion file")
    try:
        errs = validate_ingestion.check(out, ing_validator, root)
    except (OSError, json.JSONDecodeError) as exc:
        errs = [f"unreadable: {exc}"]
    if errs:
        entry["intermediates"] = inter + [out.name]   # keep it listed (C3) but not as an output
        return fail(EXIT_SCHEMA, "ingestion file failed validation: " + "; ".join(errs[:3]))
    entry.update(status="extracted", output=out.name)
    return entry, log


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch-dir", required=True)
    ap.add_argument("--run", required=True)
    ap.add_argument("--site", required=True)
    ap.add_argument("--sub", required=True)
    ap.add_argument("--file", nargs=2, action="append", required=True, metavar=("PATH", "SHA256"))
    ap.add_argument("--root")
    a = ap.parse_args(argv)
    try:
        from jsonschema import Draft202012Validator, FormatChecker
    except ImportError:
        emit({"status": "error", "code": EXIT_DEPENDENCY, "detail": "jsonschema is not installed"})
        return EXIT_DEPENDENCY
    try:
        bdir = check_path(a.batch_dir)
        if not bdir.is_dir():
            raise DwgError(EXIT_INPUT, f"batch folder not found: {a.batch_dir}")
        if not re.fullmatch(r"b[0-9]{2}", bdir.name):
            raise DwgError(EXIT_INPUT, f"batch folder name must be b<nn>: {bdir.name}")
        for _, sha in a.file:
            if not SHA_RE.match(sha):
                raise DwgError(EXIT_INPUT, f"not a SHA-256: {sha}")
    except DwgError as e:
        emit({"status": "error", "code": e.code, "detail": e.detail})
        return e.code
    vi = Draft202012Validator(json.loads(INGESTION_SCHEMA.read_text(encoding="utf-8")), format_checker=FormatChecker())
    vb = Draft202012Validator(json.loads(BATCH_SCHEMA.read_text(encoding="utf-8")))
    files, engines, ok_engines = [], set(), set()
    for path, sha in sorted(a.file):
        path = path.replace("\\", "/")
        ext = Path(path).suffix.lower()
        if ext == ".dwg":
            entry, log = dwg_entry(bdir, path, sha, vi, a.root)
            if log and log.get("engine"):
                (ok_engines if log.get("status") == "ok" else engines).add((log.get("engine"), log.get("engine_version")))
        elif ext == ".pdf":
            entry = pdf_entry(bdir, path, sha, vi, a.root)
        else:
            entry = {"path": path, "sha256": sha, "type": "other", "status": "skipped", "output": None,
                     "error": {"code": EXIT_INPUT, "detail": "file type not handled by intake-batch"}}
        files.append(entry)
    engines = ok_engines or engines        # the engine of failed conversions only when none succeeded
    if len(engines) == 1:
        name, ver = next(iter(engines))
        converter = {"name": name, "version": ver}
    elif engines:
        converter = {"name": "+".join(sorted({e for e, _ in engines})),
                     "version": "+".join(sorted({str(v) for _, v in engines}))}
    else:
        converter = {"name": None, "version": None}
    counts = {s: sum(1 for f in files if f["status"] == s) for s in ("extracted", "failed", "skipped")}
    manifest = {"run_id": a.run, "site": a.site, "sub_id": a.sub, "batch": bdir.name, "files": files,
                "converter": converter, "counts": counts}
    errs = [f"{'/'.join(map(str, e.absolute_path)) or '(root)'}: {e.message[:160]}" for e in vb.iter_errors(manifest)]
    if errs:
        emit({"status": "fail", "code": EXIT_SCHEMA, "errors": errs[:20]})
        return EXIT_SCHEMA
    out = bdir / "batch_manifest.json"
    atomic_write_json(out, manifest)
    emit({"status": "ok", "out": out.as_posix(), "counts": counts, "converter": converter})
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
