"""Fixture-driven test helpers for storystore functional tests.

Provides reusable utilities for:
- copying fixture mini-repos into temp dirs
- invoking skill scripts against a fixture and capturing exit/stdout/stderr
- asserting JSON and Markdown output shapes
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"

FIXTURE_NAMES = (
    "empty",
    "ts-cli",
    "http-api",
    "docs-heavy",
    "malformed-story",
    "drift",
)


def fixture_path(name: str) -> Path:
    path = FIXTURES_DIR / name
    if not path.is_dir():
        raise FileNotFoundError(f"fixture {name!r} not found at {path}")
    return path


def copy_fixture(name: str, dest: Path | None = None) -> Path:
    """Copy fixture <name> into a fresh directory and return its path.

    If dest is None, a tempfile.mkdtemp() directory is used. Caller owns
    cleanup of the temp dir.
    """
    src = fixture_path(name)
    if dest is None:
        dest = Path(tempfile.mkdtemp(prefix=f"storystore-fx-{name}-"))
    target = dest / name if dest.exists() and any(dest.iterdir()) else dest
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(src, target)
    return target


@dataclass
class ScriptResult:
    returncode: int
    stdout: str
    stderr: str

    def json(self) -> Any:
        return json.loads(self.stdout)


def run_skill_script(
    script_rel: str,
    fixture_dir: Path,
    *args: str,
    repo_root_flag: str = "--repo-root",
) -> ScriptResult:
    """Run a skill script (path relative to REPO_ROOT) against a fixture.

    Returns a ScriptResult with returncode, stdout, stderr. Does NOT raise on
    non-zero exit — callers assert on the result.

    If the script does not exist, returns returncode=127 with a stderr
    message; this mirrors shell behavior so tests can treat unimplemented
    scripts uniformly.
    """
    script_path = REPO_ROOT / script_rel
    if not script_path.exists():
        return ScriptResult(
            returncode=127,
            stdout="",
            stderr=f"script not found: {script_rel}\n",
        )
    cmd = [sys.executable, str(script_path), repo_root_flag, str(fixture_dir), *args]
    completed = subprocess.run(cmd, capture_output=True, text=True, check=False)
    return ScriptResult(
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )


def assert_json_keys(payload: Any, required: Iterable[str]) -> dict:
    """Assert payload is a dict and contains every required key. Returns the dict."""
    assert isinstance(payload, dict), f"expected JSON object, got {type(payload).__name__}"
    missing = [k for k in required if k not in payload]
    assert not missing, f"missing JSON keys: {missing}; got {sorted(payload)}"
    return payload


def assert_markdown_contains(text: str, *fragments: str) -> None:
    """Assert each fragment appears in text. Reports the first missing fragment."""
    for frag in fragments:
        assert frag in text, f"markdown fragment not found: {frag!r}"


def count_story_files(stories_dir: Path) -> int:
    """Count story .md files under stories_dir, excluding loader skip list."""
    skip = {"README.md", "INDEX.md", "drift-todo.md"}
    if not stories_dir.is_dir():
        return 0
    return sum(1 for p in stories_dir.glob("*.md") if p.name not in skip)
