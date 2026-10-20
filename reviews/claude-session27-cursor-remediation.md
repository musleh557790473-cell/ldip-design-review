# Session 27 — Cursor remediation (build-027 C1+C2)

| | |
|---|---|
| Date | 20 Oct 2026 |
| Owner | «نعم اعتمد» — Claude remediation plan adopted |
| Branch | `claude/build-027-c1-c2` |
| C1+C2 head (bundle) | `f6d93ca` |
| Branch tip (with `reviews/`) | see tip commit on this branch |
| Base (prerequisite) | `cfa4b20` (v0.9.0 tree on GitHub) |
| Bundle | build-027-c1-c2 (sha256 `7a2e4ad0…4c42`) |

## Owner decisions already locked in Cursor

- **§4.13H** (phase D clause) — approved («نعم اعتمد»), 19 Oct 2026.
- **DK-1…DK-5** — agreed / approved in Cursor (first-slice surface RODs through packaging 0.10.0 authorization). Build of D and C3 rule activation remain gated separately.
- **Claude remediation plan** (20 Oct 2026, «نعم اعتمد»): treat packaged **0.10.0** as experimental/revert until skill 10 is complete; land **build-027 C1+C2** first; then rebuild C3/D onto that base; keep a single source of truth via PRs that cite approved SPC text; write `reviews/` in the repo.

## What this push does

1. Imported the owner-uploaded build-027 C1+C2 bundle and pushed **`claude/build-027-c1-c2`** to GitHub (**do not merge into `main`**).
2. Six SPC-0006 Rev H/I C1+C2 commits cherry-picked onto `cfa4b20` (unchanged trees vs session-26 build-027).
3. Draft PR opened for visibility only — Cursor rebases C3/D onto this branch before any merge (owner/Claude plan).

## Cite

- SPC-0001 Rev J §6.5 row 10 (skill 10 path C1→C2→C3→D).
- SPC-0006 Rev H/I (C1/C2 builds); Rev J §4.13G / DJ-*; §4.13H + DK-1…DK-5.
- build-027 (session 26/27); push notes: `reviews/build-027-c1-c2-push.md`.

## Verification

| Check | Result |
|---|---|
| Bundle import → `claude/build-027-c1-c2` | head `f6d93ca` |
| `git push origin claude/build-027-c1-c2` | succeeded |
| Remote tip | `f6d93ca4e10bc246a1d8f661f692764ae14281c9` |
| Merged to `main` | **no** |
