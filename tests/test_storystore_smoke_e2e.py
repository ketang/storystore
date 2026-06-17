"""Smoke and end-to-end plugin verification tests.

These tests verify the full generated plugin payloads work end-to-end — not
just that files exist, but that the tooling actually runs correctly against a
fixture repo. The contract tests (test_plugin_contract.py) cover deep schema
compliance; these tests cover completeness and representative command flows.

Manual verification command set (for reference):
    # Build both plugin payloads
    scripts/build-plugin -v

    # Init a fresh repo
    python3 shared/stories_init_mechanical.py --repo-root /tmp/test-repo

    # List candidates
    python3 shared/list_candidates.py --repo-root /tmp/test-repo

    # Write a story (observed mode, JSON on stdin)
    echo '{"slug":"my-story","title":"My Story","intent":"..."}' | \
        python3 shared/write_story.py --repo-root /tmp/test-repo --observed

    # Audit
    python3 shared/audit.py --repo-root /tmp/test-repo

    # Coverage
    python3 shared/coverage.py --repo-root /tmp/test-repo

    # Edit section (should refuse on immutable story)
    python3 shared/edit_section.py --repo-root /tmp/test-repo \
        --story locked-slug --section Story --content "new text"

    # Impact check
    python3 shared/impact_check.py --repo-root /tmp/test-repo \
        --file src/cli.ts
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


@dataclass
class ScriptResult:
    returncode: int
    stdout: str
    stderr: str

    def json(self):
        return json.loads(self.stdout)


def _copy_fixture(name: str, dest: Path) -> Path:
    src = FIXTURES_DIR / name
    target = dest / name if dest.exists() and any(dest.iterdir()) else dest
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(src, target)
    return target


def _run_skill_script(
    script_rel: str,
    fixture_dir: Path,
    *args: str,
    repo_root_flag: str = "--repo-root",
) -> ScriptResult:
    script_path = REPO_ROOT / script_rel
    if not script_path.exists():
        return ScriptResult(returncode=127, stdout="", stderr=f"script not found: {script_rel}\n")
    cmd = [sys.executable, str(script_path), repo_root_flag, str(fixture_dir), *args]
    completed = subprocess.run(cmd, capture_output=True, text=True, check=False)
    return ScriptResult(
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )


# ---------------------------------------------------------------------------
# Plugin build helpers
# ---------------------------------------------------------------------------

BUILD_SCRIPT = REPO_ROOT / "scripts" / "build-plugin"

EXPECTED_SKILLS = [
    "stories-init",
    "stories-generate",
    "stories-audit",
    "stories-coverage",
    "stories-update",
    "stories-impact-check",
]


def _build_plugin(tmp_path: Path) -> Path:
    """Copy canonical sources into tmp_path and run build-plugin -v."""
    for sub in ("skills", "scripts", "shared"):
        shutil.copytree(REPO_ROOT / sub, tmp_path / sub)
    shutil.copy(REPO_ROOT / "plugin-version.json", tmp_path / "plugin-version.json")
    result = subprocess.run(
        [sys.executable, str(tmp_path / "scripts" / "build-plugin"), "-v"],
        capture_output=True,
        text=True,
        check=False,
        cwd=tmp_path,
    )
    assert result.returncode == 0, f"build-plugin failed: {result.stderr}"
    return tmp_path


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _write_story(
    repo: Path,
    slug: str,
    *,
    title: str | None = None,
    status: str = "active",
    authority: str = "accepted",
    resistance: str = "medium",
    intent: str = "Users can use this feature.",
    story: str = "A user needs this.",
    expected: str = "The system responds correctly.",
    boundaries: str = "Out of scope items.",
    claims: list[str] | None = None,
    tests: list[str] | None = None,
    surface: list[str] | None = None,
    docs: list[str] | None = None,
) -> Path:
    """Write a minimal valid story file into repo/docs/stories/<slug>.md."""
    stories_dir = repo / "docs" / "stories"
    stories_dir.mkdir(parents=True, exist_ok=True)
    title = title or slug.replace("-", " ").title()
    claims = claims or ["- The feature works as expected."]
    tests_str = "\n".join(f"- `{t}`" for t in (tests or []))
    surface_str = "\n".join(f"- `{s}`" for s in (surface or []))
    docs_str = "\n".join(f"- `{d}`" for d in (docs or []))

    content = f"""---
schema_version: 1
title: {title}
slug: {slug}
status: {status}
authority: {authority}
change_resistance: {resistance}
tests_applicable: true
---

# {title}

## Intent
{intent}

## Story
{story}

## Expected Behavior
{expected}

## Boundaries
{boundaries}

## Auditable Claims
{chr(10).join(claims)}

## Evidence
### Tests
{tests_str}
### Surface
{surface_str}
### Docs
{docs_str}
"""
    path = stories_dir / f"{slug}.md"
    path.write_text(content, encoding="utf-8")
    return path


def _run_script(script_rel: str, repo: Path, *args: str) -> ScriptResult:
    """Run a shared/ or scripts/ script against a repo directory."""
    return _run_skill_script(script_rel, repo, *args)


# ===========================================================================
# Plugin payload smoke tests
# ===========================================================================


class TestPluginPayloadSmoke:
    """Verify that build-plugin produces complete payloads for both runtimes."""

    def test_claude_manifest_and_skills_complete(self, tmp_path):
        out = _build_plugin(tmp_path)
        manifest_path = out / ".claude-plugin" / "plugin.json"
        assert manifest_path.exists(), "Claude manifest not generated"
        manifest = json.loads(manifest_path.read_text())
        assert manifest["name"] == "storystore"
        assert manifest["version"]
        assert manifest["description"]

        skills_dir = out / ".claude" / "skills"
        for name in EXPECTED_SKILLS:
            skill_file = skills_dir / f"{name}.md"
            assert skill_file.exists(), f"Claude skill {name}.md missing"
            text = skill_file.read_text()
            assert f"name: {name}" in text, f"skill name mismatch in {name}.md"
            # Smoke: skill files should have substantial content
            assert len(text) > 100, f"{name}.md suspiciously short ({len(text)} chars)"

    def test_codex_manifest_and_skills_complete(self, tmp_path):
        out = _build_plugin(tmp_path)
        manifest_path = out / ".codex-plugin" / "plugin.json"
        assert manifest_path.exists(), "Codex manifest not generated"
        manifest = json.loads(manifest_path.read_text())
        assert manifest["name"] == "storystore"
        assert manifest["version"]
        assert manifest["skills"] == "./.codex-plugin/skills"
        assert manifest["interface"]["displayName"] == "Storystore"

        skills_root = out / ".codex-plugin" / "skills"
        for name in EXPECTED_SKILLS:
            skill_md = skills_root / name / "SKILL.md"
            assert skill_md.exists(), f"Codex skill {name}/SKILL.md missing"
            text = skill_md.read_text()
            assert f"name: {name}" in text

    def test_both_manifests_version_aligned(self, tmp_path):
        out = _build_plugin(tmp_path)
        claude_v = json.loads(
            (out / ".claude-plugin" / "plugin.json").read_text()
        )["version"]
        codex_v = json.loads(
            (out / ".codex-plugin" / "plugin.json").read_text()
        )["version"]
        assert claude_v == codex_v, "Claude and Codex manifest versions differ"

    def test_codex_skills_contain_shared_scripts(self, tmp_path):
        """Codex payload directories should include materialized shared scripts."""
        out = _build_plugin(tmp_path)
        skills_root = out / ".codex-plugin" / "skills"
        # At least one skill should have scripts/ or references/ subdirectory
        has_subdir = False
        for name in EXPECTED_SKILLS:
            skill_dir = skills_root / name
            subdirs = [d.name for d in skill_dir.iterdir() if d.is_dir()]
            if subdirs:
                has_subdir = True
                break
        assert has_subdir, "No Codex skill has scripts/references subdirectories"


# ===========================================================================
# stories-init mechanical flow
# ===========================================================================


class TestInitMechanicalFlow:
    """Run init-mechanical against a bare fixture and verify scaffolding."""

    def test_init_creates_scaffolding(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "README.md").write_text("# Test repo\n")
        result = _run_script("shared/stories_init_mechanical.py", repo)
        assert result.returncode == 0, f"init failed: {result.stderr}"

        data = result.json()
        assert data["fresh_init"] is True

        stories_dir = repo / "docs" / "stories"
        assert stories_dir.is_dir()
        assert (stories_dir / "README.md").exists()
        assert (stories_dir / "INDEX.md").exists()

    def test_init_is_idempotent(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "README.md").write_text("# Test repo\n")
        _run_script("shared/stories_init_mechanical.py", repo)

        result = _run_script("shared/stories_init_mechanical.py", repo)
        assert result.returncode == 0
        data = result.json()
        assert data["fresh_init"] is False


# ===========================================================================
# stories-generate flow (list-candidates + write-story)
# ===========================================================================


class TestGenerateFlow:
    """Run list-candidates and write-story in sequence against ts-cli fixture."""

    def test_list_candidates_returns_json(self, tmp_path):
        repo = _copy_fixture("ts-cli", tmp_path)
        result = _run_script("shared/list_candidates.py", repo)
        assert result.returncode == 0, f"list_candidates failed: {result.stderr}"
        data = result.json()
        assert "candidates" in data or "surfaces" in data or isinstance(data, list), (
            f"unexpected list_candidates output shape: {list(data.keys()) if isinstance(data, dict) else type(data)}"
        )

    def test_write_story_observed_creates_file(self, tmp_path):
        repo = _copy_fixture("ts-cli", tmp_path)
        story_input = json.dumps({
            "slug": "smoke-test-story",
            "title": "Smoke Test Story",
            "intent": "Verify that a smoke test story can be written.",
            "story": "A developer runs the smoke test.",
            "expected_behavior": "The story file is created.",
            "boundaries": "No real boundaries.",
            "auditable_claims": ["- The story file exists after generation."],
        })
        cmd = [
            sys.executable,
            str(REPO_ROOT / "shared" / "write_story.py"),
            "--repo-root", str(repo),
            "--observed",
        ]
        completed = subprocess.run(
            cmd, input=story_input, capture_output=True, text=True, check=False,
        )
        assert completed.returncode == 0, f"write_story failed: {completed.stderr}"
        story_file = repo / "docs" / "stories" / "smoke-test-story.md"
        assert story_file.exists(), "write_story did not create story file"
        content = story_file.read_text()
        assert "smoke-test-story" in content
        assert "authority: observed" in content


# ===========================================================================
# stories-audit
# ===========================================================================


class TestAuditFlow:
    """Run audit against a fixture repo and verify report output."""

    def test_audit_produces_report(self, tmp_path):
        repo = _copy_fixture("ts-cli", tmp_path)
        result = _run_script("shared/audit.py", repo)
        assert result.returncode in (0, 1), f"audit crashed: {result.stderr}"
        data = result.json()
        assert "findings_count" in data
        assert isinstance(data["findings_count"], int)

    def test_audit_with_strict_flag(self, tmp_path):
        repo = _copy_fixture("ts-cli", tmp_path)
        result = _run_script("shared/audit.py", repo, "--strict")
        # strict exits 1 when findings exist, 0 when clean
        assert result.returncode in (0, 1)
        data = result.json()
        if data["findings_count"] > 0:
            assert result.returncode == 1
        else:
            assert result.returncode == 0

    def test_audit_scoped_to_single_story(self, tmp_path):
        repo = _copy_fixture("ts-cli", tmp_path)
        result = _run_script("shared/audit.py", repo, "--story", "cli-init-command")
        assert result.returncode in (0, 1), f"scoped audit failed: {result.stderr}"
        data = result.json()
        assert "findings_count" in data


# ===========================================================================
# stories-coverage
# ===========================================================================


class TestCoverageFlow:
    """Run coverage against a fixture repo and verify report output."""

    def test_coverage_produces_report(self, tmp_path):
        repo = _copy_fixture("ts-cli", tmp_path)
        result = _run_script("shared/coverage.py", repo)
        assert result.returncode in (0, 1), f"coverage crashed: {result.stderr}"
        data = result.json()
        assert "findings_count" in data
        assert isinstance(data["findings_count"], int)

    def test_coverage_strict_exit_code(self, tmp_path):
        repo = _copy_fixture("ts-cli", tmp_path)
        result = _run_script("shared/coverage.py", repo, "--strict")
        assert result.returncode in (0, 1)
        data = result.json()
        if data["findings_count"] > 0:
            assert result.returncode == 1
        else:
            assert result.returncode == 0


# ===========================================================================
# stories-update refusal (immutable story)
# ===========================================================================


class TestUpdateRefusal:
    """Verify that editing an immutable story is refused with exit 3."""

    def test_edit_immutable_story_refuses(self, tmp_path):
        repo = tmp_path
        _write_story(
            repo,
            "locked-feature",
            authority="accepted",
            resistance="immutable",
            intent="This feature is locked and should not be changed.",
        )
        result = _run_script(
            "shared/edit_section.py",
            repo,
            "--story", "locked-feature",
            "--section", "Story",
            "--content", "Attempting to modify locked story.",
        )
        assert result.returncode == 3, (
            f"expected exit 3 for immutable refusal, got {result.returncode}; "
            f"stderr: {result.stderr}"
        )
        assert "immutable" in result.stderr.lower()

    def test_edit_immutable_metadata_refuses(self, tmp_path):
        repo = tmp_path
        _write_story(
            repo,
            "locked-feature",
            authority="accepted",
            resistance="immutable",
        )
        result = _run_script(
            "shared/edit_section.py",
            repo,
            "--story", "locked-feature",
            "--section", "title",
            "--content", "New Title",
        )
        assert result.returncode == 3

    def test_edit_locked_section_refuses(self, tmp_path):
        """A story with locked_sections should refuse edits to those sections."""
        stories_dir = tmp_path / "docs" / "stories"
        stories_dir.mkdir(parents=True)
        # Write story with locked_sections in frontmatter
        content = """\
---
schema_version: 1
title: Locked Section Story
slug: locked-section-story
status: active
authority: accepted
change_resistance: high
locked_sections:
  - Intent
tests_applicable: true
---

# Locked Section Story

## Intent
This intent is locked.

## Story
This story is editable.

## Expected Behavior
Expected behavior here.

## Boundaries
Boundaries here.

## Auditable Claims
- The feature works.

## Evidence
### Tests
### Surface
### Docs
"""
        (stories_dir / "locked-section-story.md").write_text(content)
        result = _run_script(
            "shared/edit_section.py",
            tmp_path,
            "--story", "locked-section-story",
            "--section", "Intent",
            "--content", "Trying to change locked intent.",
        )
        assert result.returncode == 3, (
            f"expected exit 3 for locked section, got {result.returncode}; "
            f"stderr: {result.stderr}"
        )


# ===========================================================================
# stories-impact-check
# ===========================================================================


class TestImpactCheckFlow:
    """Run impact-check against a fixture and verify lookup results."""

    def test_impact_check_by_file(self, tmp_path):
        repo = _copy_fixture("ts-cli", tmp_path)
        result = _run_script(
            "shared/impact_check.py",
            repo,
            "--file", "src/cli.ts",
        )
        assert result.returncode == 0, f"impact_check failed: {result.stderr}"
        data = result.json()
        # Should return a list of matches or a dict with matches key
        if isinstance(data, dict):
            assert "matches" in data or "stories" in data or "affected" in data, (
                f"unexpected impact_check shape: {sorted(data.keys())}"
            )
        elif isinstance(data, list):
            pass  # list of matches is acceptable
        else:
            pytest.fail(f"unexpected impact_check return type: {type(data)}")

    def test_impact_check_by_surface(self, tmp_path):
        repo = _copy_fixture("ts-cli", tmp_path)
        result = _run_script(
            "shared/impact_check.py",
            repo,
            "--surface", "cli: cli init",
        )
        assert result.returncode == 0, f"impact_check failed: {result.stderr}"
        data = result.json()
        # At least the cli-init-command story should match
        matched = False
        if isinstance(data, dict):
            matches = data.get("matches") or data.get("stories") or data.get("affected") or []
            matched = any("init" in str(m).lower() for m in matches)
        elif isinstance(data, list):
            matched = any("init" in str(m).lower() for m in data)
        assert matched, f"Expected cli-init-command to match surface 'cli: cli init'; got {data}"

    def test_impact_check_by_description(self, tmp_path):
        repo = _copy_fixture("ts-cli", tmp_path)
        result = _run_script(
            "shared/impact_check.py",
            repo,
            "--description", "changing the init command behavior",
        )
        assert result.returncode == 0, f"impact_check failed: {result.stderr}"
        data = result.json()
        # Should be parseable — description matching is best-effort
        assert data is not None


# ===========================================================================
# End-to-end: full lifecycle in a single fixture repo
# ===========================================================================


class TestFullLifecycleE2E:
    """Run the complete storystore lifecycle against a single fresh repo:
    init -> generate -> audit -> coverage -> impact-check.
    """

    def test_lifecycle(self, tmp_path):
        repo = tmp_path / "lifecycle"
        repo.mkdir()
        (repo / "README.md").write_text("# Lifecycle test\n")
        (repo / "src").mkdir()
        (repo / "src" / "app.ts").write_text("export function main() {}\n")

        # 1. Init
        init_result = _run_script("shared/stories_init_mechanical.py", repo)
        assert init_result.returncode == 0, f"init failed: {init_result.stderr}"
        assert (repo / "docs" / "stories" / "README.md").exists()

        # 2. Write a story
        story_input = json.dumps({
            "slug": "app-main-function",
            "title": "App Main Function",
            "intent": "The application has a main entry point.",
            "story": "A developer runs the app.",
            "expected_behavior": "The main function executes without error.",
            "boundaries": "Does not cover configuration.",
            "auditable_claims": ["- main() exists and is exported."],
        })
        cmd = [
            sys.executable,
            str(REPO_ROOT / "shared" / "write_story.py"),
            "--repo-root", str(repo),
            "--observed",
        ]
        write_result = subprocess.run(
            cmd, input=story_input, capture_output=True, text=True, check=False,
        )
        assert write_result.returncode == 0, f"write_story failed: {write_result.stderr}"
        assert (repo / "docs" / "stories" / "app-main-function.md").exists()

        # 3. Audit
        audit_result = _run_script("shared/audit.py", repo)
        assert audit_result.returncode in (0, 1), f"audit failed: {audit_result.stderr}"
        audit_data = audit_result.json()
        assert "findings_count" in audit_data

        # 4. Coverage
        cov_result = _run_script("shared/coverage.py", repo)
        assert cov_result.returncode in (0, 1), f"coverage failed: {cov_result.stderr}"
        cov_data = cov_result.json()
        assert "findings_count" in cov_data

        # 5. Impact check
        impact_result = _run_script(
            "shared/impact_check.py", repo,
            "--file", "src/app.ts",
        )
        assert impact_result.returncode == 0, f"impact_check failed: {impact_result.stderr}"
