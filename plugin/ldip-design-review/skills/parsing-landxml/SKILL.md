---
name: parsing-landxml
description: >-
  LDIP design review (AICC JV): reads LandXML files exported from Civil 3D by
  Parsons, by the Employer (RCRC survey) or by AICC, and extracts road
  alignments (lines, arcs, spirals, stations), vertical profiles (PVIs,
  grades, vertical curves and K values) and TIN surfaces (points, faces,
  breaklines, extents, areas) into the common ingestion JSON in EPSG:32638
  metres, recording the file's source. Use when an LDIP submittal or survey
  package contains .xml LandXML files that road or earthworks checks need.
metadata:
  version: "0.1.1"
  spec: "AICC-BIM-SPC-0006 Rev C §2.4.2, §4.12; AICC-BIM-SPC-0001 Rev G §1.5"
---

# Parsing LandXML

This skill turns one LandXML file into `<file>.xml.json` (common ingestion file, template 1.1) plus one
`<stem>.surface-<n>.json` side file per surface holding the full TIN. It records what the file contains;
it never judges the design and never raises a finding (limits are checked later by the road and
earthworks skills through the rulebook). Run the scripts, read their one-line results, and do not read
their source.

Packages: Python 3.11+, `lxml`, `jsonschema`. No converter.

## Hard rules

1. `--provenance` is given by the caller, never guessed from the content: `parsons` for files under
   `submittals/<SITE>/<sub-id>/`, `employer` for Employer (RCRC) reference data, `aicc_export` for files
   AICC exported from Parsons Civil 3D models (their names end `-AICC-EXPORT`). If the prompt does not
   say, stop and ask. In a review run (intake-batch) the folder rule decides and `employer` is not used;
   `write_manifest.py` of skill 1 records each `.xml` and checks the same rule.
2. Write only to the output folder you were given. Never write or edit a `.xml.json` or side file by hand,
   and never fill in a value the script did not produce.
3. A file that fails (exit 2, 3 or 4) is not retried: continue with the next file and report the code and
   detail. Exit 0 with `"status": "skipped"` means the file is not LandXML (for example a Civil 3D side
   file `*.xml.aecc.pnt` / `*.xml.aecc.tri`); nothing is written, and that is not an error.
4. Warnings are data. Copy them as they are; do not resolve them. `AXIS_ORDER_SUSPECT`,
   `CRS_MISSING`, `CRS_NOT_32638` and `UNITS_NOT_METRES` are for the GIS / road checks to judge; the script
   never reprojects or converts.
5. A refused file (DOCTYPE with entity declarations, file above 500 MB) stays refused: do not edit or
   strip the file to make it pass.

## Procedure (plan → parse → validate)

Run each command from the LDIP_Review folder with paths relative to it. `<SK>` is
`${CLAUDE_PLUGIN_ROOT}/skills/parsing-landxml/scripts`, `<SK1>` is
`${CLAUDE_PLUGIN_ROOT}/skills/extracting-dwg-data/scripts`, `<B>` the output folder, `<F>` a LandXML file.

1. **Plan** — list the `.xml` files from the prompt with their provenance. Nothing else is read.
2. **Parse** — one call per file:
   `python "<SK>/parse_landxml.py" --in "<F>" --out "<B>/<file>.json" --provenance <p> --source-path "<F>"`
   (`<file>` = the file name with `.xml`; side files are written next to it. `--out "<B>"` alone also
   works standalone, but in a review run the guard needs the file form.)
   The script checks the file (root element, namespace, size, DOCTYPE), streams it, computes, validates
   its own output against the schemas, then writes the side files and the main file (`.tmp` → rename).
3. **Validate** — all ingestion files in one call:
   `python "<SK1>/validate_ingestion.py" --files "<B>/<file1>.xml.json" "<B>/<file2>.xml.json"`
   It also checks that each side file exists and matches its `tin_sha256`. Exit 4 lists the failing
   files; do not edit them.

## Outputs and exit codes

Per file: `<file>.xml.json` and, per surface, `<stem>.surface-<n>.json`. Every script prints one JSON line.
Exit codes (SPC-0006 §2.3.3): 0 done or skipped · 2 input error (missing, > 500 MB, malformed XML,
DOCTYPE with entities, unknown LandXML namespace) · 3 missing package · 4 schema failure (nothing written).

What each `content` key holds, the coordinate-order rule, the warning codes and the limits of this
version: `references/landxml_content.md`. The file skeleton: `templates/ingestion.json`.
