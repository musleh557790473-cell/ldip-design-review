# CRS row types (DM-5; Rev M §4.15.3 M4; S31 review R5)

Typing is done in `check_closed_comments.py` (not by loose keyword bags). The reviewer may override
any row with `--types <id,type.csv>`.

## L — location

**Only when** the row names a **document number** *and* a **sheet / grid / coordinate / chainage**
pattern (regex), **or** the Doc. No. column holds a document number plus a sheet reference.

Examples that qualify: `…-DWG-00-11000` + `sheet 01` / `grid A1` / `chainage 1+200`.

Bare words such as “plan”, “section”, “detail”, “layout”, “location”, “elevation”, “boundary”
alone do **not** make a row L.

## D — deliverable / data request

**Request verb** (submit / resubmit / provide / furnish / supply / include / attach) **plus** a
**deliverable noun** (report, survey, surface, sections, spot levels / elevations, quantities,
drawings, package, details, LandXML, IFC model, …).

Bare “IFC”, “model”, or “package” without a request verb does **not** make a row D.

## G — design statement

Everything else (design judgement, criteria, adequacy, guidance). Outcome is always **Cannot tell**;
optional `supporting_refs` from skills 3 / 13 never set the outcome (DM-8).

## Evidence → outcome (M4 / R4)

| Type | Evidence | Outcome |
|------|----------|---------|
| L | location changed / unchanged (`--rev-diff`) | Addressed / Not addressed |
| L | no `--rev-diff` evidence | **Cannot tell** (response copied only) |
| D | deliverable present / absent (`--package-n`) | Addressed / Not addressed |
| D | no `--package-n` | **Cannot tell** (response copied only) |
| G | — | **Cannot tell** |

The contractor response and status code are **copied**; they never set the outcome (R4).
`deferred_by_response` is a separate flag (R6 phrases). Never Closed / Open (DM-6).
Until rules_v9 PKG-014, report only — no findings (DM-7).

## Row intake (R7)

Only rows whose `#` cell is an **integer** are read. Reading stops at a reviewer / sign-off block
(e.g. “Reviewed Action: A-No Objection…”).
