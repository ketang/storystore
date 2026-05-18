# storystore Plan 1: Foundation, Init, And Generation

**Date:** 2026-05-01 (revised)
**Status:** Target implementation plan.

## Goal

Ship the initial `storystore` plugin with the authoring path:

- `stories-init`
- `stories-generate`

After this plan, a consumer repo can initialize `docs/stories/`, get a small
seed of observed-mode stories on fresh init, run a candidate scanner, and
author additional stories in either interview or observed mode.

## Files

New canonical skill files:

```text
catalog/skills/stories-init/SKILL.md
catalog/skills/stories-init/scripts/init.py

catalog/skills/stories-generate/SKILL.md
catalog/skills/stories-generate/scripts/list_candidates.py
catalog/skills/stories-generate/scripts/write_story.py
```

Note: no `references/conventions-template.md` and no
`references/index-template.md`. `CONVENTIONS.md` is killed; the plugin spec
ships separately under `catalog/shared/storystore/spec.md` (Plan 2). README
stub text is inlined in `init.py`.

New tests:

```text
tests/test_storystore_init.py
tests/test_storystore_generate_list_candidates.py
tests/test_storystore_generate_write_story.py
tests/fixtures/storystore_ts_repo/
```

Modified files:

```text
scripts/build-plugins
catalog/plugin-versions.json
.claude-plugin/marketplace.json   # remove intent-stories prototype
PLUGIN_DEFS                        # remove intent-stories; add storystore
```

Generated output:

```text
plugins/claude/storystore/
plugins/codex/storystore/
.claude-plugin/marketplace.json
```

## Plugin Registration

Remove the `intent-stories` prototype from `PLUGIN_DEFS` and
`marketplace.json`. The prototype lives in git history only; no migration
concerns.

Add `storystore` to `PLUGIN_ORDER` and `PLUGIN_DEFS` with all six skills
declared up front, even though Plans 2 and 3 ship the implementations:

```python
"storystore": {
    "description": "Intent-story documentation with audits, guarded edits, and impact checks",
    "display_name": "Storystore",
    "short_description": "Durable user intent for software repos",
    "category": "Documentation",
    "capabilities": ["Interactive", "Write"],
    "keywords": ["documentation", "stories", "intent", "audit", "drift"],
    "skills": [
        "stories-init",
        "stories-generate",
        "stories-audit",
        "stories-coverage",
        "stories-update",
        "stories-impact-check",
    ],
}
```

Plans 2 and 3 add the remaining `SKILL.md` files; Plan 1 declares only the
two it ships and leaves the rest to be added per phase.

Add a version entry:

```json
"storystore": "1.0.0"
```

## stories-init

### Behavior (two-phase)

Phase 1 is mechanical and runs in `init.py`:

- create `docs/stories/` when missing;
- write a 3-sentence `README.md` stub when absent;
- write an empty `INDEX.md` when absent;
- ensure `docs/stories/drift-todo.md` is gitignored (append the entry to
  `.gitignore` if missing);
- detect root-level `AGENTS.md`, `CLAUDE.md`, `GEMINI.md`;
- return JSON including `fresh_init: true | false`.

Phase 1 is idempotent. Any pre-existing `docs/stories/` (with or without
stories) means `fresh_init: false`. Phase 1 fills strictly missing pieces but
never authors stories.

Phase 2 is LLM-driven and lives in SKILL.md. It runs only when
`fresh_init: true`. The agent:

1. invokes `list_candidates.py`;
2. applies the SKILL.md selection criteria (user-invoked surfaces, distinct
   workflows, non-trivial intent);
3. drafts observed-mode stories with real LLM-authored prose;
4. runs the Draft Story Evaluation quality gate with a context-free evaluator
   subagent when supported;
5. writes passing or explicitly retained observed-mode stories via
   `write_story.py --observed`, defaulting to the top 5 but honoring an
   explicit user-requested count such as 10;
6. updates `INDEX.md` (handled by `write_story.py`).

There are no plugin-shipped seed stories. Stories written during init are
indistinguishable from any other observed-mode story.

After Phase 2, the agent offers a one-time pointer to detected
agent-instruction files. Pointer offer is fresh-init-only; it is never
re-offered. Audit signal in Plan 2 covers the missing-pointer case
afterward.

### init.py Contract

Command:

```bash
stories-init/scripts/init.py --repo-root <repo-root>
```

Stdout JSON:

```json
{
  "fresh_init": true,
  "created": [
    "docs/stories",
    "docs/stories/README.md",
    "docs/stories/INDEX.md"
  ],
  "preserved": [],
  "gitignore_updated": true,
  "agent_instruction_files": ["AGENTS.md"]
}
```

The script writes only under `docs/stories/` and `.gitignore`. It does not
edit agent-instruction files. The agent (after Phase 2) offers the pointer
edit and only applies it with explicit user approval.

README.md stub written on fresh init:

```markdown
# Intent Stories

This directory holds intent stories for this repository. Each `*.md` file is one
story describing a user-facing capability. See [INDEX.md](INDEX.md) for the
full list, and the storystore plugin documentation for the schema and tooling.
```

## stories-generate

### Behavior

Two modes:

- `--interview`: writes `authority: accepted` after a short human interview.
- `--observed`: writes `authority: observed` from `list_candidates.py`,
  authored by the LLM with real prose. Default initial run produces 5
  stories. `--limit N` overrides; re-runs subtract already-authored slugs.

The bar for a valid story is low: frontmatter + Intent. Everything else is
optional. The eliminated B8 "half-finished interview state" no longer exists
because there is no half-finished — only thin.

### Draft Story Evaluation Gate

Independent review is a critical quality gate for drafted stories. Before
writing, promoting, or presenting a generated story as ready, the authoring
agent launches a context-free evaluator subagent when the runtime supports
subagents. The evaluator must not inherit the parent conversation or drafting
rationale; it receives only the draft story, relevant candidate/evidence
snippets, existing story slugs/titles, and the storystore schema/editorial
rules.

If launching the subagent requires user permission, the agent asks eagerly and
explicitly:

```text
Independent story review is a required storystore quality gate. May I launch a
context-free evaluator subagent with only the draft story and evidence packet?
```

If the user declines, or the runtime cannot launch a context-free evaluator,
the story may still be written as `status: draft`, but the agent must report
that it is unevaluated and must not recommend promotion to `active`.

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

`pass` can be written as draft. `revise` should be addressed once and reviewed
again when feasible. `reject` should not be written unless the user explicitly
asks to keep the draft. Promotion to `active` requires a clean independent
review with no blocker or major findings, no placeholder Intent, sufficient
claims/evidence for scope, and no unresolved human-intent questions.

After writing a story, `write_story.py` regenerates `INDEX.md` (wholesale
overwrite).

### list_candidates.py Contract

Command:

```bash
stories-generate/scripts/list_candidates.py --repo-root <repo-root>
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

Plan 1 ships TypeScript-first plus language-agnostic discovery:

- commander-style CLI commands;
- package `bin`;
- package `exports`;
- obvious HTTP route declarations when cheap;
- test names from common e2e/spec files;
- README and DESIGN H2/H3 headings;
- top-level scripts where user-facing.

Candidates already covered by an authored story are subtracted from output.

### write_story.py Contract

Command:

```bash
stories-generate/scripts/write_story.py \
  --repo-root <repo-root> \
  [--interview | --observed]
```

Stdin JSON:

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
  "auditable_claims": [
    "The login command exists."
  ],
  "evidence": {
    "tests": ["tests/login.e2e.test.ts"],
    "surface": ["cli: login"],
    "docs": ["README.md"]
  }
}
```

There is no `group` field. The script writes:

- the story file at `docs/stories/<slug>.md`;
- a regenerated `INDEX.md` (see Index Format below).

Defaults differ by mode:

```text
--interview: status=draft, authority=accepted, change_resistance=medium,
             locked_sections=[Intent]
--observed:  status=draft, authority=observed, change_resistance=low,
             locked_sections=[]
```

Validity matrix is enforced at write: `authority: observed` cannot have
`change_resistance: high | immutable`. Violations exit 3.

The script refuses to overwrite an existing story file (exit 2).

### Slug Validation

Kebab-case ASCII. Fewer than 2 words is exit 2. 2–3 or 9+ words emit stderr
nag `STORYSTORE_SLUG_NAG: ...` and accept. Slugs are stable (never rename)
and flat.

### Index Format

`docs/stories/INDEX.md` is plugin-owned and overwritten wholesale by
`write_story.py`:

```markdown
# Intent Story Index

3 stories — generated 2026-05-01T12:34:56Z

- [authenticated-login](authenticated-login.md) — Authenticated Login *(active, accepted, high)*
- [logout](logout.md) — Logout *(active, accepted, medium)*
- [password-reset](password-reset.md) — Password Reset *(draft, accepted, medium)*
```

Slug-sorted, flat. Each entry: `- [<slug>](<slug>.md) — <title> *(status,
authority, change_resistance)*`.

`stories-update` (Plan 3) triggers an INDEX regen on metadata-affecting
changes (title, status, authority, change_resistance).

## Frontmatter Parser

Bundled stdlib parser, no PyYAML. Lives in `storystore_lib.py` (Plan 2). Plan
1's `write_story.py` writes deterministic frontmatter that the strict subset
parser accepts: top-level mapping, scalars, ISO dates, booleans, one
`locked_sections` list.

`schema_version: 1` is written at the top of frontmatter for new stories.

## Tests

Add tests for:

- init creates `docs/stories/`, `README.md`, and `INDEX.md`;
- init updates `.gitignore` for `drift-todo.md`;
- init returns `fresh_init: true` on first run, `false` afterward;
- init preserves existing files;
- init reports root-level agent-instruction files but does not edit them;
- candidate scanner finds TS CLI commands, routes, tests, package metadata,
  and README/DESIGN headings from a fixture;
- candidate scanner subtracts already-authored stories;
- write_story writes the target schema with `schema_version: 1`;
- write_story enforces validity matrix (observed + high → exit 3);
- write_story enforces slug rules (1 word → exit 2; 3 words → nag on stderr);
- write_story refuses to overwrite;
- write_story regenerates INDEX.md;
- write_story `--observed` defaults differ from `--interview`;
- build materializes `storystore` for Claude and Codex;
- `intent-stories` prototype is removed from `PLUGIN_DEFS` and
  `marketplace.json`.

## Verification

Run:

```bash
python3 -m unittest tests.test_storystore_init -v
python3 -m unittest tests.test_storystore_generate_list_candidates -v
python3 -m unittest tests.test_storystore_generate_write_story -v
python3 -m unittest tests.test_build_plugins.BuildPluginsTest.test_storystore_plugin_is_standalone_on_claude_and_codex -v
python3 -m unittest discover -s tests -t .
scripts/build-plugins
```

Smoke test:

```bash
TMPDIR=$(mktemp -d)
catalog/skills/stories-init/scripts/init.py --repo-root "$TMPDIR"
cp -r tests/fixtures/storystore_ts_repo/. "$TMPDIR/"
catalog/skills/stories-generate/scripts/list_candidates.py --repo-root "$TMPDIR"
echo '{"title":"Login","slug":"authenticated-login","intent":"Users sign in.","story":"A user signs in.","expected_behavior":"Login succeeds with valid credentials.","boundaries":"No reset.","auditable_claims":["The login command exists."],"evidence":{"surface":["cli: login"]}}' \
  | catalog/skills/stories-generate/scripts/write_story.py --repo-root "$TMPDIR" --interview
test -f "$TMPDIR/docs/stories/authenticated-login.md"
test -f "$TMPDIR/docs/stories/INDEX.md"
rm -rf "$TMPDIR"
```

## Done Criteria

- `storystore` plugin is registered with all six skill names declared.
- `stories-init` and `stories-generate` are built into Claude and Codex
  plugin outputs.
- The repo convention uses `README.md` + `INDEX.md` (no `CONVENTIONS.md`).
- Init is two-phase, idempotent, and returns `fresh_init`.
- Generation supports `--interview` and `--observed`; observed-mode produces
  real LLM prose.
- Drafted stories run through the independent Draft Story Evaluation quality
  gate when supported, and missing review blocks `active` promotion
  recommendations.
- Validity matrix and slug rules are enforced.
- `intent-stories` prototype is removed.
- Tests and build pass.
