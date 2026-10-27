# Consistency methods (skill 16 — SPC-0006 Rev N §4.16)

Roads + earthworks only (DN-1). Report only until a rule group exists (DN-4). No specification reader (DN-7).

## Inputs

| Carrier | Reader | Notes |
|---------|--------|-------|
| BOQ `.xlsx` | `read_boq.py` | Values **and** formulas; parts, items, units, CSI / CESMM4-like codes; base × factor (DN-6) |
| Design / concept report | `read_report.py` on skill-2 text | Heading locator; claim families in `claims.md` |
| Model quantities | skill 13 `earthworks_profile.json` | Cut / fill volumes; road-top − ToS stats — **not recomputed** (DN-3) |
| Drawing tables | JSON from skill 13 / 2 | Cut / fill table values when present |
| Road linework | skill 10 register / linework JSON | Road area, curb / kerb length — **not recomputed** (DN-3) |

## Checks

| # | Method | Result |
|---|--------|--------|
| C1 | BOQ earthworks **base** (before factor) vs model vs drawing table; flag when DN-5 exceeded | `inconsistency`, Probable, Minor |
| C2 | Formula `base × k` and constant split ratios (e.g. 0.2 / 0.8 / 0.6) not stated in the report | Observation ("basis not stated") |
| C3 | Text claims ("balanced", import / export) vs BOQ disposal / import | Observation |
| C4 | Stated pavement depth vs skill-13 median road-top − ToS; flag if \|Δ\| > 0.05 m | `inconsistency`, Probable, Minor |
| C5 | `#REF!` / error cells; part arithmetic (fill ≈ excavation − disposal) | `inconsistency`; Confirmed allowed for `xlsx_cell` |
| C6 | Road area / kerb vs model / linework | **Information** at 30 %; finding from 60 % |
| C7 | Specs vs BOQ | Not built (DN-7) |

## Tolerances (DN-5)

- Volumes (m³): difference > **10 %** **and** > **1,000 m³**
- Areas / lengths (m² / m): difference > **10 %**
- C4: median off > **0.05 m**

## Evidence (SPC-0003)

- BOQ cells → `xlsx_cell` (deterministic; Confirmed allowed for cell errors)
- Report claims → `pdf_vector_text` / crop (Probable cap when OCR)
- Model / profile → `script_output`
- Findings are **report-only**: `reference.rule_id` is always null (DN-4)
