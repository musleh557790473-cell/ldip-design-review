# Phase D — surface gradient, bay slope, roundabout crossfall/drainage (skill 10)

Contents: 1 Scope · 2 Sampling · 3 Checks · 4 ROD-043 [GAP] · 5 Limits

Spec: AICC-BIM-SPC-0006 §4.13H (owner-approved draft; first slice DK-1…DK-4). Skill/plugin stay at **0.7.0** / **0.9.0** on this rebuild (SPC-0001 **0.10.0** reserved until skill 10 is complete; premature 0.10.0 on `main` was experimental). C3/D rules are **not** activated here (DJ-9 / R37); no `rules_v8`.

## 1 Scope (DK-1)

| Rule | Parameter in D | Out of D |
|---|---|---|
| ROD-056 | `gradient` ≤ 2 % on refuge outline | width / length / tier-2 (C3) |
| ROD-062 | `slope` ≤ 2 % on accessible bay polygon only | counts / schedule (C3); aisle (ROD-061) |
| ROD-043 | `crossfall` + `drainage` | **apron** (60 % path) |

Inputs: C3-accepted polygons (`network.c3_refuges` / `c3_accessible_bays` / `c3_crossfall_footprints` or `--elements`); LandXML TIN side file (`landxml_surface.schema.json`) via `--landxml` or `--surface`.

## 2 Sampling (DK-4)

1. Load TIN `points` `[id, E, N, Z]` and `faces` (3 point ids).
2. Keep faces whose **2D centroid** lies inside the element polygon.
3. Face slope % from plane \(z = ax + by + c\): \(100 \times \sqrt{a^{2}+b^{2}}\).
4. Reported value = **maximum** face slope % (conservative).
5. No surface / uncovered / no element → `not_run`.
6. Surface-derived values → *Probable* (`link_cap`).
7. Several design surfaces and no `--surface-index` → `not_run` ([GAP] multi-surface choice).

## 3 Checks

| Script | Output |
|---|---|
| `tin_surface.py` | Shared TIN load + max face slope |
| `check_surfaces.py` | `surface_checks.json` — `checks` / `passed` / `not_run` |

Feed `surface_checks.json` to `build_candidates.py` with other result files.

## 4 ROD-043 [GAP]

`rules_v7.json` is **not in Git** (local deposit; sha256 recorded in SPC-0004 Rev J). Self-hosted worker offline — statement/parameters for ROD-043 were not available at build time.

| Part | Behaviour without rules file params |
|---|---|
| Apron | Always `not_run` (out of D) |
| Crossfall | `not_run` — Missing information (rule parameters) [GAP] |
| Drainage | `not_run` — Missing information (rule parameters) [GAP] |
| Footprint source | [GAP] until rule text names circulatory vs apron ring |

When parameters later appear on the rule object, the script compares max TIN face slope (crossfall) and applies only named drainage parameters — it does **not** invent radii or reuse ROD-107.

## 5 Limits

- Code ships C3+D on build-027 at skill **0.7.0** (not claiming 0.10.0); no C3 / ROD-043 activation and no `rules_v8` (DJ-9 / R37)
- Design-profile `grade_at_station` is **not** the island/bay surface metric
- ROD-021 section crossfall ≠ ROD-043
- Real 30 % sites: expect D surface params `not_run` or no element (no false D findings)
