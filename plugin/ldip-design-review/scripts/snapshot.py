#!/usr/bin/env python3
"""File snapshot and write-set check — enforcement layer L3 (SPC-0002 Rev E §8; SPC-0005 Rev C §6.2).

Sub-commands:
  take  --root R --out FILE [--reuse PREV_SNAPSHOT]
        Paths + size + mtime + SHA-256 of every file under LDIP_Review/, excluding
        golden_set/answer_key/**. With --reuse, a file whose size and mtime are unchanged
        keeps its previous hash (large submittals are not re-hashed every step).
  diff  --before B --after A --agent NAME[,NAME...] --matrix access_matrix.json
        --site S --sub ID --run RUN [--out FILE]
        Lists added / modified / deleted files and flags every change outside the union of
        the agents' write sets (parallel steps pass several agents). Result "l3": pass|fail.
        An L3 failure is never retried (SPC-0005 §6.4); the Orchestrator aborts and escalates.

Prints a one-line JSON summary; exit 0 when the check ran (pass or fail is in the result),
2 input error. No network, no shell calls.
"""
__version__ = "0.1.0"

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ldip_common import (  # noqa: E402
    EXIT_INPUT, EXIT_OK, ScriptError, any_match, atomic_write_json, expand, iso_utc, read_json, sha256_file,
)

EXCLUDE = ["golden_set/answer_key/**"]  # never read, not even for hashing (SPC-0005 §6.2)


def take(root, reuse=None):
    root = Path(root).resolve()
    if not root.is_dir():
        raise ScriptError(EXIT_INPUT, f"root not found: {root}")
    prev = reuse.get("files", {}) if reuse else {}
    files, reused = {}, 0
    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = Path(dirpath).relative_to(root).as_posix()
        rel_dir = "" if rel_dir == "." else rel_dir
        dirnames[:] = sorted(d for d in dirnames
                             if not any_match(f"{rel_dir}/{d}/x".lstrip("/"), EXCLUDE))
        for fn in sorted(filenames):
            rel = f"{rel_dir}/{fn}".lstrip("/")
            if any_match(rel, EXCLUDE):
                continue
            st = os.stat(os.path.join(dirpath, fn))
            old = prev.get(rel)
            if old and old["bytes"] == st.st_size and old["mtime_ns"] == st.st_mtime_ns:
                digest = old["sha256"]
                reused += 1
            else:
                digest = sha256_file(os.path.join(dirpath, fn))
            files[rel] = {"bytes": st.st_size, "mtime_ns": st.st_mtime_ns, "sha256": digest}
    return {"snapshot_version": __version__, "root": root.as_posix(), "taken_at": iso_utc(),
            "excluded": EXCLUDE, "files": files, "reused_hashes": reused}


def write_set(matrix, agents, values):
    pats = []
    for ag in agents:
        if ag not in matrix["agents"]:
            raise ScriptError(EXIT_INPUT, f"agent not in access matrix: {ag}")
        pats += expand(matrix["agents"][ag].get("write", []), values)
    return pats


def diff(before, after, agents, matrix, values):
    b, a = before["files"], after["files"]
    added = sorted(set(a) - set(b))
    deleted = sorted(set(b) - set(a))
    modified = sorted(k for k in set(a) & set(b) if a[k]["sha256"] != b[k]["sha256"])
    pats = write_set(matrix, agents, values)

    def allowed(rel):
        cands = [rel] + ([rel[:-4]] if rel.endswith(".tmp") else [])
        return any(any_match(c, pats) for c in cands)

    violations = [{"path": p, "change": kind}
                  for kind, lst in (("added", added), ("modified", modified), ("deleted", deleted))
                  for p in lst if not allowed(p)]
    return {
        "check": "L3 write-set", "script_version": __version__, "agents": agents,
        "run_id": values.get("{run}"), "write_set": pats,
        "changes": {"added": added, "modified": modified, "deleted": deleted},
        "violations": violations, "l3": "fail" if violations else "pass",
        "checked_at": iso_utc(),
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description="LDIP snapshot / L3 check")
    sp = ap.add_subparsers(dest="cmd", required=True)
    t = sp.add_parser("take")
    t.add_argument("--root", required=True)
    t.add_argument("--out", required=True)
    t.add_argument("--reuse")
    d = sp.add_parser("diff")
    for k in ("--before", "--after", "--agent", "--matrix", "--site", "--sub", "--run"):
        d.add_argument(k, required=True)
    d.add_argument("--out")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "take":
            snap = take(a.root, read_json(a.reuse) if a.reuse else None)
            atomic_write_json(a.out, snap)
            res = {"status": "ok", "files": len(snap["files"]), "reused_hashes": snap["reused_hashes"], "out": a.out}
        else:
            vals = {"{site}": a.site, "{sub}": a.sub, "{run}": a.run}
            r = diff(read_json(a.before), read_json(a.after), [x.strip() for x in a.agent.split(",")],
                     read_json(a.matrix), vals)
            if a.out:
                atomic_write_json(a.out, r)
            res = {"status": "ok", "l3": r["l3"], "violations": len(r["violations"]),
                   "added": len(r["changes"]["added"]), "modified": len(r["changes"]["modified"]),
                   "deleted": len(r["changes"]["deleted"])}
        print(json.dumps(res, sort_keys=True))
        return EXIT_OK
    except ScriptError as err:
        print(json.dumps({"status": "error", "code": err.code, "detail": err.detail}))
        return err.code


if __name__ == "__main__":
    sys.exit(main())
