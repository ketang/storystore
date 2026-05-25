"""Tests for shared/edit_section.py — guarded story section editing."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "shared" / "edit_section.py"


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


STORY_TEMPLATE = """\
---
title: {title}
slug: {slug}
status: {status}
authority: {authority}
change_resistance: {resistance}
{extra_fm}---

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
{claims}

## Evidence
### Tests
{tests}
### Surface
{surface}
### Docs
{docs}
"""


def _write_story(
    repo: Path,
    slug: str,
    *,
    title: str | None = None,
    status: str = "active",
    authority: str = "accepted",
    resistance: str = "medium",
    intent: str = "Users can use this.",
    story: str = "User narrative.",
    expected: str = "Visible behavior.",
    boundaries: str = "Out of scope.",
    claims: list[str] | None = None,
    tests: list[str] | None = None,
    surface: list[str] | None = None,
    docs: list[str] | None = None,
    extra_fm: str = "",
) -> Path:
    stories_dir = repo / "docs" / "stories"
    stories_dir.mkdir(parents=True, exist_ok=True)
    title = title or slug.replace("-", " ").title()
    claims_text = "\n".join(f"- {c}" for c in (claims or ["The feature exists."]))
    tests_text = "\n".join(f"- `{t}`" for t in (tests or []))
    surface_text = "\n".join(f"- `{s}`" for s in (surface or []))
    docs_text = "\n".join(f"- `{d}`" for d in (docs or []))
    path = stories_dir / f"{slug}.md"
    path.write_text(
        STORY_TEMPLATE.format(
            title=title,
            slug=slug,
            status=status,
            authority=authority,
            resistance=resistance,
            intent=intent,
            story=story,
            expected=expected,
            boundaries=boundaries,
            claims=claims_text,
            tests=tests_text,
            surface=surface_text,
            docs=docs_text,
            extra_fm=extra_fm,
        ),
        encoding="utf-8",
    )
    return path


def _run(repo: Path, slug: str, section: str, content: str, *extra_args: str) -> subprocess.CompletedProcess:
    cmd = [
        sys.executable, str(SCRIPT),
        "--repo-root", str(repo),
        "--story", slug,
        "--section", section,
        "--content", content,
        *extra_args,
    ]
    return subprocess.run(cmd, capture_output=True, text=True, check=False)


def _read_story(repo: Path, slug: str) -> str:
    return (repo / "docs" / "stories" / f"{slug}.md").read_text(encoding="utf-8")


# --------------------------------------------------------------------------- #
# Allowed edits
# --------------------------------------------------------------------------- #


class TestAllowedEdits:
    def test_edit_unlocked_section(self, tmp_path):
        repo = tmp_path
        _write_story(repo, "test-story")
        result = _run(repo, "test-story", "Story", "Updated narrative.")
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert data["edited"] is True
        assert data["section"] == "Story"
        assert data["index_updated"] is False
        content = _read_story(repo, "test-story")
        assert "Updated narrative." in content

    def test_edit_expected_behavior(self, tmp_path):
        repo = tmp_path
        _write_story(repo, "test-story")
        result = _run(repo, "test-story", "Expected Behavior", "New behavior description.")
        assert result.returncode == 0
        content = _read_story(repo, "test-story")
        assert "New behavior description." in content

    def test_edit_boundaries(self, tmp_path):
        repo = tmp_path
        _write_story(repo, "test-story")
        result = _run(repo, "test-story", "Boundaries", "Updated boundary.")
        assert result.returncode == 0
        content = _read_story(repo, "test-story")
        assert "Updated boundary." in content

    def test_edit_drift_notes_when_present(self, tmp_path):
        repo = tmp_path
        path = _write_story(repo, "test-story")
        original = path.read_text(encoding="utf-8")
        original += "\n## Drift Notes\nSome drift.\n"
        path.write_text(original, encoding="utf-8")
        result = _run(repo, "test-story", "Drift Notes", "Updated drift.")
        assert result.returncode == 0
        content = _read_story(repo, "test-story")
        assert "Updated drift." in content

    def test_add_claims_allowed(self, tmp_path):
        repo = tmp_path
        _write_story(repo, "test-story", claims=["Claim one."])
        new_claims = "- Claim one.\n- Claim two.\n- Claim three."
        result = _run(repo, "test-story", "Auditable Claims", new_claims)
        assert result.returncode == 0
        content = _read_story(repo, "test-story")
        assert "Claim three." in content

    def test_claim_reduction_with_flag(self, tmp_path):
        repo = tmp_path
        _write_story(repo, "test-story", claims=["C1.", "C2.", "C3."])
        result = _run(repo, "test-story", "Auditable Claims", "- C1.", "--allow-claim-reduction")
        assert result.returncode == 0
        content = _read_story(repo, "test-story")
        assert "C1." in content

    def test_edit_low_resistance_story(self, tmp_path):
        repo = tmp_path
        _write_story(repo, "test-story", resistance="low")
        result = _run(repo, "test-story", "Story", "New story.")
        assert result.returncode == 0


# --------------------------------------------------------------------------- #
# Policy refusals — locked sections (exit 3)
# --------------------------------------------------------------------------- #


class TestLockedSectionRefusal:
    def test_locked_section_refuses_edit(self, tmp_path):
        repo = tmp_path
        _write_story(
            repo, "guarded-story",
            extra_fm="locked_sections: [Intent]\n",
        )
        result = _run(repo, "guarded-story", "Intent", "New intent.")
        assert result.returncode == 3
        assert "locked" in result.stderr.lower()

    def test_locked_section_multiple(self, tmp_path):
        repo = tmp_path
        _write_story(
            repo, "guarded-story",
            extra_fm="locked_sections:\n  - Intent\n  - Boundaries\n",
        )
        result = _run(repo, "guarded-story", "Boundaries", "New boundaries.")
        assert result.returncode == 3

    def test_unlocked_section_on_story_with_locks(self, tmp_path):
        """Editing a non-locked section on a story that has locked sections should succeed."""
        repo = tmp_path
        _write_story(
            repo, "guarded-story",
            extra_fm="locked_sections: [Intent]\n",
        )
        result = _run(repo, "guarded-story", "Story", "Updated narrative.")
        assert result.returncode == 0


# --------------------------------------------------------------------------- #
# Policy refusals — inline locked blocks (exit 3)
# --------------------------------------------------------------------------- #


class TestInlineLockedBlockRefusal:
    def test_inline_locked_block_refuses_edit(self, tmp_path):
        repo = tmp_path
        _write_story(
            repo, "inline-locked",
            story="Before.\n<!-- lock:begin -->\nProtected text.\n<!-- lock:end -->\nAfter.",
        )
        result = _run(repo, "inline-locked", "Story", "Completely new story.")
        assert result.returncode == 3
        assert "inline locked block" in result.stderr.lower()

    def test_section_without_inline_lock_allowed(self, tmp_path):
        repo = tmp_path
        _write_story(
            repo, "inline-locked",
            story="Before.\n<!-- lock:begin -->\nProtected text.\n<!-- lock:end -->\nAfter.",
        )
        # Boundaries section has no inline lock, so editing it should work.
        result = _run(repo, "inline-locked", "Boundaries", "New boundaries.")
        assert result.returncode == 0


# --------------------------------------------------------------------------- #
# Policy refusals — claim count reduction (exit 3)
# --------------------------------------------------------------------------- #


class TestClaimCountReduction:
    def test_claim_reduction_without_flag_refuses(self, tmp_path):
        repo = tmp_path
        _write_story(repo, "claims-story", claims=["C1.", "C2.", "C3."])
        result = _run(repo, "claims-story", "Auditable Claims", "- C1.")
        assert result.returncode == 3
        assert "claim" in result.stderr.lower()
        assert "allow-claim-reduction" in result.stderr

    def test_same_count_allowed(self, tmp_path):
        repo = tmp_path
        _write_story(repo, "claims-story", claims=["C1.", "C2."])
        result = _run(repo, "claims-story", "Auditable Claims", "- X1.\n- X2.")
        assert result.returncode == 0

    def test_increase_count_allowed(self, tmp_path):
        repo = tmp_path
        _write_story(repo, "claims-story", claims=["C1."])
        result = _run(repo, "claims-story", "Auditable Claims", "- C1.\n- C2.\n- C3.")
        assert result.returncode == 0


# --------------------------------------------------------------------------- #
# Policy refusals — immutable (exit 3)
# --------------------------------------------------------------------------- #


class TestImmutableRefusal:
    def test_immutable_refuses_body_edit(self, tmp_path):
        repo = tmp_path
        _write_story(repo, "immutable-story", resistance="immutable", authority="accepted")
        result = _run(repo, "immutable-story", "Story", "New narrative.")
        assert result.returncode == 3
        assert "immutable" in result.stderr.lower()

    def test_immutable_refuses_metadata_edit(self, tmp_path):
        repo = tmp_path
        _write_story(repo, "immutable-story", resistance="immutable", authority="accepted")
        result = _run(repo, "immutable-story", "title", "New Title")
        assert result.returncode == 3
        assert "immutable" in result.stderr.lower()

    def test_immutable_refuses_intent_edit(self, tmp_path):
        repo = tmp_path
        _write_story(repo, "immutable-story", resistance="immutable", authority="accepted")
        result = _run(repo, "immutable-story", "Intent", "New intent.")
        assert result.returncode == 3


# --------------------------------------------------------------------------- #
# Policy refusals — resistance change confirmation (exit 4)
# --------------------------------------------------------------------------- #


class TestResistanceChangeConfirmation:
    def test_increase_resistance_without_flag_exits_4(self, tmp_path):
        repo = tmp_path
        _write_story(repo, "test-story", resistance="low")
        result = _run(repo, "test-story", "change_resistance", "high")
        assert result.returncode == 4
        assert "confirm-resistance-change" in result.stderr

    def test_increase_resistance_with_flag_allowed(self, tmp_path):
        repo = tmp_path
        _write_story(repo, "test-story", resistance="low")
        result = _run(repo, "test-story", "change_resistance", "high", "--confirm-resistance-change")
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert data["edited"] is True
        content = _read_story(repo, "test-story")
        assert "change_resistance: high" in content

    def test_decrease_resistance_allowed_without_flag(self, tmp_path):
        repo = tmp_path
        _write_story(repo, "test-story", resistance="high")
        result = _run(repo, "test-story", "change_resistance", "low")
        assert result.returncode == 0

    def test_invalid_resistance_value_exits_2(self, tmp_path):
        repo = tmp_path
        _write_story(repo, "test-story", resistance="medium")
        result = _run(repo, "test-story", "change_resistance", "ultra")
        assert result.returncode == 2


# --------------------------------------------------------------------------- #
# Metadata edits
# --------------------------------------------------------------------------- #


class TestMetadataEdits:
    def test_edit_title_regenerates_index(self, tmp_path):
        repo = tmp_path
        _write_story(repo, "test-story")
        result = _run(repo, "test-story", "title", "Brand New Title")
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert data["index_updated"] is True
        index_path = repo / "docs" / "stories" / "INDEX.md"
        assert index_path.exists()
        index_text = index_path.read_text(encoding="utf-8")
        assert "Brand New Title" in index_text

    def test_edit_status(self, tmp_path):
        repo = tmp_path
        _write_story(repo, "test-story", status="draft")
        result = _run(repo, "test-story", "status", "active")
        assert result.returncode == 0
        content = _read_story(repo, "test-story")
        assert "status: active" in content

    def test_edit_authority(self, tmp_path):
        repo = tmp_path
        _write_story(repo, "test-story", authority="observed", resistance="low")
        result = _run(repo, "test-story", "authority", "accepted")
        assert result.returncode == 0
        content = _read_story(repo, "test-story")
        assert "authority: accepted" in content

    def test_invalid_status_exits_2(self, tmp_path):
        repo = tmp_path
        _write_story(repo, "test-story")
        result = _run(repo, "test-story", "status", "superseded")
        assert result.returncode != 0

    def test_metadata_edit_does_not_bump_last_audited(self, tmp_path):
        repo = tmp_path
        _write_story(
            repo, "test-story",
            extra_fm="last_audited: 2025-01-01\n",
        )
        result = _run(repo, "test-story", "title", "New Title")
        assert result.returncode == 0
        content = _read_story(repo, "test-story")
        assert "last_audited: 2025-01-01" in content


# --------------------------------------------------------------------------- #
# Body edits do not bump last_audited
# --------------------------------------------------------------------------- #


class TestLastAuditedPreserved:
    def test_body_edit_preserves_last_audited(self, tmp_path):
        repo = tmp_path
        _write_story(
            repo, "test-story",
            extra_fm="last_audited: 2025-06-15\n",
        )
        result = _run(repo, "test-story", "Story", "Updated narrative.")
        assert result.returncode == 0
        content = _read_story(repo, "test-story")
        # last_audited should remain unchanged
        assert "last_audited: 2025-06-15" in content


# --------------------------------------------------------------------------- #
# Error handling
# --------------------------------------------------------------------------- #


class TestErrorHandling:
    def test_missing_story_exits_2(self, tmp_path):
        repo = tmp_path
        (repo / "docs" / "stories").mkdir(parents=True)
        result = _run(repo, "nonexistent", "Story", "x")
        assert result.returncode == 2

    def test_missing_section_exits_2(self, tmp_path):
        repo = tmp_path
        _write_story(repo, "test-story")
        result = _run(repo, "test-story", "Nonexistent Section", "x")
        assert result.returncode == 2

    def test_missing_repo_root_exits_2(self, tmp_path):
        result = _run(tmp_path / "nope", "anything", "Story", "x")
        assert result.returncode == 2

    def test_malformed_story_exits_2(self, tmp_path):
        repo = tmp_path
        stories_dir = repo / "docs" / "stories"
        stories_dir.mkdir(parents=True)
        (stories_dir / "bad.md").write_text("no frontmatter", encoding="utf-8")
        result = _run(repo, "bad", "Story", "x")
        assert result.returncode == 2


# --------------------------------------------------------------------------- #
# INDEX.md regeneration
# --------------------------------------------------------------------------- #


class TestIndexRegeneration:
    def test_body_edit_does_not_regenerate_index(self, tmp_path):
        repo = tmp_path
        _write_story(repo, "test-story")
        index_path = repo / "docs" / "stories" / "INDEX.md"
        assert not index_path.exists()
        result = _run(repo, "test-story", "Story", "Updated.")
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert data["index_updated"] is False
        # INDEX.md should not have been created by a body edit
        assert not index_path.exists()

    def test_metadata_edit_regenerates_index(self, tmp_path):
        repo = tmp_path
        _write_story(repo, "story-one", title="Story One")
        _write_story(repo, "story-two", title="Story Two")
        result = _run(repo, "story-one", "status", "deprecated")
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert data["index_updated"] is True
        index_path = repo / "docs" / "stories" / "INDEX.md"
        assert index_path.exists()
        index_text = index_path.read_text(encoding="utf-8")
        assert "story-one" in index_text
        assert "story-two" in index_text
        assert "deprecated" in index_text
