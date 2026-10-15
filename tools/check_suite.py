#!/usr/bin/env python3
"""Release test check (owner, 15 Oct 2026 13:07 — after the session 18 / 19 suite-count difference).

Runs the full test suite and fails the release step when:
  1. the number of collected tests differs from --expect (the number recorded for the build), or
  2. any test or test module was skipped because a package could not be imported
     (pytest.importorskip: "could not import ..." / "No module named ...").
Skips for real data kept out of Git or for missing Tesseract language data are allowed and listed.

Usage: python tools/check_suite.py [--expect N] [--tests tests]
Exit: 0 pass · 1 tests failed · 5 check failed (count or package skip). Repository tool, not part of the plugin.
"""
import argparse
import json
import re
import subprocess
import sys

PACKAGE_SKIP = re.compile(r"could not import|No module named", re.I)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--expect", type=int)
    ap.add_argument("--tests", default="tests")
    a = ap.parse_args(argv)
    co = subprocess.run([sys.executable, "-m", "pytest", "-q", "--co", a.tests], capture_output=True, text=True)
    m = re.search(r"(\d+) tests? collected", co.stdout)
    collected = int(m.group(1)) if m else None
    res = subprocess.run([sys.executable, "-m", "pytest", "-q", "-rs", a.tests], capture_output=True, text=True)
    skips = [ln.split("SKIPPED", 1)[1].strip() for ln in res.stdout.splitlines() if ln.startswith("SKIPPED")]
    tail = res.stdout.strip().splitlines()[-1] if res.stdout.strip() else ""
    problems = []
    if collected is None:
        problems.append("could not read the collected test count")
    elif a.expect is not None and collected != a.expect:
        problems.append(f"collected {collected} tests, expected {a.expect}")
    problems += [f"package skip: {s}" for s in skips if PACKAGE_SKIP.search(s)]
    out = {"collected": collected, "result": tail, "skips": skips, "problems": problems}
    print(json.dumps(out, ensure_ascii=False))
    if res.returncode not in (0, 5):
        return 1
    return 5 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
