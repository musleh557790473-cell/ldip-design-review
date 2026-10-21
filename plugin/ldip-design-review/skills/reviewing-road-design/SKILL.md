---
name: reviewing-road-design
description: >-
  LDIP design review (AICC JV): checks Parsons road design against the LDIP
  Rulebook road rules (ROD): road classes and design speeds, the design
  criteria stated in the design report, typical-section widths (right of
  way, lanes, medians, sidewalks, crossfall) and horizontal and vertical
  alignment (radii, grades, K values, vertical curve lengths) from LandXML,
  the presence of required road elements (sidewalks, crossings, ramps,
  markings, signs, parking lanes and others), the drawn plan geometry of
  the setting-out tables (curve radii and lengths, spirals, junction
  angles and spacing, grade near junctions), junction and roundabout
  layout from the curb line work (corner radii, design vehicle,
  roundabout diameter, entry width, splitter islands, apron), calming /
  crossings / parking dimensions (phase C3), and raises missing road
  information by design stage. Use for any LDIP road geometry, road
  design-criteria or missing road element check.
metadata:
  version: "0.10.0"
  spec: "AICC-BIM-SPC-0006 Rev D §4.13 (phase A), Rev E §4.13B (phase B1), Rev F §4.13C (phase B2) and §4.13.6 (K24), Rev G §4.13D (phase B2 part 2), Rev H §4.13E (phase C1), Rev I §4.13F (phase C2, K25), Rev J §4.13G (phase C3), §4.13H (phase D first slice); AICC-BIM-SPC-0004 Rev E §5.8-5.14, §7A, §7B — packaged in plugin / skill 0.10.0 (SPC-0001 Rev K / DJ-10 / DK-5); C3 and D rules are NOT activated until the 60 % package (R37 / DJ-9); no rules_v8"
---

# Reviewing road design (phases A, B1, B2, C1, C2, C3 and D surface slice)

This skill compares what Parsons states and draws for its roads with the road rules of the approved
rulebook and writes candidate findings (agent code `RDE`). It reads only ingestion files written by skills
1, 2 and 3 (`*.dwg.json`, `*.pdf.json`, `*.xml.json`); it never opens a DWG, PDF or XML itself. The
scripts do every check. Run them in order, read their one-line JSON results and report. Do not re-do a
check by hand, do not read the scripts' source and never edit a result file.

Phase A covers: road classes and design speeds (ROD-001, 006-009), the design-criteria table of the
design report, typical-section widths and crossfall from DWG text (ROD-002-004, 010-014, 016, 021, 048
minimum, 057 height), LandXML alignment and profile (ROD-017, 018, 024-026, 028-030) and the required
inputs C1-C4 by stage. Phase B1 covers the presence rules ROD-101-116 at package and typical-section
level: is each required element shown anywhere in its accepted carriers for the stage (sidewalks,
crossings, curb ramps, tactile tiles, sight triangles, swept paths, catch basins, accessible bays, street
name signs, markings, calming signs, splitter islands, calming devices, bus stops, turning areas, parking
lanes). Phase B2 builds the location register — the named roads of the setting-out tables, junctions,
roundabouts and road ends, each road's ROW width and class by up to five methods — and checks the
presence rules per location (per road, junction, roundabout approach and dead end). Phase C1 checks the
drawn alignment and junction geometry on the same network: curve radii (ROD-017 / 018, apart from the
stated-criteria check of phase A), arc lengths (ROD-019), spirals (ROD-020), junction angles (ROD-035),
junction spacing (ROD-036) and, at DD, the grade near junctions (ROD-037); tangents are listed as
information. Phase C2 checks junction and roundabout layout from the curb line work of the plan: corner
(curb-return) radii by junction class and the stated design vehicle (ROD-032), roundabout inscribed
diameter (ROD-039), entry width and splitter islands where islands are drawn (ROD-040, 042) and the apron
(ROD-043); at DD also driveway returns (ROD-033) and sight triangles (ROD-034). ROD-041 (entry deflection)
is not run. Phase C3 (Rev J §4.13G) checks calming-device dimensions and placement, mini-roundabouts and
chicanes, crosswalk and refuge sizes, and parking-bay dimensions and accessible counts (synthetic DD until
the first 60 % package). C2 layout may supply corner returns as an input JSON to C3. Phase D
(§4.13H first slice) checks surface gradient (ROD-056), accessible-bay slope (ROD-062) and roundabout
circulatory crossfall/drainage scaffolding (ROD-043) on the LandXML design TIN; apron is out of D;
rules stay inactive.

Packages: Python 3.11+, `jsonschema`, `pypdfium2` and `Pillow` (evidence crops, as skill 2); phase B2 also
`shapely`, `numpy` and `scipy`.

## Hard rules

1. Get the rules only through `applying-ldip-rulebook` (`get_rules.py --agent RDE`). Every value,
   tolerance, applicability and cap comes from that file. An inactive rule never runs; it is listed in
   `rules_skipped`.
2. The stage name is given by the owner per run (e.g. `30% Concept Design`, `60%`). Never guess it.
3. A road class comes only from a Parsons class term in the fixed synonym list (`references/road_checks.md`
   §2). An unknown term gives no class: report it, do not choose one.
4. A value is read as printed. If two columns look swapped or a value looks wrong, the rule check decides;
   never correct a value.
5. An absence is a finding only after the search script has searched every accepted carrier; the finding
   lists what was searched. A design-report statement counts as presence only where the rule accepts the
   report for the stage (PD). Never decide presence by looking at a drawing yourself.
6. Findings are candidates for the Verifier. Never raise a status above what `build_candidates.py` sets,
   and never write a finding by hand.
7. Phase B2: a road's class comes only from `read_class.py` (methods M1–M6 and the agreement rule); when
   the methods disagree the road has no class — never pick one. Positions and classes from drawings stay
   *Probable*. Site parameters live in `references/road_class_params.json`; never edit them in a run.

## Procedure (plan → rules → read → check → findings → validate)

Run each command from the LDIP_Review folder with paths relative to it. `<SK>` is
`${CLAUDE_PLUGIN_ROOT}/skills/reviewing-road-design/scripts`, `<SK8>` is
`${CLAUDE_PLUGIN_ROOT}/skills/applying-ldip-rulebook/scripts`, `<EV>` the output folder (standalone: the
folder the owner names). Inputs are the ingestion files the owner or the Orchestrator lists: the design
report `<R>.pdf.json`, the road hierarchy plan `<H>.dwg.json`, the typical sections `<T>.dwg.json`, and any
LandXML `<X>.xml.json`.

1. **Rules** — `python "<SK8>/get_rules.py" --rulebook rulebook/rules_v<N>.json --agent RDE --out "<EV>/rules_RDE.json"`
2. **Criteria** — `python "<SK>/read_criteria.py" --ingestion "<R>.pdf.json" --out "<EV>/criteria.json"`
3. **Register** — `python "<SK>/build_register.py" --criteria "<EV>/criteria.json" --rules "<EV>/rules_RDE.json" --hierarchy "<H>.dwg.json" --sections "<T>.dwg.json" --landxml "<X>.xml.json" --out "<EV>/road_register.json"`
   (omit an option whose files do not exist).
4. **Checks** —
   `python "<SK>/check_criteria.py" --criteria "<EV>/criteria.json" --rules "<EV>/rules_RDE.json" --out "<EV>/criteria_checks.json"`
   `python "<SK>/check_sections.py" --sections "<T>.dwg.json" --rules "<EV>/rules_RDE.json" --out "<EV>/section_checks.json"`
   `python "<SK>/check_alignment.py" --register "<EV>/road_register.json" --landxml "<X>.xml.json" --rules "<EV>/rules_RDE.json" --out "<EV>/alignment_checks.json"`
5. **Presence (phase B1)** — give every ingestion file once with its carrier type (`typical_section`,
   `plan_geometry`, `road_hierarchy_plan`, `details`, `signing_marking_plan`, `drainage_plan`,
   `landscape_plan`, `parking_schedule`, `design_report`), as the owner or the drawing titles name it:
   `python "<SK>/find_presence.py" --file design_report="<R>.pdf.json" --file typical_section="<T>.dwg.json" --file plan_geometry="<P>.dwg.json" --out "<EV>/presence.json"`
   `python "<SK>/check_presence.py" --presence "<EV>/presence.json" --rules "<EV>/rules_RDE.json" --stage "<stage name>" --sections "<EV>/section_checks.json" [--land-uses "<DLUS land use>" ...] --out "<EV>/presence_checks.json"`
   (`--land-uses`: the distinct land uses of the DLUS, for ROD-113; omit when no DLUS is given).
5a. **Location register (phase B2)** — inputs: the setting-out sheets `<S…>.dwg.json`, the road
   hierarchy plan `<H>.dwg.json` with its line work (skill 1 `--linework`) and, for M4, its PDF
   `<H>.pdf.json` with paths (skill 2 `--paths`). `<SITE>` is QRWN, NMAR or NRJS.
   `python "<SK>/read_setout.py" --ingestion "<S1>.dwg.json" "<S2>.dwg.json" --out "<EV>/setout.json"`
   `python "<SK>/build_network.py" --setout "<EV>/setout.json" --site <SITE> --linework "<H>.dwg.json" [--site-boundary "<boundary.geojson>"] --out "<EV>/network.json"`
   `python "<SK>/cross_check.py" --network "<EV>/network.json" --site <SITE> --linework "<H>.dwg.json" --out "<EV>/cross_check.json"`
   `python "<SK>/read_class.py" --network "<EV>/network.json" --site <SITE> --linework "<H>.dwg.json" [--pdf "<H>.pdf.json"] --criteria "<EV>/criteria.json" --sections "<EV>/section_checks.json" --out "<EV>/road_class.json"`
   At DD with LandXML (DG-1), the LandXML alignments are the position source and the tables the cross-check:
   `python "<SK>/read_landxml_network.py" --landxml "<X>.xml.json" --out "<EV>/landxml_network.json"`, then
   `build_network.py` with `--setout "<EV>/landxml_network.json"`, and `cross_check.py` with
   `--setout "<EV>/setout.json"` instead of `--linework` (when there are no tables, with `--linework`).
   Then run step 3 again with `--network "<EV>/network.json" --class "<EV>/road_class.json"` added.
5b. **Presence per location (phase B2)** — the DWGs with their carrier type, as in step 5:
   `python "<SK>/check_locations.py" --network "<EV>/network.json" --class "<EV>/road_class.json" --rules "<EV>/rules_RDE.json" --stage "<stage name>" --file plan_geometry="<P>.dwg.json" --sections "<EV>/section_checks.json" --criteria "<EV>/criteria.json" --cross-check "<EV>/cross_check.json" --out "<EV>/location_checks.json"`
   At DD add the remaining location lists (Rev G): low points
   `python "<SK>/find_low_points.py" --landxml "<X>.xml.json" --network "<EV>/network.json" --out "<EV>/low_points.json"`
   and give `check_locations.py` also `--file drainage_plan="<D>.dwg.json" --file signing_marking_plan="<G>.dwg.json"
   --low-points "<EV>/low_points.json" --plots "<CAD>.dwg.json" --dlus "<EV>/dlus.json" --plot-label-source "<EV>/rules_GIS.json"`
   (the cadastral drawing, the DLUS of skill 9 `read_dlus.py` and the GIS rules file holding GIS-005
   `plot_label_source`; omit what is not given — the rule then says why it did not run).
5c. **Plan geometry (phase C1)** — on the same network and position source as 5a:
   `python "<SK>/check_geometry.py" --setout "<EV>/setout.json" --network "<EV>/network.json" --class "<EV>/road_class.json" --rules "<EV>/rules_RDE.json" --stage "<stage name>" --criteria "<EV>/criteria.json" --cross-check "<EV>/cross_check.json" --out "<EV>/geometry_checks.json"`
   At DD use `--setout "<EV>/landxml_network.json"` and add `--landxml "<X>.xml.json"` (design profiles, ROD-037).
5d. **Junction and roundabout layout (phase C2)** — on the same network, with the plan DWGs (and their line
   work) as `plan_geometry`; at DD also the landscape plan:
   `python "<SK>/check_layout.py" --network "<EV>/network.json" --class "<EV>/road_class.json" --rules "<EV>/rules_RDE.json" --stage "<stage name>" --file plan_geometry="<P>.dwg.json" [--file landscape_plan="<L>.dwg.json"] --criteria "<EV>/criteria.json" --out "<EV>/layout_checks.json"`
   The curb layers of the site come from `references/road_class_params.json` (`curb_layers`); a site
   without them gives "curb layer not set" — report it, do not name layers yourself.
5e. **Calming / crossings / parking (phase C3)** — detail sheets, marking plans and parking layout at DD:
   `python "<SK>/read_details.py" --file details="<D>.dwg.json" --out "<EV>/details.json"`
   `python "<SK>/check_calming.py" --details "<EV>/details.json" --network "<EV>/network.json" --class "<EV>/road_class.json" --rules "<EV>/rules_RDE.json" --stage "<stage name>" --site <SITE> --file plan_geometry="<P>.dwg.json" --file details="<D>.dwg.json" [--landxml "<X>.xml.json"] [--criteria "<EV>/criteria.json"] --out "<EV>/calming_checks.json"`
   `python "<SK>/check_pedestrian.py" --network "<EV>/network.json" --rules "<EV>/rules_RDE.json" --stage "<stage name>" --site <SITE> --file signing_marking_plan="<G>.dwg.json" [--corner-returns "<EV>/corner_returns.json"] [--class "<EV>/road_class.json"] [--plots "<CAD>.dwg.json" --dlus "<EV>/dlus.json" --plot-label-source "<EV>/rules_GIS.json"] --out "<EV>/pedestrian_checks.json"`
   `python "<SK>/check_parking.py" --network "<EV>/network.json" --class "<EV>/road_class.json" --rules "<EV>/rules_RDE.json" --stage "<stage name>" --site <SITE> --file plan_geometry="<P>.dwg.json" [--plots "<CAD>.dwg.json" --dlus "<EV>/dlus.json" --plot-label-source "<EV>/rules_GIS.json"] --out "<EV>/parking_checks.json"`
   (At PD, C3 DD rules are listed not run; typical-section parking is still covered by step 4 `check_sections.py`.)
5f. **Surface gradient / slope / roundabout crossfall-drainage (phase D first slice)** — LandXML design TIN over C3-accepted polygons (§4.13H / DK-1…DK-4). Packaged in skill / plugin **0.10.0** with C3 (SPC-0001 Rev K / DJ-10 / DK-5); **C3 and D rules are NOT activated** until the 60 % package (R37 / DJ-9); no `rules_v8`:
   `python "<SK>/check_surfaces.py" --network "<EV>/network.json" --rules "<EV>/rules_RDE.json" --stage "<stage name>" --site <SITE> --landxml "<X>.xml.json" [--surface-index N] [--elements "<EV>/surface_elements.json"] [--file drainage_plan="<D>.dwg.json"] [--rulebook "<rulebook/rules_v7.json>"] --out "<EV>/surface_checks.json"`
   (Or pass `--surface "<stem>.surface-1.json"` instead of `--landxml`. **Multi-surface:** sole `content.surfaces[]` entry is the default; when several exist, pass `--surface-index` — no FG/name preference invented. Refuge / accessible-bay polygons from `network.c3_refuges` / `c3_accessible_bays` or `--elements`. ROD-043 footprint = **circulatory** via `c3_crossfall_footprints` / `--elements` (`kind=apron` ignored); do not invent a ring from `roundabouts` centre+radius. ROD-043 numbers from the RDE rule object or `--rulebook` / `LDIP_RULEBOOK` — full `rules_v*.json` stays out of Git; CI uses **synthetic** `tests/fixtures/rod043_excerpt.json` (not deposited rulebook numbers). Missing params or circulatory footprint → not_run. Loading params does **not** activate inactive ROD-043. Apron is out of D.)
   Without setting-out tables or LandXML alignments, skip 5a–5f and report it in `gaps`.
6. **Required inputs** — `python "<SK>/check_required.py" --stage "<stage name>" --rules "<EV>/rules_RDE.json" --register "<EV>/road_register.json" --criteria "<EV>/criteria.json" --sections "<EV>/section_checks.json" --landxml "<X>.xml.json" --out "<EV>/required_inputs.json"`
7. **Findings** — `python "<SK>/build_candidates.py" --results "<EV>/criteria_checks.json" "<EV>/section_checks.json" "<EV>/alignment_checks.json" "<EV>/road_register.json" "<EV>/required_inputs.json" "<EV>/presence_checks.json" "<EV>/cross_check.json" "<EV>/road_class.json" "<EV>/location_checks.json" "<EV>/geometry_checks.json" "<EV>/layout_checks.json" "<EV>/calming_checks.json" "<EV>/pedestrian_checks.json" "<EV>/parking_checks.json" "<EV>/surface_checks.json" --rules "<EV>/rules_RDE.json" --run <run-id> --site <SITE> --sub <sub-id> --evidence-dir "<EV>/evidence" --out "<EV>/candidates.json"`
   (list only the result files that exist).
8. **Validate** — `python "<SK>/validate_findings.py" --findings "<EV>/candidates.json" --rules "<EV>/rules_RDE.json"`
   Exit 4 lists the problems; do not edit the file.

Standalone output is a draft: it does not pass the guard or the run log, and is checked with skill 18
before use.

## Outputs and exit codes

`criteria.json`, `road_register.json`, `criteria_checks.json`, `section_checks.json`, `alignment_checks.json`,
`required_inputs.json`, `presence.json`, `presence_checks.json`, `setout.json`, `network.json`,
`cross_check.json`, `road_class.json`, `landxml_network.json`, `low_points.json`, `location_checks.json`,
`geometry_checks.json` (with the tangent list), `layout_checks.json` (with the corner-radius histogram and
the roundabout table), `details.json`, `calming_checks.json`, `pedestrian_checks.json`, `parking_checks.json`,
`surface_checks.json`, `candidates.json` and PNG crops of the PDF
evidence. Every script prints one JSON line.
Exit codes (SPC-0006 §2.3.3): 0 done · 2 input error · 3 missing package · 4 schema failure.

What each check reads, the class synonyms, how section values are assigned, status caps and the limits of
phase A: `references/road_checks.md`. The presence terms, stage and condition logic of phase B1:
`references/presence_terms.md`. The location register: `references/locations.md`. Phase C1 (arm rule, speeds,
spacing, limits): `references/geometry.md`. Phase C2 (arc fit, corners, roundabouts, limits):
`references/layout.md`. Phase C3 calming, crossings and parking: `references/calming_crossings_parking.md`.
Phase D surface slice: `references/surfaces_phase_d.md`. The finding skeleton: `templates/finding.json`.
