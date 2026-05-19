---
name: stories-init
description: Initialize docs/stories/ for a repository — create the directory, write README and INDEX stubs, gitignore drift-todo, and on fresh init seed the top observed-mode stories.
---

# stories-init

Initialize `docs/stories/` in two phases, while following the target repo's
work conventions.

## Before Writing

- Read local instructions: `AGENTS.md`, `CLAUDE.md`, `GEMINI.md`, README, and
  any documented contribution flow.
- Use the repo's required branch/worktree flow. If none is documented, use a
  dedicated feature branch in a linked worktree outside the repo.
- If the repo uses an issue tracker with claim semantics, find or create the
  relevant issue and claim it before edits.
- Note the verification command before changing files.

## Phase 1

Run:

```bash
stories-init/scripts/init.py --repo-root <repo-root>
```

The script creates missing `docs/stories/` scaffolding, appends
`docs/stories/drift-todo.md` to `.gitignore`, detects root agent-instruction
files, and returns JSON including `fresh_init`.

If `docs/stories/` already existed, treat `fresh_init: false`: fill missing
mechanical files only and do not seed stories.

## Phase 2

Run only when `fresh_init: true`:

1. Invoke `list_candidates.py`.
2. Pick distinct, user-invoked, non-trivial workflows.
3. Draft observed-mode stories.
4. Run the independent Draft Story Evaluation gate for each draft when
   subagents are available.
5. Write passing or explicitly retained drafts with `write_story.py --observed`.
6. Seed 5 stories by default, or the count the user explicitly requested.

If independent review cannot run or is declined, retained seeded stories must
stay `status: draft` and be reported as unevaluated.

## Follow-Up

After fresh init, offer this pointer for detected agent-instruction files, but
edit them only with explicit user approval:

```markdown
- This repo uses intent stories under `docs/stories/`. Before making behavioral
  changes to user-facing functionality, run `stories-impact-check`.
```

Record follow-up work in the repo tracker. Use `docs/stories/drift-todo.md`
only for story/software drift notes.

## Finish

Review the diff, rebuild generated plugin/docs outputs when the repo tracks
them, run the selected verification command, and report the branch/worktree,
tracker item, and verification result.

**Status:** Implementation deferred to Plan 1. See `spec.md` and
`2026-05-01-storystore-plan-1-foundation.md` for the full script contract.
