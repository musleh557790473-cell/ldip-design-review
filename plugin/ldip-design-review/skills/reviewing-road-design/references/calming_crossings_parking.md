# Phase C3 — calming devices, crossings, refuges and parking (skill 10)

Contents: 1 Inputs · 2 Readers · 3 Checks · 4 Status · 5 Limits

Spec: AICC-BIM-SPC-0006 Rev J §4.13G (owner authorized session 27 build; DJ-5 [AGREED] chicane; DJ-1…4, 6…10 treated as build brief). Parameters: `road_class_params.json` → `c3`.

## 1 Inputs

| Input | Source |
|---|---|
| Detail sheets | skill 1 / 2 ingestion (`details` carrier) → `read_details.py` |
| Plan / marking | skill 1 DWG (+ linework when present) |
| Network, road class | phase B2 |
| Corner returns | phase C2 (`--corner-returns` or `network.corner_returns`) |
| Parking areas / calming hits | B2 lists, or re-read from plan terms |
| DLUS / plots | skill 9 + GIS-005 `plot_label_source` (commercial frontage, counts) |

## 2 Readers

| Script | Output |
|---|---|
| `read_details.py` | `details.json` — title-anchored bands; labelled vs positional values |
| `check_sections.py` 0.3.0 | parking element (ROD-059 at PD); QRWN band-above-title / R.O.W. marks |

## 3 Checks

| Script | Rules |
|---|---|
| `check_calming.py` | ROD-044 (dims + advisory placement), 045, 046, 047 (mini-RB + chicane) |
| `check_pedestrian.py` | ROD-055, 056 |
| `check_parking.py` | ROD-059 (plan), 060, 061, 062 |

### Parking plan bays (G-1 / Rev J §4.13G.9)

- **Layers:** only closed polygons on per-site `parking_layers` in `road_class_params.json` (same style as C2 `curb_layers`, R-I3). Regex fallbacks on layer names are not used.
- **Missing list:** if `parking_layers` is empty / unset for the site → the plan is **not** read for bays; ROD-059…062 report `not_run` with reason `no parking_layers list for site <SITE> (G-1 / R-J3)`. Owner must supply NMAR / QRWN / NRJS names — **do not guess** (R-J3 GAP).
- **Candidate gates** (before ROD size checks; not rule activation):
  - area `c3.bay_area_m2` default **[10, 40] m²** (Rev J §4.13G.9);
  - width `c3.bay_width_m` default **[2.0, 6.0] m** and length `c3.bay_length_m` default **[4.5, 7.0] m** — bracket ROD-059/060/061 tables (parallel 2.5×6.0 … accessible angle 5.45×6.35) with headroom for undersize fail cases.
- Network `c3_bays` / `c3_parking_areas` remain available for synthetic harnesses when layers are unset.
- ROD-059…062 stay **inactive** in the rulebook until the 60 % package (DJ-9); this scoping only stops false positives.

## 4 Status

- Drawing positions / classes → *Probable*
- Positional detail values → *Probable*
- DD device without a detail of its type → *Missing information*
- Advisory placement, tier-2, ER ambiguity → Observation (`tier2_*` parameters)
- Surface gradient / bay slope → phase D (`check_surfaces.py`; see `surfaces_phase_d.md`)

## 5 Limits

- No C3 element at 30 % on the three sites → synthetic DD tests; TJ8 synthetic PD harness always runs; real TJ8 when `LDIP_REVIEW` is set
- ROD-044 grade is read from LandXML design-profile tangents / PVIs at hump chainage (optional `hump_grades` hook only as fallback)
- ROD-047 chicane: curb lateral-shift measurement is primary; `chicane_shift_m` is fallback when no curb samples
- ROD-047 stays inactive in the rulebook until first 60 % chicane confirm (R-J1); built and tested on synthetic data. C3 rules remain inactive when C3+D code ships (DJ-9); activation ≠ packaging (DK-5)
- Parking schedule format unknown (R-J2) → plan counts only
- Parking / marking layer names in DD packages unknown (R-J3) → `parking_layers` empty per site until owner supplies them (G-1)
- Bridge clearance for humps not run (R-J4)
- C1/C2 scripts are not in this repository checkout; C3 accepts C2 corner returns as a JSON input
- Detail *box* bands (when sheets use boxed details) are not yet separate from the column-above-title band
