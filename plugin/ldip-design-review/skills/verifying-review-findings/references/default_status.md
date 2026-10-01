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
| `dwg_vector` | recompute from `0.ai_context` — not built yet | not_rechecked |
| `pdf_vector_text`, `pdf_ocr` | re-crop with skill 2 `crop.py` — not built yet | not_rechecked |
| other | none | not_rechecked |

Values are compared at the precision printed in the extract (e.g. `5508.27` ± 0.005, `3.57095e+06` ±
5); text is compared trimmed and case-insensitive.

## 5. Known limits

- The cell re-read confirms the quoted observations; it does not recompute the rule arithmetic
  (for example GFA = area × FAR). The method text says so. Rule-level recomputation for GIS rules
  is a candidate improvement.
- A finding whose extract quotes no cell values is `not_rechecked`.
