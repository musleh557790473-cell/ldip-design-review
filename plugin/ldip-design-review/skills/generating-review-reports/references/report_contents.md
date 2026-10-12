# Report contents, routes and form filling (skill 24, standalone 0.1.0)

## Contents

1. Findings view (`load_findings.py`)
2. Route table (SPC-0003 Rev D §6)
3. Report (`build_report.py`)
4. Count check (`check_report.py`)
5. Form filling (`render_templates.py`)
6. Limits

## 1. Findings view

One row per finding, copied from the findings files: id, run, site, submittal, agent, stage, rule,
rulebook version, reference text, title, statement, issue type, severity, confidence, verification
status and re-check, manual check, document and readable location, evidence extracts. Added and
labelled: `route`, `route_basis`, `route_kind` (`provisional`, `registered`, or `none` for candidates).
The view records each input file's SHA-256, so the count check can tell when an input changed.

Inputs accepted: a JSON list of findings, or `{"findings": [...]}`, at any stage. One schema error
stops the run (exit 4). A finding id seen twice is an input error (exit 2).

## 2. Route table (first match)

| # | Condition | Route |
|---|---|---|
| — | stage `candidate` | UNVERIFIED (view label; run skill 18 first) |
| — | stage `classified` / `registered` | the stored `output_route` |
| 1 | PKG-001/002/004/006, severity Observation, legacy number (skill 6 statement "Legacy number…", or a `legacy_numbers` entry of the rulebook) | NONE |
| 2 | Confirmed and design_error / inconsistency / package_control | CRS |
| 3 | Missing information and missing_information | CRS (+ EOT assessment, internal) |
| 4 | er_ambiguity | RFI |
| 5 | schedule_blocking | RISK_EOT_REGISTER |
| 6 | Probable | ENGINEER_QUEUE |
| 7 | Not auto-verifiable | MANUAL_CHECK |
| — | no row matches | INCONSISTENT (SPC-0005 §5.9.1: listed, never drafted) |

## 3. Report

Markdown and Excel hold the same numbers:

- inputs (path, SHA-256, findings, stages) and the rulebook versions the findings cite;
- status × severity, with candidates in the row "Unverified (candidate)", and "No findings." when empty;
- counts by route; findings by rule;
- the findings table, the CRS preview, the engineer queue, the manual-check list, the inconsistencies
  and the unverified candidates;
- rules run (result `pass` or `fail` in `--results`), rules and sub-checks not run with their reasons
  (`not_run` entries and `rules_skipped` lists in `--results`; `rule_skipped` events in `--run-log`),
  and failed steps (`fail` events in `--run-log`).

Every page carries the DRAFT line with the plugin and script versions.

## 4. Count check

`check_report.py` reads the findings files again (SHA-256 must equal the view), counts them without the
view's rows, and compares the totals, the status × severity table, the routes and the per-rule counts
with `report.md` and with the Summary and "By rule" sheets of `report.xlsx`. Exit 4 lists every
difference.

## 5. Form filling

Fields are found by their printed labels.

**CRS form (.xlsx).** Header: CRS Ref, Program, Project, Section, Sub-Section, Subject, Document No,
Reviewer/s Organization and Contractor from the drafts header; PMCM, Transmittal No., Reviewer/s Name
and every date are cleared; each "Review Code" is reset to "Select Status Code". Comment table: rows
from the first numbered row to the last bordered row are cleared, then filled with `#`,
"Page/Para/Clause/ Doc. No.", "Review Comment", "Review No." and "Comment Status" = Open. Reviewer
initials, contractor response, status code and signatures stay empty. When there are more comments
than rows, rows are inserted above the reviewer block; merged cells, row heights, the print area and
the list validations move with them. The DRAFT line goes in the empty row above the table and in the
page header; a "Draft trace" sheet lists finding id, rule, status, severity, route basis and the EOT
flag for AICC use and is removed before issue.

**RFI form (.docx).** RFI Reference Number, Rev. No., Discipline, Enclosure/s, Drawing No(s)., Document,
Specification and Location are written next to their labels; Subject and Information Requested go in
the box after their headings; issue and reply dates, priority, ticks (☒ → ☐), the designer and PMCM
responses and the attachment rows are cleared; evidence files are listed as attachments. The DRAFT
line goes in the page header and above the form.

Missing labels (for example a form of another layout) → exit 2 with the labels not found.

## 6. Limits

- Cover letters: no template yet [GAP — K7]; not rendered.
- Header values per site (project, section, organisation, contractor) are [PROPOSED] defaults in skill
  22 and can be overridden there.
- Excel row heights are estimated from the text length; very long comments may need a manual height.
