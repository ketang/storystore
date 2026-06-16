---
name: stories-impact-check
description: |
  Hard trigger when docs/stories/INDEX.md exists — before any behavioral
  change to user-facing surfaces, run this skill. Finds stories affected by
  planned file, surface, or behavior changes and reports their status,
  authority, change resistance, locked sections, and intent excerpt so the
  agent can alert the user before proceeding. Skip when the repo has no
  docs/stories/INDEX.md. Read-only.
---

# stories-impact-check

Answers: **which active stories does this planned behavioral change affect?**

This skill is a hard trigger **in repos that have a story corpus**. Run it
before any behavioral change to a user-facing surface — endpoints, CLI
commands, UI flows, public library APIs, observable error messages, or
anything else a user can directly perceive.

## Existence Gate

The hard trigger is gated on `docs/stories/INDEX.md` existing. If the repo
has no `docs/stories/INDEX.md`, this skill has nothing to gate — skip it and
proceed with the change. Do not invoke it in repos that have not run
`stories-init`.

Note that a corpus can exist yet gate nothing: a freshly seeded corpus
contains only `authority: observed` stories, which are informational and
never block (see the Agent Behavior Table). Gating becomes active only once
stories are promoted `observed → accepted` via human review — see the
promotion path documented in `stories-init`.

The skill is **read-only**. It inspects `docs/stories/` and surface inventory
under the repo root; it never edits, creates, or deletes story files. Use
`stories-update` or `stories-generate` when story edits are required.

## When To Run

Run before edits when any of the following is true:

- You are about to modify a file that is referenced as test evidence by a
  story.
- You are about to change a source file that defines a claimed user-facing
  surface (route, command, exported API).
- You are about to change a surface ref directly (rename a route, change a
  CLI flag, alter an error message).
- The user describes a behavioral change in prose, even if you do not yet
  know which files are involved.

Run on the description alone if file or surface details are not yet
determined. Re-run as scope sharpens.

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

## Command

```bash
python3 "$STORYSTORE_SHARED/impact_check.py" \
  --repo-root <repo-root> \
  [--file <path>]... \
  [--surface <ref>]... \
  [--description <text>] \
  [--perf-warn-ms <ms>]
```

Flags:

- `--repo-root` — required. Repo root containing `docs/stories/`.
- `--file <path>` — repeatable. File the change will touch.
- `--surface <ref>` — repeatable. Surface reference (e.g. `POST /login`,
  `cli:foo bar`, exported symbol).
- `--description <text>` — at most one. Free-text description of the
  behavioral change. Repeating this flag is exit 2.
- `--perf-warn-ms <ms>` — override the 500 ms stderr threshold. `0` disables
  it. Also respects `STORYSTORE_PERF_WARN_MS`.

Cross-dimension matching is OR: a story matches if any `--file`, any
`--surface`, or the `--description` matches. Pass as many `--file` and
`--surface` as you have; pass at most one `--description`.

## Output

Stdout is a JSON object:

```json
{
  "matches": [
    {
      "story_slug": "login",
      "title": "Login",
      "status": "active",
      "authority": "accepted",
      "change_resistance": "high",
      "locked_sections": ["Intent"],
      "intent_excerpt": "Users sign in so the system can attribute actions.",
      "matched_via": ["surface: POST /login"]
    }
  ],
  "performance": {
    "duration_ms": 0,
    "stories_scanned": 0,
    "evidence_refs_resolved": 0,
    "phase_breakdown": {}
  }
}
```

`matched_via` enumerates each independent reason a story matched (file,
surface, or description token).

Exit codes:

```text
0  success (including empty matches)
2  invalid input (repeated --description, missing --repo-root, malformed story)
4+ unexpected runtime error
```

A stderr warning fires when `performance.duration_ms` exceeds the threshold.

## Matching Rules

- A `--file` matches if the path appears as test evidence on a story, or if
  it is a source file that defines a claimed surface on a story.
- A `--surface` matches if it equals (normalized) a surface ref claimed by
  a story, or appears in the story's evidence.
- A `--description` is tokenized and compared against each story's
  `Intent`, `Story`, `Expected Behavior`, and `Auditable Claims` sections.
  Matching is simple token overlap; semantic embeddings are out of scope.

## Agent Behavior Table

The skill surfaces matches and their resistance level. It does **not** decide
whether the change is allowed — the operating agent and the user do.

| Match                                     | Agent behavior                                                                          |
| ----------------------------------------- | --------------------------------------------------------------------------------------- |
| `active` + `change_resistance: immutable` | **Block.** Stop and require explicit human acceptance before proceeding. Do not edit.   |
| `active` + `change_resistance: high`      | Alert the user with the affected story slug and intent excerpt; require explicit user acknowledgment before editing. |
| `active` + `change_resistance: medium`    | Flag the affected story to the user and proceed with a noted impact; pause only if the user objects. |
| `active` + `change_resistance: low`       | Informational. Mention the affected story after the change is applied.                  |
| `authority: observed`                     | Informational only. Non-gating regardless of resistance. Mention; never block.          |
| `status: draft`                           | Mention only, unless `change_resistance` is `high` or `immutable`, in which case treat as the matching active row. |
| `status: deprecated`                      | Report as a **stale match**. Do not gate. Consider whether the deprecation is still accurate. |

Observed-authority handling is a hard rule: even if an `observed` story
carries `change_resistance: high`, the change is not gated — observed stories
record what the software does today, not committed intent, so they cannot
require user acknowledgment. They surface as context only.

## Mechanical Trigger (impact_trigger.py)

The skill above is description-driven: it fires only when an agent remembers
to run it. For changes that should warn *automatically*, a companion script
ships alongside it:

```bash
python3 "$STORYSTORE_SHARED/impact_trigger.py" \
  --repo-root <repo-root> \
  [<changed-path>...] \
  [--hook] [--json] [--exit-code]
```

Given the repo-relative paths a change will touch, it matches each path
against **every story's evidence refs treated as path prefixes / globs** and
prints a non-blocking warning naming the affected stories. Matching is purely
mechanical — exact, directory-prefix (`web/src/pages/` matches
`web/src/pages/Closet.tsx`), or `fnmatch` glob — so it complements, not
replaces, the semantic matching in `impact_check.py`.

It is designed to be **wired into something automatic** and to **fail open**:
a missing `docs/stories/`, an unreadable or malformed story, or any internal
error yields no warning and never a non-zero exit (unless `--exit-code` is set
*and* a real match is found). It must never stand between an agent and an
unrelated edit.

Input channels:

- **Positional args** — repo-relative or absolute changed paths.
- **Stdin (newline-delimited)** — e.g. `git diff --name-only | impact_trigger.py --repo-root .`.
- **`--hook`** — reads a Claude Code PreToolUse JSON payload on stdin and
  extracts paths from `tool_input.file_path` / `notebook_path`, and from
  `tool_input.command` tokens (so a `git mv` rename surfaces the old path).

Flags:

- `--json` — emit `{"affected": [...]}` instead of a human warning.
- `--exit-code` — exit 1 when a story is affected (for CI/pre-commit gating).
  Internal errors still exit 0; failing open always wins.

### Installing as a Claude Code PreToolUse hook

Resolve `STORYSTORE_SHARED` once (see *Locating storystore scripts*), then add
a `PreToolUse` hook to your project's `.claude/settings.json` matching the
file-mutating tools. The hook is non-blocking — it emits a warning on stderr
and exits 0:

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Write|Edit|MultiEdit|Bash",
        "hooks": [
          {
            "type": "command",
            "command": "python3 \"$STORYSTORE_SHARED/impact_trigger.py\" --repo-root \"$CLAUDE_PROJECT_DIR\" --hook"
          }
        ]
      }
    ]
  }
}
```

Substitute the absolute `impact_trigger.py` path for `$STORYSTORE_SHARED/...`
in the JSON (settings files do not expand the shell variable). To gate a
pre-commit hook or CI step instead, drop `--hook`, pipe in changed paths, and
add `--exit-code`:

```bash
git diff --cached --name-only | \
  python3 /path/to/impact_trigger.py --repo-root . --exit-code
```

## Non-Goals

- The **skill** does no PreToolUse hook enforcement; its activation is
  description-driven. Automatic, mechanical warnings are the job of the
  separate `impact_trigger.py` artifact documented above.
- No git-introspection signal. Neither the skill nor the trigger reads the
  working tree diff; pass `--file`/changed paths explicitly.
- No `--record` mode. Nothing here writes to story files.

## Cross-References

- Implementation contract: `shared/spec.md`.
- `stories-update` requires that any code change implementing a story be
  preceded by `stories-impact-check`.
