---
name: stories-coverage
description: Read-only software-to-story coverage report — reports user-facing surfaces lacking story coverage, untested active stories, and stories below a completeness threshold.
---

# stories-coverage

Answers: **what user-facing software behavior lacks story coverage?**

This skill is **read-only**. It builds the surface inventory under the
repo root, loads `docs/stories/<slug>.md`, and reports coverage gaps. It
never edits, creates, or deletes story files.

## Command

```bash
stories-coverage/scripts/coverage.py \
  --repo-root <repo-root> \
  [--strict] \
  [--surface-kind <kind>]... \
  [--completeness-min-rating <rating>] \
  [--completeness-limit <n>] \
  [--thorough --inferred-surface <path.json>] \
  [--source-root <subdir>] \
  [--include-dir <name>]... \
  [--report-path <path>] \
  [--perf-warn-ms <ms>]
```

The script is materialized into the skill's `scripts/` directory at
publish time. From a checkout of this repo, the equivalent invocation is
`python3 shared/coverage.py --repo-root <repo-root>`.

Flags:

- `--repo-root` — required. Repo root containing `docs/stories/`.
- `--strict` — exit 1 if any findings exist (default exit is 0 even with
  findings).
- `--surface-kind <kind>` — repeatable. Restrict the surface kinds
  considered. Defaults to `cli-command`, `http-route`, `bin`, `schema`,
  `copy`. Public exports are opt-in via this flag.
- `--completeness-min-rating <rating>` — rating floor below which active
  stories are reported `story-incomplete`. One of `skeletal`, `sparse`,
  `partial`, `substantial`, `complete`; default `substantial`.
- `--completeness-limit <n>` — cap on `story-incomplete` findings, worst
  first. Default 20.
- `--thorough` — opt-in coverage of non-TypeScript surfaces. Required to
  use `--inferred-surface`.
- `--inferred-surface <path>` — JSON file of inferred surface entries;
  requires `--thorough`.
- `--source-root <subdir>` — relative subtree to walk for the inventory
  (monorepo scoping).
- `--include-dir <name>` — repeatable. Directory name to pull back into
  the inventory walk.
- `--report-path <path>` — where to write the markdown report. Defaults
  to `/tmp/stories-coverage-<epoch>.md`.
- `--perf-warn-ms <ms>` — override the stderr perf-warn threshold
  (default 5000; env `STORYSTORE_PERF_WARN_MS`). `0` disables it.

## Findings

- `surface-uncovered` — a user-facing surface has no story claiming it.
- `story-untested` — an active story has no resolving test evidence.
  Suppressed by `tests_applicable: false` in the story.
- `story-incomplete` — an active story rates below
  `--completeness-min-rating`. Completeness scores 0–50 across five
  non-Intent dimensions and maps to ratings Skeletal / Sparse / Partial /
  Substantial / Complete.

## Output

The skill writes a markdown report to `--report-path` and prints a JSON
summary to **stdout**:

```json
{
  "findings_count": 4,
  "performance": {
    "duration_ms": 1,
    "stories_scanned": 2,
    "surfaces_scanned": 0,
    "phase_breakdown": {"build_inventory": 0, "coverage_stories": 0, "coverage_surfaces": 0, "load_stories": 0}
  },
  "report_path": "/tmp/stories-coverage-1781559582.md"
}
```

The markdown report opens with a `Language Coverage` block, then one
section per finding with `kind` and `story_slug`. Read the report path
from `report_path` in the JSON summary.

## Exit codes

```text
0  success (default — emitted even when findings exist)
1  findings present (only with --strict)
2  invalid input, missing docs/stories, or malformed story
4+ unexpected runtime error
```

A stderr perf-warning fires when the run exceeds the perf-warn threshold.

## Worked example

```bash
# Coverage pass; non-zero exit if any gap exists.
stories-coverage/scripts/coverage.py --repo-root . --strict

# Only HTTP routes, flag any active story below "complete".
stories-coverage/scripts/coverage.py --repo-root . \
  --surface-kind http-route --completeness-min-rating complete
```

## Cross-references

- Implementation contract: `shared/spec.md` and
  `2026-05-01-storystore-plan-2-fidelity.md`.
- Companion fidelity report: `stories-audit`.
