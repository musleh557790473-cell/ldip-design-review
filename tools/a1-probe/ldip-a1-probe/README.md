# ldip-a1-probe (TEST ONLY)

Minimal plugin for LDIP Stage 3 test A1 (AICC-BIM-SPC-0005 §11 A1; brief Rev F §11 item 8).
It checks, in Cowork: plugin sub-agents, the `agent_type` string in hook input, plugin hooks
(PreToolUse, SubagentStart, SubagentStop), command `allowed-tools`, `tools` / `disallowedTools`,
the `skills` preload field, and enable/disable.

Components: 1 command (`/a1-run`), 2 agents (`a1-worker`, `a1-restricted`), 1 skill
(`a1-preload-probe`, marker only), hooks (`hooks/hooks.json` → `hooks/probe_hook.py`).

The hook writes `hook_log.jsonl` to `$CLAUDE_PLUGIN_DATA/a1-probe/`, `$CLAUDE_PROJECT_DIR/a1-probe/`
(if set) and `<tmp>/ldip-a1-probe/`. It reads no project data, makes no network call, and only
denies tool calls that name `A1_DENY_SENTINEL`.

Test procedure and results form: `A1-TEST-PROTOCOL.md` (repo `tools/a1-probe/`).
Disable and uninstall after the test.
