"""Tests for shared/inventory.py — language detection, surface extraction,
and evidence resolution."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
LIB_PATH = REPO_ROOT / "shared" / "storystore_lib.py"
INV_PATH = REPO_ROOT / "shared" / "inventory.py"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# Order matters: inventory.py is independent, but tests build Story objects
# from storystore_lib for resolve_evidence.
lib = _load("storystore_lib", LIB_PATH)
inv = _load("storystore_inventory", INV_PATH)


# --------------------------------------------------------------------------- #
# Language detection
# --------------------------------------------------------------------------- #


def test_detect_languages_typescript_from_package_json(tmp_path):
    (tmp_path / "package.json").write_text('{"name": "x"}', encoding="utf-8")
    result = inv.detect_languages(tmp_path)
    assert "typescript" in result["detected"]
    assert "javascript" in result["detected"]
    assert sorted(result["extracted"]) == ["javascript", "typescript"]


def test_detect_languages_multiple_markers(tmp_path):
    (tmp_path / "package.json").write_text("{}", encoding="utf-8")
    (tmp_path / "go.mod").write_text("module x\n", encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    result = inv.detect_languages(tmp_path)
    assert set(result["detected"]) >= {"typescript", "javascript", "go", "python"}
    # Only typescript/javascript are in the bundled-extractor set.
    assert set(result["extracted"]) == {"typescript", "javascript"}


def test_detect_languages_walks_depth_at_most_2(tmp_path):
    deep = tmp_path / "a" / "b" / "c"
    deep.mkdir(parents=True)
    # Depth 3 marker should NOT be detected.
    (deep / "package.json").write_text("{}", encoding="utf-8")
    result = inv.detect_languages(tmp_path)
    assert result["detected"] == []

    # Depth 2 marker IS detected.
    (tmp_path / "a" / "b" / "package.json").write_text("{}", encoding="utf-8")
    result = inv.detect_languages(tmp_path)
    assert "typescript" in result["detected"]


def test_detect_languages_skips_default_skip_dirs(tmp_path):
    nm = tmp_path / "node_modules" / "pkg"
    nm.mkdir(parents=True)
    (nm / "go.mod").write_text("module x\n", encoding="utf-8")
    result = inv.detect_languages(tmp_path)
    assert "go" not in result["detected"]


def test_detect_languages_empty_for_missing_dir(tmp_path):
    result = inv.detect_languages(tmp_path / "nope")
    assert result == {"detected": [], "extracted": []}


# --------------------------------------------------------------------------- #
# build_inventory
# --------------------------------------------------------------------------- #


def _ts_cli_fixture(root: Path) -> None:
    (root / "package.json").write_text(
        '{"name": "mytool", "bin": {"mytool": "dist/cli.js"}, '
        '"exports": {".": "./dist/index.js", "./extra": "./dist/extra.js"}}',
        encoding="utf-8",
    )
    src = root / "src"
    src.mkdir(parents=True, exist_ok=True)
    (src / "cli.ts").write_text(
        """
import { Command } from "commander";
const program = new Command();
program.command("login").description("log in");
program.command("logout");
""",
        encoding="utf-8",
    )


def test_build_inventory_extracts_cli_commands(tmp_path):
    _ts_cli_fixture(tmp_path)
    result = inv.build_inventory(tmp_path)
    cli = [s for s in result["surfaces"] if s["kind"] == "cli-command"]
    names = sorted(s["name"] for s in cli)
    assert names == ["login", "logout"]
    assert all(s["source"] == "src/cli.ts" for s in cli)


def test_build_inventory_extracts_http_routes(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "routes.ts").write_text(
        """
import express from "express";
const app = express();
app.get("/users", handler);
app.post('/users', handler);
router.delete("/users/:id", handler);
""",
        encoding="utf-8",
    )
    result = inv.build_inventory(tmp_path)
    routes = [s for s in result["surfaces"] if s["kind"] == "http-route"]
    pairs = sorted((r["method"], r["path"]) for r in routes)
    assert pairs == [("DELETE", "/users/:id"), ("GET", "/users"), ("POST", "/users")]


def test_build_inventory_extracts_package_bin_and_exports(tmp_path):
    _ts_cli_fixture(tmp_path)
    result = inv.build_inventory(tmp_path)
    bins = [s for s in result["surfaces"] if s["kind"] == "bin"]
    exports = [s for s in result["surfaces"] if s["kind"] == "exports"]
    assert [b["name"] for b in bins] == ["mytool"]
    assert sorted(e["name"] for e in exports) == [".", "./extra"]
    assert all(b["source"] == "package.json" for b in bins)


def test_build_inventory_extracts_string_bin(tmp_path):
    (tmp_path / "package.json").write_text(
        '{"name": "shorty", "bin": "./shorty.js"}',
        encoding="utf-8",
    )
    result = inv.build_inventory(tmp_path)
    bins = [s for s in result["surfaces"] if s["kind"] == "bin"]
    assert [b["name"] for b in bins] == ["shorty"]


def test_build_inventory_extracts_test_names(tmp_path):
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "login.spec.ts").write_text(
        """
describe("login flow", () => {
  it("should login successfully", () => {});
  it('rejects bad credentials', () => {});
});
test("standalone test", () => {});
""",
        encoding="utf-8",
    )
    result = inv.build_inventory(tmp_path)
    names = sorted(s["name"] for s in result["surfaces"] if s["kind"] == "test")
    assert names == [
        "login flow",
        "rejects bad credentials",
        "should login successfully",
        "standalone test",
    ]


def test_build_inventory_extracts_readme_headings(tmp_path):
    (tmp_path / "README.md").write_text(
        "# Title\n\n## Authentication\n\nstuff\n\n### Login\nmore\n\n## Setup\n",
        encoding="utf-8",
    )
    result = inv.build_inventory(tmp_path)
    headings = [s for s in result["surfaces"] if s["kind"] == "heading"]
    assert {(h["text"], h["level"]) for h in headings} == {
        ("Authentication", 2),
        ("Login", 3),
        ("Setup", 2),
    }
    assert all(h["source"] == "README.md" for h in headings)


def test_build_inventory_skips_default_skip_dirs(tmp_path):
    nm = tmp_path / "node_modules" / "lib"
    nm.mkdir(parents=True)
    (nm / "thing.ts").write_text('program.command("phantom");', encoding="utf-8")
    src = tmp_path / "src"
    src.mkdir()
    (src / "cli.ts").write_text('program.command("real");', encoding="utf-8")
    result = inv.build_inventory(tmp_path)
    names = sorted(s["name"] for s in result["surfaces"] if s["kind"] == "cli-command")
    assert names == ["real"]


def test_build_inventory_include_dir_overrides_skip(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    (out / "generated.ts").write_text('program.command("gen");', encoding="utf-8")
    # Default: skipped.
    assert not any(
        s.get("name") == "gen" for s in inv.build_inventory(tmp_path)["surfaces"]
    )
    # Include-dir: surfaced.
    result = inv.build_inventory(tmp_path, include_dirs=["out"])
    assert any(s.get("name") == "gen" for s in result["surfaces"])


def test_build_inventory_source_root_narrows_walk(tmp_path):
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "cli.ts").write_text('program.command("inside");', encoding="utf-8")
    (tmp_path / "other.ts").write_text('program.command("outside");', encoding="utf-8")
    result = inv.build_inventory(tmp_path, source_root="app")
    names = sorted(s["name"] for s in result["surfaces"] if s["kind"] == "cli-command")
    assert names == ["inside"]


def test_build_inventory_includes_language_block(tmp_path):
    (tmp_path / "package.json").write_text("{}", encoding="utf-8")
    result = inv.build_inventory(tmp_path)
    assert "typescript" in result["languages"]["detected"]
    assert "typescript" in result["languages"]["extracted"]


# --------------------------------------------------------------------------- #
# resolve_evidence
# --------------------------------------------------------------------------- #


class _FakeStory:
    """Lightweight stand-in for Story dataclass — only the evidence fields matter."""

    def __init__(self, tests=(), surface=(), docs=()):
        self.evidence_tests = list(tests)
        self.evidence_surface = list(surface)
        self.evidence_docs = list(docs)


def test_resolve_evidence_resolves_exact_test_path(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "login.spec.ts").write_text("// test\n", encoding="utf-8")
    story = _FakeStory(tests=["tests/login.spec.ts"])
    out = inv.resolve_evidence(tmp_path, story)
    assert out["tests_resolved"] == ["tests/login.spec.ts"]
    assert out["tests_missing"] == []


def test_resolve_evidence_resolves_glob(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "a.spec.ts").write_text("// a\n", encoding="utf-8")
    (tmp_path / "tests" / "b.spec.ts").write_text("// b\n", encoding="utf-8")
    story = _FakeStory(tests=["tests/*.spec.ts"])
    out = inv.resolve_evidence(tmp_path, story)
    assert sorted(out["tests_resolved"]) == ["tests/a.spec.ts", "tests/b.spec.ts"]
    assert out["tests_missing"] == []


def test_resolve_evidence_reports_missing_test(tmp_path):
    story = _FakeStory(tests=["tests/nope.spec.ts", "tests/*.gone"])
    out = inv.resolve_evidence(tmp_path, story)
    assert out["tests_resolved"] == []
    assert sorted(out["tests_missing"]) == ["tests/*.gone", "tests/nope.spec.ts"]


def test_resolve_evidence_reports_missing_docs(tmp_path):
    (tmp_path / "README.md").write_text("readme\n", encoding="utf-8")
    story = _FakeStory(docs=["README.md", "MISSING.md"])
    out = inv.resolve_evidence(tmp_path, story)
    assert out["docs_resolved"] == ["README.md"]
    assert out["docs_missing"] == ["MISSING.md"]


def test_resolve_evidence_validates_surface_refs(tmp_path):
    story = _FakeStory(
        surface=[
            "cli: login",
            "route: GET /users",
            "route: get /users",  # lowercase method
            "bin: mytool",
            "exports: ./index",
            "test: foo bar",
            "unknown-prefix: x",
            "garbled",
            "route: NOT_A_VERB /x",
            "cli:",  # empty rest
        ]
    )
    out = inv.resolve_evidence(tmp_path, story)
    by_ref = {r["ref"]: r["valid"] for r in out["surface_refs"]}
    assert by_ref["cli: login"] is True
    assert by_ref["route: GET /users"] is True
    assert by_ref["route: get /users"] is False  # method must be uppercase
    assert by_ref["bin: mytool"] is True
    assert by_ref["exports: ./index"] is True
    assert by_ref["test: foo bar"] is True
    assert by_ref["unknown-prefix: x"] is False
    assert by_ref["garbled"] is False
    assert by_ref["route: NOT_A_VERB /x"] is False
    assert by_ref["cli:"] is False


def test_resolve_evidence_handles_empty_story(tmp_path):
    story = _FakeStory()
    out = inv.resolve_evidence(tmp_path, story)
    assert out == {
        "tests_resolved": [],
        "tests_missing": [],
        "surface_refs": [],
        "docs_resolved": [],
        "docs_missing": [],
    }


def test_resolve_evidence_works_with_real_story(tmp_path):
    """End-to-end: parse a real story via storystore_lib, resolve refs."""
    story_text = """\
---
title: Login
slug: login
status: active
authority: accepted
change_resistance: medium
---

# Login

## Intent
Users can log in.

## Evidence
### Tests
- `tests/login.spec.ts`
### Surface
- `cli: login`
### Docs
- `README.md`
"""
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "login.spec.ts").write_text("// test\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("# readme\n", encoding="utf-8")
    story_path = tmp_path / "login.md"
    story_path.write_text(story_text, encoding="utf-8")
    story = lib.parse_story(story_path)
    out = inv.resolve_evidence(tmp_path, story)
    assert out["tests_resolved"] == ["tests/login.spec.ts"]
    assert out["docs_resolved"] == ["README.md"]
    assert out["surface_refs"] == [{"ref": "cli: login", "valid": True}]
