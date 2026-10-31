# DWG ingestion content — field reference

## Contents

1. Engines
2. Top-level blocks
3. `content` keys
4. Rules applied by the extractor
5. Known limits

## 1. Engines (`convert.py --engine`, 0.3.0 — Amendment O-a)

| Engine | What it is | Notes |
|---|---|---|
| `ezdwg` | Python package, MIT, reads DWG R2000–R2018 | Under test (SPC-0006 Appendix A). Dimensions kept as DIMENSION, inserts kept. Classes and proxy objects are not documented as transferred, so AECC detection is limited. Entities it skips are listed in the log (`ENGINE_SKIPPED_ENTITIES`). **Pinned to 0.16.0** (D13, owner 5 Oct 2026; D13-2, owner 16 Oct 2026 — 0.12.9 dropped setting-out table cells): results from another version are not comparable; `convert.py` then warns `ENGINE_VERSION_UNPINNED` |
| `libredwg` | GNU LibreDWG `dwg2dxf` 0.14.8679 | `--libredwg-exe` or `LDIP_LIBREDWG_EXE`. Version from `dwgread --version`. DXF accepted only when ezdxf loads it (QRWN LibreDWG DXF can fail with "Invalid sort handle code 331" → next step) |
| `oda` | ODA File Converter 27.9.0 | `--exe` or env `LDIP_ODA_EXE`. Output ACAD2018 DXF, audit on. Licence: K2 [GAP]. Version recorded as `27.9.0` |
| `accoreconsole` | AutoCAD Core Console, Windows only | `--exe` or env `LDIP_ACCORE_EXE`. Script `DXFOUT … Version 2018, 16 decimals`. Prompt sequence to verify on the review workstation [GAP] |
| `chain` | Reader order O-a.2 | ezdwg → libredwg → oda → `next: local_autocad`. Missing programs logged as `skipped` |

The converter log `<stem>.convert.json` records the engine, version, input hash and DWG release
(from the file signature, e.g. `AC1032` = R2018), output hash, duration, return code, `chain[]`,
`fallback_from`, and nested-content / optional `--cross-check` results.

## 2. Top-level blocks (SPC-0006 §2.4)

- `source`: `path` as given by `--source-path`, SHA-256 and size of the DWG; `document_no` = the file
  stem when it has the shape of an LDIP number (9 or 10 fields), else null; `revision` null (the
  revision is not in the file name [GAP], see PKG-005).
- `extractor.converter`: engine name and version from the log.
- `crs.declared`: `EPSG:<code>` when the drawing has a GEODATA object that names an EPSG code; else null.
  `crs.units`: `m` (INSUNITS 6), `mm` (4), else `unknown`.
- `title_block`: fields read from the block insert with the most attributes in the first paper-space
  layout (model space if none). Tags matched: document number (`DWG_NO`, `DRAWING_NUMBER`, `DOC_NO`, …),
  revision (`REV`, `REVISION`), title (`TITLE`, `TITLE1`, `TITLE2` … joined), date, status. `source_ref`
  names the handle and block.
- `warnings`: code + detail (list in §4).
- `meta.extracted_at` is the only time-dependent value.

## 3. `content` keys (SPC-0006 §4.1.4)

| Key | Content |
|---|---|
| `header` | `insunits`, `measurement`, `extmin`, `extmax`, `acadver_source` (DWG), `acadver_dxf` |
| `layers[]` | name, colour, linetype, frozen, off, locked, entity count (all layouts) |
| `blocks[]` | every INSERT (model and paper space): name, layer, insert point, scale, rotation, attributes (tag → value; a repeated tag gets `#2`), handle, space |
| `texts[]` | TEXT and MTEXT: plain text, layer, insert point, height, rotation, handle, space |
| `dimensions[]` | dimension type, geometric measurement, stored text, `override`, `override_kind`, and for a numeric override `text_value` and `text_differs` |
| `polylines[]` | closed LWPOLYLINE and 2D POLYLINE in model space: vertices (0.001), `has_bulge`, elevation, area (0.01; arcs flattened at 0.001) |
| `open_polylines_by_layer` | count of open polylines per layer |
| `xrefs[]`, `images[]`, `fonts[]` | saved path or file name; `resolved` true/false with `--resolve-base`, null without |
| `aecc` | AECC classes; AECC entity and object types; proxy entities and proxy objects counted separately by class; `aecc_proxy_entity_count`; `civil3d_native` (at least one AECC object drawn in model or paper space); `saved_in_civil3d` (AECC classes but no AECC object drawn) |
| `origin_signals` | counts of `C-` layers, AECC layer and block names, anonymous `*U` blocks, proxies; confidence Probable |
| `layouts[]` | paper-space layouts with the title-block candidate (block, handle, attributes) |

## 4. Rules applied by the extractor

- **Dimension override (brief §5.5).** Stored text empty or `<>` → measured value shown, no override.
  Text containing `<>` → prefix/suffix only, no override. A single space → text suppressed
  (`override: true`, kind `suppressed`). Any other text → `text_override`; when it holds a number,
  `text_differs` is true if it differs from the measurement by more than 0.05 %.
- **Warnings:** `UNITS_NOT_METRES`, `CRS_NOT_DECLARED`, `CRS_NOT_32638`, `EXTENTS_NOT_SET`,
  `DXF_RECOVERED`, `TITLE_BLOCK_HEURISTIC`, `TITLE_BLOCK_NOT_FOUND`, `AECC_DETECTION_LIMITED`, and the
  converter's `ENGINE_SKIPPED_ENTITIES`, `ENGINE_FALLBACK`, `NESTED_CONTENT_LOSS` (O-a.3.1) and
  `TITLE_BLOCK_ATTRIBS_UNDECODED` (K5-2: the DWG holds block
  attributes ezdwg cannot decode, or a known Parsons title-block insert ("Ladun - Qairwan ATT", K5 profile)
  without readable attributes; the convert log `attributes` gives the counts and the blocks.
  `TITLE_BLOCK_NOT_FOUND` is then not added, and the title block is `null`).
- **Fonts:** SHX fonts installed with every AutoCAD (`txt`, `simplex`, `romans`, `isocp` …, list in
  `extract_dxf.py` `AUTOCAD_SHX`) count as resolved; any other font must be in the package (PKG-011).
- **Integrity:** the extractor refuses (exit 2) when the DWG hash differs from the one in the converter
  log, so a DXF can never be attached to the wrong DWG.

## 5. Known limits

- Parsons sheets (Namar 30%, AC1032) hold the title-block values as attributes of the paper-space block
  "Ladun - Qairwan ATT"; ezdwg 0.12.9 does not decode them and 0.16.0 does not write them to the DXF, so the DWG title block is read only with the
  oda or accoreconsole engine (K2). Tag names are not known until then; values read by tag name are
  Medium confidence at best (`TITLE_BLOCK_HEURISTIC`). The PDF title block is read by skill 2 (K5-1).
- Blocks inside blocks are not expanded; attributes are read from the top-level inserts only.
- 3D polylines, meshes, hatches and Civil 3D objects themselves are not extracted; Civil 3D data comes
  by LandXML export (brief §9.4).
- Real-file tests T1–T4 (SPC-0006 §4.1.8) need the `TAWAZUN PROGRAM` DWGs copied to
  `golden_set/inputs/` or `submittals/<SITE>/<sub-id>/`.
- The guard hook refuses a Bash command that contains `& | ; < >` or a backtick, so a DWG whose file
  name contains one of these characters cannot be processed until it is renamed when the submittal is filed.
- Civil 3D test (P-S3-22, owner 30 Sep 2026): a DWG saved in a Civil 3D session carries the whole AECC
  class catalogue (345–448 classes in the Parsons samples) even with no Civil 3D object, so classes alone
  give `saved_in_civil3d`, not `civil3d_native`.

## Block expansion and line work (0.2.0, SPC-0006 Rev F §4.1A)

- Template 1.2. Model-space block references (bound xrefs included) are expanded, nested to depth 4.
  Texts and closed polylines found inside are added to `texts` / `polylines` with `via_block` (block-name
  chain), `insert_handle` (the model-space insert) and `layer_short` (layer name after the last `$`);
  `handle` is the handle in the block definition. An entity on layer 0 takes its insert's layer.
  Inserts with attributes and the Parsons title-block blocks are not expanded (K5).
  `content.block_expansion` counts what was expanded (`--no-expand`: off).
- `--linework` writes `<stem>.linework.json` (schema `linework.schema.json` 1.0): every LINE, ARC,
  CIRCLE, LWPOLYLINE, 2-D POLYLINE and SPLINE of model space and of the expanded blocks, flattened at
  0.05 m, open and closed (`closed`), with `points` in drawing units; `layers` gives records and length per
  layer. Listed in `content.side_files` with its SHA-256 and record count. Over 200,000 records:
  `LINEWORK_LARGE` and the layer list only.
- Reader chain (`--engine chain`, convert.py 0.3.0, Amendment O-a / Rev F §4.1A): ezdwg → LibreDWG
  `dwg2dxf` → ODA → `next: local_autocad` (owner's local AutoCAD route). Missing programs are `skipped`.
  After each engine, O-a.3.1 compares INSERT/TEXT/MTEXT counts per layer (raw DWG vs DXF, all block
  records; handles are not compared). Any layer with raw > DXF → `NESTED_CONTENT_LOSS` and the next
  engine is tried; if every engine loses content, the DXF with the smallest loss is kept with the
  warning. Expected ezdwg real losses (probe): QRWN UTL-30000-001 28 layers / 218 entities; NRJS
  IUC-30001 23 / 97; NMAR UTL-30000-001 0 / 0; NMAR UTL-10000-000 2 / 4.
- Owner helper `dwg_common.insert_owner_chain(path)` (O-a.3.2): `decode_entity_placements` →
  (handle, entmode, owner); entmode 1/2 = top-level (ignore owner). Fallback: cycle rule when
  entmode unavailable (S33 G1 / §3B). Skill 14 adopts later. QRWN handle 18 → entmode 2, owner 0.
- Optional `--cross-check`: `dwgread -O JSON` vs ezdwg INSERT pos/scale by handle (tol 0.001 m).
  UTL-30000 band probe: QRWN 110/110, NRJS 44/44, NMAR 123/123, 0 mismatches.
