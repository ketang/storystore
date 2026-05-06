# storystore Plan 3: Guarded Edits And Impact Checks

**Date:** 2026-05-01 (revised)
**Status:** Target implementation plan.

## Goal

Ship the two agent-facing runtime skills:

- `stories-update`
- `stories-impact-check`

After this phase, agents can safely maintain existing stories with structural
backstops on locked content, and can find which active stories a planned
behavioral change affects before editing code.

## Files

New skill files:

```text
catalog/skills/stories-update/SKILL.md
catalog/skills/stories-update/packaging.json
catalog/skills/stories-update/scripts/lock_check.py
catalog/skills/stories-update/scripts/edit_section.py
catalog/skills/stories-update/scripts/drift_todo.py

catalog/skills/stories-impact-check/SKILL.md
catalog/skills/stories-impact-check/packaging.json
catalog/skills/stories-impact-check/scripts/impact_check.py
```

New tests:

```text
tests/test_storystore_lock_check.py
tests/test_storystore_edit_section.py
tests/test_storystore_drift_todo.py
tests/test_storystore_impact_check.py
```

Modified files:

```text
scripts/build-plugins
catalog/plugin-versions.json
```

Both skills declare shared scripts:

```json
{
  "shared_scripts": [
    "storystore/storystore_lib.py",
    "storystore/inventory.py",
    "storystore/spec.md"
  ]
}
```

## stories-update

Question:

> Can an existing intent story be safely edited without hiding software drift
> or changing protected meaning?

`stories-update` is orchestration-heavy. The SKILL.md owns the conversational
flow. Scripts enforce structural policy.

### Required Flow

1. Identify targeted story slugs.
2. Run scoped audit against targeted stories:
   `stories-audit --story <slug> ...` (no full inventory build, no coverage
   findings). There is no `--audit-report` reuse mechanism.
3. If targeted stories have findings, present them and ask the user to
   classify drift one finding at a time:
   - code-side bug;
   - deliberate story-side change.
4. For code-side bugs, append to `docs/stories/drift-todo.md` via
   `drift_todo.py` and do not edit the story.
5. For story-side changes, proceed through edit classification and lock
   checks.
6. Apply edits only when gates are satisfied.

No `--accept-drift` script flag.

### Edit Classification (honor system)

The skill states the edit class before applying an edit:

```text
evidence-refresh
review-metadata
drift-note
prose-clarification
claim-change
boundary-change
intent-change
authority-change
resistance-change
```

Edit class is honor-system. There is no `classify_edit.py` and `lock_check.py`
does not compare diffs. Backstops are structural, not classifier-based.

### Structural Backstops (lock_check.py + edit_section.py)

- locked-section enforcement: editing a section listed in `locked_sections`
  requires `--confirm-locked`;
- inline locked-block byte-equality preservation: any change to a fenced
  inline locked block requires `--confirm-locked` to override;
- frontmatter changes detected mechanically;
- `Auditable Claims` bullet-count guard: if old vs new bullet count differs,
  `--confirm-meaning-change` is required.

### Change Resistance Gates

```text
low:
  evidence, metadata, drift notes, prose clarification allowed
  meaning changes require explicit approval

medium:
  same as low, but agent must present edit classification before applying

high:
  evidence, metadata, drift notes allowed
  claim, boundary, intent, authority, resistance changes require explicit
    approval
  locked sections require explicit approval by section name

immutable:
  evidence refresh, drift-note append, last_audited bump (via stories-audit
    --bump-clean) allowed
  no agent change to change_resistance, authority, status
  no agent edit to any locked section
  no agent edit to inline locked blocks
  no agent-authored meaning changes anywhere
  human must edit the frontmatter directly to lower change_resistance
```

`immutable` is unconditionally agent-immutable for protected meaning. No
flag combination overrides it.

### lock_check.py

Command:

```bash
stories-update/scripts/lock_check.py \
  --repo-root <repo-root> \
  --slug <slug> \
  --section <section>
```

Stdout JSON (state report; no diff comparison):

```json
{
  "story_slug": "login",
  "status": "active",
  "authority": "accepted",
  "change_resistance": "high",
  "section": "Intent",
  "section_locked": true,
  "inline_locked_blocks": 0,
  "locked_sections": ["Intent"],
  "auditable_claims_count": 3
}
```

### edit_section.py

Command:

```bash
stories-update/scripts/edit_section.py \
  --repo-root <repo-root> \
  --slug <slug> \
  --section <section> \
  [--confirm-locked] \
  [--confirm-meaning-change] \
  [--confirm-resistance-change]
```

Stdin is the new section body.

Rules:

- refuse locked-section edit without `--confirm-locked` (exit 3);
- refuse inline locked-block change without `--confirm-locked` (exit 3);
- refuse `Auditable Claims` bullet-count change without
  `--confirm-meaning-change` (exit 3);
- refuse any agent-authored change to immutable stories beyond the explicit
  allowlist (exit 3);
- refuse frontmatter change to `change_resistance`, `authority`, or `status`
  for `immutable` stories (exit 3);
- refuse frontmatter change to `change_resistance` without
  `--confirm-resistance-change` for non-immutable stories (exit 3);
- exit 2 for invalid input or malformed story.

The script does not bump `last_audited` after arbitrary edits. `last_audited`
means latest clean audit. It changes only via `stories-audit --bump-clean`.

When metadata-affecting frontmatter changes (title, status, authority,
`change_resistance`) succeed, the script regenerates `INDEX.md`.

### drift_todo.py

Command:

```bash
stories-update/scripts/drift_todo.py \
  [--path docs/stories/drift-todo.md] \
  --story-slug <slug> \
  --kind <kind> \
  --title <title> \
  --body <body>
```

`--path` defaults to `docs/stories/drift-todo.md` (the location ensured
gitignored by `stories-init`). Append-only, date-stamped sections. The drift
todo is a staging buffer for tracker intake. The skill may suggest
`beads-issue-flow` or `github-issue-flow` after writing it.

## stories-impact-check

Question:

> Which active stories does this planned behavioral change affect?

The skill is read-only and is a hard trigger before any behavioral change to
user-facing surfaces.

Frontmatter description must be direct and load-bearing (description-driven
hard trigger; hook-based enforcement is deferred):

```yaml
description: |
  Hard trigger — before any behavioral change to user-facing surfaces, run
  this skill. Finds stories affected by planned file, surface, or behavior
  changes and reports their status, authority, change resistance, locked
  sections, and intent excerpt so the agent can alert the user before
  proceeding. Read-only.
```

Cross-reference from `stories-update` SKILL.md: any code change implementing
a story should be preceded by `stories-impact-check`.

Explicit Non-Goals: PreToolUse hook enforcement, git-introspection signal,
`--record` mode.

### impact_check.py

Inputs:

```text
--file <path>        repeatable; OR-combined within and across dimensions
--surface <ref>      repeatable; OR-combined
--description <text> at most one value; repeating it is exit 2
```

Cross-dimension is OR.

Command:

```bash
stories-impact-check/scripts/impact_check.py \
  --repo-root <repo-root> \
  --file src/server.ts \
  --surface "POST /login" \
  --description "changing login behavior"
```

Stdout JSON:

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

Stderr threshold warning when `duration_ms` > 500. Override via env
`STORYSTORE_PERF_WARN_MS` or flag `--perf-warn-ms` (0 disables).

Matching:

- file matches story test evidence;
- file matches source file defining a claimed surface;
- surface ref matches story evidence;
- description tokens match `Intent`, `Story`, `Expected Behavior`, or
  `Auditable Claims`.

Description matching is simple token matching. Semantic embeddings are out of
scope.

### Agent Behavior Table

```text
active + immutable:    stop and ask before proceeding
active + high:         alert user, ask confirmation
active + medium:       mention affected stories; proceed unless user objects
active + low:          mention affected stories after the change is applied
authority: observed:   mention only; do not gate
status: draft:         mention only unless change_resistance is high/immutable
status: deprecated:    report as stale match; do not gate
```

The skill does not decide whether the change is allowed. It surfaces
relevant stories and their resistance level to the operating agent and user.

## Exit Codes

```text
0  success
1  findings in --strict mode (where applicable)
2  invalid input, malformed story, repeated --description, missing setup
3  policy refusal (locked-without-confirmation, immutable rule violation,
   meaning-change without --confirm-meaning-change, resistance change
   without --confirm-resistance-change, validity-matrix violation)
4+ unexpected runtime error
```

Stderr always carries a one-line human-readable explanation.

## Tests

Add tests for:

- `lock_check.py` reports state (status, authority, change resistance,
  locked sections, inline locked blocks, auditable claims count);
- `lock_check.py` does not perform diff comparison;
- `edit_section.py` refuses locked-section edits without `--confirm-locked`;
- `edit_section.py` refuses inline locked-block edits without
  `--confirm-locked`;
- `edit_section.py` refuses Auditable Claims bullet-count change without
  `--confirm-meaning-change`;
- `edit_section.py` refuses immutable agent edits across the full allowlist
  (status, authority, change_resistance, locked sections, locked blocks,
  meaning anywhere);
- `edit_section.py` allows evidence refresh, drift-note append on immutable;
- `edit_section.py` does not bump `last_audited`;
- `edit_section.py` regenerates `INDEX.md` on metadata-affecting frontmatter
  changes;
- `drift_todo.py` defaults `--path` to `docs/stories/drift-todo.md`;
- `drift_todo.py` creates and appends date-stamped Markdown;
- `impact_check.py` matches by test evidence file;
- `impact_check.py` matches by source file defining a claimed surface;
- `impact_check.py` matches by direct surface reference;
- `impact_check.py` matches by description token fallback;
- `impact_check.py` rejects repeated `--description` (exit 2);
- `impact_check.py` OR-combines `--file` and `--surface` within and across
  dimensions;
- `impact_check.py` includes status, authority, change resistance, locked
  sections, intent excerpt, and match reasons;
- `impact_check.py` returns deprecated stories as stale matches without
  gate instructions;
- `impact_check.py` does not gate on `authority: observed`;
- `impact_check.py` emits `performance` block; stderr warning fires above
  threshold; env and flag override threshold.

## Verification

Run:

```bash
python3 -m unittest tests.test_storystore_lock_check -v
python3 -m unittest tests.test_storystore_edit_section -v
python3 -m unittest tests.test_storystore_drift_todo -v
python3 -m unittest tests.test_storystore_impact_check -v
python3 -m unittest discover -s tests -t .
scripts/build-plugins
```

Smoke test impact check:

```bash
TMPDIR=$(mktemp -d)
cp -r tests/fixtures/storystore_audit_repo/. "$TMPDIR/"
mkdir -p "$TMPDIR/docs/stories"
# write login story fixture, then:
catalog/skills/stories-impact-check/scripts/impact_check.py \
  --repo-root "$TMPDIR" \
  --surface "POST /login" \
  --description "changing login behavior"
rm -rf "$TMPDIR"
```

Smoke test guarded edit:

```bash
TMPDIR=$(mktemp -d)
mkdir -p "$TMPDIR/docs/stories"
# write high-resistance story with locked Intent, then:
echo "Different intent." | catalog/skills/stories-update/scripts/edit_section.py \
  --repo-root "$TMPDIR" --slug login --section Intent
# expected exit 3 (policy refusal: locked-without-confirmation)
rm -rf "$TMPDIR"
```

## Done Criteria

- `stories-update` and `stories-impact-check` are registered and built.
- Both skills receive shared scripts (including `spec.md` reference) through
  build packaging.
- Update flow always invokes scoped `stories-audit --story <slug>` before
  edits.
- Lock and change-resistance gates are enforced by structural backstops in
  `edit_section.py`; honor-system edit classification is preserved in
  SKILL.md.
- `immutable` is unconditionally agent-immutable beyond the documented
  allowlist.
- `drift_todo.py` defaults to `docs/stories/drift-todo.md`.
- Impact check exposes the locked behavior table and emits a `performance`
  block.
- Description input is single-value; file/surface inputs are repeatable
  OR-combined.
- Tests and build pass.
