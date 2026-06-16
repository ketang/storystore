# Stub-skill loud-failure contract

A **stub skill** is a skill whose `SKILL.md` ships in a release before its
implementation exists — for example a planned capability advertised in a
pre-1.0 plugin so downstream agents can discover that it is coming.

Shipping a stub is allowed. Shipping a stub that **exits clean** is not.

## The failure this prevents

A skill that documents itself as "deferred" and then prints status prose
gives the invoking agent a clean exit and no signal that the capability
does not exist. The agent treats the clean exit as success. In one real
incident this let three weeks of evidence drift accumulate in a consumer
repo before anyone noticed the skill had never actually run.

The complementary release gate `TestNoStubLanguageInSkills` (see
`tests/test_storystore_plugin_contracts.py`) already blocks the *shipped*
skills in `skills/` from carrying passive stub phrases like "deferred" or
"not implemented". This contract covers the other case: a skill that is
**intentionally** shipped in a stub state must fail loudly when invoked.

## The contract

Any skill shipped in a stub / not-yet-implemented state MUST:

1. Declare itself a stub in frontmatter with `stub: true`.
2. Make its **first** instruction run the loud-failure guard:

   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/stub-skill-guard.py" --skill <skill-name>
   ```

   The guard prints an actionable error naming the skill and the shipped
   plugin version (read from `plugin-version.json`) and exits non-zero
   (`78`, `EX_CONFIG`). A non-zero exit is the visible signal that the
   capability does not exist; the agent must surface it to the user and
   must not proceed as if the skill succeeded.
3. NOT instruct the agent to continue, fabricate output, or "note that
   this is deferred and move on" after the guard runs. The guard is
   terminal.

A clean exit (`0`) from a stub skill is a contract violation, even if the
SKILL.md text explains that the feature is unfinished. Prose does not stop
an agent; a non-zero exit does.

## Stub SKILL.md template

````markdown
---
name: stories-example
description: |
  Planned capability — NOT yet implemented. Invoking this skill fails
  loudly and names the shipped plugin version.
stub: true
---

# stories-example

This skill is a **stub**. The capability is planned but not implemented in
this release. Your first and only step is to run the loud-failure guard,
which exits non-zero and names the shipped plugin version:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/stub-skill-guard.py" --skill stories-example
```

Surface the guard's error to the user. Do not proceed as if the skill
ran.
````

## Why the guard rather than inline prose

The guard centralizes three things a hand-written stub keeps getting
wrong: it always exits non-zero, it always names the current shipped
version (so the report is accurate after every version bump), and it
phrases the error as an instruction to the agent rather than a status
note. See `scripts/stub-skill-guard.py`.

The contract is exercised by `tests/test_stub_skill_contract.py`, which
asserts the guard fails loudly with the version named, and that a stub
which exits clean is rejected by the same check.
