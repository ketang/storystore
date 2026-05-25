# Installing Storystore

## Claude Code

Claude Code discovers plugins from the `.claude-plugin/` directory at the
repository root. Storystore ships a pre-built `.claude-plugin/plugin.json`
manifest and per-skill flat files under `.claude/skills/`, so no installation
step is needed beyond cloning or checking out the storystore repository as a
Claude plugin source.

To use storystore as a plugin in another project's Claude Code session, add
the plugin via the Claude Code plugin system pointing at the storystore
repository. Claude Code will read `.claude-plugin/plugin.json` and load the
six `stories-*` skills automatically.

### Verifying Claude Plugin Layout

After a build, the Claude plugin files are:

```text
.claude-plugin/plugin.json       # plugin manifest (name, description, version)
.claude/skills/stories-init.md
.claude/skills/stories-generate.md
.claude/skills/stories-audit.md
.claude/skills/stories-coverage.md
.claude/skills/stories-update.md
.claude/skills/stories-impact-check.md
```

## Codex

Install Storystore for Codex from the public GitHub-hosted installer:

```bash
curl -fsSL https://raw.githubusercontent.com/ketang/storystore/main/scripts/install-codex-plugin | bash
```

Do not install from a local repository checkout. The public installer is the
supported installation method because it exercises the same downloaded payload
users receive and avoids stale local build artifacts.

To view installer options:

```bash
curl -fsSL https://raw.githubusercontent.com/ketang/storystore/main/scripts/install-codex-plugin | bash -s -- --help
```

### Installer Options

| Option | Default | Description |
|---|---|---|
| `--codex-home <path>` | `~/.codex` | Codex home directory to register against. |
| `--marketplace-root <path>` | `<codex-home>/marketplace` | Marketplace root directory. |
| `--skip-register` | off | Copy plugin files without running `codex marketplace register`. |
| `--install-policy <policy>` | `INSTALLED_BY_DEFAULT` | One of `AVAILABLE`, `INSTALLED_BY_DEFAULT`, `NOT_AVAILABLE`. |
| `--auth-policy <policy>` | `ON_INSTALL` | Codex auth policy for the plugin. |
| `--dry-run` | off | Print what would be done without writing files. |
| `--verbose` | off | Print each file operation. |

### Codex Plugin Layout

After installation, the Codex plugin files are placed under the marketplace:

```text
<marketplace-root>/storystore/
  plugin.json                         # Codex plugin manifest
  skills/stories-init/SKILL.md        # per-skill payload
  skills/stories-generate/SKILL.md
  skills/stories-audit/SKILL.md
  skills/stories-coverage/SKILL.md
  skills/stories-update/SKILL.md
  skills/stories-impact-check/SKILL.md
  skills/<name>/scripts/              # shared runtime scripts per skill
  skills/<name>/references/           # shared reference docs per skill
```

## Building From Source

Prerequisites: Python 3.10+ (no third-party dependencies for the build).

```bash
git clone https://github.com/ketang/storystore.git
cd storystore
scripts/build-plugin -v
```

This regenerates all Claude and Codex plugin outputs from the canonical
`skills/<name>/SKILL.md` sources and the shared Python runtime under
`shared/`. The version is read from `plugin-version.json`.

To bump the patch version and rebuild:

```bash
scripts/build-plugin --bump
```

To materialize shared scripts into skill directories without a full rebuild:

```bash
scripts/build-plugin --shared-only
```

### Running Tests

```bash
python3 -m pytest tests/ -x -q
```
