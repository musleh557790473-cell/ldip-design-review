# Drafting rules (skill 22, standalone 0.1.0)

## CRS row — one per verified finding on the CRS route

| Form column | Value |
|---|---|
| `#` | 1, 2, 3 … within one CRS (one CRS per site + submittal) |
| Page/Para/Clause/ Doc. No. Etc. | Document number (+ revision), then the location: sheet and cells, PDF page and box, DWG layer and handles, plots, or "whole submittal" |
| Review Comment | The finding statement, then `Reference: <rule> (rulebook <v>) — <source document> — <clause>`, then `Evidence: <extract 1> \| <extract 2>` (at most two extracts, 300 characters each) |
| Reviewer Initial | empty (reviewer) |
| Review No. | `R0` (option `--review-no` for a later review) |
| Contractor's Response, Responder Initial | empty (Parsons) |
| Status Code | empty (reviewer: A, B, C, D) |
| Comment Status | Open |

A finding with status Missing information carries `eot_assessment: true` in the drafts and on the
"Draft trace" sheet only; the CRS text does not mention EOT.

## RFI — one per verified finding on the RFI route

| Form field | Value |
|---|---|
| RFI Reference Number | "DRAFT — number assigned on issue" |
| Rev. No. | 00 |
| Discipline | from the finding's agent: INT Document control · GIS GIS / Masterplan · RDE Roads · WET Wet utilities · DRY Dry utilities · COO Coordination · STR Structures |
| Subject | the finding title |
| Drawing No(s). | document number (+ revision) |
| Document / Specification | reference source document / clause |
| Location | site name (table below) |
| Information requested | the finding statement + the reference line |
| Attached documents | evidence crop files of the finding, if any |
| Reason for RFI ticks, dates, priority, signatures, responses | empty |

## Header values per site [PROPOSED]

| Site | Project | Section | Sub-Section | Location |
|---|---|---|---|---|
| QRWN | LDIP - Package (1) – Al-Qairwan & Al-Narjis | Package-1 | QRWN | AL-QAIRAWAN |
| NRJS | LDIP - Package (1) – Al-Qairwan & Al-Narjis | Package-1 | NRJS | AL-NARJIS |
| NMAR | LDIP - Package (3) – Dahiyat Namar | Package-3 | NMAR | DAHIYAT NAMAR |

All sites: Program LDIP · CRS Ref "DRAFT — number assigned on issue" · Reviewer/s Organization
"JV LADUN & AL-AYUNI" · Contractor "Saudi Arabian Parsons Engineering Consultancy (SAPEC)" · Subject
"Design review comments — <site> — submittal <sub-id>" · Document No = the single document number, or
"Multiple — see column 'Page/Para/Clause/ Doc. No.'".

Override keys for `--header`: project, section, sub_section, location, reviewer_org, contractor,
crs_ref, rfi_ref, subject, document_no, pmcm.

## Not drafted

| Route | Listed in | Reason |
|---|---|---|
| ENGINEER_QUEUE | `engineer_queue[]` | Probable — an engineer decides |
| MANUAL_CHECK | `manual_check[]` | Not auto-verifiable — what and where to check |
| UNVERIFIED | `not_drafted[]` | candidate — run skill 18 first |
| NONE | `not_drafted[]` | legacy number, register only |
| RISK_EOT_REGISTER | `not_drafted[]` | internal register, not correspondence |
| INCONSISTENT | `not_drafted[]` | status and issue type match no route (SPC-0005 §5.9.1) |
