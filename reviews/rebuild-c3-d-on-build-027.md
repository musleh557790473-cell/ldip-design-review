# Rebuild C3+D onto build-027 (Cursor remediation)

| | |
|---|---|
| Date | 20 Oct 2026 |
| Owner | «نعم اعتمد» — Claude remediation; §4.13H + DK-1…DK-5 already approved in Cursor |
| Base branch | `claude/build-027-c1-c2` tip `1caf667` (C1+C2 head `f6d93ca`) |
| Rebuild branch | `cursor/rebuild-c3-d-on-build-027-0f89` |
| Target | draft PR → `main` (includes build-027 ancestry + C3+D+ROD-043+related) |
| Visibility PR | [#8](https://github.com/musleh557790473-cell/ldip-design-review/pull/8) — **leave open**; do not merge as-is |

## Cite

- SPC-0001 Rev J §6.5 row 10 (A → B1 → B2 → C1 → C2 → C3 → D; **0.10.0 when skill 10 complete**)
- SPC-0006 Rev H/I (C1/C2 on build-027); Rev J §4.13G / DJ-* (C3); §4.13H + DK-1…DK-5 (D)
- Claude remediation (20 Oct 2026): land C1+C2 first; rebuild C3/D; treat premature 0.10.0 as experimental

## Version decision

| Artifact | This rebuild | Rationale |
|---|---|---|
| Skill 10 / `build_candidates.__version__` | **0.7.0** | Same as original C3 draft-build; **does not** claim skill-complete **0.10.0** |
| Plugin `plugin.json` | **0.9.0** | Unchanged from B2 / build-027 base |
| SPC-0001 **0.10.0** | **Reserved** | Until skill 10 is complete per §6.5 (activation / owner gates still open) |
| Premature `0.10.0` on `main` (`c88ff7a`) | **Experimental / superseded** by this rebuild | Remedia­tion «نعم اعتمد» |

`build_candidates` still accepts `surface_checks.json` (D) at 0.7.0 so findings wiring works without a false complete release.

## What was rebuilt (cherry-picks)

| Source | Content |
|---|---|
| `1eeba18` + `66a7a2c` | Session 27 C3 (§4.13G) + PR #1 review fixes / CI |
| `582a31d` | Phase D first slice (§4.13H DK-1…DK-4) |
| *(new)* | Wire D into `build_candidates`; keep **0.7.0** (skip premature 0.10.0 packaging commit) |
| `f5c0773` | ROD-043 params via `--rulebook` / fixture (no full `rules_v7.json`) |
| `9a938c8` (PR #6) | Multi-surface CLI policy + circulatory footprint hook |
| `a1a9e8c` (PR #5) | TJ8 clearer `LDIP_REVIEW` failure |

## Conflict resolution (`check_sections` and friends)

Overlap with build-027 C1+C2: `check_sections.py`, `build_candidates.py`, `SKILL.md`, `road_class_params.json`, `test_revg_build.py`.

**`check_sections.py` (careful merge → 0.3.0):**

1. Kept **C3 parking** (ROD-059 labels, `NO_PARKING` title) and **band-above-title** QRWN path when ROW / `R.O.W.` mark pairs exist.
2. Kept **C2 K25 title-anchor fallback** (`title_bands` / `analyse`) when **no** mark pairs are found — parking-aware `analyse`.
3. Did **not** discard either QRWN strategy; both paths remain.

**`SKILL.md` / `build_candidates.py`:** union of C1+C2 steps (5c/5d) with C3 (5e) and D (5f); version history 0.5.0→0.6.0→0.7.0 (+D surface_checks note).

**`road_class_params.json`:** auto-merged C2 + C3 keys (`c1`/`c2`/`c3`).

## Explicit non-goals

- Do **not** merge visibility PR #8 into `main`
- Do **not** commit full `rules_v7.json` (fixture `tests/fixtures/rod043_excerpt.json` only)
- Do **not** activate C3/D rules / `rules_v8` / 60 % (DJ-9 / R37)
- Do **not** invent circulatory rings from `roundabouts` centre+radius

## Related open drafts

| PR | Action |
|---|---|
| #5 TJ8 | Superseded by this rebuild (same patch included) — comment |
| #6 multi-surface | Superseded by this rebuild — comment |
| #7 C1/C2 restore gap | Obsolete now that build-027 restores scripts — comment |
| #8 build-027 visibility | Leave open |
