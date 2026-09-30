---
description: TEST ONLY - run the LDIP A1 plugin probe
allowed-tools: Read, Glob, Agent
argument-hint: (no arguments)
---

This is the LDIP Stage 3 test A1. Run these steps in order. Do not skip a step if an earlier one fails; record the failure and continue. Do not fix, retry with other tools, or work around anything: the point is to observe behaviour.

**Step 1 — hook in the main session.** Use the Read tool on the relative path `A1_DENY_SENTINEL.txt`. Record the exact tool result or error.

**Step 2 — plugin sub-agent with deny list.** Invoke the sub-agent `a1-worker` of this plugin. First try `subagent_type` = `ldip-a1-probe:a1-worker`; if that is rejected, try `a1-worker`. Record which string worked, and the agent's final JSON verbatim.

**Step 3 — plugin sub-agent with a one-tool allowlist.** Invoke `a1-restricted` the same way. Record the string that worked and the JSON verbatim.

**Step 4 — command allowed-tools.** The Write tool is NOT in this command's `allowed-tools`. Try once to use the Write tool to create `a1_allowed_tools_test.txt` in the working directory with the text `A1`. Record exactly what happened: tool unavailable / blocked with a message / permission prompt shown to the user / file written.

**Step 5 — hook log.** Read `/tmp/ldip-a1-probe/hook_log.jsonl`. If it does not exist, use Glob for `**/a1-probe/hook_log.jsonl` and read what is found. Record the path read. Count lines by (`event`, `agent_type`) and copy the `env` block of the first line.

**Step 6 — report.** Output one Markdown table with columns `Test | Observed | Evidence (verbatim)` and these rows:

| Test | Question |
|---|---|
| A1-01 | Did `/a1-run` appear and run after the plugin was installed? |
| A1-02 | Which `subagent_type` string worked for plugin agents? |
| A1-03 | Did PreToolUse fire in the main session, and what agent_type did it show? |
| A1-04 | Did PreToolUse fire inside `a1-worker`, and what exact agent_type string did it show? |
| A1-05 | Did the hook's deny decision stop the Read (in main and in each sub-agent)? |
| A1-06 | Did SubagentStart additionalContext reach `a1-worker`? |
| A1-07 | Did SubagentStart / SubagentStop appear in the log? |
| A1-08 | Did the `skills` field preload `a1-preload-probe` into `a1-worker`? |
| A1-09 | Were Agent and Skill absent from `a1-worker` (disallowedTools)? |
| A1-10 | Was Bash absent from `a1-restricted` (tools allowlist)? |
| A1-11 | What did Write do under `allowed-tools` (Step 4)? |
| A1-12 | Where do hooks and sub-agent Bash run (host, platform, python, cwd)? |

End with the full hook-log summary from Step 5.
