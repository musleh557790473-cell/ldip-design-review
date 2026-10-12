#!/usr/bin/env python3
"""Merge the batch manifests of one submittal into the file list used by build_register.py (review run).

Usage: python merge_batches.py --context 0.ai_context/<SITE>/<sub-id> --run <run-id> --out <files.json>
Reads every b*/batch_manifest.json under --context (written by intake-batch, SPC-0006 §3.1) and writes
the same shape as inventory.py: one entry per received file with path (relative to the LDIP_Review
folder, the working directory), format, size, SHA-256, document number from the name, and the ingestion
file and extraction status from the batch. A file listed by two batches, or a manifest from another run,
is an input error. Run from the LDIP_Review folder.
Exit: 0 written · 2 input error. No network, no shell calls.
"""
__version__ = "0.1.0"

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pkg_common import (EXIT_INPUT, EXIT_OK, atomic_write_json, emit, fail, format_of, number_from_name,  # noqa: E402
                        path_ok, read_json)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--context", required=True)
    ap.add_argument("--run", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    ctx = Path(a.context)
    if not path_ok(a.out):
        return fail(EXIT_INPUT, "output path longer than 250 characters")
    manifests = sorted(ctx.glob("b*/batch_manifest.json"))
    if not manifests:
        return fail(EXIT_INPUT, f"no b*/batch_manifest.json under {ctx}")
    files, seen, site, sub = [], {}, None, None
    for m in manifests:
        try:
            bm = read_json(m)
        except (OSError, ValueError) as exc:
            return fail(EXIT_INPUT, f"cannot read {m}: {exc}")
        if bm.get("run_id") != a.run:
            return fail(EXIT_INPUT, f"{m} belongs to run {bm.get('run_id')}, not {a.run}")
        site, sub = bm["site"], bm["sub_id"]
        for f in bm["files"]:
            if f["path"] in seen:
                return fail(EXIT_INPUT, f"{f['path']} listed by {seen[f['path']]} and {bm['batch']}")
            seen[f["path"]] = bm["batch"]
            p = Path(f["path"])
            files.append({"path": p.as_posix(), "name": p.name, "ext": p.suffix.lower(), "format": format_of(p),
                          "bytes": p.stat().st_size if p.exists() else None, "sha256": f["sha256"],
                          "number": number_from_name(p.name), "kind": "file", "batch": bm["batch"],
                          "extraction": {"status": f["status"], "ingestion": f.get("output"), "error": f.get("error")}})
    files.sort(key=lambda f: f["path"])
    doc = {"tool": {"name": "merge_batches.py", "version": __version__}, "mode": "run", "root": ".",
           "run_id": a.run, "site": site, "sub_id": sub, "files": files, "skipped": [],
           "counts": {"files": len(files), "with_number": sum(1 for f in files if f["number"]),
                      "batches": len(manifests)}}
    atomic_write_json(a.out, doc)
    emit({"status": "ok", "out": str(a.out), **doc["counts"]})
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
