# Drawing / coordinate rules — what the scripts check and the values they need

Contents: 1 Inputs · 2 GIS-008 · 3 GIS-017 · 4 GIS-009 · 5 Matching rules · 6 Values · 7 Known limits

Spec: SPC-0006 Rev B §4.6 (base) + Rev P §4.18 (coordinates 0.3.0). Schedule rules are in
`schedule_rules.md`. Rule values come only from the rules file (skill 8); never from this document.

## 1 Inputs

- DWG ingestion (`*.dwg.json`, skill 1) — closed polylines, texts, inserts; expanded blocks included.
- LandXML ingestion / surface side files (skill 3) and IFC surface side files (skill 13) when present.
- Rules file from `get_rules.py`. Absent / null / GAP value → that sub-check is `not_run`.
- Per-site tables use internal keys `QRWN`, `NRJS`, `NMAR`.

## 2 GIS-008 — `check_crs.py` (0.2.0)

| Step | Value | Pattern |
|---|---|---|
| C1 units | `insunits` (6 = metres) vs `$INSUNITS` | `insunits_not_metres` |
| C2 CRS | `crs` vs GEODATA / LandXML `CoordinateSystem` (normalised: `32638` / `EPSG:32638` / `EPSG::32638` / `urn:ogc:def:crs:EPSG::32638`); none → `not_declared`, no finding | `crs_declared_other` |
| C3 envelope | `site_envelope[SITE]` + `envelope_buffer`; `context_layers[SITE]` (name/prefix, incl. via_block) → `profile_context_outside` only | `outside_site_envelope/<layer>` |
| C4 displace | `known_displacements[SITE]`, `displacement_min_entities`; ≥95 % inside after **adding** a known vector (`site = drawn + vector`, SRC-11) → finding; else candidate vector (info) | `displaced_layer_group/<layer>` |
| C5 models | LandXML/IFC design → inside test; survey (`provenance` employer, or role/name NGL/EGL) → coverage vs `site_boundary` | `outside_site_envelope/<source>`, `survey_not_covering_site` |
| C6 control | `datum_reference[SITE]` vs boundary / centroid | none (`control[]` info) |

GIS-008 stays advisory → candidates capped Probable. Extents from geometry, not `$EXTMIN`/`$EXTMAX`.

## 3 GIS-017 — `check_control.py` (0.1.0)

| Check | Value | Pattern |
|---|---|---|
| Site TBM | `datum_reference[SITE].site_tbm` (from GIS-008) | `no_site_tbm` |
| Distance | `max_distance_tbm`; boundary from GIS-011 `site_boundary` else envelope | `tbm_too_far` |

No TBM → fail with `no_site_tbm` (issue_type `missing_information`). Missing threshold or boundary → `not_run`.

## 4 GIS-009 — `check_topology.py`

Plot polygons on `plot_layers[SITE]`. Validity, closure, overlap (`overlap_tol`), sliver (`sliver_min_area`). Gap test not run. Unchanged in 0.3.0.

## 5 Matching rules — `match_plots.py` (0.2.0)

Plot table needs GIS-005 `plot_label_source[SITE]`. GIS-001 duplicates excluded.

| Rule | Value(s) | Pattern |
|---|---|---|
| GIS-005 | `plot_label_source` | `in_dwg_not_dlus`, `in_dlus_not_dwg` |
| GIS-006 | `tolerance_abs` (+ `tolerance_rel` when set) | `area_differs` |
| GIS-004 | `layer_mapping[SITE]` | `land_use_differs` |
| GIS-010 | `min_area_by_land_use` | `below_min_area/<Level 2>` |
| GIS-011 | `tolerance_abs`, `site_boundary[SITE]`; C8 also `official_site_area[SITE]` (nature/deed) vs drawn boundary | `site_area_differs` |
| GIS-007 | `reference_dataset[SITE]`, `tolerance` (0.02 m); runs only when dataset set | `boundary_off_reference` |
| GIS-016 | CMP source | always `not_run` |

## 6 Values

Envelope polygons, context layer lists, known displacements, TBM distance, official areas and the
reference cadastre live in the approved rulebook (rules_v9 for envelope/context/displacement; rules_v8
holds datum_reference / official_site_area shapes). This skill never reads an envelope from a drawing
at run time (DP-2).

**Parameter note — `known_displacements[SITE]`.** Each entry is `(dE, dN)` to **add** to the drawn
coordinates to bring that layer group into the site frame (`site = drawn + vector`). SRC-11 §4.6 /
SPC-0006 §4.18.3 C4. Example: NRJS `(−1107.56, +34.31)`. The wrong sign must not match.

## 7 Known limits

- Arcs: vertices only; bulges → `not_checked_bulge` for validity.
- Layer names depend on the converter; layer-based values need oda / accoreconsole (or ezdwg with care).
- GIS-007 needs an approved reference dataset (R-P3); method is built, inactive until the value is set.
- Vertical datum / elevation checks are out of scope for 0.3.0.
