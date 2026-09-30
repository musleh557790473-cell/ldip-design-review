#!/usr/bin/env python3
"""LDIP A1 probe hook (TEST ONLY).

Reads the hook input JSON from stdin, appends one line to the probe log and,
for two cases only, returns a decision:
  * PreToolUse whose tool input mentions A1_DENY_SENTINEL -> deny, with the
    agent_type echoed in the reason (so the agent can report it verbatim).
  * SubagentStart -> additionalContext carrying the agent_type.
Any internal error is logged and the hook exits 0 (never blocks by accident).
No network, no shell calls.
"""
__version__ = "0.1.0"

import json
import os
import platform
import socket
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

SENTINEL = "A1_DENY_SENTINEL"


def log_dirs():
    dirs = []
    for var in ("CLAUDE_PLUGIN_DATA", "CLAUDE_PROJECT_DIR"):
        v = os.environ.get(var)
        if v:
            dirs.append(Path(v) / "a1-probe")
    dirs.append(Path(tempfile.gettempdir()) / "ldip-a1-probe")
    return dirs


def write_log(record):
    written = []
    line = json.dumps(record, ensure_ascii=False, sort_keys=True)
    for d in log_dirs():
        try:
            d.mkdir(parents=True, exist_ok=True)
            with open(d / "hook_log.jsonl", "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
            written.append(str(d / "hook_log.jsonl"))
        except OSError:
            pass
    return written


def main():
    raw = sys.stdin.read()
    try:
        data = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        data = {"_unparsed": raw[:500]}

    event = data.get("hook_event_name", "?")
    agent_type = data.get("agent_type")
    agent_id = data.get("agent_id")
    tool_name = data.get("tool_name")
    tool_input = data.get("tool_input", {})
    shown_type = agent_type if agent_type else "<none>"

    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "probe_version": __version__,
        "event": event,
        "agent_type": agent_type,
        "agent_id": agent_id,
        "tool_name": tool_name,
        "tool_input_keys": sorted(tool_input.keys()) if isinstance(tool_input, dict) else None,
        "input_keys": sorted(data.keys()),
        "env": {
            "host": socket.gethostname(),
            "platform": platform.platform(),
            "python": platform.python_version(),
            "cwd": os.getcwd(),
            "CLAUDE_PLUGIN_ROOT": os.environ.get("CLAUDE_PLUGIN_ROOT"),
            "CLAUDE_PLUGIN_DATA": os.environ.get("CLAUDE_PLUGIN_DATA"),
            "CLAUDE_PROJECT_DIR": os.environ.get("CLAUDE_PROJECT_DIR"),
        },
    }

    out = None
    if event == "PreToolUse" and SENTINEL in json.dumps(tool_input):
        record["decision"] = "deny"
        out = {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": (
                    f"A1-DENY agent_type={shown_type} agent_id={agent_id} "
                    f"tool={tool_name} host={record['env']['host']}"
                ),
            }
        }
    elif event == "SubagentStart":
        out = {
            "hookSpecificOutput": {
                "hookEventName": "SubagentStart",
                "additionalContext": f"A1-SUBAGENTSTART-CONTEXT agent_type={shown_type}",
            }
        }

    record["log_files"] = write_log(record)
    if out is not None:
        print(json.dumps(out))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # probe must never block by accident
        try:
            write_log({"event": "probe_error", "error": repr(exc)})
        except Exception:
            pass
        sys.exit(0)
