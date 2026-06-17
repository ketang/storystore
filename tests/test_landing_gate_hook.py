"""Tests for the copyable land-work landing-gate hook script.

examples/land-work/hook-scripts/pre/30-stories-audit.sh is a copyable bento
land-work `pre` hook that runs the strict storystore audit before a merge.
These tests run it against three corpora:

  - no docs/stories/INDEX.md      -> exit 0 (nothing to audit)
  - a clean strict audit          -> exit 0 (gate passes)
  - an injected failing finding   -> exit nonzero (gate blocks the merge)

The script resolves shared/audit.py via $STORYSTORE_SHARED; the tests point it
at this repo's shared/ dir so the run is deterministic and self-contained.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
HOOK_SCRIPT = (
    REPO_ROOT
    / "examples"
    / "land-work"
    / "hook-scripts"
    / "pre"
    / "30-stories-audit.sh"
)
SHARED_DIR = REPO_ROOT / "shared"

CLEAN_STORY = """\
---
schema_version: 1
title: Greet command
slug: greet-command
status: active
authority: observed
change_resistance: medium
tests_applicable: false
---

# Greet command

## Intent
A user runs the greet command to print a friendly greeting.

## Story
Someone trying the tool for the first time wants immediate, visible output.

## Expected Behavior
Running the command prints a greeting to stdout.

## Boundaries
This story does not cover localization or output formatting.

## Auditable Claims
- The project documents how to print a greeting.

## Evidence
### Docs
- `README.md`
"""

INDEX = """\
# Story Index
- [greet-command](greet-command.md) — active — observed
"""


def _write_clean_corpus(root: Path) -> None:
    """Write a minimal story corpus that passes a strict audit."""
    (root / "README.md").write_text("# Sample project\nRun `greet` to greet.\n")
    stories = root / "docs" / "stories"
    stories.mkdir(parents=True)
    (stories / "INDEX.md").write_text(INDEX)
    (stories / "greet-command.md").write_text(CLEAN_STORY)


def _run_hook(repo_root: Path) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["STORYSTORE_SHARED"] = str(SHARED_DIR)
    env["BENTO_HOOK_REPO_ROOT"] = str(repo_root)
    return subprocess.run(
        ["bash", str(HOOK_SCRIPT)],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


def test_hook_script_exists_and_is_executable() -> None:
    assert HOOK_SCRIPT.is_file(), f"missing hook script: {HOOK_SCRIPT}"
    assert os.access(HOOK_SCRIPT, os.X_OK), "hook script must be executable"


def test_absent_index_passes_immediately(tmp_path: Path) -> None:
    # No docs/stories/INDEX.md → nothing to audit → gate is a no-op.
    result = _run_hook(tmp_path)
    assert result.returncode == 0, result.stderr


def test_clean_corpus_passes(tmp_path: Path) -> None:
    _write_clean_corpus(tmp_path)
    result = _run_hook(tmp_path)
    assert result.returncode == 0, f"clean corpus should pass: {result.stderr}"


def test_failing_finding_blocks(tmp_path: Path) -> None:
    _write_clean_corpus(tmp_path)
    # Inject a dangling doc-evidence ref so the strict audit reports a finding.
    story = tmp_path / "docs" / "stories" / "greet-command.md"
    story.write_text(
        story.read_text().replace(
            "- `README.md`",
            "- `README.md`\n- `docs/NONEXISTENT.md`",
        )
    )
    result = _run_hook(tmp_path)
    assert result.returncode != 0, "failing strict audit must block the merge"


def test_missing_shared_dir_reports_and_blocks(tmp_path: Path) -> None:
    # When no resolution path finds audit.py, the gate must fail loudly rather
    # than silently passing the merge. Copy the script into an isolated dir so
    # its in-plugin-tree fallback (script_dir/../../../../shared) cannot find
    # this repo's real shared/, and point HOME at an empty dir so the cache
    # scan finds nothing.
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_clean_corpus(repo)
    isolated = tmp_path / "isolated"
    isolated.mkdir()
    script_copy = isolated / "30-stories-audit.sh"
    script_copy.write_text(HOOK_SCRIPT.read_text())
    script_copy.chmod(0o755)

    env = dict(os.environ)
    env["STORYSTORE_SHARED"] = str(tmp_path / "no-such-shared")
    env["BENTO_HOOK_REPO_ROOT"] = str(repo)
    env["HOME"] = str(tmp_path / "empty-home")
    result = subprocess.run(
        ["bash", str(script_copy)],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    assert result.returncode != 0
    assert "STORYSTORE_SHARED" in result.stderr


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
