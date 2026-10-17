# Phase C1 — alignment and junction geometry (`check_geometry.py`)

Spec: AICC-BIM-SPC-0006 Rev H §4.13E (DH-1 … DH-10, owner 17 Oct 2026). Parameters: `road_class_params.json` `c1`.

## Contents
1. Inputs and position source
2. Speed for speed-keyed tables
3. The checks
4. Status, severity and findings
5. Limits

## 1. Inputs and position source

| Input | From | Use |
|---|---|---|
| `setout.json` (PD) or `landxml_network.json` (DD) | `read_setout.py` / `read_landxml_network.py` | curve rows: radius, length, stations, handles; spiral rows |
| `network.json` | `build_network.py` 0.3.0 | junctions with leg bearings 10 m out (`angle_offset_m`), roundabouts, chainage of each location per road |
| `road_class.json` | `read_class.py` | class of each road; a road without a class is not judged |
| `criteria.json` | `read_criteria.py` | stated design speed per class |
| `cross_check.json` | `cross_check.py` | flags `position_uncertain` |
| LandXML ingestion (DD) | skill 3 | design profiles for ROD-037 |

At DD, LandXML is the source (DG-1). Its arcs are checked by `check_alignment.py` in phase A, so the drawn-curve
check of ROD-017 / 018 is not repeated there.

Roundabout circles (all-curve alignments named `RA-…`, K19-4) are left out. They belong to C2 (ROD-039).

**Discontinuous roads:** some roads are table pieces that do not merge into one line. Each location on such a road
gets its chainage from the station of the nearest piece plus the distance along that piece
(`chainage_basis: stations`). A location still without a chainage is listed in `network.json`
`not_positioned` and is not used for spacing.

## 2. Speed for speed-keyed tables (D10-5, DH-6)

The check uses:
- the stated speed of the class (criteria table);
- and, where the stated speed is below the ER class minimum (`vd_min` of ROD-006 / 007 / 009), that minimum as well.

Both results go into one finding. A speed that is not a key of the rule table (e.g. 20 km/h) is not interpolated;
it is listed in `not_run`. Without a stated speed, the ER minimum alone is used.

## 3. The checks

| Rule | What is compared | Notes |
|---|---|---|
| ROD-017 / 018 | radius of each curve vs `r_min` of the class | value as printed, no tolerance; `mode_tag` drawn_curves → its own finding (DH-2) |
| ROD-019 | arc length vs `arc_min` at the speed | |
| ROD-020 | a curve with R ≤ `spiral_upper_radius` at the speed needs a `spiral` row next to it | at or below `spiral_not_required_up_to` (30 km/h) not applied; PD absence reads "no spiral in the setting-out table" (R-H1) |
| ROD-035 | smallest acute angle between arms of different roads vs `angle_range` (75° … 105°) | arm rule below |
| ROD-036 | centre-to-centre chainage between consecutive junctions / roundabouts on a road vs `spacing_local` / `spacing_collector` | `spacing_signalised` not run (R-H4) |
| ROD-037 | steepest design-profile grade within `distance` (30 m) of the junction station vs `g_max` ± tolerance | DD only; vertical curves are taken as a linear grade change |

**Arm rule (DH-3):**
- Two legs of a junction whose bearings are within `through_tolerance_deg` (15°) of a straight line form one through arm. This holds even when the two legs carry different road names (NMAR J051: RD-01 / RD-12).
- A junction where every arm belongs to the same road gives no angle.

**Tangents (DH-8):**
- every tangent row is listed in `tangents`, with the neighbouring curves and whether they turn the same way or in reverse;
- no finding is raised, because no rule exists (R-H2).

## 4. Status, severity and findings

- **Status:** every check carries `link_cap` Probable. The class comes from drawings and decides the limit (DH-4), so this applies even when the geometry is from LandXML.
- **Evidence:**
  - setting-out rows → `dwg_vector`, with the table cell handles and the PC position;
  - junction checks → `script_output`, with the junction position;
  - ROD-037 → `landxml`.
- **Severity:** a check of an **advisory** rule carries `severity_override` Observation (DH-7; ROD-036 in rules_v6).
- **Findings** (`build_candidates.py` 0.5.0) are grouped as follows:
  - ROD-019 / 020 (speed-keyed): one finding per rule and class;
  - ROD-017 / 018 drawn curves: one finding per rule;
  - others: one finding per rule, parameter and class.

## 5. Limits

- Bearings are measured 10 m from the junction centre on the network geometry. A junction whose leg is shorter than 10 m has no bearing for that leg.
- The tables carry no superelevation or design-vehicle data. Those rules are not part of C1.
- Profile stations are read as alignment stations, with the alignment's station equations (as Rev G low points). The first DD package will confirm this (T3).
