# Earthworks MIDP input (`--midp`)

`build_surfaces.py --midp` accepts either a **compact earthworks JSON** (hand-written or
adapted) or the full output of skill 6 `read_midp.py`. Every MIDP-scheduled exchange format
not received raises *Missing information* (L-a DL-15) — including XML / LandXML. There is
no `require_xml` flag.

## Compact JSON (earthworks list)

```json
{
  "models": [
    {
      "number": "LDIP-RCRC-SAP-03-DN-ROD-MOD-90000-002",
      "title": "Site Grading",
      "role": "design_grading",
      "formats": ["IFC", "XML"],
      "stage": "30% Concept Design",
      "milestone": 30
    }
  ]
}
```

| Field | Required | Notes |
|---|---|---|
| `number` / `deliverable` | yes | Document / model number matched to IFC file name / `document_no` first (D2) |
| `title` | recommended | Fallback match with `role` (ADDENDUM 4) |
| `role` | recommended | `existing` · `design_grading` · `road_top` · `finished_grading` |
| `formats` | yes for DL-15 | Exchange formats at the stage (e.g. `IFC`, `XML`, `DWG`) |
| `stage` / `milestone` | for D7 | When MIDP is supplied and **no** row is at 30 %, PD findings/MI are not raised |

A bare JSON array of the same objects is also accepted.

## Skill 6 `read_midp.py` output

Pass the skill 6 JSON directly:

```bash
python "<SK6>/read_midp.py" --file "<MIDP.xlsm>" --out "<EV>/midp.json"
python "<SK>/build_surfaces.py" --surface design_grading="<tin.json>" \
    --midp "<EV>/midp.json" --site NMAR --out "<EV>/surfaces.json"
```

`build_surfaces.py` detects `tool.name == "read_midp.py"` (or a top-level `rows` list) and
keeps rows whose exchanges mention 30 % / Concept and whose number/title look like NGL /
grading / road-surface models (`ROD-MOD-90000`, `IRO-MOD`, etc.).

Standalone adapter (same filter):

```bash
python "<SK>/adapt_midp.py" --midp "<EV>/midp.json" --out "<EV>/midp_earthworks.json"
```

## Matching order (D2)

1. `document_no` / `source_file` / IFC header name contains the MIDP `number`
2. Else `role` + `title` (substring / significant words)
3. Else `title` alone

Surface side files from `read_ifc_surface.py` carry `document_no` parsed from the IFC
file name (e.g. `…-ROD-MOD-90000-002`).
