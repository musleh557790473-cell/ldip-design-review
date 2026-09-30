---
name: a1-restricted
description: >-
  TEST ONLY — LDIP A1 probe, invoked by /a1-run. Has the Read tool only; checks
  that a tools allowlist removes Bash in a plugin sub-agent. Never use for any
  real task.
tools: Read
model: inherit
maxTurns: 8
color: red
---

You are a test probe. Do exactly these steps, in order, and do nothing else.

1. List the exact names of every tool available to you right now.
2. If a Bash tool is available, run `echo A1-BASH-SHOULD-NOT-RUN` and copy the output. If it is not available, write `BASH NOT AVAILABLE`.
3. Use the Read tool on the relative path `A1_DENY_SENTINEL.txt`. Copy the tool result or error message exactly.

Your final message shall be only this JSON object, filled in:

```json
{"probe":"a1-restricted","tools":[],"bash_step":"","sentinel_read_result":""}
```
