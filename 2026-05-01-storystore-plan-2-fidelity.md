# storystore Plan 2: Shared Modules, Audit, And Coverage

**Date:** 2026-05-01 (revised)
**Status:** Target implementation plan.

## Goal

Ship read-only fidelity and coverage reports:

- `stories-audit`
- `stories-coverage`

This phase also adds shared-script packaging to `scripts/build-plugins`, ships
the bundled stdlib frontmatter parser, introduces the canonical shared
storystore library, and ships the plugin spec doc that replaces
`CONVENTIONS.md`.

## Files

New shared files:

```text
catalog/shared/storystore/storystore_lib.py
catalog/shared/storystore/inventory.py
catalog/shared/storystore/spec.md
```

New skill files:

```text
catalog/skills/stories-audit/SKILL.md
catalog/skills/stories-audit/packaging.json
catalog/skills/stories-audit/scripts/audit.py

catalog/skills/stories-coverage/SKILL.md
catalog/skills/stories-coverage/packaging.json
catalog/skills/stories-coverage/scripts/coverage.py
```

New tests:

```text
tests/test_build_plugins_shared_scripts.py
tests/test_storystore_lib.py
tests/test_storystore_inventory.py
tests/test_storystore_audit.py
tests/test_storystore_coverage.py
tests/test_storystore_completeness.py
tests/fixtures/storystore_audit_repo/
```

Modified files:

```text
scripts/build-plugins
catalog/plugin-versions.json
tests/conftest.py    # auto-materialize shared scripts on first test run
```

## Shared Script Packaging

Skills declare shared scripts in `packaging.json`. Both audit and coverage
declare:

```json
{
  "shared_scripts": [
    "storystore/storystore_lib.py",
    "storystore/inventory.py",
    "storystore/spec.md"
  ]
}
```

`spec.md` is materialized into each skill's `references/` directory.
`storystore_lib.py` and `inventory.py` are materialized into each skill's
`scripts/` directory.

`scripts/build-plugins` must:

1. Copy the canonical skill directory as today.
2. Read `packaging.json`.
3. Materialize declared shared files into:
   - the canonical skill dir (`catalog/skills/<skill>/scripts/<file>` or
     `references/<file>`, gitignored), and
   - the generated plugin output dir (existing behavior).
4. Apply sorted (lexicographic) copy order.
5. Reject malformed `packaging.json`, `shared_scripts` not a list, non-string
   entries, paths escaping `catalog/shared/`, missing shared files, and
   filename collisions with skill-owned scripts.
6. Preserve executable mode.
7. Continue excluding `packaging.json` from generated output.
8. Support `--shared-only` fast mode for tests/dev (idempotent; skips full
   plugin rebuild).

`tests/conftest.py` auto-materializes shared scripts into canonical skill
dirs on first test run if missing. Scripts can `import storystore_lib`
naturally with no path tricks.

The field name is `shared_scripts`. (Not `shared_modules`.)

Build tests must cover:

- shared scripts are materialized into both canonical skill dirs and
  generated plugin outputs;
- materialization is idempotent;
- `--shared-only` mode works without full plugin rebuild;
- invalid shared path fails;
- collision with a skill-owned script fails;
- malformed `packaging.json` fails;
- non-string shared entry fails;
- `packaging.json` is not copied to plugin output;
- executable mode is preserved.

## Frontmatter Parser

Bundled stdlib only. No PyYAML. `parse_frontmatter` lives in
`storystore_lib.py`.

Strict YAML subset:

- top-level mapping;
- scalars (unquoted strings, ISO dates, booleans);
- one list field, flow `[a, b]` or block `- a` form.

Disallowed: anchors, aliases, nested mappings, multi-line strings, flow
mappings. Unknown keys are errors. Enum values checked. Implementation is a
lexer + parser, not regex chains. Error messages include line and column.

## storystore_lib.py

Responsibilities:

- parse YAML frontmatter (strict subset);
- validate enum fields and validity matrix
  (`authority: observed` excludes `change_resistance: high | immutable`;
  `tests_applicable: false` with non-empty `Evidence.Tests` is exit 2);
- parse H2 sections;
- parse inline locked blocks;
- extract evidence subsections;
- load all story files under `docs/stories/`;
- skip `README.md`, `INDEX.md`, `drift-todo.md`;
- expose structured `Story` and `LockedBlock` dataclasses;
- expose `PerfTimer` helper for performance instrumentation.

Expected parsed story fields:

```text
path
schema_version
title
slug
status
authority
change_resistance
tests_applicable
locked_sections
last_audited
sections
locked_blocks
evidence_tests
evidence_surface
evidence_docs
```

Validation:

- missing frontmatter is exit 2;
- missing Intent is exit 2;
- invalid enum values are exit 2;
- `locked_sections` must be a list;
- validity-matrix violations are exit 3;
- everything else (missing optional sections, sparse content) is reported as
  audit/coverage findings, not parser failure.

## inventory.py

Responsibilities:

- extract user-facing surface inventory;
- extract test inventory;
- resolve story evidence references;
- normalize surface references for matching;
- detect repository languages.

Initial extractors (TypeScript-first):

- TypeScript CLI commands (commander-style);
- TypeScript HTTP route declarations;
- package `bin`;
- package `exports`;
- TypeScript e2e/spec/test names;
- README/DESIGN sections where useful.

Unsupported languages cleanly return empty surface inventory. Language-agnostic
checks (resolving test globs, README headings) still run.

### Language Detection

`detect_languages(repo_root) -> {"detected": [...], "extracted": [...]}`
walks at depth ≤ 2 looking for marker files:

```text
package.json    -> typescript, javascript
go.mod          -> go
Cargo.toml      -> rust
pyproject.toml  -> python
setup.py        -> python
Gemfile         -> ruby
pom.xml         -> java
```

`extracted` lists the subset that the bundled extractors actually cover
(initially: typescript, javascript). `audit.py` and `coverage.py` use this to
emit a Language Coverage block at the top of every report; when extractors
don't cover detected languages, the header suggests `--thorough`.

### Vendored And Monorepo Layouts

Ship `DEFAULT_SKIP_DIRS`: `.git`, `node_modules`, `vendor`, `dist`, `build`,
`out`, `target`, `__pycache__`, `.venv`, `venv`, `.tox`, `.next`, `.nuxt`,
`.cache`, `.pytest_cache`, `coverage`, `.idea`, `.vscode`. Walk skips any
directory whose basename is in the set. `audit.py`/`coverage.py` accept
`--source-root <relative-path>` and `--include-dir <name>` (repeatable;
removes from skip set for this run). No `.gitignore` integration in v1.

## stories-audit

Question:

> Do existing stories still accurately describe the software surfaces and
> evidence they claim?

Default command:

```bash
stories-audit/scripts/audit.py --repo-root <repo-root> --report-path <path>
```

Default report path:

```text
/tmp/stories-audit-<timestamp>.md
```

### Modes

- default: full repo audit.
- `--story <slug>` (repeatable): scoped audit. Skips coverage findings and
  full inventory build; resolves only targeted stories' refs. Used by
  `stories-update` (Plan 3).
- `--bump-clean`: writes `last_audited` for stories with zero findings in the
  run. With D-pass, includes narrative-clean. There is no separate
  `mark_audited.py`.
- `--thorough`: opt-in non-TS coverage. Agent reads codebase, builds inferred
  surface JSON, passes via `--inferred-surface <path>`. Inferred entries
  marked `[inferred]` in finding bodies. No severity reduction.
- `--strict`: exit 1 if findings exist (default exit 0).
- `--perf-warn-ms N` / env `STORYSTORE_PERF_WARN_MS`: override perf threshold
  (default 5000ms; 0 disables).

### Finding Kinds

Audit deterministic:

```text
surface-missing
test-evidence-missing
claim-unsupported
intent-conflict
```

Audit narrative (D-pass, opt-in, agent-emitted):

```text
claim-contradicted
story-ambiguous
documented-untested
```

Audit informational:

```text
agent-pointer-missing
```

Dropped: `narrative-drift`, `confirmed`, `surface-missing`/
`evidence-tests-missing` fold from the revisions doc.

D-pass is opt-in and must not be automatic. If the runtime requires explicit
permission for subagents, the skill asks before doing it.

`agent-pointer-missing`: emitted when at least one of `AGENTS.md`,
`CLAUDE.md`, `GEMINI.md` exists at repo root and none contain the pointer
marker phrase. Suppression marker `<!-- storystore: no-pointer -->` in any
present agent-instruction file silences the finding. Severity fixed low,
repo-level (`story_slug: null`).

### Severity Mapping

Severity derives from `change_resistance`:

```text
low       -> low
medium    -> medium
high      -> high
immutable -> high (flagged)
```

Fixed severities: `intent-conflict` is high; `agent-pointer-missing` is low.

### tests_applicable Opt-Out

`tests_applicable: false` suppresses `story-untested` and
`test-evidence-missing` for that story. Parse-time conflict
(`tests_applicable: false` with non-empty `Evidence.Tests`) is exit 2.

### Output

Reports are Markdown. Each finding is a structured Markdown subsection:

```markdown
## Finding 1: <title>

- kind: surface-missing
- story_slug: login
- severity: medium
- suggested_action: fix-code | update-story | add-evidence | triage

<body>
```

Stdout JSON includes:

```json
{
  "report_path": "/tmp/...",
  "findings_count": 0,
  "performance": {
    "duration_ms": 0,
    "stories_scanned": 0,
    "evidence_refs_resolved": 0,
    "phase_breakdown": {}
  }
}
```

Reports begin with a Language Coverage block:

```markdown
## Language Coverage

Detected: typescript, go
Extracted: typescript
Note: go is detected but not covered by built-in extractors. Re-run with
--thorough to author inferred surface coverage.
```

## stories-coverage

Question:

> What user-facing software behavior lacks story coverage?

Default command:

```bash
stories-coverage/scripts/coverage.py --repo-root <repo-root> --report-path <path>
```

### Finding Kinds

Coverage deterministic:

```text
surface-uncovered
story-untested
story-incomplete
```

Dropped: `test-scenario-uncovered`.

Default surface kinds:

```text
cli-command,http-route,package-bin
```

Public exports are opt-in.

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

`coverage.py` emits `story-incomplete` for active stories rated below
`--completeness-min-rating` (default `substantial`), capped at
`--completeness-limit` (default 20), worst first.

User-facing presentation uses qualitative ratings; numeric scores stay in
JSON for ranking.

Coverage report header includes a one-line note when active stories carry
the canonical placeholder Intent string `Inferred from code; not
human-confirmed.`:

```markdown
N active stories have placeholder Intent: <slug-1>, <slug-2>, ...
```

### Modes

- `--strict`: exit 1 if findings exist.
- `--thorough`: same as audit; agent supplies inferred surface JSON.
- `--source-root` / `--include-dir`: as for audit.
- `--perf-warn-ms` / env `STORYSTORE_PERF_WARN_MS`: same threshold semantics.

## Performance Instrumentation

Every script (`audit.py`, `coverage.py`, and `impact_check.py` in Plan 3)
emits a `performance` block in stdout JSON: `duration_ms`, `stories_scanned`,
`evidence_refs_resolved`, `phase_breakdown`. Stderr threshold warning when
over: audit/coverage 5000ms (impact-check 500ms in Plan 3). Threshold
overridable via env `STORYSTORE_PERF_WARN_MS` or flag `--perf-warn-ms`
(0 disables). `PerfTimer` helper lives in `storystore_lib.py`. No durable
perf log file in v1.

## Exit Codes

```text
0  success
1  findings in --strict mode
2  invalid input, malformed story, missing repository setup, parse-time
   conflict (e.g., tests_applicable=false + non-empty Evidence.Tests)
3  policy refusal (validity-matrix violation)
4+ unexpected runtime error
```

Stderr always carries a one-line human-readable explanation.

## Tests

Add tests for:

- shared script materialization into canonical and generated dirs;
- `--shared-only` build mode;
- malformed `packaging.json` and invalid shared paths;
- bundled frontmatter parser: strict subset, line/column errors, unknown-key
  rejection, enum validation, validity matrix, `tests_applicable` conflict;
- section parsing;
- locked block parsing;
- evidence extraction;
- TypeScript inventory extraction;
- evidence glob resolution;
- language detection (marker files at depth ≤ 2);
- DEFAULT_SKIP_DIRS skipping vendored layouts;
- audit findings: `surface-missing`, `test-evidence-missing`,
  `claim-unsupported`, `intent-conflict`;
- audit informational `agent-pointer-missing` (and suppression marker);
- audit `--bump-clean` writes `last_audited` only on zero-finding stories;
- audit `--story <slug>` scoped mode skips coverage findings;
- audit `--thorough` accepts inferred surface JSON and marks `[inferred]`;
- audit default exit 0 with findings, `--strict` exit 1;
- coverage findings: `surface-uncovered`, `story-untested`,
  `story-incomplete`;
- completeness scoring boundaries (per-dimension and aggregate);
- TODO/FIXME/XXX/TBD count as absent;
- coverage `--completeness-min-rating` and `--completeness-limit`;
- coverage placeholder-Intent header note;
- coverage default and `--strict` exit codes;
- performance block emitted; stderr warning fires above threshold; env and
  flag override threshold.

## Verification

Run:

```bash
python3 -m unittest tests.test_build_plugins_shared_scripts -v
python3 -m unittest tests.test_storystore_lib -v
python3 -m unittest tests.test_storystore_inventory -v
python3 -m unittest tests.test_storystore_audit -v
python3 -m unittest tests.test_storystore_coverage -v
python3 -m unittest tests.test_storystore_completeness -v
python3 -m unittest discover -s tests -t .
scripts/build-plugins
```

Smoke tests:

```bash
TMPDIR=$(mktemp -d)
cp -r tests/fixtures/storystore_audit_repo/. "$TMPDIR/"
mkdir -p "$TMPDIR/docs/stories"
# write a minimal story fixture, then:
catalog/skills/stories-audit/scripts/audit.py --repo-root "$TMPDIR" --report-path "$TMPDIR/audit.md"
catalog/skills/stories-coverage/scripts/coverage.py --repo-root "$TMPDIR" --report-path "$TMPDIR/coverage.md"
test -f "$TMPDIR/audit.md"
test -f "$TMPDIR/coverage.md"
rm -rf "$TMPDIR"
```

## Done Criteria

- Shared storystore modules canonical under `catalog/shared/storystore/`,
  including `spec.md`.
- Build script materializes shared files into both canonical skill dirs and
  plugin outputs; `--shared-only` works.
- Bundled stdlib frontmatter parser ships in `storystore_lib.py`; no PyYAML.
- `stories-audit` and `stories-coverage` are registered, built, and emit the
  locked finding kinds.
- `--bump-clean`, `--story`, `--thorough`, `--strict`, and perf flags work.
- Completeness scoring system works end-to-end and emits `story-incomplete`.
- Reports are Markdown with Language Coverage header.
- Default and strict exit behavior match the target design.
- Tests and build pass.
