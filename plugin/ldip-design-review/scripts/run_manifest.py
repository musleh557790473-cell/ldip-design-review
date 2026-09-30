#!/usr/bin/env python3
"""Run manifest and run log for /review-submittal (SPC-0002 Rev E §4.3, §7; SPC-0005 Rev C §5.0, §6).

Sub-commands:
  open   --root R --site S --sub ID --mode live|test|shadow --rulebook rulebook/rules_vN.json
         Creates runs/<run-id>/manifest.json (status running), runs/_active_run.json (read by the
         guard hook) and the first run.jsonl event. Refuses (exit 5) if a run is already active,
         the run-id exists, the rulebook is not 'approved' (SPC-0005 §4.12), or the guard hook's
         configured root (LDIP_REVIEW_ROOT / guard_config.json) is missing or differs from --root (P-S3-8).
  Emergency end of a crashed run: close --status aborted (removes the active-run marker, P-S3-1).
  event  --root R --run RUN --agent A --event E [--instance N] [--detail JSON]
         Appends one event to runs/<run>/run.jsonl (SPC-0002 §7.2 event names only).
  close  --root R --run RUN --status completed|failed|aborted
         Closes the manifest and removes runs/_active_run.json.

Prints a one-line JSON summary; exit codes per SPC-0006 §2.3.3. No network, no shell calls.
"""
__version__ = "0.2.0"

import argparse
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ldip_common import (  # noqa: E402
    ACTIVE_RUN_FILE, EXIT_INPUT, EXIT_OK, EXIT_REFUSED, RUN_ID_RE, SITES, SUB_ID_RE, ScriptError,
    append_jsonl, atomic_write_json, check_path_len, configured_review_root, iso_utc, now_riyadh,
    read_json, sha256_file, to_posix_abs,
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
    if not sub_dir.is_dir():
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

    name, version, skills, scripts = plugin_versions()
    manifest = {
        "run_id": run_id, "site": a.site, "sub_id": a.sub, "mode": a.mode,
        "plugin_name": name, "plugin_version": version,
        "skill_versions": skills, "script_versions": scripts,
        "rulebook_file": rb_path.relative_to(root).as_posix() if rb_path.is_relative_to(root) else str(rb_path),
        "rulebook_version": rb.get("rulebook_version"), "rulebook_sha256": sha256_file(rb_path),
        "inputs": hash_inputs(root, sub_dir),
        "started_at": iso_utc(), "ended_at": None, "status": "running",
        "manifest_script": f"run_manifest.py {__version__}",
    }
    atomic_write_json(run_dir / "manifest.json", manifest)
    atomic_write_json(active, {"run_id": run_id, "site": a.site, "sub_id": a.sub, "mode": a.mode,
                               "started_at": manifest["started_at"]})
    append_jsonl(run_dir / "run.jsonl", {"ts": iso_utc(), "agent": "orchestrator", "instance": "1",
                                         "event": "start", "detail": {"mode": a.mode}})
    return {"status": "ok", "run_id": run_id, "inputs": len(manifest["inputs"]),
            "rulebook_version": manifest["rulebook_version"], "plugin_version": version}


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
    detail = {}
    if a.detail:
        try:
            detail = json.loads(a.detail)
        except json.JSONDecodeError as exc:
            raise ScriptError(EXIT_INPUT, f"--detail is not JSON: {exc}") from exc
    append_jsonl(d / "run.jsonl", {"ts": iso_utc(), "agent": a.agent, "instance": a.instance,
                                   "event": a.event, "detail": detail})
    return {"status": "ok", "run_id": a.run, "event": a.event, "agent": a.agent}


def cmd_close(a):
    d = run_dir_of(a.root, a.run)
    if a.status not in STATUSES:
        raise ScriptError(EXIT_INPUT, f"status must be one of {STATUSES}")
    m = read_json(d / "manifest.json")
    if m.get("status") != "running":
        raise ScriptError(EXIT_REFUSED, f"run already closed ({m.get('status')}); manifests are immutable after the run (SPC-0002 §9.3)")
    m["status"], m["ended_at"] = a.status, iso_utc()
    atomic_write_json(d / "manifest.json", m)
    append_jsonl(d / "run.jsonl", {"ts": iso_utc(), "agent": "orchestrator", "instance": "1",
                                   "event": "end", "detail": {"status": a.status}})
    active = Path(a.root).resolve() / ACTIVE_RUN_FILE
    if active.exists() and read_json(active).get("run_id") == a.run:
        active.unlink()
    return {"status": "ok", "run_id": a.run, "run_status": a.status}


def main(argv=None):
    ap = argparse.ArgumentParser(description="LDIP run manifest / run log")
    sp = ap.add_subparsers(dest="cmd", required=True)
    o = sp.add_parser("open")
    o.add_argument("--root", required=True)
    o.add_argument("--site", required=True)
    o.add_argument("--sub", required=True)
    o.add_argument("--mode", default="live")
    o.add_argument("--rulebook", required=True)
    o.add_argument("--now", help=argparse.SUPPRESS)  # fixed stamp for tests
    e = sp.add_parser("event")
    e.add_argument("--root", required=True)
    e.add_argument("--run", required=True)
    e.add_argument("--agent", required=True)
    e.add_argument("--event", required=True)
    e.add_argument("--instance", default="1")
    e.add_argument("--detail")
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
