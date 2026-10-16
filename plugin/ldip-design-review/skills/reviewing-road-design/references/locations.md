# Phase B2 — location register and presence per location (skill 10)

Contents: 1 Sources of position · 2 Network · 3 Cross-check · 4 Road class (M1–M6) · 5 Decisions per
location · 6 Status · 7 Limits

Spec: AICC-BIM-SPC-0006 Rev F §4.13C (owner approved 16 Oct 2026 00:04; K17-1, K19-1 … 5, RC-1 … 4,
DF-1 … DF-12) and Rev G §4.13D (owner approved 16 Oct 2026 21:18; DG-1 … DG-8). Parameters: `road_class_params.json` (network values [PROPOSED] until tested, K19-3).

## 1 Sources of position (K19-1)

| Stage | Source | Script |
|---|---|---|
| PD | setting-out tables of the setting-out sheets (Civil 3D alignment tables, "ALIGNMENT NAME: …") | `read_setout.py` |
| DD | LandXML alignments by name (skill 3); tables become the cross-check (DG-1) | `read_landxml_network.py` (same network format) |
| fallback | plan centreline line work with label points (skill 1 `--linework`) | phase A route |

`read_landxml_network.py`: lines → tangents; arcs from Start, Center and rot, split into pieces of at most
120°; spirals flattened at 1 m (clothoid from Start toward the PI; a closing error above 0.5 m is reported).

`read_setout.py` finds each table's columns from its header row (DF-4); a header cell that skill 1 could
not decode is placed by its position. Sheets that repeat the same tables are read once (`sheets` lists
them); a table that differs between sheets gives `SETOUT_SHEETS_DIFFER`.

## 2 Network (`build_network.py`)

- Tangents from POB / POE (or PT / PC); curves rebuilt from PC, PI, PT and radius.
- Junction: an alignment end within 1.0 m of another alignment; legs within 15 m are one location.
  Angles between legs are kept for phase C (DF-12).
- Roundabout (K19-4): two half circles through opposite points, or one 360° row whose centre comes from a
  closed plan centreline of the same radius. Alignment ends on the ring or inside it are its approaches.
- Road end types: `gap` (another alignment within 3 m), `site_exit` (site boundary given, or the road
  points to a plan centreline outside the tables within 50 m), else `dead_end`. Without a boundary
  (GIS-011 `site_boundary` [GAP]) an end the line work cannot place stays `dead_end` with
  `exit_checked: false`, and its finding says so.

## 3 Cross-check (`cross_check.py`, K19-2)

Each table segment of 5 m or more with less than 95 % of its length within 0.5 m of the plan centreline
is a disagreement; one `inconsistency` check per road (ROD-001, register). At DD the LandXML network is
compared with the tables (`--setout`) instead of the plan centreline (DG-1).

## 4 Road class (`read_class.py`, RC-1 … RC-4)

| Method | Reads |
|---|---|
| M1 | ROW class layer ("15 m ROW") along ≥ 50 % of the road within 1 m |
| M2 | ROW in the alignment name ("(15m R.O.W)") |
| M3 | site naming code (QRWN: B 36 / C 30 / D 25 / E 20 / F 18 / G 15 m) |
| M4 | legend colour of the hierarchy-plan PDF after fitting it to the network (only a colour map confirmed for the site; legend strip excluded) |
| M5 | ROW boundary lines of the same road-layout reference: per side the lower quartile of 5 cuts per tangent |
| M6 | width → class: criteria-table columns (`row_widths`) and typical-section titles |

Agreement (RC-2): methods that give a width agree → used (`agreement` = number of methods; one method is
named). Disagreement → no width and one `inconsistency` check. A width M6 does not map to one class →
class unknown and one Missing-information finding for all such roads (K23 route: RFI / CRS, M7).

## 5 Decisions per location (`check_locations.py`, DF-6 … DF-9)

1. Phase B1 steps 1–3 apply (inactive rules not run; stage gate; accepted carriers).
2. Hits with a position from the accepted DWG carriers: texts, block names, line-work layer names with the
   B1 terms. Radius 25 m at junctions, 30 m at dead ends and roundabout approaches (DF-6).
3. ROD-101 per road (DF-7): a typical section of the road's ROW width with two sidewalks, or sidewalk
   line work on both sides; roads without a section of their width are listed as not run.
4. ROD-104 at PD: package level only (DB-3); location level from DD.
5. ROD-115 (DF-8): a turning-area term within 30 m, or the ROW at 5 m from the end wider than the road
   by `turning_widening_m`; a declared departure on dead ends is quoted, never cancelling.
6. ROD-102, 103, 105, 109 per junction; ROD-112 per roundabout approach.
7. ROD-107 (DG-2): low points from the LandXML design profiles (`find_low_points.py`): sag curve at
   BVC + (−g_in · L) / (g_out − g_in); a PVI without curve where the grade turns from falling to rising; a
   profile end reached by a falling grade when it is a dead end. Station equations applied. A catch basin /
   gully / inlet within 15 m. Sidewalk low points are not checked (no sidewalk profiles, R-G3).
8. ROD-108 (DG-3): parking areas = closed polylines on parking layers (`parking_area_terms`, site
   `parking_layers`), ≥ 25 m², not inside a larger area; an accessible-parking hit inside or within 3 m. The
   parking schedule is not read (R-G1).
9. ROD-111 (DG-4): calming devices = traffic-calming hits merged within 10 m; a W2-1 / SLOW hit within 30 m.
   No device → not run (not applicable).
10. ROD-113 (DG-5): school / playground plots (`frontage_land_uses` in the DLUS Level 1 / 2) from the
   GIS-005 `plot_label_source` layers; frontage on each local_access road within ROW / 2 + 3 m, ± 30 m; a
   calming device on the frontage. Roads without a class: listed as not run; no `plot_label_source`: not run.
11. One finding per rule with the count and up to ten locations (M4) and what was searched (M5).

## 6 Status

Positions and classes from drawings cap at *Probable* (K19-1); low points from LandXML keep the rule cap
(Rev G §4.13D.8); a road marked by the cross-check is
`position_uncertain` and its findings stay *Probable* (DF-9). A class not stated (M6) is *Missing
information*.

## 7 Limits

- Rev G lists are tested on synthetic DD data only; the 30 % submissions hold no parking layers, calming
  devices, catch basins or school texts (R37: first 60 % submission).
- Profile PVI stations are read as alignment stations; an equation region that does not contain a station
  leaves that low point in `not_positioned`.
- The tables decide which roads exist; a road drawn without a table is not in the register (the
  cross-check reports plan centreline not covered by the tables as a share only).
- Element terms find what is named; an element drawn without a name is not found, and the finding lists
  what was searched.
- ezdwg 0.12.9 dropped some table cells (NMAR 60000: 67 MTEXT); the pin is 0.16.0 since D13-2 (16 Oct 2026).
  A row whose cells are missing is reported as `SETOUT_ROW_UNREAD`.
