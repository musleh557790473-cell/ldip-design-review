# build-027 (C1 + C2) — push to GitHub

Session 27, 20 Oct 2026. Branch `claude/build-027-c1-c2`, head `f6d93ca`.

- Source: `S26-revI-build-review/build-027.bundle` (sha256 `216e2f18…6757`, matches session 26 log), head `4822e69`.
- Its base, local v0.9.0 `c282db7`, has a tree identical to GitHub `cfa4b20`. The six commits (`0f7c4e5`, `72865fc`, `6688dd9`, `6f07903`, `9520fb4`, `4822e69`) were cherry-picked onto `cfa4b20` without change. Final tree = build-027 tree (checked).
- `check_no_data`: 0 blocked. Suite: 482 passed / 27 skipped / 1 failed (ezdwg not installed in the container; same total as session 26).
- Bundle `build-027-c1-c2.bundle` sha256 `7a2e4ad0…4c42`, prerequisite `cfa4b20` (on GitHub).

## Push (run once, in any local clone of the repository)

```
git fetch origin
git fetch "<path>/build-027-c1-c2.bundle" claude/build-027-c1-c2:claude/build-027-c1-c2
git push origin claude/build-027-c1-c2
```

Do **not** merge this branch into `main` directly. Cursor rebases its C3 work onto it in a new PR (see the session 27 review / remediation note).
