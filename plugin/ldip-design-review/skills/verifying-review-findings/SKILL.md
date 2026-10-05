---
name: verifying-review-findings
description: >-
  LDIP design review (AICC JV): independently re-checks candidate findings on
  Parsons deliverables and sets each one's verification status (Confirmed,
  Probable, Not auto-verifiable, Missing information) from its evidence
  source, the LDIP Rulebook cap and the re-check result, without adding,
  merging or editing findings. Use when candidate findings of an LDIP review
  run need verification before registration.
metadata:
  version: "0.2.0"
  spec: "AICC-BIM-SPC-0006 Rev B §4.7; AICC-BIM-SPC-0005 Rev C §5.7; AICC-BIM-SPC-0003 Rev D §4.6"
---

# Verifying review findings

You are the Checker in the Maker–Checker loop. For every candidate finding you re-check the
evidence by a second, independent path, set the `verification` block, and write `verified.json`.
The scripts do the checking and the status arithmetic: run them in order and read their results.
Do not read their source.

Packages: Python 3.11+, `jsonschema` (xlsx is read from the package XML with the standard library).

## Hard rules

1. Never read `findings/<run>/reasoning/**` or `golden_set/answer_key/**` (the guard denies it).
2. Never add, delete, merge, split or edit a finding. Only `stage` and `verification` change; the
   format checker compares every other field (FC2) and counts every candidate once (FC7).
3. Status is computed by `set_status.py`: the lowest of the evidence default, the rule cap and the
   re-check. Never raise it by hand. `disagrees` is Probable and is not retried (brief §4.3c).
4. A finder's own `script_output` is not a re-check. Where no second path exists the result is
   `not_rechecked` and the status is at most Probable (SPC-0005 §5.7.1; V1).
5. Use only an approved rulebook; the scripts refuse any other (exit 5). Stop and return `failed`.
6. Nothing you produce is issued. Severity is not yours to change; status is not severity.

## Procedure (plan → re-check → set status → verify)

Run from the LDIP_Review folder. `<SK>` is `${CLAUDE_PLUGIN_ROOT}/skills/verifying-review-findings/scripts`,
`<RB>` the rulebook path from the prompt, `<F>` = `findings/<run>`.

1. **Plan** — list the candidate files `<F>/candidates/*.json` named in the prompt.
2. **Re-check** —
   `python "<SK>/recheck.py" --candidates "<F>/candidates/gis.json" "<F>/candidates/intake.json" --rulebook "<RB>" --out "<F>/evidence/verifier/recheck.json"`
   With `--rulebook`, the GIS-012 / 013 / 014 formulas are recomputed from the re-read cells.
   Read the counts. `disagrees` lines name the cells that differ; keep them for your summary.
3. **Set status and write** —
   `python "<SK>/set_status.py" --candidates <same files> --recheck "<F>/evidence/verifier/recheck.json" --rulebook "<RB>" --out "<F>/verified.json"`
4. **Verify** —
   `python "<SK>/validate_verified.py" --verified "<F>/verified.json" --candidates <same files> --rulebook "<RB>"`
   `"status": "fail"` → report the errors as `failed`; do not hand-edit `verified.json`.
5. **Return** the summary: counts by status, every `disagrees` with its cells, every
   `not_rechecked` with its reason, in `inconsistencies[]` or `gaps[]` as they apply.

## Outputs and exit codes

`<F>/evidence/verifier/recheck.json` and `<F>/verified.json`. Exit codes (SPC-0006 §2.3.3):
0 done · 2 input error · 3 missing package · 4 schema or check failure · 5 rulebook refused.

The status model, the V1–V5 rules, what each evidence source gets and what each re-check path
covers: `references/default_status.md`.
