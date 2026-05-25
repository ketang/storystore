"""Tests for shared/lock_check.py — lock-state report for a single story."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "shared" / "lock_check.py"


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
    extra_fm: str = "",
    body_extra: str = "",
) -> Path:
    stories_dir = repo / "docs" / "stories"
    stories_dir.mkdir(parents=True, exist_ok=True)
    title = title or slug.replace("-", " ").title()
    claims_text = "\n".join(f"- {c}" for c in (claims or ["The feature exists."]))
    tests_text = "\n".join(f"- `{t}`" for t in (tests or []))
    path = stories_dir / f"{slug}.md"
    content = STORY_TEMPLATE.format(
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
        extra_fm=extra_fm,
    )
    if body_extra:
        content += "\n" + body_extra + "\n"
    path.write_text(content, encoding="utf-8")
    return path


def _run(repo: Path, slug: str, *args: str) -> subprocess.CompletedProcess:
    cmd = [sys.executable, str(SCRIPT), "--repo-root", str(repo), "--slug", slug, *args]
    return subprocess.run(cmd, capture_output=True, text=True, check=False)


# --------------------------------------------------------------------------- #
# Tests
# --------------------------------------------------------------------------- #


class TestBasicReport:
    def test_basic_report_structure(self, tmp_path):
        repo = tmp_path
        _write_story(repo, "user-login-flow", resistance="medium")
        result = _run(repo, "user-login-flow")
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert data["story_slug"] == "user-login-flow"
        assert data["change_resistance"] == "medium"
        assert data["immutable"] is False
        assert isinstance(data["locked_sections"], list)
        assert isinstance(data["inline_locked_blocks"], list)
        assert isinstance(data["auditable_claims_count"], int)

    def test_immutable_story_reports_immutable_true(self, tmp_path):
        repo = tmp_path
        _write_story(repo, "core-policy", resistance="immutable", authority="accepted")
        result = _run(repo, "core-policy")
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert data["immutable"] is True
        assert data["change_resistance"] == "immutable"

    def test_title_and_status_in_report(self, tmp_path):
        repo = tmp_path
        _write_story(repo, "user-login-flow", title="User Login Flow", status="draft")
        result = _run(repo, "user-login-flow")
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert data["title"] == "User Login Flow"
        assert data["status"] == "draft"


class TestLockedSections:
    def test_locked_sections_reported(self, tmp_path):
        repo = tmp_path
        _write_story(
            repo, "guarded-story",
            extra_fm="locked_sections:\n  - Intent\n  - Boundaries\n",
        )
        result = _run(repo, "guarded-story")
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert "Intent" in data["locked_sections"]
        assert "Boundaries" in data["locked_sections"]

    def test_no_locked_sections(self, tmp_path):
        repo = tmp_path
        _write_story(repo, "open-story")
        result = _run(repo, "open-story")
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert data["locked_sections"] == []

    def test_section_flag_locked(self, tmp_path):
        repo = tmp_path
        _write_story(
            repo, "guarded-story",
            extra_fm="locked_sections: [Intent]\n",
        )
        result = _run(repo, "guarded-story", "--section", "Intent")
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert data["section"] == "Intent"
        assert data["section_locked"] is True

    def test_section_flag_unlocked(self, tmp_path):
        repo = tmp_path
        _write_story(
            repo, "guarded-story",
            extra_fm="locked_sections: [Intent]\n",
        )
        result = _run(repo, "guarded-story", "--section", "Story")
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert data["section"] == "Story"
        assert data["section_locked"] is False


class TestInlineLockedBlocks:
    def test_inline_locked_blocks_reported(self, tmp_path):
        repo = tmp_path
        _write_story(
            repo, "inline-locked",
            story="Before.\n<!-- lock:begin -->\nProtected.\n<!-- lock:end -->\nAfter.",
        )
        result = _run(repo, "inline-locked")
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert len(data["inline_locked_blocks"]) == 1
        block = data["inline_locked_blocks"][0]
        assert "start_line" in block
        assert "end_line" in block

    def test_no_inline_locked_blocks(self, tmp_path):
        repo = tmp_path
        _write_story(repo, "no-locks")
        result = _run(repo, "no-locks")
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert data["inline_locked_blocks"] == []


class TestClaimsCounting:
    def test_claims_counted(self, tmp_path):
        repo = tmp_path
        _write_story(
            repo, "multi-claims",
            claims=["Claim one.", "Claim two.", "Claim three."],
        )
        result = _run(repo, "multi-claims")
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert data["auditable_claims_count"] == 3

    def test_single_default_claim(self, tmp_path):
        repo = tmp_path
        _write_story(repo, "one-claim")
        result = _run(repo, "one-claim")
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert data["auditable_claims_count"] == 1


class TestErrorCases:
    def test_missing_story_exits_2(self, tmp_path):
        repo = tmp_path
        (repo / "docs" / "stories").mkdir(parents=True)
        result = _run(repo, "nonexistent-story")
        assert result.returncode == 2
        assert "not found" in result.stderr.lower()

    def test_missing_repo_root_exits_2(self, tmp_path):
        result = _run(tmp_path / "nope", "anything")
        assert result.returncode == 2

    def test_malformed_story_exits_2(self, tmp_path):
        repo = tmp_path
        stories_dir = repo / "docs" / "stories"
        stories_dir.mkdir(parents=True)
        (stories_dir / "bad.md").write_text("no frontmatter here", encoding="utf-8")
        result = _run(repo, "bad")
        assert result.returncode == 2
