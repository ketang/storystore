"""Tests for shared/write_story.py — story file writer and INDEX regeneration."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest


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


def _init_repo(tmp_path: Path) -> Path:
    stories = tmp_path / "docs" / "stories"
    stories.mkdir(parents=True)
    (stories / "README.md").write_text("# stories\n")
    (stories / "INDEX.md").write_text("")
    return tmp_path


def _base_payload(**overrides):
    payload = {
        "title": "Authenticated Login",
        "slug": "authenticated-login",
        "intent": "Users sign in so the system can attribute actions.",
        "story": "A user signs in with credentials before using private commands.",
        "expected_behavior": "Valid credentials establish an authenticated session.",
        "boundaries": "Does not cover password reset.",
        "auditable_claims": ["The login command exists."],
        "evidence": {
            "tests": ["tests/login.e2e.test.ts"],
            "surface": ["cli: login"],
            "docs": ["README.md"],
        },
    }
    payload.update(overrides)
    return payload


def _run(repo: Path, payload: dict, mode: str = "--interview", *, check: bool = False):
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--repo-root", str(repo), mode],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        check=check,
    )


def test_interview_defaults_written_in_frontmatter(tmp_path):
    repo = _init_repo(tmp_path)
    result = _run(repo, _base_payload(), "--interview")
    assert result.returncode == 0, result.stderr
    out = json.loads(result.stdout)
    assert out["path"] == "docs/stories/authenticated-login.md"
    assert out["index_updated"] is True
    story_path = repo / "docs" / "stories" / "authenticated-login.md"
    text = story_path.read_text()
    assert text.startswith("---\nschema_version: 1\n")
    data = lib.parse_frontmatter(text)
    assert data["schema_version"] == 1
    assert data["authority"] == "accepted"
    assert data["change_resistance"] == "medium"
    assert data["status"] == "draft"
    assert data["locked_sections"] == ["Intent"]


def test_observed_defaults_written_in_frontmatter(tmp_path):
    repo = _init_repo(tmp_path)
    result = _run(repo, _base_payload(), "--observed")
    assert result.returncode == 0, result.stderr
    text = (repo / "docs" / "stories" / "authenticated-login.md").read_text()
    data = lib.parse_frontmatter(text)
    assert data["authority"] == "observed"
    assert data["change_resistance"] == "low"
    assert data["status"] == "draft"
    assert data["locked_sections"] == []


def test_explicit_payload_fields_override_defaults(tmp_path):
    repo = _init_repo(tmp_path)
    payload = _base_payload(
        status="active",
        authority="accepted",
        change_resistance="high",
        locked_sections=["Intent", "Boundaries"],
    )
    result = _run(repo, payload, "--interview")
    assert result.returncode == 0, result.stderr
    text = (repo / "docs" / "stories" / "authenticated-login.md").read_text()
    data = lib.parse_frontmatter(text)
    assert data["status"] == "active"
    assert data["change_resistance"] == "high"
    assert data["locked_sections"] == ["Intent", "Boundaries"]


def test_observed_plus_high_resistance_exits_3(tmp_path):
    repo = _init_repo(tmp_path)
    payload = _base_payload(authority="observed", change_resistance="high")
    result = _run(repo, payload, "--observed")
    assert result.returncode == 3
    assert "validity matrix" in result.stderr.lower() or "observed" in result.stderr.lower()
    assert not (repo / "docs" / "stories" / "authenticated-login.md").exists()


def test_observed_plus_immutable_resistance_exits_3(tmp_path):
    repo = _init_repo(tmp_path)
    payload = _base_payload(authority="observed", change_resistance="immutable")
    result = _run(repo, payload, "--observed")
    assert result.returncode == 3


def test_slug_with_one_word_exits_2(tmp_path):
    repo = _init_repo(tmp_path)
    payload = _base_payload(slug="login")
    result = _run(repo, payload, "--interview")
    assert result.returncode == 2
    assert not (repo / "docs" / "stories" / "login.md").exists()


def test_slug_with_three_words_emits_nag_but_succeeds(tmp_path):
    repo = _init_repo(tmp_path)
    payload = _base_payload(slug="user-can-login")
    result = _run(repo, payload, "--interview")
    assert result.returncode == 0, result.stderr
    assert "STORYSTORE_SLUG_NAG" in result.stderr
    assert (repo / "docs" / "stories" / "user-can-login.md").exists()


def test_slug_with_nine_words_emits_nag(tmp_path):
    repo = _init_repo(tmp_path)
    slug = "-".join(["word"] + [f"w{i}" for i in range(8)])  # 9 words
    payload = _base_payload(slug=slug)
    result = _run(repo, payload, "--interview")
    assert result.returncode == 0, result.stderr
    assert "STORYSTORE_SLUG_NAG" in result.stderr


def test_overwrite_existing_story_exits_2(tmp_path):
    repo = _init_repo(tmp_path)
    payload = _base_payload()
    first = _run(repo, payload, "--interview")
    assert first.returncode == 0, first.stderr
    second = _run(repo, payload, "--interview")
    assert second.returncode == 2
    assert "exist" in second.stderr.lower() or "already" in second.stderr.lower()


def test_regenerates_index_md_slug_sorted(tmp_path):
    repo = _init_repo(tmp_path)
    payload_b = _base_payload(title="Beta Thing", slug="beta-thing")
    payload_a = _base_payload(title="Alpha Thing", slug="alpha-thing")
    payload_c = _base_payload(title="Gamma Thing", slug="gamma-thing")
    assert _run(repo, payload_b, "--interview").returncode == 0
    assert _run(repo, payload_a, "--interview").returncode == 0
    assert _run(repo, payload_c, "--interview").returncode == 0
    index = (repo / "docs" / "stories" / "INDEX.md").read_text()
    # slug order: alpha, beta, gamma
    a_idx = index.find("alpha-thing")
    b_idx = index.find("beta-thing")
    c_idx = index.find("gamma-thing")
    assert -1 < a_idx < b_idx < c_idx
    assert "3 stories" in index
    assert "# Intent Story Index" in index


def test_index_entry_includes_status_authority_resistance(tmp_path):
    repo = _init_repo(tmp_path)
    payload = _base_payload(
        title="Authenticated Login",
        slug="authenticated-login",
        status="active",
        change_resistance="high",
    )
    result = _run(repo, payload, "--interview")
    assert result.returncode == 0, result.stderr
    index = (repo / "docs" / "stories" / "INDEX.md").read_text()
    assert "[authenticated-login](authenticated-login.md)" in index
    assert "Authenticated Login" in index
    assert "(active, accepted, high)" in index


def test_written_story_parses_with_storystore_lib(tmp_path):
    repo = _init_repo(tmp_path)
    result = _run(repo, _base_payload(), "--interview")
    assert result.returncode == 0, result.stderr
    story_path = repo / "docs" / "stories" / "authenticated-login.md"
    parsed = lib.parse_story(story_path)
    assert parsed.title == "Authenticated Login"
    assert parsed.slug == "authenticated-login"
    assert parsed.authority == "accepted"
    assert parsed.evidence_tests == ["tests/login.e2e.test.ts"]
    assert parsed.evidence_surface == ["cli: login"]
    assert parsed.evidence_docs == ["README.md"]
    assert "Users sign in" in parsed.sections["Intent"]
