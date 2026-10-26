# Revision comparison methods (skill 7 — Rev B §4.4 + Rev M §4.15; S31 R1–R9)

## Pairing (M1 / DM-2 / R1 / R2)

Order **(a) → (b) → (c)**. No silent pairs.

| Level | When | Status |
|-------|------|--------|
| `normalised` | Same key after stripping spaces, single-letter stray fields, revision suffix | confirmed |
| `number_map` | `--number-map` CSV `old,new` — **fixed comma dialect** (R2); `csv.Error` → exit 2 | confirmed, `renumbered` |
| `title_sheet` | Unique title + sheet "x of y" (when text exists) | proposed, Probable |
| `raster_mad` | PDF render → grey → fixed size → mask right title-block strip → MAD cost → **Hungarian one-to-one**; `weak` if margin < `weak_margin` (R1) | proposed, Probable |

Raster params: `references/params.json` tagged `[PROPOSED — DM-9, from T7-R1 probe]` (R9): 25 dpi, 400×283, mask 18 %, weak_margin 1.5.

## PKG-010 (M2 / DM-3 / R3 / R3b)

Load through skill 8. Revision from the **revision table only** (crop title-block strip →
`extract_words`; headers Revision / Description / Date; newest by date else nearest header;
reject `%` / `SHEET-nn` / `nn OF nn`). Source `revision_table` or `not_read`.

**Same-number pairs only** → PKG-010 pass/fail. Renumbered (map / proposed): same rev + changed
content → `observation`; otherwise → `info`. Never PKG-010 pass/fail on renumbered pairs (R3b).

## Change (M3 / DM-4)

Priority: DWG entity hash → surface ΔZ → PDF raster (Probable).

## CRS closure (M4 / R4–R7)

See `row_types.md`. Response **copied only** — never sets outcome (R4). Integer `#` only; stop at sign-off (R7). Deferred phrases (R6). Report only until PKG-014 (DM-7).
