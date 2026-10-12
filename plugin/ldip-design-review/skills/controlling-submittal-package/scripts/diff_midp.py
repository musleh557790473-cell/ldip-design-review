#!/usr/bin/env python3
"""Compare two versions of the Parsons MIDP (outputs of read_midp.py) — owner decision D2, 11 Oct 2026.

Usage: python diff_midp.py --old <midp_old.json> --new <midp_new.json> --out <midp_diff.json>
Reports, per MIDP file (matched by the document number in the file name, else by position):
  identity       Author, Date Last Updated, Document Reference, file hash, old -> new
  stages         data-exchange stage names per sheet, old -> new
  rows           counts; added and removed numbers grouped by (section code, discipline, type) and by
                 MIDP section heading
  renumbered     a removed and an added number with the same title, section heading and sheet: the
                 fields that changed (e.g. discipline DIG -> TEL); matched one-to-one in row order
  changed        same number in both: title, section heading, exchange formats; data exchanges are
                 compared by stage NAME (the stage columns may move between versions)
The comparison is deterministic and states facts only; it raises no finding.
Exit: 0 written · 2 input error. No network, no shell calls.
"""
__version__ = "0.1.0"

import argparse
import collections
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pkg_common import EXIT_INPUT, EXIT_OK, atomic_write_json, emit, fail, path_ok, read_json  # noqa: E402

MAX_LIST = 200


def pattern_key(number):
    f = number.split("-")
    return "-".join(f[4:7]) if len(f) >= 7 else number


def stage_map(row):
    return {e["stage"]: {k: e.get(k, "") for k in ("owner", "planned", "actual", "revision")} for e in row["exchanges"]}


def pair_files(old, new):
    pairs = []
    for nf in new["files"]:
        of = next((f for f in old["files"] if f["number_from_name"] and f["number_from_name"] == nf["number_from_name"]),
                  None)
        if of is None and nf["index"] < len(old["files"]):
            of = old["files"][nf["index"]]
        pairs.append((of, nf))
    return pairs


def diff_file(old, new, of, nf):
    meta = {}
    for k in sorted(set(of["meta"]) | set(nf["meta"])):
        a, b = of["meta"].get(k, {}).get("value"), nf["meta"].get(k, {}).get("value")
        if a != b:
            meta[k] = {"old": a, "new": b}
    stages = {}
    for s in nf["sheets"]:
        o = next((x for x in of["sheets"] if x["name"] == s["name"]), None)
        a = [e["stage"] for e in o["exchanges"]] if o else None
        b = [e["stage"] for e in s["exchanges"]]
        if a != b:
            stages[s["name"]] = {"old": a, "new": b}
    sheets = {"old": [s["name"] for s in of["sheets"]], "new": [s["name"] for s in nf["sheets"]]}
    do = {r["number"]: r for r in old["rows"] if r["file"] == of["index"]}
    dn = {r["number"]: r for r in new["rows"] if r["file"] == nf["index"]}
    removed = sorted(set(do) - set(dn), key=lambda n: (do[n]["sheet"], do[n]["row"]))
    added = sorted(set(dn) - set(do), key=lambda n: (dn[n]["sheet"], dn[n]["row"]))
    pool = collections.defaultdict(list)
    for n in added:
        r = dn[n]
        pool[(r["sheet"], r["section"], r["title"])].append(n)
    renum = []
    for n in removed:
        r = do[n]
        cand = pool.get((r["sheet"], r["section"], r["title"]))
        if cand:
            m = cand.pop(0)
            fa, fb = n.split("-"), m.split("-")
            ch = [{"field": i + 1, "old": fa[i] if i < len(fa) else None, "new": fb[i] if i < len(fb) else None}
                  for i in range(max(len(fa), len(fb))) if (fa[i] if i < len(fa) else None) != (fb[i] if i < len(fb) else None)]
            renum.append({"old": n, "new": m, "sheet": r["sheet"], "title": r["title"], "changes": ch,
                          "old_cell": r["cell"], "new_cell": dn[m]["cell"]})
    ren_old, ren_new = {x["old"] for x in renum}, {x["new"] for x in renum}
    rem_only = [n for n in removed if n not in ren_old]
    add_only = [n for n in added if n not in ren_new]
    ren_pat = collections.Counter(
        "; ".join(f"field {c['field']} {c['old']}->{c['new']}" for c in x["changes"]) for x in renum)
    changed = {"title": [], "section": [], "formats": [], "exchanges": collections.Counter()}
    for n in sorted(set(do) & set(dn)):
        a, b = do[n], dn[n]
        if a["title"] != b["title"]:
            changed["title"].append({"number": n, "old": a["title"], "new": b["title"], "cell": b["cell"], "sheet": b["sheet"]})
        if a["section"] != b["section"]:
            changed["section"].append({"number": n, "old": a["section"], "new": b["section"]})
        if a["formats"] != b["formats"]:
            changed["formats"].append({"number": n, "old": a["formats"], "new": b["formats"], "sheet": b["sheet"], "cell": b["cell"]})
        sa, sb = stage_map(a), stage_map(b)
        for st in sorted(set(sa) | set(sb)):
            va, vb = sa.get(st, {}), sb.get(st, {})
            for k in ("owner", "planned", "actual", "revision"):
                x, y = va.get(k, ""), vb.get(k, "")
                if x != y:
                    changed["exchanges"][f"{st} | {k} | {'filled' if not x else 'cleared' if not y else 'changed'}"] += 1
    fmt_pat = collections.Counter(f"{'+'.join(c['old']) or '-'} -> {'+'.join(c['new']) or '-'}" for c in changed["formats"])
    return {
        "old_file": {"name": of["name"], "sha256": of["sha256"]}, "new_file": {"name": nf["name"], "sha256": nf["sha256"]},
        "identity_changes": meta, "sheets": sheets, "stage_changes": stages,
        "rows": {"old": len(do), "new": len(dn), "removed": len(removed), "added": len(added),
                 "renumbered": len(renum), "removed_only": len(rem_only), "added_only": len(add_only)},
        "renumbered_patterns": dict(ren_pat.most_common()),
        "renumbered": renum[:MAX_LIST],
        "removed_only_by_code": dict(collections.Counter(pattern_key(n) for n in rem_only).most_common()),
        "added_only_by_code": dict(collections.Counter(pattern_key(n) for n in add_only).most_common()),
        "removed_only_by_section": dict(collections.Counter(f"{do[n]['sheet']} | {do[n]['section']}" for n in rem_only).most_common()),
        "added_only_by_section": dict(collections.Counter(f"{dn[n]['sheet']} | {dn[n]['section']}" for n in add_only).most_common()),
        "removed_only": [{"number": n, "title": do[n]["title"], "sheet": do[n]["sheet"], "cell": do[n]["cell"]} for n in rem_only][:MAX_LIST],
        "added_only": [{"number": n, "title": dn[n]["title"], "sheet": dn[n]["sheet"], "cell": dn[n]["cell"]} for n in add_only][:MAX_LIST],
        "title_changes": changed["title"][:MAX_LIST], "title_changes_count": len(changed["title"]),
        "section_moves_count": len(changed["section"]),
        "format_changes": dict(fmt_pat), "format_changes_list": changed["formats"][:MAX_LIST],
        "exchange_changes": dict(sorted(changed["exchanges"].items())),
    }


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--old", required=True)
    ap.add_argument("--new", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    if not path_ok(a.out):
        return fail(EXIT_INPUT, "output path longer than 250 characters")
    try:
        old, new = read_json(a.old), read_json(a.new)
    except (OSError, ValueError) as exc:
        return fail(EXIT_INPUT, f"cannot read input: {exc}")
    files = [diff_file(old, new, of, nf) for of, nf in pair_files(old, new) if of is not None]
    doc = {"tool": {"name": "diff_midp.py", "version": __version__}, "files": files,
           "totals": {k: sum(f["rows"][k] for f in files) for k in ("old", "new", "removed", "added", "renumbered",
                                                                    "removed_only", "added_only")}}
    atomic_write_json(a.out, doc)
    emit({"status": "ok", "out": str(a.out), **doc["totals"]})
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
