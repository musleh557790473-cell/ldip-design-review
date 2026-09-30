# Schedule rules — what the scripts check

## Contents

1. Reading the sheets
2. Rules and patterns
3. Candidate fields
4. Known limits

## 1. Reading the sheets

- **DLUS (GIS-002, `read_dlus.py`).** The header is found by its labels ("Plot ID", then two header
  rows). Required columns: Plot ID, Level 1, Level 2, Land Area. Plot rows end at the first empty
  Plot ID; anything below (for example a CMP/CAD check block) is counted in `trailing_rows_ignored`
  and never read as plots. A Plot ID of `-` is the street row: counted in areas, not in plot counts
  (SPC-0004 §5.2).
- **Numbers.** `1,263.04`, `(88.64)` (negative), `0.34%` and `-` (zero in a numeric column) are parsed
  the same way from xlsx and CSV.
- **Overall Summary.** Header found by "Level 1". Land-use rows run to the `TOTAL` row. Below it, up
  to the first blank row, a row with an area and a text label is a *labelled reconciliation line*;
  rows whose label contains "check" are check totals, not reconciliation lines.
- **Planning Assumptions.** Header found by "Level 1"; rate and unit columns under the Residents,
  Workers, Visitors and Parking groups.
- **Community Facilities.** The values next to "Site Area (m2)", "Residents" and "Density (pr/ha)".
- Level 1 and Level 2 are compared trimmed and case-insensitive.

## 2. Rules and patterns

| Rule | Script | Pattern | Meaning |
|---|---|---|---|
| GIS-002 | `read_dlus.py` | `header_not_found`, `missing_column`, `unparseable_area` | DLUS not readable; stops the dependent rules |
| GIS-001 | `check_ids.py` | `duplicate_plot_id` | A Plot ID on more than one row; IDs go to `excluded_plot_ids` |
| GIS-003 | `check_schedule.py` | `level2_under_several_level1` | One Level 2 under different Level 1 classes across DLUS and Summary |
| GIS-012 | `check_schedule.py` | `level2_missing_from_summary` | DLUS Level 2 not in the Summary and not explained by a labelled line |
| | | `summary_level2_not_in_dlus` | Summary Level 2 with area or plots but no DLUS rows (zero rows are ignored) |
| | | `area_ne_dlus` | Area differs by more than `area_tol`, and no labelled line equals the difference |
| | | `plots_ne_dlus` | Plot count differs (exact; not checked for the street row) |
| GIS-013 | `check_schedule.py` | `villa_gfa_not_fixed` | Villa GFA ≠ `fixed_villa_gfa` × units |
| | | `gfa_ne_area_x_far/<FAR> vs implied <x>` | GFA ≠ Land Area × FAR (non-villa rows) |
| | | `gfa_without_breakdown/<Level 2>` | GFA with all breakdown columns empty |
| | | `gfa_ne_breakdown/<Level 2>` | GFA ≠ Residential + Commercial + Community Facilities |
| GIS-014 | `check_schedule.py` | `<field>_ne_planning_assumption/villas <x> per unit` | Residents or required parking ≠ Planning Assumptions rate × units (villa rows) |
| GIS-015 | `check_schedule.py` | `site_area_ne_dlus` | Community Facilities site area differs from the DLUS total by more than `tol_rel` %; the extract also gives the stated density and the density on the DLUS area |
| | | `far_ne_summary` | A DLUS FAR differs from the Summary FAR for the same Level 2 (villas excluded, SPC-0004 §7.2) |
| | | `height_ne_summary` | A DLUS height differs from the Summary height (same notation only) |

**Labelled reconciliation (GIS-012).** The Level 2 groups missing from the Summary are tried as a set:
if the sum of the areas of some of them equals a labelled line within `area_tol`, those groups pass.
Example: QRWN "Villa - Effected by boundary" + "Mixed use-Effected by Boundary" = 53,430.44 m², the line
"Total area effected by boundary". An unlabelled number (NMAR row 19, 35,995.52 m²) explains nothing.

## 3. Candidate fields

`build_candidates.py` sets, per pattern:

- `finding_id` `<run>/GIS-nnn` in order; `agent` GIS; `stage` candidate; `confidence` High (deterministic cell reads).
- `statement`: the pattern message, the count, the Level 2 names or sample Plot IDs, up to three
  observed extracts, and the rule requirement. No reasoning (SPC-0002 C5).
- `location`: the file and sheet the pattern was read from (`file_path` relative to LDIP_Review,
  `file_sha256` of that file), up to 40 cells of that sheet, and the Plot IDs. Cells of another sheet
  appear only in the extract.
- `evidence` E01: tier deterministic, `source_type` `xlsx_cell` or `csv_cell`, all sample extracts,
  the count as `measured_value`, and the script name and version.
- `proposed_status`: the lower of the rule's `default_status` for the source type and its cap.
- `severity`, `issue_type`, `owner.role`: the rule defaults.

## 4. Known limits

- GIS-014 checks villa rows only. Other land uses need an agreed DLUS Level 2 → Planning Assumptions
  mapping [GAP]; their count is reported as `rows_not_checked_no_mapping`.
- GIS-015 height: a numeric DLUS height (e.g. `2`) and a `G+n` Summary height are not compared
  (the notations may count floors differently) [GAP]; the skip is listed in `subchecks_skipped`.
- GIS-003: the controlled vocabulary of land-use zones (ER §7.2.1) is not received [GAP]; only the
  internal consistency is checked.
- The DWG rules (GIS-008, GIS-009, GIS-004 to GIS-007, GIS-010, GIS-011, GIS-016, GIS-017) need skill 1
  output and are built in phase (b).
