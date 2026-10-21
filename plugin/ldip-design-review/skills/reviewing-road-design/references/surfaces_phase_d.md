# Phase D — surface gradient, bay slope, roundabout crossfall/drainage (skill 10)

Contents: 1 Scope · 2 Sampling · 3 Multi-surface · 4 Checks · 5 ROD-043 parameters · 6 Footprint · 7 Limits

Spec: AICC-BIM-SPC-0006 §4.13H (owner-approved draft; first slice DK-1…DK-4). Packaged in plugin / skill **0.10.0** with C3 (SPC-0001 Rev K / DJ-10 / DK-5). **C3 and D rules are NOT activated** until the 60 % package (R37 / DJ-9); no `rules_v8`.

## 1 Scope (DK-1)

| Rule | Parameter in D | Out of D |
|---|---|---|
| ROD-056 | `gradient` ≤ 2 % on refuge outline | width / length / tier-2 (C3) |
| ROD-062 | `slope` ≤ 2 % on accessible bay polygon only | counts / schedule (C3); aisle (ROD-061) |
| ROD-043 | `circulatory_crossfall` + `drainage_g_min` | **apron** / `apron_width` (60 % path) |

Inputs: C3-accepted polygons (`network.c3_refuges` / `c3_accessible_bays` / `c3_crossfall_footprints` or `--elements`); LandXML TIN side file (`landxml_surface.schema.json`) via `--landxml` or `--surface`.

## 2 Sampling (DK-4)

1. Load TIN `points` `[id, E, N, Z]` and `faces` (3 point ids).
2. Keep faces whose **2D centroid** lies inside the element polygon.
3. Face slope % from plane \(z = ax + by + c\): \(100 \times \sqrt{a^{2}+b^{2}}\).
4. Reported value = **maximum** face slope % for ≤ limits (056/062/crossfall); **minimum** for `*_min` / drainage grade ≥.
5. No surface / uncovered / no element → `not_run`.
6. Surface-derived values → *Probable* (`link_cap`).

## 3 Multi-surface CLI policy (DK-4)

Rev J / §4.13H.3 do **not** name which surface to prefer when `content.surfaces[]` length > 1. No FG/name heuristic is invented.

| Case | Behaviour |
|---|---|
| 0 surfaces | `not_run` — no design surface in the package |
| **1 surface** | **CLI default** — use that sole entry (no `--surface-index` needed) |
| **>1 surfaces**, no index | `not_run` — pass `--surface-index <content.surfaces[].index>` |
| `--surface-index N` | Pick the surface whose `index` equals N |
| `--surface <tin side file>` | Bypass LandXML multi-surface choice entirely |

## 4 Checks

| Script | Output |
|---|---|
| `tin_surface.py` | Shared TIN load + max/min face slope |
| `check_surfaces.py` | `surface_checks.json` — `checks` / `passed` / `not_run` |

Feed `surface_checks.json` to `build_candidates.py` with other result files.

Optional rulebook for ROD-043 numbers (does **not** activate inactive rules):

```text
--rulebook <rules_vN.json|rod043_excerpt.json>
# or env: LDIP_RULEBOOK / LDIP_RULES_V7
```

## 5 ROD-043 parameters

Full `rules_v*.json` is **not in Git** (gitignored; local / Project-store deposit). CI uses `tests/fixtures/rod043_excerpt.json` — **synthetic** values that exercise the loader shape only (deliberately **not** deposited rulebook numbers). `tools/check_no_data.py` blocks deposited ROD-043 text/numeric fingerprints.

| Parameter | Source | D behaviour |
|---|---|---|
| `apron_width` | deposited rulebook (not in Git) | Always `not_run` (out of D) |
| `circulatory_crossfall` | deposited rulebook / `--rulebook` | Max TIN face slope ≤ limit on **circulatory** footprint |
| `drainage_g_min` | deposited rulebook / `--rulebook` | Min TIN face slope ≥ limit on same footprint |

Without `--rules` params and without `--rulebook` / `LDIP_RULEBOOK` → `not_run` + *Missing information* (rule parameters). Numbers are never invented. **Loading a rulebook does not change `state`** — ROD-043 stays **inactive** until 60 % / R37.

## 6 Crossfall / drainage footprint

**Settled from ROD-043 parameter naming:** the parameter is named `circulatory_crossfall` (statement refers to circulatory crossfall). Apron is a separate parameter and is **out of D** (SPC-0004 §4.8E / DK-3). So the D footprint kind is **circulatory**, not the apron ring.

| Intake | Role |
|---|---|
| `network.c3_crossfall_footprints` | Best documented hook (same pattern as `c3_refuges` / `c3_accessible_bays`) |
| `--elements.crossfall_footprints` | Override / synthetic supply |
| Optional `kind` | `circulatory` (default when unmarked); `apron` / `apron_ring` → ignored in D |

**Not used (would invent):** `network.roundabouts[].centre` + `radius` alone. `build_network.py` rebuilds a temporary ring then **drops** it before write; carriageway width is not in the deposit. No buffer width is invented here.

**Remaining [GAP]:** nothing on `main` yet *produces* circulatory outline polygons into `c3_crossfall_footprints` from plan/C3. Until a producer exists (or `--elements` supplies them), crossfall/drainage surface checks stay `not_run` with that reason — no false findings.

## 7 Limits

- Packaged in **0.10.0** (code only; SPC-0001 Rev K); **C3 and D rules are NOT activated** until the 60 % package (R37 / DJ-9); no `rules_v8`
- Design-profile `grade_at_station` is **not** the island/bay surface metric
- ROD-021 section crossfall ≠ ROD-043
- Real 30 % sites: expect D surface params `not_run` or no element (no false D findings)
