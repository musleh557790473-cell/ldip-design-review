---
name: coordinating-utility-corridors
description: >-
  LDIP design review (AICC JV): checks the allocation of utilities in the road
  corridor — typical-section band offsets from the curb, wet/dry side rule,
  power horizontal spacing, presence of every utility, and plan networks vs
  allocated bands. Use when an LDIP intake reviews utilities-corridor cross
  sections (UTL-30000) or corridor plans (UTL-10000) for COR-001 / COR-002 /
  COR-101.
metadata:
  version: "0.1.1"
  spec: "SPC-0006 Rev O §4.17"
---

# Coordinating utility corridors

This skill checks where each utility sits in the typical road section (offset from
the curb, side), the horizontal spacing of power cables to other utilities, that
every utility has a position, and that plan networks follow their allocated band.
The scripts do the work. Run them in order, read their one-line JSON results and
report. Do not re-do a check by hand, do not read the scripts' source, and never
edit a result file.

Rules covered (report only until DO-4): **COR-001**, **COR-002**, **COR-101**.
Agent code `COO`. Out of scope: each network's own design (skills 11 / 12),
hydraulics, culvert need, cover to pavement (R-O3), 3D clashes until DD.

**Report only (DO-4):** `corridors.json` findings carry `issue_type` with
**no `rule_id`** until COR states change in rules_v9 after T14-R on all three
sites. C2 compares unsigned band–curb distance to ER §7.10(e) positions
(water 1.5 / storm 2.5 / sewer 3.0 m; DO-5 / R-O5); C4 at PD is *Not
auto-verifiable* (DO-6).

Packages: Python 3.11+, `ezdwg==0.16.0` (pinned), `jsonschema`, `pdfplumber`
(PDF fallback only).

## Hard rules

1. Read band INSERTs with `read_corridor_sections.py` on the **ezdwg raw** route
   (DO-2 / G-O1). Do **not** use `to_dxf` for band geometry — nested INSERTs in
   bound-xref blocks are dropped there.
2. Map layers through `references/utility_layers.json` (per site; DO-3 moves the
   code list to rules_v9 later). Do not invent layer→utility mappings mid-run.
3. Detection thresholds live in `references/params.json` (DO-7 C5 tolerance;
   DO-5a `c2_er_positions_m` / `c2_tolerance_m`). Do not put deposited rulebook
   numbers into Git.
4. C2 uses **unsigned** band-centre distance to the curb vs
   `params.c2_er_positions_m` (R-O5). Finding when `|d − ER| > c2_tolerance_m`
   (0.5 m): `design_error`, Probable, Major, one per section × utility, CRS to
   Parsons. Storm CB bands are not compared. C3 runs. C4 writes the spacing
   table and marks close pairs *Not auto-verifiable* — no Confirmed design_error
   from C4 at PD (DO-6).
5. PDF fallback caps every finding status at **Probable** (DO-2).
6. Write outputs only under the context / evidence folders. Submittal folders are
   read-only.

## Procedure (plan → read → check → verify)

`<SK>` is `${CLAUDE_PLUGIN_ROOT}/skills/coordinating-utility-corridors/scripts`;
`<CTX>` = `0.ai_context/<SITE>/<sub-id>`; `<EV>` = `findings/<run>/evidence/intake`.

1. **Plan — stage / site:** take the owner stage name (e.g. `30% Concept Design`)
   and site. Never guess.
2. **Read — sections (DWG raw):**
   `python "<SK>/read_corridor_sections.py" --dwg "<UTL-30000>.dwg" --site <SITE> --out "<EV>/sections.json"`
   PDF only when the DWG cannot be read:
   `python "<SK>/read_corridor_sections.py" --pdf "<UTL-30000>.pdf" --site <SITE> --out "<EV>/sections.json"`
3. **Check — C1–C6:**
   `python "<SK>/check_corridors.py" --sections "<EV>/sections.json" --site <SITE> --sub <sub-id> --stage "<stage>" --out "<CTX>/corridors.json"`
   Add `--plan "<EV>/corridor_plan.json"` when skill-1 linework for `UTL-10000-*`
   and discipline plans is available (C5 / C6).
4. **Verify** — confirm `corridors.json` validates against
   `templates/corridors.schema.json`. Report section × utility row counts and
   finding counts. Do **not** turn report-only findings into rule-backed CRS rows
   (DO-4).

## Outputs and exit codes

| File | Writer |
|------|--------|
| `sections.json` | `read_corridor_sections.py` |
| `0.ai_context/<SITE>/<sub>/corridors.json` | `check_corridors.py` |

Exit codes (SPC-0006 §2.3.3): 0 done · 2 input error · 3 missing package · 4 schema failure.
Report the script's `detail` on any non-zero exit.

Methods and layer map: `references/`. Finding / row skeleton: `templates/corridors.json`.
C2 ER wording: cite R-O5 (AICC project note S33-R-O5 (not in Git)).
