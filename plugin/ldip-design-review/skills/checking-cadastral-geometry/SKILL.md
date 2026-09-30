---
name: checking-cadastral-geometry
description: >-
  LDIP design review (AICC JV): checks Parsons masterplan schedules (DLUS,
  Overall Summary, Planning Assumptions, Community Facilities) and cadastral
  drawings against the LDIP Rulebook GIS rules: Plot ID uniqueness, land-use
  roll-ups, GFA and population arithmetic, EPSG:32638 units and site envelope,
  plot polygon validity and DLUS-to-drawing matching. Use for any LDIP GIS,
  DLUS, plot or land-use check.
metadata:
  version: "0.1.0"
  spec: "AICC-BIM-SPC-0006 Rev B §4.6; AICC-BIM-SPC-0004 Rev C §5, §7"
---

# Checking cadastral geometry

This skill runs the GIS rules of the LDIP Rulebook on a Parsons masterplan schedule and writes
candidate findings. The scripts do the checks. You run them in order, read their one-line results and
write the return summary. Do not re-do a check by hand, and do not read the scripts' source.

Current coverage: the schedule rules GIS-001, GIS-002, GIS-003 and GIS-012 to GIS-015. The drawing
rules (GIS-008, GIS-009 and the DLUS-to-DWG matching rules) are not built yet. If the rules file lists
them, report each one in `rules_skipped[]` with the reason "script not built yet (skill 9 phase b)".

Packages: Python 3.11+, `openpyxl`, `jsonschema`.

## Hard rules

1. Get the rules only through `applying-ldip-rulebook` (step 1). Every tolerance and fixed value
   comes from that rules file. Never type a value yourself.
2. Run GIS-002 first. If `read_dlus.py` reports `header_not_found` or `missing_column`, the other
   scripts return `not_run` for every dependent rule. Report that; do not work around it.
3. A rule the scripts return as `not_run` produces no finding. Copy its reason into
   `rules_skipped[]` (or into `gaps[]` when the reason names a [GAP]).
4. One finding per rule and pattern, with the count and up to ten samples (SPC-0004 §5.7).
   `build_candidates.py` does this; never split a pattern into one finding per plot.
5. Do not change a statement, status, severity or location that `build_candidates.py` wrote. If a
   candidate looks wrong, say so in `reasoning/gis.md` and in the return summary.
6. Plot IDs listed by GIS-001 (`excluded_plot_ids`) are excluded from any later plot-matching rule
   (SPC-0004 §5.6).

## Procedure (plan → validate → execute → verify)

Run each command from the LDIP_Review folder, with paths relative to it. `<SK>` is
`${CLAUDE_PLUGIN_ROOT}/skills/checking-cadastral-geometry/scripts`, `<RB>` is the rulebook path from
the prompt, `<EV>` is `findings/<run>/evidence/gis` and `<WB>` is the schedule workbook. For a site
that has only CSV exports, pass each sheet's CSV file instead of the workbook (`DLUS.csv`,
`Planning Assumptions.csv`, `Overall Summary.csv`, `Community Facilities.csv`).

1. **Plan** — rules for this agent (skill `applying-ldip-rulebook`):
   `python "${CLAUDE_PLUGIN_ROOT}/skills/applying-ldip-rulebook/scripts/get_rules.py" --rulebook "<RB>" --agent GIS --out "<EV>/rules_GIS.json"`
2. **Validate the input (GIS-002)** — `python "<SK>/read_dlus.py" --file "<WB>" --sheet DLUS --out "<EV>/dlus.json"`
   Expect `"status": "ok"`. `"result": "fail"` is a GIS-002 finding, not a script failure.
3. **Execute**
   - GIS-001: `python "<SK>/check_ids.py" --dlus "<EV>/dlus.json" --out "<EV>/gis001.json"`
   - GIS-003, GIS-012 to GIS-015:
     `python "<SK>/check_schedule.py" --dlus "<EV>/dlus.json" --rules "<EV>/rules_GIS.json" --pa-file "<WB>" --summary-file "<WB>" --cf-file "<WB>" --out "<EV>/schedule.json"`
     Omit an argument when that sheet is not in the submittal; the rules that need it return `not_run`.
4. **Build and verify the candidates**
   `python "<SK>/build_candidates.py" --results "<EV>/dlus.json" "<EV>/gis001.json" "<EV>/schedule.json" --rules "<EV>/rules_GIS.json" --run <run-id> --site <SITE> --sub <sub-id> --document-no <schedule document number> --out "findings/<run>/candidates/gis.json"`
   The script validates every candidate against `finding.schema.json` and writes nothing if one
   fails (exit 4). Then check the caps:
   `python "${CLAUDE_PLUGIN_ROOT}/skills/applying-ldip-rulebook/scripts/check_cap.py" --findings "findings/<run>/candidates/gis.json" --rulebook "<RB>"`
5. **Report** — write `findings/<run>/reasoning/gis.md` (what ran, counts per rule, anything that
   looked wrong) and return the summary. Put every `not_run` reason and every [GAP] the scripts name
   (for example the controlled vocabulary of GIS-003, or the GIS-014 mapping of non-villa land uses)
   in `rules_skipped[]` or `gaps[]`.

The document number is the schedule's Parsons number. When only CSV exports were received and no
number is known, stop and return `failed` with the gap "schedule document number"; do not invent one.

## Outputs and exit codes

Every script prints one JSON line and writes one JSON file. Exit codes (SPC-0006 §2.3.3): 0 done ·
2 input error (missing file or sheet, path over 250 characters) · 3 missing package · 4 candidates
failed the finding schema. Codes 2–4 are retried by the Orchestrator; report the script's `detail`.

What each rule checks, the pattern names, how a labelled reconciliation line is accepted, and the
known limits: `references/schedule_rules.md`.
