# Revision comparison methods (skill 7)

## Pairing (M1)

| Level | When | Status |
|-------|------|--------|
| `normalised` | Same key after stripping spaces, single-letter stray fields (e.g. `-G-`), and revision suffix | confirmed |
| `number_map` | `--number-map` CSV `old,new` | confirmed, `renumbered` |
| `title_sheet` | Unique match on title + sheet x of y | **proposed**, Probable |

Unmatched previous / current rows are listed; nothing is paired silently.

## PKG-010 (M2)

Load the rule through skill 8. The method text orders codes within each series
(`0A<0B<…`; `00<01<…`; `A0<A1<…`). Same-number pairs produce a PKG-010 result
(pass / fail / missing_information / incomparable / not_run).

**DM-3:** a renumbered pair (map or proposed) that keeps the same revision while content changed
is recorded as an **Observation**, not under PKG-010.

Revisions are read from the file-name suffix and from the skill-2 title-block `Revision:` field.
(The full revision-history table is not yet read by skill 2.)

## Entity / surface / raster / inventory (M3)

- DWG: SHA-256 of kind + layer_short + rounded vertices — **not** handle.
- Surfaces: plan-position vertex match; ΔZ min / max / mean / rms.
- PDF raster: sheet-coordinate regions; verification status **Probable** (DM-4); parameters in
  `params.json` tagged `[GAP — set from T7-R1]` (DM-9).
- Inventory: bases / formats added or removed between two `package.json` files.

## CRS closure (M4)

Report only (DM-7). See `row_types.md`.
