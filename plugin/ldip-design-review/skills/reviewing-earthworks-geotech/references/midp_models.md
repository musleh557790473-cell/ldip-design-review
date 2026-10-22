# Earthworks MIDP + package inventory

`build_surfaces.py --midp` accepts a **compact earthworks JSON** or skill 6
`read_midp.py` output. Optional `--inventory` judges drawing/exchange formats
against the submittal (R1). Site filter via `--site` (R2). There is no
`require_xml` flag — every scheduled format is checked when evidence exists.

## Compact JSON (`--midp`)

```json
{
  "models": [
    {
      "number": "LDIP-RCRC-SAP-03-DN-ROD-MOD-90000-002",
      "title": "Site Grading",
      "role": "design_grading",
      "formats": ["IFC", "XML", "DWG", "NWC", "DWFx"],
      "stage": "30% Concept Design",
      "milestone": 30
    }
  ]
}
```

| Field | Required | Notes |
|---|---|---|
| `number` / `deliverable` | yes | Matched to file / `document_no` after stripping trailing `-00` / `-0A` revision |
| `title` | recommended | Used with role for old-scheme numbers (L-a §7 item 4) |
| `role` | recommended | `existing` · `design_grading` · `road_top` · `finished_grading` · `drawing` |
| `formats` | yes for DL-15 | Exchange formats at the stage |
| `stage` / `milestone` | for D7 | No row at 30 % → PD findings/MI not raised |

A bare JSON array of the same objects is also accepted.

## Skill 6 `read_midp.py` → adapt

```bash
python "<SK6>/read_midp.py" --file "<MIDP.xlsm>" --out "<EV>/midp.json"
python "<SK>/adapt_midp.py" --midp "<EV>/midp.json" --site NMAR --out "<EV>/midp_ewk.json"
python "<SK>/build_surfaces.py" --surface design_grading="<tin.json>" \
    --midp "<EV>/midp.json" --inventory "<EV>/files.json" --site NMAR \
    --out "<EV>/surfaces.json"
```

`build_surfaces --midp` auto-adapts skill 6 output (same filter as `adapt_midp.py`).

**Site filter (R2):** `AQ` → QRWN, `AN` → NRJS, `DN` → NMAR (section code in the number).

**Carrier filter (R3 / R7):** number patterns `ROD-MOD-90000`, `IRO-MOD`,
`ROD-DWG-10300`…`10600`; title phrases including whole-word `NGL` / `EGL`,
Existing Contour Plan, Cut/Fill Stake-Out Plan, Roads Solids Model →
`design_grading`, Site Grading, Road Surface. **"Single-Line Diagram" is not a carrier.**

## Package inventory (`--inventory`) — R1

Accepted shapes:

1. **Skill 6 `inventory.py` / `files.json`** — `{ "files": [ { "path", "name", "format", "number" } ] }`
2. **Skill 6 `package.json`** — `{ "sheet_register": [ { "base", "files": [...] } ] }`
3. **Plain file list** — `["path/to/a.ifc", …]` or `[{ "path", "format", "number" }, …]`

| With `--inventory` | Without `--inventory` |
|---|---|
| Every scheduled format judged vs inventory + surfaces | Format check `not_run` for DWG / NWC / DWFx / PDF |
| | Surface formats (IFC, XML) still judged from `--surface` / LandXML |
| | "Model not received" only when no surface **and** no inventory file |

Never raise DWG / NWC / DWFx / PDF missing without an inventory.

## Matching order

1. Document number / source file (revision stripped) — `matched_by: document_no/source_file`
2. Caller **role** when numbers differ (old-scheme NRJS) — `matched_by: role` + note
3. Real title (not IFC proxy names) — `matched_by: title`

## DL-15 finding

One *Missing information* candidate **per site** listing every missing deliverable and
format. `rule_id` = `EWK-001`; clause cites required inputs for EWK-001 / 002 / 003.
