# Revision comparison methods (skill 7 — Rev B §4.4 + Rev M §4.15)

## Pairing (M1 / DM-2)

Order **(a) → (b) → (c)**. No silent pairs.

| Level | When | Status |
|-------|------|--------|
| `normalised` | Same key after stripping spaces, single-letter stray fields (e.g. `-G-`), revision suffix | confirmed |
| `number_map` | `--number-map` CSV `old,new` | confirmed, `renumbered` |
| `title_sheet` / `title_sheet_raster` / `raster_similarity` | Unique best score from title + sheet "x of y" + raster digest overlap | **proposed**, Probable |

## PKG-010 (M2 / DM-3)

Load through skill 8. Method text orders codes within each series (`0A<0B`; `00<01`; `A0<A1`).
Same-number pairs only for PKG-010. Renumbered + same revision + changed content → **Observation**.

Revisions from file-name suffix and skill-2 title-block / revision-table text when supplied
(`title_block_revision`).

## Change (M3 / DM-4)

Priority: **DWG entity hash → surface ΔZ / vertices → PDF raster** (fallback, Probable).
Inventory: deliverables / formats added or removed. Raster params: `params.json` `[GAP — set from T7-R1]` (DM-9).

## CRS closure (M4 / DM-5…DM-8)

See `row_types.md`. Report only until rules_v9 PKG-014 (DM-7).
