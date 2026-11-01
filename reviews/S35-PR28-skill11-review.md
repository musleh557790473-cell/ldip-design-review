# S35 — PR #28 review: skill 11 `reviewing-wet-utilities` 0.1.0 phase A (head `e130938`)

| Item | Value |
|---|---|
| Date | 1 Nov 2026 22:40 (session 35) |
| Base | `main` `d6e33ab` (`v0.14.0`) |
| Basis | SPC-0006 Rev Q §4.19 APPROVED (DQ-1 … DQ-15); prompt `reviews/S35-cursor-prompt-skill11-0.1.0.md`; probe `reviews/S35-skill11-wet-utilities-probe.md` |
| **Verdict** | **Changes requested.** Suite clean, but the readers are built for invented formats: on the owner's real models and drawings they read nothing |

## 1. Scope and hygiene — OK

- 19 files, +3,674 / −6: the skill folder, `tests/`, the finding-schema agent enum (`WUT` added to `agent` and the `finding_id` pattern), CI `--expect 737`. No other skill, no rulebook, no `plugin.json` ✔.
- Suite (Claude): **737 collected, 674 passed / 63 skipped / 0 failed** ✔. `check_no_data --all` 0 ✔.

## 2. Real data (owner models / DWGs, scratch)

| Test | Result | Cause |
|---|---|---|
| T11-R1 NMAR sewer | **FAIL** — `unrecognised Bentley sqlite schema (no Physical / Haestad tables)` | R1 |
| T11-R2 QRWN sewer | **FAIL** — same | R1 |
| T11-R3 NRJS sewer | **FAIL** — same | R1 |
| T11-R4 NMAR PW | **FAIL** — same | R1 |
| T11-R5 storm model missing | pass — but it builds its own presence dict and empty network; it tests no real data | R5 |

`read_network_plan.py` on the NMAR plans (skill 1 DXF): sewer `SWN-10000-000` → **1 node, 0 pipe labels** (probe: 164 node labels, 164 `L= / S=` labels); storm `STO-10000-000` → 24 "pipes" (probe: 136 segments, 5.91 km by diameter layer).

## 3. Required changes (blocking)

| # | Item | Detail |
|---|---|---|
| **R1** | Bentley reader does not read the real schema | `read_bentley_sqlite.py` expects synthetic tables `Elements / Links / Physical / Alternatives`, then a heuristic "discovery". The prompt §2 listed the **real tables** exactly: `HMIModelingElement` (`IsDeleted = 0`, `Label`); sewer `Conduit`, `Conduit_Physical_Data.ConduitDiameter`, `PhysicalLink_Physical_Data.Physical_UpstreamInvert / Physical_DownstreamInvert`, `BaseLink_HMIDataSetTopology_Data.HMITopologyStartNodeID / HMITopologyStopNodeID`, `BaseLink_HmiDataSetGeometry_Data.HMIGeometryScaledLength`, `GravityLinkBase_Physical_Data` (material, `Physical_ManningsN`), ground levels `BaseNode_Physical_Data` / `BaseDirectedNode_Physical_Data.Physical_GroundElevation`; water / irrigation `IdahoPipe`, `IdahoPipe_Physical_Data` (`Physical_PipeMaterial`, `Physical_PipeDiameter`, `Physical_HazenWilliamsC`), `BaseIdahoNode_Physical_Data.Physical_NodeElevation`. Rewrite against these tables. Rebuild the synthetic fixture with the **same table and column names** (not `Elements / Physical`) |
| **R2** | Unit guessing | Remove every heuristic ("if > 5 treat as inches", "if > 1000 assume feet", "keep if 400–800"). DQ-3: storage is **US feet, always** → m (× 0.3048), diameters mm (× 304.8). One rule, no detection |
| **R3** | Plan layers invented | `wet_layers.json` holds corridor-section layers (`-02- Sewerage`, `-05- Hydrant` …) and invented ones (`SWN-PIPE`, `C-PIPE-200`). The real NMAR plan layers (probe §6, drawn from the DXFs) are, short name after the last `$`: sewer pipes `CI-Ss_50_35_08_30-P-SS_Pipe Dia <D>`, sewer labels `CI-Ss_50_35_08_30-P-SS_Annotation Main Sewer`, manholes `C-SSWR-MNHL-P`; storm pipes `CI-Ss_50_35_08_85-M-SW_Pipe Dia <D>` (400 … 1100), catch basins `Xref-Storm CB` / `HP` / `LP`, outlets `outlet Stru`, catchments `Catchments`; water pipes `%%c<D>mm` (e.g. `%%c160mm`), `C-PW-BUTTERFLY VALVE`, `C-PW-VALVE`, hydrants `C-PW-FIRE HYDRANT COVER CATCH` (block `Fire Hydrant`). Diameter comes from the layer (`Pipe Dia (\d+)`, `%%c(\d+)mm`). Use these; remove invented names. QRWN / NRJS: read the layer list from their plans in the real tests and add what differs (per-site entries) |
| **R4** | Plan label parsing | Real node MTEXT is `<ID>\P CL: 779.45m\P IL: 777.40m\P E: 656670.49\P N: 2714078.99` (colon, `m` suffix, paragraph breaks — after `plain_text()` these are newlines). Real pipe labels are `L=63.01\PS=1.50%` and `200L=7.58\PS=0.63%` (diameter prefixed, no space). Parse both; join on newlines. Pipes on the plan are counted from the labels (W2), lines only for length by diameter (storm) |
| **R5** | Real tests incomplete / not real | Add the values the prompt §7 listed and assert them **as fixed numbers**: T11-R1 links > 80 m = **38**, W2 report 9.70 km / 173 MH / 1.40 – 3.50 m vs model 9.26 / 164 / 1.70 – 3.80, plan labels **149 / 164** identical (L, S), force-main label **Ø1000 = 1**; T11-R2 slope below ER **241**, links > 80 m **310**; T11-R3 links > 80 m **3**; T11-R4 ID **131 / 167 / 204** mm, C 140; T11-R5 must run on the real NMAR report storm table (P-055 0.18, P-118 0.63, P-119 1.15 m; P-092 110.6, P-088 100.7 m) and the real presence (no storm model). `w3_spacing_flags: 0` in the current T11-R1 contradicts the ER value (80 m) in `params.json` — it must be 38 |
| **R6** | Values not in the ER | `params.json` sets sewer `cover_m: 1.2` and water `min_diameter_mm: 100` as ER values. The ER gives neither (sewer cover: "sufficient … special protection if shallow", §7.5.2; water min D not stated). Per DQ-7 use the **declared** values where the ER is silent and label them `source: declared` with the report clause (sewer soffit < 1.0 m → protection, §6.3.3; water min D 110 / 160 mm, §5.3.7). Water cover 1.2 m is ER "~1.2 m in roads" → keep, advisory |
| **R7** | Criteria reader untested on the real report | `read_wet_criteria.py` has no real test. Add a gated test on the NMAR `DES-RPT-00001` skill-2 text: declared tables (PW §5.3, sewer §6.3 incl. Table 6.1 / 6.3, storm §10.2), totals (PW ≈ 11 km, 2.76 / 5.27 bar; sewer 9.70 km, 173 MH, 1.40 – 3.50 m) and the storm Table 10.9 rows split across pages |

## 4. Minor

- SKILL.md says "WUT-001…049 … cited in finding text only" — fine; keep it consistent with DQ-11.
- `wet_layers.json` tag says [PROPOSED]; DQ-6 is [AGREED 21:55].
- Governance note for the owner: the clone was served as a **public** repository (proxy note). The project record (30 Sep) says Private. Check the repository visibility in GitHub settings.

## 5. Next

Cursor updates PR #28 in place (prompt `reviews/S35-cursor-prompt-PR28-fixes.md`). Claude re-runs T11-R1 … R5 on the owner data.
