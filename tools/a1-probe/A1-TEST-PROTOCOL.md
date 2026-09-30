# A1 Test Protocol — Cowork plugin behaviour (LDIP Stage 3, session 1)

| Item | Value |
|---|---|
| Test | A1 (SPC-0005 §11; SPC-0002 F4; brief Rev F §2.3, §11 item 8) |
| Probe | `ldip-a1-probe` v0.1.0 (TEST ONLY, no project data) |
| Machine | aynlap1201, Claude desktop app, Cowork |
| Tester | Owner (SBM) |
| Date | [fill] |

## 1. What A1 decides

| # | Question | Spec that depends on it |
|---|---|---|
| Q1 | Do plugin sub-agents load in Cowork, and what `subagent_type` string calls them? | SPC-0005 §3.1, §5 |
| Q2 | Do plugin hooks (`hooks/hooks.json`) fire, in the main session and inside sub-agents? | SPC-0005 §7 (L2) |
| Q3 | What exact `agent_type` string does the hook receive for a plugin agent, and what for the main session? | SPC-0005 §3.2, §7.2 |
| Q4 | Does a `permissionDecision: deny` from the hook stop the call? | SPC-0005 §7.2–7.3 |
| Q5 | Do `tools` / `disallowedTools` remove tools from a plugin sub-agent? | SPC-0005 §4.1–4.2 |
| Q6 | Does the `skills` field preload a skill into a plugin sub-agent? | SPC-0005 §5, brief §3.5 |
| Q7 | Does a command's `allowed-tools` restrict, pre-approve, or do nothing? | SPC-0005 §5.0 (Orchestrator tool control) |
| Q8 | Does disabling the plugin remove its command, agents and hooks; does enabling restore them? | Brief §2.3 |
| Q9 | Where do hooks and sub-agent Bash run (cloud container, local VM)? | SPC-0005 §4.4c, A2; SPC-0006 K1 |

## 2. Procedure

| Step | Action | Record |
|---|---|---|
| 1 | Install `ldip-a1-probe.plugin` from the chat card (accept). | Installed Y/N; any warning text |
| 2 | Start a **new** Cowork task (plugins load at session start). Type `/a1-run`. | Command visible Y/N (A1-01) |
| 3 | Let `/a1-run` finish. If a permission prompt appears at Step 4 of the command, **deny it** and note that it appeared. | Copy the full table the command prints |
| 4 | In the same task, ask: "Read A1_DENY_SENTINEL.txt". | Deny text shown Y/N |
| 5 | Disable the plugin (Customize / Plugins → toggle off). In the **same** task, ask again: "Read A1_DENY_SENTINEL.txt", then type `/a1-run`. | Deny still shown? Command still listed? (hot disable) |
| 6 | Start a **new** task with the plugin disabled. Repeat Step 5. | Same questions (disable at session start) |
| 7 | Re-enable the plugin, new task, run `/a1-run` once more. | Works again Y/N |
| 8 | Uninstall the probe. | Done |

Send me the table from Step 3 and your notes for Steps 4–7. If the hook log was written to a folder I can reach, I will read it directly.

## 3. Pass criteria and consequence

| Result | Consequence for the build |
|---|---|
| A1-02 string known; A1-04 shows it | `access_matrix.json` keys use that exact string (SPC-0005 §3.2 closes) |
| A1-03 shows `<none>` for the main session | Guard treats "no agent_type" as Orchestrator (SPC-0005 §7.2 holds) |
| A1-05 deny works in sub-agents | L2 is effective; L3 still built (§7.5) |
| A1-05 deny does not work, or hooks do not fire | L2 = [GAP]; rely on L1 + L3; answer key kept outside connected folders (§7.1A) |
| A1-09 / A1-10 tools removed | §4.1–4.2 hold |
| A1-08 preload works | "Blocked" skill model holds (brief §3.5) |
| A1-11 Write blocked | `allowed-tools` is a restriction → Orchestrator control by frontmatter |
| A1-11 Write prompted or allowed | `allowed-tools` only pre-approves → Orchestrator control relies on the guard hook (§5.0) |
| Step 5/6 disable removes hooks | Brief §2.3 "enable only when needed" is workable |

## 4. Results form

| Test | Observed | Evidence (verbatim) |
|---|---|---|
| A1-01 command visible | | |
| A1-02 `subagent_type` string | | |
| A1-03 main-session agent_type | | |
| A1-04 sub-agent agent_type | | |
| A1-05 deny effective (main / worker / restricted) | | |
| A1-06 SubagentStart context | | |
| A1-07 SubagentStart/Stop logged | | |
| A1-08 skill preload | | |
| A1-09 Agent, Skill removed | | |
| A1-10 Bash removed by allowlist | | |
| A1-11 `allowed-tools` effect | | |
| A1-12 where hooks / Bash run | | |
| A1-13 hot disable (Step 5) | | |
| A1-14 disable at session start (Step 6) | | |
| A1-15 re-enable (Step 7) | | |
