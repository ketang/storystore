---
name: stories-audit
description: Read-only story-to-software fidelity report — reports declared evidence that no longer resolves, claims unsupported by evidence, and intent that contradicts deterministic evidence.
---

# stories-audit

Answers: **do existing stories still accurately describe the software
surfaces and evidence they claim?**

This skill is **read-only**. It walks `docs/stories/<slug>.md` under the
repo root and reports fidelity findings. It never edits, creates, or
deletes story files — except `--bump-clean`, which writes `last_audited`
on stories that finish the run with zero findings.

## Command

```bash
stories-audit/scripts/audit.py \
  --repo-root <repo-root> \
  [--story <slug>]... \
  [--strict] \
  [--bump-clean] \
  [--thorough --inferred-surface <path.json>] \
  [--source-root <subdir>] \
  [--include-dir <name>]... \
  [--report-path <path>] \
  [--perf-warn-ms <ms>]
```

The script is materialized into the skill's `scripts/` directory at
publish time. From a checkout of this repo, the equivalent invocation is
`python3 shared/audit.py --repo-root <repo-root>`.

Flags:

- `--repo-root` — required. Repo root containing `docs/stories/`.
- `--story <slug>` — repeatable. Scope the audit to one or more story
  slugs. Scoped runs skip the repo-level `agent-pointer-missing` check.
- `--strict` — exit 1 if any findings exist (default exit is 0 even with
  findings).
- `--bump-clean` — write today's date to `last_audited` for every story
  with zero findings in this run. This is the only state the audit
  mutates.
- `--thorough` — opt-in coverage of non-TypeScript surfaces. Requires
  `--inferred-surface`.
- `--inferred-surface <path>` — JSON file of inferred surface entries,
  used with `--thorough`.
- `--source-root <subdir>` — relative subtree to walk for the surface
  inventory (monorepo scoping).
- `--include-dir <name>` — repeatable. Directory name to pull back into
  the inventory walk.
- `--report-path <path>` — where to write the markdown report. Defaults
  to `/tmp/stories-audit-<UTC-timestamp>.md`.
- `--perf-warn-ms <ms>` — override the stderr perf-warn threshold
  (default 5000; env `STORYSTORE_PERF_WARN_MS`). `0` disables it.

## Findings

Deterministic findings (always emitted):

- `surface-missing` — a declared surface ref resolves to nothing in the
  extracted inventory.
- `test-evidence-missing` — a declared test-evidence path no longer
  resolves.
- `claim-unsupported` — an auditable claim has no supporting evidence.
- `intent-conflict` — stated intent contradicts deterministic evidence.

Repo-level: `agent-pointer-missing` (low) fires when at least one root
agent-instruction file exists and none reference the storystore
convention. Skipped in `--story`-scoped runs.

Optional narrative pass (D-pass, when configured) adds
`claim-contradicted`, `story-ambiguous`, and `documented-untested`.

## Output

The skill writes a markdown report to `--report-path` and prints a JSON
summary to **stdout**:

```json
{
  "report_path": "/tmp/stories-audit-20260615T213942Z.md",
  "findings_count": 2,
  "performance": {
    "duration_ms": 2,
    "stories_scanned": 2,
    "evidence_refs_resolved": 3,
    "phase_breakdown": {"load_stories": 0, "build_inventory": 1, "resolve_evidence": 0}
  }
}
```

The markdown report opens with a `Language Coverage` block, then one
section per finding with `kind`, `story_slug`, `severity`, and
`suggested_action`. Read the report path from `report_path` in the JSON
summary.

## Exit codes

```text
0  success (default — emitted even when findings exist)
1  findings present (only with --strict)
2  invalid input or malformed story
3  validity-matrix violation (raised by storystore_lib)
4+ unexpected runtime error
```

A stderr `STORYSTORE_PERF_WARN` line fires when the run exceeds the
perf-warn threshold.

## Worked example

```bash
# Full audit; non-zero exit if anything is wrong.
stories-audit/scripts/audit.py --repo-root . --strict

# Audit a single story and bump its last_audited if clean.
stories-audit/scripts/audit.py --repo-root . --story login --bump-clean
```

## Cross-references

- Implementation contract: `shared/spec.md` and
  `2026-05-01-storystore-plan-2-fidelity.md`.
- Companion coverage report: `stories-coverage`.
