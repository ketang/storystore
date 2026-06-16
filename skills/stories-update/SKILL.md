---
name: stories-update
description: Guarded editing for existing stories — runs scoped audit before edits, blocks silent updates on stories with audit findings, and enforces locked-section and meaning-change policies.
---

# stories-update

Guarded editing for existing intent stories. Enforces scoped audit before
any edit, drift triage before silent updates, and structural backstops on
locked sections and meaning changes.

## When To Run

Run when you need to edit an existing story file under `docs/stories/`. Do
not use this skill to create new stories (use `stories-generate`) or to run
read-only checks (`stories-audit`, `stories-impact-check`).

## Before Editing

Use the repo's required branch/worktree flow. If none is documented, use a
dedicated feature branch in a linked worktree outside the repo. If the repo
uses an issue tracker with claim semantics, claim the relevant issue before
edits.

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

## Required Flow

### 1. Identify Target Stories

Identify the story slug(s) that need editing. One slug per edit pass.

### 2. Run Scoped Audit

Before any edit, run a scoped audit against the targeted story:

```bash
python3 "$STORYSTORE_SHARED/audit.py" --repo-root <repo-root> --story <slug>
```

This is non-negotiable. Every edit pass starts with a scoped audit. There
is no `--audit-report` reuse mechanism — re-run for each edit session.

### 3. Triage Audit Findings (Drift Classification)

If the scoped audit returns findings on the targeted story, present them to
the user and classify each finding as one of:

- **Code-side bug** — the software diverged from the story's intent. The
  story is correct; the code needs fixing.
- **Story-side drift** — the story no longer accurately describes the
  software. The story needs updating.

Classify one finding at a time. Do not batch-classify.

### 4. Route Code-Side Bugs to Drift Todo

For findings classified as code-side bugs, append to the drift todo via
`drift_todo.py` and do **not** edit the story:

```bash
PYTHONPATH="$STORYSTORE_SHARED" python3 - <<'PY'
from drift_todo import append_drift_todo

append_drift_todo(
    slug="<story-slug>",
    description="<human-readable description of the mismatch>",
    metadata={"finding_kind": "<kind>", "suggested_action": "fix-code"},
)
PY
```

The drift todo defaults to `docs/stories/drift-todo.md` (gitignored;
append-only). After writing, suggest `beads-issue-flow` or
`github-issue-flow` for tracker intake.

Do not edit the story for code-side bugs. The story records intended
behavior; the code is what diverged.

### 5. Check Lock State

Before applying any story-side edit, check the lock state:

```bash
python3 "$STORYSTORE_SHARED/lock_check.py" --repo-root <repo-root> --slug <slug> --section <section>
```

Review the output:

```json
{
  "story_slug": "login",
  "title": "Login",
  "status": "active",
  "authority": "accepted",
  "change_resistance": "high",
  "locked_sections": ["Intent"],
  "inline_locked_blocks": [],
  "immutable": false,
  "auditable_claims_count": 3,
  "section": "Intent",
  "section_locked": true
}
```

If `immutable` is true, only evidence refresh, drift-note append, and
`last_audited` bump (via `stories-audit --bump-clean`) are allowed. No
agent change to `change_resistance`, `authority`, `status`, locked
sections, inline locked blocks, or meaning anywhere. Stop and tell the
user; humans must edit frontmatter directly to lower `change_resistance`.

### 6. Classify the Edit

State the edit classification before applying any story-side edit:

```text
evidence-refresh       — updating Evidence refs to match current paths
review-metadata        — updating title, status, or non-protected metadata
drift-note             — appending to Drift Notes section
prose-clarification    — rewording for clarity without changing meaning
claim-change           — adding, removing, or altering Auditable Claims
boundary-change        — changing Boundaries section meaning
intent-change          — changing Intent section meaning
authority-change       — changing authority field
resistance-change      — changing change_resistance field
```

Edit classification is honor-system. There is no `classify_edit.py` and
`lock_check.py` does not compare diffs. Structural backstops in
`edit_section.py` enforce the hard gates independently.

### 7. Apply Change Resistance Gates

Before applying the edit, check that the edit class is permitted for the
story's `change_resistance` level:

**low:**
- Allowed: evidence-refresh, review-metadata, drift-note, prose-clarification
- Meaning changes (claim-change, boundary-change, intent-change,
  authority-change, resistance-change) require explicit user approval

**medium:**
- Same as low, but the agent must present the edit classification to the
  user before applying any edit

**high:**
- Allowed without approval: evidence-refresh, review-metadata, drift-note
- Requires explicit user approval: claim-change, boundary-change,
  intent-change, authority-change, resistance-change
- Locked sections require explicit approval by section name

**immutable:**
- Allowed: evidence-refresh, drift-note append, `last_audited` bump (via
  `stories-audit --bump-clean`)
- No agent change to `change_resistance`, `authority`, `status`
- No agent edit to any locked section or inline locked block
- No agent-authored meaning changes anywhere
- Human must edit frontmatter directly to lower `change_resistance`

### 8. Cross-Reference Impact Check

When the edit involves a behavioral change to a user-facing surface, run
`stories-impact-check` before applying code changes:

```bash
python3 "$STORYSTORE_SHARED/impact_check.py" \
  --repo-root <repo-root> \
  [--file <path>]... \
  [--surface <ref>]... \
  [--description <text>]
```

Follow the agent behavior table in the `stories-impact-check` skill for
gating decisions based on `change_resistance` and `authority`.

### 9. Apply the Edit

Apply the edit via `edit_section.py`:

```bash
python3 "$STORYSTORE_SHARED/edit_section.py" \
  --repo-root <repo-root> \
  --story <slug> \
  --section <section> \
  --content <new-content> \
  [--allow-claim-reduction] \
  [--confirm-resistance-change]
```

`--section` accepts either:
- A body section heading: `Intent`, `Story`, `Expected Behavior`,
  `Boundaries`, `Auditable Claims`, `Evidence`, `Drift Notes`
- A metadata frontmatter field: `title`, `status`, `authority`,
  `change_resistance`

Flags:
- `--allow-claim-reduction` — required when `Auditable Claims` bullet count
  would decrease
- `--confirm-resistance-change` — required when increasing
  `change_resistance` to a higher level

Exit codes:
- `0` — success
- `2` — invalid input or malformed story
- `3` — policy refusal (locked section, inline locked block, claim
  reduction without flag, immutable story)
- `4` — resistance-change confirmation required

On success, stdout JSON:

```json
{"edited": true, "section": "Story", "index_updated": false}
```

`edit_section.py` regenerates `INDEX.md` automatically when title, status,
authority, or change_resistance changes. It does **not** bump
`last_audited` — that changes only via `stories-audit --bump-clean`.

### 10. Repeat or Finish

For multiple edits to the same story, repeat from step 5 (lock check) for
each section. For edits to a different story, restart from step 2 (scoped
audit).

## Finish

Review the diff, rebuild generated plugin/docs outputs when the repo
tracks them, run the selected verification command, and report the
branch/worktree, tracker item, and verification result.

**Commit the edited story.** Stories are durable repo artifacts, not
scratch output. Commit the edited `docs/stories/<slug>.md` (and the
regenerated `docs/stories/INDEX.md` when title, status, authority, or
change_resistance changed) on the current branch so the edit is tracked
rather than left untracked. Do not commit `docs/stories/drift-todo.md` (it
is gitignored). Branch and worktree conventions belong to the host repo's
workflow and are out of scope here — commit on whatever branch the update
ran on.

## Structural Backstops

These are enforced by `edit_section.py` regardless of the honor-system
classification:

1. **Locked sections** — editing a section listed in `locked_sections`
   exits 3
2. **Inline locked blocks** — editing a section containing
   `<!-- lock:begin -->` / `<!-- lock:end -->` markers exits 3
3. **Auditable Claims bullet-count guard** — reducing bullet count without
   `--allow-claim-reduction` exits 3
4. **Immutable stories** — any agent edit beyond the allowlist (evidence
   refresh, drift-note append, `last_audited` bump) exits 3
5. **Resistance increase** — increasing `change_resistance` without
   `--confirm-resistance-change` exits 4

## Non-Goals

- No `--accept-drift` script flag. Drift triage is conversational.
- No `classify_edit.py`. Edit classification is honor-system.
- No `--audit-report` reuse mechanism. Re-run the scoped audit each time.
- `last_audited` is never bumped by `edit_section.py`.

## Cross-References

- `stories-audit` — scoped audit required before any edit
- `stories-impact-check` — run before behavioral changes to user-facing
  surfaces
- `shared/spec.md` — schema reference for frontmatter fields, change
  resistance levels, and audit finding kinds
