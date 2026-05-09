---
name: stories-impact-check
description: Pre-change lookup — reports stories affected by a proposed behavioral change to user-facing surfaces, with status, authority, change resistance, and match reasons.
---

# stories-impact-check

Hard trigger before any behavioral change to user-facing surfaces.
Read-only. Returns affected stories with status, authority, change
resistance, locked sections, intent excerpt, and match reasons.

Inputs (cross-dimension OR):

- `--file <path>` (repeatable)
- `--surface <ref>` (repeatable)
- `--description <text>` (at most one; repeating is exit 2)

Behavior table:

```text
active + immutable:    stop and ask before proceeding
active + high:         alert user, ask confirmation
active + medium:       mention affected stories; proceed unless objected
active + low:          mention affected stories after the change
authority: observed:   mention only; do not gate
status: draft:         mention only unless change_resistance high/immutable
status: deprecated:    report as stale match; do not gate
```

The skill surfaces relevant stories; it does not decide whether the
change is allowed. Hook-based enforcement is deferred; the hard trigger
is description-driven only.

**Status:** Implementation deferred to Plan 3. See `spec.md` and
`2026-05-01-storystore-plan-3-edits-and-impact.md` for the full contract.
