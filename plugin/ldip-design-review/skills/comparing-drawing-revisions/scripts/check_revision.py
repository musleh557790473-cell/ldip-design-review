#!/usr/bin/env python3
"""PKG-010 revision advance check for paired drawings (skill 7 M2).

Reads revision from the file name and from the PDF title-block revision field (skill 2 text).
Orders codes per the PKG-010 method text returned by skill 8 get_rules.py — never hard-codes
rule values or rule state.

Same-number pairs → PKG-010 result.
Renumbered pairs (number_map / proposed) with the same revision and changed content → Observation
(DM-3), not a PKG-010 hit.

Usage:
  python check_revision.py --pairs <pairs.json> --rules <rules_INT.json> --out <rev_diff.json>
       [--content-changed <pair_id_or_index csv>] [--entity-diff <entity_diff.json>]

Exit: 0 done · 2 input error. No network, no shell calls.
"""
__version__ = "0.1.0"

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from rev_common import (  # noqa: E402
    EXIT_INPUT, EXIT_OK, atomic_write_json, compare_revisions, emit, fail, load_rules,
    path_ok, read_json, revision_from_number, rule_pkg010,
)


def rev_of(side):
    """Prefer title-block revision, then explicit revision, then file-name suffix."""
    return (side.get("title_block_revision")
            or side.get("revision")
            or revision_from_number(side.get("number"))
            or None)


def content_changed_set(args, pairs):
    changed = set()
    if args.content_changed:
        for tok in str(args.content_changed).split(","):
            tok = tok.strip()
            if tok:
                changed.add(tok)
    if args.entity_diff:
        ed = read_json(args.entity_diff)
        for d in ed.get("entity_diffs") or ed.get("diffs") or []:
            if d.get("changed") or d.get("added") or d.get("removed") or d.get("moved"):
                if d.get("pair_index") is not None:
                    changed.add(str(d["pair_index"]))
                if d.get("pair_id"):
                    changed.add(str(d["pair_id"]))
        for d in ed.get("inventory_diff", {}).get("added") or []:
            changed.add("inventory")
    # Infer from pairs that carry an explicit flag
    for i, p in enumerate(pairs):
        if p.get("content_changed"):
            changed.add(str(i))
            changed.add(str(p.get("id") or ""))
    return changed


def check_pair(pair, index, rule, method, changed_ids):
    prev, curr = pair["previous"], pair["current"]
    old_r, new_r = rev_of(prev), rev_of(curr)
    comparison = compare_revisions(old_r, new_r, method)
    renumbered = bool(pair.get("renumbered") or pair.get("match") in ("number_map",) 
                      or (pair.get("match") == "title_sheet" and pair.get("renumbered")))
    if pair.get("match") == "number_map":
        renumbered = True
    pair_id = pair.get("id") or f"pair-{index}"
    content_changed = (
        str(index) in changed_ids
        or pair_id in changed_ids
        or str(prev.get("id")) in changed_ids
        or str(curr.get("id")) in changed_ids
        or pair.get("content_changed") is True
    )

    # DM-3: renumbered + same revision + changed content → Observation (not PKG-010)
    if renumbered and comparison == "same" and content_changed:
        return {
            "pair_index": index,
            "pair_id": pair_id,
            "match": pair.get("match"),
            "renumbered": True,
            "previous_revision": old_r,
            "current_revision": new_r,
            "comparison": comparison,
            "content_changed": True,
            "rule_id": None,
            "result": "observation",
            "severity": "Observation",
            "status": "Confirmed",
            "reason": "Renumbered pair keeps the same revision while content changed (DM-3); "
                      "not scored under PKG-010",
        }

    if rule is None:
        return {
            "pair_index": index,
            "pair_id": pair_id,
            "match": pair.get("match"),
            "renumbered": renumbered,
            "previous_revision": old_r,
            "current_revision": new_r,
            "comparison": comparison,
            "content_changed": content_changed,
            "rule_id": "PKG-010",
            "result": "not_run",
            "reason": "PKG-010 not returned by get_rules.py (inactive or absent)",
        }

    if comparison == "advanced":
        result, reason = "pass", "Revision advanced within the same series"
    elif comparison == "same":
        result, reason = "fail", "Revision code did not advance from the previous submittal"
    elif comparison == "not_advanced":
        result, reason = "fail", "Current revision is earlier than the previous revision"
    elif comparison == "missing":
        result, reason = "missing_information", "Revision code missing on previous or current"
    else:
        result, reason = "incomparable", "Revision codes are in different or unknown series"

    default_status = (rule.get("default_status") or {}).get("script_output", "Confirmed")
    cap = rule.get("cap") or "Confirmed"
    # Cap status to rule cap
    status = default_status
    rank = {"Confirmed": 3, "Missing information": 3, "Probable": 2, "Not auto-verifiable": 1}
    if rank.get(status, 0) > rank.get(cap, 3):
        status = cap
    if result == "missing_information":
        status = "Missing information" if cap == "Confirmed" else "Probable"

    return {
        "pair_index": index,
        "pair_id": pair_id,
        "match": pair.get("match"),
        "renumbered": renumbered,
        "previous_revision": old_r,
        "current_revision": new_r,
        "filename_revision": revision_from_number(curr.get("number")),
        "title_block_revision": curr.get("title_block_revision"),
        "comparison": comparison,
        "content_changed": content_changed,
        "rule_id": "PKG-010",
        "result": result,
        "severity": rule.get("severity_default") if result == "fail" else None,
        "status": status if result in ("fail", "missing_information") else None,
        "reason": reason,
        "rule_state": rule.get("state"),
        "rule_cap": cap,
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--pairs", required=True, help="Output of match_revisions.py")
    ap.add_argument("--rules", required=True, help="get_rules.py output (agent INT)")
    ap.add_argument("--out", required=True, help="rev_diff.json (created or merged)")
    ap.add_argument("--content-changed", help="Comma-separated pair indices/ids known changed")
    ap.add_argument("--entity-diff", help="Optional diff_entities.py output to detect changes")
    ap.add_argument("--previous-submittal")
    ap.add_argument("--current-submittal")
    a = ap.parse_args(argv)
    try:
        for p in (a.pairs, a.rules, a.out):
            if not path_ok(p):
                return fail(EXIT_INPUT, f"path longer than 250 characters: {p}")
        pairs_doc = read_json(a.pairs)
        pairs = pairs_doc.get("pairs") or []
        _, by_id = load_rules(a.rules)
        rule = rule_pkg010(by_id)
        method = (rule or {}).get("method") or ""
        changed = content_changed_set(a, pairs)

        checks = [check_pair(p, i, rule, method, changed) for i, p in enumerate(pairs)]
        observations = [c for c in checks if c.get("result") == "observation"]
        pkg = [c for c in checks if c.get("rule_id") == "PKG-010" and c.get("result") != "observation"]

        # Merge into existing rev_diff if present
        existing = {}
        out_path = Path(a.out)
        if out_path.is_file():
            try:
                existing = read_json(a.out)
            except ValueError:
                existing = {}

        result = {
            "rev_diff_version": existing.get("rev_diff_version") or "1.0",
            "previous_submittal": a.previous_submittal or existing.get("previous_submittal"),
            "current_submittal": a.current_submittal or existing.get("current_submittal"),
            "pairs": existing.get("pairs") or pairs,
            "unmatched_previous": existing.get("unmatched_previous") or pairs_doc.get("unmatched_previous"),
            "unmatched_current": existing.get("unmatched_current") or pairs_doc.get("unmatched_current"),
            "revision_checks": checks,
            "observations": observations,
            "entity_diffs": existing.get("entity_diffs") or [],
            "surface_diffs": existing.get("surface_diffs") or [],
            "raster_diffs": existing.get("raster_diffs") or [],
            "inventory_diff": existing.get("inventory_diff") or {},
            "counts": {
                **(existing.get("counts") or {}),
                "revision_checks": len(checks),
                "pkg010_fail": sum(1 for c in pkg if c.get("result") == "fail"),
                "pkg010_pass": sum(1 for c in pkg if c.get("result") == "pass"),
                "observations_dm3": len(observations),
                "pkg010_not_run": sum(1 for c in pkg if c.get("result") == "not_run"),
            },
            "meta": {
                **(existing.get("meta") or {}),
                "pkg010_present": rule is not None,
                "pkg010_state": (rule or {}).get("state"),
                "skill": "comparing-drawing-revisions",
                "skill_version": __version__,
            },
        }
        atomic_write_json(a.out, result)
        emit({"status": "ok", "out": str(a.out),
              "checks": len(checks),
              "pkg010_fail": result["counts"]["pkg010_fail"],
              "observations_dm3": result["counts"]["observations_dm3"]})
        return EXIT_OK
    except ValueError as exc:
        return fail(EXIT_INPUT, str(exc))
    except OSError as exc:
        return fail(EXIT_INPUT, str(exc))


if __name__ == "__main__":
    sys.exit(main())
