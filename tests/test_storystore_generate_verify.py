"""Tests for ``write_story.py --verify`` deterministic evidence verification.

A story's evidence refs are resolved against the repo before the story is
written. Refs that are mechanically checkable but fail resolution (the
pickpackit pattern: a fabricated endpoint or a route missing its mount
prefix), and refs outside deterministic reach, are quarantined under
``### <Kind> (unverified)`` headings instead of being written as clean
evidence. The story still generates.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "shared" / "write_story.py"
LIB_PATH = REPO_ROOT / "shared" / "storystore_lib.py"


def _load_lib():
    spec = importlib.util.spec_from_file_location("storystore_lib", LIB_PATH)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules["storystore_lib"] = mod
    spec.loader.exec_module(mod)
    return mod


lib = _load_lib()


def _pickpackit_repo(tmp_path: Path) -> Path:
    """Build a TS repo reproducing the pickpackit shape.

    The server mounts a real route at ``GET /api/v1/widgets``. The repo also
    ships a real test file and a real README so verifiable refs can pass clean.
    """
    repo = tmp_path
    stories = repo / "docs" / "stories"
    stories.mkdir(parents=True)
    (stories / "README.md").write_text("# stories\n")
    (stories / "INDEX.md").write_text("")

    (repo / "package.json").write_text(json.dumps({"name": "pickpackit"}) + "\n")
    (repo / "README.md").write_text("# Pickpackit\n")

    src = repo / "src"
    src.mkdir()
    (src / "server.ts").write_text(
        "import express from 'express';\n"
        "const app = express();\n"
        "app.get('/api/v1/widgets', (req, res) => res.json([]));\n"
    )

    tests = repo / "tests"
    tests.mkdir()
    (tests / "widgets.test.ts").write_text(
        "import { describe, it } from 'vitest';\n"
        "describe('widgets', () => { it('lists widgets', () => {}); });\n"
    )
    return repo


def _run(repo: Path, payload: dict, mode: str, *verify: str):
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--repo-root", str(repo), mode, *verify],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
    )


def _payload(**overrides):
    base = {
        "title": "Browse Available Widgets",
        "slug": "browse-available-widgets",
        "intent": "Users browse widgets to decide what to pick.",
        "story": "A user opens the widget list before picking one.",
        "expected_behavior": "The list endpoint returns the available widgets.",
        "boundaries": "Does not cover creating widgets.",
        "auditable_claims": ["The widget list endpoint exists."],
        "evidence": {"tests": [], "surface": [], "docs": []},
    }
    base.update(overrides)
    return base


def test_pickpackit_pattern_quarantines_fabricated_and_missing_prefix(tmp_path):
    repo = _pickpackit_repo(tmp_path)
    payload = _payload(
        evidence={
            "tests": ["tests/widgets.test.ts", "tests/missing.test.ts"],
            "surface": [
                "route: GET /api/v1/widgets",   # real -> clean
                "route: GET /widgets",          # missing mount prefix -> failed
                "route: GET /api/v1/fabricated",  # fabricated -> failed
                "test: lists widgets",          # outside deterministic reach
            ],
            "docs": ["README.md", "MISSING.md"],
        }
    )
    result = _run(repo, payload, "--observed", "--verify")
    # Story still generates — verification marks, it does not block.
    assert result.returncode == 0, result.stderr
    out = json.loads(result.stdout)
    assert out["verified"] is True

    by_ref = {r["ref"]: r for r in out["unverified"]}
    # Mechanically-checkable failures: fabricated endpoint + missing prefix.
    assert by_ref["route: GET /widgets"]["deterministic"] is True
    assert by_ref["route: GET /api/v1/fabricated"]["deterministic"] is True
    assert by_ref["tests/missing.test.ts"]["deterministic"] is True
    assert by_ref["MISSING.md"]["deterministic"] is True
    # Outside deterministic reach: name-based surface ref.
    assert by_ref["test: lists widgets"]["deterministic"] is False
    # Resolved refs are absent from the unverified report.
    assert "route: GET /api/v1/widgets" not in by_ref
    assert "tests/widgets.test.ts" not in by_ref
    assert "README.md" not in by_ref

    story_path = repo / "docs" / "stories" / "browse-available-widgets.md"
    parsed = lib.parse_story(story_path)
    # Only verified refs are written as clean evidence.
    assert parsed.evidence_surface == ["route: GET /api/v1/widgets"]
    assert parsed.evidence_tests == ["tests/widgets.test.ts"]
    assert parsed.evidence_docs == ["README.md"]

    text = story_path.read_text()
    assert "### Surface (unverified)" in text
    assert "### Tests (unverified)" in text
    assert "### Docs (unverified)" in text
    assert "route: GET /api/v1/fabricated" in text
    assert "route: GET /widgets" in text

    assert "STORYSTORE_EVIDENCE_UNVERIFIED" in result.stderr
    assert "FAILED" in result.stderr


def test_verifiable_evidence_passes_clean_with_no_unverified_section(tmp_path):
    repo = _pickpackit_repo(tmp_path)
    payload = _payload(
        evidence={
            "tests": ["tests/widgets.test.ts"],
            "surface": ["route: GET /api/v1/widgets"],
            "docs": ["README.md"],
        }
    )
    result = _run(repo, payload, "--observed", "--verify")
    assert result.returncode == 0, result.stderr
    out = json.loads(result.stdout)
    assert out["unverified"] == []
    text = (repo / "docs" / "stories" / "browse-available-widgets.md").read_text()
    assert "(unverified)" not in text
    assert "STORYSTORE_EVIDENCE_UNVERIFIED" not in result.stderr


def test_prose_surface_ref_passes_through_marked_unverified(tmp_path):
    repo = _pickpackit_repo(tmp_path)
    payload = _payload(
        evidence={
            "tests": [],
            "surface": ["heading: How Widgets Work"],
            "docs": [],
        }
    )
    result = _run(repo, payload, "--observed", "--verify")
    assert result.returncode == 0, result.stderr
    out = json.loads(result.stdout)
    assert len(out["unverified"]) == 1
    entry = out["unverified"][0]
    assert entry["ref"] == "heading: How Widgets Work"
    assert entry["deterministic"] is False
    parsed = lib.parse_story(repo / "docs" / "stories" / "browse-available-widgets.md")
    assert parsed.evidence_surface == []


def test_fabricated_file_path_marked_unverified(tmp_path):
    repo = _pickpackit_repo(tmp_path)
    payload = _payload(
        evidence={"tests": ["tests/does-not-exist.test.ts"], "surface": [], "docs": []}
    )
    result = _run(repo, payload, "--observed", "--verify")
    assert result.returncode == 0, result.stderr
    out = json.loads(result.stdout)
    assert out["unverified"][0]["ref"] == "tests/does-not-exist.test.ts"
    assert out["unverified"][0]["deterministic"] is True
    parsed = lib.parse_story(repo / "docs" / "stories" / "browse-available-widgets.md")
    assert parsed.evidence_tests == []


def test_without_verify_flag_evidence_written_clean_unchanged(tmp_path):
    # Backward compatibility: without --verify, refs are written verbatim and
    # the result carries no verification metadata side effects.
    repo = _pickpackit_repo(tmp_path)
    payload = _payload(
        evidence={
            "tests": ["tests/missing.test.ts"],
            "surface": ["route: GET /api/v1/fabricated"],
            "docs": ["MISSING.md"],
        }
    )
    result = _run(repo, payload, "--observed")
    assert result.returncode == 0, result.stderr
    out = json.loads(result.stdout)
    assert out["verified"] is False
    assert out["unverified"] == []
    parsed = lib.parse_story(repo / "docs" / "stories" / "browse-available-widgets.md")
    assert parsed.evidence_surface == ["route: GET /api/v1/fabricated"]
    assert parsed.evidence_tests == ["tests/missing.test.ts"]
    assert parsed.evidence_docs == ["MISSING.md"]
    assert "STORYSTORE_EVIDENCE_UNVERIFIED" not in result.stderr
