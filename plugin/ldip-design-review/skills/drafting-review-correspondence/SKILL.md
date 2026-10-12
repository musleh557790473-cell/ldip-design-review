---
name: drafting-review-correspondence
description: >-
  LDIP design review (AICC JV): drafts Comment Review Sheet (CRS) rows to
  Parsons, Requests for Information to RCRC and cover letters from fixed AICC
  templates, using only registered findings whose route is CRS or RFI, and
  marks every draft as not for issue until approved at the Human Review Gate.
  Use when an LDIP review run has registered findings to put into
  correspondence.
metadata:
  version: "0.1.0"
  spec: "AICC-BIM-SPC-0006 Rev B §4.8 (scripts per P-S22-5, owner 12 Oct 2026); AICC-BIM-SPC-0003 Rev D §6; AICC-BIM-SPC-0005 Rev C §5.9"
---

# Drafting review correspondence

This skill turns verified findings into DRAFT correspondence content: CRS rows to Parsons and RFIs to
RCRC. A script composes every row from the finding's own fields; you never write or reword comment
text. Skill 24 renders the content into the CRS and RFI forms.

Packages: Python 3.11+, `jsonschema`.

## Hard rules

1. Draft only from **verified** findings (skill 18 `verified.json`, or later `registered.json`). A view
   with candidates only is refused (exit 5): run skill 18 first. Candidates in a mixed view are listed
   in `not_drafted[]` (P-S22-4).
2. Never write, reword, shorten, translate or merge a CRS or RFI text. `draft_crs.py` composes it from
   the statement, the reference and up to two evidence extracts (P-S22-5); `validate_drafts.py` rebuilds
   it and fails on any difference.
3. CRS rows only for the CRS route; RFIs only for the RFI route. Probable findings go to the engineer
   queue, Not auto-verifiable ones to the manual-check list; NONE, RISK_EOT_REGISTER, UNVERIFIED and
   INCONSISTENT are listed, never drafted (SPC-0005 §5.9, §5.9.1).
4. Reviewer initials, comment status codes, review codes, signatures, dates and RFI or CRS numbers are
   left for the reviewers. Nothing is issued.
5. Correspondence is in English. Header values (project, section, organisation, contractor) are the
   site defaults in `references/drafting_rules.md`; change one only when the owner asks, with
   `--header key=value`.
6. **Arabic summary — only when the owner asks.** Write it as a separate file
   `<OUT>/drafts_summary_ar.md` from `drafts.json`: first line the DRAFT line, then one short line per
   draft (number, rule, location, gist). Keep document numbers, rule ids, codes and figures exactly as
   written, and English technical terms where no agreed Arabic term exists. It is a reading aid, never
   part of a form, and never sent to Parsons or RCRC.
7. Cover letters: no template is approved yet [GAP — K7]. Do not write one; say so.

## Procedure (plan → validate → execute → verify)

`<S24>` is `${CLAUDE_PLUGIN_ROOT}/skills/generating-review-reports/scripts`,
`<SK>` is `${CLAUDE_PLUGIN_ROOT}/skills/drafting-review-correspondence/scripts`, `<OUT>` the output folder.

1. **Plan — findings view** (skill 24's shared loader; skip if the view already exists):
   `python "<S24>/load_findings.py" --findings "<verified.json>" --rulebook "<RB>" --out "<OUT>/findings_view.json"`
2. **Execute — drafts:**
   `python "<SK>/draft_crs.py" --view "<OUT>/findings_view.json" --out "<OUT>/drafts.json"`
   Optional `--review-no R1` for a resubmittal, `--header key=value` on the owner's instruction.
   Exit 5 means no verified finding: tell the owner to run skill 18 first.
3. **Verify:**
   `python "<SK>/validate_drafts.py" --drafts "<OUT>/drafts.json" --view "<OUT>/findings_view.json"`
   Only `"status": "pass"` allows rendering. On a fail, report the problems; never edit `drafts.json`.
4. **Render** with skill 24 `render_templates.py` (see its SKILL.md, step 4).
5. **Report back:** number of CRS rows and RFIs, the engineer-queue and manual-check counts, and every
   `not_drafted` finding with its reason.

## Outputs and exit codes

`<OUT>/drafts.json` (schema `templates/drafts.schema.json`). Exit codes (SPC-0006 §2.3.3): 0 done ·
2 input error · 3 missing package · 4 schema or rebuild check failed · 5 refused (no verified finding).

How each field is composed and the site header table: `references/drafting_rules.md`.
