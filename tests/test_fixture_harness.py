"""Tests for the fixture-driven functional test harness.

Exercises the helpers in tests/helpers.py and verifies each committed
fixture is structurally what its name claims. Also exercises the
script-runner against the storystore skill scripts that exist today and
treats not-yet-implemented scripts as deferred (returncode 127).
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

import pytest

from tests.helpers import (
    FIXTURE_NAMES,
    REPO_ROOT,
    ScriptResult,
    assert_json_keys,
    assert_markdown_contains,
    copy_fixture,
    count_story_files,
    fixture_path,
    run_skill_script,
)


# ---- fixture presence ------------------------------------------------------


def test_all_named_fixtures_exist():
    for name in FIXTURE_NAMES:
        path = fixture_path(name)
        assert path.is_dir(), f"missing fixture dir: {path}"
        assert (path / "README.md").exists(), f"{name} missing README.md"


def test_empty_fixture_has_no_stories_dir():
    assert not (fixture_path("empty") / "docs" / "stories").exists()


def test_ts_cli_fixture_has_two_stories():
    assert count_story_files(fixture_path("ts-cli") / "docs" / "stories") == 2


def test_http_api_fixture_evidence_resolves():
    """drift-free fixture: declared test path actually exists."""
    root = fixture_path("http-api")
    assert (root / "tests" / "test_widgets.py").exists()


def test_docs_heavy_fixture_has_many_stories():
    assert count_story_files(fixture_path("docs-heavy") / "docs" / "stories") >= 5


def test_malformed_story_fixture_has_nested_mapping():
    text = (
        fixture_path("malformed-story")
        / "docs"
        / "stories"
        / "broken-frontmatter.md"
    ).read_text()
    assert "nested:" in text and "  not: allowed" in text


def test_drift_fixture_evidence_does_not_resolve():
    root = fixture_path("drift")
    story = (root / "docs" / "stories" / "orphaned-evidence.md").read_text()
    assert "tests/test_missing.py" in story
    assert not (root / "tests" / "test_missing.py").exists()


# ---- copy_fixture ----------------------------------------------------------


@pytest.fixture
def tmp_workdir():
    d = Path(tempfile.mkdtemp(prefix="storystore-harness-"))
    yield d
    shutil.rmtree(d, ignore_errors=True)


def test_copy_fixture_creates_writable_copy(tmp_workdir):
    dest = copy_fixture("ts-cli", tmp_workdir / "ts-cli")
    assert dest.is_dir()
    # Mutating the copy must not affect the source fixture.
    new = dest / "docs" / "stories" / "added-by-test.md"
    new.write_text("---\nslug: x\n---\n# x\n")
    assert new.exists()
    assert not (
        fixture_path("ts-cli") / "docs" / "stories" / "added-by-test.md"
    ).exists()


def test_copy_fixture_default_temp_dir(tmp_workdir):
    dest = copy_fixture("empty", tmp_workdir / "empty")
    assert (dest / "README.md").exists()
    assert dest != fixture_path("empty")


def test_copy_fixture_unknown_raises():
    with pytest.raises(FileNotFoundError):
        copy_fixture("does-not-exist")


# ---- run_skill_script ------------------------------------------------------


def test_run_skill_script_missing_returns_127(tmp_workdir):
    fx = copy_fixture("empty", tmp_workdir / "empty")
    result = run_skill_script("skills/stories-init/scripts/init.py", fx)
    assert isinstance(result, ScriptResult)
    assert result.returncode == 127
    assert "not found" in result.stderr


def test_run_skill_script_runs_existing_script(tmp_workdir):
    """build-plugin exists today; invoke its --help to prove the runner works.

    build-plugin doesn't take --repo-root, so call with a custom flag set to
    --help and confirm exit 0 and recognizable stdout.
    """
    fx = copy_fixture("empty", tmp_workdir / "empty")
    result = run_skill_script(
        "scripts/build-plugin", fx, repo_root_flag="--help"
    )
    # build-plugin --help prints usage and exits 0.
    assert result.returncode == 0, result.stderr
    assert "build-plugin" in result.stdout


# ---- assertion helpers -----------------------------------------------------


def test_assert_json_keys_passes_and_fails():
    payload = {"fresh_init": True, "stories_dir": "docs/stories"}
    assert_json_keys(payload, ["fresh_init"])
    with pytest.raises(AssertionError, match="missing JSON keys"):
        assert_json_keys(payload, ["nope"])
    with pytest.raises(AssertionError, match="expected JSON object"):
        assert_json_keys([1, 2], ["a"])


def test_assert_markdown_contains_passes_and_fails():
    md = "# Title\n\nSome body with a **bold** fragment."
    assert_markdown_contains(md, "# Title", "**bold**")
    with pytest.raises(AssertionError, match="not found"):
        assert_markdown_contains(md, "missing fragment")


# ---- scenarios across fixtures (deferred-script aware) --------------------


@pytest.mark.parametrize("fx_name", ["empty", "malformed-story", "ts-cli"])
def test_init_script_against_each_fixture_is_deferred(fx_name, tmp_workdir):
    """When stories-init/scripts/init.py lands, this asserts shape per fixture.

    Until then, the runner reports 127 and the test documents the contract:
    - empty: JSON should include fresh_init: true
    - ts-cli: JSON should include fresh_init: false (stories/ already exists)
    - malformed-story: should still report fresh_init: false; parse errors
      surface from later loader skills, not init.
    """
    fx = copy_fixture(fx_name, tmp_workdir / fx_name)
    result = run_skill_script("skills/stories-init/scripts/init.py", fx)
    if result.returncode == 127:
        pytest.skip("stories-init script not yet implemented")
    payload = result.json()
    assert_json_keys(payload, ["fresh_init"])
    expected_fresh = fx_name == "empty"
    assert payload["fresh_init"] is expected_fresh
