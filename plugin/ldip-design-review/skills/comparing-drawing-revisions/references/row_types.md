# CRS row types (DM-5)

Each CRS row is typed **L**, **D** or **G** from the Review Comment / response / Doc. No. text.
The reviewer may override any row with `--types <id,type.csv>`.

Keyword lists below are matched case-insensitively as substrings. Highest hit count wins; ties
prefer L, then D, then G. No hit → **G**.

## L

Location / drawing-specific comments.

- drawing
- sheet
- detail
- layout
- plan
- section
- elevation
- polyline
- alignment
- plot
- title block
- cloud
- revision cloud
- dwg
- pdf sheet

## D

Document / design comments (reports, specs, calculations — not a single sheet).

- specification
- report
- calculation
- design criteria
- design standard
- schedule
- midp
- document
- clause
- paragraph
- table
- workbook
- landxml
- model

## G

General / guidance comments.

- general
- for information
- fyi
- guidance
- note
- overall
- package
- submittal
- editorial
- typographical
- typo
- formatting

## Outcomes (not types)

Outcomes are only **Addressed**, **Not addressed**, or **Cannot tell**. Never Closed or Open
(DM-6). `deferred_by_response` is a separate flag (DM-8). G rows may list `supporting_refs`; that
list never sets the outcome (DM-8).
