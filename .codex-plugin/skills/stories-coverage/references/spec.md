# Storystore Plugin Spec

This document is the schema and tooling reference for the `storystore` plugin.
It is shipped with each storystore skill via the `shared_scripts` packaging
mechanism. Consumer repositories do not need a `CONVENTIONS.md`; if a repo
wants local conventions, the natural home is `docs/stories/README.md`.

## Purpose

`storystore` maintains durable, prose-first **intent stories** under
`docs/stories/`. Each story describes a user-facing capability of the
software. Stories are written for humans first and agents second.

The core rule is:

> Code and tests are evidence. They are not automatic authority to rewrite
> accepted intent.

## Repository Layout

```text
docs/stories/
  README.md         # user-owned; init writes a 3-sentence stub on fresh init
  INDEX.md          # plugin-owned; auto-generated; wholesale overwrite
  drift-todo.md     # gitignored; append-only, date-stamped sections
  <slug>.md         # one story per durable user-facing workflow or capability
```

Loader skip list (`README.md`, `INDEX.md`, `drift-todo.md`) is excluded from
story scans.

## Frontmatter Schema

```yaml
---
schema_version: 1
title: Human title
slug: kebab-case-slug
status: draft | active | deprecated
authority: observed | accepted
change_resistance: low | medium | high | immutable
tests_applicable: true
locked_sections:
  - Intent
last_audited: YYYY-MM-DD
---
```

| Field               | Type    | Required | Notes                                                                                          |
|---------------------|---------|----------|------------------------------------------------------------------------------------------------|
| `schema_version`    | int     | no       | Defaults to 1 when absent. Written by `stories-generate` for new stories.                       |
| `title`             | string  | yes      | Human-readable title.                                                                           |
| `slug`              | string  | yes      | Kebab-case ASCII; target 4–8 words; <2 words is exit 2; 2–3 or 9+ words emit a stderr nag.      |
| `status`            | enum    | yes      | `draft`, `active`, `deprecated`. There is no `superseded`.                                      |
| `authority`         | enum    | yes      | `observed` or `accepted`. There is no `proposed`; that role is `status: draft`.                 |
| `change_resistance` | enum    | yes      | `low`, `medium`, `high`, `immutable`.                                                           |
| `tests_applicable`  | bool    | no       | Defaults to `true`. Set `false` to suppress `story-untested` and `test-evidence-missing`.       |
| `locked_sections`   | list    | no       | H2 sections requiring explicit confirmation before agent edit.                                  |
| `last_audited`      | ISO date | no      | Written only by `stories-audit --bump-clean`.                                                   |

### Validity Matrix

- `authority: observed` cannot have `change_resistance: high | immutable`
  (exit 3).
- `tests_applicable: false` with non-empty `Evidence.Tests` is exit 2.

### YAML Dialect

Strict subset, parsed by a stdlib-only parser bundled with storystore. No
PyYAML dependency.

Allowed:

- top-level mapping;
- scalars: unquoted strings, ISO dates, booleans;
- one list field, in flow `[a, b]` or block `- a` form.

Disallowed (parse error with line and column): anchors, aliases, nested
mappings, multi-line strings, flow mappings.

Unknown keys are errors. Enum values are checked.

## Body Sections

Six sections. `Intent` is hard-required; missing Intent is exit 2. The other
five are soft-required; audit and coverage findings are conditional on
content being present.

```markdown
# Human title

## Intent
One sentence. Soft-enforced ≤ 2 sentences; longer Intent emits a low-severity
nag finding.

## Story
Qualitative prose describing the user need and expected workflow.

## Expected Behavior
What the software should visibly do.

## Boundaries
What this story does not promise.

## Auditable Claims
- Concrete claim that can be checked against tests, commands, docs, source,
  or generated output.

## Evidence
### Tests
- `tests/...`
### Surface
- `cli: example`
- `POST /example`
### Docs
- `README.md`

## Drift Notes
Optional. Pointers to tracker issues or known unresolved mismatches.
```

The bar to *create* a story is just frontmatter + Intent. Audit and coverage
do what they can with what is present.

The canonical placeholder Intent for observed-mode stories that the agent
could not infer is the literal string:

```text
Inferred from code; not human-confirmed.
```

Coverage reports list slugs still carrying this placeholder.

## Authority And Change Resistance

Two independent axes.

`authority` answers "where did this story come from?":

- **observed** — inferred from current software behavior. Descriptive.
  `stories-generate --observed` writes this.
- **accepted** — human-approved intended behavior. `stories-generate
  --interview` writes this.

`change_resistance` answers "how cautious should agents be about meaning
changes?":

- **low** — routine updates allowed when evidence supports them.
- **medium** — agents must classify edit types and preserve meaning unless
  the user approves.
- **high** — agents must stop before changing intent, boundaries, or
  auditable claims.
- **immutable** — unconditionally agent-immutable for protected meaning.
  Allowed under immutable: `Evidence` refresh, `Drift Notes` append,
  `last_audited` bump via `stories-audit --bump-clean`. Not allowed: any
  agent change to `change_resistance`, `authority`, `status`, locked
  sections, inline locked blocks, or meaning anywhere. Humans edit the
  frontmatter directly to lower `change_resistance`.

## Status Lifecycle

- **draft** — incomplete or under review.
- **active** — current and relevant to the software.
- **deprecated** — intentionally obsolete. Tools may report stale matches
  but do not gate active work on it.

`stories-update` regenerates `INDEX.md` on title/status/authority/
change_resistance changes.

## Audit Findings

Emitted by `stories-audit`.

### Deterministic

| Kind                     | When emitted                                                            |
|--------------------------|--------------------------------------------------------------------------|
| `surface-missing`        | Declared `Evidence.Surface` ref does not resolve.                        |
| `test-evidence-missing`  | Declared `Evidence.Tests` ref does not resolve.                          |
| `claim-unsupported`      | Auditable claim has no deterministic evidence support.                   |
| `intent-conflict`        | Declared Intent contradicts deterministic evidence (severity fixed high). |

### Narrative (D-pass, opt-in, agent-emitted)

| Kind                  | When emitted                                                |
|-----------------------|-------------------------------------------------------------|
| `claim-contradicted`  | Narrative pass finds a claim contradicted by evidence.      |
| `story-ambiguous`     | Narrative pass finds the story body ambiguous.              |
| `documented-untested` | Narrative pass finds documented behavior with no tests.     |

### Informational

| Kind                     | When emitted                                                                 |
|--------------------------|------------------------------------------------------------------------------|
| `agent-pointer-missing`  | At least one root agent-instruction file exists and none contain the pointer. Severity fixed low; repo-level. Suppression marker `<!-- storystore: no-pointer -->` silences. |

### Severity

Severity derives from `change_resistance`:

```text
low       -> low
medium    -> medium
high      -> high
immutable -> high (flagged)
```

Fixed: `intent-conflict` is high; `agent-pointer-missing` is low.

### Common Finding Shape

```text
kind, story_slug (null for repo-level), severity,
suggested_action (fix-code | update-story | add-evidence | triage),
kind-specific body
```

### last_audited

Written only by `stories-audit --bump-clean`. Bump applies to stories with
zero findings in the run. With D-pass, includes narrative-clean. There is no
separate `mark_audited.py`.

### Modes

- `--story <slug>` (repeatable): scoped audit. Skips coverage findings and
  full inventory build; resolves only targeted stories' refs.
- `--bump-clean`: see above.
- `--thorough`: opt-in non-TS coverage. Agent supplies inferred surface JSON
  via `--inferred-surface <path>`. Inferred entries marked `[inferred]` in
  finding bodies. No severity reduction.
- `--strict`: exit 1 if findings exist (default exit 0).
- `--source-root <relative-path>`, `--include-dir <name>` (repeatable): scope
  inventory and findings to a subtree, or pull a directory back into the
  walk.

## Coverage Findings + Completeness Scoring

Emitted by `stories-coverage`.

### Deterministic Findings

| Kind                | When emitted                                                 |
|---------------------|--------------------------------------------------------------|
| `surface-uncovered` | A user-facing surface has no story.                          |
| `story-untested`    | An active story has no test evidence (unless `tests_applicable: false`). |
| `story-incomplete`  | An active story scores below `--completeness-min-rating`.    |

Default surface kinds: `cli-command,http-route,package-bin`. Public exports
are opt-in.

### Completeness Scoring

Five non-Intent dimensions: Story prose, Expected Behavior, Boundaries,
Auditable Claims, Evidence. Per-dimension levels: absent (0), minimal (1),
weak (4), sufficient (10). TODO/FIXME/XXX/TBD markers count as `absent`.

Boundaries:

```text
Story prose:        <20 / 20-49 / >=50 words
Expected Behavior:  <15 / 15-29 / >=30 words
Boundaries:         <10 / 10-19 / >=20 words
Auditable Claims:   1 / 2 / >=3 bullets
Evidence:           1 / 2 / >=3 refs OR refs in >=2 subsections
```

Score 0–50 maps to ratings:

```text
0-9   Skeletal
10-24 Sparse
25-34 Partial
35-44 Substantial
45-50 Complete
```

`coverage.py` emits `story-incomplete` for active stories below
`--completeness-min-rating` (default `substantial`), capped at
`--completeness-limit` (default 20), worst first. User-facing presentation
uses qualitative ratings; numeric scores stay in JSON for ranking.

### Language Coverage Header

Audit and coverage reports begin with a Language Coverage block. Detection
walks at depth ≤ 2 looking for marker files: `package.json` (TS/JS),
`go.mod` (Go), `Cargo.toml` (Rust), `pyproject.toml`/`setup.py` (Python),
`Gemfile` (Ruby), `pom.xml` (Java). When extractors don't cover detected
languages, the header suggests `--thorough`.

### Placeholder-Intent Note

Coverage report header includes a one-line note when active stories carry
the canonical placeholder Intent:

```markdown
N active stories have placeholder Intent: <slug-1>, <slug-2>, ...
```

## Impact Check Behavior Table

`stories-impact-check` reports affected stories before behavioral changes.
Hard trigger via SKILL.md description (hook-based enforcement deferred).

```text
active + immutable:    stop and ask before proceeding
active + high:         alert user, ask confirmation
active + medium:       mention affected stories; proceed unless user objects
active + low:          mention affected stories after the change is applied
authority: observed:   mention only; do not gate
status: draft:         mention only unless change_resistance is high/immutable
status: deprecated:    report as stale match; do not gate
```

### Inputs

- `--file <path>`: repeatable. OR-combined within and across dimensions.
- `--surface <ref>`: repeatable. OR-combined.
- `--description <text>`: at most one value; repeating is exit 2.

Cross-dimension is OR. The skill does not decide whether the change is
allowed; it surfaces relevant stories.

## Performance

Each runtime script (`audit.py`, `coverage.py`, `impact_check.py`) emits a
`performance` block in stdout JSON: `duration_ms`, `stories_scanned`,
`evidence_refs_resolved`, `phase_breakdown`.

Stderr threshold warning when over:

```text
impact_check    500 ms
audit/coverage  5000 ms
```

Override via env `STORYSTORE_PERF_WARN_MS` or flag `--perf-warn-ms`
(0 disables). `PerfTimer` helper lives in `storystore_lib.py`. No durable
perf log file in v1.

## Vendored And Monorepo Layouts

`inventory.py` ships `DEFAULT_SKIP_DIRS`: `.git`, `node_modules`, `vendor`,
`dist`, `build`, `out`, `target`, `__pycache__`, `.venv`, `venv`, `.tox`,
`.next`, `.nuxt`, `.cache`, `.pytest_cache`, `coverage`, `.idea`, `.vscode`.
Walks skip any directory whose basename is in the set. `audit.py` and
`coverage.py` accept `--source-root <relative-path>` and `--include-dir
<name>` (repeatable; removes from skip set for this run). No `.gitignore`
integration in v1.

## Exit Codes

```text
0  success
1  findings in --strict mode
2  invalid input, malformed story, missing repository setup, parse-time
   conflict (e.g., tests_applicable=false + non-empty Evidence.Tests),
   repeated --description on impact_check
3  policy refusal (locked-without-confirmation, immutable rule violation,
   meaning-change without --confirm-meaning-change, resistance-change
   without --confirm-resistance-change, validity-matrix violation)
4+ unexpected runtime error
```

Stderr always carries a one-line human-readable explanation.

## Non-Goals For V1

- No CI integration.
- No PreToolUse hook (description-driven hard trigger only).
- No semantic embedding for impact-check description matching.
- No language coverage beyond TypeScript without `--thorough`.
- No automatic CONVENTIONS.md refresh (CONVENTIONS.md is killed).
- No story migration logic (`schema_version: 1` is the only version).
- No durable perf log file.
- No impact-index cache.
- No `mark_audited.py` separate script (folded into `stories-audit
  --bump-clean`).
- No `group` field anywhere.
- No `--audit-report` reuse mechanism for `stories-update`.
- No git-history introspection for skipped-impact-check detection.
