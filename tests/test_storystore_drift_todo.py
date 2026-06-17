"""Tests for shared/drift_todo.py — append-only drift todo helper."""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "shared" / "drift_todo.py"


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def run_drift_todo(
    tmp_path: Path,
    slug: str,
    description: str,
    metadata: dict | None = None,
    now_iso: str | None = None,
) -> subprocess.CompletedProcess:
    """Run drift_todo.append_drift_todo via a subprocess helper."""
    code = [
        "import sys, json",
        "sys.path.insert(0, {!r})".format(str(REPO_ROOT / "shared")),
        "from drift_todo import append_drift_todo",
        "from datetime import datetime, timezone",
        "from pathlib import Path",
        "",
        "slug = {!r}".format(slug),
        "description = {!r}".format(description),
        "metadata = {}".format(repr(metadata)),
        "drift_path = Path({!r}) / 'drift-todo.md'".format(str(tmp_path)),
    ]
    if now_iso:
        code.append(
            "now = datetime.fromisoformat({!r})".format(now_iso)
        )
    else:
        code.append("now = None")

    code.append(
        "result = append_drift_todo(slug, description, metadata=metadata, "
        "drift_todo_path=drift_path, now=now)"
    )
    code.append("print(str(result))")

    script = "\n".join(code)
    return subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
    )


# --------------------------------------------------------------------------- #
# Tests
# --------------------------------------------------------------------------- #


class TestCreateFromScratch:
    """File does not exist — should be created with header + entry."""

    def test_creates_file_with_header(self, tmp_path: Path):
        drift_file = tmp_path / "drift-todo.md"
        assert not drift_file.exists()

        result = run_drift_todo(
            tmp_path,
            slug="user-login",
            description="Login button text diverged from story.",
            now_iso="2025-03-15T10:30:00+00:00",
        )
        assert result.returncode == 0, result.stderr

        content = drift_file.read_text()
        assert content.startswith("# Drift Todo\n")
        assert "Append-only log" in content

    def test_entry_present(self, tmp_path: Path):
        run_drift_todo(
            tmp_path,
            slug="user-login",
            description="Login button text diverged from story.",
            now_iso="2025-03-15T10:30:00+00:00",
        )
        content = (tmp_path / "drift-todo.md").read_text()
        assert "## [2025-03-15 10:30:00 UTC] user-login" in content
        assert "Login button text diverged from story." in content


class TestAppendToExisting:
    """File already exists — old content must be preserved."""

    def test_preserves_existing_content(self, tmp_path: Path):
        # First entry
        run_drift_todo(
            tmp_path,
            slug="first-story",
            description="First drift noted.",
            now_iso="2025-01-01T00:00:00+00:00",
        )
        first_content = (tmp_path / "drift-todo.md").read_text()

        # Second entry
        run_drift_todo(
            tmp_path,
            slug="second-story",
            description="Second drift noted.",
            now_iso="2025-02-01T00:00:00+00:00",
        )
        second_content = (tmp_path / "drift-todo.md").read_text()

        # Old content preserved
        assert "first-story" in second_content
        assert "First drift noted." in second_content
        # New content appended
        assert "second-story" in second_content
        assert "Second drift noted." in second_content
        # Second content is strictly longer (append-only)
        assert len(second_content) > len(first_content)

    def test_header_not_duplicated(self, tmp_path: Path):
        run_drift_todo(tmp_path, slug="a", description="A.")
        run_drift_todo(tmp_path, slug="b", description="B.")
        content = (tmp_path / "drift-todo.md").read_text()
        assert content.count("# Drift Todo") == 1


class TestDateStampFormat:
    """Date stamp must follow YYYY-MM-DD HH:MM:SS UTC."""

    def test_format_matches(self, tmp_path: Path):
        run_drift_todo(
            tmp_path,
            slug="ts-check",
            description="Checking timestamp.",
            now_iso="2025-12-31T23:59:59+00:00",
        )
        content = (tmp_path / "drift-todo.md").read_text()
        assert "## [2025-12-31 23:59:59 UTC] ts-check" in content

    def test_different_timestamps(self, tmp_path: Path):
        run_drift_todo(
            tmp_path,
            slug="morning",
            description="AM entry.",
            now_iso="2025-06-15T08:00:00+00:00",
        )
        run_drift_todo(
            tmp_path,
            slug="evening",
            description="PM entry.",
            now_iso="2025-06-15T20:30:45+00:00",
        )
        content = (tmp_path / "drift-todo.md").read_text()
        assert "## [2025-06-15 08:00:00 UTC] morning" in content
        assert "## [2025-06-15 20:30:45 UTC] evening" in content


class TestMultipleAppends:
    """Multiple sequential appends work correctly."""

    def test_three_sequential_appends(self, tmp_path: Path):
        slugs = ["alpha", "beta", "gamma"]
        for i, slug in enumerate(slugs):
            run_drift_todo(
                tmp_path,
                slug=slug,
                description=f"Drift {i}.",
                now_iso=f"2025-04-{10 + i:02d}T12:00:00+00:00",
            )

        content = (tmp_path / "drift-todo.md").read_text()
        for slug in slugs:
            assert slug in content

        # Verify ordering — each entry appears after the previous
        positions = [content.index(s) for s in slugs]
        assert positions == sorted(positions)

    def test_metadata_included(self, tmp_path: Path):
        run_drift_todo(
            tmp_path,
            slug="with-meta",
            description="Has metadata.",
            metadata={"file": "src/login.py", "line": 42},
            now_iso="2025-05-01T00:00:00+00:00",
        )
        content = (tmp_path / "drift-todo.md").read_text()
        assert "```json" in content
        assert '"file": "src/login.py"' in content
        assert '"line": 42' in content

    def test_no_metadata_block_when_none(self, tmp_path: Path):
        run_drift_todo(
            tmp_path,
            slug="no-meta",
            description="No metadata here.",
            now_iso="2025-05-01T00:00:00+00:00",
        )
        content = (tmp_path / "drift-todo.md").read_text()
        assert "```json" not in content


# --------------------------------------------------------------------------- #
# CLI (argparse / __main__ entrypoint)
# --------------------------------------------------------------------------- #


def run_cli(*args: str) -> subprocess.CompletedProcess:
    """Invoke shared/drift_todo.py as a command-line script."""
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
    )


class TestCli:
    """Exercise the argparse CLI added on top of append_drift_todo."""

    def test_writes_file_and_reports_path_on_stdout(self, tmp_path: Path):
        drift_file = tmp_path / "drift-todo.md"
        result = run_cli(
            "--slug", "user-login",
            "--description", "Login button text diverged from story.",
            "--kind", "intent-contradiction",
            "--drift-todo-path", str(drift_file),
        )
        assert result.returncode == 0, result.stderr

        # Path is reported on stdout as JSON (matching audit.py/coverage.py).
        summary = json.loads(result.stdout)
        assert summary == {"drift_todo_path": str(drift_file)}

        content = drift_file.read_text()
        assert "## " in content and "user-login" in content
        assert "Login button text diverged from story." in content

    def test_kind_maps_to_finding_kind_metadata(self, tmp_path: Path):
        drift_file = tmp_path / "drift-todo.md"
        run_cli(
            "--slug", "with-kind",
            "--description", "Has a kind.",
            "--kind", "missing-evidence",
            "--drift-todo-path", str(drift_file),
        )
        content = drift_file.read_text()
        assert '"finding_kind": "missing-evidence"' in content
        assert '"suggested_action": "fix-code"' in content

    def test_kind_optional_still_writes_suggested_action(self, tmp_path: Path):
        drift_file = tmp_path / "drift-todo.md"
        result = run_cli(
            "--slug", "no-kind",
            "--description", "No kind supplied.",
            "--drift-todo-path", str(drift_file),
        )
        assert result.returncode == 0, result.stderr
        content = drift_file.read_text()
        assert '"suggested_action": "fix-code"' in content
        assert "finding_kind" not in content

    def test_missing_required_args_exit_nonzero(self, tmp_path: Path):
        result = run_cli("--slug", "lonely")
        assert result.returncode != 0
        assert "--description" in result.stderr
