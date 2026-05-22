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

## Edge Cases And Failure Modes

In both modes, prompt for or infer meaningful edge cases and failure modes
and place them in `Expected Behavior` and/or `Boundaries`:

- **Interview mode**: ask the user what happens when things go wrong —
  validation errors, missing inputs, empty states, permission/auth
  failures, unsupported formats, timeout or network failures, idempotency
  behavior, fallback paths. Ask about edge cases the workflow can hit
  (size limits, locale/encoding, partial inputs, concurrent edits) and
  explicit non-promises. Record observable failure behavior in `Expected
  Behavior` and exclusions or known non-promises in `Boundaries`.
- **Observed mode**: inspect deterministic evidence (tests asserting
  error paths, validation code, error-handling branches, documented error
  responses) for the same categories. Capture only failure behavior that
  the evidence supports.

Do not invent failure modes the user has not accepted or the evidence does
not support. Sparse drafts are still allowed (frontmatter + Intent), but
high-quality drafts should mention meaningful negative-path behavior when
it is known or inferable. The independent evaluator gate checks for this
coverage and may emit `boundary-weak` findings when obvious edge cases or
failure modes are omitted.

## Independent Draft Evaluation

Independent review is a critical quality gate. Before writing, promoting, or
presenting a drafted story as ready, launch a context-free evaluator subagent
when the runtime supports subagents.

The evaluator must not inherit this conversation, the drafting rationale, or
unstated product intent. Pass only a bounded review packet:

- the draft story;
- relevant candidate metadata and deterministic evidence snippets;
- existing story slugs and titles;
- the storystore schema and editorial rules.

If launching a subagent requires user permission, ask eagerly and explicitly:

```text
Independent story review is a required storystore quality gate. May I launch a
context-free evaluator subagent with only the draft story and evidence packet?
```

If permission is declined, or the runtime cannot launch a context-free
evaluator, the story may still be written as `status: draft`, but report that
it is unevaluated and do not recommend promotion to `active`.

Evaluator verdicts:

- `pass`: write the story as draft.
- `revise`: address targeted findings once and review again when feasible.
- `reject`: do not write unless the user explicitly asks to keep the draft.

Promotion to `active` requires a clean independent review with no blocker or
major findings, no placeholder Intent, sufficient claims/evidence for scope,
and no unresolved human-intent questions. The evaluator is advisory; human
acceptance is still required for `authority: accepted`.

**Status:** Implementation deferred to Plan 1. See `spec.md` and
`2026-05-01-storystore-plan-1-foundation.md` for the full contract.
