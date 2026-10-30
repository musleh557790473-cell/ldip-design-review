# Utility-corridor methods (skill 14)

SPC-0006 Rev O §4.17. Binding decisions DO-1…DO-8. S33 PR #21 fixes R1–R8 / M1–M3, F1–F3 / N1–N2, and G1–G3.

## Reader (`read_corridor_sections.py`)

1. **DWG raw route (primary, DO-2 / G-O1).** Band INSERTs via `ezdwg.raw.decode_insert_entities` (pinned `ezdwg==0.16.0`), owner + layer handles. Nested INSERTs inside bound-xref blocks that `to_dxf` drops are kept. Do **not** use `to_dxf` for band geometry.
2. **Band recognition (R5 / F1 / F2).** Key on **block-header handle**, not name (names repeat: `*U1`, parent sheets). Band block = header handle whose own entities contain exactly one LWPOLYLINE of width 1 at local x = −0.5 — including anonymous `*U*` blocks. INSERT target from `decode_insert_write_properties` / `decode_block_entity_name_maps`. Layer must still map in `utility_layers.json`.
3. Band interval = `[x − sx, x]`; centre = `x − sx/2`. Layer short name after last `$`.
4. **CL / curb (R2 / F3 / N1 / G3).** `LIMIT OF WORK` and `Center line` are LWPOLYLINE (plus LINE fallback), filtered to the section x-span and y-band. Per section: ROW = outer band edges; CL = mid-ROW cross-checked with Center line inside the span (≤ 0.05 m). Curb = **lane chain**: start at CL, or at the **carriageway-facing** edge of a `median_face_pair_m` (0.40 m) median curb pair when present; walk outward through LIMIT OF WORK lines spaced `params.curb.lane_width_m` [3.0, 3.65]; last line is the curb face. Lane dims confirm / fallback (`curb_method`: `lane_chain` / `lane_dims` / `none`; `curb_chain_left` / `curb_chain_right` in `sections.json`). Missing curb → `offset_to_curb` null, C2 `not_run`.
5. **Sections (R3 / G2).** Group bands by insert y; split at x-gaps > 3 m; attach labels matching `^[AB]\d+(-\d+)?$` by **global closest-first** assignment: label x in section span ±2 m and label 0–8 m below the section bottom. Two sections may share one y. **No same-utility merge (R4)** — each band is its own row. Band span must contain its Center line within ±1 m; otherwise warn and skip (F2).
6. **Nested transform (R6 / F2 / G1).** Parent = the INSERT that **targets** the owner header handle (not name). Validate the owner chain: on a repeated header, drop parents after the first visit and warn `OWNER_CYCLE`; stop at `*Model_Space` / `*Paper_Space` or a non-header owner. If the target cannot be resolved by handle: band coords treated as world, parents (0,0) scale 1, with a warning. Warn when a parent header is inserted more than once.
7. **PDF fallback (R8).** Stripe clusters; metres = ROW dim text ÷ stripe span; legend colours from the sheet at run time. Statuses capped at **Probable**.

## Checks (`check_corridors.py`)

| Check | Rule (report-only until DO-4) | Notes |
|---|---|---|
| C1 | COR-101 presence | Required utilities; `storm_cb`→storm; tree `c1_as` for SL / irrigation secondary |
| C2 | COR-001 offsets | Information until R-O5; **not_run** when curb missing (R2) |
| C3 | COR-001 side rule | Per-band classes per side; tree bands excluded |
| C4 | COR-002 horizontal | Per-band pairs; tree excluded; `< 1.0 m` → *Not auto-verifiable* (DO-6) |
| C5 | Plan vs band | Offset ≤ half-width + 0.5 m (DO-7 AGREED) |
| C6 | Section vs plan | Plan cuts from raw TEXT / MTEXT / ATTRIB (`--plan-dwg`, M3) |

Findings carry `report_only: true` and `reference.rule_id: null` (DO-4). Agent code `COO`.
