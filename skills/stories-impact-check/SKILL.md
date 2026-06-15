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

## Command

```bash
stories-impact-check/scripts/impact_check.py \
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

## Non-Goals

- No PreToolUse hook enforcement. Activation is description-driven.
- No git-introspection signal. The skill does not read the working tree
  diff; pass `--file` explicitly.
- No `--record` mode. The skill does not write anywhere.

## Cross-References

- Implementation contract: `shared/spec.md` and
  `2026-05-01-storystore-plan-3-edits-and-impact.md`.
- `stories-update` requires that any code change implementing a story be
  preceded by `stories-impact-check`.
