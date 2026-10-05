# Drawing rules — what the scripts check and the values they need

Contents: 1 Inputs · 2 GIS-008 · 3 GIS-009 · 4 Matching rules · 5 Values not yet in the rulebook · 6 Known limits

## 1 Inputs

- Ingestion files `0.ai_context/<SITE>/<sub-id>/b<nn>/<file>.dwg.json` written by skill 1. Only
  closed model-space polylines, model-space texts and block inserts are used.
- The rules file from `get_rules.py`. A rule that is not in it (inactive, or not a GIS rule) returns
  `not_run`. A value that is absent, `null` or tagged GAP turns its sub-check into `not_run`.
- Per-site values are tables keyed by the internal site key (`QRWN`, `NRJS`, `NMAR`).

## 2 GIS-008 — `check_crs.py`

| Sub-check | Value | Pattern |
|---|---|---|
| units | `insunits` (6 = metres) vs header `$INSUNITS` | `insunits_not_metres` |
| crs | `crs` vs a declared GEODATA system; none declared → `not_declared`, no finding | `crs_declared_other` |
| envelope | `site_envelope[SITE]` (`bbox` or `polygon`) grown by `envelope_buffer` | `outside_site_envelope/<layer>` |

Extents are taken from the geometry, because `$EXTMIN` / `$EXTMAX` are empty with some converters.
One finding per layer, with the count and ten sample handles.

## 3 GIS-009 — `check_topology.py`

Plot polygons are the closed polylines on `plot_layers[SITE]`. Without that value no sub-check runs.
Validity is still measured on every closed polyline and written to `profile_invalid_by_layer` as
information only. Sub-checks: validity (`invalid_polygon/<layer>`), closure (open polylines on a plot
layer, `open_polyline/<layer>`), overlap above `overlap_tol` (`overlap`), area below `sliver_min_area`
(`sliver/<layer>`). The gap test is not run (site boundary and non-plot areas undefined).

## 4 Matching rules — `match_plots.py`

The plot table needs GIS-005 `plot_label_source[SITE]` = `{"polygon_layers": [...], "text_layers": [...]}`.
A plot's ID is the single label text whose insert point lies inside the polygon. Polygons with no
label or several labels are counted in `plots`, never matched. GIS-001 duplicates are excluded.

| Rule | Value(s) | Pattern |
|---|---|---|
| GIS-005 | `plot_label_source` | `in_dwg_not_dlus`, `in_dlus_not_dwg` |
| GIS-006 | `tolerance_abs` (and `tolerance_rel` %, both must be exceeded when set) | `area_differs` |
| GIS-004 | `layer_mapping[SITE]` = {layer: DLUS Level 2} | `land_use_differs` |
| GIS-010 | `min_area_by_land_use` = {Level 2: m2} (DLUS only) | `below_min_area/<Level 2>` |
| GIS-011 | `tolerance_abs`, `site_boundary[SITE]` = [[E, N], ...] | `site_area_differs` |
| GIS-007, GIS-016 | reference cadastre, CMP source | always `not_run` |

## 5 Values not yet in the rulebook

`site_envelope`, `plot_layers`, `plot_label_source`, `layer_mapping` and the tolerances above are not
in rules_v0. Their shape is a proposal (P-S3-44 and the R8 answer); the values come only from a later
approved rulebook. Until then the scripts report the sub-checks as `not_run`.

## 6 Known limits

- Arcs: the ingestion keeps vertices only. Polygons with bulges are counted `not_checked_bulge` for
  validity; their area (from the arcs) is used for sliver, and their chords for overlap.
- Layer names depend on the converter: ezdwg renames some layers (for example `S1` → `LAYER_3F6F`).
  Layer-based values work only with the oda or accoreconsole engine.
- Legend and key-plan swatches drawn outside the site are reported by the envelope test. The
  Verifier and the reviewer judge them; the script does not guess which layers are legends.
