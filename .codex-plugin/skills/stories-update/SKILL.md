---
name: stories-update
description: Guarded editing for existing stories — runs scoped audit before edits, blocks silent updates on stories with audit findings, and enforces locked-section and meaning-change policies.
---

# stories-update

Always invokes `stories-audit --story <slug>` before edits. Findings on
the targeted story block silent updates and require human drift triage.

Edit class is honor-system; structural backstops in `lock_check.py`
enforce locked sections, inline locked-block byte equality, frontmatter
changes (`--confirm-resistance-change` for `change_resistance` shifts),
and an `Auditable Claims` bullet-count guard. Meaning changes require
`--confirm-meaning-change` outside `low` resistance.

Regenerates `INDEX.md` on metadata-affecting changes (title, status,
authority, change_resistance).

**Status:** Implementation deferred to Plan 3. See `spec.md` and
`2026-05-01-storystore-plan-3-edits-and-impact.md` for the full contract.
