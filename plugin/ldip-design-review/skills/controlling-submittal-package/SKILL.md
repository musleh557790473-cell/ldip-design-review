---
name: controlling-submittal-package
description: >-
  LDIP design review (AICC JV): builds the sheet register of a Parsons
  submittal, pairs DWG and PDF files, parses document numbers against the
  LDIP numbering convention and checks completeness, formats, naming, section
  codes and duplicates using the LDIP Rulebook PKG rules. Use after all batch
  extractions of an LDIP submittal are complete.
metadata:
  version: "0.3.2"
  spec: "AICC-BIM-SPC-0006 Rev B §4.3; AICC-BIM-SPC-0004 Rev C §5.1, §5.7, §6; owner decisions D1-D8 (11 Oct 2026)"
---

# Controlling a submittal package

This skill checks a Parsons submittal as a package: which documents arrived, in which formats, under
which numbers, against the drawing or transmittal list and the Parsons MIDP. The scripts do every
check. You run them in order, read their one-line JSON results and report. Do not re-do a check by
hand, do not read the scripts' source and never edit a result file.

Rules covered: PKG-001 to PKG-009 and PKG-011, each according to its state in the rules file.
PKG-010 belongs to skill 7; PKG-012 is inactive in rules_v0. LOD / LOIN and ER deliverables are not
checked (no rulebook source, owner decision D7).

Packages: Python 3.11+, `openpyxl`, `jsonschema`.

## Hard rules

1. Get the rules only through `applying-ldip-rulebook` (step 1). Code lists, site map, legacy numbers,
   effective date, drawing formats and the regular expressions all come from that rules file.
2. SRC-02 governs numbering (SPC-0004 §5.1): DWG / MOD numbers have 10 fields, other documents 9. The
   MIDP's own naming scheme (sequence + sheet, no level, no revision) is not a standard to check against.
3. A file is matched to a MIDP row only `exact` or `normalised` (key: package, site group of the section
   code, discipline, type, sequence). `ambiguous` and `unmatched` are reported, never resolved by you.
4. The stage table (planned for this stage vs received) is information only. Never turn it into a
   finding (D5; proposed PKG-013 for rules_v1).
5. One finding per rule + field + pattern with the count and up to ten samples (SPC-0004 §5.7).
   `build_candidates.py` writes them; do not change a statement, status, severity or location.
6. Legacy numbers (rule parameter `legacy_numbers`, or issued before `effective_from`) are Observations.
7. The DWG origin (civil3d_native / saved_in_civil3d / autocad_or_exported) is a record in
   `package.json`, never a finding (SPC-0004 §6.3).
8. A rule or sub-check returned as `not_run` produces no finding. Copy its reason into
   `rules_skipped[]`, or `gaps[]` when the reason names a [GAP].
9. The submittal folder is read-only. Write outputs only to the output folder.

## Two modes (D1)

**Review run** — preloaded by `intake-package`, run from the LDIP_Review folder after all batches:
`<CTX>` = `0.ai_context/<SITE>/<sub-id>`, `<EV>` = `findings/<run>/evidence/intake`, MIDP files under
`rulebook/sources/midp/` (D6). Step 2 is `merge_batches.py`.

**Standalone** — the owner points the skill at a folder of received files. `<CTX>` and `<EV>` are one
output folder outside the submittal folder; `<run-id>` is `<SITE>-<label>-YYYYMMDDTHHMM`. Step 2 is
`inventory.py`. Standalone output does not pass the guard or the run log: it is a draft to be checked
with skill 18 before any use.

## Procedure (plan → validate → execute → verify)

`<SK>` is `${CLAUDE_PLUGIN_ROOT}/skills/controlling-submittal-package/scripts`; `<RB>` the rulebook file;
`<MIDP>` each MIDP workbook (repeat `--file`); `<STAGE>` the MIDP stage name of this submittal.

1. **Plan — rules:**
   `python "${CLAUDE_PLUGIN_ROOT}/skills/applying-ldip-rulebook/scripts/get_rules.py" --rulebook "<RB>" --agent INT --out "<EV>/rules_INT.json"`
2. **Validate the input — file list:**
   - review run: `python "<SK>/merge_batches.py" --context "<CTX>" --run <run-id> --out "<EV>/files.json"`
   - standalone: `python "<SK>/inventory.py" --root "<submittal folder>" --site <SITE> --sub <label> --out "<EV>/files.json"`
3. **Execute**
   - Register: `python "<SK>/build_register.py" --files "<EV>/files.json" --site <SITE> --sub <sub-id> --list "<drawing list>" --out "<CTX>/package.json"`
     When the owner confirms no drawing or transmittal list was received, use `--list-not-received`
     (PKG-007 reports it as missing); when it is simply not available, omit both (PKG-007 `not_run`). In
     standalone mode add `--ingestion <x.dwg.json> …` when skill 1 / 2 outputs exist. Title blocks are
     compared only through those files; never read a title block yourself.
   - MIDP: `python "<SK>/read_midp.py" --file "<MIDP>" --out "<EV>/midp.json"`
   - Match: `python "<SK>/match_midp.py" --package "<CTX>/package.json" --midp "<EV>/midp.json" --rules "<EV>/rules_INT.json" --stage "<STAGE>" --out "<EV>/midp_match.json"`
     An unknown stage name exits 2 and lists the MIDP's stage names; report them, do not pick one.
   - Rules: `python "<SK>/check_package.py" --rules "<EV>/rules_INT.json" --site <SITE> --package "<CTX>/package.json" --midp-match "<EV>/midp_match.json" --out "<EV>/pkg_results.json"`
     Add `--issue-date YYYY-MM-DD` when the transmittal date is known. To check the MIDP's own numbers
     (owner request), add `--midp "<EV>/midp.json" --midp-self`.
4. **Build and verify the candidates**
   `python "<SK>/build_candidates.py" --results "<EV>/pkg_results.json" --rules "<EV>/rules_INT.json" --run <run-id> --site <SITE> --sub <sub-id> --package "<CTX>/package.json" --out "findings/<run>/candidates/intake.json"`
   `python "<SK>/validate_findings.py" --findings "findings/<run>/candidates/intake.json" --rules "<EV>/rules_INT.json"`
   `python "${CLAUDE_PLUGIN_ROOT}/skills/applying-ldip-rulebook/scripts/check_cap.py" --findings "findings/<run>/candidates/intake.json" --rulebook "<RB>"`
   In standalone mode write the candidates to `<EV>/candidates.json` instead.
5. **Report** — standalone: also write the DRAFT workbook
   `python "<SK>/report_xlsx.py" --out "<EV>/package_report.xlsx" --package "<CTX>/package.json" --midp-match "<EV>/midp_match.json" --results "<EV>/pkg_results.json" --candidates "<EV>/candidates.json"`
   Review run: write `findings/<run>/reasoning/intake.md` (what ran, counts per rule, match levels,
   anything that looked wrong) and return the summary.

**New MIDP version** — compare it with the previous one before using it (two `read_midp.py` outputs):
`python "<SK>/diff_midp.py" --old "<EV>/midp_old.json" --new "<EV>/midp.json" --out "<EV>/midp_diff.json"`
Pass `--midp-diff "<EV>/midp_diff.json"` to `report_xlsx.py` to add the "MIDP changes" sheet. A draft
update may keep the same revision field: the version is the "Date Last Updated" cell plus the file hash.

## Outputs and exit codes

Every script prints one JSON line and writes one file. Exit codes (SPC-0006 §2.3.3): 0 done · 2 input
error · 3 missing package · 4 candidates failed `finding.schema.json` (nothing written). Report the
script's `detail` on any non-zero exit.

What each rule checks, the carriers, the patterns and the known limits: `references/package_rules.md`.
