---
name: comparing-drawing-revisions
description: >-
  LDIP design review (AICC JV): pairs drawings between a previous and a current
  Parsons submittal, checks that revision codes advance (PKG-010), diffs DWG
  geometry by hash (not handle), surfaces, PDF raster regions and package
  inventory, and reports CRS comment closure without raising findings. Use when
  an LDIP intake compares two submittal revisions or closes prior review
  comments.
metadata:
  version: "0.1.0"
  spec: "SPC-0006 Rev B §4.4 + Rev M §4.15"
---

# Comparing drawing revisions

This skill compares one Parsons submittal with the previous one: which sheets pair, whether the
revision advanced (PKG-010), what geometry / surfaces / raster regions / deliverables changed, and
how prior CRS comments look against the contractor responses. The scripts do the work. You run them
in order, read their one-line JSON results and report. Do not re-do a check by hand, do not read the
scripts' source, and never edit a result file.

Rules covered: **PKG-010** only (active in rules_v8 / rules_v7). Package completeness stays with
skill 6. This skill writes **no findings** from the CRS closure report (DM-7); `closure.json` /
`closure.xlsx` are report-only.

Packages: Python 3.11+, `openpyxl`, `jsonschema`.

## Hard rules

1. Get PKG-010 only through `applying-ldip-rulebook` (`get_rules.py --agent INT`). Do not hard-code
   revision order, rule values or rule state; the method text on the returned rule is the ordering
   contract.
2. Pair in order (a) normalised number → (b) `--number-map` → (c) proposed from title + sheet
   "x of y" + raster similarity (DM-2). Never invent a silent pair. Proposed pairs stay
   `status: proposed` at Probable.
3. Diff DWG entities by **geometry hash**, never by AutoCAD handle (Rev B §4.4). Change-source
   priority: DWG → surface → PDF raster (DM-4).
4. PDF raster diffs are capped at **Probable** (DM-4). Raster parameters (`dpi`, `threshold`,
   `region_size`) live in `references/params.json` tagged `[GAP — set from T7-R1]` (DM-9).
5. Renumbered pairs (map / proposed) that keep the same revision while content changed are an
   **Observation** (DM-3), not a PKG-010 hit.
6. CRS closure (M4): type L / D / G (`references/row_types.md`, DM-5). L = changed/unchanged at the
   location (`--rev-diff`); D = present/absent in N (`--package-n`); G = Cannot tell with optional
   `supporting_refs` from skills 3 / 13 (DM-8). Outcomes only Addressed / Not addressed /
   Cannot tell. **Never write Closed / Open** (DM-6). Flag `deferred_by_response`. Report only —
   no findings until rules_v9 PKG-014 (DM-7).
7. Write outputs only under the context / evidence folders. The submittal folders are read-only.

## Procedure (plan → validate → execute → verify)

`<SK>` is `${CLAUDE_PLUGIN_ROOT}/skills/comparing-drawing-revisions/scripts`; `<RB>` the rulebook;
`<CTX>` = `0.ai_context/<SITE>/<sub-id>`; `<EV>` = `findings/<run>/evidence/intake`.

1. **Plan — rules:**
   `python "${CLAUDE_PLUGIN_ROOT}/skills/applying-ldip-rulebook/scripts/get_rules.py" --rulebook "<RB>" --agent INT --skill comparing-drawing-revisions --out "<EV>/rules_INT_skill7.json"`
2. **Validate — pair:**
   `python "<SK>/match_revisions.py" --previous "<prev package or drawing list>" --current "<CTX>/package.json" --out "<EV>/pairs.json"`
   Add `--number-map <old,new.csv>` when the owner supplies renumbers.
3. **Execute — diff then revision:**
   `python "<SK>/diff_entities.py" --pairs "<EV>/pairs.json" --previous-package "<prev package>" --current-package "<CTX>/package.json" --out "<CTX>/rev_diff.json"`
   `python "<SK>/check_revision.py" --pairs "<EV>/pairs.json" --rules "<EV>/rules_INT_skill7.json" --entity-diff "<CTX>/rev_diff.json" --out "<CTX>/rev_diff.json"`
4. **Execute — CRS closure (when a CRS workbook is in the prompt):**
   `python "<SK>/check_closed_comments.py" --crs "<CRS.xlsx>" --out "<EV>/closure.json" --xlsx "<EV>/closure.xlsx" --package-n "<CTX>/package.json" --rev-diff "<CTX>/rev_diff.json"`
   Reviewer type overrides: `--types <id,type.csv>`. G supporting refs (skills 3 / 13): `--supporting-refs <id,ref.csv>`.
5. **Verify** — confirm `rev_diff.json` / `closure.json` validate against
   `templates/rev_diff.schema.json` and `templates/closure.schema.json`. Report counts; do not turn
   closure rows into findings.

## Outputs and exit codes

| File | Writer |
|------|--------|
| `0.ai_context/<SITE>/<sub>/rev_diff.json` | `diff_entities.py` + `check_revision.py` |
| `closure.json` / `closure.xlsx` | `check_closed_comments.py` |

Exit codes (SPC-0006 §2.3.3): 0 done · 2 input error · 3 missing package · 4 schema failure.
Report the script's `detail` on any non-zero exit.

Methods, pairing, row types and raster gaps: `references/`.
