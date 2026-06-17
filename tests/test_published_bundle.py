"""Verify the published plugin archive excludes internal-only files.

Claude installs storystore from the GitHub source tarball (its plugin
cache is an archive extract, not a git clone) and the Codex installer
downloads the same codeload tarball. GitHub honors `export-ignore` from
.gitattributes when generating those archives, so the contents of
`git archive` are the contract for what a consumer receives.

These tests assert that internal planning docs, the test suite, and the
tracker state are absent from the archive while every runtime-needed
file is present. `--worktree-attributes` makes the check read the
working-tree .gitattributes so it holds before the change is committed.
"""

from __future__ import annotations

import subprocess
import tarfile
from io import BytesIO
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]

EXCLUDED_PREFIXES = [
    "2026-05-01-storystore-plan-1-foundation.md",
    "2026-05-01-storystore-plan-2-fidelity.md",
    "2026-05-01-storystore-plan-3-edits-and-impact.md",
    "2026-05-01-storystore-target-design.md",
    "tests/",
    ".beads/",
]

REQUIRED_FILES = [
    "spec.md",
    "README.md",
    "INSTALL.md",
    "plugin-version.json",
    ".claude-plugin/plugin.json",
    ".codex-plugin/plugin.json",
]

REQUIRED_DIR_PREFIXES = [
    "skills/",
    "shared/",
    "scripts/",
    ".claude/skills/",
    # Copyable example hook scripts (e.g. the land-work landing gate) must
    # reach consumers in the published bundle, not just the git checkout.
    "examples/",
]


def _archive_members() -> list[str]:
    proc = subprocess.run(
        ["git", "archive", "--worktree-attributes", "--format=tar", "HEAD"],
        cwd=REPO_ROOT,
        capture_output=True,
        check=True,
    )
    with tarfile.open(fileobj=BytesIO(proc.stdout)) as tar:
        return [m.name for m in tar.getmembers() if m.isfile()]


@pytest.fixture(scope="module")
def members() -> list[str]:
    return _archive_members()


@pytest.mark.parametrize("prefix", EXCLUDED_PREFIXES)
def test_excluded_paths_absent_from_archive(members: list[str], prefix: str) -> None:
    leaked = [m for m in members if m == prefix or m.startswith(prefix)]
    assert not leaked, f"published archive must not contain {prefix!r}: {leaked}"


@pytest.mark.parametrize("path", REQUIRED_FILES)
def test_required_files_present_in_archive(members: list[str], path: str) -> None:
    assert path in members, f"published archive is missing runtime file {path!r}"


@pytest.mark.parametrize("prefix", REQUIRED_DIR_PREFIXES)
def test_required_dirs_present_in_archive(members: list[str], prefix: str) -> None:
    assert any(m.startswith(prefix) for m in members), (
        f"published archive is missing runtime directory {prefix!r}"
    )
