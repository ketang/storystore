---
name: stories-init
description: Initialize docs/stories/ for a repository — create the directory, write README and INDEX stubs, gitignore drift-todo, and on fresh init seed the top observed-mode stories.
---

# stories-init

Initialize `docs/stories/` in two phases. Phase 1 is mechanical and is
already implemented by `scripts/stories-init-mechanical`. This skill
documents Phase 2: the LLM-driven seeding pass that runs only on a fresh
init.

## Before Writing

- Read local instructions: `AGENTS.md`, `CLAUDE.md`, `GEMINI.md`, README,
  and any documented contribution flow.
- Use the repo's required branch/worktree flow. If none is documented, use
  a dedicated feature branch in a linked worktree outside the repo.
- If the repo uses an issue tracker with claim semantics, find or create
  the relevant issue and claim it before edits.
- Note the verification command before changing files.

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
invocation below runs as `python3 "$STORYSTORE_SHARED/<script>.py"`. The Phase 1
mechanical tool is not a shared script — it lives in the plugin's top-level
`scripts/` dir, reached as `"$skill_dir/../../scripts/stories-init-mechanical"`.

## Phase 1 (mechanical)

Run the bundled script:

```bash
"$skill_dir/../../scripts/stories-init-mechanical" --repo-root <repo-root>
```

It creates missing `docs/stories/` scaffolding (`README.md`, `INDEX.md`),
appends `docs/stories/drift-todo.md` to `.gitignore`, detects root
agent-instruction files, and prints JSON of the form:

```json
{
  "fresh_init": true,
  "created": ["docs/stories/", "docs/stories/README.md", "docs/stories/INDEX.md"],
  "preserved": [],
  "gitignore_updated": true,
  "agent_instruction_files": ["AGENTS.md"]
}
```

Phase 1 is idempotent. Any pre-existing `docs/stories/` (with or without
stories) yields `fresh_init: false`. Phase 1 never authors stories and
never edits agent-instruction files.

If `fresh_init` is `false`, stop here. Do not seed stories. Phase 2 is
fresh-init-only.

## Phase 2 (LLM seeding — fresh init only)

Run Phase 2 only when the Phase 1 output reports `fresh_init: true`.

1. **Discover candidates.** Invoke `list_candidates.py`:

   ```bash
   python3 "$STORYSTORE_SHARED/list_candidates.py" --repo-root <repo-root>
   ```

   Output is `{"candidates": [{"kind", "name", "summary", "evidence": [...]}, ...]}`.

2. **Select the top candidates.** Apply these criteria:

   - user-invoked surfaces (CLI commands, HTTP routes, top-level user
     scripts, package `bin`, package `exports`);
   - distinct workflows (do not pick variants of the same flow);
   - non-trivial intent (skip surfaces whose purpose is purely structural
     or infrastructural — build, lint, typecheck, etc.).

   Default to the top 5. Honor an explicit user-requested count when given
   (for example, 10).

3. **Draft observed-mode stories.** Write real LLM-authored prose for each
   candidate. For each draft, inspect deterministic evidence for both the
   happy path and non-happy-path behavior: tests, validation code,
   error-handling branches, documented error responses. Look for
   validation errors, missing inputs, empty states, permission/auth
   failures, unsupported formats, timeout or network failures, idempotency
   behavior, and fallback paths. Capture observable failure behavior in
   `Expected Behavior` and known edge cases or exclusions in `Boundaries`.
   Do not invent failure modes the evidence does not support; if no
   negative-path evidence exists for a draft, omit it rather than
   fabricating it.

4. **Run the Draft Story Evaluation gate.** Before writing or recommending
   promotion of any draft, launch a context-free evaluator subagent when
   the runtime supports subagents. The evaluator must not inherit this
   conversation or the drafting rationale. Pass only:

   - the draft story;
   - the relevant candidate metadata and evidence snippets;
   - existing story slugs and titles (none on fresh init);
   - the storystore schema and editorial rules (`shared/spec.md`).

   If launching the subagent requires user permission, ask eagerly and
   explicitly:

   ```text
   Independent story review is a required storystore quality gate. May I
   launch a context-free evaluator subagent with only the draft story and
   evidence packet?
   ```

   Evaluator output format:

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
   - `revise`: address findings once and review again when feasible.
   - `reject`: do not write unless the user explicitly asks to keep the
     draft.

   Promotion to `active` requires a clean review (no blocker or major
   findings, no placeholder Intent, sufficient claims/evidence, no
   unresolved human-intent questions).

   If permission is declined or the runtime cannot launch a context-free
   evaluator, retained drafts must be written with `status: draft` and
   reported as unevaluated. Do not recommend promotion to `active`.

5. **Write passing drafts.** For each draft that passed (or was explicitly
   retained by the user), pipe its JSON to:

   ```bash
   python3 "$STORYSTORE_SHARED/write_story.py" --repo-root <repo-root> --observed
   ```

   `write_story.py` enforces the slug rules and validity matrix, writes
   `docs/stories/<slug>.md`, and wholesale-regenerates `docs/stories/INDEX.md`.

   `--observed` defaults: `status=draft`, `authority=observed`,
   `change_resistance=low`, `locked_sections=[]`. `authority: observed`
   with `change_resistance` in `{high, immutable}` exits 3.

6. **Apply the agent-instruction pointer and record its outcome.** This is a
   required step, not an optional offer. For *each* file in Phase 1's
   `agent_instruction_files` list, the pointer below must reach one recorded
   terminal state — there is no silent third state, and no detected file may be
   left unaddressed.

   Pointer text to add (append near the top of the file, or under an existing
   conventions/workflow heading):

   ```markdown
   - This repo uses intent stories under `docs/stories/`. Before making
     behavioral changes to user-facing functionality, run
     `stories-impact-check`.
   ```

   Resolve each detected file to exactly one of:

   - `applied` — the pointer text was written into the file. This is the
     default; apply it unless the user explicitly declines.
   - `already-present` — the pointer text (or an equivalent direction to run
     `stories-impact-check`) is already in the file; nothing to write.
   - `declined` — the user explicitly declined the pointer for this file.
     A decline must be recorded durably in the repo tracker (a tracker note or
     issue) so the decision survives the session. Do not use
     `docs/stories/drift-todo.md` for this — it is reserved for story/software
     drift notes.

   Carry the per-file outcome through to the completion report (see **Finish**).

## Promotion Path: Observed → Accepted

Seeded stories are written with `authority: observed` — they record what the
software does today, not committed intent. **Observed stories never gate.**
`stories-impact-check` reports them as informational context but never blocks
a change, regardless of `change_resistance`.

This is deliberate, but it has a consequence worth stating plainly at init
time: **until at least one story is promoted to `accepted`, the
`stories-impact-check` hard trigger is inert** — it can fire, but it has
nothing to gate. A corpus left entirely in `observed` authority taxes every
edit (the agent runs the check) while never protecting anything, and agents
learn to ignore it.

To make the corpus gate, promote reviewed stories `observed → accepted`:

1. A human reads the observed story and confirms it describes *intended*
   behavior, not merely current behavior.
2. Run `stories-update` on that story and apply an `authority-change` edit
   (observed → accepted). This requires explicit user approval — it is a
   meaning change, never agent-initiated.
3. Promote `status: draft → active` (seeded stories default to `draft`, and
   a draft gates only at `high`/`immutable` resistance), and set a
   `change_resistance` appropriate to how protected the behavior is
   (`low`/`medium`/`high`/`immutable`). Per the `stories-impact-check` Agent
   Behavior Table, only `accepted` + `active` stories gate, and only at
   `change_resistance` `medium` or above; `low`-resistance stories remain
   informational and do not block.

Tell the user this at the end of a fresh init: the seeded corpus is a
starting point that does not yet gate anything, and the next step — when they
are ready — is human review and promotion of the stories that capture real
intent.

## Finish

Review the diff, rebuild generated plugin/docs outputs when the repo
tracks them, run the selected verification command, and report the
branch/worktree, tracker item, and verification result.

**Report the agent-instruction pointer outcome.** The completion report must
name every file in Phase 1's `agent_instruction_files` list and state its
pointer outcome from Step 6 — `applied`, `already-present`, or `declined`
(with the tracker reference where the decline was recorded). Every detected
file must appear with an outcome; an unreported file is a gap to fix, not to
omit.

**Verify the gitignore change landed.** Phase 1 appends
`docs/stories/drift-todo.md` to `.gitignore`. Confirm that entry is
actually present before finishing — a fresh init that reports
`gitignore_updated: true` but leaves no entry in `.gitignore` is a
failure to fix, not to ignore:

```bash
grep -qxF 'docs/stories/drift-todo.md' .gitignore
```

If the entry is missing, re-run Phase 1 or add it before committing.

**Commit the new artifacts.** Stories are durable repo artifacts, not
scratch output. Commit `docs/stories/` and the `.gitignore` change on the
current branch so the seeded stories are tracked rather than left
untracked. Do not commit `docs/stories/drift-todo.md` (it is gitignored).
Branch and worktree conventions belong to the host repo's workflow and are
out of scope here — commit on whatever branch the init ran on.
