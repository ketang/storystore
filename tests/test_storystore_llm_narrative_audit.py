"""Opt-in LLM-backed tests for narrative audit findings (D-pass) using local Ollama.

These tests ask an LLM to evaluate story prose for ambiguity, contradictions,
and unsupported claims against fixture repos. They validate that the LLM can
identify narrative quality issues that deterministic passes cannot catch.

All tests skip cleanly when Ollama is unavailable.

Structural assertions check finding kinds and story slugs — not exact wording,
since LLM output is non-deterministic.
"""

from __future__ import annotations

import json
import textwrap
from pathlib import Path
from typing import Any

import pytest

from .llm_provider import LLMResponse, OllamaProvider, require_ollama

REPO_ROOT = Path(__file__).resolve().parents[1]

# ---------------------------------------------------------------------------
# Prompt metadata — pinned for reproducibility
# ---------------------------------------------------------------------------

PROMPT_VERSION = "narrative-audit-v1"

SYSTEM_PROMPT = """\
You are a technical auditor reviewing intent stories for a software project.
Each story describes a user-facing capability, its expected behavior, and
auditable claims backed by evidence references.

You will be given a story's full text and its evidence context. Your job is to
identify narrative quality issues. Report each issue as a JSON object in a
JSON array.

Issue kinds you may report:
- "claim-contradicted": An auditable claim is contradicted by the evidence or
  expected behavior described elsewhere in the story.
- "story-ambiguous": The story body (Intent, Expected Behavior, or Boundaries)
  contains language that is vague, contradictory, or impossible to verify.
- "documented-untested": The story documents specific behavior but the evidence
  section shows no test coverage for it.

For each issue, produce:
{
  "kind": "<one of the three kinds above>",
  "story_slug": "<slug from frontmatter>",
  "detail": "<1-2 sentence explanation>"
}

Rules:
- Output ONLY a JSON array of issue objects. No markdown fences, no explanation.
- If there are no issues, output an empty array: []
- Be conservative: only report clear, unambiguous problems.
- Do not invent issues that are not supported by the text.
"""

NARRATIVE_AUDIT_PROMPT_TEMPLATE = """\
Review this intent story for narrative quality issues.

Story text:
---
{story_text}
---

Evidence context:
- Tests declared: {tests_declared}
- Surfaces declared: {surfaces_declared}
- Tests that resolve to existing files: {tests_resolved}

Report any claim-contradicted, story-ambiguous, or documented-untested issues.
"""

# ---------------------------------------------------------------------------
# Fixture story content — deliberately problematic prose
# ---------------------------------------------------------------------------

AMBIGUOUS_STORY = textwrap.dedent("""\
    ---
    schema_version: 1
    title: User exports data somehow
    slug: user-exports-data-somehow
    status: active
    authority: observed
    change_resistance: low
    tests_applicable: true
    ---

    # User exports data somehow

    ## Intent
    A user might want to export some data, possibly in various formats,
    or maybe just view it on screen depending on circumstances.

    ## Expected Behavior
    The export feature should probably work most of the time. Results may
    vary depending on the input. The output format is flexible and could
    be anything reasonable.

    ## Boundaries
    Some things are out of scope but it is hard to say exactly which ones.

    ## Auditable Claims
    - The export generally produces output.
    - Data is handled appropriately.

    ## Evidence
    ### Surface
    - `cli: export`
""")

CONTRADICTED_STORY = textwrap.dedent("""\
    ---
    schema_version: 1
    title: Widget creation returns identifier
    slug: widget-creation-returns-identifier
    status: active
    authority: accepted
    change_resistance: high
    tests_applicable: true
    ---

    # Widget creation returns identifier

    ## Intent
    An API client creates a widget and receives a unique identifier.

    ## Expected Behavior
    A valid POST to `/widgets` with a `name` field returns HTTP 201 and
    a JSON body containing the new widget's numeric `id`.
    A request missing `name` returns HTTP 400.

    ## Boundaries
    Authentication is not handled by this endpoint.

    ## Auditable Claims
    - Valid widget creation returns HTTP 200 with a string UUID.
    - Missing `name` returns HTTP 404.

    ## Evidence
    ### Tests
    - `tests/test_widgets.py`
    ### Surface
    - `route: POST /widgets`
""")

UNTESTED_STORY = textwrap.dedent("""\
    ---
    schema_version: 1
    title: Batch import processes CSV files
    slug: batch-import-processes-csv-files
    status: active
    authority: accepted
    change_resistance: medium
    tests_applicable: true
    ---

    # Batch import processes CSV files

    ## Intent
    An administrator uploads a CSV file to bulk-create records.

    ## Expected Behavior
    The import command reads a CSV file, validates each row, creates records
    for valid rows, and reports errors for invalid rows. Duplicate detection
    prevents re-importing the same record twice. The progress is reported
    to stdout line-by-line.

    ## Boundaries
    Only CSV format is supported; Excel files are rejected.

    ## Auditable Claims
    - Duplicate rows are skipped without error.
    - Invalid rows produce a per-row error message on stderr.
    - Progress output shows processed/total count.

    ## Evidence
    ### Surface
    - `cli: import`
""")

CLEAN_STORY = textwrap.dedent("""\
    ---
    schema_version: 1
    title: Health endpoint returns status
    slug: health-endpoint-returns-status
    status: active
    authority: observed
    change_resistance: low
    tests_applicable: true
    ---

    # Health endpoint returns status

    ## Intent
    An operator checks whether the service is running.

    ## Expected Behavior
    GET /health returns HTTP 200 with a JSON body `{"status": "ok"}`.

    ## Boundaries
    Does not check downstream dependencies.

    ## Auditable Claims
    - GET /health returns HTTP 200.
    - Response body contains `{"status": "ok"}`.

    ## Evidence
    ### Tests
    - `tests/test_health.py`
    ### Surface
    - `route: GET /health`
""")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


# Models too small for narrative reasoning (< ~2B params).
_TOO_SMALL_SUFFIXES = (":1b", ":0.5b", ":mini")

# Preferred models for narrative audit — larger models first.
_NARRATIVE_PREFERRED = (
    "gemma4:e4b",
    "llama3.1",
    "llama3.2",
    "gemma3:4b",
    "gemma3",
    "gemma2",
    "mistral",
    "phi3",
)


def _require_narrative_provider() -> OllamaProvider:
    """Return an OllamaProvider with a model capable of narrative reasoning.

    Prefers larger models for this task. Skips if only very small models
    are available.
    """
    provider = require_ollama()
    models = provider.list_models()

    # Try to find a preferred model
    for preferred in _NARRATIVE_PREFERRED:
        for available in models:
            if available == preferred or available.startswith(preferred + ":"):
                return OllamaProvider(model=available)

    # Filter out known-too-small models
    viable = [
        m for m in models
        if not any(m.endswith(s) for s in _TOO_SMALL_SUFFIXES)
    ]
    if viable:
        return OllamaProvider(model=viable[0])

    pytest.skip(
        f"No model large enough for narrative reasoning; "
        f"available: {models}"
    )


@pytest.fixture(scope="module")
def ollama_provider() -> OllamaProvider:
    """Module-scoped fixture: skip if Ollama unavailable or model too small."""
    return _require_narrative_provider()


@pytest.fixture
def ambiguous_repo(tmp_path: Path) -> Path:
    """Fixture repo containing a deliberately ambiguous story."""
    return _create_fixture_repo(tmp_path, AMBIGUOUS_STORY)


@pytest.fixture
def contradicted_repo(tmp_path: Path) -> Path:
    """Fixture repo containing a story with contradicted claims."""
    repo = _create_fixture_repo(tmp_path, CONTRADICTED_STORY)
    # Create a test file that exists so evidence resolves
    tests_dir = repo / "tests"
    tests_dir.mkdir(exist_ok=True)
    (tests_dir / "test_widgets.py").write_text("# widget tests\n")
    return repo


@pytest.fixture
def untested_repo(tmp_path: Path) -> Path:
    """Fixture repo with documented behavior but no test evidence."""
    return _create_fixture_repo(tmp_path, UNTESTED_STORY)


@pytest.fixture
def clean_repo(tmp_path: Path) -> Path:
    """Fixture repo with a well-written, unambiguous story."""
    repo = _create_fixture_repo(tmp_path, CLEAN_STORY)
    tests_dir = repo / "tests"
    tests_dir.mkdir(exist_ok=True)
    (tests_dir / "test_health.py").write_text("# health tests\n")
    return repo


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _create_fixture_repo(tmp_path: Path, story_text: str) -> Path:
    """Set up a minimal repo with docs/stories/ containing the given story."""
    stories_dir = tmp_path / "docs" / "stories"
    stories_dir.mkdir(parents=True)
    (stories_dir / "README.md").write_text("# stories\n")
    (stories_dir / "INDEX.md").write_text("")
    # Extract slug from story text for filename
    slug = _extract_slug(story_text)
    (stories_dir / f"{slug}.md").write_text(story_text)
    return tmp_path


def _extract_slug(story_text: str) -> str:
    """Pull slug from frontmatter."""
    for line in story_text.splitlines():
        if line.startswith("slug:"):
            return line.split(":", 1)[1].strip()
    raise ValueError("no slug found in story text")


def _build_audit_prompt(
    story_text: str,
    *,
    tests_declared: list[str] | None = None,
    surfaces_declared: list[str] | None = None,
    tests_resolved: list[str] | None = None,
) -> str:
    return NARRATIVE_AUDIT_PROMPT_TEMPLATE.format(
        story_text=story_text,
        tests_declared=", ".join(tests_declared or []) or "(none)",
        surfaces_declared=", ".join(surfaces_declared or []) or "(none)",
        tests_resolved=", ".join(tests_resolved or []) or "(none)",
    )


def _extract_findings(text: str) -> list[dict[str, Any]]:
    """Extract a JSON array of findings from LLM response text.

    Handles markdown fences, leading/trailing text, and common LLM quirks.
    """
    cleaned = text.strip()

    # Strip markdown code fences
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        start = 1
        end = len(lines)
        for i in range(len(lines) - 1, 0, -1):
            if lines[i].strip() == "```":
                end = i
                break
        cleaned = "\n".join(lines[start:end]).strip()

    # Try direct parse
    try:
        result = json.loads(cleaned)
        if isinstance(result, list):
            return result
    except json.JSONDecodeError:
        pass

    # Try to find JSON array in text
    bracket_start = cleaned.find("[")
    if bracket_start == -1:
        return []
    depth = 0
    for i in range(bracket_start, len(cleaned)):
        if cleaned[i] == "[":
            depth += 1
        elif cleaned[i] == "]":
            depth -= 1
            if depth == 0:
                candidate = cleaned[bracket_start : i + 1]
                try:
                    result = json.loads(candidate)
                    if isinstance(result, list):
                        return result
                except json.JSONDecodeError:
                    pass
                break
    return []


def _run_narrative_audit(
    provider: OllamaProvider,
    story_text: str,
    *,
    tests_declared: list[str] | None = None,
    surfaces_declared: list[str] | None = None,
    tests_resolved: list[str] | None = None,
) -> tuple[list[dict[str, Any]], LLMResponse]:
    """Run a narrative audit via LLM and return (findings, raw_response)."""
    prompt = _build_audit_prompt(
        story_text,
        tests_declared=tests_declared,
        surfaces_declared=surfaces_declared,
        tests_resolved=tests_resolved,
    )
    response = provider.generate(prompt, system=SYSTEM_PROMPT)
    findings = _extract_findings(response.text)
    return findings, response


VALID_FINDING_KINDS = {"claim-contradicted", "story-ambiguous", "documented-untested"}


# ---------------------------------------------------------------------------
# Deterministic tests (no LLM needed)
# ---------------------------------------------------------------------------


class TestPromptMetadata:
    """Verify prompt structure and metadata — deterministic, no LLM needed."""

    def test_prompt_version_is_set(self):
        assert PROMPT_VERSION == "narrative-audit-v1"

    def test_system_prompt_mentions_all_finding_kinds(self):
        for kind in VALID_FINDING_KINDS:
            assert kind in SYSTEM_PROMPT, f"system prompt missing kind: {kind}"

    def test_system_prompt_requests_json_array(self):
        assert "JSON array" in SYSTEM_PROMPT

    def test_prompt_template_includes_evidence_context(self):
        for field in ("tests_declared", "surfaces_declared", "tests_resolved"):
            assert field in NARRATIVE_AUDIT_PROMPT_TEMPLATE


class TestFindingExtraction:
    """Tests for _extract_findings helper — deterministic, no LLM needed."""

    def test_plain_json_array(self):
        text = '[{"kind": "story-ambiguous", "story_slug": "test", "detail": "vague"}]'
        result = _extract_findings(text)
        assert len(result) == 1
        assert result[0]["kind"] == "story-ambiguous"

    def test_empty_array(self):
        result = _extract_findings("[]")
        assert result == []

    def test_json_with_markdown_fences(self):
        text = '```json\n[{"kind": "claim-contradicted", "story_slug": "x", "detail": "y"}]\n```'
        result = _extract_findings(text)
        assert len(result) == 1

    def test_json_with_surrounding_text(self):
        text = 'Here are the issues:\n[{"kind": "story-ambiguous", "story_slug": "s", "detail": "d"}]\nDone.'
        result = _extract_findings(text)
        assert len(result) == 1

    def test_no_json_returns_empty(self):
        result = _extract_findings("no json here")
        assert result == []

    def test_multiple_findings(self):
        text = json.dumps([
            {"kind": "story-ambiguous", "story_slug": "a", "detail": "x"},
            {"kind": "claim-contradicted", "story_slug": "b", "detail": "y"},
        ])
        result = _extract_findings(text)
        assert len(result) == 2


class TestFixtureStoryContent:
    """Verify fixture stories have expected structure — deterministic."""

    def test_ambiguous_story_has_slug(self):
        assert _extract_slug(AMBIGUOUS_STORY) == "user-exports-data-somehow"

    def test_contradicted_story_has_slug(self):
        assert _extract_slug(CONTRADICTED_STORY) == "widget-creation-returns-identifier"

    def test_untested_story_has_slug(self):
        assert _extract_slug(UNTESTED_STORY) == "batch-import-processes-csv-files"

    def test_clean_story_has_slug(self):
        assert _extract_slug(CLEAN_STORY) == "health-endpoint-returns-status"

    def test_ambiguous_story_contains_vague_language(self):
        # Verify the fixture actually has the vague language we expect LLM to find
        assert "probably" in AMBIGUOUS_STORY
        assert "flexible" in AMBIGUOUS_STORY
        assert "hard to say" in AMBIGUOUS_STORY

    def test_contradicted_story_has_mismatched_claims(self):
        # Expected behavior says 201, claim says 200; expected says 400, claim says 404
        assert "HTTP 201" in CONTRADICTED_STORY
        assert "HTTP 200" in CONTRADICTED_STORY
        assert "HTTP 400" in CONTRADICTED_STORY
        assert "HTTP 404" in CONTRADICTED_STORY

    def test_untested_story_has_no_test_evidence(self):
        assert "### Tests" not in UNTESTED_STORY


# ---------------------------------------------------------------------------
# LLM narrative audit tests (require Ollama)
# ---------------------------------------------------------------------------


class TestLLMNarrativeAuditAmbiguous:
    """LLM detects ambiguity in deliberately vague story prose."""

    def test_ambiguous_story_produces_findings(
        self, ollama_provider: OllamaProvider, ambiguous_repo: Path
    ):
        """LLM identifies narrative issues in a vague story."""
        findings, response = _run_narrative_audit(
            ollama_provider,
            AMBIGUOUS_STORY,
            surfaces_declared=["cli: export"],
        )
        assert response.model, "response should include model name"
        assert response.provider == "ollama"
        # LLM should find at least one issue with the vague story
        assert len(findings) > 0, (
            f"Expected findings for ambiguous story, got none. "
            f"Model: {response.model}, response: {response.text[:500]}"
        )

    def test_ambiguous_findings_have_valid_kinds(
        self, ollama_provider: OllamaProvider, ambiguous_repo: Path
    ):
        """All findings from ambiguous story use valid finding kinds."""
        findings, _ = _run_narrative_audit(
            ollama_provider,
            AMBIGUOUS_STORY,
            surfaces_declared=["cli: export"],
        )
        for finding in findings:
            assert "kind" in finding, f"finding missing 'kind': {finding}"
            assert finding["kind"] in VALID_FINDING_KINDS, (
                f"unexpected finding kind: {finding['kind']!r}"
            )

    def test_ambiguous_findings_reference_correct_slug(
        self, ollama_provider: OllamaProvider, ambiguous_repo: Path
    ):
        """Findings reference the correct story slug."""
        findings, _ = _run_narrative_audit(
            ollama_provider,
            AMBIGUOUS_STORY,
            surfaces_declared=["cli: export"],
        )
        for finding in findings:
            assert finding.get("story_slug") == "user-exports-data-somehow", (
                f"finding references wrong slug: {finding.get('story_slug')!r}"
            )

    def test_ambiguous_story_includes_story_ambiguous_kind(
        self, ollama_provider: OllamaProvider, ambiguous_repo: Path
    ):
        """LLM should identify story-ambiguous issues in vague prose."""
        findings, response = _run_narrative_audit(
            ollama_provider,
            AMBIGUOUS_STORY,
            surfaces_declared=["cli: export"],
        )
        kinds = {f.get("kind") for f in findings}
        assert "story-ambiguous" in kinds, (
            f"Expected 'story-ambiguous' finding for vague story. "
            f"Got kinds: {kinds}. Model: {response.model}"
        )


class TestLLMNarrativeAuditContradicted:
    """LLM detects contradictions between claims and expected behavior."""

    def test_contradicted_story_produces_findings(
        self, ollama_provider: OllamaProvider, contradicted_repo: Path
    ):
        """LLM identifies contradictions in mismatched story claims."""
        findings, response = _run_narrative_audit(
            ollama_provider,
            CONTRADICTED_STORY,
            tests_declared=["tests/test_widgets.py"],
            surfaces_declared=["route: POST /widgets"],
            tests_resolved=["tests/test_widgets.py"],
        )
        assert len(findings) > 0, (
            f"Expected findings for contradicted story, got none. "
            f"Model: {response.model}, response: {response.text[:500]}"
        )

    def test_contradicted_findings_include_claim_contradicted(
        self, ollama_provider: OllamaProvider, contradicted_repo: Path
    ):
        """LLM should find claim-contradicted issues for mismatched HTTP codes."""
        findings, response = _run_narrative_audit(
            ollama_provider,
            CONTRADICTED_STORY,
            tests_declared=["tests/test_widgets.py"],
            surfaces_declared=["route: POST /widgets"],
            tests_resolved=["tests/test_widgets.py"],
        )
        kinds = {f.get("kind") for f in findings}
        assert "claim-contradicted" in kinds, (
            f"Expected 'claim-contradicted' for mismatched HTTP codes. "
            f"Got kinds: {kinds}. Model: {response.model}"
        )

    def test_contradicted_findings_reference_correct_slug(
        self, ollama_provider: OllamaProvider, contradicted_repo: Path
    ):
        """Findings reference the correct story slug."""
        findings, _ = _run_narrative_audit(
            ollama_provider,
            CONTRADICTED_STORY,
            tests_declared=["tests/test_widgets.py"],
            surfaces_declared=["route: POST /widgets"],
            tests_resolved=["tests/test_widgets.py"],
        )
        for finding in findings:
            assert finding.get("story_slug") == "widget-creation-returns-identifier", (
                f"finding references wrong slug: {finding.get('story_slug')!r}"
            )


class TestLLMNarrativeAuditUntested:
    """LLM detects documented behavior with no test evidence."""

    def test_untested_story_produces_findings(
        self, ollama_provider: OllamaProvider, untested_repo: Path
    ):
        """LLM identifies missing test coverage for documented behavior."""
        findings, response = _run_narrative_audit(
            ollama_provider,
            UNTESTED_STORY,
            surfaces_declared=["cli: import"],
        )
        assert len(findings) > 0, (
            f"Expected findings for untested story, got none. "
            f"Model: {response.model}, response: {response.text[:500]}"
        )

    def test_untested_findings_include_documented_untested(
        self, ollama_provider: OllamaProvider, untested_repo: Path
    ):
        """LLM should find documented-untested issues when no tests declared."""
        findings, response = _run_narrative_audit(
            ollama_provider,
            UNTESTED_STORY,
            surfaces_declared=["cli: import"],
        )
        kinds = {f.get("kind") for f in findings}
        assert "documented-untested" in kinds, (
            f"Expected 'documented-untested' for story with no test evidence. "
            f"Got kinds: {kinds}. Model: {response.model}"
        )


class TestLLMNarrativeAuditClean:
    """LLM finds no issues in a well-written story."""

    def test_clean_story_produces_few_or_no_findings(
        self, ollama_provider: OllamaProvider, clean_repo: Path
    ):
        """Well-written story with full evidence should produce minimal findings."""
        findings, response = _run_narrative_audit(
            ollama_provider,
            CLEAN_STORY,
            tests_declared=["tests/test_health.py"],
            surfaces_declared=["route: GET /health"],
            tests_resolved=["tests/test_health.py"],
        )
        # A well-written story should produce at most 1 finding (LLMs may
        # be overzealous). The key assertion is that it produces fewer
        # findings than the deliberately broken stories.
        assert len(findings) <= 1, (
            f"Clean story produced too many findings ({len(findings)}). "
            f"Model: {response.model}, findings: {findings}"
        )

    def test_clean_story_findings_have_valid_structure(
        self, ollama_provider: OllamaProvider, clean_repo: Path
    ):
        """Any findings from clean story still have valid structure."""
        findings, _ = _run_narrative_audit(
            ollama_provider,
            CLEAN_STORY,
            tests_declared=["tests/test_health.py"],
            surfaces_declared=["route: GET /health"],
            tests_resolved=["tests/test_health.py"],
        )
        for finding in findings:
            assert "kind" in finding
            assert "story_slug" in finding
            assert finding["kind"] in VALID_FINDING_KINDS


class TestLLMNarrativeAuditResponseMetadata:
    """Verify response metadata for reproducibility."""

    def test_response_includes_model_name(
        self, ollama_provider: OllamaProvider
    ):
        """LLM response includes model name for reproducibility."""
        _, response = _run_narrative_audit(
            ollama_provider,
            CLEAN_STORY,
            tests_declared=["tests/test_health.py"],
            surfaces_declared=["route: GET /health"],
            tests_resolved=["tests/test_health.py"],
        )
        assert response.model, "model name must be non-empty"
        assert response.provider == "ollama"

    def test_response_includes_raw_data(
        self, ollama_provider: OllamaProvider
    ):
        """LLM response includes raw API response for debugging."""
        _, response = _run_narrative_audit(
            ollama_provider,
            CLEAN_STORY,
            tests_declared=["tests/test_health.py"],
            surfaces_declared=["route: GET /health"],
            tests_resolved=["tests/test_health.py"],
        )
        assert isinstance(response.raw, dict)
        # Ollama responses include timing metadata
        assert "total_duration" in response.raw or "response" in response.raw

    def test_prompt_version_logged(self, ollama_provider: OllamaProvider):
        """Prompt version is available for reproducibility tracking."""
        # This test verifies the metadata is accessible; in a real pipeline,
        # this would be logged alongside test results.
        assert PROMPT_VERSION.startswith("narrative-audit-")
        assert ollama_provider.name() == "ollama"
