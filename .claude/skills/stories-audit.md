---
name: stories-audit
description: Read-only story-to-software fidelity report — reports declared evidence that no longer resolves, claims unsupported by evidence, and intent that contradicts deterministic evidence.
---

# stories-audit

Answers: do existing stories still accurately describe the software
surfaces and evidence they claim?

Deterministic findings: `surface-missing`, `test-evidence-missing`,
`claim-unsupported`, `intent-conflict` (severity fixed high). Optional
narrative pass (D-pass) emits `claim-contradicted`, `story-ambiguous`,
`documented-untested`. Repo-level `agent-pointer-missing` (low) when at
least one root agent-instruction file exists and none reference the
storystore convention.

Modes: `--story <slug>` (scoped, repeatable), `--bump-clean` (writes
`last_audited` for stories with zero findings in the run), `--thorough`
(non-TS coverage with `--inferred-surface <path>`), `--strict` (exit 1
on any findings), `--source-root` and `--include-dir` for monorepo
scoping.

**Status:** Implementation deferred to Plan 2. See `spec.md` and
`2026-05-01-storystore-plan-2-fidelity.md` for the full contract.
