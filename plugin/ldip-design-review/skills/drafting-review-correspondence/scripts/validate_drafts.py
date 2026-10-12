#!/usr/bin/env python3
"""Check DRAFT correspondence content against its findings view (skill 22, P-S22-5).

Usage: python validate_drafts.py --drafts <drafts.json> --view <findings_view.json>
Checks: (1) drafts.schema.json; (2) the DRAFT marking; (3) the view file is the one the drafts were built
from (SHA-256); (4) no CRS row or RFI comes from an unverified candidate; (5) the drafts equal a fresh
mechanical build from the view, field by field — so any reworded comment, added or dropped row, or changed
status is reported. Nothing is corrected.
Exit: 0 pass · 2 input error · 3 jsonschema missing · 4 a check failed. No network, no shell calls.
"""
__version__ = "0.1.0"

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from dr_common import (DRAFT, DRAFTS_SCHEMA, EXIT_DEP, EXIT_INPUT, EXIT_OK, EXIT_SCHEMA, build_drafts,  # noqa: E402
                       emit, fail, path_ok, read_json, sha256_file)

PARTS = ("crs", "rfi", "engineer_queue", "manual_check", "not_drafted", "counts")


def diff(path, a, b, out):
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b)):
            diff(f"{path}/{k}", a.get(k, "<missing>"), b.get(k, "<missing>"), out)
    elif isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            out.append(f"{path}: {len(b)} item(s) in the drafts, {len(a)} expected")
        for i, (x, y) in enumerate(zip(a, b)):
            diff(f"{path}/{i}", x, y, out)
    elif a != b:
        out.append(f"{path}: drafts hold {json.dumps(b, ensure_ascii=False)[:120]}, "
                   f"expected {json.dumps(a, ensure_ascii=False)[:120]}")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--drafts", required=True)
    ap.add_argument("--view", required=True)
    a = ap.parse_args(argv)
    for p in (a.drafts, a.view):
        if not path_ok(p) or not Path(p).is_file():
            return fail(EXIT_INPUT, f"file not found or path too long: {p}")
    try:
        import jsonschema
    except ImportError:
        return fail(EXIT_DEP, "jsonschema is not installed")
    try:
        drafts, view = read_json(a.drafts), read_json(a.view)
    except (OSError, ValueError) as exc:
        return fail(EXIT_INPUT, f"cannot read input: {exc}")
    problems = [f"schema: {'/'.join(map(str, e.absolute_path))}: {e.message[:200]}"
                for e in jsonschema.Draft202012Validator(read_json(DRAFTS_SCHEMA)).iter_errors(drafts)]
    if problems:
        emit({"status": "fail", "code": EXIT_SCHEMA, "problems": problems[:20], "count": len(problems)})
        return EXIT_SCHEMA
    if not drafts["marking"].startswith(DRAFT):
        problems.append("DRAFT marking missing")
    if drafts["view"]["sha256"] != sha256_file(a.view):
        problems.append("the view is not the file the drafts were built from (SHA-256 differs)")
    stage = {r["finding_id"]: r["stage"] for r in view["findings"]}
    for c in drafts["crs"]:
        for r in c["rows"]:
            if stage.get(r["finding_id"], "candidate") == "candidate":
                problems.append(f"CRS row {r['no']} ({r['finding_id']}) is not a verified finding")
    for r in drafts["rfi"]:
        if stage.get(r["finding_id"], "candidate") == "candidate":
            problems.append(f"RFI {r['no']} ({r['finding_id']}) is not a verified finding")
    expected = build_drafts(view, drafts["review_no"], drafts["header_overrides"])
    for part in PARTS:
        diff(part, expected[part], drafts[part], problems)
    if problems:
        emit({"status": "fail", "code": EXIT_SCHEMA, "problems": problems[:30], "count": len(problems)})
        return EXIT_SCHEMA
    emit({"status": "pass", **drafts["counts"]})
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
