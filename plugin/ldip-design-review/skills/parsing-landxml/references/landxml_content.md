# LandXML ingestion content (template 1.1)

Source: AICC-BIM-SPC-0006 Rev C §2.4.2 (I1–I6), §4.12.5–4.12.11.

## Contents

- Top-level fields
- `content` keys
- Coordinate order and units
- Warning codes
- Limits of version 0.1.0

## Top-level fields

| Field | LandXML value |
|---|---|
| `template_version` | `"1.1"` (DWG / PDF stay `"1.0"`) |
| `source.file_type` | `landxml` |
| `source.provenance` | `parsons` · `employer` · `aicc_export` — from `--provenance` |
| `source.document_no` / `revision` | from the file name only (SPC-0004 §5.1 rule, as skill 6); `null` when the name carries no number. A `-AICC-EXPORT` suffix is ignored for the number |
| `extractor` | `parsing-landxml`, `parse_landxml.py`, version; `converter` `{name: null, version: null}` |
| `crs.declared` | `EPSG:<epsgCode>` from `CoordinateSystem`, or `null` |
| `crs.units` | `meter` → `m`, `millimeter` → `mm`, any other `linearUnit` as read |
| `crs.insunits` | `null` |
| `title_block` | `null` (LandXML has no title block) |

## `content` keys

| Key | Holds |
|---|---|
| `header` | LandXML `version`, `date`, `time`, namespace version; `units` (system, linear, area, angular, direction, as read); `coordinate_system` (`epsg_code`, `desc`, `horizontal_datum`, SHA-256 of `ogcWktCode`); `project_name`; `application` (name, version, manufacturer, time stamp) |
| `carriers` | `landxml_alignment` (an alignment with geometry), `landxml_profile` (a design profile with PVIs), `landxml_surface` (a surface) — rulebook `required_inputs` carrier names (rules_v2) |
| `alignments[]` | `name`, `desc`, `sta_start`, `length`; `elements[]` in file order (`line` · `arc` · `spiral`: start / end E N, `length` as read, `length_computed` from geometry for lines and arcs, `radius`, `rot`, `center`, `pi`, `spi_type`, `radius_start` / `radius_end` (`"INF"` for a tangent end), `dir_start_deg` / `dir_end_deg` as read converted to degrees, `sta_start`, and for clothoids `end_check_m`); `station_equations[]`; `element_counts`; `min_radius` (arcs); `min_spiral_radius` (finite spiral radii); `continuity_gaps[]` (gap > 0.001 m between one element's end and the next one's start) |
| `profiles[]` | One entry per `ProfAlign` (`kind: design`) and per `ProfSurf` (`kind: existing`), with `alignment` = parent alignment name. Design: `pvis[]` (station, elevation, type) sorted by station; `tangents[]` with `grade_pct`; `curves[]` with `g_in_pct`, `g_out_pct`, `a_pct` = \|g_out − g_in\|, `k` = L / A, `curve` crest / sag. Existing: point count, station and elevation range |
| `surfaces[]` | `name`, `desc`, `surf_type`; `declared` (`area2DSurf`, `area3DSurf`, `elevMin`, `elevMax`); `computed` (2D / 3D area from visible faces only, Z range from all points); `declared_vs_computed` (computed − declared); `tin_built`; `counts` (points, faces as read, visible, invisible `i="1"`, excluded for a missing point, breaklines, breakline points, boundaries, duplicate ids); `extents` (E, N from all points); `tin_file` and `tin_sha256` |
| `inventory` | Count of every other top-level element (`CgPoints`, `Parcels`, `PipeNetworks`, `PlanFeatures`, `Roadways`, …) and of other alignment children (`Alignment/<name>`); each also a `NOT_EXTRACTED` warning |

Side file `<stem>.surface-<n>.json` (`schemas/landxml_surface.schema.json`): `points` `[id, E, N, Z]` in file
order, `faces` (visible faces with all three points present, as point ids), `breaklines` (`brk_type`,
`desc`, points `[E, N, Z]`; Z `null` for a 2D breakline).

Rounding: coordinates, lengths, stations, elevations 0.001 m; areas 0.01 m²; grades and A 0.0001 %; K 0.01.

## Coordinate order and units

LandXML writes `N E [Z]` in `P`, `Start`, `End`, `Center`, `PI`, `PntList2D` / `PntList3D` (plan). One
function (`landxml_geom.ne_to_en`) turns this into `E N [Z]`; every coordinate in the output is E, N, Z.
Profile text (`PVI`, curves, `ProfSurf`) is station, elevation and is not reordered.

Sanity check when the file declares EPSG:32638 or no CRS: Easting 166,000–834,000 m, Northing
0–9,330,000 m. Points that fit only when exchanged → `AXIS_ORDER_SUSPECT`; points that fit neither way →
`COORD_OUTSIDE_UTM_RANGE`. Site envelopes are GIS-008 (skill 9), not this skill.

## Warning codes

| Code | When |
|---|---|
| `CRS_MISSING` | no `CoordinateSystem`, or no usable `epsgCode` |
| `CRS_NOT_32638` | another EPSG code (no reprojection) |
| `UNITS_NOT_METRES` | linear unit other than metre (values not converted) |
| `AXIS_ORDER_SUSPECT` / `COORD_OUTSIDE_UTM_RANGE` | see above |
| `FACE_MISSING_POINT` | a face refers to a point id that is not in the surface (face excluded) |
| `DUPLICATE_POINT_ID` | the same point id twice (first kept) |
| `FACE_NOT_TRIANGLE` | a face with 4 points (split in two) or another count (excluded) |
| `TIN_NOT_BUILT` | a surface without `Faces` (areas `null`) |
| `NOT_EXTRACTED` | element types this version does not extract |
| `ELEMENT_NOT_EXTRACTED` / `ELEMENT_NOT_READ` | a `CoordGeom` element other than Line / Curve / Spiral, or one without readable Start / End |
| `CURVE_TYPE_NOT_ARC` | a `Curve` with `crvType` other than `arc` |
| `ELEMENT_LENGTH_MISMATCH` | `length` attribute vs geometry differ by > 0.001 m |
| `SPIRAL_END_MISMATCH` | clothoid End differs from the computed end by > 0.01 m |
| `CONTINUITY_GAP` | at least one gap in `continuity_gaps` |
| `PROFILE_WITHOUT_ALIGNMENT` | a profile whose alignment has no geometry or length (stations not checked) |
| `PROFILE_STATION_OUTSIDE_ALIGNMENT` | PVI stations outside the alignment range |
| `CURVE_AT_PROFILE_END` | a vertical curve with no tangent on one side (K not computed) |
| `PVI_NOT_READ` | a PVI without station and elevation |
| `NO_CARRIERS` | no alignment geometry, design profile or surface |
| `PROVENANCE_NAME_MISMATCH` | `-AICC-EXPORT` name suffix and `--provenance` disagree |
| `DOCTYPE_PRESENT` | a DOCTYPE without entity declarations (not loaded) |

## Limits of version 0.1.0

- Pipe networks, parcels, CG points, plan features, roadways and cross-sections are counted, not extracted.
- `dir*` attributes are recorded as read (converted to degrees); their angular convention is not
  interpreted. Spiral end checks use the tangent from geometry (Start → PI) and run only for clothoids.
- Station equations are recorded; element stations are internal (cumulative) stations.
- Real alignment / profile data (test T3) waits for the first DD package (brief Rev G §11 item 25).
