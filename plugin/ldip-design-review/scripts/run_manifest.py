#!/usr/bin/env python3
"""Run manifest and run log for /review-submittal (SPC-0002 Rev E §4.3, §7; SPC-0005 Rev C §5.0, §6).

Sub-commands:
  open   --root R --site S --sub ID --mode live|test|shadow --rulebook rulebook/rules_vN.json
         Creates runs/<run-id>/manifest.json (status running), runs/_active_run.json (read by the
         guard hook) and the first run.jsonl event. Refuses (exit 5) if a run is already active,
         the run-id exists, the rulebook is not 'approved' (SPC-0005 §4.12), or the guard hook's
         configured root (LDIP_REVIEW_ROOT / guard_config.json) is missing or differs from --root (P-S3-8).
         --source <folder> (0.3.0, SPC-0005 Rev F §5.0.5): the submittal is read in place from an
         external folder under a guard_config.json `submittal_source_roots` entry; for the run it is the
         logical folder submittals/<SITE>/<sub-id>/. inputs[] keep logical paths (+ mtime_ns); the
         manifest and the active-run marker carry source {logical, path}. Refused (exit 5) when no
         roots are configured, the folder (links resolved) is outside them, a link inside it resolves
         outside it, or submittals/<SITE>/<sub-id>/ also exists.
  Emergency end of a crashed run: close --status aborted (removes the active-run marker, P-S3-1).
  event  --root R --run RUN --agent A --event E [--instance N] [--detail JSON]
         Appends one event to runs/<run>/run.jsonl (SPC-0002 §7.2 event names only).
         --event rule_skipped --from-summary runs/<run>/returns/<step>_<i>.json logs one event per
         rules_skipped[] entry (rule_id and reason only), so no free text passes through the shell:
         rulebook reasons contain ';' which the guard denies on a command line (live trial 2026-10-01).
  close  --root R --run RUN --status completed|failed|aborted
         Closes the manifest and removes runs/_active_run.json. With an external source (Rev F
         §6.2.4, D39-2) the folder is compared with inputs[] (file set, bytes, mtime_ns; a file whose
         mtime changed is re-hashed): any difference closes the run `aborted` with
         source_check.changed, whatever status was asked.

Prints a one-line JSON summary; exit codes per SPC-0006 §2.3.3. No network, no shell calls.
"""
__version__ = "0.3.0"

import argparse
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ldip_common import (  # noqa: E402
    ACTIVE_RUN_FILE, EXIT_INPUT, EXIT_OK, EXIT_REFUSED, RUN_ID_RE, SITES, SUB_ID_RE, ScriptError,
    append_jsonl, atomic_write_json, check_path_len, configured_review_root, configured_source_roots,
    iso_utc, now_riyadh, parse_summary, path_under, read_json, sha256_file, to_posix_abs,
)

MODES = ("live", "test", "shadow")
EVENTS = ("start", "end", "retry", "fail", "format_check", "rule_skipped")  # SPC-0002 §7.2
STATUSES = ("completed", "failed", "aborted")
VERSION_RE = re.compile(r'^__version__\s*=\s*["\']([^"\']+)["\']', re.M)
FM_VERSION_RE = re.compile(r"^\s+version:\s*[\"']?([^\"'\n]+)", re.M)

PLUGIN_ROOT = Path(os.environ.get("CLAUDE_PLUGIN_ROOT") or Path(__file__).resolve().parent.parent)


def plugin_versions():
    pj = PLUGIN_ROOT / ".claude-plugin" / "plugin.json"
    meta = read_json(pj) if pj.is_file() else {}
    skills = {}
    for sk in sorted((PLUGIN_ROOT / "skills").glob("*/SKILL.md")):
        m = FM_VERSION_RE.search(sk.read_text(encoding="utf-8").split("\n---", 2)[0])
        skills[sk.parent.name] = m.group(1).strip() if m else None
    scripts = {}
    for py in sorted(list((PLUGIN_ROOT / "scripts").glob("*.py")) + list((PLUGIN_ROOT / "hooks").glob("*.py"))):
        m = VERSION_RE.search(py.read_text(encoding="utf-8"))
        scripts[f"{py.parent.name}/{py.name}"] = m.group(1) if m else None
    return meta.get("name"), meta.get("version"), skills, scripts


def hash_inputs(root, folder):
    items = []
    for p in sorted(folder.rglob("*")):
        if p.is_file():
            check_path_len(p)
            items.append({"path": p.relative_to(root).as_posix(), "bytes": p.stat().st_size,
                          "sha256": sha256_file(p)})
    return items


def list_source(base, logical):
    """(logical path, real Path) of every file under an external source, links not followed
    (os.walk). A link that resolves outside the source folder -> refused (D39-1)."""
    base_posix = to_posix_abs(base, "/")
    out = []
    for dirpath, dirnames, filenames in os.walk(base, followlinks=False):
        dirnames.sort()
        for name in list(dirnames) + filenames:
            q = Path(dirpath) / name
            if q.is_symlink() and not path_under(to_posix_abs(q, "/"), base_posix):
                raise ScriptError(EXIT_REFUSED, f"link inside the source resolves outside it: {q} (SPC-0005 Rev F §7.2)")
        for name in sorted(filenames):
            q = Path(dirpath) / name
            if not q.is_file():
                continue
            rel = q.relative_to(base).as_posix()
            logical_path = f"{logical}/{rel}"
            check_path_len(logical_path)
            out.append((logical_path, q))
    return sorted(out)


def hash_source(base, logical):
    items = []
    for lp, q in list_source(base, logical):
        st = q.stat()
        items.append({"path": lp, "bytes": st.st_size, "mtime_ns": st.st_mtime_ns, "sha256": sha256_file(q)})
    return items


def open_source(a, root, guard_root, sub_dir):
    roots = configured_source_roots(PLUGIN_ROOT / "hooks", guard_root)
    if not roots:
        raise ScriptError(EXIT_REFUSED, "--source given but no submittal_source_roots in guard_config.json "
                                        "(SPC-0005 Rev F §7.6)")
    src = Path(a.source).resolve()
    if not src.is_dir():
        raise ScriptError(EXIT_INPUT, f"source folder not found: {a.source}")
    src_posix = to_posix_abs(src, "/")
    if not any(path_under(src_posix, r) for r in roots):
        raise ScriptError(EXIT_REFUSED, f"source {src_posix} is not under a configured submittal source root "
                                        "(SPC-0005 Rev F §5.0.5b, D39-1)")
    if sub_dir.exists():
        raise ScriptError(EXIT_REFUSED, f"submittals/{a.site}/{a.sub}/ also exists in LDIP_Review; one logical "
                                        "folder, one physical folder (SPC-0005 Rev F §5.0.5b)")
    logical = f"submittals/{a.site}/{a.sub}"
    return {"logical": logical, "path": src_posix}, hash_source(src, logical)


def source_changes(m):
    """Logical paths added, deleted or changed in the external source since open (Rev F §6.2.4)."""
    src = m["source"]
    base = Path(src["path"])
    if not base.is_dir():
        return [f"{src['logical']}/ (source folder not found)"]
    old = {i["path"]: i for i in m.get("inputs", [])}
    now = dict(list_source(base, src["logical"]))
    changed = set(old) ^ set(now)
    for lp in set(old) & set(now):
        st, o = now[lp].stat(), old[lp]
        if st.st_size != o.get("bytes"):
            changed.add(lp)
        elif st.st_mtime_ns != o.get("mtime_ns") and sha256_file(now[lp]) != o.get("sha256"):
            changed.add(lp)
    return sorted(changed)


def cmd_open(a):
    root = Path(a.root).resolve()
    if a.site not in SITES:
        raise ScriptError(EXIT_INPUT, f"site must be one of {SITES}")
    if not SUB_ID_RE.match(a.sub):
        raise ScriptError(EXIT_INPUT, "sub-id must be the short Aconex form, no spaces (SPC-0002 §4.2)")
    if a.mode not in MODES:
        raise ScriptError(EXIT_INPUT, f"mode must be one of {MODES}")
    guard_root = configured_review_root(PLUGIN_ROOT / "hooks")
    if guard_root is None:
        raise ScriptError(EXIT_REFUSED, "guard root not configured (LDIP_REVIEW_ROOT or guard_config.json); "
                                        "the L2 guard would not protect this run (P-S3-8)")
    if guard_root != to_posix_abs(root, "/"):
        raise ScriptError(EXIT_REFUSED, f"guard root {guard_root} differs from --root {root} (P-S3-8)")
    sub_dir = root / "submittals" / a.site / a.sub
    if not a.source and not sub_dir.is_dir():
        raise ScriptError(EXIT_INPUT, f"submittal folder not found: {sub_dir}")
    active = root / ACTIVE_RUN_FILE
    if active.exists():
        raise ScriptError(EXIT_REFUSED, f"a run is already active: {read_json(active).get('run_id')}")
    rb_path = (root / a.rulebook).resolve()
    rb = read_json(rb_path)
    if rb.get("status") != "approved":
        raise ScriptError(EXIT_REFUSED, f"rulebook status is '{rb.get('status')}', not 'approved' (SPC-0005 §4.12)")

    stamp = a.now or now_riyadh().strftime("%Y%m%dT%H%M")
    run_id = f"{a.site}-{a.sub}-{stamp}"
    if not RUN_ID_RE.match(run_id):
        raise ScriptError(EXIT_INPUT, f"invalid run-id {run_id}")
    run_dir = root / "runs" / run_id
    if run_dir.exists():
        raise ScriptError(EXIT_REFUSED, f"run folder exists, nothing is overwritten (SPC-0002 §4.3): {run_id}")

    source, inputs = open_source(a, root, guard_root, sub_dir) if a.source else (None, None)
    name, version, skills, scripts = plugin_versions()
    manifest = {
        "run_id": run_id, "site": a.site, "sub_id": a.sub, "mode": a.mode,
        "plugin_name": name, "plugin_version": version,
        "skill_versions": skills, "script_versions": scripts,
        "rulebook_file": rb_path.relative_to(root).as_posix() if rb_path.is_relative_to(root) else str(rb_path),
        "rulebook_version": rb.get("rulebook_version"), "rulebook_sha256": sha256_file(rb_path),
        "inputs": inputs if source else hash_inputs(root, sub_dir),
        "started_at": iso_utc(), "ended_at": None, "status": "running",
        "manifest_script": f"run_manifest.py {__version__}",
    }
    marker = {"run_id": run_id, "site": a.site, "sub_id": a.sub, "mode": a.mode,
              "started_at": manifest["started_at"]}
    if source:
        manifest["source"] = marker["source"] = source
    atomic_write_json(run_dir / "manifest.json", manifest)
    atomic_write_json(active, marker)
    append_jsonl(run_dir / "run.jsonl", {"ts": iso_utc(), "agent": "orchestrator", "instance": "1",
                                         "event": "start", "detail": {"mode": a.mode}})
    res = {"status": "ok", "run_id": run_id, "inputs": len(manifest["inputs"]),
           "rulebook_version": manifest["rulebook_version"], "plugin_version": version}
    if source:
        res["source"] = source["path"]
    return res


def run_dir_of(root, run):
    if not RUN_ID_RE.match(run):
        raise ScriptError(EXIT_INPUT, f"invalid run-id {run}")
    d = Path(root).resolve() / "runs" / run
    if not (d / "manifest.json").is_file():
        raise ScriptError(EXIT_INPUT, f"no manifest for run {run}")
    return d


def cmd_event(a):
    d = run_dir_of(a.root, a.run)
    if a.event not in EVENTS:
        raise ScriptError(EXIT_INPUT, f"event must be one of {EVENTS}")
    if a.from_summary:
        return log_skipped(d, a)
    detail = {}
    if a.detail:
        try:
            detail = json.loads(a.detail)
        except json.JSONDecodeError as exc:
            raise ScriptError(EXIT_INPUT, f"--detail is not JSON: {exc}") from exc
    append_jsonl(d / "run.jsonl", {"ts": iso_utc(), "agent": a.agent, "instance": a.instance,
                                   "event": a.event, "detail": detail})
    return {"status": "ok", "run_id": a.run, "event": a.event, "agent": a.agent}


def log_skipped(d, a):
    if a.event != "rule_skipped" or a.detail:
        raise ScriptError(EXIT_INPUT, "--from-summary goes with --event rule_skipped and no --detail")
    src = Path(a.from_summary)
    src = (src if src.is_absolute() else Path(a.root) / src).resolve()
    if src.parent != d / "returns":
        raise ScriptError(EXIT_INPUT, f"--from-summary shall be a file in runs/{a.run}/returns/")
    try:
        summary, _ = parse_summary(src.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ScriptError(EXIT_INPUT, f"file not found: {src}") from exc
    except json.JSONDecodeError as exc:
        raise ScriptError(EXIT_INPUT, f"invalid JSON in {src}: {exc}") from exc
    entries = summary.get("rules_skipped") if isinstance(summary, dict) else None
    if not isinstance(entries, list):
        raise ScriptError(EXIT_INPUT, "return summary has no rules_skipped list")
    n = 0
    for e in entries:
        if not isinstance(e, dict) or not isinstance(e.get("rule_id"), str):
            raise ScriptError(EXIT_INPUT, f"rules_skipped entry without rule_id: {str(e)[:80]}")
        append_jsonl(d / "run.jsonl", {"ts": iso_utc(), "agent": a.agent, "instance": a.instance,
                                       "event": "rule_skipped",
                                       "detail": {"rule_id": e["rule_id"], "reason": str(e.get("reason", ""))[:300]}})
        n += 1
    return {"status": "ok", "run_id": a.run, "event": "rule_skipped", "agent": a.agent, "logged": n}


def cmd_close(a):
    d = run_dir_of(a.root, a.run)
    if a.status not in STATUSES:
        raise ScriptError(EXIT_INPUT, f"status must be one of {STATUSES}")
    m = read_json(d / "manifest.json")
    if m.get("status") != "running":
        raise ScriptError(EXIT_REFUSED, f"run already closed ({m.get('status')}); manifests are immutable after the run (SPC-0002 §9.3)")
    status, detail = a.status, {"status": a.status}
    if m.get("source"):
        changed = source_changes(m)
        m["source_check"] = {"result": "changed" if changed else "unchanged", "changed": changed,
                             "checked_at": iso_utc()}
        if changed:
            status = "aborted"
            detail = {"status": status, "requested": a.status, "source_changed": changed[:200],
                      "source_changed_count": len(changed)}
    m["status"], m["ended_at"] = status, iso_utc()
    atomic_write_json(d / "manifest.json", m)
    append_jsonl(d / "run.jsonl", {"ts": iso_utc(), "agent": "orchestrator", "instance": "1",
                                   "event": "end", "detail": detail})
    active = Path(a.root).resolve() / ACTIVE_RUN_FILE
    if active.exists() and read_json(active).get("run_id") == a.run:
        active.unlink()
    res = {"status": "ok", "run_id": a.run, "run_status": status}
    if m.get("source"):
        res["source_check"] = m["source_check"]["result"]
        res["source_changed_count"] = len(m["source_check"]["changed"])
    return res


def main(argv=None):
    ap = argparse.ArgumentParser(description="LDIP run manifest / run log")
    sp = ap.add_subparsers(dest="cmd", required=True)
    o = sp.add_parser("open")
    o.add_argument("--root", required=True)
    o.add_argument("--site", required=True)
    o.add_argument("--sub", required=True)
    o.add_argument("--mode", default="live")
    o.add_argument("--rulebook", required=True)
    o.add_argument("--source", help="external submittal folder (SPC-0005 Rev F §5.0.5)")
    o.add_argument("--now", help=argparse.SUPPRESS)  # fixed stamp for tests
    e = sp.add_parser("event")
    e.add_argument("--root", required=True)
    e.add_argument("--run", required=True)
    e.add_argument("--agent", required=True)
    e.add_argument("--event", required=True)
    e.add_argument("--instance", default="1")
    e.add_argument("--detail")
    e.add_argument("--from-summary")
    c = sp.add_parser("close")
    c.add_argument("--root", required=True)
    c.add_argument("--run", required=True)
    c.add_argument("--status", required=True)
    a = ap.parse_args(argv)
    try:
        res = {"open": cmd_open, "event": cmd_event, "close": cmd_close}[a.cmd](a)
        print(json.dumps(res, sort_keys=True))
        return EXIT_OK
    except ScriptError as err:
        print(json.dumps({"status": "error", "code": err.code, "detail": err.detail}))
        return err.code


if __name__ == "__main__":
    sys.exit(main())
