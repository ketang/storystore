"""Tests for shared/impact_check.py."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
IMPACT_PATH = REPO_ROOT / "shared" / "impact_check.py"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


impact = _load("storystore_impact_check", IMPACT_PATH)


# --------------------------------------------------------------------------- #
# Fixture helpers
# --------------------------------------------------------------------------- #


def _write_story(stories_dir: Path, slug: str, **kw) -> Path:
    title = kw.get("title", slug.replace("-", " ").title())
    status = kw.get("status", "active")
    authority = kw.get("authority", "accepted")
    resistance = kw.get("change_resistance", "medium")
    intent = kw.get("intent", "Users do a thing for a reason.")
    tests = kw.get("tests", [])
    surfaces = kw.get("surfaces", [])
    docs = kw.get("docs", [])
    extra_sections = kw.get("extra_sections", {})

    evidence_lines: list[str] = ["## Evidence"]
    if tests:
        evidence_lines.append("### Tests")
        evidence_lines.extend(f"- `{t}`" for t in tests)
    if surfaces:
        evidence_lines.append("### Surface")
        evidence_lines.extend(f"- `{s}`" for s in surfaces)
    if docs:
        evidence_lines.append("### Docs")
        evidence_lines.extend(f"- `{d}`" for d in docs)

    extra_block = "\n".join(
        f"## {section}\n{body}\n" for section, body in extra_sections.items()
    )

    text = f"""---
title: {title}
slug: {slug}
status: {status}
authority: {authority}
change_resistance: {resistance}
tests_applicable: {"true" if tests else "false"}
---

# {title}

## Intent
{intent}

{extra_block}
{"\n".join(evidence_lines)}
"""
    path = stories_dir / f"{slug}.md"
    path.write_text(text, encoding="utf-8")
    return path


def _make_repo(tmp_path: Path) -> Path:
    (tmp_path / "docs" / "stories").mkdir(parents=True)
    return tmp_path


def _run(repo_root: Path, *args: str) -> tuple[int, dict, str]:
    proc = subprocess.run(
        [sys.executable, str(IMPACT_PATH), "--repo-root", str(repo_root), *args],
        capture_output=True,
        text=True,
    )
    parsed: dict = {}
    if proc.stdout.strip():
        try:
            parsed = json.loads(proc.stdout)
        except json.JSONDecodeError:
            pass
    return proc.returncode, parsed, proc.stderr


# --------------------------------------------------------------------------- #
# Tests
# --------------------------------------------------------------------------- #


def test_file_match_via_test_evidence(tmp_path):
    repo = _make_repo(tmp_path)
    (repo / "tests").mkdir()
    (repo / "tests" / "login.spec.ts").write_text("// t\n", encoding="utf-8")
    _write_story(
        repo / "docs" / "stories",
        "login",
        tests=["tests/login.spec.ts"],
    )

    code, out, _ = _run(repo, "--file", "tests/login.spec.ts")
    assert code == 0
    slugs = [m["slug"] for m in out["matches"]]
    assert slugs == ["login"]
    assert any("file: tests/login.spec.ts" in r for r in out["matches"][0]["match_reasons"])


def test_surface_match(tmp_path):
    repo = _make_repo(tmp_path)
    _write_story(
        repo / "docs" / "stories",
        "login",
        surfaces=["cli: login"],
    )
    _write_story(
        repo / "docs" / "stories",
        "logout",
        surfaces=["cli: logout"],
    )

    code, out, _ = _run(repo, "--surface", "cli: login")
    assert code == 0
    slugs = [m["slug"] for m in out["matches"]]
    assert slugs == ["login"]
    assert "surface: cli: login" in out["matches"][0]["match_reasons"]


def test_description_match_against_title_and_intent(tmp_path):
    repo = _make_repo(tmp_path)
    _write_story(
        repo / "docs" / "stories",
        "authenticated-login",
        title="Authenticated Login",
        intent="Users sign in with credentials so the system attributes actions.",
    )
    _write_story(
        repo / "docs" / "stories",
        "search",
        title="Search",
        intent="Users find items quickly.",
    )

    code, out, _ = _run(repo, "--description", "tighten credentials and login")
    assert code == 0
    slugs = [m["slug"] for m in out["matches"]]
    assert slugs == ["authenticated-login"]
    assert any("description" in r for r in out["matches"][0]["match_reasons"])


def test_or_combination_across_dimensions(tmp_path):
    repo = _make_repo(tmp_path)
    _write_story(repo / "docs" / "stories", "a", surfaces=["cli: alpha"])
    _write_story(
        repo / "docs" / "stories",
        "b",
        intent="The beta workflow handles checkout.",
    )
    _write_story(repo / "docs" / "stories", "c", surfaces=["cli: gamma"])

    code, out, _ = _run(
        repo,
        "--surface", "cli: alpha",
        "--description", "beta workflow",
    )
    assert code == 0
    slugs = sorted(m["slug"] for m in out["matches"])
    assert slugs == ["a", "b"]


def test_repeated_description_exits_2(tmp_path):
    repo = _make_repo(tmp_path)
    _write_story(repo / "docs" / "stories", "x", surfaces=["cli: x"])
    code, _, err = _run(repo, "--description", "one", "--description", "two")
    assert code == 2
    assert "description" in err.lower()


def test_observed_authority_story_is_matched_and_flagged(tmp_path):
    repo = _make_repo(tmp_path)
    _write_story(
        repo / "docs" / "stories",
        "observed-thing",
        authority="observed",
        change_resistance="low",
        surfaces=["cli: observed"],
    )

    code, out, _ = _run(repo, "--surface", "cli: observed")
    assert code == 0
    assert len(out["matches"]) == 1
    m = out["matches"][0]
    assert m["authority"] == "observed"
    assert any("observed-authority" in f for f in m["flags"])


def test_deprecated_story_is_matched_and_flagged(tmp_path):
    repo = _make_repo(tmp_path)
    _write_story(
        repo / "docs" / "stories",
        "old-feature",
        status="deprecated",
        surfaces=["cli: old"],
    )

    code, out, _ = _run(repo, "--surface", "cli: old")
    assert code == 0
    assert len(out["matches"]) == 1
    assert any("deprecated" in f for f in out["matches"][0]["flags"])


def test_draft_story_is_matched_and_flagged(tmp_path):
    repo = _make_repo(tmp_path)
    _write_story(
        repo / "docs" / "stories",
        "wip-feature",
        status="draft",
        surfaces=["cli: wip"],
    )

    code, out, _ = _run(repo, "--surface", "cli: wip")
    assert code == 0
    assert any("draft" in f for f in out["matches"][0]["flags"])


def test_no_match_story_omitted(tmp_path):
    repo = _make_repo(tmp_path)
    _write_story(repo / "docs" / "stories", "alpha", surfaces=["cli: alpha"])
    _write_story(repo / "docs" / "stories", "beta", surfaces=["cli: beta"])

    code, out, _ = _run(repo, "--surface", "cli: alpha")
    assert code == 0
    slugs = [m["slug"] for m in out["matches"]]
    assert slugs == ["alpha"]


def test_performance_block_present(tmp_path):
    repo = _make_repo(tmp_path)
    _write_story(repo / "docs" / "stories", "alpha", surfaces=["cli: alpha"])
    _write_story(repo / "docs" / "stories", "beta", surfaces=["cli: beta"])

    code, out, _ = _run(repo, "--surface", "cli: alpha")
    assert code == 0
    perf = out["performance"]
    assert "duration_ms" in perf
    assert isinstance(perf["duration_ms"], int)
    assert perf["stories_scanned"] == 2


def test_file_match_via_source_defining_surface(tmp_path):
    """A --file pointing at the source file that defines a story's claimed
    surface should match that story even without explicit test evidence."""
    repo = _make_repo(tmp_path)
    src = repo / "src"
    src.mkdir()
    (src / "cli.ts").write_text(
        'program.command("login").description("log in");\n',
        encoding="utf-8",
    )
    (repo / "package.json").write_text('{"name": "x"}', encoding="utf-8")
    _write_story(
        repo / "docs" / "stories",
        "login",
        surfaces=["cli: login"],
    )

    code, out, _ = _run(repo, "--file", "src/cli.ts")
    assert code == 0
    slugs = [m["slug"] for m in out["matches"]]
    assert slugs == ["login"]


def test_missing_stories_dir_yields_empty_matches(tmp_path):
    code, out, _ = _run(tmp_path, "--surface", "cli: x")
    assert code == 0
    assert out["matches"] == []
    assert out["performance"]["stories_scanned"] == 0


def test_perf_warn_threshold_zero_disables_warning(tmp_path):
    repo = _make_repo(tmp_path)
    _write_story(repo / "docs" / "stories", "x", surfaces=["cli: x"])
    code, _, err = _run(repo, "--surface", "cli: x", "--perf-warn-ms", "0")
    assert code == 0
    assert "exceeded threshold" not in err
