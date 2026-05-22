"""Tests for the stories-init mechanical Phase 1 script."""

import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "stories-init-mechanical"


def run(repo_root: Path) -> dict:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--repo-root", str(repo_root)],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout)


def init_repo(tmp_path: Path) -> Path:
    (tmp_path / ".gitignore").write_text("# existing\n*.pyc\n")
    return tmp_path


def test_fresh_init_creates_scaffolding(tmp_path):
    repo = init_repo(tmp_path)
    out = run(repo)

    assert out["fresh_init"] is True
    assert (repo / "docs" / "stories").is_dir()
    readme = repo / "docs" / "stories" / "README.md"
    index = repo / "docs" / "stories" / "INDEX.md"
    assert readme.exists()
    assert index.exists()
    assert index.read_text() == ""
    assert "docs/stories/" in out["created"]
    assert "docs/stories/README.md" in out["created"]
    assert "docs/stories/INDEX.md" in out["created"]
    assert out["preserved"] == []
    assert out["gitignore_updated"] is True
    gitignore = (repo / ".gitignore").read_text()
    assert "docs/stories/drift-todo.md" in gitignore
    assert out["agent_instruction_files"] == []


def test_idempotent_rerun(tmp_path):
    repo = init_repo(tmp_path)
    first = run(repo)
    readme = repo / "docs" / "stories" / "README.md"
    original_readme = readme.read_text()

    second = run(repo)

    assert first["fresh_init"] is True
    assert second["fresh_init"] is False
    assert second["created"] == []
    assert "docs/stories/README.md" in second["preserved"]
    assert "docs/stories/INDEX.md" in second["preserved"]
    assert second["gitignore_updated"] is False
    assert readme.read_text() == original_readme
    gitignore = (repo / ".gitignore").read_text()
    assert gitignore.count("docs/stories/drift-todo.md") == 1


def test_preserves_existing_readme(tmp_path):
    repo = init_repo(tmp_path)
    stories = repo / "docs" / "stories"
    stories.mkdir(parents=True)
    custom = "# my own readme\ncontent here\n"
    (stories / "README.md").write_text(custom)

    out = run(repo)

    assert out["fresh_init"] is False
    assert (stories / "README.md").read_text() == custom
    assert "docs/stories/README.md" in out["preserved"]
    assert "docs/stories/INDEX.md" in out["created"]


def test_gitignore_no_duplicate_when_already_present(tmp_path):
    repo = init_repo(tmp_path)
    (repo / ".gitignore").write_text("*.pyc\ndocs/stories/drift-todo.md\n")

    out = run(repo)

    assert out["gitignore_updated"] is False
    gitignore = (repo / ".gitignore").read_text()
    assert gitignore.count("docs/stories/drift-todo.md") == 1


def test_gitignore_created_when_missing(tmp_path):
    repo = tmp_path
    out = run(repo)

    assert out["gitignore_updated"] is True
    assert (repo / ".gitignore").exists()
    assert "docs/stories/drift-todo.md" in (repo / ".gitignore").read_text()


def test_detects_agent_instruction_files(tmp_path):
    repo = init_repo(tmp_path)
    (repo / "AGENTS.md").write_text("agents\n")
    (repo / "CLAUDE.md").write_text("claude\n")
    (repo / "GEMINI.md").write_text("gemini\n")

    out = run(repo)

    assert sorted(out["agent_instruction_files"]) == ["AGENTS.md", "CLAUDE.md", "GEMINI.md"]


def test_no_agent_instruction_files_when_absent(tmp_path):
    repo = init_repo(tmp_path)
    out = run(repo)
    assert out["agent_instruction_files"] == []


def test_partial_existing_only_index_missing(tmp_path):
    repo = init_repo(tmp_path)
    stories = repo / "docs" / "stories"
    stories.mkdir(parents=True)
    (stories / "README.md").write_text("# kept\n")

    out = run(repo)

    assert out["fresh_init"] is False
    assert "docs/stories/INDEX.md" in out["created"]
    assert "docs/stories/README.md" in out["preserved"]
