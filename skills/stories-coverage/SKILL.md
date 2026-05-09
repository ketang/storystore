---
name: stories-coverage
description: Read-only software-to-story coverage report — reports user-facing surfaces lacking story coverage, untested active stories, and stories below a completeness threshold.
---

# stories-coverage

Answers: what user-facing software behavior lacks story coverage?

Deterministic findings: `surface-uncovered`, `story-untested` (suppressed
by `tests_applicable: false`), `story-incomplete` (active stories below
`--completeness-min-rating`, default `substantial`, capped by
`--completeness-limit`, default 20, worst first).

Default surface kinds: `cli-command`, `http-route`, `package-bin`. Public
exports are opt-in. Reports begin with a Language Coverage block.
Completeness scores 0-50 over five non-Intent dimensions map to ratings
Skeletal / Sparse / Partial / Substantial / Complete.

**Status:** Implementation deferred to Plan 2. See `spec.md` and
`2026-05-01-storystore-plan-2-fidelity.md` for the full contract.
