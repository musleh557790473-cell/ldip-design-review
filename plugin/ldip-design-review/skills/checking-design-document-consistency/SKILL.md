---
name: checking-design-document-consistency
description: >-
  LDIP design review (AICC JV): compares roads and earthworks quantities between
  BOQ, design / concept report, drawing tables and model quantities (skills 10 /
  13), and reports each disagreement with both source values. Use when an LDIP
  intake checks consistency across documents of one roads / earthworks submittal.
metadata:
  version: "0.1.0"
  spec: "SPC-0006 Rev N §4.16"
---

# Checking design-document consistency

This skill compares quantities and stated values **between documents of one discipline
(roads and earthworks)** in one submittal and reports each disagreement with the values
from both sources. It does **not** decide which source is right. The scripts do the work.
Run them in order, read their one-line JSON results and report. Do not re-do a check by
hand, do not read the scripts' source, and never edit a result file.

Out of scope: rates / amounts (skills 19 / 21); drawing revisions (skill 7); other
disciplines (skill 14); missing deliverables (skill 6); specifications vs BOQ (DN-7).

**Report only (DN-4):** `consistency.json` findings carry `issue_type` `inconsistency`
(or Observation severity) with **no `rule_id`** until a consistency rule group lands in
rules_v9. Do not raise them as rule-backed CRS findings.

Packages: Python 3.11+, `openpyxl`, `jsonschema`.

## Hard rules

1. Scope is **roads + earthworks only** (DN-1). Do not extend to other disciplines.
2. Read the BOQ with `read_boq.py` (values **and** formulas). Compare the BOQ **base
   before its factor**; record the factor as stated without judging it (DN-6).
3. Read the design / concept report with `read_report.py` on skill-2 text (heading
   locator + `references/claims.md`). No specification reader (DN-7).
4. C4 and C6 consume skill 13 / 10 outputs — **never recompute** cut / fill, pavement
   depth or linework lengths (DN-3).
5. Tolerances live in `references/params.json` (DN-5). Do not invent others.
6. C6 is **Information** at 30 %; findings only from 60 % (DN-5).
7. Write outputs only under the context / evidence folders. Submittal folders are read-only.

## Procedure (plan → read → check → verify)

`<SK>` is `${CLAUDE_PLUGIN_ROOT}/skills/checking-design-document-consistency/scripts`;
`<CTX>` = `0.ai_context/<SITE>/<sub-id>`; `<EV>` = `findings/<run>/evidence/intake`.

1. **Plan — stage:** take the owner stage name (e.g. `30% Concept Design`). Never guess.
2. **Read — BOQ:**
   `python "<SK>/read_boq.py" --xlsx "<BOQ.xlsx>" --out "<EV>/boq.json"`
3. **Read — report** (skill-2 PDF text JSON):
   `python "<SK>/read_report.py" --text "<CTX>/<report>.pdf.json" --out "<EV>/report_claims.json"`
4. **Check — C1–C6:**
   `python "<SK>/check_consistency.py" --boq "<EV>/boq.json" --report "<EV>/report_claims.json" --profile "<EV>/earthworks_profile.json" --drawing-table "<EV>/cut_fill_table.json" --road-register "<EV>/road_register.json" --stage "<stage>" --site <SITE> --sub <sub-id> --out "<CTX>/consistency.json"`
   Omit `--profile` / `--drawing-table` / `--road-register` when absent.
5. **Verify** — confirm `consistency.json` validates against
   `templates/consistency.schema.json`. Report comparison and finding counts. Do **not**
   turn report-only findings into rule-backed CRS rows (DN-4).

## Outputs and exit codes

| File | Writer |
|------|--------|
| `boq.json` | `read_boq.py` |
| `report_claims.json` | `read_report.py` |
| `0.ai_context/<SITE>/<sub>/consistency.json` | `check_consistency.py` |

Exit codes (SPC-0006 §2.3.3): 0 done · 2 input error · 3 missing package · 4 schema failure.
Report the script's `detail` on any non-zero exit.

Methods, claim patterns and tolerances: `references/`.
