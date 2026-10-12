# PDF ingestion content (`content` of `*.pdf.json`)

Contents: 1 Fields · 2 Scanned-page rule · 3 OCR · 4 Crops · 5 Known limits · 6 Pictures

## 1 Fields

| Field | Content |
|---|---|
| `pages[]` | `page` (from 1), `width_pt`, `height_pt` (as displayed), `rotation`, `char_count`, `objects`, `image_cover` (largest image / page area), `raster_ratio` (all images / page area, capped at 1), `scanned`, `spans[]`, `ocr_spans[]`, `colours_used[]`, `colours_complete`, `error` |
| `spans[]` | Vector words built from PDFium characters (break at spaces, line ends and large gaps, so rotated text works): `text`, `bbox` [x0, top, x1, bottom] in points from the displayed page's top-left corner (crop box and /Rotate applied); `font` and `size` are `null` (not reported) |
| `ocr_spans[]` | OCR words: `text`, `bbox` (same convention), `conf` (Tesseract 0–100), `source_type: pdf_ocr`, `region` (`page` = whole scanned page, `image` = picture region of a vector page) |
| `image_count`, `image_regions[]` | Number of images; touching image pieces merged into regions (`bbox`, `pieces`, `share` of the page, `ocr_candidate`) — up to 50, largest first |
| `colours_used[]` | Up to 20 colours with use counts (text fill; path fill and stroke) — recorded, never sole evidence. On pages above 200,000 objects only text colours are read (`colours_complete: false`, warning `LARGE_PAGE`) |
| `callouts` | Empty until the Parsons callout style is known (warning `CALLOUT_STYLE_UNKNOWN`) |
| `ocr` | `null`, or engine, version, language, DPI, tile size, overlap and pages after `ocr_tiles.py` |
| `scanned_rule` | The two values used for the `scanned` flag |

`title_block` (top level) is the title block of the first page that has one, read by `title_block.py`
from the Parsons label layout (`title_blocks.md`): `document_no`, `revision`, `title`, `date`, `status`
(purpose of issue), `sheet`, `scale`, `project_no`, `designed`, `drawn`, `checked`, `approved`,
`fields` (value, value `bbox`, `label_bbox` per field) and `source_ref`. Each page also carries its own
`title_block`. `null` with `TITLE_BLOCK_NOT_FOUND` when no page has the "Drawing No.:" label (cover sheet,
scanned page, other template). `TITLE_BLOCK_FIELD_EMPTY`: a label with no value under it;
`TITLE_BLOCK_MULTIPLE`: pages carry different drawing numbers.

## 2 Scanned-page rule

A page is scanned when it has fewer than 20 vector characters and its images together cover at least
90 % of it (owner approved 5 Oct 2026). Calibrated on SPC-0006 §4.2.6 T3: its pages are seven image
strips of about 14 % each, so a one-image rule missed them. `detect_scanned.py`
lists the evidence per page and what other values would decide.

## 3 OCR

300 DPI, tiles of 2000 px with 100 px overlap, Tesseract `ara+eng`. A word touching an inner tile
edge is dropped from that tile; the same word read in two tiles is kept once. About 35 s per A3 page.
Calibration on T3 page 1 (5 Oct 2026): `ara+eng` read 41 coordinate-like numbers and 412 Arabic
words, `eng+ara` 33 and 384 — `ara+eng` kept. Neither read every value of the coordinate table
(e.g. P1 easting), so OCR'd coordinates need a transcription check; OCR evidence is Probable at most
(SPC-0003).

Picture regions inside vector pages are OCR'd when their shorter side is at least 72 pt and they
cover at least 0.5 % of the page ([PROPOSED] values); OCR words on vector text are dropped. On the
QRWN DMP sheets the only images are logos (906 one-pixel strips merged into one region), so nothing
is OCR'd there.

## 4 Crops

`crop.py` renders the box with pypdfium2 at the given DPI (72–600) and writes a PNG without
metadata; the SHA-256 is stable for the same input and pypdfium2 build.

## 5 Known limits

- Rotated pages: boxes are converted to the displayed page; checked on T1 (rotation 270) with a crop.
- The Verifier's second reader (pdfplumber) is not used on rotated pages or pages above 200,000
  objects; such findings stay `not_rechecked`.
- Arabic vector text may come out in visual order or with presentation forms, as stored in the PDF.
- Damaged content streams: the page is recorded with `error` and `PAGE_UNREADABLE`; other pages are read.
- Backends: pypdfium2 / PDFium (Apache-2.0 / BSD) reads and renders; pdfplumber (MIT) is only the
  Verifier's second reader — it ran out of memory (8 GB) on T1 (1.87 million objects). PyMuPDF (AGPL)
  is not used until its licence review closes (SPC-0006 A.3).

## 6 Pictures — what is not understood

The skill reads **text** in pictures (OCR), never their **shapes**. A parcel, road or plan drawn only as
a picture gives its labels and numbers, but no boundary, centreline, width or area. Two later options
are recorded as [GAP] (owner, 5 Oct 2026), not built: (a) visual reading of crops by the agent model,
evidence `pdf_raster_visual`, Probable at most; (b) raster shape extraction (e.g. OpenCV contours of a
filled parcel, georeferenced by OCR'd coordinates) — accuracy limited by the scan (about 0.2 m per
pixel at 1:2500 and 300 DPI), so gross checks only. The DWG of the same sheet stays the measurable source.
