#!/usr/bin/env python3
"""Pre-commit guard (brief §10.2, project rule 5). Fails if a staged or tracked file
looks like project data. Usage: python tools/check_no_data.py [--all]"""
__version__ = "0.1.0"
import re, subprocess, sys
from pathlib import Path

BLOCK_EXT = {".dwg", ".dxf", ".dwf", ".dwfx", ".pdf", ".xlsx", ".xls", ".xlsm", ".csv", ".doc",
             ".docx", ".zip", ".rar", ".7z", ".shp", ".shx", ".dbf", ".ifc", ".rvt", ".imx",
             ".png", ".jpg", ".tif", ".tiff"}
BLOCK_NAME = re.compile(r"(rules_v\d+\.json$|DLUS|answer_key|golden_set/|submittals/|LDIP-RCRC-SAP-)", re.I)
MAX_BYTES = 1_000_000

def files(all_tracked):
    cmd = ["git", "ls-files"] if all_tracked else ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR"]
    out = subprocess.run(cmd, capture_output=True, text=True, check=True).stdout
    return [l for l in out.splitlines() if l.strip()]

def main():
    bad = []
    for f in files("--all" in sys.argv):
        p = Path(f)
        if p.suffix.lower() in BLOCK_EXT:
            bad.append((f, "data file type"))
        elif BLOCK_NAME.search(f):
            bad.append((f, "project-data name pattern"))
        elif p.exists() and p.stat().st_size > MAX_BYTES:
            bad.append((f, "larger than 1 MB"))
    for f, why in bad:
        print(f"BLOCKED {f}: {why}")
    print(f"check_no_data: {len(bad)} blocked")
    return 1 if bad else 0

if __name__ == "__main__":
    sys.exit(main())
