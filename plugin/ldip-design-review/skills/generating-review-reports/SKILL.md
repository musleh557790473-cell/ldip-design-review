---
name: generating-review-reports
description: >-
  LDIP design review (AICC JV): produces the review run report and executive
  summary from registered findings and the run log, with counts by status and
  severity, findings by rule, skipped rules and versions, and renders draft
  CRS and RFI content into the AICC templates. Use at the end of an LDIP
  review run after the register has been updated.
metadata:
  version: "0.1.0"
  spec: "AICC-BIM-SPC-0006 Rev B §4.10, §4.8.3; AICC-BIM-SPC-0003 Rev D §6; AICC-BIM-SPC-0005 Rev C §5.9; owner decisions P-S22-1…10 (12 Oct 2026)"
---

# Generating review reports

This skill turns findings files into a readable DRAFT report and renders skill 22 drafts into the CRS
and RFI forms. The scripts do all counting and writing. Run them in order and read their one-line JSON
results. Do not count, summarise or retype findings yourself, do not read the scripts' source and never
edit an output file.

Packages: Python 3.11+, `jsonschema`, `openpyxl`, `python-docx`.

## Hard rules

1. The report is a view. It never changes a status, severity, route or statement, and it adds no
   finding. Every number comes from `build_report.py` and is checked by `check_report.py`.
2. A candidate (stage `candidate`) is unverified. It is reported in its own row and on the
   "Unverified" list, never as Confirmed, and it gets no route (P-S22-3).
3. Routes of verified findings come from the SPC-0003 §6 table and are **provisional** in standalone
   mode. No register number is assigned; that is skill 23.
4. Every page and sheet carries "DRAFT — not for issue until approved at the Human Review Gate" and the
   plugin version. Nothing is issued.
5. The CRS and RFI forms are the owner's files and stay outside Git. Pass them by path; never copy them
   into the plugin or the repository. Without a form, a neutral layout with the same labels is used.
6. `check_report.py` failing means the report is not used. Report the problems; do not fix the files by
   hand.

## Two modes (P-S22-1)

**Standalone** (built now) — the owner gives findings files from any single skill: skill 6 or 9
candidates, or skill 18 `verified.json`, and one output folder `<OUT>` outside the submittal and
LDIP_Review inputs.

**Review run** (later, with skill 23 and the outputs-register agent) — the input is
`outputs/<run>/registered.json`; outputs go to `outputs/<run>/reports/` and `outputs/<run>/drafts/`.

## Procedure (plan → validate → execute → verify)

`<SK>` is `${CLAUDE_PLUGIN_ROOT}/skills/generating-review-reports/scripts`.

1. **Validate the input — findings view:**
   `python "<SK>/load_findings.py" --findings "<file 1>" "<file 2>" --rulebook "<RB>" --out "<OUT>/findings_view.json"`
   `--rulebook` is optional; with it, legacy numbers listed in the PKG rules are routed NONE. Exit 4
   lists the findings that failed `finding.schema.json`; nothing is written. Report them.
2. **Execute — report:**
   `python "<SK>/build_report.py" --view "<OUT>/findings_view.json" --results "<result file>" --out-md "<OUT>/report.md" --out-xlsx "<OUT>/report.xlsx"`
   `--results` takes the skills' result files (for example skill 6 `pkg_results.json`; skill 9 `gis001.json`,
   `schedule.json`, `gis008.json`, `gis009.json`, `match.json`; skill 8 `rules_*.json` for inactive rules) to list the rules run and not run; in a review run add `--run-log runs/<run>/run.jsonl`.
3. **Verify — counts:**
   `python "<SK>/check_report.py" --view "<OUT>/findings_view.json" --md "<OUT>/report.md" --xlsx "<OUT>/report.xlsx"`
   Only `"status": "pass"` allows the report to be used.
4. **Render drafts** (after skill 22 has written and validated `drafts.json`):
   `python "<SK>/render_templates.py" --drafts "<OUT>/drafts.json" --crs-template "<CRS form>.xlsx" --rfi-template "<RFI form>.docx" --out-dir "<OUT>/drafts"`
   The out-dir shall not be the forms' folder. Open one output and confirm the DRAFT line is visible.
5. **Report back** in chat: totals by status and route, the files written, every check failure, and
   the unverified, inconsistent and not-run items. Give the reader the file paths, not the content.

An Arabic summary is written only when the owner asks for one (see skill 22, hard rule 6).

## Outputs and exit codes

`findings_view.json`, `report.md`, `report.xlsx`, `CRS-<SITE>-<sub>-DRAFT.xlsx`, `RFI-<SITE>-<nn>-DRAFT.docx`.
Exit codes (SPC-0006 §2.3.3): 0 done · 2 input error · 3 missing package · 4 schema or count check
failed · 5 refused.

What the report holds, the route table, and how the forms are filled: `references/report_contents.md`.
