---
name: stories-init
description: Initialize docs/stories/ for a repository — create the directory, write README and INDEX stubs, gitignore drift-todo, and on fresh init seed the top observed-mode stories.
---

# stories-init

Two-phase initialization for the storystore convention.

Phase 1 (mechanical, `init.py`): create `docs/stories/`, write a 3-sentence
`README.md` stub when absent, write an empty `INDEX.md`, append
`drift-todo.md` to `.gitignore`, detect root-level agent-instruction files
(`AGENTS.md`, `CLAUDE.md`, `GEMINI.md`), and return JSON including
`fresh_init: true | false`. Idempotent: any pre-existing `docs/stories/`
means `fresh_init: false`.

Phase 2 (LLM-driven, fresh-init only): invoke `list_candidates.py`, pick
the top user-invoked surfaces, and write 5 observed-mode stories with
real prose via `write_story.py --observed`.

**Status:** Implementation deferred to Plan 1. See `spec.md` and
`2026-05-01-storystore-plan-1-foundation.md` for the full contract.
