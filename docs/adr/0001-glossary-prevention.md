# ADR 0001: Glossary-As-Prevention Skill

**Status:** Accepted
**Date:** 2026-05-22

## Context

When an agent introduces a new identifier (`Account`) for a concept the host
repo already names (`Org`), drift is expensive to detect later: both names
accumulate behavior across many files, tests pin both shapes in place, and
unifying them becomes a large refactor. Catching the candidate name at the
moment an agent is about to introduce it is much cheaper and more decisive
than detecting the drift after the fact.

This ADR scopes the design of a **prevention** skill that consults a small,
maintained glossary at the moment an agent is about to introduce a new
top-level concept identifier. It is complementary to — and explicitly
narrower than — the planned `concepts-cohere` detection track, which uses
the `object-catalog` lens to surface same-name drift in already-written
code (see `2026-05-20-storystore-plan-4-concepts.md`). Prevention catches
naming drift at write time; detection catches conceptual drift at audit
time. Both are needed.

This document commits the design. Implementation is a separate follow-up
issue (`storystore-glossary-impl`) once this ADR lands. Seeding a real
glossary in storystore itself, and any UI on top of the JSON output, are
out of scope here.

## Decision

### 1. Glossary file format and canonical location

**Decision:** A standalone YAML file at the canonical path
`docs/glossary.yml` in the host repo.

**Schema (per entry):**

```yaml
- name: Org                       # required, kebab/PascalCase identifier
  definition: >-                  # required, one sentence
    A tenant boundary; every user, document, and billing record belongs to
    exactly one Org.
  symbol: src/models/org.py:Org   # optional, representative type/symbol
  aliases: [Organization, Tenant] # optional, previously used names to
                                  # actively discourage
  rationale: >-                   # required ONLY when an entry is added in
                                  # response to a flagged candidate
    Distinct from Account: Account is a billing-only concept; Org is the
    permissions boundary.
```

Top-level shape is a list of entries under a single `entries:` key so the
file can grow a metadata header later without a breaking change.

**Rationale:** YAML is machine-readable for the matcher, human-editable
without tooling, diff-friendly in code review, and avoids the parser
ambiguity that comes from extracting structured data out of Markdown
tables. Storystore already ships a strict-subset YAML parser
(`storystore_lib.py`), which the skill will reuse.

**Alternatives considered:**

- *Markdown table with YAML frontmatter.* Friendlier to render on a static
  site, but invites freeform edits inside cells and is harder to keep
  machine-parseable as the list grows. Rejected.
- *Per-entry files under `docs/glossary/*.yml`.* Easier to land via small
  PRs, but adds I/O cost on every check and makes a missing-entry scan
  noisier. Rejected; revisit only if a single file grows past ~500
  entries.

### 2. Similarity method for candidate-name matching

**Decision:** Default to a deterministic string-distance heuristic with a
two-stage threshold. No embedding model in v1.

**Method:**

1. Normalize candidate and each glossary entry (`name` + `aliases`):
   lowercase, split CamelCase / snake_case / kebab-case into tokens,
   singularize trailing `s`, drop common suffixes (`Service`, `Manager`,
   `Model`, `DTO`, `Impl`).
2. **Exact-normalized match:** if the normalized candidate equals a
   normalized glossary token set, flag as `exact-alias`.
3. **Near match:** compute Levenshtein distance on the normalized
   single-token join; flag as `near-match` when distance ≤ 2 **and**
   max(len) ≥ 4, OR when Jaccard similarity over tokens ≥ 0.67.

**Required dependencies/runtime:** Python stdlib only. No model download,
no network, no cold-start cost. Total wall time target ≤ 50 ms per
candidate against a 500-entry glossary.

**Failure modes:**

- *False negatives on semantic synonyms* (e.g., `Org` vs. `Workspace`).
  Accepted; the `aliases` field is the explicit escape hatch — humans add
  the synonym once and the matcher catches it forever.
- *False positives on short tokens.* Mitigated by the `max(len) ≥ 4`
  guard; below that, only exact-alias fires.

**Alternatives considered:**

- *Local embedding model (e.g., MiniLM).* Catches semantic synonyms with
  no human curation, but adds a multi-hundred-MB runtime dependency, a
  cold-start, nondeterministic outputs across model versions, and a
  failure mode where the host repo can't run the skill at all in an
  offline environment. Rejected for v1; reconsider when storystore as a
  whole takes on a model dependency.
- *Soundex / Metaphone.* Catches homophones but misses the common drift
  cases (`Org` ↔ `Organization`, `User` ↔ `Account`). Rejected as the
  primary; can be added as a third stage cheaply if needed.

### 3. Invocation mechanism

**Decision:** Both — with the **explicit skill** (`glossary-check`) as the
primary, supported by an **opt-in hook** the host repo can wire up
locally.

- *Primary:* the agent invokes `glossary-check --candidate <Name>` (or a
  batch form) before introducing a new top-level identifier. The skill's
  `SKILL.md` description makes it a hard trigger for "introducing a new
  top-level type, struct, dataclass, or module-level concept noun."
- *Secondary:* a `PreToolUse` hook recipe is shipped in the skill's
  `references/` directory. The hook parses the candidate identifiers out
  of a pending `Edit`/`Write` diff and calls the same script. Host repos
  opt in by adding the recipe to their `.claude/settings.json`.

**Rationale:** Description-driven triggers are what storystore already
uses (`stories-impact-check` is the precedent); they're portable across
agent runtimes and don't require host-repo settings changes. The hook is
the belt-and-braces version for repos that want enforcement rather than
agent compliance. Shipping the recipe but not enabling it by default
keeps storystore's "no PreToolUse hook" v1 non-goal honest while still
giving the user a one-line opt-in.

**Alternatives considered:**

- *Hook only.* Hard to configure per-repo, brittle across agent runtimes
  that differ in hook semantics, invisible to the agent's planning step.
  Rejected as primary.
- *Skill only.* Relies entirely on agent compliance; a forgetful agent
  silently introduces drift. Rejected as sole mechanism.

### 4. UX on a flagged match

**Decision:** Return **structured JSON on stdout** with a non-zero exit
code for `exact-alias`, and zero exit with the same JSON shape for
`near-match`. The skill never blocks the agent process itself; the
consuming layer (agent or hook) decides what to do with the verdict.

**JSON shape:**

```json
{
  "candidate": "Account",
  "verdict": "exact-alias | near-match | clear",
  "matches": [
    {
      "glossary_name": "Org",
      "distance": 0,
      "via": "alias",
      "definition": "...",
      "symbol": "src/models/org.py:Org"
    }
  ],
  "resolution_paths": ["rename", "add-entry"]
}
```

**Exit codes:**

```
0  clear OR near-match (advisory)
3  exact-alias (policy refusal — caller must resolve)
2  invalid input (missing glossary, malformed candidate)
```

**Rationale:** Matches storystore's existing exit-code idiom
(`spec.md` § Exit Codes: `3` is policy refusal). Structured output lets
agents make a decision without parsing prose. Reserving the hard block
for `exact-alias` only — where the candidate is provably the same token
as an existing alias — keeps the false-positive cost low; `near-match`
is advisory so the agent can use judgment.

**Alternatives considered:**

- *Always block.* Too disruptive on near-match false positives; the
  short-token problem alone would make this unusable. Rejected.
- *Always warn.* Loses the ability to actually prevent a known-bad
  rename (e.g., re-introducing `Organization` when `Org` is the
  canonical name). Rejected.
- *Prose output.* Forces the agent to re-parse and loses the resolution
  affordance. Rejected.

### 5. Definition of concept-laden areas

**Decision:** Apply the check when a candidate identifier appears in any
of these positions, language-agnostically:

- A new top-level type declaration. Language-specific matchers:
  - Python: `class Foo`, `@dataclass class Foo`, `Foo = TypedDict(...)`,
    `Foo: TypeAlias = ...`
  - TypeScript/JavaScript: `class Foo`, `interface Foo`, `type Foo =`,
    `enum Foo`
  - Go: `type Foo struct`, `type Foo interface`, top-level `type Foo`
  - Rust: `struct Foo`, `enum Foo`, `trait Foo`
  - Java/Kotlin: `class Foo`, `interface Foo`, `record Foo`,
    `sealed class Foo`
- A new top-level module/package name whose basename is a domain noun
  (heuristic: PascalCase or snake_case basename that is not in a
  stop-list of infrastructure words like `utils`, `helpers`, `internal`,
  `test`).
- A new top-level constant or variable whose name is a PascalCase domain
  noun (catches `const Account = ...` factory patterns).

**Excluded:** local variables, function parameters, private (`_`-prefixed)
identifiers, test fixtures, generated code (paths under storystore's
`DEFAULT_SKIP_DIRS`).

**Language-agnostic fallback:** when no language-specific matcher fits, a
regex over PascalCase top-level identifiers at column 0 of added lines in
a diff is the floor.

**Rationale:** Top-level type declarations are where naming drift becomes
expensive — they propagate into imports, tests, and API shapes. Locals
and parameters are cheap to rename later. The stop-list keeps the
matcher quiet on the genuinely-infrastructure names that every repo has.

**Alternatives considered:**

- *All identifiers.* Drowns the agent in flags on every refactor.
  Rejected.
- *Only when an explicit `// concept:` marker is present.* Requires
  retrofit across the host repo. Rejected.

### 6. Two resolution paths

**Decision:** When the candidate is flagged, the agent has exactly two
valid responses:

1. **Rename to the canonical glossary entry.** The agent uses the
   `glossary_name` from the JSON output and proceeds with that
   identifier. No glossary edit required.
2. **Add a new glossary entry.** The agent edits `docs/glossary.yml` to
   add an entry for the new concept, including a required `rationale`
   field that is one sentence answering "why is this distinct from the
   matched entry?" Adding an entry without a `rationale` when one was
   added under flag conditions is itself an error (caught by a
   schema-validation pass in the implementation).

"Justifying" concretely means: the `rationale` field is a single sentence
distinguishing the new concept from the matched concept on a dimension
other than spelling (e.g., "Account is the billing-only mirror of Org;
Org is the permissions boundary."). Tautological rationales ("Account is
different from Org because it represents an account") are caught by a
length+token-overlap heuristic in the implementation.

A third option — "ignore the flag" — is not an offered path. If the
agent believes the flag is wrong, the resolution is still to add a
glossary entry (with the rationale being why the matcher mis-fired).
That creates a durable record and turns a one-off override into either a
new alias on the existing entry or a new concept entry.

**Rationale:** Two paths give the agent the affordance to do the right
thing in both directions — pull toward an existing concept, or split a
genuinely new one — without an escape hatch that silently re-opens the
drift problem.

**Alternatives considered:**

- *Allow `--force` to bypass.* Quickly becomes the default in practice.
  Rejected.
- *Require a human reviewer for "add entry."* Higher friction than the
  problem warrants for v1; the rationale field plus normal code review
  is sufficient. Reconsider if rationale quality is empirically poor.

## Consequences

**Positive:**

- Naming drift is caught at write time, before it propagates into tests
  and imports.
- The glossary becomes a small, human-curated artifact that doubles as
  onboarding documentation.
- The same JSON output is consumable by an explicit skill call or a
  PreToolUse hook, so host repos can dial enforcement to taste.
- No new runtime dependencies; the skill runs offline.

**Negative / risks:**

- Semantic-synonym drift (`Org` vs. `Workspace`) is not caught until a
  human adds the alias.
- The glossary itself can fall out of date if entries are added
  carelessly. Mitigated by the rationale requirement; not eliminated.
- `near-match` false positives on short, common tokens are a known
  failure mode; the `max(len) ≥ 4` guard and structured output let the
  agent dismiss them, but the noise floor is not zero.
- Storystore takes on responsibility for a second maintained artifact
  per host repo (`docs/glossary.yml` joins `docs/stories/`). Acceptable
  given the prevention payoff.

**Out of scope (handled elsewhere):**

- Detection of drift in already-written code: lives in
  `concepts-cohere`'s `object-catalog` lens (`catalog-name-collision`,
  `catalog-near-duplicate`). See plan 4.
- Seeding a real glossary in storystore itself.
- CI integration; per storystore v1 non-goals.

## Follow-up work

The following follow-up issues are generated by this ADR. IDs are
proposed; the implementation track will file them under Beads.

- **`storystore-glossary-impl`** — Implement the `glossary-check` skill:
  YAML loader (reusing `storystore_lib.py`'s parser), matcher
  (normalization + Levenshtein + Jaccard), per-language candidate
  extractors, JSON output, exit-code policy. Acceptance: synthetic
  glossary + candidate fixtures produce the documented JSON shape and
  exit codes for each of `exact-alias`, `near-match`, `clear`.
- **`storystore-glossary-hook-recipe`** — Ship a `PreToolUse` hook
  recipe under the skill's `references/` directory that parses
  candidate identifiers from a pending `Edit`/`Write` diff and calls
  `glossary-check`. Includes a one-paragraph install snippet for
  `.claude/settings.json`. Depends on `storystore-glossary-impl`.
- **`storystore-glossary-seed-storystore`** — Seed `docs/glossary.yml`
  in storystore itself with the ~10 concepts already named in
  `spec.md` (`story`, `intent`, `authority`, `change-resistance`,
  `evidence`, `claim`, `surface`, `audit`, `coverage`, `concept`).
  Depends on `storystore-glossary-impl`.
- **`storystore-glossary-tests`** — Fixture tests against a small
  synthetic host repo with seeded drift candidates. Depends on
  `storystore-glossary-impl` and the fixture harness from
  `storystore-fixture-harness`.

**Complementary track (not blocked by this ADR):**

- `concepts-cohere` `object-catalog` lens, per plan 4. Detection of
  same-name drift in already-written code via catalog set comparison
  across randomized slicings. Different lifecycle stage, same broad
  problem. The two tracks should share neither code nor schema; the
  glossary is a maintained artifact and the catalog is ephemeral.
