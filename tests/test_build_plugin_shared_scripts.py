"""Tests for shared-script packaging support in build-plugin.

Covers:
- shared scripts materialized into canonical skill dirs (.py → scripts/, .md → references/)
- shared scripts materialized into Codex plugin skill dirs
- materialization idempotency
- --shared-only mode skips manifest writes
- packaging.json excluded from generated output
- executable mode preserved
- error cases: path traversal, missing file, non-string entry, malformed JSON,
  collision with skill-owned file
"""

from __future__ import annotations

import json
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
BUILD_SCRIPT = REPO_ROOT / "scripts" / "build-plugin"
SHARED_ROOT = REPO_ROOT / "shared"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _copy_tree_for_build(tmp_path: Path) -> None:
    """Copy skills, scripts, shared, and version file into tmp_path."""
    for sub in ("skills", "scripts", "shared"):
        shutil.copytree(REPO_ROOT / sub, tmp_path / sub)
    shutil.copy(REPO_ROOT / "plugin-version.json", tmp_path / "plugin-version.json")


def run_build(tmp_path: Path, extra_args: list[str] | None = None, *, check: bool = True):
    cmd = [sys.executable, str(tmp_path / "scripts" / "build-plugin"), "-v"]
    if extra_args:
        cmd.extend(extra_args)
    return subprocess.run(cmd, capture_output=True, text=True, check=check, cwd=tmp_path)


def setup_build(tmp_path: Path) -> Path:
    _copy_tree_for_build(tmp_path)
    run_build(tmp_path)
    return tmp_path


# ---------------------------------------------------------------------------
# Materialization into canonical skill dirs
# ---------------------------------------------------------------------------

def test_shared_py_files_materialized_into_canonical_scripts_dir(tmp_path):
    out = setup_build(tmp_path)
    scripts = out / "skills" / "stories-audit" / "scripts"
    assert (scripts / "storystore_lib.py").exists(), "storystore_lib.py missing from canonical scripts/"
    assert (scripts / "inventory.py").exists(), "inventory.py missing from canonical scripts/"


def test_shared_md_files_materialized_into_canonical_references_dir(tmp_path):
    out = setup_build(tmp_path)
    refs = out / "skills" / "stories-audit" / "references"
    assert (refs / "spec.md").exists(), "spec.md missing from canonical references/"


def test_shared_files_content_matches_source(tmp_path):
    out = setup_build(tmp_path)
    src = out / "shared" / "storystore_lib.py"
    dst = out / "skills" / "stories-audit" / "scripts" / "storystore_lib.py"
    assert dst.read_text() == src.read_text()


def test_materialization_covers_all_packaging_json_skills(tmp_path):
    out = setup_build(tmp_path)
    for skill in ("stories-audit", "stories-coverage", "stories-impact-check"):
        scripts = out / "skills" / skill / "scripts"
        assert (scripts / "storystore_lib.py").exists(), f"{skill}: storystore_lib.py missing"
        assert (scripts / "inventory.py").exists(), f"{skill}: inventory.py missing"
        assert (out / "skills" / skill / "references" / "spec.md").exists(), f"{skill}: spec.md missing"


def test_stories_init_and_update_materialize_declared_scripts(tmp_path):
    out = setup_build(tmp_path)
    scripts = out / "skills" / "stories-init" / "scripts"
    assert (scripts / "storystore_lib.py").exists(), "stories-init: storystore_lib.py missing"
    assert (scripts / "inventory.py").exists(), "stories-init: inventory.py missing"
    assert (scripts / "stories_init_mechanical.py").exists(), (
        "stories-init: stories_init_mechanical.py missing"
    )
    scripts = out / "skills" / "stories-update" / "scripts"
    assert (scripts / "storystore_lib.py").exists(), "stories-update: storystore_lib.py missing"
    assert (scripts / "audit.py").exists(), "stories-update: audit.py missing"


# ---------------------------------------------------------------------------
# Materialization into Codex generated output
# ---------------------------------------------------------------------------

def test_shared_py_files_materialized_into_codex_skill_dir(tmp_path):
    out = setup_build(tmp_path)
    for skill in ("stories-audit", "stories-coverage", "stories-impact-check"):
        scripts = out / ".codex-plugin" / "skills" / skill / "scripts"
        assert (scripts / "storystore_lib.py").exists(), f"{skill}: storystore_lib.py missing from Codex output"
        assert (scripts / "inventory.py").exists(), f"{skill}: inventory.py missing from Codex output"


def test_shared_md_files_materialized_into_codex_references_dir(tmp_path):
    out = setup_build(tmp_path)
    for skill in ("stories-audit", "stories-coverage", "stories-impact-check"):
        refs = out / ".codex-plugin" / "skills" / skill / "references"
        assert (refs / "spec.md").exists(), f"{skill}: spec.md missing from Codex references/"


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------

def test_materialization_is_idempotent(tmp_path):
    _copy_tree_for_build(tmp_path)
    run_build(tmp_path)
    run_build(tmp_path)  # second run must not error


# ---------------------------------------------------------------------------
# packaging.json excluded from output
# ---------------------------------------------------------------------------

def test_packaging_json_not_in_codex_skill_output(tmp_path):
    out = setup_build(tmp_path)
    for skill in ("stories-audit", "stories-coverage"):
        assert not (out / ".codex-plugin" / "skills" / skill / "packaging.json").exists()


def test_packaging_json_not_in_claude_skill_output(tmp_path):
    out = setup_build(tmp_path)
    claude_skills = out / ".claude" / "skills"
    for f in claude_skills.iterdir():
        assert "packaging" not in f.name.lower()


# ---------------------------------------------------------------------------
# Executable mode preserved
# ---------------------------------------------------------------------------

def test_executable_mode_preserved_for_py_files(tmp_path):
    _copy_tree_for_build(tmp_path)
    src = tmp_path / "shared" / "storystore_lib.py"
    src.chmod(src.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    run_build(tmp_path)
    dst = tmp_path / "skills" / "stories-audit" / "scripts" / "storystore_lib.py"
    assert dst.stat().st_mode & stat.S_IXUSR, "executable bit not preserved in canonical skill dir"
    codex_dst = tmp_path / ".codex-plugin" / "skills" / "stories-audit" / "scripts" / "storystore_lib.py"
    assert codex_dst.stat().st_mode & stat.S_IXUSR, "executable bit not preserved in Codex output"


# ---------------------------------------------------------------------------
# --shared-only mode
# ---------------------------------------------------------------------------

def test_shared_only_materializes_without_writing_manifests(tmp_path):
    _copy_tree_for_build(tmp_path)
    run_build(tmp_path, extra_args=["--shared-only"])
    # Shared scripts materialized
    assert (tmp_path / "skills" / "stories-audit" / "scripts" / "storystore_lib.py").exists()
    # Full manifests NOT written
    assert not (tmp_path / ".claude-plugin" / "plugin.json").exists()
    assert not (tmp_path / ".codex-plugin" / "plugin.json").exists()


def test_shared_only_is_idempotent(tmp_path):
    _copy_tree_for_build(tmp_path)
    run_build(tmp_path, extra_args=["--shared-only"])
    run_build(tmp_path, extra_args=["--shared-only"])  # must not error


# ---------------------------------------------------------------------------
# Error cases
# ---------------------------------------------------------------------------

def _build_with_bad_packaging(tmp_path: Path, skill: str, packaging: object) -> subprocess.CompletedProcess:
    _copy_tree_for_build(tmp_path)
    pkg_file = tmp_path / "skills" / skill / "packaging.json"
    pkg_file.write_text(json.dumps(packaging))
    return run_build(tmp_path, check=False)


def test_path_traversal_in_shared_scripts_fails(tmp_path):
    result = _build_with_bad_packaging(
        tmp_path, "stories-init",
        {"shared_scripts": ["../../../etc/passwd"]},
    )
    assert result.returncode != 0
    assert "escap" in (result.stderr + result.stdout).lower() or "traversal" in (result.stderr + result.stdout).lower() or "invalid" in (result.stderr + result.stdout).lower()


def test_absolute_path_in_shared_scripts_fails(tmp_path):
    result = _build_with_bad_packaging(
        tmp_path, "stories-init",
        {"shared_scripts": ["/etc/passwd"]},
    )
    assert result.returncode != 0


def test_missing_shared_file_fails(tmp_path):
    result = _build_with_bad_packaging(
        tmp_path, "stories-init",
        {"shared_scripts": ["nonexistent.py"]},
    )
    assert result.returncode != 0
    assert "nonexistent" in (result.stderr + result.stdout)


def test_nonstring_entry_in_shared_scripts_fails(tmp_path):
    result = _build_with_bad_packaging(
        tmp_path, "stories-init",
        {"shared_scripts": [42]},
    )
    assert result.returncode != 0


def test_malformed_packaging_json_not_a_dict_fails(tmp_path):
    result = _build_with_bad_packaging(
        tmp_path, "stories-init",
        ["not", "a", "dict"],
    )
    assert result.returncode != 0


def test_shared_scripts_not_a_list_fails(tmp_path):
    result = _build_with_bad_packaging(
        tmp_path, "stories-init",
        {"shared_scripts": "storystore_lib.py"},
    )
    assert result.returncode != 0


def test_collision_with_skill_owned_file_fails(tmp_path):
    _copy_tree_for_build(tmp_path)
    # Plant a skill-owned file with the same name as a shared script but different content
    scripts_dir = tmp_path / "skills" / "stories-audit" / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    (scripts_dir / "storystore_lib.py").write_text("# skill-owned version, different content\n")
    result = run_build(tmp_path, check=False)
    assert result.returncode != 0
    assert "collision" in (result.stderr + result.stdout).lower() or "conflict" in (result.stderr + result.stdout).lower()
