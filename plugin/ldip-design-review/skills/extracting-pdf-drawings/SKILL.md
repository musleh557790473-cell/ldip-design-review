---
name: extracting-pdf-drawings
description: >-
  LDIP design review (AICC JV): extracts vector text with page coordinates,
  title-block fields, sheet and detail callouts from Parsons PDF drawings and
  reports; detects scanned pages and runs OCR on tiles; produces evidence crops
  for a given page and box. Use when an LDIP submittal contains PDFs, or when a
  finding needs a verbatim text crop as evidence.
metadata:
  version: "0.3.0"
  spec: "AICC-BIM-SPC-0006 Rev B §2.4, §4.2; Rev F §4.13C.3 (DF-5)"
---

# Extracting PDF drawings

This skill turns each PDF of your batch into `<file>.pdf.json`, the common ingestion file the other
agents read, and makes evidence crops on request. It records what the PDF contains; it never judges
the design and never raises a finding. Run the scripts, read their one-line results, and do not read
their source.

Packages: Python 3.11+, `pypdfium2` (reads and renders), `Pillow`, `pytesseract`, `jsonschema`; the
`tesseract` program with Arabic (`ara`) and English data. `pdfplumber` is the Verifier's second
reader (skill 18) and reads the vector paths of `--paths`. PyMuPDF is not used (licence review open).

## Hard rules

1. Work only on the files in your list. Intake writes only to its batch folder
   `0.ai_context/<SITE>/<sub-id>/b<nn>/`; the Verifier writes crops only to
   `findings/<run>/evidence/verifier/`.
2. A file that fails (exit other than 0) is not retried: continue with the next file;
   `write_manifest.py` records it as `failed`. Never write a `.pdf.json` by hand.
3. The title block is read by the script from the Parsons label layout (`references/title_blocks.md`);
   a page without one gets `TITLE_BLOCK_NOT_FOUND`. Callouts are not read yet (`CALLOUT_STYLE_UNKNOWN`).
   Do not read either yourself from the text spans.
4. OCR text (`ocr_spans`, `source_type: pdf_ocr`) is Probable evidence at most. The skill reads text
   in pictures, never their shapes: plot boundaries, road edges or areas drawn only as a picture are not
   extracted — do not describe or measure them. Colours in
   `colours_used` are recorded only; colour is never sole evidence (brief §5.5).
5. Warnings are data: copy them as they are (for example `PAGE_UNREADABLE` for a damaged page).

## Procedure — intake (plan → extract → OCR → validate → manifest)

Run each command from the LDIP_Review folder with relative paths, one `python` call per Bash call.
`<SK>` is `${CLAUDE_PLUGIN_ROOT}/skills/extracting-pdf-drawings/scripts`, `<SK1>` is
`${CLAUDE_PLUGIN_ROOT}/skills/extracting-dwg-data/scripts`, `<B>` your batch folder, `<F>` a PDF of
your list and `<name>` its file name (with `.pdf`).

1. **Plan** — list the PDFs of your batch from the prompt.
2. **Extract** — `python "<SK>/extract_pdf.py" --pdf "<F>" --out "<B>/<name>.json" --source-path "<F>"`
   The result lists `scanned_pages`, `image_region_pages` (picture regions inside vector pages) and
   `unreadable_pages`. Add `--paths` only for a PDF the prompt names as the road hierarchy plan: it also
   writes `<B>/<name without .pdf>.paths.json` (coloured vector paths, for the road-class method M4 of
   skill 10) and lists it in the ingestion file.
3. **OCR** — only when `scanned_pages` or `image_region_pages` is not empty, with the Bash `timeout`
   set to 600000 (it reads scanned pages whole and the picture regions of the other pages):
   `python "<SK>/ocr_tiles.py" --ingestion "<B>/<name>.json" --pdf "<F>"`
   OCR takes about 35 s per A3 page: with more than 12 scanned pages, run it in groups of at most 12
   with `--pages <n> <n> …` (one call per group).
   Exit 3 (tesseract or Arabic data missing): continue; the vector part stays valid.
4. **Validate** — `python "<SK1>/validate_ingestion.py" --files "<B>/<name>.json"` (one call may list
   all `.dwg.json` and `.pdf.json` files of the batch).
5. **Manifest** — the single `write_manifest.py` call of skill 1 (`extracting-dwg-data` step 5) with
   every file of your list; it records each PDF as `extracted` or `failed`.

## Procedure — evidence crop (Verifier)

`python "<SK>/crop.py" --pdf "<F>" --page <n> --bbox <x0> <top> <x1> <bottom> --dpi 200 --out "findings/<run>/evidence/verifier/<finding-no>_<k>.png"`
The box is in PDF points from the page's top-left corner (as in `.pdf.json`). The result prints the
PNG's `sha256`, also written to `<png>.sha256`; quote it in your evidence. Same input → same hash.

## Outputs and exit codes

Per PDF: `<name>.json` (= `<file>.pdf.json`), and `<stem>.paths.json` with `--paths`. Crops: `<…>.png` and `<…>.png.sha256`. Every script
prints one JSON line. Exit codes (SPC-0006 §2.3.3): 0 done · 2 input error (missing file, not a PDF,
encrypted, page or box outside the page, path over 250 characters) · 3 missing package or OCR data ·
4 schema failure. Content fields, the scanned-page rule and the known limits: `references/pdf_content.md`.
