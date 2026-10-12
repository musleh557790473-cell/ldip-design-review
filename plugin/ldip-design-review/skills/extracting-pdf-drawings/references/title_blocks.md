# Parsons title block — how `title_block.py` reads it

Owner approval: K5-1, 11 Oct 2026. Source sheets: Namar 30% Concept Design package
(`submittals/NMAR/10-2026`), Parsons template "Ladun Dahiyat Namar Title Block_A1".

## Contents

1. Layout
2. Reading rule
3. Fields
4. Calibration
5. Limits

## 1. Layout

The title block is a column at the right edge of the sheet. Its label → value pairs sit in the bottom
part of that column; each value is printed under its label.

| Row (top → bottom) | Labels |
|---|---|
| Reference drawings | "Drawing Number", "Drawing Title", "Revision" (table, not read) |
| Revision table | revision, description, date rows above "Revision / Description / Date" (not read) |
| Purpose | "Purpose of Issue:" |
| Parties | "Client:", "PM / CM:", "Developer:", "Engineering Consultant:" (logos) |
| Project | "Project:", "Project No.:" |
| Title | "Drawing Title:" |
| Names | "Designed:", "Drawn:", "Checked:", "Approved:" |
| Data | "Scale at A1:", "Date:", "Revision:", "Sheet:" |
| Number | "Drawing No.:" |

The same template is printed on A1 (2384 × 1684 pt) and scaled onto A0 with page rotation 270
(3370 × 2384 pt displayed). The labels sit at the same share of the page on both.

## 2. Reading rule

1. Search zone: spans with x0 ≥ 0.75 × page width and top ≥ 0.60 × page height (displayed page).
2. Find each label by its words, on one row (±3 pt), each word starting within 12 pt of the previous one.
3. Value of a label = spans under it, from the label's left edge (−3 pt) to the next label on the same
   row, and down to the next label below that overlaps the column. Read top to bottom, left to right.
4. No "Drawing No.:" label → no title block on that page (`TITLE_BLOCK_NOT_FOUND` when no page has one).
   Cover sheets have no title block (owner approval K5-3).
5. `document_no` is upper-cased with spaces removed. Other values are kept as printed.

No fixed coordinates are used, so a sheet of another size with the same labels is read the same way.

## 3. Fields

| Field | Label | Example (ROD-DWG-10200-001) |
|---|---|---|
| `document_no` | Drawing No.: | LDIP-RCRC-SAP-03-DN-ROD-DWG-10200-001 (MIDP number, no revision) |
| `revision` | Revision: | 00 |
| `title` | Drawing Title: | ROAD LAYOUT |
| `date` | Date: | SEPT. 2026 |
| `status` | Purpose of Issue: | ISSUED FOR APPROVAL |
| `sheet` | Sheet: | 01 OF 05 |
| `scale` | Scale at A1: | 1:1000M |
| `project_no` | Project No.: | 20260622158 |
| `designed`, `drawn`, `checked`, `approved` | Designed: … Approved: | ESH, MIY, MSA, NOW |

## 4. Calibration

Seven Namar sheets, all read correctly (11 Oct 2026):

| Sheet | Size | Result |
|---|---|---|
| ROD-DWG-10200-001-00 | A1 | number, revision 00, ROAD LAYOUT, 01 OF 05 |
| ROD-DWG-00002-001-00 | A1 | LIST OF DRAWINGS, 01 OF 01 |
| UTL-DWG-30000-001-00 | A1 | UTILITIES CORRIDOR CROSS SECTION, date AUG. 2026 |
| PWT-DWG-10000-001-0A | A0, rotation 270 | revision 0A, 1 OF 5 |
| ELV-DWG-60000-001-00 | A1 | hold-out, LOW VOLTAGE SINGLE LINE DIAGRAM |
| ROD-DWG-10200-000-00 | A1 | hold-out, OVERALL ROAD LAYOUT |
| ROD-DWG-40000-001-00 | A1 | hold-out, PROFILE LAYOUT RD-M-01 |
| ROD-DWG-00001-001-00 | A1 | cover sheet: no title block (expected) |

## 5. Limits

- The revision table and the reference-drawing table are not read.
- A title block that is only a picture (scanned sheet) is not read; OCR spans are not used.
- In the DWG files the same values are attributes of the paper-space block "Ladun - Qairwan ATT"
  (insert at 831, 10 mm on the A1 frame). ezdwg 0.12.9 cannot decode those attributes (AC1032), so the
  DWG title block waits for K2 (skill 1 warning `TITLE_BLOCK_ATTRIBS_UNDECODED`, K5-2).
