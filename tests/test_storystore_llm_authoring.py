"""Opt-in LLM-backed tests for observed story authoring using local Ollama.

These tests ask an LLM to author observed stories from deterministic candidates,
then validate the resulting stories through write_story.py and stories-audit.

All tests skip cleanly when Ollama is unavailable.

Structural assertions are stored here; exact LLM output is not asserted.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from .llm_provider import LLMProvider, OllamaProvider, get_provider, require_ollama

REPO_ROOT = Path(__file__).resolve().parents[1]
WRITE_STORY_SCRIPT = REPO_ROOT / "shared" / "write_story.py"
AUDIT_SCRIPT = REPO_ROOT / "shared" / "audit.py"
LIB_PATH = REPO_ROOT / "shared" / "storystore_lib.py"


def _load_lib():
    if "storystore_lib" in sys.modules:
        return sys.modules["storystore_lib"]
    spec = importlib.util.spec_from_file_location("storystore_lib", LIB_PATH)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules["storystore_lib"] = mod
    spec.loader.exec_module(mod)
    return mod


lib = _load_lib()

# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """\
You are a technical writer for a software project. You author intent stories
that describe user-facing capabilities. Each story follows a strict schema.

You will be given a candidate (a discovered user-facing surface) and must
produce a JSON object with these fields:

- title: A human-readable title (sentence case, 3-8 words)
- slug: kebab-case, 4-8 hyphen-separated words, lowercase ASCII only
- intent: 1-2 sentences explaining WHY this capability exists
- story: 1-3 sentences describing WHAT the user does
- expected_behavior: 1-3 sentences describing WHAT should happen
- boundaries: 1-2 sentences describing what is NOT covered
- auditable_claims: a list of 1-3 short, verifiable factual claims
- evidence: an object with keys "tests" (list of test file paths),
  "surface" (list of surface refs like "cli: command-name"), and
  "docs" (list of doc file paths)

Rules:
- The slug MUST be kebab-case with 4-8 words (e.g. "user-authenticates-via-login-command")
- The slug MUST contain only lowercase letters, digits, and single hyphens
- Output ONLY the JSON object, no markdown fences, no explanation
- All string values must be non-empty
"""

CANDIDATE_PROMPT_TEMPLATE = """\
Author an observed-mode intent story for this candidate:

Kind: {kind}
Name: {name}
Summary: {summary}
Evidence files: {evidence}

Produce the JSON object now.
"""

# ---------------------------------------------------------------------------
# Deterministic candidates for LLM authoring
# ---------------------------------------------------------------------------

DETERMINISTIC_CANDIDATES = [
    {
        "kind": "cli-command",
        "name": "deploy",
        "summary": "CLI command deploy",
        "evidence": ["src/commands/deploy.ts"],
    },
    {
        "kind": "http-route",
        "name": "GET /api/health",
        "summary": "HTTP route GET /api/health",
        "evidence": ["src/routes/health.ts"],
    },
    {
        "kind": "cli-command",
        "name": "config-set",
        "summary": "CLI command config-set",
        "evidence": ["src/commands/config.ts"],
    },
]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def ollama_provider() -> OllamaProvider:
    """Module-scoped fixture: skip entire module if Ollama unavailable."""
    return require_ollama()


@pytest.fixture
def story_repo(tmp_path: Path) -> Path:
    """Create a minimal repo structure for write_story.py."""
    stories = tmp_path / "docs" / "stories"
    stories.mkdir(parents=True)
    (stories / "README.md").write_text("# stories\n")
    (stories / "INDEX.md").write_text("")
    return tmp_path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_prompt(candidate: dict[str, Any]) -> str:
    return CANDIDATE_PROMPT_TEMPLATE.format(
        kind=candidate["kind"],
        name=candidate["name"],
        summary=candidate["summary"],
        evidence=", ".join(candidate["evidence"]),
    )


def _extract_json(text: str) -> dict[str, Any]:
    """Extract a JSON object from LLM response text.

    Handles common LLM quirks: markdown fences, leading text, trailing text.
    """
    # Strip markdown code fences
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        # Remove first line (```json or ```) and last line (```)
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
        if isinstance(result, dict):
            return result
    except json.JSONDecodeError:
        pass

    # Try to find JSON object in text
    brace_start = cleaned.find("{")
    if brace_start == -1:
        raise ValueError(f"No JSON object found in LLM response: {text[:200]!r}")
    depth = 0
    for i in range(brace_start, len(cleaned)):
        if cleaned[i] == "{":
            depth += 1
        elif cleaned[i] == "}":
            depth -= 1
            if depth == 0:
                candidate_json = cleaned[brace_start : i + 1]
                return json.loads(candidate_json)
    raise ValueError(f"Unbalanced braces in LLM response: {text[:200]!r}")


def _run_write_story(repo: Path, payload: dict[str, Any]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(WRITE_STORY_SCRIPT), "--repo-root", str(repo), "--observed"],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        check=False,
    )


def _run_audit(repo: Path, slug: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            sys.executable,
            str(AUDIT_SCRIPT),
            "--repo-root",
            str(repo),
            "--story",
            slug,
        ],
        capture_output=True,
        text=True,
        check=False,
    )


def _fixup_slug(payload: dict[str, Any], fallback: str) -> None:
    """Ensure payload has a slug with at least 2 words for write_story.py.

    LLMs sometimes generate single-word slugs despite instructions. This
    applies a deterministic fallback rather than failing the test on LLM
    formatting variance.
    """
    import re

    slug = payload.get("slug", "")
    parts = [p for p in slug.split("-") if p]
    if len(parts) < 2 or not re.match(r"^[a-z0-9]+(?:-[a-z0-9]+)*$", slug):
        payload["slug"] = fallback


def _author_story(provider: LLMProvider, candidate: dict[str, Any]) -> dict[str, Any]:
    """Ask the LLM to author a story from a candidate. Returns parsed JSON."""
    prompt = _build_prompt(candidate)
    response = provider.generate(prompt, system=SYSTEM_PROMPT)
    assert response.text, "LLM returned empty response"
    return _extract_json(response.text)


# ---------------------------------------------------------------------------
# Provider abstraction tests (no LLM needed)
# ---------------------------------------------------------------------------


class TestProviderAbstraction:
    """Tests for the provider abstraction itself — no Ollama required."""

    def test_get_provider_ollama(self):
        provider = get_provider("ollama")
        assert isinstance(provider, OllamaProvider)
        assert provider.name() == "ollama"

    def test_get_provider_unknown_raises(self):
        with pytest.raises(ValueError, match="unknown LLM provider"):
            get_provider("nonexistent")

    def test_ollama_provider_default_base_url(self):
        provider = OllamaProvider()
        assert provider.base_url == "http://localhost:11434"

    def test_ollama_provider_custom_base_url(self):
        provider = OllamaProvider(base_url="http://example.com:9999/")
        assert provider.base_url == "http://example.com:9999"

    def test_ollama_provider_explicit_model(self):
        provider = OllamaProvider(model="test-model:latest")
        assert provider._explicit_model == "test-model:latest"


# ---------------------------------------------------------------------------
# LLM authoring tests (require Ollama)
# ---------------------------------------------------------------------------


class TestLLMAuthoring:
    """Tests that use a real LLM to author stories from candidates."""

    def test_llm_authors_valid_json(self, ollama_provider: OllamaProvider):
        """LLM produces parseable JSON with required fields."""
        candidate = DETERMINISTIC_CANDIDATES[0]
        payload = _author_story(ollama_provider, candidate)
        required_fields = {"title", "slug", "intent", "story", "expected_behavior"}
        missing = required_fields - set(payload.keys())
        assert not missing, f"LLM output missing fields: {missing}"

    def test_llm_slug_is_valid_kebab_case(self, ollama_provider: OllamaProvider):
        """LLM-generated slug follows basic kebab-case format rules."""
        candidate = DETERMINISTIC_CANDIDATES[0]
        payload = _author_story(ollama_provider, candidate)
        slug = payload.get("slug", "")
        assert slug, "slug is empty"
        # Must be lowercase kebab-case characters only
        assert slug == slug.lower(), f"slug not lowercase: {slug!r}"
        assert "--" not in slug, f"slug has double hyphens: {slug!r}"
        assert not slug.startswith("-") and not slug.endswith("-"), (
            f"slug has leading/trailing hyphens: {slug!r}"
        )
        # Verify all chars are valid (lowercase alpha, digits, hyphens)
        import re
        assert re.match(r"^[a-z0-9]+(?:-[a-z0-9]+)*$", slug), (
            f"slug contains invalid characters: {slug!r}"
        )

    def test_llm_story_writes_via_write_story(
        self, ollama_provider: OllamaProvider, story_repo: Path
    ):
        """LLM-authored story passes through write_story.py successfully."""
        candidate = DETERMINISTIC_CANDIDATES[0]
        payload = _author_story(ollama_provider, candidate)

        # Ensure slug has enough words for write_story validation
        _fixup_slug(payload, "llm-authored-deploy-command")

        result = _run_write_story(story_repo, payload)
        assert result.returncode == 0, (
            f"write_story.py failed (exit {result.returncode}):\n"
            f"stderr: {result.stderr}\n"
            f"payload: {json.dumps(payload, indent=2)}"
        )
        out = json.loads(result.stdout)
        assert out["index_updated"] is True

        # Verify the story file was written
        story_path = story_repo / "docs" / "stories" / f"{payload['slug']}.md"
        assert story_path.exists(), f"story file not created at {story_path}"

    def test_llm_story_parses_with_storystore_lib(
        self, ollama_provider: OllamaProvider, story_repo: Path
    ):
        """Written LLM story parses correctly with storystore_lib."""
        candidate = DETERMINISTIC_CANDIDATES[1]  # health route
        payload = _author_story(ollama_provider, candidate)

        _fixup_slug(payload, "health-endpoint-returns-status")

        result = _run_write_story(story_repo, payload)
        assert result.returncode == 0, (
            f"write_story.py failed:\nstderr: {result.stderr}\npayload: {json.dumps(payload, indent=2)}"
        )

        story_path = story_repo / "docs" / "stories" / f"{payload['slug']}.md"
        parsed = lib.parse_story(story_path)
        assert parsed.slug == payload["slug"]
        assert parsed.authority == "observed"
        assert parsed.change_resistance == "low"
        assert parsed.status == "draft"

    def test_llm_story_passes_audit(
        self, ollama_provider: OllamaProvider, story_repo: Path
    ):
        """Written LLM story passes stories-audit without errors."""
        candidate = DETERMINISTIC_CANDIDATES[2]  # config-set
        payload = _author_story(ollama_provider, candidate)

        _fixup_slug(payload, "user-sets-configuration-values")

        result = _run_write_story(story_repo, payload)
        assert result.returncode == 0, (
            f"write_story.py failed:\nstderr: {result.stderr}\npayload: {json.dumps(payload, indent=2)}"
        )

        audit_result = _run_audit(story_repo, payload["slug"])
        # Audit should succeed (exit 0) — findings are acceptable,
        # but the story must not cause a parse error (exit 2/3).
        assert audit_result.returncode == 0, (
            f"audit failed (exit {audit_result.returncode}):\n"
            f"stderr: {audit_result.stderr}\n"
            f"stdout: {audit_result.stdout}"
        )

    def test_llm_observed_defaults_applied(
        self, ollama_provider: OllamaProvider, story_repo: Path
    ):
        """Observed mode defaults (authority, change_resistance) are applied."""
        candidate = DETERMINISTIC_CANDIDATES[0]
        payload = _author_story(ollama_provider, candidate)

        _fixup_slug(payload, "deploy-application-to-production")

        # Remove fields that write_story should default
        payload.pop("authority", None)
        payload.pop("change_resistance", None)
        payload.pop("status", None)

        result = _run_write_story(story_repo, payload)
        assert result.returncode == 0, f"write_story.py failed:\n{result.stderr}"

        story_path = story_repo / "docs" / "stories" / f"{payload['slug']}.md"
        text = story_path.read_text()
        data = lib.parse_frontmatter(text)
        assert data["authority"] == "observed"
        assert data["change_resistance"] == "low"
        assert data["locked_sections"] == []

    def test_llm_index_regenerated(
        self, ollama_provider: OllamaProvider, story_repo: Path
    ):
        """INDEX.md is regenerated after LLM story is written."""
        candidate = DETERMINISTIC_CANDIDATES[1]
        payload = _author_story(ollama_provider, candidate)

        _fixup_slug(payload, "health-check-endpoint-responds")

        result = _run_write_story(story_repo, payload)
        assert result.returncode == 0, f"write_story.py failed:\n{result.stderr}"

        index = (story_repo / "docs" / "stories" / "INDEX.md").read_text()
        assert payload["slug"] in index
        assert "1 stories" in index
        assert "# Intent Story Index" in index


# ---------------------------------------------------------------------------
# JSON extraction tests (deterministic, no LLM)
# ---------------------------------------------------------------------------


class TestJSONExtraction:
    """Tests for the _extract_json helper — deterministic, no LLM needed."""

    def test_plain_json(self):
        text = '{"title": "Deploy App", "slug": "deploy-the-app"}'
        result = _extract_json(text)
        assert result["title"] == "Deploy App"

    def test_json_with_markdown_fences(self):
        text = '```json\n{"title": "Deploy App", "slug": "deploy-the-app"}\n```'
        result = _extract_json(text)
        assert result["title"] == "Deploy App"

    def test_json_with_surrounding_text(self):
        text = 'Here is the JSON:\n{"title": "Deploy App", "slug": "deploy-the-app"}\nDone.'
        result = _extract_json(text)
        assert result["title"] == "Deploy App"

    def test_no_json_raises(self):
        with pytest.raises(ValueError, match="No JSON object found"):
            _extract_json("no json here")

    def test_nested_braces(self):
        text = '{"title": "X", "evidence": {"tests": ["a.ts"]}}'
        result = _extract_json(text)
        assert result["evidence"]["tests"] == ["a.ts"]


# ---------------------------------------------------------------------------
# Prompt structure tests (deterministic, no LLM)
# ---------------------------------------------------------------------------


class TestPromptStructure:
    """Verify prompts contain expected structural elements."""

    def test_system_prompt_mentions_required_fields(self):
        for field in ("title", "slug", "intent", "story", "expected_behavior"):
            assert field in SYSTEM_PROMPT, f"system prompt missing field: {field}"

    def test_system_prompt_mentions_kebab_case(self):
        assert "kebab-case" in SYSTEM_PROMPT

    def test_candidate_prompt_includes_candidate_data(self):
        candidate = DETERMINISTIC_CANDIDATES[0]
        prompt = _build_prompt(candidate)
        assert candidate["kind"] in prompt
        assert candidate["name"] in prompt
        assert candidate["summary"] in prompt
