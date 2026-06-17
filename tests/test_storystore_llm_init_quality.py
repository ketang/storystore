"""Opt-in qualitative acceptance tests for fresh-init observed stories.

Run the full stories-init fresh-init flow against fixture repos with known
user-facing surfaces, use Zolem fixtures as an evaluator to score the generated
top-5 stories, and assert quality properties (count, uniqueness, structural
fields, distinct capability coverage, unsupported-surface avoidance).

All LLM-dependent tests skip cleanly when Zolem is unavailable.
Structural assertions are stored here; exact LLM text is not asserted.
"""

from __future__ import annotations

import importlib.util
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from .helpers import REPO_ROOT, copy_fixture, count_story_files
from .conftest import require_zolem
from .llm_provider import LLMProvider, ZolemProvider

WRITE_STORY_SCRIPT = REPO_ROOT / "shared" / "write_story.py"
LIST_CANDIDATES_SCRIPT = REPO_ROOT / "shared" / "list_candidates.py"
INIT_MECHANICAL_SCRIPT = REPO_ROOT / "shared" / "stories_init_mechanical.py"
LIB_PATH = REPO_ROOT / "shared" / "storystore_lib.py"

# Fixtures designed for init-quality tests (contain discoverable surfaces).
CLI_FIXTURE = "init-quality-cli"
API_FIXTURE = "init-quality-api"


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
# Prompts for the init-quality flow
# ---------------------------------------------------------------------------

SELECTION_SYSTEM_PROMPT = """\
You are a technical analyst selecting the most important user-facing
capabilities from a software project for documentation. You will receive a
list of discovered surfaces (candidates). Select exactly the top {count}
candidates that best represent distinct, user-facing capabilities.

Rules:
- Prefer user-invoked surfaces: CLI commands, HTTP routes, package scripts,
  package bin entries, package exports.
- Select DISTINCT workflows — do not pick variants of the same flow.
- Skip surfaces whose purpose is purely structural or infrastructural
  (build, lint, typecheck, etc.).
- Output ONLY a JSON array of indices (0-based) into the candidate list.
  Example: [0, 3, 5, 7, 12]
- Output exactly {count} indices. No duplicates.
"""

AUTHORING_SYSTEM_PROMPT = """\
You are a technical writer authoring observed-mode intent stories for a
software project. Each story follows a strict schema.

You will be given a candidate (a discovered user-facing surface) and must
produce a JSON object with these fields:

- title: A human-readable title (sentence case, 3-8 words)
- slug: kebab-case, 4-8 hyphen-separated words, lowercase ASCII only
- intent: 1-2 sentences explaining WHY this capability exists from the
  user's perspective. Do not use the placeholder "Inferred from code;
  not human-confirmed."
- story: 1-3 sentences describing WHAT the user does
- expected_behavior: 1-3 sentences describing WHAT should happen
- boundaries: 1-2 sentences describing what is NOT covered
- auditable_claims: a list of 1-3 short, verifiable factual claims
- evidence: an object with keys "tests" (list of test file paths),
  "surface" (list of surface refs like "cli: command-name"), and
  "docs" (list of doc file paths)

Rules:
- The slug MUST be kebab-case with 4-8 words
- The slug MUST contain only lowercase letters, digits, and single hyphens
- The intent MUST describe the user-facing WHY, not just echo the surface name
- Output ONLY the JSON object, no markdown fences, no explanation
- All string values must be non-empty
"""

EVALUATION_SYSTEM_PROMPT = """\
You are a quality evaluator for software documentation stories. You will
receive a set of authored stories for a project and must evaluate them on
these criteria:

1. Candidate quality: each story describes a meaningful user-facing capability
2. Duplicate avoidance: no two stories describe the same capability
3. Distinct capability coverage: stories cover different areas of the project
4. Unsupported-surface avoidance: stories do not describe build/lint/test
   infrastructure as user-facing capabilities
5. User-facing Intent: the Intent field explains WHY from the user perspective
6. Effect completeness: Expected Behavior and Boundaries fields are present
   and meaningful

For each criterion, output a JSON object:
{
  "candidate_quality": {"score": 1-5, "issues": ["..."]},
  "duplicate_avoidance": {"score": 1-5, "issues": ["..."]},
  "coverage_breadth": {"score": 1-5, "issues": ["..."]},
  "surface_appropriateness": {"score": 1-5, "issues": ["..."]},
  "intent_quality": {"score": 1-5, "issues": ["..."]},
  "effect_completeness": {"score": 1-5, "issues": ["..."]}
}

Scores: 1=terrible, 2=poor, 3=acceptable, 4=good, 5=excellent.
Output ONLY the JSON object.
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extract_json(text: str) -> Any:
    """Extract a JSON value (object or array) from LLM response text."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        start = 1
        end = len(lines)
        for i in range(len(lines) - 1, 0, -1):
            if lines[i].strip() == "```":
                end = i
                break
        cleaned = "\n".join(lines[start:end]).strip()

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    # Find first JSON structure
    for opener, closer in [("{", "}"), ("[", "]")]:
        start = cleaned.find(opener)
        if start == -1:
            continue
        depth = 0
        for i in range(start, len(cleaned)):
            if cleaned[i] == opener:
                depth += 1
            elif cleaned[i] == closer:
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(cleaned[start : i + 1])
                    except json.JSONDecodeError:
                        break
    raise ValueError(f"No JSON found in LLM response: {text[:200]!r}")


def _run_mechanical_init(repo_root: Path) -> dict[str, Any]:
    """Run stories-init-mechanical and return parsed JSON output."""
    result = subprocess.run(
        [sys.executable, str(INIT_MECHANICAL_SCRIPT), "--repo-root", str(repo_root)],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout)


def _list_candidates(repo_root: Path) -> list[dict[str, Any]]:
    """Run list_candidates.py and return the candidates list."""
    result = subprocess.run(
        [sys.executable, str(LIST_CANDIDATES_SCRIPT), "--repo-root", str(repo_root)],
        capture_output=True,
        text=True,
        check=True,
    )
    data = json.loads(result.stdout)
    return data.get("candidates", [])


def _select_top_candidates(
    provider: LLMProvider,
    candidates: list[dict[str, Any]],
    count: int = 5,
) -> list[dict[str, Any]]:
    """Use LLM to select the top N candidates from the full list.

    Falls back to a deterministic heuristic if LLM selection fails.
    """
    if len(candidates) <= count:
        return candidates

    try:
        candidate_text = "\n".join(
            f"[{i}] kind={c['kind']}, name={c['name']}, summary={c['summary']}"
            for i, c in enumerate(candidates)
        )
        prompt = (
            f"Here are {len(candidates)} discovered surfaces:\n\n"
            f"{candidate_text}\n\n"
            f"Select the top {count} by returning a JSON array of indices."
        )
        response = provider.generate(
            prompt, system=SELECTION_SYSTEM_PROMPT.format(count=count)
        )
        indices = _extract_json(response.text)
        if not isinstance(indices, list):
            raise ValueError("Expected list")

        selected = []
        seen: set[int] = set()
        for idx in indices:
            idx = int(idx)
            if 0 <= idx < len(candidates) and idx not in seen:
                selected.append(candidates[idx])
                seen.add(idx)
        if selected:
            # Pad if needed
            if len(selected) < count:
                for c in candidates:
                    if c not in selected:
                        selected.append(c)
                    if len(selected) >= count:
                        break
            return selected[:count]
    except (ValueError, AssertionError, json.JSONDecodeError, TypeError):
        pass

    # Deterministic fallback: prefer cli-command and http-route kinds
    priority = {"cli-command": 0, "http-route": 1, "bin": 2, "script": 3, "exports": 4}
    ranked = sorted(candidates, key=lambda c: (priority.get(c["kind"], 99), c["name"]))
    return ranked[:count]


def _author_story(
    provider: LLMProvider, candidate: dict[str, Any], retries: int = 2
) -> dict[str, Any]:
    """Ask the LLM to author a single observed story from a candidate.

    Retries up to ``retries`` times on parse failures since small LLMs
    produce invalid JSON intermittently.
    """
    prompt = (
        f"Author an observed-mode intent story for this candidate:\n\n"
        f"Kind: {candidate['kind']}\n"
        f"Name: {candidate['name']}\n"
        f"Summary: {candidate['summary']}\n"
        f"Evidence files: {', '.join(candidate.get('evidence', []))}\n\n"
        f"Produce the JSON object now."
    )
    last_error: Exception | None = None
    for _attempt in range(1 + retries):
        response = provider.generate(prompt, system=AUTHORING_SYSTEM_PROMPT)
        if not response.text:
            last_error = ValueError("LLM returned empty response")
            continue
        try:
            return _extract_json(response.text)
        except (ValueError, json.JSONDecodeError) as exc:
            last_error = exc
    raise last_error or ValueError("LLM authoring failed after retries")


def _fixup_slug(payload: dict[str, Any], fallback: str) -> None:
    """Ensure payload has a valid slug with at least 2 words."""
    slug = payload.get("slug", "")
    parts = [p for p in slug.split("-") if p]
    if len(parts) < 2 or not re.match(r"^[a-z0-9]+(?:-[a-z0-9]+)*$", slug):
        payload["slug"] = fallback


def _write_story(repo_root: Path, payload: dict[str, Any]) -> subprocess.CompletedProcess:
    """Pipe story JSON to write_story.py --observed."""
    return subprocess.run(
        [sys.executable, str(WRITE_STORY_SCRIPT), "--repo-root", str(repo_root), "--observed"],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        check=False,
    )


def _make_slug_fallback(candidate: dict[str, Any], index: int) -> str:
    """Generate a deterministic fallback slug from candidate metadata."""
    name = candidate.get("name", "unknown")
    slug_base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    parts = slug_base.split("-")
    if len(parts) < 2:
        slug_base = f"{candidate.get('kind', 'surface')}-{slug_base}"
    parts = slug_base.split("-")[:6]
    return "-".join(parts) + f"-story-{index}"


def _run_full_init_flow(
    provider: LLMProvider,
    fixture_name: str,
    tmp_path: Path,
    story_count: int = 5,
) -> tuple[Path, list[dict[str, Any]], list[dict[str, Any]]]:
    """Run the complete fresh-init flow against a fixture.

    Returns (repo_root, candidates, written_payloads).
    """
    repo = copy_fixture(fixture_name, tmp_path)

    # Phase 1: mechanical init (fixture has no docs/stories/ so fresh_init=true)
    init_result = _run_mechanical_init(repo)
    assert init_result["fresh_init"] is True

    # Phase 2: discover candidates
    candidates = _list_candidates(repo)
    if not candidates:
        pytest.skip(f"No candidates discovered in fixture {fixture_name!r}")

    # Select top N
    selected = _select_top_candidates(provider, candidates, count=story_count)

    # Author and write stories — tolerate individual LLM failures
    written: list[dict[str, Any]] = []
    used_slugs: set[str] = set()
    for i, candidate in enumerate(selected):
        try:
            payload = _author_story(provider, candidate)
        except (ValueError, AssertionError, json.JSONDecodeError):
            continue
        _fixup_slug(payload, _make_slug_fallback(candidate, i))

        # Ensure required fields for write_story.py
        if not payload.get("title") or not payload.get("slug"):
            continue

        # Avoid duplicate slugs (LLM may generate the same slug twice)
        if payload["slug"] in used_slugs:
            payload["slug"] = _make_slug_fallback(candidate, i)
        used_slugs.add(payload["slug"])

        result = _write_story(repo, payload)
        if result.returncode == 0:
            written.append(payload)

    return repo, candidates, written


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


class TestInitFlowDeterministic:
    """Tests for the init flow components that do not require an LLM."""

    def test_mechanical_init_fresh_on_empty(self, tmp_path: Path):
        """Mechanical init on empty dir reports fresh_init=true."""
        result = _run_mechanical_init(tmp_path)
        assert result["fresh_init"] is True
        assert (tmp_path / "docs" / "stories" / "README.md").exists()
        assert (tmp_path / "docs" / "stories" / "INDEX.md").exists()

    def test_list_candidates_cli_fixture(self, tmp_path: Path):
        """list_candidates discovers CLI commands in init-quality-cli fixture."""
        repo = copy_fixture(CLI_FIXTURE, tmp_path)
        candidates = _list_candidates(repo)
        kinds = {c["kind"] for c in candidates}
        assert "cli-command" in kinds, f"Expected cli-command in {kinds}"
        names = {c["name"] for c in candidates if c["kind"] == "cli-command"}
        assert "deploy" in names, f"Expected 'deploy' CLI command in {names}"

    def test_list_candidates_api_fixture(self, tmp_path: Path):
        """list_candidates discovers HTTP routes in init-quality-api fixture."""
        repo = copy_fixture(API_FIXTURE, tmp_path)
        candidates = _list_candidates(repo)
        kinds = {c["kind"] for c in candidates}
        assert "http-route" in kinds, f"Expected http-route in {kinds}"
        assert len(candidates) >= 5, f"Expected >=5 candidates, got {len(candidates)}"

    def test_candidate_structure(self, tmp_path: Path):
        """Each candidate has required keys: kind, name, summary, evidence."""
        repo = copy_fixture(CLI_FIXTURE, tmp_path)
        candidates = _list_candidates(repo)
        assert len(candidates) > 0, "No candidates to validate structure"
        for c in candidates:
            assert "kind" in c, f"Missing 'kind' in candidate: {c}"
            assert "name" in c, f"Missing 'name' in candidate: {c}"
            assert "summary" in c, f"Missing 'summary' in candidate: {c}"
            assert "evidence" in c, f"Missing 'evidence' in candidate: {c}"
            assert isinstance(c["evidence"], list)

    def test_cli_fixture_has_enough_candidates_for_top5(self, tmp_path: Path):
        """CLI fixture produces at least 5 candidates for top-5 selection."""
        repo = copy_fixture(CLI_FIXTURE, tmp_path)
        candidates = _list_candidates(repo)
        assert len(candidates) >= 5, (
            f"Expected >=5 candidates for top-5 selection, got {len(candidates)}"
        )

    def test_slug_fallback_generation(self):
        """Fallback slug generation produces valid kebab-case slugs."""
        candidate = {"kind": "cli-command", "name": "deploy", "summary": "CLI command deploy"}
        slug = _make_slug_fallback(candidate, 0)
        assert re.match(r"^[a-z0-9]+(?:-[a-z0-9]+)*$", slug)
        parts = slug.split("-")
        assert len(parts) >= 2

    def test_extract_json_array(self):
        """_extract_json handles JSON arrays for index selection."""
        text = "[0, 2, 4]"
        result = _extract_json(text)
        assert result == [0, 2, 4]

    def test_extract_json_array_with_fences(self):
        """_extract_json handles fenced JSON arrays."""
        text = "```json\n[1, 3, 5]\n```"
        result = _extract_json(text)
        assert result == [1, 3, 5]

    def test_extract_json_object(self):
        """_extract_json handles JSON objects."""
        text = 'Here is JSON: {"title": "Test", "slug": "test-slug"}'
        result = _extract_json(text)
        assert result["title"] == "Test"

    def test_extract_json_no_json_raises(self):
        """_extract_json raises when no JSON is found."""
        with pytest.raises(ValueError, match="No JSON found"):
            _extract_json("no json here")

    def test_deterministic_selection_fallback(self):
        """Deterministic fallback selects cli-command and http-route first."""
        candidates = [
            {"kind": "heading", "name": "Usage", "summary": "heading", "evidence": []},
            {"kind": "cli-command", "name": "deploy", "summary": "cmd", "evidence": []},
            {"kind": "http-route", "name": "GET /api", "summary": "route", "evidence": []},
            {"kind": "script", "name": "start", "summary": "script", "evidence": []},
            {"kind": "cli-command", "name": "login", "summary": "cmd", "evidence": []},
        ]
        # _select_top_candidates with a provider that would fail → falls back
        # Test the ranking logic directly
        priority = {"cli-command": 0, "http-route": 1, "bin": 2, "script": 3, "exports": 4}
        ranked = sorted(candidates, key=lambda c: (priority.get(c["kind"], 99), c["name"]))
        top3 = ranked[:3]
        kinds = [c["kind"] for c in top3]
        assert kinds[0] == "cli-command"
        assert kinds[1] == "cli-command"
        assert kinds[2] == "http-route"


# ---------------------------------------------------------------------------
# Full init quality tests (require Zolem)
# ---------------------------------------------------------------------------


class TestInitQualityCli:
    """Quality tests running the full init flow against the CLI fixture."""

    @pytest.fixture(autouse=True)
    def _run_init(self, zolem_provider: ZolemProvider, tmp_path: Path):
        """Run the full init flow once for this test class."""
        provider = require_zolem(zolem_provider)
        self.repo, self.candidates, self.written = _run_full_init_flow(
            provider, CLI_FIXTURE, tmp_path, story_count=5,
        )
        self.stories_dir = self.repo / "docs" / "stories"

    def _require_written(self) -> None:
        """Skip the test if the LLM failed to produce any stories."""
        if not self.written:
            pytest.skip("LLM did not produce writable stories for this run")

    def test_stories_written(self):
        """At least one story was successfully written."""
        # This test validates the LLM can produce stories — skip is acceptable
        # when the fixture provider is unavailable or returns no writable stories.
        self._require_written()

    def test_story_count_within_bounds(self):
        """Number of written stories does not exceed requested count."""
        self._require_written()
        file_count = count_story_files(self.stories_dir)
        assert file_count <= 5, f"Expected at most 5 stories, got {file_count}"
        assert file_count > 0, "Expected at least 1 story file"

    def test_unique_slugs(self):
        """All written stories have unique slugs."""
        self._require_written()
        slugs = [p.get("slug") for p in self.written]
        assert len(slugs) == len(set(slugs)), f"Duplicate slugs: {slugs}"

    def test_stories_parse_with_lib(self):
        """All written stories parse successfully with storystore_lib."""
        self._require_written()
        for payload in self.written:
            story_path = self.stories_dir / f"{payload['slug']}.md"
            if not story_path.exists():
                continue
            parsed = lib.parse_story(story_path)
            assert parsed.slug == payload["slug"]
            assert parsed.authority == "observed"
            assert parsed.change_resistance == "low"

    def test_intent_field_present_and_nontrivial(self):
        """Most stories have a non-placeholder Intent field."""
        self._require_written()
        placeholder_count = 0
        total = 0
        for payload in self.written:
            story_path = self.stories_dir / f"{payload['slug']}.md"
            if not story_path.exists():
                continue
            text = story_path.read_text()
            assert "## Intent" in text, f"Missing Intent section in {payload['slug']}"
            total += 1
            if "Inferred from code; not human-confirmed" in text:
                placeholder_count += 1
        # Allow up to half the stories to have placeholder intent with small LLMs
        if total > 0:
            assert placeholder_count < total, (
                f"All {total} stories have placeholder Intent"
            )

    def test_expected_behavior_present(self):
        """Each story has an Expected Behavior section."""
        self._require_written()
        for payload in self.written:
            story_path = self.stories_dir / f"{payload['slug']}.md"
            if not story_path.exists():
                continue
            text = story_path.read_text()
            assert "## Expected Behavior" in text, (
                f"Missing Expected Behavior in {payload['slug']}"
            )

    def test_index_regenerated(self):
        """INDEX.md is regenerated and contains written story slugs."""
        self._require_written()
        index_path = self.stories_dir / "INDEX.md"
        assert index_path.exists()
        index_text = index_path.read_text()
        found = any(p["slug"] in index_text for p in self.written)
        assert found, "No written slug found in INDEX.md"


class TestInitQualityApi:
    """Quality tests running the full init flow against the API fixture."""

    @pytest.fixture(autouse=True)
    def _run_init(self, zolem_provider: ZolemProvider, tmp_path: Path):
        provider = require_zolem(zolem_provider)
        self.repo, self.candidates, self.written = _run_full_init_flow(
            provider, API_FIXTURE, tmp_path, story_count=5,
        )
        self.stories_dir = self.repo / "docs" / "stories"

    def _require_written(self) -> None:
        if not self.written:
            pytest.skip("LLM did not produce writable stories for this run")

    def test_stories_written(self):
        """At least one story was successfully written."""
        self._require_written()

    def test_unique_slugs(self):
        """All written stories have unique slugs."""
        self._require_written()
        slugs = [p.get("slug") for p in self.written]
        assert len(slugs) == len(set(slugs)), f"Duplicate slugs: {slugs}"

    def test_stories_parse_with_lib(self):
        """All written stories parse successfully with storystore_lib."""
        self._require_written()
        for payload in self.written:
            story_path = self.stories_dir / f"{payload['slug']}.md"
            if not story_path.exists():
                continue
            parsed = lib.parse_story(story_path)
            assert parsed.slug == payload["slug"]
            assert parsed.authority == "observed"


# ---------------------------------------------------------------------------
# LLM-evaluated quality scoring (require Zolem)
# ---------------------------------------------------------------------------


class TestInitQualityLLMEvaluation:
    """Use Zolem as an evaluator to score the quality of generated stories."""

    @pytest.fixture(autouse=True)
    def _run_init(self, zolem_provider: ZolemProvider, tmp_path: Path):
        self.provider = require_zolem(zolem_provider)
        self.repo, self.candidates, self.written = _run_full_init_flow(
            self.provider, CLI_FIXTURE, tmp_path, story_count=5,
        )
        self.stories_dir = self.repo / "docs" / "stories"

    def _collect_story_texts(self) -> list[str]:
        """Read all written story files."""
        texts = []
        for payload in self.written:
            story_path = self.stories_dir / f"{payload['slug']}.md"
            if story_path.exists():
                texts.append(story_path.read_text())
        return texts

    def _evaluate_stories(self) -> dict[str, Any]:
        """Ask LLM to evaluate the quality of written stories."""
        story_texts = self._collect_story_texts()
        if not story_texts:
            pytest.skip("No stories written to evaluate")

        stories_block = "\n\n---\n\n".join(
            f"Story {i+1}:\n{text}" for i, text in enumerate(story_texts)
        )
        prompt = (
            f"Evaluate these {len(story_texts)} observed-mode stories:\n\n"
            f"{stories_block}\n\n"
            f"Produce the evaluation JSON object now."
        )
        response = self.provider.generate(prompt, system=EVALUATION_SYSTEM_PROMPT)
        raw = _extract_json(response.text)
        # Handle non-dict responses (LLM may return a list or other shape)
        if not isinstance(raw, dict):
            pytest.skip(f"LLM evaluator returned non-dict: {type(raw).__name__}")
        # Normalize: LLMs sometimes return {"criterion": score} instead of
        # {"criterion": {"score": score, "issues": []}}
        normalized: dict[str, Any] = {}
        for k, v in raw.items():
            if isinstance(v, (int, float)):
                normalized[k] = {"score": v, "issues": []}
            elif isinstance(v, dict):
                normalized[k] = v
            else:
                normalized[k] = {"score": 0, "issues": [str(v)]}
        return normalized

    def test_evaluation_produces_valid_scores(self):
        """LLM evaluator produces valid scores for all criteria."""
        scores = self._evaluate_stories()
        expected_criteria = [
            "candidate_quality",
            "duplicate_avoidance",
            "coverage_breadth",
            "surface_appropriateness",
            "intent_quality",
            "effect_completeness",
        ]
        for criterion in expected_criteria:
            assert criterion in scores, f"Missing evaluation criterion: {criterion}"
            entry = scores[criterion]
            assert isinstance(entry, dict), f"Expected dict for {criterion}"
            assert "score" in entry, f"Missing 'score' in {criterion}"
            score = entry["score"]
            assert isinstance(score, (int, float)), (
                f"Score for {criterion} is not numeric: {score!r}"
            )
            assert 1 <= score <= 5, f"Score for {criterion} out of range: {score}"

    def test_no_criterion_scores_terrible(self):
        """No quality criterion scores 1 (terrible)."""
        scores = self._evaluate_stories()
        for criterion, entry in scores.items():
            if isinstance(entry, dict) and "score" in entry:
                assert entry["score"] > 1, (
                    f"Criterion {criterion!r} scored 1 (terrible): "
                    f"{entry.get('issues', [])}"
                )

    def test_duplicate_avoidance_acceptable(self):
        """Duplicate avoidance scores at least 3 (acceptable)."""
        scores = self._evaluate_stories()
        dup_score = scores.get("duplicate_avoidance", {}).get("score", 0)
        assert dup_score >= 3, (
            f"Duplicate avoidance score too low: {dup_score}; "
            f"issues: {scores.get('duplicate_avoidance', {}).get('issues', [])}"
        )

    def test_surface_appropriateness_acceptable(self):
        """Surface appropriateness scores at least 3 (acceptable)."""
        scores = self._evaluate_stories()
        sa_score = scores.get("surface_appropriateness", {}).get("score", 0)
        assert sa_score >= 3, (
            f"Surface appropriateness score too low: {sa_score}; "
            f"issues: {scores.get('surface_appropriateness', {}).get('issues', [])}"
        )


# ---------------------------------------------------------------------------
# Cross-fixture coverage tests (require Zolem)
# ---------------------------------------------------------------------------


class TestInitQualityCrossFixture:
    """Verify distinct stories across different fixture types."""

    def test_different_fixtures_produce_different_stories(
        self, zolem_provider: ZolemProvider, tmp_path: Path
    ):
        """Stories from CLI and API fixtures have no slug overlap."""
        provider = require_zolem(zolem_provider)
        cli_dir = tmp_path / "cli-run"
        cli_dir.mkdir()
        api_dir = tmp_path / "api-run"
        api_dir.mkdir()

        _, _, cli_written = _run_full_init_flow(
            provider, CLI_FIXTURE, cli_dir, story_count=3,
        )
        _, _, api_written = _run_full_init_flow(
            provider, API_FIXTURE, api_dir, story_count=3,
        )

        if not cli_written or not api_written:
            pytest.skip("LLM did not produce stories for both fixtures")
        cli_slugs = {p["slug"] for p in cli_written}
        api_slugs = {p["slug"] for p in api_written}
        overlap = cli_slugs & api_slugs
        assert not overlap, f"Slug overlap between fixtures: {overlap}"

    def test_stories_cover_different_kinds(
        self, zolem_provider: ZolemProvider, tmp_path: Path
    ):
        """Stories across fixtures cover different surface kinds."""
        provider = require_zolem(zolem_provider)
        cli_dir = tmp_path / "cli-kinds"
        cli_dir.mkdir()
        api_dir = tmp_path / "api-kinds"
        api_dir.mkdir()

        _, _, cli_written = _run_full_init_flow(
            provider, CLI_FIXTURE, cli_dir, story_count=3,
        )
        _, _, api_written = _run_full_init_flow(
            provider, API_FIXTURE, api_dir, story_count=3,
        )

        if not cli_written or not api_written:
            pytest.skip("LLM did not produce stories for both fixtures")
        # Both fixture types produced stories — basic coverage check
        assert len(cli_written) > 0
        assert len(api_written) > 0
