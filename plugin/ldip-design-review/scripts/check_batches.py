#!/usr/bin/env python3
"""Batch-ownership check for intake-batch output (SPC-0002 C3; P-S3-4).

The guard lets every intake-batch instance write any b<nn>/ folder of its submittal, because a
hook cannot tell which batch an instance owns. This check closes the gap after the parallel step:
every file in 0.ai_context/<SITE>/<sub-id>/b<nn>/ must be batch_manifest.json or an output listed
in THAT folder's batch_manifest.json, and no manifest may list an output in another batch folder.
Intended to be called by format_check.py after the intake-batch step.

Usage: python check_batches.py --root R --site S --sub ID [--out FILE]
Prints a one-line JSON summary; exit 0 when the check ran (pass/fail in the result), 2 input error.
No network, no shell calls.
"""
__version__ = "0.1.0"

import argparse
import json
import sys
from pathlib import Path, PurePosixPath

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ldip_common import EXIT_INPUT, EXIT_OK, ScriptError, atomic_write_json, iso_utc, read_json  # noqa: E402


def check(root, site, sub):
    root = Path(root).resolve()
    base = root / "0.ai_context" / site / sub
    if not base.is_dir():
        raise ScriptError(EXIT_INPUT, f"extraction folder not found: {base}")
    violations, batches = [], sorted(p for p in base.glob("b*") if p.is_dir())
    for bdir in batches:
        rel_b = bdir.relative_to(root).as_posix()
        mpath = bdir / "batch_manifest.json"
        if not mpath.is_file():
            violations.append({"batch": bdir.name, "issue": "batch_manifest.json missing"})
            continue
        try:
            m = read_json(mpath)
        except ScriptError as e:
            violations.append({"batch": bdir.name, "issue": f"batch_manifest unreadable: {e.detail}"})
            continue
        expected = {f"{rel_b}/batch_manifest.json"}
        for f in m.get("files", []):
            out = f.get("output")
            if not out:
                continue
            out_rel = str(PurePosixPath(str(out).replace("\\", "/")))
            if "/" not in out_rel:
                out_rel = f"{rel_b}/{out_rel}"
            if not out_rel.startswith(rel_b + "/"):
                violations.append({"batch": bdir.name, "issue": "manifest lists an output in another folder",
                                   "path": out_rel})
            expected.add(out_rel)
        for fp in sorted(bdir.rglob("*")):
            if fp.is_file():
                rel = fp.relative_to(root).as_posix()
                if rel not in expected:
                    violations.append({"batch": bdir.name, "issue": "file not listed in this batch's manifest",
                                       "path": rel})
    return {"check": "C3 batch ownership", "script_version": __version__, "site": site, "sub_id": sub,
            "batches": [b.name for b in batches], "violations": violations,
            "result": "fail" if violations else "pass", "checked_at": iso_utc()}


def main(argv=None):
    ap = argparse.ArgumentParser(description="LDIP intake-batch ownership check")
    ap.add_argument("--root", required=True)
    ap.add_argument("--site", required=True)
    ap.add_argument("--sub", required=True)
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    try:
        r = check(a.root, a.site, a.sub)
        if a.out:
            atomic_write_json(a.out, r)
        print(json.dumps({"status": "ok", "result": r["result"], "batches": len(r["batches"]),
                          "violations": len(r["violations"])}))
        return EXIT_OK
    except ScriptError as e:
        print(json.dumps({"status": "error", "code": e.code, "detail": e.detail}))
        return e.code


if __name__ == "__main__":
    sys.exit(main())
