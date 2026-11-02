# Wet utilities methods (skill 11 phase A)

SPC-0006 Rev Q §4.19. Detection thresholds: `params.json` (ER connected 83-page edition, DQ-14).

| Check | Content | Notes |
|---|---|---|
| W1 | Plans + hydraulic models per discipline; storm model required (DQ-8); missing calculations MI (DQ-4) | `missing_information`; Minor (PD) / Major (DD); route CRS |
| W2 | Report ↔ model ↔ drawing (L, S, levels, E/N, totals) | DQ-9 tolerances; Confirmed when DWG label vs model; Minor |
| W3 | Gravity sewer geometry: min D, min gradient/D, MH spacing, cover, adverse | Major for gradient / spacing / adverse; model → Probable |
| W5 geom | Pressure min D, cover, hydrant spacing, valve / washout / air-valve presence | Hydraulics (pressure, V, HW C) deferred to phase B |
| W6 plan | Storm diameters, MH spacing from plan, outfalls / ponds; report pipe table; ER conflict Observations (DQ-15) | Applies Table 7-8 / 7-11 until RFI answered |

Findings: `report_only: true`, `reference.rule_id: null` (DQ-11). Agent `WUT`, owner `DMR` (DQ-5). Rule IDs (`WUT-0xx` / `WUT-1xx`) cited in statement text only.
