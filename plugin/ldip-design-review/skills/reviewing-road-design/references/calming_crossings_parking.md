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

## 4 Status

- Drawing positions / classes → *Probable*
- Positional detail values → *Probable*
- DD device without a detail of its type → *Missing information*
- Advisory placement, tier-2, ER ambiguity → Observation (`tier2_*` parameters)
- Surface gradient / bay slope → not run (phase D)

## 5 Limits

- No C3 element at 30 % on the three sites → synthetic DD tests; real TJ8 = not run at PD
- ROD-047 stays inactive in the rulebook until first 60 % chicane confirm (R-J1); built and tested on synthetic data
- Parking schedule format unknown (R-J2) → plan counts only
- Bridge clearance for humps not run (R-J4)
- C1/C2 scripts are not in this repository checkout; C3 accepts C2 corner returns as a JSON input
