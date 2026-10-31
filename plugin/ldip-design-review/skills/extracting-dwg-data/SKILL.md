---
name: extracting-dwg-data
description: 'LDIP design review (AICC JV): converts Parsons DWG drawings to DXF and extracts layers, blocks and attributes, text, dimensions with override flags, closed polylines, external references, units, CRS signals and Civil 3D (AECC) object presence into the common ingestion JSON. Use when an LDIP submittal batch contains .dwg files that must be read before package or GIS checks.'
metadata:
  version: "0.2.2"
  spec: "AICC-BIM-SPC-0006 Rev B §2.4, §4.1; Rev F §4.1A; Amendment O-a; G-Oa4 (DOa4)"
---

# Extracting DWG data

This skill turns each DWG of your batch into `<stem>.dwg.json`, the common ingestion file that the
package and GIS agents read. It records what the drawing contains; it never judges the design and
never raises a finding. The scripts do the work: run them, read their one-line results, and do not
read their source.

Packages: Python 3.11+, `ezdxf`, `jsonschema`; engine `ezdwg==0.16.0` (pinned, D13 / D13-2), or ODA File
Converter 27.9.0 / AutoCAD Core Console as external programs. Another ezdwg version still converts, but
`convert.py` adds the warning `ENGINE_VERSION_UNPINNED` (results not comparable with pinned runs).
Engine `chain` (`convert.py` 0.3.0, Amendment O-a / Rev F §4.1A): ezdwg → LibreDWG `dwg2dxf`
(`LDIP_LIBREDWG_EXE`) → ODA (`LDIP_ODA_EXE`) → `next: local_autocad`. Missing programs are skipped
and logged. After each engine, a nested-content check (INSERT/TEXT/MTEXT per layer, raw vs DXF) may
warn `NESTED_CONTENT_LOSS` and try the next reader; if every reader loses content, the DXF with the
smallest loss is kept with that warning.

## Hard rules

1. Use the engine named in the prompt (`--engine`). Never switch engine on your own; a failed
   conversion is recorded, not retried with another engine. With `--engine chain` the script itself
   tries the readers in the agreed order and records which one converted; a file that fails with
   `next: local_autocad` is left for the owner's local AutoCAD route (you never call a connector).
2. Work only on the files in your batch list and write only to your batch folder
   `0.ai_context/<SITE>/<sub-id>/b<nn>/`.
3. A file that fails (any exit code other than 0) is not retried: continue with the next file;
   `write_manifest.py` records it as `status: failed` with the script's code and detail. Never write a
   `.dwg.json` by hand and never fill in values the script did not produce.
4. Warnings are data. Copy them as they are; do not resolve them (for example, units not metres or no
   CRS is for GIS-008 to judge). Treat `NESTED_CONTENT_LOSS` the same way: record it; do not invent
   missing inserts or texts.
5. `origin_signals` is Probable by definition; `aecc.civil3d_native: true` (an AECC object is drawn)
   is the only deterministic Civil 3D signal (brief §1.4). `saved_in_civil3d: true` means only that
   the file was saved in a Civil 3D session. With engine `ezdwg`, an AECC count of 0 proves nothing
   (warning `AECC_DETECTION_LIMITED`).

## Procedure (plan → convert → extract → validate → manifest)

Run each command from the LDIP_Review folder with paths relative to it. `<SK>` is
`${CLAUDE_PLUGIN_ROOT}/skills/extracting-dwg-data/scripts`, `<B>` is your batch folder, `<F>` a DWG from
your list and `<stem>` its file name without `.dwg`.

1. **Plan** — list the DWGs of your batch from the prompt. Nothing else is read.
2. **Convert** — for each DWG, with the Bash tool `timeout` set to 600000 (a 15 MB DWG takes
   about 2 minutes with ezdwg; the default 120 s cuts it off):
   `python "<SK>/convert.py" --engine <engine> --dwg "<F>" --out "<B>/<stem>.dxf" --log "<B>/<stem>.convert.json" --reuse`
   Prefer `--engine chain` unless the prompt names a single engine. Optional `--cross-check` compares
   INSERT pos/scale by handle (`dwgread -O JSON` vs ezdwg, tol 0.001 m) into the convert log.
   `--reuse` keeps an earlier conversion of the same DWG with the same engine (a retry does not
   convert again). Exit 3 = engine missing, DWG version not supported, or timeout; exit 2 = not a
   DWG or no DXF produced. Either way: continue; the manifest script records it (hard rule 3).
3. **Extract** —
   `python "<SK>/extract_dxf.py" --dxf "<B>/<stem>.dxf" --dwg "<F>" --convert-log "<B>/<stem>.convert.json" --out "<B>/<stem>.dwg.json" --source-path "<F>" --resolve-base "<folder of F>" [--linework]`
   Add `--linework` for a road drawing: the prompt marks it as a road carrier, or its document number
   has the discipline code `ROD` or `IRO`. It writes `<B>/<stem>.linework.json` (all line work,
   flattened) and lists it in the ingestion file. Block references are expanded by default (texts,
   closed polylines and DIMENSIONs inside them are kept with `via_block`; ATTRIBs on expanded inserts
   become texts with `source: "attrib"`). Title-block inserts (paper space, Parsons `TB_ATTRIB_BLOCKS`,
   or a name containing "Title Block") are not expanded. Never pass `--no-expand` unless the prompt
   says so.
4. **Validate** — all ingestion files of the batch in one call:
   `python "<SK>/validate_ingestion.py" --files "<B>/<stem1>.dwg.json" "<B>/<stem2>.dwg.json"`
   Exit 4 lists the failing files; do not edit the JSON (`write_manifest.py` records them as failed).
5. **Manifest** — one call writes `<B>/batch_manifest.json` from the logs and ingestion files on
   disk; never write or edit it yourself:
   `python "<SK>/write_manifest.py" --out "<B>/batch_manifest.json" --run <run_id> --site <SITE> --sub <sub-id> --file "<F1>" <sha256-1> --file "<F2>" <sha256-2>`
   (always this `--out` form: it also works when every file failed and `<B>` was never created.)
   Give every file of your list (DWG, PDF, LandXML and other) with the SHA-256 from the prompt. It
   sets `status`, `output`, `intermediates`, `side_files` (LandXML surfaces, DWG linework), `error`, `converter` and
   `counts`, validates every listed `.dwg.json`, `.pdf.json` and `.xml.json` (LandXML also: side files
   and the provenance folder rule) and the manifest itself (SPC-0006 §3.1), and exits 4 without writing
   if the manifest fails its schema. The format checker then checks the batch (S2, C3).

## Outputs and exit codes

Per DWG: `<stem>.dxf`, `<stem>.convert.json`, `<stem>.dwg.json` (template 1.2), and
`<stem>.linework.json` with `--linework`. Every script prints one JSON line.
`batch_manifest.json` per batch (from `write_manifest.py`).
Exit codes (SPC-0006 §2.3.3): 0 done · 2 input error · 3 missing engine or package · 4 schema failure.

What each content key holds, the dimension override rule, the title-block heuristic, the reader chain
and nested-content warning: `references/dwg_content.md`. The file skeleton: `templates/ingestion.json`.
