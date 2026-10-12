# Package rules — what the scripts check

## Contents

1. Inputs and files
2. Document numbers and carriers
3. MIDP reading and matching
4. Rule by rule
5. Findings and evidence
6. Known limits ([GAP])

## 1. Inputs and files

| File | Written by | Content |
|---|---|---|
| `files.json` | `inventory.py` (standalone) / `merge_batches.py` (run) | One entry per received file: path, format, size, SHA-256, number from the name, ingestion file (run). Standalone: each .zip member too (`<zip>!/<member>`, kind `zip_member`, P-S6-5); `number_inherited` for unnumbered files of a numbered zip / folder (P-S6-7) |
| `package.json` | `build_register.py` | SPC-0006 §3.2: `transmittal`, `sheet_register[]`, `dwg_origin[]`, `missing[]`, `unlisted[]`, `files_without_number[]`, `previous_submittal` |
| `midp.json` | `read_midp.py` | Every MIDP row: sheet, cell, number, title, section heading, flagged formats + flag cells, file-name fields, data exchanges (stage, owner, planned, actual, revision); workbook identity cells |
| `midp_diff.json` | `diff_midp.py` | Identity, stage, row, renumbering, title, format and exchange changes between two MIDP versions |
| `midp_match.json` | `match_midp.py` | Match level per register row; stage table (information only) |
| `pkg_results.json` | `check_package.py` | Per rule and carrier: pass / fail / not_run, patterns with counts and samples, sub-checks not run |
| candidates | `build_candidates.py` | SPC-0003 findings, stage `candidate`, agent `INT` |
| `package_report.xlsx` | `report_xlsx.py` | DRAFT workbook: Summary, Findings, Register, Stage, Not run, MIDP changes |

## 2. Document numbers and carriers

- A file name carries a number when its leading token has at least six hyphen-separated fields of letters
  and digits. Text after the token (" - title", "_title") is ignored. Other names are listed under
  `files_without_number` and reported by PKG-001 (`no_document_number`).
- Parsing (SPC-0004 §5.1): field 7 is the type. DWG / MOD: 10 fields (… type-level-sequence-revision);
  any other type: 9 fields (… type-sequence-revision). When the count conforms, the last field is the
  revision and the rest is the base. When it does not, only fields 1–7 are interpreted (their position is
  the same in every structure) and the whole token is the base.
- MIDP layout (owner decision P-S6-6, 11 Oct 2026): Parsons names drawings with the MIDP number plus a
  revision (`…-DWG-10200-001-00` = type-sequence-sheet-revision). Such a number has 10 fields, so its
  base (`…-DWG-10200-001`) equals the MIDP number and the title-block number. A conforming 10-field number
  whose field 8 matches the sequence regex and field 9 is three digits is reported once under PKG-001
  `midp_scheme_naming`; its level (PKG-002) and sequence (PKG-003) are not checked separately.
- MIDP layout recognised by the parser (`layout: midp`): field 8 five digits, field 9 three digits, optional
  revision; the register base is then the MIDP number (first 9 fields) for drawings, models and documents.
  With rules_v1 PKG-001 `accepted_layouts` = [src02, midp] (owner, 12 Oct 2026: the latest MIDP governs),
  such numbers are compliant: no PKG-001 / PKG-002 level / PKG-003 hit, revision read from field 10.
- P-S6-7 (owner, 12 Oct 2026): a file without a number inside a numbered zip, in the folder extracted from
  it (same member path), or inside a folder whose name carries a number, takes that number
  (`attached_files` in `package.json`) and is not reported under `no_document_number`.
- P-S6-8 (owner, 12 Oct 2026, numbering only): with rules_v1 PKG-001 `numbering_scope` = `midp_deliverables`,
  the number structure (PKG-001) is checked only for deliverables planned in the MIDP. `match_midp.py` records
  `in_midp` per row: exact / normalised / ambiguous match, `prefix` (a MIDP number starts with the received
  number + "-") or `same_type_unique` (one MIDP row with the same package, site group, discipline and type).
  PKG-004 (revision) is not limited.
- A register row groups the files that share a base. Its carriers are: file names; DWG and PDF title
  blocks (from skill 1 / 2 ingestion files matched by SHA-256); the drawing-list entry.
- The drawing / transmittal list is read by header labels: a column named document / drawing number (or
  "Doc No", "Document Number"), and optional revision and title columns. CSV or XLSX.

## 3. MIDP reading and matching

- Columns are found by labels: the header row has "Document Number" in column B and the format labels
  (DWG, PDF, NWD, RVT, NWC, IFC, DWFx, DOCX, XLSX, XML, GDB); the row above gives Owner / Planned /
  Actual / Revision; the row above that gives "Stage:" + the stage name for each data exchange.
- Section headings (text in B, nothing in C, no flag) are recorded as the row's `section`.
- The MIDP version is identified by "Date Last Updated" and the file hash (owner decision, 11 Oct 2026:
  a draft update keeps the same revision field).
- Match levels: `exact` (a received number or its base equals a MIDP number), `normalised` (one MIDP row
  with the same package, site group of the section code, discipline, type and sequence), `ambiguous`
  (several rows; up to ten listed), `unmatched`. The site group comes from the PKG-006 site map
  (QN, AQ → QRWN; AN → NRJS; DN → NMAR; GN → QRWN and NRJS).

## 4. Rule by rule

| Rule | Carrier | What is checked | Patterns |
|---|---|---|---|
| PKG-001 | file, midp | Field count vs `fields_drawing_model` / `fields_document`; MIDP layout | `<drawing_model|document>_fields_<n>_expected_<m>`, `no_document_number`, `midp_scheme_naming` |
| PKG-002 | file, midp | Fields 1–7 (+ level for conforming 10-field numbers) in the code lists | `<field>_code_not_listed` (codes counted) |
| PKG-003 | file, midp | Sequence matches the method regex (conforming numbers only) | `sequence_format`; category sub-check `not_run` [GAP] |
| PKG-004 | file, midp | Revision matches the method regex; a number one field short ending in a sequence has no revision | `revision_format`, `revision_missing`; stage consistency `not_run` (PROPOSED value) |
| PKG-005 | file | Number base equal on file name, DWG title block, PDF title block, drawing list; revision equal on file name (K5-4), title blocks and list | `<carrier>_vs_<carrier>`, `revision_<carrier>_vs_<carrier>`; unread title blocks listed per reason (`<carrier>_not_read`, K5-2); cover sheets (sequence 00001, no title block) listed, not reported (K5-3) |
| PKG-005 | midp | File-name number vs the workbook's "Document Reference" cell | `file_name_vs_document_reference` |
| PKG-006 | file | Section field allowed for `--site` by the site map | `section_not_for_site` |
| PKG-007 | file | Drawing list vs received, both directions; no list received (`build_register.py --list-not-received`, owner 12 Oct 2026) | `listed_not_received` (missing_information), `received_not_listed` (package_control, Minor, Confirmed), `transmittal_not_received` (missing_information) |
| PKG-013 | file | MIDP rows with a planned date for `--stage` that were not received (owner activated 12 Oct 2026); `not_run` without `--stage` | `planned_not_received` (missing_information, cites the MIDP row) |
| PKG-008 | file | MIDP-flagged formats of exact / normalised matches; rulebook `drawing_formats` for other drawings | `missing_<FMT>/midp`, `missing_<FMT>/rulebook`; native files `not_run` [GAP] |
| PKG-009 | file | Same number + format received twice with different SHA-256 | `same_number_different_content` |
| PKG-009 | midp | Number planned twice | `duplicate_number` |
| PKG-011 | file | Unresolved xrefs / images / fonts in skill-1 ingestion files read with `--resolve-base` | `unresolved_<kind>` (dwg_vector → Not auto-verifiable) |

A `/legacy` suffix marks the legacy patterns of PKG-001, -002, -004 and -006 (severity Observation).

## 5. Findings and evidence

- MIDP-carrier patterns are kept per workbook sheet, so each finding points at one sheet and its cells
  and the Verifier can re-read them (`xlsx_cell`, quoted as `B15=<number>`).
- PKG-008 against the MIDP quotes the format flag cells (`J123=X`) and cites the MIDP (number, Date Last
  Updated) as `reference.source_doc`, so a Missing information status has its obligation.
- PKG-007 `listed_not_received` cites the list file and its cells.
- Other file-name patterns are `script_output`, located at the document (one sample) or at package level.

## 6. Known limits ([GAP])

- PDF title blocks are read by skill 2 (K5-1). DWG title blocks of Parsons sheets are attributes ezdwg
  cannot decode (`TITLE_BLOCK_ATTRIBS_UNDECODED`); they wait for the oda / accoreconsole engine (K2).
- Sequence category keywords are not in the rules file (seq_category holds digits only).
- `revision_in_file_name` is still a GAP parameter in rules_v0; the comparison runs by owner decision
  K5-4 (11 Oct 2026) and the parameter should be set in rules_v1.
- Zip members are listed in standalone mode only; in a review run intake does not open archives.
- Which deliverables need a native authoring file is not defined per MIDP row (PKG-008 native files).
- The Aconex transmittal format is not defined (K4); any CSV / XLSX list with a number column is read.
- The `normalised` key is calibrated on synthetic data only until the first real package (T3).
