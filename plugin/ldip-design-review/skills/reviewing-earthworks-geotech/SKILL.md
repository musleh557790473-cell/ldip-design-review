---
name: reviewing-earthworks-geotech
description: >-
  LDIP design review (AICC JV): checks Parsons earthworks and grading against
  the LDIP Rulebook earthworks rules (EWK): minimum gradient of unpaved areas,
  cut and fill slopes against 2H:1V and the stated factors of safety, water
  trapping on plots, and the presence of slope protection and retaining
  structures, using design and existing surfaces from LandXML or IFC; reports
  cut / fill areas and volumes as information. Use for any LDIP grading,
  earthworks, slope or plot-drainage check.
metadata:
  version: "0.1.0"
  spec: "AICC-BIM-SPC-0006 Rev L §4.14 + Amendment L-a (Earthworks at 30 %) + ADDENDUM (IFC probe); baseline plugin 0.10.0; rules_v7 (EWK inactive until rules_v8 after real 30 % tests — DL-17); no rules_v8 in this build"
---

# Reviewing earthworks and geotech (skill 13)

This skill compares Parsons grading / earthworks carriers with the EWK rules of the approved
rulebook and writes candidate findings (agent code `RDE`). It reads LandXML surface side files
(skill 3) and IFC models via a dependency-free STEP reader; it does not decode Civil 3D DWG
surfaces. The scripts do every check. Run them in order, read their one-line JSON results and
report. Do not re-do a check by hand, do not invent geometry or FoS numbers, and never edit a
result file.

**At 30 % (PD)** — Amendment L-a: EWK-001, EWK-002 (slopes), EWK-003 (trapping) and EWK-102 raise
findings when carriers are present; MIDP-scheduled models missing → *Missing information* (DL-15).
EWK-002 FoS, EWK-003 frontage / 0.30 m and EWK-101 stay at **60 % (DD)**. An earthworks profile
(cut / fill, slope bands, inventory) is always written as information (no rule).

Packages: Python 3.11+, `jsonschema`, `shapely`, `numpy` (already used by skills 9 / 10). No new
dependencies. No IfcOpenShell in the build.

## Hard rules

1. Get the rules only through `applying-ldip-rulebook` (`get_rules.py --agent RDE`). Every value,
   tolerance, applicability and cap comes from that file. An inactive rule never runs; it is listed
   in `rules_skipped`.
2. The stage name is given by the owner per run (e.g. `30% Concept Design`, `60%`). Never guess it.
3. Surface **roles** (`existing`, `design_grading`, `road_top`, `finished_grading`) come from the
   caller, MIDP-title default or site `surface_roles` — never from geometry.
4. Do not composite formation-level and finished-level surfaces (`MIXED_LEVELS`).
5. Civil 3D DWG surfaces → `not_run` with the LandXML / IFC route. Do not enable LibreDWG (L-1 open).
6. Findings are candidates for the Verifier. Never raise a status above what `build_candidates.py`
   sets, and never write a finding by hand.
7. Detection thresholds live in `references/earthworks_params.json` (not rule values). Do not put
   deposited rulebook numbers into Git.

## Procedure

Run from the LDIP_Review folder. `<SK>` =
`${CLAUDE_PLUGIN_ROOT}/skills/reviewing-earthworks-geotech/scripts`, `<SK8>` =
`${CLAUDE_PLUGIN_ROOT}/skills/applying-ldip-rulebook/scripts`, `<EV>` the output folder.

1. **Rules** —
   `python "<SK8>/get_rules.py" --rulebook rulebook/rules_v<N>.json --agent RDE --out "<EV>/rules_RDE.json"`
2. **IFC surfaces** (when IFC is given) —
   `python "<SK>/read_ifc_surface.py" --ifc "<model>.ifc" --out-dir "<EV>/ifc" --prefix <stem>`
3. **Inventory / roles** —
   `python "<SK>/build_surfaces.py" --surface design_grading="<tin.json>" [--surface existing="<tin.json>"] [--surface road_top="<tin.json>"] [--landxml design_grading="<X>.xml.json"] [--civil3d-dwg design_grading="<G>.dwg"] [--midp "<midp.json>"] [--inventory "<EV>/files.json"] --site <SITE> --out "<EV>/surfaces.json"`
   `--midp` accepts the compact earthworks list (`references/midp_models.md`) or skill 6
   `read_midp.py` output. `--inventory` is skill 6 `files.json` / `package.json` / a plain file
   list (needed to judge DWG/NWC/DWFx/PDF). Optional:
   `python "<SK>/adapt_midp.py" --midp "<EV>/midp.json" --site <SITE> --out "<EV>/midp_earthworks.json"`.
4. **Slopes (EWK-001 / 002)** —
   `python "<SK>/check_slopes.py" --surfaces "<EV>/surfaces.json" --rules "<EV>/rules_RDE.json" --stage "<stage>" --site <SITE> [--report "<R>.pdf.json"] --out "<EV>/slopes.json"`
5. **Drainage (EWK-003)** —
   `python "<SK>/check_drainage.py" --surfaces "<EV>/surfaces.json" --rules "<EV>/rules_RDE.json" --stage "<stage>" --site <SITE> [--plots "<CAD>.dwg.json"] [--finished-levels] [--plot-levels "<levels.json>"] --out "<EV>/drainage.json"`
6. **Presence (EWK-101 / 102) + MIDP required** —
   `python "<SK>/check_presence.py" --slopes "<EV>/slopes.json" --rules "<EV>/rules_RDE.json" --stage "<stage>" --site <SITE> --surfaces "<EV>/surfaces.json" --file grading_plan="<P>.dwg.json" --file details="<D>.dwg.json" --out "<EV>/presence.json" --required-out "<EV>/required_inputs.json"`
7. **Profile (information)** —
   `python "<SK>/earthworks_profile.py" --surfaces "<EV>/surfaces.json" --slopes "<EV>/slopes.json" --drainage "<EV>/drainage.json" --presence "<EV>/presence.json" [--balance-quote "…"] --out "<EV>/earthworks_profile.json"`
8. **Findings** —
   `python "<SK>/build_candidates.py" --results "<EV>/slopes.json" "<EV>/drainage.json" "<EV>/presence.json" "<EV>/required_inputs.json" --rules "<EV>/rules_RDE.json" --run <run-id> --site <SITE> --sub <sub-id> --evidence-dir "<EV>/evidence" --out "<EV>/candidates.json"`
9. **Validate** —
   `python "<SK>/validate_findings.py" --findings "<EV>/candidates.json" --rules "<EV>/rules_RDE.json"`

## Outputs and exit codes

`surfaces.json`, `slopes.json`, `drainage.json`, `presence.json`, `required_inputs.json`,
`earthworks_profile.json`, `candidates.json`. Every script prints one JSON line.
Exit codes (SPC-0006 §2.3.3): 0 done · 2 input error · 3 missing package · 4 schema failure.

Methods and limits: `references/earthworks_methods.md`. Detection thresholds and terms:
`references/earthworks_params.json`. MIDP JSON contract + skill 6 adapter:
`references/midp_models.md`. Finding skeleton: `templates/finding.json`.
