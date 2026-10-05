# Status model and re-check paths

## Contents

1. The four statuses
2. How the status is computed
3. Default status by evidence source
4. Re-check paths
5. Known limits

## 1. The four statuses (brief §5.1)

| Status | Meaning |
|---|---|
| Confirmed | Deterministic evidence, a cited rule, and an independent re-check that agrees |
| Probable | Likely, but the evidence or the re-check is not strong enough for Confirmed |
| Not auto-verifiable | Needs a person; `manual_check` says what and where |
| Missing information | A deliverable owed by Parsons is absent; needs `issue_type: missing_information` and a cited obligation. Ranks with Confirmed |

Missing information is not Not auto-verifiable: the first is a finding against Parsons, the second is
a request for a manual check.

## 2. How the status is computed (SPC-0005 §5.7.2)

Status = the lowest of:

- (a) the rule's `default_status` for the strongest evidence source (table §3);
- (b) the rule cap: active → Confirmed, advisory → Probable; inactive or unknown rule → Probable;
- (c) the re-check: `disagrees` → Probable (V2); `not_rechecked` → at most Probable (V1).

Then SPC-0003 §4.6: V5 Low confidence never Confirmed; V4 Missing information without
`issue_type: missing_information`, `source_doc` and `clause` → Probable; V3 Not auto-verifiable gets
`manual_check.what` / `where`.

## 3. Default status by evidence source (SPC-0004 §5.5)

| Source | Default |
|---|---|
| `dwg_vector`, `xlsx_cell`, `csv_cell`, `script_output` | Confirmed |
| `pdf_vector_text` with a verbatim crop | Confirmed |
| `pdf_ocr` | Probable |
| `pdf_colour`, `pdf_raster_visual` | Probable, never sole evidence |

The rule's own `default_status` in the rulebook governs; this table is the convention behind it.

## 4. Re-check paths (`recheck.py`)

| Evidence | Second path | Result |
|---|---|---|
| `xlsx_cell` | every quoted `CELL=value` re-read from the workbook XML (zipfile + ElementTree), not openpyxl; sheet prefixes `Summary`, `DLUS`, `PA`, `Community Facilities` map to their sheets | agrees / disagrees |
| `csv_cell` | the same with an own CSV parser, not the csv module; tokens of other sheets (other CSV files) are skipped | agrees / disagrees |
| any | source file hash ≠ `location.file_sha256` | disagrees (file changed since the finder read it) |
| `xlsx_cell` GIS-012 / 013 / 014, with `--rulebook` | operand cells named by role re-read, rule formula recomputed with the rulebook tolerance (P-S3-24) | agrees only if every sample still breaches; else disagrees |
| `dwg_vector` | no independent second path yet | not_rechecked |
| `pdf_vector_text` | every quoted `"text"` looked for in the text pdfplumber reads inside `location.pdf.bbox_pt` (finder: pypdfium2); rotated pages and pages above 200,000 objects not supported | agrees / disagrees (not_rechecked on a rotated or very large page) |
| `pdf_ocr` | none independent (a second OCR is not independent); skill 2 `crop.py` gives the image | not_rechecked |
| other | none | not_rechecked |

Value match by type (P-S3-43): a quoted value is a **number** only when the whole token is a number
(thousands commas, `%`, parentheses allowed), optionally followed by one unit `m2`, `m²`, `m`, `ha`,
`pr/ha` (recorded, not compared). Numbers are compared at the printed precision (e.g. `5508.27` ± 0.005,
`3.57095e+06` ± 5). Anything else — `1/1`, `G+2`, `12a`, `5 kg` — is **text**, compared with the cell's
raw text after collapsing spaces, case-insensitive. A number against a text cell disagrees. `-` means
zero or an empty cell.

Recomputed formulas (P-S3-24): GIS-013 `|gfa − area × far|`, `|gfa − units × fixed_villa_gfa|`,
`|gfa − (res + com + cf)|` against `tol`; GIS-014 `|field − units × rate / per|` against `tol_persons` /
`tol_spaces`; GIS-012 Summary area / plots against the DLUS sum / count of that Level 2 re-read over
the quoted row range, against `area_tol`.

## 5. Known limits

- Rule arithmetic is recomputed for GIS-012, GIS-013 and GIS-014 only, and only with `--rulebook`.
  For other rules the re-read confirms the quoted values; the method text says which applied.
- GIS-012: a labelled reconciliation line is not re-evaluated. CSV sources: operands in another CSV
  file (another sheet) are not recomputed.
- A finding whose extract quotes no cell values is `not_rechecked`.
