# CRS row types (DM-5; Rev M §4.15.3 M4)

Each CRS row is typed **L**, **D** or **G** from the Review Comment / Doc. No. / response text.
The reviewer may override any row with `--types <id,type.csv>`.

Keyword lists below are matched case-insensitively as substrings. Highest hit count wins; ties
prefer L, then D, then G. No hit → **G**.

## L

**Location** on a document: sheet / grid / coordinates (Rev M M4).

- sheet
- grid
- coordinate
- coordinates
- chainage
- station
- easting
- northing
- location
- detail
- layout
- plan
- section
- elevation
- title block
- drawing no
- doc. no
- page/

## D

**Deliverable or data request** (Rev M M4) — ask for a file, model, schedule or dataset.

- deliverable
- submit
- provide
- missing
- not received
- data request
- request the
- please provide
- landxml
- ifc
- model
- schedule
- register
- package
- workbook
- native file
- midp
- xlsx
- csv

## G

**Design statement** (Rev M M4) — judgement / criteria / adequacy; not a sheet pin or a file ask.

- design
- criteria
- adequate
- adequacy
- should be
- consider
- recommendation
- clarify the design
- design intent
- design statement
- engineering judgement
- general note
- for information
- fyi
- guidance

## Evidence → outcome (M4)

| Type | Evidence | Outcome |
|------|----------|---------|
| L | location **changed** in N vs N-1 (`--rev-diff`) | Addressed |
| L | location **unchanged** | Not addressed |
| L | no location evidence | Cannot tell (response copied; deferred flagged) |
| D | deliverable / data **present** in N (`--package-n`) | Addressed |
| D | **absent** in N | Not addressed |
| D | no package for N | Cannot tell (response fallback when package omitted) |
| G | design statement | **Cannot tell** always; optional `supporting_refs` from skills 3 / 13 (DM-8) — never an outcome |

Outcomes are only **Addressed**, **Not addressed**, or **Cannot tell**. Never Closed or Open (DM-6).
`deferred_by_response` is a separate flag when the contractor defers to a later stage (DM-8).
Until rules_v9 PKG-014, this skill writes the report only — no findings (DM-7).
