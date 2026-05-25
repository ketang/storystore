# storystore

A Claude/Codex plugin for durable, prose-first **intent stories** in software
repositories. Intent stories describe what software is for from a user
perspective. They live under `docs/stories/` in consumer repos. They are
written for humans first and agents second, but they have enough structure
for agents to audit them, find affected stories before behavioral changes,
and avoid silently rewriting intent to match implementation drift.

The core rule:

> Code and tests are evidence. They are not automatic authority to rewrite
> accepted intent.

## Skills

Six skills, all named `stories-*`:

| Skill | Description |
|---|---|
| `stories-init` | Initialize `docs/stories/` for a repository — creates the directory, writes README and INDEX stubs, gitignores `drift-todo.md`, and seeds top observed-mode stories on fresh init. |
| `stories-generate` | Author a new intent story in interview mode (`authority: accepted`) or observed mode (`authority: observed`). Includes independent LLM-driven editorial review before promotion. Regenerates `INDEX.md`. |
| `stories-audit` | Read-only story-to-software fidelity report. Deterministic findings: `surface-missing`, `test-evidence-missing`, `claim-unsupported`, `intent-conflict`. Optional narrative D-pass: `claim-contradicted`, `story-ambiguous`, `documented-untested`. Supports `--strict`, `--bump-clean`, `--thorough`, and monorepo scoping. |
| `stories-coverage` | Read-only software-to-story coverage report. Findings: `surface-uncovered`, `story-untested`, `story-incomplete`. Completeness scoring across five dimensions (Skeletal through Complete). Default surface kinds: `cli-command`, `http-route`, `package-bin`. |
| `stories-update` | Guarded editing for existing stories. Runs scoped audit before edits, blocks silent updates on stories with audit findings, and enforces locked-section and meaning-change policies. Regenerates `INDEX.md` on metadata changes. |
| `stories-impact-check` | Hard-trigger pre-change lookup. Reports stories affected by planned file, surface, or behavior changes with status, authority, change resistance, and intent excerpt. Read-only. |

## Repository Layout

```text
skills/<name>/SKILL.md        # canonical hand-edited skill source
shared/                        # Python runtime (audit, coverage, inventory, etc.)
scripts/build-plugin          # builds Claude and Codex plugin payloads
scripts/install-codex-plugin  # public installer for Codex
tests/                         # pytest suite
plugin-version.json           # single source of truth for the plugin version
spec.md                       # plugin schema and tooling reference
```

Generated outputs (committed, rebuilt by `scripts/build-plugin`):

```text
.claude-plugin/plugin.json    # Claude plugin manifest
.claude/skills/<name>.md      # Claude per-skill flat files (skill + shared scripts inlined)
.codex-plugin/plugin.json     # Codex plugin manifest
.codex-plugin/skills/<name>/  # Codex per-skill payloads (SKILL.md + shared scripts)
```

## Build

```bash
scripts/build-plugin          # build manifests + skill payloads
scripts/build-plugin --bump   # bump patch version then build
scripts/build-plugin -v       # print each output path
scripts/build-plugin --shared-only  # materialize shared scripts without full rebuild
```

## Test

```bash
python3 -m pytest tests/ -x -q
```

## Install

See [INSTALL.md](INSTALL.md) for step-by-step installation instructions for
Claude Code and Codex.

## Schema Reference

See [spec.md](spec.md) for the full story frontmatter schema, body sections,
finding kinds, severity rules, completeness scoring, exit codes, and
performance contracts.
