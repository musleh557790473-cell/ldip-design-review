# Utility-corridor methods (skill 14)

SPC-0006 Rev O §4.17. Binding decisions DO-1…DO-8. S33 PR #21 fixes R1–R8 / M1–M3.

## Reader (`read_corridor_sections.py`)

1. **DWG raw route (primary, DO-2 / G-O1).** Band INSERTs via `ezdwg.raw.decode_insert_entities` (pinned `ezdwg==0.16.0`), owner + layer handles. Nested INSERTs inside bound-xref blocks that `to_dxf` drops are kept. Do **not** use `to_dxf` for band geometry.
2. **Band recognition (R5).** An INSERT is a band only if (a) its block contains exactly one LWPOLYLINE of width 1 at local x = −0.5 and (b) its layer maps in `utility_layers.json` (utility or tree / combined).
3. Band interval = `[x − sx, x]`; centre = `x − sx/2`. Layer short name after last `$`.
4. **CL / curb (R2).** `LIMIT OF WORK` and `Center line` are LWPOLYLINE (plus LINE fallback). Per section: ROW = outer band edges; CL = mid-ROW cross-checked with Center line inside the span (≤ 0.05 m); curb face = LIMIT OF WORK at the outer edge of the driving lanes (lane dims); 0.30 m pairs are a cross-check only. Missing curb → `offset_to_curb` null.
5. **Sections (R3).** Group bands by insert y; split at x-gaps > 3 m; attach nearest label matching `^[AB]\d+(-\d+)?$`. Two sections may share one y. **No same-utility merge (R4)** — each band is its own row.
6. **Nested transform (R6).** Parent chain kept; warn when a parent block is inserted more than once.
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
