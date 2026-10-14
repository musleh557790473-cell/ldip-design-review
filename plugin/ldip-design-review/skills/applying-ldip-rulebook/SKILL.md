---
name: applying-ldip-rulebook
description: >-
  LDIP design review (AICC JV): loads the approved LDIP Rulebook file,
  validates it, returns the PKG, GIS, road (ROD), earthworks (EWK) or
  utility-corridor (COR) rules for an agent with their state (active,
  advisory, inactive), parameters, tolerances, code lists and highest allowed
  verification status, and checks a proposed or verified status against that
  cap. Use whenever an LDIP agent needs a rule value, tolerance, code list or
  status cap.
metadata:
  version: "0.2.0"
  spec: "AICC-BIM-SPC-0006 Rev B §4.5; AICC-BIM-SPC-0004 Rev E §3-§4"
---

# Applying the LDIP Rulebook

The rulebook (`rulebook/rules_v<N>.json` under LDIP_Review) holds every rule value used in an LDIP
review. This skill is the only way a finder or the Verifier reads it. The Orchestrator passes the
rulebook path in the prompt; do not search for another file.

## Hard rules

1. Use only a rulebook with `status: approved`. The scripts refuse any other file (exit 5). Stop and
   return `failed` with the script's message; do not look for another rulebook (SPC-0005 §4.12).
2. Never edit a rulebook, a rule state or a value, and never substitute a value of your own. A missing
   value is a gap: record it in `gaps[]` of the return summary (SPC-0004 §1.4, §4.4).
3. Never run an `inactive` rule. List each one in `rules_skipped[]` with the reason the script gives.
4. The status of a finding shall not exceed its rule's cap: `active` → Confirmed; `advisory` → Probable.
   *Missing information* counts as Confirmed, so an advisory rule cannot produce it.
5. A parameter tagged GAP with `blocking: false` does not stop the rule; skip only the sub-check that
   needs it (listed in `skip_subchecks_for`) and say so in `gaps[]` (SPC-0004 §4.2).
6. Read code lists and parameters from the rulebook every run (for example the PKG-006 site map).
   Never hard-code them.
7. A parameter with `enforcement: advisory` runs, but a finding from its sub-check shall not exceed
   Probable (its `cap` in the `get_rules.py` output). Name the parameter in `reference.parameter` so
   the cap check can see it (SPC-0004 Rev E §4.7; SPC-0003 Rev E).
8. Compare a measured value with a limit using the parameter's `tolerance` when one is given; never
   invent a tolerance.

## Procedure (plan → validate → execute → verify)

Run the scripts; do not read their source. `<RB>` is the rulebook path from the prompt and `<SK>` is
`${CLAUDE_PLUGIN_ROOT}/skills/applying-ldip-rulebook/scripts`.

1. **Validate** — `python "<SK>/load_rules.py" "<RB>"`
   Expect `"status": "ok"` and the state counts. Exit 5 → stop (hard rule 1).
2. **Plan** — get your rules and write them to your evidence folder:
   `python "<SK>/get_rules.py" --rulebook "<RB>" --agent GIS --out "findings/<run>/evidence/gis/rules_GIS.json"`
   (Intake package step: `--agent INT`, folder `evidence/intake/`; roads and earthworks `--agent RDE`;
   coordination `--agent COO`.) The file lists `rules` (state, cap, parameters with their own cap and
   tolerance, code lists, default status by evidence source, exclusions, applicability, required inputs),
   `rules_skipped` (with `target_state`) and the `stage_map`.
3. **Execute** — run each listed rule with its own skill. For a code list or one parameter:
   `python "<SK>/lookup_code.py" --rulebook "<RB>" --list section --value AN`
   `python "<SK>/lookup_code.py" --rulebook "<RB>" --param PKG-006.site_map`
   Set `proposed_status` = the lower of the rule's `default_status` for the evidence source and its cap.
4. **Verify** — before returning, check your findings file against the caps:
   `python "<SK>/check_cap.py" --findings "findings/<run>/candidates/gis.json" --rulebook "<RB>"`
   `"result": "fail"` → correct the listed statuses (lower them; never change the rule) and re-run.

The Verifier uses step 4 on `verified.json` before returning. The format checker runs the same check
(FC5) after every step, so a status above the cap is a format failure.

## Outputs and exit codes

All scripts print one JSON object. Exit codes (SPC-0006 §2.3.3): 0 done · 2 input error ·
3 missing dependency (`jsonschema`) · 5 refused (rulebook invalid or not approved; not retried).

Details of the state model, the lint checks and the output fields: `references/rule_states.md`.
