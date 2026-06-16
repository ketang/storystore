---
name: stories-generate
description: Author a new intent story in either interview mode (authority=accepted) or observed mode (authority=observed) and regenerate INDEX.md.
---

# stories-generate

Author a new intent story for the target repository. Two modes:

- `--interview`: short human interview, writes `authority: accepted`.
- `--observed`: derives candidates from the deterministic scanner, LLM
  authors prose, writes `authority: observed`.

The bar for a valid story is low — frontmatter plus `Intent`. Everything
else is optional. Sparse drafts are allowed.

## Before Writing

- Read local instructions: `AGENTS.md`, `CLAUDE.md`, `GEMINI.md`, README,
  and any documented contribution flow.
- Use the repo's required branch/worktree flow. If none is documented, use
  a dedicated feature branch in a linked worktree outside the repo.
- If the repo uses an issue tracker with claim semantics, claim the
  relevant issue before edits.

## Locating storystore scripts

Storystore's runtime scripts ship at different paths depending on install
layout, so resolve their directory once and reuse it for every command below.
Set `skill_dir` to the absolute path of the directory containing **this
`SKILL.md`**, then:

```bash
# Claude layout: this file is <plugin-root>/.claude/skills/<name>.md → scripts at <plugin-root>/shared
# Codex layout:  this file is <plugin-root>/.codex-plugin/skills/<name>/SKILL.md → scripts at <skill_dir>/scripts
STORYSTORE_SHARED="$(for d in "$skill_dir/scripts" "$skill_dir/../../shared"; do [ -d "$d" ] && (cd "$d" && pwd) && break; done)"
```

If `STORYSTORE_SHARED` comes back empty, the plugin is not laid out as
expected — stop and report rather than guessing a path. Every shared-script
invocation below runs as `python3 "$STORYSTORE_SHARED/<script>.py"`.

## Interview Mode

Run with `--interview`. Conduct a short interview with the user:

1. Title, slug (kebab-case, 2+ words), and 1–2 sentence Intent.
2. The Story (who/what/why), Expected Behavior, and Boundaries.
3. Auditable Claims and Evidence (`tests`, `surface`, `docs`).
4. Edge cases and failure modes — ask the user what happens when things go
   wrong: validation errors, missing inputs, empty states, permission/auth
   failures, unsupported formats, timeout or network failures, idempotency
   behavior, fallback paths. Ask about edge cases the workflow can hit
   (size limits, locale/encoding, partial inputs, concurrent edits) and
   explicit non-promises. Record observable failure behavior in
   `Expected Behavior` and exclusions or known non-promises in
   `Boundaries`. Do not invent failure modes the user has not accepted.

Pipe the resulting JSON to `write_story.py --interview`.

Interview defaults: `status=draft`, `authority=accepted`,
`change_resistance=medium`, `locked_sections=[Intent]`.

## Observed Mode

Run with `--observed`. Default initial run produces 5 stories;
`--limit N` overrides. Re-runs subtract already-authored slugs (the
candidate scanner removes candidates already covered by an authored
story).

1. **Discover candidates.**

   ```bash
   python3 "$STORYSTORE_SHARED/list_candidates.py" --repo-root <repo-root>
   ```

   Output: `{"candidates": [{"kind", "name", "summary", "evidence": [...]}, ...]}`.
   Already-authored slugs are subtracted by the scanner.

2. **Select candidates.** Prefer user-invoked surfaces, distinct
   workflows, and non-trivial intent. Skip purely structural or
   build/lint/typecheck surfaces.

3. **Draft observed-mode prose.** Inspect deterministic evidence (tests
   asserting error paths, validation code, error-handling branches,
   documented error responses) for the same edge-case and failure
   categories listed under interview mode. Capture only failure behavior
   that the evidence supports — do not fabricate.

4. **Run the Draft Story Evaluation gate** (see below) before writing.

5. **Write passing drafts** with `write_story.py --observed`.

Observed defaults: `status=draft`, `authority=observed`,
`change_resistance=low`, `locked_sections=[]`.

## Draft Story Evaluation Gate

Independent review is a critical quality gate. Before writing, promoting,
or presenting a drafted story as ready, launch a context-free evaluator
subagent when the runtime supports subagents.

The evaluator must not inherit this conversation, the drafting rationale,
or unstated product intent. Pass only a bounded review packet:

- the draft story;
- relevant candidate metadata and deterministic evidence snippets;
- existing story slugs and titles;
- the storystore schema and editorial rules (`shared/spec.md`).

If launching a subagent requires user permission, ask eagerly and
explicitly:

```text
Independent story review is a required storystore quality gate. May I
launch a context-free evaluator subagent with only the draft story and
evidence packet?
```

Evaluator output:

```json
{
  "verdict": "pass | revise | reject",
  "promotion_recommendation": "keep_draft | ready_for_active | needs_human_acceptance",
  "confidence": "low | medium | high",
  "findings": [
    {
      "severity": "blocker | major | minor",
      "kind": "intent-vague | scope-too-broad | implementation-led | claim-not-auditable | evidence-overreach | authority-mismatch | boundary-weak | duplicate-risk",
      "section": "Intent",
      "issue": "What is wrong.",
      "suggested_fix": "Small, concrete repair.",
      "requires_human": false
    }
  ]
}
```

Verdict handling:

- `pass`: write the story as draft.
- `revise`: address targeted findings once and review again when feasible.
- `reject`: do not write unless the user explicitly asks to keep the
  draft.

Promotion to `active` requires a clean independent review with no blocker
or major findings, no placeholder Intent, sufficient claims/evidence for
scope, and no unresolved human-intent questions. The evaluator is
advisory; human acceptance is still required for `authority: accepted`.

If permission is declined or the runtime cannot launch a context-free
evaluator, the story may still be written as `status: draft`, but report
that it is unevaluated and do not recommend promotion to `active`.

The evaluator gate applies to both modes. In interview mode it
specifically checks negative-path coverage and may emit `boundary-weak`
findings when obvious edge cases or failure modes are omitted.

## Script Contracts

`list_candidates.py`:

```bash
python3 "$STORYSTORE_SHARED/list_candidates.py" --repo-root <repo-root>
```

Stdout JSON:

```json
{
  "candidates": [
    {
      "kind": "cli-command",
      "name": "login",
      "summary": "CLI command login",
      "evidence": ["src/cli.ts"]
    }
  ]
}
```

Exit codes: `0` success; `2` invalid input. Candidates already covered by
an authored story are subtracted from output.

`write_story.py`:

```bash
python3 "$STORYSTORE_SHARED/write_story.py" --repo-root <repo-root> [--interview | --observed]
```

Stdin JSON (mode supplies defaults for any omitted optional fields):

```json
{
  "schema_version": 1,
  "title": "Authenticated Login",
  "slug": "authenticated-login",
  "status": "draft",
  "authority": "accepted",
  "change_resistance": "medium",
  "locked_sections": ["Intent"],
  "intent": "Users sign in so the system can attribute actions.",
  "story": "A user signs in with credentials before using private commands.",
  "expected_behavior": "Valid credentials establish an authenticated session.",
  "boundaries": "Does not cover password reset.",
  "auditable_claims": ["The login command exists."],
  "evidence": {
    "tests": ["tests/login.e2e.test.ts"],
    "surface": ["cli: login"],
    "docs": ["README.md"]
  }
}
```

Exit codes:

- `0` success;
- `2` invalid input (bad slug, overwrite refusal, JSON parse error,
  missing required field, unknown mode);
- `3` validity-matrix violation (`authority=observed` with
  `change_resistance` in `{high, immutable}`).

Slug rules: kebab-case ASCII; fewer than 2 words exits 2; 2–3 or 9+ words
emit `STORYSTORE_SLUG_NAG: ...` on stderr and accept. Slugs are stable —
never rename.

`write_story.py` refuses to overwrite an existing story file (exit 2) and
wholesale-regenerates `docs/stories/INDEX.md` after a successful write.

## Finish

Review the diff, rebuild generated plugin/docs outputs when the repo
tracks them, run the selected verification command, and report the
branch/worktree, tracker item, and verification result.

**Commit the new story.** Stories are durable repo artifacts, not scratch
output. Commit the new `docs/stories/<slug>.md` and the regenerated
`docs/stories/INDEX.md` on the current branch so the work is tracked
rather than left untracked. Do not commit `docs/stories/drift-todo.md` (it
is gitignored). Branch and worktree conventions belong to the host repo's
workflow and are out of scope here — commit on whatever branch the
generate ran on.
