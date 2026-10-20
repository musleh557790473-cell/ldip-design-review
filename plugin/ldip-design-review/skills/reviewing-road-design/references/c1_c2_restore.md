# C1 / C2 restore inventory (optional restore, 20 Oct 2026)

Contents: 1 Verdict · 2 Searched · 3 Recovered · 4 Still missing · 5 C3 corner-returns contract · 6 Rules touched by C1/C2 · 7 How to finish restore

Spec context: SPC-0001 Rev J §6.5 row 10 (C1 then C2 then C3); SPC-0006 Rev H §4.13E (C1), Rev I §4.13F (C2); rulebook Rev J §4.8E. **This note does not activate C1/C2/C3 rules or the 60 % package.**

## 1 Verdict

**No C1/C2 Python scripts or prior TI/TH test suites were recoverable** from GitHub `main`/history, the Project store, or prior cloud-agent transcripts. Nothing was invented. C3 already accepts corner returns as JSON (see §5); that consumer contract is what remains in this tree.

## 2 Searched (no script source found)

| Source | Result |
|---|---|
| GitHub `musleh557790473-cell/ldip-design-review` — all commits / trees | No `check_layout.py`, `check_geometry.py`, `c2/` package, or corner-return writer. Skill 10 jumped B2 (0.9.0) → C3 (session 27) → D → 0.10.0 |
| Cited local commit `4822e69` (`check_layout.py` 0.1.1 in rulebook Rev J) | **Not present** in this repository |
| Project store `/cursor/stores/self/` | Specs and notes only; prior explore (`c3-patterns-explore.md`) already recorded absence |
| Cloud agents (incl. “Fetch C1/C2 code patterns”) | Confirmed absent from checkout |
| OneDrive Claude outputs (named in deposit report) | `S25-revH-build-review`, `S26-revI-build-review`, `reviews/S26-revI-build-changeset.md`, `c2._impl` — **referenced, never deposited** into the store or Git |

## 3 Recovered (faithful, non-invented)

| Artifact | Where |
|---|---|
| C3 consumer of C2 corner returns | `scripts/check_pedestrian.py` (`--corner-returns` or `network.corner_returns`) |
| Synthetic shape used by TJ5 | `tests/test_revj_build.py` → `network.corner_returns[]` with `junction` + `tangent_point` |
| File-form shape | `--corner-returns` JSON with top-level `returns` (same object fields) |
| Example fixture (documented contract only) | `tests/fixtures/corner_returns_example.json` |
| Named scripts / modules (from specs, not recovered code) | `check_layout.py`, `check_geometry.py`, `c2._impl` |
| Session / build labels | Session 25 = C1 (skill 0.5.0); session 26 = C2 (skill 0.6.0); `build-027`; TI1–TI9 |

## 4 Still missing (do not invent)

| Item | Notes |
|---|---|
| `check_geometry.py` (C1) | Alignment / junction geometry per Rev H §4.13E |
| `check_layout.py` (C2) | Junction / roundabout layout + corner returns per Rev I §4.13F |
| `c2` / `c2._impl` package | Values [AGREED] in Rev J text; source not in Git |
| Writers that emit corner-return JSON into `network.json` | C3 can still be fed JSON by hand / fixture |
| `tests/test_revh_*.py` / `test_revi_*.py` (TH* / TI1–TI9) | Not in this repo; only C3 TJ* and earlier Rev F/G tests exist |
| Change set `reviews/S26-revI-build-changeset.md` | Named in rulebook; not deposited |

## 5 C3 corner-returns contract (from existing C3 code)

**CLI:** `--corner-returns <file.json>` → reads `returns` array.  
**Or** `network.json` field `corner_returns` (array).

Each return object (fields actually read by `check_pedestrian.py`):

| Field | Role |
|---|---|
| `junction` | Optional; if set, must match the crosswalk’s junction `id` |
| `tangent_point` or `xy` | `[E, N]` plan point of the radius-return tangent |

ROD-055 setback: distance along the road from the crosswalk near edge to the nearest matching tangent point; required range **2.0–3.0 m**. If no returns are given, setback is `not_run` (not a false fail).

## 6 Rules touched by C1/C2 (from deposited `rules_v7` reasons — not Git)

**C1 (session 25 / skill 0.5.0):** ROD-019, 020, 035, 036 (advisory), 037 (inactive).  
**C2 (session 26 / skill 0.6.0):** ROD-032, 033–034 (inactive), 039, 040 (advisory), 042, 043 (inactive; D owns crossfall/drainage scaffolding).

Rule **activation / 60 %** is out of scope for this restore.

## 7 How to finish restore

1. Deposit onto the Project store (or attach to a follow-up) the local trees  
   `S25-revH-build-review`, `S26-revI-build-review`, and `reviews/S26-revI-build-changeset.md` from Claude outputs.
2. Copy only scripts/tests that match those trees; do not re-implement from the rulebook alone.
3. Wire any recovered `test_revh_*` / `test_revi_*` (or TI/TH) into CI the same way as `test_revj_*`.
4. Keep C3 JSON corner-returns path until C2 can write `network.corner_returns` itself.
