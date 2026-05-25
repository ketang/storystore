"""Systematic mutation tests for policy gates in edit_section.py.

Generates controlled mutations across frontmatter fields, locked sections,
inline locked blocks, auditable claims, evidence subsections, and metadata,
then asserts edit_section.py allows or refuses each mutation according to
the story's change-resistance level.

Complements (does not duplicate) the existing unit tests in
test_storystore_edit_section.py by covering the full mutation space
systematically rather than testing individual API paths.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "shared" / "edit_section.py"

# All body sections that exist in the template
BODY_SECTIONS = ("Intent", "Story", "Expected Behavior", "Boundaries",
                 "Auditable Claims", "Evidence")

METADATA_FIELDS = ("title", "status", "authority", "change_resistance")

RESISTANCE_LEVELS = ("low", "medium", "high", "immutable")

# Valid values for metadata fields (used in mutation payloads)
VALID_STATUS_VALUES = ("draft", "active", "deprecated")
VALID_AUTHORITY_VALUES = ("observed", "accepted")


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


def _run(
    repo: Path, slug: str, section: str, content: str, *extra_args: str,
) -> subprocess.CompletedProcess:
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
# Mutation: body section edits across resistance levels
# --------------------------------------------------------------------------- #


class TestBodySectionMutationsByResistance:
    """Edit every body section at each non-immutable resistance level.

    All unlocked body sections on non-immutable stories should be editable
    regardless of resistance level.
    """

    @pytest.mark.parametrize("resistance", ["low", "medium", "high"])
    @pytest.mark.parametrize("section", ["Intent", "Story", "Expected Behavior", "Boundaries"])
    def test_unlocked_body_section_allowed(self, tmp_path, resistance, section):
        repo = tmp_path
        _write_story(repo, "mut-story", resistance=resistance)
        result = _run(repo, "mut-story", section, f"Mutated {section} content.")
        assert result.returncode == 0, (
            f"Expected allowed for {section} at resistance={resistance}, "
            f"got rc={result.returncode}: {result.stderr}"
        )
        content = _read_story(repo, "mut-story")
        assert f"Mutated {section} content." in content


class TestImmutableRefusesAllMutations:
    """Immutable stories must refuse every mutation -- body and metadata."""

    @pytest.mark.parametrize("section", list(BODY_SECTIONS))
    def test_immutable_refuses_body_section(self, tmp_path, section):
        repo = tmp_path
        _write_story(repo, "frozen", resistance="immutable", authority="accepted")
        result = _run(repo, "frozen", section, "Attempted change.")
        assert result.returncode == 3, (
            f"Expected refusal (exit 3) for {section} on immutable story, "
            f"got rc={result.returncode}"
        )
        assert "immutable" in result.stderr.lower()

    @pytest.mark.parametrize("field", list(METADATA_FIELDS))
    def test_immutable_refuses_metadata_field(self, tmp_path, field):
        repo = tmp_path
        _write_story(repo, "frozen", resistance="immutable", authority="accepted")
        # Pick a valid new value for the field
        values = {
            "title": "New Title",
            "status": "deprecated",
            "authority": "observed",
            "change_resistance": "low",
        }
        result = _run(repo, "frozen", field, values[field])
        assert result.returncode == 3, (
            f"Expected refusal (exit 3) for metadata field {field!r} on immutable story, "
            f"got rc={result.returncode}"
        )
        assert "immutable" in result.stderr.lower()


# --------------------------------------------------------------------------- #
# Mutation: locked section permutations
# --------------------------------------------------------------------------- #


class TestLockedSectionMutations:
    """Systematically lock individual sections and verify refusal/allowance."""

    @pytest.mark.parametrize("locked_section", ["Intent", "Story", "Expected Behavior", "Boundaries"])
    def test_editing_locked_section_refused(self, tmp_path, locked_section):
        repo = tmp_path
        _write_story(
            repo, "lock-test",
            extra_fm=f"locked_sections: [{locked_section}]\n",
        )
        result = _run(repo, "lock-test", locked_section, "Attempted change.")
        assert result.returncode == 3
        assert "locked" in result.stderr.lower()

    @pytest.mark.parametrize("locked_section", ["Intent", "Story", "Expected Behavior", "Boundaries"])
    def test_editing_other_section_when_one_locked(self, tmp_path, locked_section):
        """Non-locked sections remain editable even when one section is locked."""
        repo = tmp_path
        _write_story(
            repo, "lock-test",
            extra_fm=f"locked_sections: [{locked_section}]\n",
        )
        # Pick a different section to edit
        other = "Story" if locked_section != "Story" else "Boundaries"
        result = _run(repo, "lock-test", other, "Allowed change.")
        assert result.returncode == 0, (
            f"Expected allowed for {other} when {locked_section} is locked, "
            f"got rc={result.returncode}: {result.stderr}"
        )

    def test_multiple_locked_sections_all_refused(self, tmp_path):
        """When multiple sections are locked, each one is individually refused."""
        repo = tmp_path
        locked = ["Intent", "Boundaries", "Expected Behavior"]
        fm = "locked_sections:\n" + "".join(f"  - {s}\n" for s in locked)
        _write_story(repo, "multi-lock", extra_fm=fm)
        for section in locked:
            result = _run(repo, "multi-lock", section, "Attempt.")
            assert result.returncode == 3, (
                f"Expected refusal for locked section {section!r}"
            )

    def test_metadata_editable_despite_body_locks(self, tmp_path):
        """Locked body sections do not block metadata edits."""
        repo = tmp_path
        _write_story(
            repo, "lock-meta",
            extra_fm="locked_sections: [Intent, Story]\n",
        )
        result = _run(repo, "lock-meta", "title", "New Title")
        assert result.returncode == 0


# --------------------------------------------------------------------------- #
# Mutation: inline locked blocks
# --------------------------------------------------------------------------- #


class TestInlineLockedBlockMutations:
    """Inline lock markers within a section body prevent editing that section."""

    @pytest.mark.parametrize("section,field", [
        ("Story", "story"),
        ("Expected Behavior", "expected"),
        ("Boundaries", "boundaries"),
    ])
    def test_inline_lock_refuses_section_edit(self, tmp_path, section, field):
        repo = tmp_path
        kwargs = {
            field: "Before.\n<!-- lock:begin -->\nProtected.\n<!-- lock:end -->\nAfter."
        }
        _write_story(repo, "inline-lock", **kwargs)
        result = _run(repo, "inline-lock", section, "Replacement content.")
        assert result.returncode == 3
        assert "inline locked block" in result.stderr.lower()

    def test_inline_lock_in_one_section_allows_other_sections(self, tmp_path):
        """Inline lock in Story does not block editing Boundaries."""
        repo = tmp_path
        _write_story(
            repo, "inline-partial",
            story="Text.\n<!-- lock:begin -->\nLocked.\n<!-- lock:end -->\nMore.",
        )
        result = _run(repo, "inline-partial", "Boundaries", "New boundaries.")
        assert result.returncode == 0

    def test_multiple_inline_locks_still_refused(self, tmp_path):
        """Section with multiple inline lock blocks is still refused."""
        repo = tmp_path
        _write_story(
            repo, "multi-inline",
            story=(
                "A.\n<!-- lock:begin -->\nB.\n<!-- lock:end -->\n"
                "C.\n<!-- lock:begin -->\nD.\n<!-- lock:end -->\nE."
            ),
        )
        result = _run(repo, "multi-inline", "Story", "Replacement.")
        assert result.returncode == 3


# --------------------------------------------------------------------------- #
# Mutation: auditable claims
# --------------------------------------------------------------------------- #


class TestAuditableClaimsMutations:
    """Systematic claim-count mutations: increase, same, decrease."""

    def test_adding_claims_allowed(self, tmp_path):
        repo = tmp_path
        _write_story(repo, "claims-mut", claims=["C1.", "C2."])
        result = _run(repo, "claims-mut", "Auditable Claims", "- C1.\n- C2.\n- C3.")
        assert result.returncode == 0

    def test_same_count_different_text_allowed(self, tmp_path):
        repo = tmp_path
        _write_story(repo, "claims-mut", claims=["C1.", "C2."])
        result = _run(repo, "claims-mut", "Auditable Claims", "- X1.\n- X2.")
        assert result.returncode == 0

    def test_reducing_claims_refused_without_flag(self, tmp_path):
        repo = tmp_path
        _write_story(repo, "claims-mut", claims=["C1.", "C2.", "C3."])
        result = _run(repo, "claims-mut", "Auditable Claims", "- C1.")
        assert result.returncode == 3
        assert "claim" in result.stderr.lower()

    def test_reducing_claims_allowed_with_flag(self, tmp_path):
        repo = tmp_path
        _write_story(repo, "claims-mut", claims=["C1.", "C2.", "C3."])
        result = _run(
            repo, "claims-mut", "Auditable Claims", "- C1.",
            "--allow-claim-reduction",
        )
        assert result.returncode == 0

    def test_reducing_to_zero_refused_without_flag(self, tmp_path):
        repo = tmp_path
        _write_story(repo, "claims-mut", claims=["C1."])
        result = _run(repo, "claims-mut", "Auditable Claims", "No bullets here.")
        assert result.returncode == 3

    def test_reducing_to_zero_allowed_with_flag(self, tmp_path):
        repo = tmp_path
        _write_story(repo, "claims-mut", claims=["C1."])
        result = _run(
            repo, "claims-mut", "Auditable Claims", "No bullets here.",
            "--allow-claim-reduction",
        )
        assert result.returncode == 0

    @pytest.mark.parametrize("resistance", ["low", "medium", "high"])
    def test_claim_reduction_refused_across_resistance_levels(self, tmp_path, resistance):
        """Claim-count reduction gate applies at all non-immutable resistance levels."""
        repo = tmp_path
        _write_story(repo, "claims-res", resistance=resistance, claims=["C1.", "C2."])
        result = _run(repo, "claims-res", "Auditable Claims", "- C1.")
        assert result.returncode == 3


# --------------------------------------------------------------------------- #
# Mutation: evidence subsections
# --------------------------------------------------------------------------- #


class TestEvidenceSubsectionMutations:
    """Evidence section edits should follow the same gates as other body sections."""

    def test_evidence_editable_when_unlocked(self, tmp_path):
        repo = tmp_path
        _write_story(repo, "ev-mut")
        result = _run(repo, "ev-mut", "Evidence", "### Tests\n- `test_new.py`")
        assert result.returncode == 0
        content = _read_story(repo, "ev-mut")
        assert "test_new.py" in content

    def test_evidence_refused_when_locked(self, tmp_path):
        repo = tmp_path
        _write_story(
            repo, "ev-lock",
            extra_fm="locked_sections: [Evidence]\n",
        )
        result = _run(repo, "ev-lock", "Evidence", "### Tests\n- `hack.py`")
        assert result.returncode == 3

    def test_evidence_refused_on_immutable(self, tmp_path):
        repo = tmp_path
        _write_story(repo, "ev-imm", resistance="immutable", authority="accepted")
        result = _run(repo, "ev-imm", "Evidence", "### Tests\n- `hack.py`")
        assert result.returncode == 3

    def test_evidence_with_inline_lock_refused(self, tmp_path):
        """Evidence section containing inline lock markers is not editable."""
        repo = tmp_path
        path = _write_story(repo, "ev-inline")
        text = path.read_text(encoding="utf-8")
        # Inject inline lock into Evidence section
        text = text.replace(
            "### Tests\n",
            "### Tests\n<!-- lock:begin -->\nProtected evidence.\n<!-- lock:end -->\n",
        )
        path.write_text(text, encoding="utf-8")
        result = _run(repo, "ev-inline", "Evidence", "Replacement.")
        assert result.returncode == 3


# --------------------------------------------------------------------------- #
# Mutation: metadata field edits across resistance levels
# --------------------------------------------------------------------------- #


class TestMetadataFieldMutations:
    """Metadata field edits at each resistance level."""

    @pytest.mark.parametrize("resistance", ["low", "medium", "high"])
    def test_title_editable_at_non_immutable(self, tmp_path, resistance):
        repo = tmp_path
        _write_story(repo, "meta-mut", resistance=resistance)
        result = _run(repo, "meta-mut", "title", "Updated Title")
        assert result.returncode == 0
        content = _read_story(repo, "meta-mut")
        assert "title: Updated Title" in content

    @pytest.mark.parametrize("resistance", ["low", "medium", "high"])
    def test_status_editable_at_non_immutable(self, tmp_path, resistance):
        repo = tmp_path
        _write_story(repo, "meta-mut", resistance=resistance, status="draft")
        result = _run(repo, "meta-mut", "status", "active")
        assert result.returncode == 0

    @pytest.mark.parametrize("resistance", ["low", "medium"])
    def test_authority_editable_observed_to_accepted(self, tmp_path, resistance):
        """Authority observed->accepted allowed at low/medium resistance."""
        repo = tmp_path
        _write_story(repo, "meta-mut", resistance=resistance, authority="observed")
        result = _run(repo, "meta-mut", "authority", "accepted")
        assert result.returncode == 0

    def test_authority_editable_at_high_resistance(self, tmp_path):
        """Authority accepted->accepted (no-op) allowed at high resistance."""
        repo = tmp_path
        _write_story(repo, "meta-mut", resistance="high", authority="accepted")
        result = _run(repo, "meta-mut", "authority", "accepted")
        assert result.returncode == 0










# --------------------------------------------------------------------------- #
# Mutation: resistance-change matrix
# --------------------------------------------------------------------------- #


class TestResistanceChangeMutationMatrix:
    """Full matrix of resistance transitions: allowed, refused, or confirmation-needed."""

    RANK = {"low": 0, "medium": 1, "high": 2, "immutable": 3}

    @pytest.mark.parametrize("from_level,to_level", [
        ("low", "medium"),
        ("low", "high"),
        ("low", "immutable"),
        ("medium", "high"),
        ("medium", "immutable"),
        ("high", "immutable"),
    ])
    def test_resistance_increase_requires_confirmation(self, tmp_path, from_level, to_level):
        repo = tmp_path
        _write_story(repo, "res-up", resistance=from_level)
        result = _run(repo, "res-up", "change_resistance", to_level)
        assert result.returncode == 4, (
            f"Expected exit 4 for {from_level}->{to_level}, got {result.returncode}"
        )
        assert "confirm-resistance-change" in result.stderr

    @pytest.mark.parametrize("from_level,to_level", [
        ("low", "medium"),
        ("low", "high"),
        ("low", "immutable"),
        ("medium", "high"),
        ("medium", "immutable"),
        ("high", "immutable"),
    ])
    def test_resistance_increase_allowed_with_flag(self, tmp_path, from_level, to_level):
        repo = tmp_path
        _write_story(repo, "res-up", resistance=from_level)
        result = _run(
            repo, "res-up", "change_resistance", to_level,
            "--confirm-resistance-change",
        )
        assert result.returncode == 0, (
            f"Expected allowed for {from_level}->{to_level} with flag, "
            f"got {result.returncode}: {result.stderr}"
        )
        content = _read_story(repo, "res-up")
        assert f"change_resistance: {to_level}" in content

    @pytest.mark.parametrize("from_level,to_level", [
        ("medium", "low"),
        ("high", "low"),
        ("high", "medium"),
    ])
    def test_resistance_decrease_allowed_without_flag(self, tmp_path, from_level, to_level):
        repo = tmp_path
        _write_story(repo, "res-down", resistance=from_level)
        result = _run(repo, "res-down", "change_resistance", to_level)
        assert result.returncode == 0, (
            f"Expected allowed for {from_level}->{to_level}, got {result.returncode}"
        )

    @pytest.mark.parametrize("level", ["low", "medium", "high"])
    def test_resistance_same_level_allowed(self, tmp_path, level):
        repo = tmp_path
        _write_story(repo, "res-same", resistance=level)
        result = _run(repo, "res-same", "change_resistance", level)
        assert result.returncode == 0


# --------------------------------------------------------------------------- #
# Mutation: combined gates -- locked + resistance interactions
# --------------------------------------------------------------------------- #


class TestCombinedGateInteractions:
    """Verify that multiple gates compose correctly."""

    def test_locked_section_on_high_resistance_still_refuses(self, tmp_path):
        """Locked-section gate fires before resistance checks."""
        repo = tmp_path
        _write_story(
            repo, "combined",
            resistance="high",
            extra_fm="locked_sections: [Intent]\n",
        )
        result = _run(repo, "combined", "Intent", "Change.")
        assert result.returncode == 3

    def test_inline_lock_on_low_resistance_still_refuses(self, tmp_path):
        """Inline lock fires even at low resistance."""
        repo = tmp_path
        _write_story(
            repo, "combined",
            resistance="low",
            story="X.\n<!-- lock:begin -->\nY.\n<!-- lock:end -->\nZ.",
        )
        result = _run(repo, "combined", "Story", "Change.")
        assert result.returncode == 3

    def test_claim_reduction_on_high_resistance(self, tmp_path):
        """Claim-count gate fires at high resistance too."""
        repo = tmp_path
        _write_story(repo, "combined", resistance="high", claims=["A.", "B."])
        result = _run(repo, "combined", "Auditable Claims", "- A.")
        assert result.returncode == 3

    def test_unlocked_section_on_low_resistance_allowed(self, tmp_path):
        """Simplest allow case: low resistance, no locks."""
        repo = tmp_path
        _write_story(repo, "combined", resistance="low")
        result = _run(repo, "combined", "Story", "New story.")
        assert result.returncode == 0

    def test_immutable_overrides_everything(self, tmp_path):
        """Immutable gate fires before locked-section or inline checks."""
        repo = tmp_path
        _write_story(
            repo, "combined",
            resistance="immutable",
            authority="accepted",
            extra_fm="locked_sections: [Intent]\n",
        )
        # Even editing an unlocked section on an immutable story is refused
        result = _run(repo, "combined", "Story", "Change.")
        assert result.returncode == 3
        assert "immutable" in result.stderr.lower()


# --------------------------------------------------------------------------- #
# Regression: content integrity after allowed mutations
# --------------------------------------------------------------------------- #


class TestContentIntegrityAfterMutation:
    """After allowed edits, verify the file is still parseable and content is correct."""

    def test_sequential_body_edits_preserve_structure(self, tmp_path):
        """Multiple sequential edits to different sections all land correctly."""
        repo = tmp_path
        _write_story(repo, "seq-edit", resistance="low")

        edits = [
            ("Intent", "Updated intent."),
            ("Story", "Updated story."),
            ("Boundaries", "Updated boundaries."),
        ]
        for section, content in edits:
            result = _run(repo, "seq-edit", section, content)
            assert result.returncode == 0, (
                f"Edit to {section} failed: {result.stderr}"
            )

        final = _read_story(repo, "seq-edit")
        for _, content in edits:
            assert content in final

    def test_metadata_edit_preserves_body(self, tmp_path):
        """Metadata edit does not corrupt body sections."""
        repo = tmp_path
        _write_story(
            repo, "meta-body",
            story="Original narrative.",
            claims=["Important claim."],
        )
        result = _run(repo, "meta-body", "title", "New Title")
        assert result.returncode == 0
        content = _read_story(repo, "meta-body")
        assert "Original narrative." in content
        assert "Important claim." in content
        assert "title: New Title" in content

    def test_body_edit_preserves_frontmatter(self, tmp_path):
        """Body edit does not corrupt frontmatter fields."""
        repo = tmp_path
        _write_story(
            repo, "body-fm",
            resistance="high",
            status="active",
            authority="accepted",
        )
        result = _run(repo, "body-fm", "Story", "New story content.")
        assert result.returncode == 0
        content = _read_story(repo, "body-fm")
        assert "change_resistance: high" in content
        assert "status: active" in content
        assert "authority: accepted" in content

    def test_claim_addition_preserves_existing_claims(self, tmp_path):
        """Adding claims does not lose existing ones."""
        repo = tmp_path
        _write_story(repo, "claim-add", claims=["C1.", "C2."])
        new_claims = "- C1.\n- C2.\n- C3.\n- C4."
        result = _run(repo, "claim-add", "Auditable Claims", new_claims)
        assert result.returncode == 0
        content = _read_story(repo, "claim-add")
        for c in ["C1.", "C2.", "C3.", "C4."]:
            assert c in content
