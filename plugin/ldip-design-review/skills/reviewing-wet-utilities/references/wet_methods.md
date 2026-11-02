# Wet utilities methods (skill 11 0.1.1)

SPC-0006 Rev Q §4.19. Detection thresholds: `params.json` with **DQ-7a source
tiers** (1 contract ER→DTR→CMP … 5 Parsons declared). Governing = lowest tier
that supplies a value.

| Check | Content | Notes |
|---|---|---|
| W1 | Plans + hydraulic models per discipline; storm model required (DQ-8); missing calculations MI (DQ-4) | `missing_information`; Minor (PD) / Major (DD); route CRS |
| W2 | Report ↔ model ↔ drawing (L, S, levels, E/N, totals) | DQ-9 tolerances; Confirmed when DWG label vs model; Minor |
| W3 | Gravity sewer: min D, min gradient/D, MH spacing, **crown cover / max depth** `GL−(IL+D)`, adverse | Cover <1.2 Minor (confirm protection to 0.5); <0.5 Major; depth >6.0+0.01 Major (CMP Q185); Major for gradient / spacing / adverse |
| W5 geom | Pressure min D, cover, hydrant spacing, valve / washout / air-valve presence | Hydraulics (pressure, V, HW C) deferred; fire-main min D 160 CMP Q166 |
| W6 plan | Storm diameters, MH spacing from plan, outfalls / ponds; report pipe table; ER conflict Observations (DQ-15) | Applies Table 7-8 / 7-11 until RFI answered |
| W8 | Declared (tier 5) vs governing; same-tier CMP≠ER alternatives as Observation | Observation if declared stricter; Minor if weaker; Confirmed; equal/inside-range → no finding |

Findings: `report_only: true`, `reference.rule_id: null` (DQ-11). Agent `WUT`, owner `DMR` (DQ-5). Rule IDs (`WUT-0xx` / `WUT-1xx`) cited in statement text only.
