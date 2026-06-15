"""Contract tests for the generated Claude and Codex plugin payloads.

These tests verify the *output* of ``scripts/build-plugin`` against the
expected contract: every skill is present, shared scripts are materialized
with correct content and permissions, manifests carry required fields, and
build-only artifacts (``packaging.json``) never leak into plugin outputs.

Run ``scripts/build-plugin -v`` before this module so the generated trees
are fresh.  The tests read from the real repo checkout — no temp-dir copies.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

# ── Canonical skill list ────────────────────────────────────────────

EXPECTED_SKILLS = [
    "stories-init",
    "stories-generate",
    "stories-audit",
    "stories-coverage",
    "stories-update",
    "stories-impact-check",
]

# Skills that declare shared_scripts via packaging.json.
SKILLS_WITH_PACKAGING = {
    "stories-audit",
    "stories-coverage",
    "stories-generate",
    "stories-impact-check",
}

SKILLS_WITHOUT_PACKAGING = {
    "stories-init",
    "stories-update",
}

# ── Derived packaging expectations ──────────────────────────────────


def _load_packaging(skill: str) -> dict:
    """Load a skill's packaging.json from the canonical source."""
    p = REPO_ROOT / "skills" / skill / "packaging.json"
    return json.loads(p.read_text())


def _shared_scripts_for(skill: str) -> list[str]:
    return _load_packaging(skill)["shared_scripts"]


# ── Paths ───────────────────────────────────────────────────────────

CLAUDE_PLUGIN_DIR = REPO_ROOT / ".claude-plugin"
CLAUDE_SKILLS_DIR = REPO_ROOT / ".claude" / "skills"
CODEX_PLUGIN_DIR = REPO_ROOT / ".codex-plugin"
CODEX_SKILLS_DIR = CODEX_PLUGIN_DIR / "skills"


# ═══════════════════════════════════════════════════════════════════
# 1. Claude payload completeness
# ═══════════════════════════════════════════════════════════════════


class TestClaudeManifest:
    """Verify .claude-plugin/plugin.json has all required fields."""

    @pytest.fixture()
    def manifest(self) -> dict:
        return json.loads((CLAUDE_PLUGIN_DIR / "plugin.json").read_text())

    def test_manifest_exists(self):
        assert (CLAUDE_PLUGIN_DIR / "plugin.json").exists()

    def test_required_fields_present(self, manifest):
        for key in ("name", "description", "version", "author"):
            assert key in manifest, f"missing required field '{key}'"

    def test_name_is_storystore(self, manifest):
        assert manifest["name"] == "storystore"

    def test_version_matches_source(self, manifest):
        source_version = json.loads(
            (REPO_ROOT / "plugin-version.json").read_text()
        )["version"]
        assert manifest["version"] == source_version

    def test_author_has_name(self, manifest):
        assert manifest["author"]["name"]

    def test_no_extra_top_level_keys(self, manifest):
        allowed = {"name", "description", "version", "author"}
        extra = set(manifest.keys()) - allowed
        assert not extra, f"unexpected keys in Claude manifest: {extra}"


class TestClaudeSkillFiles:
    """Verify .claude/skills/<name>.md for every expected skill."""

    def test_all_skills_present(self):
        for name in EXPECTED_SKILLS:
            path = CLAUDE_SKILLS_DIR / f"{name}.md"
            assert path.exists(), f"missing Claude skill file: {path.name}"

    def test_no_unexpected_skill_files(self):
        expected = {f"{s}.md" for s in EXPECTED_SKILLS}
        actual = {p.name for p in CLAUDE_SKILLS_DIR.glob("*.md")}
        extra = actual - expected
        assert not extra, f"unexpected Claude skill files: {extra}"

    @pytest.mark.parametrize("skill", EXPECTED_SKILLS)
    def test_skill_has_frontmatter(self, skill):
        text = (CLAUDE_SKILLS_DIR / f"{skill}.md").read_text()
        assert text.startswith("---\n"), f"{skill}.md missing frontmatter fence"

    @pytest.mark.parametrize("skill", EXPECTED_SKILLS)
    def test_skill_contains_name(self, skill):
        text = (CLAUDE_SKILLS_DIR / f"{skill}.md").read_text()
        assert f"name: {skill}" in text

    @pytest.mark.parametrize("skill", EXPECTED_SKILLS)
    def test_skill_matches_canonical_source(self, skill):
        generated = (CLAUDE_SKILLS_DIR / f"{skill}.md").read_bytes()
        canonical = (REPO_ROOT / "skills" / skill / "SKILL.md").read_bytes()
        assert generated == canonical, (
            f"Claude skill {skill}.md differs from canonical SKILL.md"
        )


# ═══════════════════════════════════════════════════════════════════
# 2. Codex payload completeness
# ═══════════════════════════════════════════════════════════════════


class TestCodexManifest:
    """Verify .codex-plugin/plugin.json has all required fields."""

    @pytest.fixture()
    def manifest(self) -> dict:
        return json.loads((CODEX_PLUGIN_DIR / "plugin.json").read_text())

    def test_manifest_exists(self):
        assert (CODEX_PLUGIN_DIR / "plugin.json").exists()

    def test_required_fields_present(self, manifest):
        for key in (
            "name", "description", "version", "author",
            "homepage", "repository", "license", "keywords",
            "skills", "interface",
        ):
            assert key in manifest, f"missing required field '{key}'"

    def test_name_is_storystore(self, manifest):
        assert manifest["name"] == "storystore"

    def test_version_matches_source(self, manifest):
        source_version = json.loads(
            (REPO_ROOT / "plugin-version.json").read_text()
        )["version"]
        assert manifest["version"] == source_version

    def test_skills_path(self, manifest):
        assert manifest["skills"] == "./.codex-plugin/skills"

    def test_author_has_name_email_url(self, manifest):
        author = manifest["author"]
        for key in ("name", "email", "url"):
            assert key in author, f"author missing '{key}'"
            assert author[key], f"author.{key} is empty"

    def test_interface_required_fields(self, manifest):
        interface = manifest["interface"]
        for key in (
            "displayName", "shortDescription", "longDescription",
            "developerName", "category", "capabilities",
            "websiteURL", "privacyPolicyURL", "termsOfServiceURL",
            "defaultPrompt",
        ):
            assert key in interface, f"interface missing '{key}'"

    def test_interface_display_name(self, manifest):
        assert manifest["interface"]["displayName"] == "Storystore"

    def test_interface_category(self, manifest):
        assert manifest["interface"]["category"] == "Documentation"

    def test_interface_capabilities(self, manifest):
        caps = manifest["interface"]["capabilities"]
        assert "Interactive" in caps
        assert "Write" in caps

    def test_default_prompt_is_nonempty_list(self, manifest):
        prompts = manifest["interface"]["defaultPrompt"]
        assert isinstance(prompts, list)
        assert len(prompts) >= 1
        assert all(isinstance(p, str) and p for p in prompts)


class TestCodexSkillDirs:
    """Verify .codex-plugin/skills/<name>/SKILL.md for every expected skill."""

    def test_all_skills_present(self):
        for name in EXPECTED_SKILLS:
            path = CODEX_SKILLS_DIR / name / "SKILL.md"
            assert path.exists(), f"missing Codex skill: {name}/SKILL.md"

    def test_no_unexpected_skill_dirs(self):
        expected = set(EXPECTED_SKILLS)
        actual = {p.name for p in CODEX_SKILLS_DIR.iterdir() if p.is_dir()}
        extra = actual - expected
        assert not extra, f"unexpected Codex skill dirs: {extra}"

    @pytest.mark.parametrize("skill", EXPECTED_SKILLS)
    def test_skill_has_frontmatter(self, skill):
        text = (CODEX_SKILLS_DIR / skill / "SKILL.md").read_text()
        assert text.startswith("---\n"), f"{skill}/SKILL.md missing frontmatter"

    @pytest.mark.parametrize("skill", EXPECTED_SKILLS)
    def test_skill_contains_name(self, skill):
        text = (CODEX_SKILLS_DIR / skill / "SKILL.md").read_text()
        assert f"name: {skill}" in text

    @pytest.mark.parametrize("skill", EXPECTED_SKILLS)
    def test_skill_matches_canonical_source(self, skill):
        generated = (CODEX_SKILLS_DIR / skill / "SKILL.md").read_bytes()
        canonical = (REPO_ROOT / "skills" / skill / "SKILL.md").read_bytes()
        assert generated == canonical, (
            f"Codex skill {skill}/SKILL.md differs from canonical SKILL.md"
        )


# ═══════════════════════════════════════════════════════════════════
# 3. Shared-script materialization into Codex output
# ═══════════════════════════════════════════════════════════════════


class TestCodexSharedScripts:
    """Verify shared scripts land in the right Codex subdirectories."""

    @pytest.mark.parametrize("skill", sorted(SKILLS_WITH_PACKAGING))
    def test_py_files_in_scripts_dir(self, skill):
        for entry in _shared_scripts_for(skill):
            if entry.endswith(".py"):
                path = CODEX_SKILLS_DIR / skill / "scripts" / entry
                assert path.exists(), f"missing Codex script: {skill}/scripts/{entry}"

    @pytest.mark.parametrize("skill", sorted(SKILLS_WITH_PACKAGING))
    def test_non_py_files_in_references_dir(self, skill):
        for entry in _shared_scripts_for(skill):
            if not entry.endswith(".py"):
                path = CODEX_SKILLS_DIR / skill / "references" / entry
                assert path.exists(), (
                    f"missing Codex reference: {skill}/references/{entry}"
                )

    @pytest.mark.parametrize("skill", sorted(SKILLS_WITH_PACKAGING))
    def test_content_matches_shared_source(self, skill):
        for entry in _shared_scripts_for(skill):
            source = (REPO_ROOT / "shared" / entry).read_bytes()
            if entry.endswith(".py"):
                dest = CODEX_SKILLS_DIR / skill / "scripts" / entry
            else:
                dest = CODEX_SKILLS_DIR / skill / "references" / entry
            assert dest.read_bytes() == source, (
                f"Codex {skill}/{entry} differs from shared/{entry}"
            )

    @pytest.mark.parametrize("skill", sorted(SKILLS_WITHOUT_PACKAGING))
    def test_skills_without_packaging_have_no_scripts_dir(self, skill):
        scripts_dir = CODEX_SKILLS_DIR / skill / "scripts"
        assert not scripts_dir.exists(), (
            f"{skill} should not have scripts/ (no packaging.json)"
        )

    @pytest.mark.parametrize("skill", sorted(SKILLS_WITHOUT_PACKAGING))
    def test_skills_without_packaging_have_no_references_dir(self, skill):
        refs_dir = CODEX_SKILLS_DIR / skill / "references"
        assert not refs_dir.exists(), (
            f"{skill} should not have references/ (no packaging.json)"
        )


# ═══════════════════════════════════════════════════════════════════
# 4. Executable permission preservation
# ═══════════════════════════════════════════════════════════════════


class TestExecutablePermissions:
    """Verify that executable source scripts remain executable in output."""

    @pytest.mark.parametrize("skill", sorted(SKILLS_WITH_PACKAGING))
    def test_codex_scripts_preserve_exec_bit(self, skill):
        for entry in _shared_scripts_for(skill):
            source = REPO_ROOT / "shared" / entry
            if not os.access(source, os.X_OK):
                continue
            if entry.endswith(".py"):
                dest = CODEX_SKILLS_DIR / skill / "scripts" / entry
            else:
                dest = CODEX_SKILLS_DIR / skill / "references" / entry
            assert os.access(dest, os.X_OK), (
                f"Codex {skill}/{entry} lost executable permission"
            )

    @pytest.mark.parametrize("skill", sorted(SKILLS_WITH_PACKAGING))
    def test_canonical_scripts_preserve_exec_bit(self, skill):
        """Verify materialized scripts in skills/<name>/scripts/ keep exec."""
        for entry in _shared_scripts_for(skill):
            source = REPO_ROOT / "shared" / entry
            if not os.access(source, os.X_OK):
                continue
            if entry.endswith(".py"):
                dest = REPO_ROOT / "skills" / skill / "scripts" / entry
            else:
                dest = REPO_ROOT / "skills" / skill / "references" / entry
            assert os.access(dest, os.X_OK), (
                f"canonical {skill}/{entry} lost executable permission"
            )

    @pytest.mark.parametrize("skill", sorted(SKILLS_WITH_PACKAGING))
    def test_non_executable_source_stays_non_executable(self, skill):
        """Files without exec in shared/ should not gain exec in output."""
        for entry in _shared_scripts_for(skill):
            source = REPO_ROOT / "shared" / entry
            if os.access(source, os.X_OK):
                continue
            if entry.endswith(".py"):
                dest = CODEX_SKILLS_DIR / skill / "scripts" / entry
            else:
                dest = CODEX_SKILLS_DIR / skill / "references" / entry
            assert not os.access(dest, os.X_OK), (
                f"Codex {skill}/{entry} gained unexpected executable permission"
            )


# ═══════════════════════════════════════════════════════════════════
# 5. Build-only file exclusion
# ═══════════════════════════════════════════════════════════════════


class TestBuildOnlyFileExclusion:
    """Verify packaging.json and other build-only files never leak."""

    def test_no_packaging_json_in_claude_plugin(self):
        hits = list(CLAUDE_PLUGIN_DIR.rglob("packaging.json"))
        assert not hits, f"packaging.json leaked into .claude-plugin/: {hits}"

    def test_no_packaging_json_in_codex_plugin(self):
        hits = list(CODEX_PLUGIN_DIR.rglob("packaging.json"))
        assert not hits, f"packaging.json leaked into .codex-plugin/: {hits}"

    def test_no_packaging_json_in_claude_skills(self):
        hits = list(CLAUDE_SKILLS_DIR.rglob("packaging.json"))
        assert not hits, f"packaging.json leaked into .claude/skills/: {hits}"

    @pytest.mark.parametrize("pattern", [
        "*.pyc", "__pycache__", ".DS_Store", "*.egg-info",
    ])
    def test_no_build_artifacts_in_codex_plugin(self, pattern):
        hits = list(CODEX_PLUGIN_DIR.rglob(pattern))
        assert not hits, f"{pattern} found in .codex-plugin/: {hits}"

    @pytest.mark.parametrize("pattern", [
        "*.pyc", "__pycache__", ".DS_Store", "*.egg-info",
    ])
    def test_no_build_artifacts_in_claude_plugin(self, pattern):
        hits = list(CLAUDE_PLUGIN_DIR.rglob(pattern))
        assert not hits, f"{pattern} found in .claude-plugin/: {hits}"


# ═══════════════════════════════════════════════════════════════════
# 6. Payload structure — no unexpected files
# ═══════════════════════════════════════════════════════════════════


class TestPayloadStructure:
    """Verify the generated trees contain only expected entries."""

    def test_claude_plugin_contains_only_manifest(self):
        """The .claude-plugin/ dir should contain only plugin.json."""
        entries = list(CLAUDE_PLUGIN_DIR.rglob("*"))
        files = [e for e in entries if e.is_file()]
        assert len(files) == 1
        assert files[0].name == "plugin.json"

    def test_codex_skill_dirs_contain_only_expected_subdirs(self):
        """Each Codex skill dir should contain only SKILL.md, scripts/, references/."""
        allowed_names = {"SKILL.md", "scripts", "references"}
        for skill in EXPECTED_SKILLS:
            skill_dir = CODEX_SKILLS_DIR / skill
            children = {p.name for p in skill_dir.iterdir()}
            extra = children - allowed_names
            assert not extra, (
                f"Codex skill {skill} has unexpected entries: {extra}"
            )

    def test_codex_plugin_top_level_structure(self):
        """Top level of .codex-plugin/ should be plugin.json + skills/."""
        top = {p.name for p in CODEX_PLUGIN_DIR.iterdir()}
        expected = {"plugin.json", "skills"}
        assert top == expected, f"unexpected top-level entries: {top - expected}"


# ═══════════════════════════════════════════════════════════════════
# 7. Cross-platform consistency
# ═══════════════════════════════════════════════════════════════════


class TestCrossPlatformConsistency:
    """Verify Claude and Codex payloads agree on shared properties."""

    @pytest.fixture()
    def claude_manifest(self) -> dict:
        return json.loads((CLAUDE_PLUGIN_DIR / "plugin.json").read_text())

    @pytest.fixture()
    def codex_manifest(self) -> dict:
        return json.loads((CODEX_PLUGIN_DIR / "plugin.json").read_text())

    def test_name_matches(self, claude_manifest, codex_manifest):
        assert claude_manifest["name"] == codex_manifest["name"]

    def test_version_matches(self, claude_manifest, codex_manifest):
        assert claude_manifest["version"] == codex_manifest["version"]

    def test_description_matches(self, claude_manifest, codex_manifest):
        assert claude_manifest["description"] == codex_manifest["description"]

    def test_author_name_matches(self, claude_manifest, codex_manifest):
        assert (
            claude_manifest["author"]["name"]
            == codex_manifest["author"]["name"]
        )

    @pytest.mark.parametrize("skill", EXPECTED_SKILLS)
    def test_claude_and_codex_skill_content_identical(self, skill):
        """The skill SKILL.md content must be identical across platforms."""
        claude = (CLAUDE_SKILLS_DIR / f"{skill}.md").read_bytes()
        codex = (CODEX_SKILLS_DIR / skill / "SKILL.md").read_bytes()
        assert claude == codex, (
            f"skill {skill} differs between Claude and Codex payloads"
        )


# ═══════════════════════════════════════════════════════════════════
# 5. Release readiness — no unimplemented-stub language in any skill
# ═══════════════════════════════════════════════════════════════════

# Phrases that mark a skill as a not-yet-implemented stub. A canonical
# SKILL.md carrying any of these tells a reading agent the capability
# does not exist and to stop — even when the backing script ships. Block
# publish until the SKILL.md documents real invocation instead.
FORBIDDEN_STUB_PHRASES = (
    "deferred",
    "not implemented",
    "not yet implemented",
    "implementation deferred",
    "todo: implement",
)


class TestNoStubLanguageInSkills:
    """Release gate: published SKILL.md files must not advertise themselves
    as unimplemented stubs."""

    @pytest.mark.parametrize("skill", EXPECTED_SKILLS)
    def test_canonical_skill_has_no_stub_phrase(self, skill):
        text = (REPO_ROOT / "skills" / skill / "SKILL.md").read_text().lower()
        hits = [p for p in FORBIDDEN_STUB_PHRASES if p in text]
        assert not hits, (
            f"skills/{skill}/SKILL.md contains stub language {hits}; "
            "rewrite with real invocation instructions before publishing"
        )

    def test_all_canonical_skills_scanned(self):
        """Guard against the scan silently covering nothing if the skills
        directory layout changes."""
        scanned = [
            s for s in EXPECTED_SKILLS
            if (REPO_ROOT / "skills" / s / "SKILL.md").exists()
        ]
        assert scanned == EXPECTED_SKILLS, (
            f"missing canonical SKILL.md for: "
            f"{set(EXPECTED_SKILLS) - set(scanned)}"
        )
