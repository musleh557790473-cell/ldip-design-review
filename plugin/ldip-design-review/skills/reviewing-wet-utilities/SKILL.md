---
name: reviewing-wet-utilities
description: >-
  LDIP design review (AICC JV): checks each wet network's own design — storm,
  sewer (gravity and force main), potable water / fire, and TSE / irrigation —
  against the Employer's Requirements and Parsons' declared criteria, and checks
  that report, hydraulic model and drawings agree. Use when an LDIP intake
  reviews STO / SWN / PWT / IRR plans or SewerGEMS / WaterGEMS models for
  WUT-0xx / WUT-1xx wet-utility checks.
metadata:
  version: "0.1.1"
  spec: "SPC-0006 Rev Q §4.19"
---

# Reviewing wet utilities

This skill checks the **own design of each wet network** in one submittal against
(a) the Employer's Requirements / CMP (connected 83-page edition) and Parsons'
declared criteria where the ER is silent, and (b) report ↔ model ↔ drawing
agreement. The scripts do the work. Run them in order, read their one-line JSON
results and report. Do not re-do a check by hand, do not read the scripts'
source, and never edit a result file.

**0.1.1 (DQ-7a):** **W1**, **W2**, **W3**, **W5-geometry**, **W6-plan / report
table**, **W8** (declared vs governing). Every threshold in
`references/params.json` carries `source_tier` (1–5), `source`, and `value`;
governing = lowest tier that supplies a value. Out of this version: W4, W5
hydraulics, W7, `wntr` recompute, binary `.out` decoders.

Rules covered (report only until DQ-11): **WUT-001…049** presence / design,
**WUT-1xx** consistency — cited in finding text only. Agent code `WUT`. Owner
role `DMR` (DQ-5). Out of scope: corridor allocation (skill 14), catch-basin
spacing ROD-069 (skill 10), structures (skills 27 / 28), BOQ quantities
(skill 16), external NWC / SIO approvals (information only).

**Report only (DQ-11):** findings carry `issue_type` with **no `rule_id`** until
WUT states change in the next rulebook (skill 14 DO-4 pattern).

Packages: Python 3.11+, `ezdxf`, `jsonschema` (stdlib `sqlite3` for models).

## Hard rules

1. Read hydraulic models with `read_bentley_sqlite.py` from the Bentley
   `.stsw.sqlite` / `.wtg.sqlite` (DQ-3). Base alternative = **lowest
   AlternativeID**. Convert US feet → metres; diameters → mm. SHP exports are a
   cross-check only (NMAR Co-17 style via `--shp` / `--label`).
2. Read discipline plans with `read_network_plan.py` on **skill-1 DXF only**
   (DQ-6). Map layers through `references/wet_layers.json` (per site). Do not
   invent layer→utility mappings mid-run.
3. Read declared criteria with `read_wet_criteria.py` on skill-2 `*.pdf.json`
   (skill 10 pattern). Thresholds live in `references/params.json` with DQ-7a
   source tiers. Do not put deposited rulebook numbers into Git.
4. W3–W6 compare against the **governing** threshold (lowest source_tier). Same-
   tier CMP≠ER alternatives are **Observation** findings (DQ-15 style). W8
   compares each declared (tier 5) criterion with governing: Observation if
   declared stricter, Minor if weaker; Confirmed; no finding when equal / inside
   range.
5. W3 crown cover / depth to crown = `GL−(IL+D)` (stored as `cover_m`). Cover
   < 1.2 m → Minor ("allowed to 0.5 m if protected — confirm"); < 0.5 m → Major.
   Depth > 6.0 + 0.01 m → Major (CMP Q185).
6. W6 applies Table 7-8 / 7-11 and §7.5.7–7.5.8; ER storm conflicts are
   **Observation** findings (DQ-13 / DQ-15).
7. Severity (DQ-12): pressure / fire / min gradient / adverse / MH spacing /
   max depth = Major; cover, diameter labels, report-vs-model, declared-weaker =
   Minor.
8. Status: DWG label vs model value → Confirmed (unless PDF status_cap); model-
   only geometry → Probable; W8 text → Confirmed.
9. Write outputs only under the context / evidence folders. Submittal folders are
   read-only. Never commit real project data.

## Procedure (plan → read → check → verify)

`<SK>` is `${CLAUDE_PLUGIN_ROOT}/skills/reviewing-wet-utilities/scripts`;
`<CTX>` = `0.ai_context/<SITE>/<sub-id>`; `<EV>` = `findings/<run>/evidence/intake`.

1. **Plan — stage / site:** take the owner stage name (e.g. `30% Concept Design`)
   and site. Never guess.
2. **Read — hydraulic model:**
   `python "<SK>/read_bentley_sqlite.py" --sqlite "<SWN-MOD>.stsw.sqlite" --network sewer --out "<EV>/networks.json"`
   Optional SHP cross-check:
   `... --shp "<export>.shp" --label Co-17`
   Repeat per discipline (`water` / `irrigation` / `storm` when submitted).
3. **Read — plan (DXF):**
   `python "<SK>/read_network_plan.py" --dxf "<SWN-10000>.dxf" --site <SITE> --network sewer --merge-networks "<EV>/networks.json" --out "<EV>/networks.json"`
4. **Read — criteria** (skill-2 PDF text JSON):
   `python "<SK>/read_wet_criteria.py" --text "<CTX>/<report>.pdf.json" --out "<EV>/criteria.json"`
5. **Check — W1 / W2 / W3 / W5-geom / W6 / W8:**
   `python "<SK>/check_wet.py" --networks "<EV>/networks.json" --criteria "<EV>/criteria.json" --presence "<EV>/wet_presence.json" --site <SITE> --sub <sub-id> --stage "<stage>" --out-consistency "<CTX>/consistency.json" --out-candidates "findings/<run>/candidates/wut.json"`
   Omit `--presence` to infer from `networks.json`; omit `--criteria` when absent
   (W8 then limited to params-declared fallbacks).
6. **Verify** — confirm outputs validate against `templates/*.schema.json`. Report
   conduit counts, length_km, finding counts by check. Do **not** turn report-only
   findings into rule-backed CRS rows (DQ-11).

## Outputs and exit codes

| File | Writer |
|------|--------|
| `networks.json` | `read_bentley_sqlite.py` (+ plan merge) |
| `criteria.json` | `read_wet_criteria.py` |
| `0.ai_context/<SITE>/<sub>/consistency.json` | `check_wet.py` |
| `findings/<run>/candidates/wut.json` | `check_wet.py` |

Exit codes (SPC-0006 §2.3.3): 0 done · 2 input error · 3 missing package · 4 schema failure.
Report the script's `detail` on any non-zero exit.

Methods, ER tables and layer map: `references/`. Skeletons: `templates/`.
