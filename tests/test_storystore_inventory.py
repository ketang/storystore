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


def test_build_inventory_extracts_skill_directories(tmp_path):
    # A markdown-only repo whose only user-facing surfaces are skill dirs.
    for name in ("stories-audit", "stories-generate"):
        d = tmp_path / "skills" / name
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text(f"# {name}\n", encoding="utf-8")
    result = inv.build_inventory(tmp_path)
    skills = [s for s in result["surfaces"] if s["kind"] == "skill"]
    assert {s["name"] for s in skills} == {"stories-audit", "stories-generate"}
    assert {s["source"] for s in skills} == {
        "skills/stories-audit/SKILL.md",
        "skills/stories-generate/SKILL.md",
    }


def test_build_inventory_skill_extraction_is_language_agnostic(tmp_path):
    # No language markers at all — skills are still inventoried.
    d = tmp_path / "catalog" / "skills" / "deploy-app"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text("# deploy-app\n", encoding="utf-8")
    result = inv.build_inventory(tmp_path)
    assert result["languages"]["detected"] == []
    assert [s["name"] for s in result["surfaces"] if s["kind"] == "skill"] == [
        "deploy-app"
    ]


def test_build_inventory_skill_marker_at_root_is_skipped(tmp_path):
    # A SKILL.md with no naming directory yields no skill surface.
    (tmp_path / "SKILL.md").write_text("# root\n", encoding="utf-8")
    result = inv.build_inventory(tmp_path)
    assert [s for s in result["surfaces"] if s["kind"] == "skill"] == []


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
            "skill: stories-audit",
            "test: foo bar",
            "unknown-prefix: x",
            "garbled",
            "route: NOT_A_VERB /x",
            "cli:",  # empty rest
            "skill:",  # empty rest
        ]
    )
    out = inv.resolve_evidence(tmp_path, story)
    by_ref = {r["ref"]: r["valid"] for r in out["surface_refs"]}
    assert by_ref["cli: login"] is True
    assert by_ref["route: GET /users"] is True
    assert by_ref["route: get /users"] is False  # method must be uppercase
    assert by_ref["bin: mytool"] is True
    assert by_ref["exports: ./index"] is True
    assert by_ref["skill: stories-audit"] is True
    assert by_ref["test: foo bar"] is True
    assert by_ref["unknown-prefix: x"] is False
    assert by_ref["garbled"] is False
    assert by_ref["route: NOT_A_VERB /x"] is False
    assert by_ref["cli:"] is False
    assert by_ref["skill:"] is False


def test_validate_surface_ref_public_wrapper_accepts_skill():
    assert inv.validate_surface_ref("skill: stories-audit") is True
    assert inv.validate_surface_ref("skill:stories-audit") is True  # no space
    assert inv.validate_surface_ref("skill:") is False
    assert inv.validate_surface_ref("nope: x") is False


def test_resolve_evidence_handles_empty_story(tmp_path):
    story = _FakeStory()
    out = inv.resolve_evidence(tmp_path, story)
    assert out == {
        "tests_resolved": [],
        "tests_missing": [],
        "surface_refs": [],
        "docs_resolved": [],
        "docs_missing": [],
        "schema_resolved": [],
        "schema_missing": [],
        "flag_resolved": [],
        "flag_missing": [],
        "copy_resolved": [],
        "copy_missing": [],
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


# --------------------------------------------------------------------------- #
# Schema evidence resolution
# --------------------------------------------------------------------------- #


class _FakeStoryWithSchema:
    """Stand-in for Story with evidence_schema field."""

    def __init__(self, tests=(), surface=(), docs=(), schema=()):
        self.evidence_tests = list(tests)
        self.evidence_surface = list(surface)
        self.evidence_docs = list(docs)
        self.evidence_schema = list(schema)


def _make_sql_migration(repo: Path, rel_path: str, content: str) -> Path:
    """Write a migration file at ``rel_path`` under ``repo``."""
    full = repo / rel_path
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(content, encoding="utf-8")
    return full


def test_resolve_schema_ref_found_sql(tmp_path):
    """schema:users.email resolves when a SQL migration defines the column."""
    _make_sql_migration(
        tmp_path,
        "migrations/001_create_users.sql",
        "CREATE TABLE users (\n  id INTEGER PRIMARY KEY,\n  email VARCHAR(255) NOT NULL\n);\n",
    )
    story = _FakeStoryWithSchema(schema=["users.email"])
    out = inv.resolve_evidence(tmp_path, story)
    assert len(out["schema_resolved"]) == 1
    assert out["schema_resolved"][0]["ref"] == "users.email"
    assert out["schema_resolved"][0]["file"] == "migrations/001_create_users.sql"
    assert out["schema_resolved"][0]["line"] == 3  # line with 'email'
    assert out["schema_missing"] == []


def test_resolve_schema_ref_found_rails(tmp_path):
    """schema:users.email resolves in a Rails-style migration."""
    _make_sql_migration(
        tmp_path,
        "db/migrate/20240101_create_users.rb",
        (
            "class CreateUsers < ActiveRecord::Migration[7.0]\n"
            "  def change\n"
            "    create_table :users do |t|\n"
            "      t.string :email, null: false\n"
            "      t.timestamps\n"
            "    end\n"
            "  end\n"
            "end\n"
        ),
    )
    story = _FakeStoryWithSchema(schema=["users.email"])
    out = inv.resolve_evidence(tmp_path, story)
    assert len(out["schema_resolved"]) == 1
    assert out["schema_resolved"][0]["ref"] == "users.email"
    assert "db/migrate" in out["schema_resolved"][0]["file"]
    assert out["schema_missing"] == []


def test_resolve_schema_ref_not_found(tmp_path):
    """Schema ref reports missing when no migration matches."""
    _make_sql_migration(
        tmp_path,
        "migrations/001_create_users.sql",
        "CREATE TABLE users (\n  id INTEGER PRIMARY KEY\n);\n",
    )
    story = _FakeStoryWithSchema(schema=["users.phone"])
    out = inv.resolve_evidence(tmp_path, story)
    assert out["schema_resolved"] == []
    assert out["schema_missing"] == ["users.phone"]


def test_resolve_schema_ref_no_migrations(tmp_path):
    """Schema ref reports missing when no migration files exist."""
    story = _FakeStoryWithSchema(schema=["users.email"])
    out = inv.resolve_evidence(tmp_path, story)
    assert out["schema_resolved"] == []
    assert out["schema_missing"] == ["users.email"]


def test_resolve_schema_ref_malformed(tmp_path):
    """Malformed schema refs are reported as missing."""
    story = _FakeStoryWithSchema(schema=["not-a-valid-ref", "users", ".column", ""])
    out = inv.resolve_evidence(tmp_path, story)
    assert out["schema_resolved"] == []
    # Empty string is stripped and skipped.
    assert sorted(out["schema_missing"]) == [".column", "not-a-valid-ref", "users"]


def test_resolve_schema_ref_most_recent_migration_wins(tmp_path):
    """When multiple migrations reference the column, the last one wins."""
    _make_sql_migration(
        tmp_path,
        "migrations/001_create_users.sql",
        "CREATE TABLE users (\n  id INTEGER,\n  email VARCHAR(100)\n);\n",
    )
    _make_sql_migration(
        tmp_path,
        "migrations/002_alter_users.sql",
        "ALTER TABLE users ALTER COLUMN email VARCHAR(255);\n",
    )
    story = _FakeStoryWithSchema(schema=["users.email"])
    out = inv.resolve_evidence(tmp_path, story)
    assert len(out["schema_resolved"]) == 1
    assert out["schema_resolved"][0]["file"] == "migrations/002_alter_users.sql"


def test_validate_surface_ref_schema_valid():
    assert inv._validate_surface_ref("schema: users.email") is True
    assert inv._validate_surface_ref("schema: accounts.created_at") is True


def test_validate_surface_ref_schema_invalid():
    assert inv._validate_surface_ref("schema: users") is False
    assert inv._validate_surface_ref("schema: .col") is False
    assert inv._validate_surface_ref("schema: ") is False


# --------------------------------------------------------------------------- #
# Flag evidence resolution
# --------------------------------------------------------------------------- #


class _FakeStoryWithFlag:
    """Stand-in for Story with evidence_flag field."""

    def __init__(self, tests=(), surface=(), docs=(), schema=(), flag=()):
        self.evidence_tests = list(tests)
        self.evidence_surface = list(surface)
        self.evidence_docs = list(docs)
        self.evidence_schema = list(schema)
        self.evidence_flag = list(flag)


def _write_flag_file(repo: Path, rel_path: str, content: str) -> Path:
    """Write a source file at ``rel_path`` under ``repo``."""
    full = repo / rel_path
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(content, encoding="utf-8")
    return full


def test_resolve_flag_ref_found_ruby(tmp_path):
    """flag:experimental_collab resolves in a Ruby feature_flag definition."""
    _write_flag_file(
        tmp_path,
        "config/flags.rb",
        "class Flags\n  feature_flag :experimental_collab\n  feature_flag :dark_mode\nend\n",
    )
    story = _FakeStoryWithFlag(flag=["experimental_collab"])
    out = inv.resolve_evidence(tmp_path, story)
    assert len(out["flag_resolved"]) == 1
    assert out["flag_resolved"][0]["ref"] == "experimental_collab"
    assert out["flag_resolved"][0]["file"] == "config/flags.rb"
    assert out["flag_resolved"][0]["line"] == 2
    assert out["flag_missing"] == []


def test_resolve_flag_ref_found_python(tmp_path):
    """flag:experimental_collab resolves in a Python FLAGS dict."""
    _write_flag_file(
        tmp_path,
        "config/flags.py",
        'FLAGS = {\n    "experimental_collab": True,\n    "dark_mode": False,\n}\n',
    )
    story = _FakeStoryWithFlag(flag=["experimental_collab"])
    out = inv.resolve_evidence(tmp_path, story)
    assert len(out["flag_resolved"]) == 1
    assert out["flag_resolved"][0]["ref"] == "experimental_collab"
    assert out["flag_resolved"][0]["file"] == "config/flags.py"
    assert out["flag_missing"] == []


def test_resolve_flag_ref_found_js(tmp_path):
    """flag:experimental_collab resolves in a JS/TS const object."""
    _write_flag_file(
        tmp_path,
        "src/flags.ts",
        "export const FLAGS = {\n  experimental_collab: true,\n  dark_mode: false,\n};\n",
    )
    story = _FakeStoryWithFlag(flag=["experimental_collab"])
    out = inv.resolve_evidence(tmp_path, story)
    assert len(out["flag_resolved"]) == 1
    assert out["flag_resolved"][0]["ref"] == "experimental_collab"
    assert out["flag_resolved"][0]["file"] == "src/flags.ts"
    assert out["flag_missing"] == []


def test_resolve_flag_ref_found_yaml(tmp_path):
    """flag:experimental_collab resolves in a YAML config file."""
    _write_flag_file(
        tmp_path,
        "config/features.yml",
        "experimental_collab:\n  enabled: true\ndark_mode:\n  enabled: false\n",
    )
    story = _FakeStoryWithFlag(flag=["experimental_collab"])
    out = inv.resolve_evidence(tmp_path, story)
    assert len(out["flag_resolved"]) == 1
    assert out["flag_resolved"][0]["ref"] == "experimental_collab"
    assert out["flag_resolved"][0]["file"] == "config/features.yml"
    assert out["flag_resolved"][0]["line"] == 1
    assert out["flag_missing"] == []


def test_resolve_flag_ref_not_found(tmp_path):
    """Flag ref reports missing when no source file contains the identifier."""
    _write_flag_file(
        tmp_path,
        "config/flags.rb",
        "class Flags\n  feature_flag :dark_mode\nend\n",
    )
    story = _FakeStoryWithFlag(flag=["experimental_collab"])
    out = inv.resolve_evidence(tmp_path, story)
    assert out["flag_resolved"] == []
    assert out["flag_missing"] == ["experimental_collab"]


def test_resolve_flag_ref_no_files(tmp_path):
    """Flag ref reports missing when no source files exist."""
    story = _FakeStoryWithFlag(flag=["experimental_collab"])
    out = inv.resolve_evidence(tmp_path, story)
    assert out["flag_resolved"] == []
    assert out["flag_missing"] == ["experimental_collab"]


def test_resolve_flag_ref_malformed(tmp_path):
    """Malformed flag refs are reported as missing."""
    story = _FakeStoryWithFlag(flag=["not a valid ref!", "valid_flag", ""])
    _write_flag_file(
        tmp_path,
        "config/flags.py",
        'FLAGS = {\n    "valid_flag": True,\n}\n',
    )
    out = inv.resolve_evidence(tmp_path, story)
    assert len(out["flag_resolved"]) == 1
    assert out["flag_resolved"][0]["ref"] == "valid_flag"
    # Empty string is stripped and skipped; malformed ref reported.
    assert out["flag_missing"] == ["not a valid ref!"]


def test_validate_surface_ref_flag_valid():
    assert inv._validate_surface_ref("flag: experimental_collab") is True
    assert inv._validate_surface_ref("flag: dark-mode") is True
    assert inv._validate_surface_ref("flag: my_flag_123") is True


def test_validate_surface_ref_flag_invalid():
    assert inv._validate_surface_ref("flag: ") is False
    assert inv._validate_surface_ref("flag: not a flag!") is False
    assert inv._validate_surface_ref("flag: 123start") is False


# --------------------------------------------------------------------------- #
# Copy evidence resolution
# --------------------------------------------------------------------------- #


class _FakeStoryWithCopy:
    """Stand-in for Story with evidence_copy field."""

    def __init__(self, tests=(), surface=(), docs=(), schema=(), copy=()):
        self.evidence_tests = list(tests)
        self.evidence_surface = list(surface)
        self.evidence_docs = list(docs)
        self.evidence_schema = list(schema)
        self.evidence_copy = list(copy)


def _make_locale_file(repo: Path, rel_path: str, content: str) -> Path:
    """Write a locale file at ``rel_path`` under ``repo``."""
    full = repo / rel_path
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(content, encoding="utf-8")
    return full


def test_resolve_copy_ref_found_json(tmp_path):
    """copy:en/messages.json#errors.permission_denied resolves when key exists."""
    _make_locale_file(
        tmp_path,
        "en/messages.json",
        '{\n  "errors": {\n    "permission_denied": "Access denied"\n  }\n}\n',
    )
    story = _FakeStoryWithCopy(copy=["en/messages.json#errors.permission_denied"])
    out = inv.resolve_evidence(tmp_path, story)
    assert len(out["copy_resolved"]) == 1
    assert out["copy_resolved"][0]["ref"] == "en/messages.json#errors.permission_denied"
    assert out["copy_resolved"][0]["file"] == "en/messages.json"
    assert out["copy_resolved"][0]["line"] >= 1
    assert out["copy_missing"] == []


def test_resolve_copy_ref_found_yaml(tmp_path):
    """copy:en/messages.yaml#errors.permission_denied resolves in YAML locale."""
    _make_locale_file(
        tmp_path,
        "en/messages.yaml",
        "errors:\n  permission_denied: Access denied\n",
    )
    story = _FakeStoryWithCopy(copy=["en/messages.yaml#errors.permission_denied"])
    out = inv.resolve_evidence(tmp_path, story)
    assert len(out["copy_resolved"]) == 1
    assert out["copy_resolved"][0]["ref"] == "en/messages.yaml#errors.permission_denied"
    assert out["copy_resolved"][0]["file"] == "en/messages.yaml"
    assert out["copy_missing"] == []


def test_resolve_copy_ref_found_yml(tmp_path):
    """copy:en/messages.yml#greeting resolves with .yml extension."""
    _make_locale_file(
        tmp_path,
        "en/messages.yml",
        "greeting: Hello\n",
    )
    story = _FakeStoryWithCopy(copy=["en/messages.yml#greeting"])
    out = inv.resolve_evidence(tmp_path, story)
    assert len(out["copy_resolved"]) == 1
    assert out["copy_resolved"][0]["ref"] == "en/messages.yml#greeting"
    assert out["copy_missing"] == []


def test_resolve_copy_ref_not_found_missing_key(tmp_path):
    """Copy ref reports missing when the key doesn't exist in the locale file."""
    _make_locale_file(
        tmp_path,
        "en/messages.json",
        '{\n  "errors": {\n    "not_found": "Not found"\n  }\n}\n',
    )
    story = _FakeStoryWithCopy(copy=["en/messages.json#errors.permission_denied"])
    out = inv.resolve_evidence(tmp_path, story)
    assert out["copy_resolved"] == []
    assert out["copy_missing"] == ["en/messages.json#errors.permission_denied"]


def test_resolve_copy_ref_not_found_missing_file(tmp_path):
    """Copy ref reports missing when the locale file doesn't exist."""
    story = _FakeStoryWithCopy(copy=["en/messages.json#errors.permission_denied"])
    out = inv.resolve_evidence(tmp_path, story)
    assert out["copy_resolved"] == []
    assert out["copy_missing"] == ["en/messages.json#errors.permission_denied"]


def test_resolve_copy_ref_malformed(tmp_path):
    """Malformed copy refs are reported as missing."""
    story = _FakeStoryWithCopy(copy=["not-a-valid-ref", "missing_hash.json", ""])
    out = inv.resolve_evidence(tmp_path, story)
    assert out["copy_resolved"] == []
    # Empty string is stripped and skipped.
    assert sorted(out["copy_missing"]) == ["missing_hash.json", "not-a-valid-ref"]


def test_resolve_copy_ref_nested_json_key(tmp_path):
    """Deep nested key navigation works in JSON locale files."""
    _make_locale_file(
        tmp_path,
        "locales/en.json",
        '{\n  "app": {\n    "settings": {\n      "theme": {\n        "dark": "Dark mode"\n      }\n    }\n  }\n}\n',
    )
    story = _FakeStoryWithCopy(copy=["locales/en.json#app.settings.theme.dark"])
    out = inv.resolve_evidence(tmp_path, story)
    assert len(out["copy_resolved"]) == 1
    assert out["copy_resolved"][0]["ref"] == "locales/en.json#app.settings.theme.dark"
    assert out["copy_missing"] == []


def test_validate_surface_ref_copy_valid():
    assert inv._validate_surface_ref("copy: en/messages.json#errors.denied") is True
    assert inv._validate_surface_ref("copy: locales/fr.yaml#greeting") is True
    assert inv._validate_surface_ref("copy: i18n/de.yml#nav.home") is True


def test_validate_surface_ref_copy_invalid():
    assert inv._validate_surface_ref("copy: messages.txt#key") is False
    assert inv._validate_surface_ref("copy: ") is False
    assert inv._validate_surface_ref("copy: file.json") is False
