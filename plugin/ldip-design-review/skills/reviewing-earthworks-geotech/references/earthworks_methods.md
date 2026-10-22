# Earthworks methods (skill 13) — SPC-0006 Rev L §4.14 + Amendment L-a + Amendment L-b

Loaded only when a step needs them. Rule values come from skill 8 (`get_rules.py --agent RDE`). Detection thresholds are in `earthworks_params.json` (not rule values).

## Readers

- **LandXML** — skill 3 side files (`*.surface-N.json`), any provenance.
- **IFC** — `read_ifc_surface.py`: line-by-line STEP stream (ADDENDUM); `IfcMappedItem` path; single-layer preference; `|n_z| > 0` for non-vertical; up/down only to split a thin solid; rotated placement → `IFC_ROTATED_PLACEMENT`.
- **Civil 3D DWG surfaces** — not read → `not_run` + route (Parsons LandXML/IFC or owner `aicc_export`).

## Roles (DL-3)

Caller assigns `existing` / `design_grading` / `road_top` / `finished_grading`. MIDP-title default allowed; site `surface_roles` overrides. Never from geometry. No mixed-level composites (`MIXED_LEVELS`).

## Stage (DL-4 amended by L-a DL-14)

| At 30 % (PD) | At 60 % (DD) |
|---|---|
| EWK-001, EWK-002 slopes, EWK-003 trapping, EWK-102 → findings | All checks |
| EWK-002 FoS, EWK-003 frontage / 0.30 m, EWK-101 → not at 30 % | FoS, frontage, 0.30 m, EWK-101 |
| MIDP-scheduled models missing → *Missing information* (DL-15) | same |
| Profile always (information) | Profile + candidates |

EWK-102 at 30 %: height ≥ **1.0 m** (DL-18). Other thresholds as DL-5…DL-8.

## Slope grouping (L-b DL-19…DL-23)

- Face margin `m = max(grade_pct, u)` with `u = 100 · δz · P / (2 · A)` and
  `δz = slope_precision_dz_m` (**1 mm**, same for IFC and LandXML).
- **EWK-002:** clearly steep = slope > L + m; possibly steep = slope > L − m (≤ wall);
  groups = possibly-steep faces connected by vertex **coordinates** (1 mm), not point IDs;
  report only groups with ≥1 clearly steep face. Finding text states mean/max slope and H:V.
- **EWK-001:** clearly / possibly flat about `g_min`; report if clearly-flat area ≥ 25 m².
- Format invariance (Amendment L-b §8; DL-21 revised): same surface as IFC vs LandXML → EWK-001 counts within 5 %; EWK-002 counts (all and ≥ 1.0 m) within 6 %; largest ten heights within 1 m (TL11).

## Status (DL-9)

Surface-derived and PDF FoS → *Probable*; absences → *Missing information*; 0.30 m → Observation.

## Limits

- R-Lb2 lifted (owner, 22 Oct 2026 21:17 Asia/Riyadh): QRWN EWK-002 and EWK-102 results are valid for the CRS to Parsons. Do not withhold them. NMAR and NRJS are not covered by this lift. Grouping, thresholds and δz stay as Amendment L-b.
- No earthworks-balance rule (R-L4); volumes are information only.
- "3H:1V preferred" is mentioned in finding text only (no parameter).
- Rules stay inactive in rules_v7 after this build (DL-10); L-a DL-17 schedules rules_v8 after real 30 % tests — not in this PR.
- LibreDWG stays test-only (L-1).
