---
name: stories-generate
description: Author a new intent story in either interview mode (authority=accepted) or observed mode (authority=observed) and regenerate INDEX.md.
---

# stories-generate

Two modes:

- `--interview`: writes `authority: accepted` after a short human
  interview. Defaults: `status=draft`, `change_resistance=medium`,
  `locked_sections=[Intent]`.
- `--observed`: writes `authority: observed` from the deterministic
  candidate list (`list_candidates.py`), with real LLM-authored prose.
  Defaults: `status=draft`, `change_resistance=low`, `locked_sections=[]`.
  Default initial run produces 5 stories; `--limit N` overrides; re-runs
  subtract already-authored slugs.

The bar for a valid story is frontmatter + Intent. Validity matrix is
enforced at write: `authority: observed` cannot have `change_resistance:
high | immutable` (exit 3). The script refuses to overwrite an existing
story file (exit 2). After write, regenerates `INDEX.md`.

**Status:** Implementation deferred to Plan 1. See `spec.md` and
`2026-05-01-storystore-plan-1-foundation.md` for the full contract.
