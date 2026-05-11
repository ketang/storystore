"""Smoke test for the storystore build-plugin script.

The build-plugin script produces:
- .claude-plugin/plugin.json (Claude manifest)
- .claude/skills/<skill>.md (Claude flat skill files)
- .codex-plugin/plugin.json (Codex manifest with full interface block)
- .codex-plugin/skills/<skill>/SKILL.md (Codex per-skill payload)

Tests run the script as an external process against the real repo so
the test surfaces real path handling and shebang/exec-bit issues.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from importlib.machinery import SourceFileLoader
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
BUILD_SCRIPT = REPO_ROOT / "scripts" / "build-plugin"

EXPECTED_SKILLS = [
    "stories-init",
    "stories-generate",
    "stories-audit",
    "stories-coverage",
    "stories-update",
    "stories-impact-check",
]


def load_build_plugin():
    """Import scripts/build-plugin as a module so unit tests can call helpers."""
    loader = SourceFileLoader("build_plugin", str(BUILD_SCRIPT))
    spec = importlib.util.spec_from_loader("build_plugin", loader)
    module = importlib.util.module_from_spec(spec)
    sys.modules["build_plugin"] = module
    loader.exec_module(module)
    return module


@pytest.fixture
def build_module():
    return load_build_plugin()


def test_build_script_is_executable():
    assert BUILD_SCRIPT.exists(), "scripts/build-plugin missing"
    assert BUILD_SCRIPT.stat().st_mode & 0o111, "scripts/build-plugin must be executable"


def test_help_flag_exits_zero():
    result = subprocess.run(
        [sys.executable, str(BUILD_SCRIPT), "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert "build-plugin" in result.stdout


def test_skill_names_constant_matches_expected(build_module):
    assert build_module.SKILL_NAMES == EXPECTED_SKILLS


def test_claude_manifest_shape(build_module):
    manifest = build_module.claude_manifest("9.9.9")
    assert manifest["name"] == "storystore"
    assert manifest["version"] == "9.9.9"
    assert manifest["description"]
    assert manifest["author"]["name"]


def test_codex_manifest_shape(build_module):
    manifest = build_module.codex_manifest("9.9.9")
    assert manifest["name"] == "storystore"
    assert manifest["version"] == "9.9.9"
    assert manifest["skills"] == "./.codex-plugin/skills"
    interface = manifest["interface"]
    assert interface["displayName"] == "Storystore"
    assert interface["category"] == "Documentation"
    assert "Interactive" in interface["capabilities"]
    assert "Write" in interface["capabilities"]


def test_canonical_skill_sources_exist():
    for name in EXPECTED_SKILLS:
        path = REPO_ROOT / "skills" / name / "SKILL.md"
        assert path.exists(), f"missing canonical {path}"
        text = path.read_text()
        assert text.startswith("---\n"), f"{path} missing frontmatter fence"
        assert f"name: {name}" in text


def run_build_in(tmp_path: Path) -> Path:
    """Copy the canonical sources into tmp_path and run build-plugin there."""
    import shutil

    for sub in ("skills", "scripts", "shared"):
        shutil.copytree(REPO_ROOT / sub, tmp_path / sub)
    shutil.copy(REPO_ROOT / "plugin-version.json", tmp_path / "plugin-version.json")
    result = subprocess.run(
        [sys.executable, str(tmp_path / "scripts" / "build-plugin"), "-v"],
        capture_output=True,
        text=True,
        check=True,
        cwd=tmp_path,
    )
    assert "Built plugin" in result.stdout
    return tmp_path


def test_build_writes_claude_manifest(tmp_path):
    out = run_build_in(tmp_path)
    manifest = json.loads((out / ".claude-plugin" / "plugin.json").read_text())
    assert manifest["name"] == "storystore"
    assert manifest["description"]
    assert manifest["version"] == "0.1.0"


def test_build_writes_codex_manifest(tmp_path):
    out = run_build_in(tmp_path)
    manifest = json.loads((out / ".codex-plugin" / "plugin.json").read_text())
    assert manifest["name"] == "storystore"
    assert manifest["interface"]["displayName"] == "Storystore"
    assert manifest["skills"] == "./.codex-plugin/skills"


def test_build_writes_claude_skill_files(tmp_path):
    out = run_build_in(tmp_path)
    target_dir = out / ".claude" / "skills"
    for name in EXPECTED_SKILLS:
        path = target_dir / f"{name}.md"
        assert path.exists(), f"missing {path}"
        assert f"name: {name}" in path.read_text()


def test_build_writes_codex_skill_dirs(tmp_path):
    out = run_build_in(tmp_path)
    target_root = out / ".codex-plugin" / "skills"
    for name in EXPECTED_SKILLS:
        path = target_root / name / "SKILL.md"
        assert path.exists(), f"missing {path}"
        assert f"name: {name}" in path.read_text()


def test_build_refreshes_codex_skills_removes_stale_entries(tmp_path):
    """Codex skills tree is rebuilt wholesale; stale skill dirs are removed."""
    out = run_build_in(tmp_path)
    stale = out / ".codex-plugin" / "skills" / "stories-removed"
    stale.mkdir(parents=True)
    (stale / "SKILL.md").write_text("stale\n")
    subprocess.run(
        [sys.executable, str(out / "scripts" / "build-plugin")],
        cwd=out,
        check=True,
    )
    assert not stale.exists(), "stale codex skill dir should be removed"


def test_build_bump_increments_patch(tmp_path):
    out = run_build_in(tmp_path)
    subprocess.run(
        [sys.executable, str(out / "scripts" / "build-plugin"), "--bump"],
        cwd=out,
        check=True,
    )
    version = json.loads((out / "plugin-version.json").read_text())["version"]
    assert version == "0.1.1"


def test_build_aborts_when_canonical_skill_missing(tmp_path):
    import shutil

    for sub in ("skills", "scripts", "shared"):
        shutil.copytree(REPO_ROOT / sub, tmp_path / sub)
    shutil.copy(REPO_ROOT / "plugin-version.json", tmp_path / "plugin-version.json")
    (tmp_path / "skills" / "stories-init" / "SKILL.md").unlink()
    result = subprocess.run(
        [sys.executable, str(tmp_path / "scripts" / "build-plugin")],
        capture_output=True,
        text=True,
        cwd=tmp_path,
    )
    assert result.returncode != 0
    assert "stories-init" in (result.stderr + result.stdout)
