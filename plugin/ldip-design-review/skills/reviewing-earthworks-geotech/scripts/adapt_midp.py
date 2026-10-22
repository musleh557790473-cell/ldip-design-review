#!/usr/bin/env python3
"""Adapt skill 6 read_midp.py output → compact earthworks MIDP JSON (D5).

Usage:
  python adapt_midp.py --midp <midp.json> [--stage 30] --out <midp_earthworks.json>
"""
__version__ = "0.1.1"

import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from ewk_common import EXIT_OK, adapt_midp_rows, atomic_write_json, emit, main_guard, read_json  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--midp", required=True, help="skill 6 read_midp.py JSON (or compact models)")
    ap.add_argument("--stage", default="30", help="stage hint (default 30)")
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)

    def run():
        doc = read_json(a.midp)
        entries = adapt_midp_rows(doc, stage_hint=str(a.stage))
        out = {
            "tool": {"script": "adapt_midp.py", "version": __version__},
            "source": str(a.midp),
            "stage_hint": str(a.stage),
            "models": entries,
            "counts": {"models": len(entries)},
        }
        atomic_write_json(a.out, out)
        emit({"status": "ok", "out": a.out, "models": len(entries)})
        return EXIT_OK

    return main_guard(run)


if __name__ == "__main__":
    sys.exit(main())
