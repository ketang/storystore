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

- `stories-init` — initialize `docs/stories/` for a repo.
- `stories-generate` — author a new story (interview or observed mode).
- `stories-audit` — story-to-software fidelity report.
- `stories-coverage` — software-to-story coverage report.
- `stories-update` — guarded edits to existing stories.
- `stories-impact-check` — pre-change lookup of affected stories.

## Repository Layout

```text
skills/<name>/SKILL.md        # canonical hand-edited skill source
scripts/build-plugin          # builds Claude and Codex plugin payloads
tests/                         # pytest suite
plugin-version.json           # single source of truth for the plugin version
spec.md                       # plugin schema and tooling reference
.claude-plugin/plugin.json    # generated Claude manifest
.claude/skills/<name>.md      # generated Claude per-skill flat files
.codex-plugin/plugin.json     # generated Codex manifest
.codex-plugin/skills/<name>/  # generated Codex per-skill payloads
```

## Build

```bash
scripts/build-plugin          # build manifests + skill payloads
scripts/build-plugin --bump   # bump patch version then build
scripts/build-plugin -v       # print each output path
```

## Install For Codex

From a checkout:

```bash
scripts/install-codex-plugin
```

Or directly from GitHub:

```bash
curl -fsSL https://raw.githubusercontent.com/ketang/storystore/main/scripts/install-codex-plugin | bash
```

Use `bash -s -- --help` after the pipe to see installer options, including
`--skip-register`, `--codex-home`, and `--marketplace-root`.

## Status

Bootstrap only. Skills are placeholder SKILL.md stubs. The phased
implementation lives in three plan documents:

- `2026-05-01-storystore-plan-1-foundation.md` — `stories-init`, `stories-generate`
- `2026-05-01-storystore-plan-2-fidelity.md` — `stories-audit`, `stories-coverage`
- `2026-05-01-storystore-plan-3-edits-and-impact.md` — `stories-update`, `stories-impact-check`

See `2026-05-01-storystore-target-design.md` and `spec.md` for the schema
and tooling reference.
