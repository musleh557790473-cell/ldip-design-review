# Rule states, cap and lint — reference for applying-ldip-rulebook

Source: AICC-BIM-SPC-0004 Rev E §3–§4 (schema 1.1); SPC-0003 Rev E; SPC-0005 Rev C §8; SPC-0006 Rev B §4.5.

## Contents
1. Rule states and cap
2. Validation (schema + lint)
3. get_rules.py output fields
4. check_cap.py result

## 1. Rule states and cap

| State | Runs? | Highest status (cap) | Condition |
|---|---|---|---|
| active | yes | Confirmed | every value AGREED or from an approved / internally agreed source; code lists approved |
| advisory | yes | Probable | at least one value PROPOSED, or a source for approval / unknown |
| inactive | no — logged as `rule_skipped` | — | a blocking parameter is GAP |

Status ranking used for the cap: Confirmed = Missing information (3) > Probable (2) > Not auto-verifiable (1).
A finding with no `rule_id` may not be Confirmed.

Advisory parameter (Rev E §4.7): in a running rule, a parameter with `enforcement: advisory` has cap
Probable. When a finding names it in `reference.parameter`, FC5 applies that cap; an unknown parameter
name fails FC5. Inactive rules whose skill is not built carry `target_state` (Rev E §4.8).

## 2. Validation

`load_rules.py` runs the JSON Schema (`schemas/rules.schema.json` in the plugin) and the lint:

- L1 — a blocking GAP parameter forces `inactive`; a non-active rule needs a `state_reason`.
- L2 — an `active` rule uses no PROPOSED value and only approved / internally agreed sources and AGREED code lists.
- L3 — `rule_id` unique and matching its `group`.
- L4 — every referenced `source_id` and code list exists.

Schema 1.1 lint (SPC-0004 Rev E §4.9):

- L5 — an advisory parameter is AGREED.
- L6 — an inactive "skill not built" rule carries `target_state`.
- L7 — every non-GAP parameter of a ROD / EWK / COR rule cites a `clause`.
- L8 — `tolerance_ref` names a tolerance; none PROPOSED or GAP in an active rule.
- L9 — a `keyed_by` value has a key for every value of the rule's `applicability`.
- L10 — every ROD / EWK / COR rule declares `required_inputs`.
- L11 — a presence rule cites a tier-1 or tier-2 source; tier 2 only → advisory.
- L12 — `stage_map` covers every phase used; each DD entry has its MIDP milestone.

Schema 1.0 files (rules_v0, rules_v1) still validate. Any error → exit 5 with the list of errors. Note: SPC-0004 Rev C §4.1 / §4.5 text reads "[AGREED]" where
the lint (and the Stage 2 lint script) test PROPOSED values — an editorial artefact of the Stage 2 gate
tag change; the lint follows the original meaning. [GAP: correct the wording at SPC-0004 Rev D]

## 3. get_rules.py output

`rulebook_version`, `agent`, and:

- `rules[]` — `rule_id`, `title`, `skill`, `state`, `cap`, `statement`, `method`, `inputs`, `parameters`
  (value, unit, tag, source, clause, blocking, and with schema 1.1 `enforcement`, `keyed_by`, `cap`,
  `tolerance`), `skip_subchecks_for` (non-blocking GAP parameters), `advisory_parameters`,
  `code_lists` (name → values), `severity_default`, `issue_type`, `default_status` (by evidence
  source), `exclusions`, `owner_role`; when present `check_type`, `applicability`, `required_inputs`,
  `required_from_phase`, `decision_ref`.
- `rules_skipped[]` — `rule_id`, `reason` (`inactive: <state_reason>`), `target_state` when set.
- `stage_map` — MIDP stage name → ER phase and DD milestone (Rev E §5.14, §5.14A), when the rulebook has one.

## 4. check_cap.py result

`result` pass | fail and `errors[]` with `path` (JSON pointer into the findings file) and `message`:
unknown rule, inactive rule, Confirmed without a rule, status above the cap (rule or advisory parameter),
unknown parameter, rulebook version mismatch.
