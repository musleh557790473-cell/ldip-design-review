# Utility-corridor methods (skill 14)

SPC-0006 Rev O §4.17. Binding decisions DO-1…DO-8.

## Reader (`read_corridor_sections.py`)

1. **DWG raw route (primary, DO-2 / G-O1).** Band INSERTs are read with `ezdwg.raw.decode_insert_entities` (pinned `ezdwg==0.16.0`), plus owner and layer handles. Nested INSERTs inside bound-xref blocks that `to_dxf` drops are kept. Do **not** use `to_dxf` for band geometry.
2. Band interval = `[x − sx, x]` on the insert; centre = `x − sx/2`. Layer (after last `$`) maps through `utility_layers.json`.
3. Curb face = vertical `LIMIT OF WORK` line at the outer edge of the driving lanes (lane-dimension 0.30 m pairs as a cross-check). CL / ROW from `Center line`. Section label from `DWG_TITLE`.
4. **PDF fallback.** Filled vector stripes matched to the sheet legend colours (`pdfplumber`). Metres from the ROW dimension. Statuses capped at **Probable**. Only when the DWG cannot be read.

## Checks (`check_corridors.py`)

| Check | Rule (report-only until DO-4) | Notes |
|---|---|---|
| C1 | COR-101 presence | Required utilities per section; `missing_information`, Minor |
| C2 | COR-001 offsets | Information rows until R-O5 — **no value findings** (DO-5) |
| C3 | COR-001 side rule | Dry not on the same side of CL as wet; `design_error`, Probable, Major |
| C4 | COR-002 horizontal | Spacing table; `< 1.0 m` → *Not auto-verifiable* at PD (DO-6) |
| C5 | Plan vs band | Offset ≤ band half-width + 0.5 m (DO-7); `inconsistency`, Probable, Minor |
| C6 | Section vs plan | Coverage / duplicates → Observation |

Findings carry `report_only: true` and `reference.rule_id: null` (DO-4). Agent code `COO`.
