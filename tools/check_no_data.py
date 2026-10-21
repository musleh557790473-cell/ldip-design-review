#!/usr/bin/env python3
"""Pre-commit guard (brief §10.2, project rule 5). Fails if a staged or tracked file
looks like project data — by name/extension **or by content fingerprints** of deposited
rulebook values (so real rules_v7 numbers cannot sneak into Git as a “fixture”).

Usage: python tools/check_no_data.py [--all]
"""
__version__ = "0.4.0"
import json
import re
import subprocess
import sys
from pathlib import Path

BLOCK_EXT = {".dwg", ".dxf", ".dwf", ".dwfx", ".pdf", ".xlsx", ".xls", ".xlsm", ".csv", ".doc",
             ".docx", ".zip", ".rar", ".7z", ".shp", ".shx", ".dbf", ".ifc", ".rvt", ".imx",
             ".png", ".jpg", ".tif", ".tiff", ".xml", ".landxml", ".pnt", ".tri"}
# LandXML (skill 3) is .xml: only generated synthetic eval files are allowed (SPC-0006 Rev C §4.12.12 T5).
SYNTHETIC_OK = re.compile(r"^plugin/[^/]+/skills/[^/]+/evals/synthetic_[a-z0-9_]+\.xml$")
BLOCK_NAME = re.compile(r"(rules_v\d+\.json$|DLUS|answer_key|golden_set/|submittals/|LDIP-RCRC-SAP-)", re.I)
# Skill scripts named in SPC-0006 (e.g. read_dlus.py) are code, not data: the DLUS name test skips them.
CODE_OK = re.compile(r"^plugin/[^/]+/skills/[^/]+/scripts/[a-z0-9_]+\.py$")
MAX_BYTES = 1_000_000

# Distinctive deposited rules_v7 ROD-043 text / clause (owner deposit; must stay out of Git).
# Bare numbers like 2.0 / 0.5 appear elsewhere legitimately — fingerprint the rule text instead.
# Built from parts so this guard file does not contain a contiguous copy of the deposit.
def _content_block_substrings():
    en_dash, hyphen = "\u2013", "-"
    clause = "\u00a7" + "7.12(c) p.59"
    apron = "1.5" + en_dash + "2.0 m mountable apron"
    apron_ascii = "1.5" + hyphen + "2.0 m mountable apron"
    stmt_tail = "circulatory crossfall 2 %; drainage gradient at least 0.5 %."
    return (
        "The central island shall have a " + apron + "; " + stmt_tail,
        "The central island shall have a " + apron_ascii + "; " + stmt_tail,
        clause,
        "circulatory crossfall 2 %; drainage gradient at least 0.5",
        apron,
        apron_ascii,
    )


CONTENT_BLOCK_SUBSTRINGS = _content_block_substrings()
# Do not content-scan this guard (fingerprints are assembled here by design).
SKIP_CONTENT_SCAN = frozenset({"tools/check_no_data.py"})

# Text-ish suffixes scanned for content fingerprints (skip binaries / huge blobs).
TEXT_SUFFIX = {".json", ".md", ".txt", ".py", ".yml", ".yaml", ".toml", ".csv", ".html",
               ".rst", ".ini", ".cfg", ".xml", ".svg"}

# ROD-043 param shape matching the deposited numeric triple (not synthetic CI).
REAL_043_CROSSFALL = 2.0
REAL_043_DRAINAGE = 0.5
REAL_043_APRON = (1.5, 2.0)


def files(all_tracked):
    cmd = ["git", "ls-files"] if all_tracked else ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR"]
    out = subprocess.run(cmd, capture_output=True, text=True, check=True).stdout
    return [l for l in out.splitlines() if l.strip()]


def _read_text(path: Path):
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _rod043_params_look_real(doc) -> bool:
    """True if a JSON object carries deposited ROD-043 numeric values (not synthetic CI)."""
    if not isinstance(doc, dict):
        return False
    rules = doc.get("rules")
    candidates = []
    if isinstance(rules, list):
        candidates.extend(r for r in rules if isinstance(r, dict) and r.get("rule_id") == "ROD-043")
    if doc.get("rule_id") == "ROD-043":
        candidates.append(doc)
    for rule in candidates:
        params = rule.get("parameters") or {}
        if not isinstance(params, dict):
            continue
        cf = params.get("circulatory_crossfall") or {}
        dr = params.get("drainage_g_min") or {}
        ap = params.get("apron_width") or {}
        try:
            cf_v = float(cf["value"]) if isinstance(cf, dict) and "value" in cf else None
            dr_v = float(dr["value"]) if isinstance(dr, dict) and "value" in dr else None
            ap_v = ap.get("value") if isinstance(ap, dict) else None
        except (TypeError, ValueError):
            continue
        if cf_v == REAL_043_CROSSFALL and dr_v == REAL_043_DRAINAGE:
            return True
        if isinstance(ap_v, (list, tuple)) and len(ap_v) == 2:
            try:
                if (float(ap_v[0]), float(ap_v[1])) == REAL_043_APRON and (
                        cf_v == REAL_043_CROSSFALL or dr_v == REAL_043_DRAINAGE):
                    return True
            except (TypeError, ValueError):
                pass
    return False


def content_hits(rel: str, path: Path):
    """Return reasons if file content looks like deposited rulebook data."""
    if rel.replace("\\", "/") in SKIP_CONTENT_SCAN:
        return []
    if path.suffix.lower() not in TEXT_SUFFIX and path.name != "rules_v7.json":
        return []
    if not path.is_file() or path.stat().st_size > MAX_BYTES:
        return []
    text = _read_text(path)
    if text is None:
        return []
    hits = []
    for s in CONTENT_BLOCK_SUBSTRINGS:
        if s and s in text:
            hits.append(f"deposited rulebook text fingerprint: {s[:60]}…")
            break
    if path.suffix.lower() == ".json":
        try:
            doc = json.loads(text)
        except json.JSONDecodeError:
            doc = None
        if doc is not None and _rod043_params_look_real(doc):
            hits.append("ROD-043 parameters match deposited rules_v7 numbers (use synthetic CI fixture)")
    return hits


def main():
    bad = []
    scan_all = "--all" in sys.argv
    for f in files(scan_all):
        p = Path(f)
        if p.suffix.lower() in BLOCK_EXT and not SYNTHETIC_OK.match(f):
            bad.append((f, "data file type"))
        elif BLOCK_NAME.search(f) and not (CODE_OK.match(f) and not re.search(r"rules_v\d|LDIP-RCRC-SAP-", f, re.I)):
            bad.append((f, "project-data name pattern"))
        elif p.exists() and p.stat().st_size > MAX_BYTES:
            bad.append((f, "larger than 1 MB"))
        else:
            if p.exists():
                for why in content_hits(f, p):
                    bad.append((f, why))
    for f, why in bad:
        print(f"BLOCKED {f}: {why}")
    print(f"check_no_data: {len(bad)} blocked")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
