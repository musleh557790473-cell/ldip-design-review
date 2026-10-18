# Phase C2 — junction and roundabout layout (SPC-0006 Rev I §4.13F)

## Contents
1. Inputs
2. Arcs of the curb line work
3. Corner returns and design vehicle (ROD-032)
4. Roundabouts (ROD-039, 040, 042, 043)
5. Driveways and sight triangles (ROD-033, 034; DD)
6. Status, severity and findings
7. Limits

## 1. Inputs

- `network.json` (phase B2): junction centres and roads; roundabouts with centre, setting-out radius and approach roads.
- `road_class.json`: the class of each road (methods M1–M6).
- `criteria.json` (optional): the design-vehicle row of the criteria table and the declared departures (`DEP-nn`).
- `--file plan_geometry=<P>.dwg.json`: the road plan with its skill 1 line work (`--linework`). At DD also `--file landscape_plan=<L>.dwg.json`.
- Site parameters in `road_class_params.json`:
  - `curb_layers`: NMAR `__Curb`; QRWN `-RD_Edges`; NRJS `RD_CURB_RETURN`, `RD_Edge`;
  - `plot_layers`: used for driveway frontages;
  - `c2`: the values below. Keys listed in `_impl` were set in the build and are not named in the spec.

## 2. Arcs of the curb line work

- **Where arcs come from:**
  - ARC and CIRCLE records;
  - runs of consecutive polyline segments shorter than `seg_max_m` (3 m) that all turn the same way. Skill 1 flattens bulges into such runs.
- **Fit:** each run gets a least-squares circle. It is accepted when the largest residual is ≤ `arc_fit_tol` × R (5 %).
- **Dropped or merged:**
  - runs shorter than `arc_min_len_m` (2 m), which are fit slivers at transitions;
  - a curve drawn twice, or drawn on two curb layers (curb face and edge line with the same centre and mid point, radii within 0.5 m), counts once. The first listed layer wins.

## 3. Corner returns and design vehicle (ROD-032)

- **What counts as a corner:** an arc that is not a full circle, with:
  - `corner_r_min_m` (3) ≤ R < `corner_r_max_m` (30); smaller arcs are median and island noses;
  - sweep within `corner_sweep_deg` (45°–135°);
  - not concentric with a roundabout;
  - mid point within `corner_search_m` (25 m) of a junction centre.

  It belongs to the nearest junction only.
- **Range (DI-2 a):** the highest class of the roads meeting at the junction sets `corner_radius`.
  - A junction with a road without a class is not judged (K23).
  - The radius is rounded to 0.1 m and has no tolerance.
- **Output:** the corner-radius histogram per class is written to `corners`.
- **Design vehicle:**
  - each stated design vehicle (per class) is compared with "WB-15 or city bus";
  - a departure whose text names the design vehicle, WB-15 or curb returns is quoted in the finding. It never cancels the finding (as DF-8);
  - not stated → not run at PD; at DD it is *Missing information* (C5).
- **Swept path:** left to ROD-106.

## 4. Roundabouts (ROD-039, 040, 042, 043)

- **Centre:** from the network.
- **Concentric pieces:** arcs whose centre lies within `concentric_m` (1 m) of it and whose R < `ring_max_m` (30). Edges of splitter islands are excluded.
  - **Central island:** the smallest full circle or closed ring.
  - **Outer edge:** the mean radius of the pieces within `ring_tol_m` (0.3 m) of the largest radius.
  - No outer piece → the setting-out radius is used and the check says so.
- **Junction type (DI-3):**
  - C-C: every approach is a collector;
  - L-L: every approach is local;
  - C-L: otherwise;
  - an approach without a class → not run.
- **ROD-039:** ICD = 2 × the outer radius, rounded to 0.1 m.
- **Approach legs:** where each approach road crosses a circle 10 m outside the outer edge. QRWN draws approach centrelines to the centre, so the B2 approach point can be the centre itself. A road crossing twice gives two legs.
- **ROD-042 splitter islands (DI-4):**
  - closed curb outlines with area < `island_max_area_m2` (200), centroid within `island_search_m` (40 m) of the centre and outside the central island;
  - bearing within `island_bearing_deg` (30°) of a leg;
  - width and length are the sides of the minimum rotated rectangle;
  - `length_desirable` (advisory) is checked only when `length_min` passes.
  - A leg without an island is listed in `approaches_without_island`. It is a matter for ROD-112 (DD), not ROD-042.
- **ROD-040 entry width (DI-5):**
  - only on a leg with an island;
  - measured as the shortest distance from the island to the curb line on the entry side, i.e. to the right of an entering vehicle (right-hand traffic), within `entry_band_m` (15 m) outside the outer edge.
- **ROD-043 apron (DI-6):**
  - a concentric ring between the island and the outer edge; width = ring radius − island radius;
  - not drawn → not run at PD; *Missing information* at DD;
  - crossfall and drainage grade are phase D.

## 5. Driveways and sight triangles (ROD-033, 034; DD)

- **Before DD:** not run ("not required at stage").
- **Driveway returns:**
  - curb arcs with R ≤ `driveway_r_max_m` (8) and sweep 45°–135°;
  - more than 25 m from every junction and more than `ring_max_m` + 10 m from every roundabout;
  - within `driveway_frontage_m` (5 m) of a plot outline on the site `plot_layers`. Without plot layers this condition is skipped and the extract says so;
  - the class of the nearest road sets `driveway_radius`.
- **Sight triangles:**
  - legs along the two tangents from the tangent intersection point of each corner (10 m) and driveway return (5 m);
  - obstructions are blocks, and line work of the plan and landscape carriers, whose layer or block name holds an `obstruction_terms` word;
  - a triangle that holds one is listed.

## 6. Status, severity and findings

- **Status:**
  - every geometric check carries `link_cap` Probable (class from drawings, DH-4);
  - the DD absences (design vehicle, apron) carry `status_override` *Missing information* and `issue_type` missing_information.
- **Evidence:**
  - curb arcs and islands → `dwg_vector`, with the handles and position;
  - the design vehicle → `pdf_vector_text`, with the criteria cell (crop).
- **Findings** (`build_candidates.py` 0.6.0): one finding per rule, parameter and class or junction type, with the count and up to ten items.
- **Severity:** the advisory splitter-island length is Observation. A check of an advisory rule is Observation (ROD-040 in rules_v7).

## 7. Limits

- Entry deflection (ROD-041) is not run: the measuring method is a [GAP] (R-I1).
- Entry width is measured only where a splitter island bounds the entry.
- Arc detection depends on the curb layer names of the site (R-I3). A new site or a renamed layer needs the parameter.
- The design vehicle is read from the criteria table only. A vehicle named only in the report text is not read.
