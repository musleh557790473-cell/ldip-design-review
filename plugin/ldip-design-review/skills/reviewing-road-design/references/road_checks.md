# Road checks — phase A (skill 10)

Contents: 1 Rules and modes · 2 Road classes · 3 Design-criteria table · 4 Typical sections · 5 LandXML
· 6 Required inputs and stage · 7 Status and severity · 8 Limits of version 0.1.0

Spec: AICC-BIM-SPC-0006 Rev D §4.13 (owner approved 15 Oct 2026 10:34, D10-1 … D10-8).

## 1 Rules and modes

| Mode | Script | Rules |
|---|---|---|
| register | `build_register.py` | ROD-001 (class terms map to ER classes; one class per ROW width across carriers) |
| criteria | `check_criteria.py` | ROD-002–004, 006–012, 014, 016, 017, 018, 021, 022, 024–026, 028–031, 057 — only rules that list `design_report` in `inputs` (D10-4; rules_v3) |
| sections | `check_sections.py` | ROD-002–004, 010–012, 014, 016, 021, 048 (lowest width only), 057 |
| alignment | `check_alignment.py` | ROD-017, 018, 024–026, 028–030 |
| required | `check_required.py` | items C1–C4 of `required_inputs` |

A rule absent from the rules file (inactive) never runs.

## 2 Road classes (D10-2, on D-13 / SPC-0004 Rev E §5.8)

| Parsons term (whole words, longest first) | Rulebook key |
|---|---|
| neighbourhood collector, urban collector, sub collector, collector | `sub_collector` |
| local / access, local access, access road, local road, local | `local_access` |
| service road, frontage road | `service_road` |
| arterial | `arterial` |

Any other term → no class (no guess).

## 3 Design-criteria table

Header line: "Parameter" plus two or more class headings. Columns are bounded half-way between heading
centres. Row labels → criteria: design speed, posted speed, functional classification, right-of-way, lane
width, number of lanes, median, curb, carriageway cross slope, sidewalk cross slope, e-max, minimum
horizontal radius, maximum / minimum longitudinal grade, K crest, K sag, minimum vertical curve length,
vertical clearance, design / check vehicle. "15/18" → two values, each checked. "None" for median or e-max
→ element not provided, no check. Speed-keyed rules (ROD-028–030): stated speed, and the ER minimum of the
class when the stated speed is below it (D10-5); a speed that is not a key of the rule table is not
interpolated. ROD-028 at 50 km/h uses `k_crest_50_derived` (D-16). Departures (DEP-nn) are listed in
`criteria.json` only (D10-7).

## 4 Typical sections (DWG text)

Skill 1 gives text with insert points; dimensions have no position. A section = the band between a pair
of vertical "RIGHT OF WAY" labels; its title = the class text just below the band. Element assignment:

| Assignment | When | Cap |
|---|---|---|
| `labelled` | a "DRIVING LANE", "SIDE WALK" or "MEDIAN" label directly under the value; ROW from "(RIGHT OF WAY)" or equal to the title width; curb height from "CURB (150 mm)" | rule cap |
| `positional` | no label: outermost value each side = sidewalk; inner 2.5–4.0 m values = lanes; % values between the lanes = crossfall | Probable |

`dimension_match` = the value equals a dimension measurement of the drawing (information only).
Sidewalk (ROD-048): frontage land use is not read in phase A (C8), so only the lowest required width is
enforced; a width between the lowest and the highest is listed as not run.

## 5 LandXML (skill 3 ingestion)

Class of an alignment: a class term in its name or desc (Probable cap). Link to a road label: by name, else
a label within half the widest ROW group of the alignment (Probable). Without class: only ROD-026 runs.

## 6 Required inputs and stage

Stage name → phase by the rulebook `stage_map`. Item before its phase → not run. Present in a listed carrier
→ pass. A listed carrier not read in phase A (plan_geometry, details, sections, drainage plans …) → not run,
Not auto-verifiable (M2-3). Else → Missing information, one finding per item with the files searched (M5).
DD milestone exceptions from the MIDP (§5.14A) are not read in phase A.

## 7 Status and severity

Status = lowest of: the rule's `default_status` for the evidence source (`pdf_vector_text` Probable,
`dwg_vector` / `landxml` Confirmed), the parameter cap (advisory → Probable) and any link cap.
Severity = the rule's; tier-2 parameters (`tier2_*`) → Observation, one finding per (rule, parameter) per
submittal (Rev E §5.9). PDF evidence carries a PNG crop (label and value row) with its SHA-256.

## 8 Limits of version 0.1.0

- Road class per road from the hierarchy linework is not available: skill 1 keeps no open-polyline
  geometry ([GAP] K17). Classes are per class column (criteria) and per section title.
- PDF-only typical sections are not read (DWG only).
- ROD-019, 020 stay inactive until real LandXML is tested (T7, D10-6). ROD-027 and 037 are phase C.
- Presence rules (ROD-101 … 116) are phase B.
