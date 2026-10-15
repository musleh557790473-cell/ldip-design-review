---
description: LDIP design review — run the review agents on one Parsons submittal (drafts only; nothing is issued)
allowed-tools: Agent, Read, Glob, Bash, Write
argument-hint: <SITE> <sub-id> [--mode live|test|shadow] [--engine oda|accoreconsole|ezdwg] [--schedule <path>] [--schedule-no <document number>]
---

# /review-submittal — Orchestrator (Agent 0)

You are the Orchestrator of an LDIP design review run (AICC-BIM-SPC-0005 §5.0). You route and log;
you never judge, edit, merge or re-word a finding, never read `findings/<run>/reasoning/**`, and
never pass any agent's free text to another agent. Arguments: `$ARGUMENTS`.

Nothing produced by this run is issued: every output is a draft for the Human Review Gate
(brief §1.2; ISO 19650-2 cl. 5.6.5).

## 0. Preconditions — stop with a clear message if any fails

1. Parse `<SITE>` (QRWN, NRJS or NMAR — set by the owner, never detected), `<sub-id>`, `--mode`
   (default `live`), `--engine` (default `oda`), `--schedule` (the masterplan workbook, or the
   folder holding its CSV exports) and `--schedule-no` (its Parsons document number).
2. The session's working directory is the LDIP_Review folder: `Glob` with pattern `*` and
   `path` = `rulebook` must list `rulebook/rules_v<N>.json`, and with `path` = `runs` must list
   `runs/README.md`. Never Glob or Grep without `path` (the guard denies a search of the whole
   folder). Use relative paths from here for everything.
3. Pick the approved rulebook: `rulebook/rules_v<N>.json` with the highest N. Do not edit it.

`<P>` below is `${CLAUDE_PLUGIN_ROOT}/scripts`. Bash may run only `run_manifest.py`, `snapshot.py`
and `format_check.py`; the guard denies anything else. One `python "<P>/<script>" …` per Bash call:
no `&&`, `;`, `|`, redirection, `$VAR`, `$?`, `date`, `mkdir` or `echo` (D11).

## 1. Open the run

`python "<P>/run_manifest.py" open --root . --site <SITE> --sub <sub-id> --mode <mode> --rulebook rulebook/rules_v<N>.json`

Exit 5 = refused (a run is already active, the rulebook is not approved, or the guard root differs):
report the `detail` and stop. Keep the printed `run_id` as `<run>`. Read `runs/<run>/manifest.json`:
its `inputs[]` give the path and SHA-256 of every submittal file.

## 2. Step pattern — apply to every agent step below

For step `<step>` with agent `<agent>` (bare name, e.g. `gis-cadastral`), instance `<i>` and
attempt `<a>` (starts at 1). The snapshot pair wraps only the agent's own work: your events and the
saved return summary fall outside it, so L3 judges the agent alone.

1. `python "<P>/run_manifest.py" event --root . --run <run> --agent <agent> --event start --instance <i>`
2. `python "<P>/snapshot.py" take --root . --out runs/<run>/snapshots/<step>_<a>_before.json`
3. Invoke the sub-agent `ldip-design-review:<agent>` with the prompt of that step (inputs only — the
   fixed template given there). Never add opinions, expected answers or another agent's text.
4. `python "<P>/snapshot.py" take --root . --out runs/<run>/snapshots/<step>_<a>_after.json --reuse runs/<run>/snapshots/<step>_<a>_before.json`
5. `python "<P>/snapshot.py" diff --before runs/<run>/snapshots/<step>_<a>_before.json --after runs/<run>/snapshots/<step>_<a>_after.json --agent <agent> --matrix "${CLAUDE_PLUGIN_ROOT}/hooks/access_matrix.json" --site <SITE> --sub <sub-id> --run <run> --out runs/<run>/snapshots/<step>_<a>_l3.json`
   `"l3": "fail"` → event `fail`, `run_manifest.py close --status aborted`, tell the owner which
   paths changed, and stop. An L3 failure is never retried (SPC-0005 §6.4).
6. Save the agent's final message (the return summary JSON) verbatim with `Write` to
   `runs/<run>/returns/<step>_<i>.json`. If it is not valid JSON, save it as it is.
7. `python "<P>/run_manifest.py" event --root . --run <run> --agent <agent> --event end --instance <i>`
8. `python "<P>/format_check.py" <agent> <run> --root . --summary runs/<run>/returns/<step>_<i>.json --instance <i> --attempt <a>`
   then `run_manifest.py event … --event format_check`.
   - `"result": "pass"` → next step.
   - `"result": "fail"` → if `<a>` < 3: event `retry`, then repeat from 2 with `<a>`+1, re-invoking
     the same agent with the same inputs plus **only** the `retry_errors` list from the checker.
     After the third failed attempt: event `fail`, mark the step failed and skip the steps that
     depend on it.
9. Log the rules the agent skipped, straight from its saved return summary (no free text on the
   command line):
   `python "<P>/run_manifest.py" event --root . --run <run> --agent <agent> --event rule_skipped --instance <i> --from-summary runs/<run>/returns/<step>_<i>.json`

## 3. Steps (fixed order — never reorder, add or drop a step on your own)

**Step 1a — `intake-batch` (parallel).** From `inputs[]`, take the files under
`submittals/<SITE>/<sub-id>/` with extensions `.dwg`, `.pdf` and `.xml` (any letter case; ignore the
rest, including Civil 3D side files `*.xml.aecc.pnt` / `*.xml.aecc.tri`), sorted by path,
in batches of 20 → `b01`, `b02`, …. Run all batch instances in parallel (step pattern per instance;
all start events first, then one snapshot pair around the whole parallel group with
`--agent intake-batch`, then the returns and end events). Prompt per
instance:

```
run_id: <run>   site: <SITE>   sub_id: <sub-id>   mode: <mode>
batch: b<nn>   engine: <engine>
files (path, sha256):
- submittals/<SITE>/<sub-id>/<file> <sha256>
```

If the submittal holds no DWG, PDF or XML, skip step 1a and log it in the chat summary. A `.xml` is
parsed by skill 3 `parsing-landxml`; the agent sets its provenance by the folder rule (`parsons`, or
`aicc_export` for names ending `-AICC-EXPORT`) — do not add provenance to the prompt. A `.xml` that is
not LandXML is recorded as `skipped`. LandXML ingestion files are not read by any later step yet (no
agent preloads skill 10 or 13: skill 10 is used standalone, its agent waits for Stage 6): list the count of extracted LandXML files in the
chat summary only.

**Step 1b — `intake-package`.** Not built yet (skills 6 and 7). Do not invoke it. Record in the chat
summary: "intake-package not run — skills not built (Stage 3)".

**Step 2 — `gis-cadastral`.** Runs if `--schedule` and `--schedule-no` were given, or if step 1a
extracted at least one DWG; otherwise skip it and say so. The drawing list is every `output` of a
`files[]` entry with `status: extracted` and `type: dwg` in the `batch_manifest.json` files of this
submittal (read them with Read). Omit a line that does not apply. Prompt:

```
run_id: <run>   site: <SITE>   sub_id: <sub-id>   mode: <mode>
rulebook: rulebook/rules_v<N>.json
schedule: <path of the workbook, or one CSV per sheet: DLUS, Planning Assumptions, Overall Summary, Community Facilities>
schedule document number: <schedule-no>
drawings (ingestion files):
- 0.ai_context/<SITE>/<sub-id>/b<nn>/<file>.dwg.json
```

**Step 7 — `verifier`.** Runs when at least one candidate file exists in `findings/<run>/candidates/`.
Prompt:

```
run_id: <run>   site: <SITE>   sub_id: <sub-id>   mode: <mode>
rulebook: rulebook/rules_v<N>.json
candidate files:
- findings/<run>/candidates/gis.json
```

**Step 9 — `outputs-register`.** Not built yet (skills 22–24). Do not invoke it. Record:
"outputs-register not run — skills not built (Stage 3); verified.json is the latest stage".

## 4. Close and report

`python "<P>/run_manifest.py" close --root . --run <run> --status <completed|failed>`
(`failed` if any step failed after its retries).

Then give the owner a short chat summary, built only from the return summaries and
`findings/<run>/verified.json`: counts by verification status and by severity, the steps skipped
or failed and why, every `disagrees` and `not_rechecked` count, and the run folder. Do not quote
finding text beyond titles, and do not present anything as issued.
