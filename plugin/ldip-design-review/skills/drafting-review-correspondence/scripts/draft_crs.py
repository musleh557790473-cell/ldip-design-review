#!/usr/bin/env python3
"""Build the DRAFT correspondence content from a findings view (skill 22, SPC-0006 §4.8; P-S22-4, P-S22-5).

Usage: python draft_crs.py --view <findings_view.json> --out <drafts.json> [--review-no R0]
           [--header key=value ...]
The view comes from load_findings.py (skill 24). Only verified findings are drafted (P-S22-4):
  CRS route        one CRS row per finding, grouped in one CRS per site + submittal; text composed
                   mechanically from the finding (statement, reference, up to two evidence extracts),
                   never reworded (P-S22-5)
  RFI route        one RFI per finding (subject = title, information requested = statement + reference)
  ENGINEER_QUEUE   listed (Probable)       MANUAL_CHECK  listed (Not auto-verifiable)
  anything else    listed in not_drafted[] with the reason (UNVERIFIED candidates, NONE, RISK_EOT_REGISTER,
                   INCONSISTENT)
A view with candidates only is refused (exit 5): run skill 18 first. Header values come from the site
table in dr_common.py [PROPOSED]; --header overrides one (keys: project, section, sub_section, location,
reviewer_org, contractor, crs_ref, rfi_ref, subject, document_no, pmcm). Cover letters are not drafted
(no template yet [GAP]). Every draft carries the DRAFT marking and the plugin version.
Exit: 0 written · 2 input error · 3 jsonschema missing · 4 drafts failed drafts.schema.json · 5 refused.
No network, no shell calls.
"""
__version__ = "0.1.0"

import argparse
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from dr_common import (DRAFTS_SCHEMA, EXIT_DEP, EXIT_INPUT, EXIT_OK, EXIT_REFUSED, EXIT_SCHEMA, RIYADH,  # noqa: E402
                       atomic_write_json, build_drafts, emit, fail, marking, path_ok, plugin_version, read_json,
                       sha256_file)

HEADER_KEYS = {"project", "section", "sub_section", "location", "reviewer_org", "contractor", "crs_ref", "rfi_ref",
               "subject", "document_no", "pmcm"}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--view", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--review-no", default="R0")
    ap.add_argument("--header", nargs="*", default=[])
    a = ap.parse_args(argv)
    for p in (a.view, a.out):
        if not path_ok(p):
            return fail(EXIT_INPUT, f"path longer than 250 characters: {p}")
    overrides = {}
    for kv in a.header:
        k, sep, v = kv.partition("=")
        if not sep or k not in HEADER_KEYS:
            return fail(EXIT_INPUT, f"--header expects key=value with key in {sorted(HEADER_KEYS)}: {kv}")
        overrides[k] = v
    try:
        import jsonschema
    except ImportError:
        return fail(EXIT_DEP, "jsonschema is not installed")
    try:
        view = read_json(a.view)
    except (OSError, ValueError) as exc:
        return fail(EXIT_INPUT, f"cannot read {a.view}: {exc}")
    if view.get("view_version") != "1.0" or not isinstance(view.get("findings"), list):
        return fail(EXIT_INPUT, f"{a.view} is not a findings view from load_findings.py")
    rows = view["findings"]
    if rows and all(r["stage"] == "candidate" for r in rows):
        return fail(EXIT_REFUSED, "only unverified candidates in the view: run skill 18 (verifying-review-findings) "
                                  "first; no draft is made from a candidate (P-S22-4)", candidates=len(rows))
    drafts = build_drafts(view, a.review_no, overrides)
    doc = {"drafts_version": "1.0", "marking": marking("draft_crs.py", __version__),
           "plugin_version": plugin_version(), "review_no": a.review_no, "header_overrides": overrides,
           "view": {"path": a.view, "sha256": sha256_file(a.view)}, **drafts,
           "meta": {"generated_at": datetime.now(RIYADH).isoformat(timespec="seconds"),
                    "script": "draft_crs.py", "version": __version__,
                    "note": "Text composed from the findings without rewording (P-S22-5). Nothing is issued."}}
    errs = list(jsonschema.Draft202012Validator(read_json(DRAFTS_SCHEMA)).iter_errors(doc))
    if errs:
        return fail(EXIT_SCHEMA, "drafts failed drafts.schema.json; nothing written",
                    errors=[f"{'/'.join(map(str, e.absolute_path))}: {e.message[:200]}" for e in errs[:10]])
    atomic_write_json(a.out, doc)
    emit({"status": "ok", **drafts["counts"], "out": a.out})
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
