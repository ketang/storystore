"""Tests for shared/impact_trigger.py — the mechanical stories-impact trigger.

Covers the ss-wwr acceptance criteria:
- A fixture story citing ``web/src/pages/Closet.tsx`` warns when an edit/rename
  touches that path, naming the story.
- Prefix/glob matching: evidence ref ``web/src/pages/`` matches an edit to
  ``web/src/pages/Closet.test.tsx``.
- Fails open: a missing/corrupt corpus never blocks an unrelated edit.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
TRIGGER_PATH = REPO_ROOT / "shared" / "impact_trigger.py"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


trigger = _load("storystore_impact_trigger", TRIGGER_PATH)


# --------------------------------------------------------------------------- #
# Fixture helpers
# --------------------------------------------------------------------------- #


def _make_repo(tmp_path: Path) -> Path:
    (tmp_path / "docs" / "stories").mkdir(parents=True)
    return tmp_path


def _write_story(
    repo: Path,
    slug: str,
    *,
    title: str | None = None,
    status: str = "active",
    authority: str = "accepted",
    change_resistance: str = "high",
    intent: str = "Users browse their closet so they can plan outfits.",
    tests: list[str] | None = None,
    docs: list[str] | None = None,
    surfaces: list[str] | None = None,
) -> Path:
    title = title or slug.replace("-", " ").title()
    evidence: list[str] = ["## Evidence"]
    if tests:
        evidence.append("### Tests")
        evidence.extend(f"- `{t}`" for t in tests)
    if docs:
        evidence.append("### Docs")
        evidence.extend(f"- `{d}`" for d in docs)
    if surfaces:
        evidence.append("### Surface")
        evidence.extend(f"- `{s}`" for s in surfaces)
    text = f"""---
title: {title}
slug: {slug}
status: {status}
authority: {authority}
change_resistance: {change_resistance}
---

# {title}

## Intent
{intent}

{chr(10).join(evidence)}
"""
    path = repo / "docs" / "stories" / f"{slug}.md"
    path.write_text(text, encoding="utf-8")
    return path


def _run(repo: Path, *args: str, stdin: str | None = None):
    proc = subprocess.run(
        [sys.executable, str(TRIGGER_PATH), "--repo-root", str(repo), *args],
        capture_output=True,
        text=True,
        input=stdin,
    )
    return proc


# --------------------------------------------------------------------------- #
# Acceptance: warning fires on a match, names the story
# --------------------------------------------------------------------------- #


def test_exact_path_match_warns_naming_story(tmp_path):
    repo = _make_repo(tmp_path)
    _write_story(repo, "closet-page", docs=["web/src/pages/Closet.tsx"])

    proc = _run(repo, "web/src/pages/Closet.tsx")
    assert proc.returncode == 0
    assert "closet-page" in proc.stderr
    assert "stories-impact" in proc.stderr


def test_rename_touching_cited_path_warns(tmp_path):
    """A rename touches the old path; passing it surfaces the story."""
    repo = _make_repo(tmp_path)
    _write_story(repo, "closet-page", docs=["web/src/pages/Closet.tsx"])

    # Rename: old path is the cited evidence, new path is unrelated.
    proc = _run(repo, "web/src/pages/Closet.tsx", "web/src/pages/MyStuff.tsx")
    assert proc.returncode == 0
    assert "closet-page" in proc.stderr


def test_prefix_match_directory_ref(tmp_path):
    """Evidence ref `web/src/pages/` matches `web/src/pages/Closet.test.tsx`."""
    repo = _make_repo(tmp_path)
    _write_story(repo, "pages-area", tests=["web/src/pages/"])

    proc = _run(repo, "web/src/pages/Closet.test.tsx", "--json")
    assert proc.returncode == 0
    out = json.loads(proc.stdout)
    slugs = [s["slug"] for s in out["affected"]]
    assert slugs == ["pages-area"]
    assert "prefix" in out["affected"][0]["match_reasons"][0]


def test_prefix_match_without_trailing_slash(tmp_path):
    repo = _make_repo(tmp_path)
    _write_story(repo, "pages-area", tests=["web/src/pages"])

    proc = _run(repo, "web/src/pages/Closet.test.tsx", "--json")
    out = json.loads(proc.stdout)
    assert [s["slug"] for s in out["affected"]] == ["pages-area"]


def test_glob_match(tmp_path):
    repo = _make_repo(tmp_path)
    _write_story(repo, "all-pages", tests=["web/src/pages/*.tsx"])

    proc = _run(repo, "web/src/pages/Closet.tsx", "--json")
    out = json.loads(proc.stdout)
    assert [s["slug"] for s in out["affected"]] == ["all-pages"]
    assert "glob" in out["affected"][0]["match_reasons"][0]


# --------------------------------------------------------------------------- #
# Silence on non-match
# --------------------------------------------------------------------------- #


def test_no_match_is_silent(tmp_path):
    repo = _make_repo(tmp_path)
    _write_story(repo, "closet-page", docs=["web/src/pages/Closet.tsx"])

    proc = _run(repo, "web/src/components/Button.tsx")
    assert proc.returncode == 0
    assert proc.stderr.strip() == ""
    assert proc.stdout.strip() == ""


def test_prefix_does_not_over_match_sibling(tmp_path):
    """`web/src/pages` must not match `web/src/pages-archive/Old.tsx`."""
    repo = _make_repo(tmp_path)
    _write_story(repo, "pages-area", tests=["web/src/pages"])

    proc = _run(repo, "web/src/pages-archive/Old.tsx", "--json")
    out = json.loads(proc.stdout)
    assert out["affected"] == []


def test_surface_ref_does_not_match_paths(tmp_path):
    """Non-path evidence refs (e.g. `cli: login`) never match file paths."""
    repo = _make_repo(tmp_path)
    _write_story(repo, "login", surfaces=["cli: login"])

    proc = _run(repo, "web/src/pages/Closet.tsx", "--json")
    out = json.loads(proc.stdout)
    assert out["affected"] == []


# --------------------------------------------------------------------------- #
# Fail open
# --------------------------------------------------------------------------- #


def test_missing_stories_dir_fails_open(tmp_path):
    # No docs/stories/ at all.
    proc = _run(tmp_path, "web/src/pages/Closet.tsx", "--json")
    assert proc.returncode == 0
    assert json.loads(proc.stdout)["affected"] == []


def test_corrupt_story_is_skipped_not_fatal(tmp_path):
    repo = _make_repo(tmp_path)
    _write_story(repo, "closet-page", docs=["web/src/pages/Closet.tsx"])
    # A garbage file in the corpus must not prevent the valid match.
    (repo / "docs" / "stories" / "broken.md").write_text(
        "\x00\x00 not --- valid : frontmatter [[[", encoding="utf-8"
    )

    proc = _run(repo, "web/src/pages/Closet.tsx", "--json")
    assert proc.returncode == 0
    slugs = [s["slug"] for s in json.loads(proc.stdout)["affected"]]
    assert "closet-page" in slugs


def test_unreadable_repo_root_fails_open(tmp_path):
    # Point at a path that is not a directory; must not crash or block.
    bogus = tmp_path / "does-not-exist"
    proc = _run(bogus, "web/src/pages/Closet.tsx", "--json")
    assert proc.returncode == 0
    assert json.loads(proc.stdout)["affected"] == []


def test_exit_code_flag_blocks_only_on_match(tmp_path):
    repo = _make_repo(tmp_path)
    _write_story(repo, "closet-page", docs=["web/src/pages/Closet.tsx"])

    hit = _run(repo, "web/src/pages/Closet.tsx", "--exit-code")
    assert hit.returncode == 1

    miss = _run(repo, "web/src/other.tsx", "--exit-code")
    assert miss.returncode == 0


def test_exit_code_flag_still_fails_open_on_missing_corpus(tmp_path):
    # --exit-code must NOT turn a broken/absent corpus into a block.
    proc = _run(tmp_path, "web/src/pages/Closet.tsx", "--exit-code")
    assert proc.returncode == 0


# --------------------------------------------------------------------------- #
# Input channels
# --------------------------------------------------------------------------- #


def test_paths_from_stdin(tmp_path):
    repo = _make_repo(tmp_path)
    _write_story(repo, "closet-page", docs=["web/src/pages/Closet.tsx"])

    proc = _run(repo, "--json", stdin="web/src/pages/Closet.tsx\n")
    out = json.loads(proc.stdout)
    assert [s["slug"] for s in out["affected"]] == ["closet-page"]


def test_absolute_path_normalized_to_repo_relative(tmp_path):
    repo = _make_repo(tmp_path)
    _write_story(repo, "closet-page", docs=["web/src/pages/Closet.tsx"])
    abs_path = str(repo / "web" / "src" / "pages" / "Closet.tsx")

    proc = _run(repo, abs_path, "--json")
    out = json.loads(proc.stdout)
    assert [s["slug"] for s in out["affected"]] == ["closet-page"]


def test_path_outside_repo_is_ignored(tmp_path):
    repo = _make_repo(tmp_path)
    _write_story(repo, "closet-page", docs=["web/src/pages/Closet.tsx"])

    proc = _run(repo, "/etc/passwd", "--json")
    out = json.loads(proc.stdout)
    assert out["affected"] == []


# --------------------------------------------------------------------------- #
# Hook payload mode
# --------------------------------------------------------------------------- #


def test_hook_mode_edit_payload(tmp_path):
    repo = _make_repo(tmp_path)
    _write_story(repo, "closet-page", docs=["web/src/pages/Closet.tsx"])
    payload = {
        "tool_name": "Edit",
        "tool_input": {"file_path": str(repo / "web/src/pages/Closet.tsx")},
    }

    proc = _run(repo, "--hook", "--json", stdin=json.dumps(payload))
    out = json.loads(proc.stdout)
    assert [s["slug"] for s in out["affected"]] == ["closet-page"]


def test_hook_mode_bash_rename_payload(tmp_path):
    """The motivating case: `git mv` of a cited path must fire a warning."""
    repo = _make_repo(tmp_path)
    _write_story(repo, "closet-page", docs=["web/src/pages/Closet.tsx"])
    payload = {
        "tool_name": "Bash",
        "tool_input": {
            "command": "git mv web/src/pages/Closet.tsx web/src/pages/MyStuff.tsx"
        },
    }

    proc = _run(repo, "--hook", stdin=json.dumps(payload))
    assert proc.returncode == 0
    assert "closet-page" in proc.stderr


def test_hook_mode_malformed_payload_fails_open(tmp_path):
    repo = _make_repo(tmp_path)
    _write_story(repo, "closet-page", docs=["web/src/pages/Closet.tsx"])

    proc = _run(repo, "--hook", stdin="not json at all {{{")
    assert proc.returncode == 0
    assert proc.stderr.strip() == ""


def test_hook_mode_unrelated_edit_is_silent(tmp_path):
    repo = _make_repo(tmp_path)
    _write_story(repo, "closet-page", docs=["web/src/pages/Closet.tsx"])
    payload = {
        "tool_name": "Edit",
        "tool_input": {"file_path": str(repo / "README.md")},
    }

    proc = _run(repo, "--hook", stdin=json.dumps(payload))
    assert proc.returncode == 0
    assert proc.stderr.strip() == ""


# --------------------------------------------------------------------------- #
# Unit-level matching
# --------------------------------------------------------------------------- #


def test_match_ref_unit():
    assert trigger.match_ref("a/b/c.tsx", "a/b/c.tsx") == "exact"
    assert trigger.match_ref("a/b/c.tsx", "a/b/") == "prefix"
    assert trigger.match_ref("a/b/c.tsx", "a/b") == "prefix"
    assert trigger.match_ref("a/b/c.tsx", "a/b/*.tsx") == "glob"
    assert trigger.match_ref("a/b/c.tsx", "a/x/") is None
    assert trigger.match_ref("a/b-archive/c.tsx", "a/b") is None
    assert trigger.match_ref("a/b/c.tsx", "cli: login") is None
