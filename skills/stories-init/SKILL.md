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
the top user-invoked surfaces, draft observed-mode stories with real prose,
run the independent Draft Story Evaluation quality gate, and write passing
or explicitly retained drafts via `write_story.py --observed`. Default
initial seeding writes 5 stories. If the user explicitly requests a different
count (for example, 10 stories), honor that count as the observed-mode limit
and continue to apply the same selection criteria.

Independent review is a critical quality gate. When the runtime supports
subagents, launch a context-free evaluator subagent for each drafted story
before writing it. If launching that subagent requires user permission, ask
eagerly and explicitly:

```text
Independent story review is a required storystore quality gate. May I launch a
context-free evaluator subagent with only the draft story and evidence packet?
```

If independent review cannot run or is declined, any retained seeded stories
must remain `status: draft`, be reported as unevaluated, and must not be
recommended for promotion to `active`.

**Status:** Implementation deferred to Plan 1. See `spec.md` and
`2026-05-01-storystore-plan-1-foundation.md` for the full contract.
