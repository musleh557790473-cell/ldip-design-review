# Rule states, cap and lint — reference for applying-ldip-rulebook

Source: AICC-BIM-SPC-0004 Rev C §3–§4; SPC-0005 Rev C §8; SPC-0006 Rev B §4.5.

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

## 2. Validation

`load_rules.py` runs the JSON Schema (`schemas/rules.schema.json` in the plugin) and the lint:

- L1 — a blocking GAP parameter forces `inactive`; a non-active rule needs a `state_reason`.
- L2 — an `active` rule uses no PROPOSED value and only approved / internally agreed sources and AGREED code lists.
- L3 — `rule_id` unique and matching its `group`.
- L4 — every referenced `source_id` and code list exists.

Any error → exit 5 with the list of errors. Note: SPC-0004 Rev C §4.1 / §4.5 text reads "[AGREED]" where
the lint (and the Stage 2 lint script) test PROPOSED values — an editorial artefact of the Stage 2 gate
tag change; the lint follows the original meaning. [GAP: correct the wording at SPC-0004 Rev D]

## 3. get_rules.py output

`rulebook_version`, `agent`, and:

- `rules[]` — `rule_id`, `title`, `skill`, `state`, `cap`, `statement`, `method`, `inputs`, `parameters`
  (value, unit, tag, source, clause, blocking), `skip_subchecks_for` (non-blocking GAP parameters),
  `code_lists` (name → values), `severity_default`, `issue_type`, `default_status` (by evidence
  source), `exclusions`, `owner_role`.
- `rules_skipped[]` — `rule_id`, `reason` (`inactive: <state_reason>`).

## 4. check_cap.py result

`result` pass | fail and `errors[]` with `path` (JSON pointer into the findings file) and `message`:
unknown rule, inactive rule, Confirmed without a rule, status above the cap, rulebook version mismatch.
