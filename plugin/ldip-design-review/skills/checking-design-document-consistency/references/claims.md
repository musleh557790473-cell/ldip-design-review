# Report claim patterns (skill 16 — SPC-0006 Rev N §4.16; DN-2)

`read_report.py` locates headings, then matches these claim families in skill-2 PDF text.
Patterns are detection aids only — not rulebook values.

## Section headings

| Section | Heading regex (case-insensitive) |
|---------|----------------------------------|
| earthworks | `earthworks?`, `cut\s*(and|&|/)\s*fill`, `grading`, `mass\s+haul` |
| pavement | `pavement`, `road\s+construction`, `flexible\s+pavement`, `wearing\s+course` |
| geotechnical | `geotech`, `ground\s+condition`, `foundation`, `soil` |

## Claim families (C2 / C3 / C4)

| Family | Tokens / regex | Check |
|--------|----------------|-------|
| `balanced` | `\bbalanced\b`, `cut\s*(and|&)\s*fill\s+balanc`, `earthworks?\s+balanc` | C3 |
| `import` | `\bimport(?:ed)?\s+(?:fill|material)\b`, `\bborrow\b` | C3 |
| `export` | `\bexport(?:ed)?\b`, `\bdispos(?:al|ed)\s+off[- ]?site\b` | C3 |
| `basis_ratio` | `0\.2|20\s*%`, `0\.8|80\s*%`, `0\.6|60\s*%` near rock / suitable / disposal | C2 |
| `pavement_depth` | `(\d+(?:\.\d+)?)\s*mm` near pavement / wearing / ToS / thickness | C4 |

## Notes

- Page numbers come from skill-2 `content.pages[].page`.
- OCR spans (`source_type: pdf_ocr`) are accepted but capped at Probable evidence elsewhere.
- Absence of a claim is itself evidence for C2 (“basis not stated”).
