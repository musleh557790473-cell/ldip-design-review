#!/usr/bin/env python3
"""Write batch_manifest.json for one intake batch from what is on disk (SPC-0006 §3.1; D8, 0.2.2; P7).

The agent no longer writes the manifest by hand. For each input of the batch the script reads the
converter log (<stem>.convert.json) and the ingestion file (<stem>.dwg.json) in the batch folder,
validates the ingestion file (validate_ingestion.check), and sets status / output / intermediates /
error. PDFs (skill 2): the ingestion file <name>.pdf.json written by extract_pdf.py is validated →
`extracted`; missing or invalid → `failed`. LandXML (skill 3, P7): <file>.xml.json written by
parse_landxml.py is validated with its surface side files, its source hash and its provenance against
the folder rule (landxml_common.expected_provenance) → `extracted` with `side_files`; a .xml whose root is
not LandXML → `skipped` (type `other`); refused, missing or invalid → `failed`. Other files are
`skipped` (code 2). DWG linework side files (0.4.0, SPC-0006 Rev F §4.1A.4): an extracted DWG lists its
`<stem>.linework.json` under `side_files`; a linework file left by a failed extraction is an intermediate.
An extracted PDF lists its `<file>.paths.json` (skill 2 --paths) under `side_files`.
The manifest is validated against schemas/batch_manifest.schema.json before it is written.

Usage: python write_manifest.py --out <0.ai_context/SITE/SUB/bNN>/batch_manifest.json | --batch-dir <…/bNN>
                                --run <run_id> --site <SITE> --sub <sub_id>
                                --file <path> <sha256> [--file <path> <sha256> ...] [--root <LDIP_Review>]
--out (0.3.0, G19-1) is the form used in a review run: it names a file inside the batch folder, which the
guard can check before the folder exists, so a batch whose files were all refused before anything was
written still gets a manifest (all entries `failed`); the folder is created. --batch-dir needs the folder.
Exit: 0 written · 2 input error · 3 jsonschema missing · 4 manifest failed its schema (not written).
No network, no shell calls.
"""
__version__ = "0.4.0"

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
# Skill 3 (parsing-landxml): one implementation of "is this LandXML" and of the provenance folder rule.
sys.path.insert(0, str(PLUGIN_ROOT / "skills" / "parsing-landxml" / "scripts"))
import landxml_common  # noqa: E402
import parse_landxml  # noqa: E402
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
    name = Path(path).name
    side = bdir / ((name[:-4] if name.lower().endswith(".pdf") else name) + ".paths.json")
    orphan = [side.name] if side.is_file() else []
    entry = {"path": path, "sha256": sha, "type": "pdf", "status": "failed", "output": None, "error": None}

    def fail(code, detail, keep_out=True):
        inter = ([out.name] if keep_out else []) + orphan
        if inter:
            entry["intermediates"] = inter
        entry["error"] = {"code": code, "detail": detail}
        return entry

    if not out.is_file():
        return fail(EXIT_INPUT, PDF_MISSING, keep_out=False)
    doc = read_json(out)
    if doc is None:
        return fail(EXIT_SCHEMA, "ingestion file is not readable JSON")
    if (doc.get("source") or {}).get("sha256") != sha:
        return fail(EXIT_INPUT, "ingestion source hash differs from the file list")
    try:
        errs = validate_ingestion.check(out, ing_validator, root)
    except (OSError, json.JSONDecodeError) as exc:
        errs = [f"unreadable: {exc}"]
    if errs:
        return fail(EXIT_SCHEMA, "ingestion file failed validation: " + "; ".join(errs[:3]))
    entry.update(status="extracted", output=out.name)
    sides = [x["path"] for x in doc.get("content", {}).get("side_files", [])]
    if sides:
        entry["side_files"] = sides
    stale = [o for o in orphan if o not in sides]
    if stale:
        entry["intermediates"] = stale
    return entry


def landxml_entry(bdir, path, sha, ing_validator, root):
    name = Path(path).name
    stem = name[:-4] if name.lower().endswith(".xml") else name
    out = bdir / f"{name}.json"
    entry = {"path": path, "sha256": sha, "type": "landxml", "status": "failed", "output": None, "error": None}
    orphans = sorted(p.name for p in bdir.glob(f"{stem}.surface-*.json") if p.is_file())

    def fail(code, detail, keep_out=False):
        inter = ([out.name] if keep_out else []) + orphans
        if inter:
            entry["intermediates"] = inter     # on disk and owned by this batch (C3), not an output
        entry["error"] = {"code": int(code), "detail": str(detail)[:500]}
        return entry

    if not out.is_file():
        status, code, detail = parse_landxml.classify(Path(root or ".") / path)
        if status == "skipped":
            entry.update(type="other", status="skipped", error={"code": int(code), "detail": detail})
            if orphans:
                entry["intermediates"] = orphans
            return entry
        return fail(code, detail)
    doc = read_json(out)
    if doc is None:
        return fail(EXIT_SCHEMA, "ingestion file is not readable JSON", True)
    if (doc.get("source") or {}).get("sha256") != sha:
        return fail(EXIT_INPUT, "ingestion source hash differs from the file list", True)
    try:
        errs = validate_ingestion.check(out, ing_validator, root)
    except (OSError, json.JSONDecodeError) as exc:
        errs = [f"unreadable: {exc}"]
    if errs:
        return fail(EXIT_SCHEMA, "ingestion file failed validation: " + "; ".join(errs[:3]), True)
    want = landxml_common.expected_provenance(path)
    got = doc["source"].get("provenance")
    if want is None:
        return fail(EXIT_INPUT, "no provenance rule for this location in a review run "
                                "(only submittals/<SITE>/<sub-id>/; brief Rev G §11 item 26)", True)
    if got != want:
        return fail(EXIT_INPUT, f"provenance '{got}' differs from the folder rule ('{want}', SPC-0006 Rev C §4.12.4)", True)
    sides = [s["tin_file"] for s in doc["content"].get("surfaces", [])]
    entry.update(status="extracted", output=out.name)
    if sides:
        entry["side_files"] = sides
    stale = [o for o in orphans if o not in sides]
    if stale:
        entry["intermediates"] = stale         # left by an earlier attempt; listed so C3 accounts for them
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

    lw = bdir / f"{stem}.linework.json"
    if lw.is_file():
        entry["intermediates"] = inter = inter + [lw.name]    # until the ingestion file lists it
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
    sides = [x["path"] for x in (read_json(out) or {}).get("content", {}).get("side_files", [])]
    if sides:
        entry["side_files"] = sides
        entry["intermediates"] = [i for i in inter if i not in sides]
    return entry, log


def main(argv=None):
    ap = argparse.ArgumentParser()
    where = ap.add_mutually_exclusive_group(required=True)
    where.add_argument("--batch-dir")
    where.add_argument("--out")
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
        if a.out:
            outp = check_path(a.out)
            if outp.name != "batch_manifest.json":
                raise DwgError(EXIT_INPUT, f"--out must be <batch folder>/batch_manifest.json: {a.out}")
            bdir = outp.parent
            if bdir.exists() and not bdir.is_dir():
                raise DwgError(EXIT_INPUT, f"not a folder: {bdir}")
        else:
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
        elif ext == ".xml":
            entry = landxml_entry(bdir, path, sha, vi, a.root)
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
