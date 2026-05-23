"""Tests for shared/list_candidates.py — observed-mode candidate discovery."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "shared" / "list_candidates.py"


def _run(repo: Path) -> dict:
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--repo-root", str(repo)],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(proc.stdout)


def _make_stories_dir(repo: Path) -> Path:
    stories = repo / "docs" / "stories"
    stories.mkdir(parents=True, exist_ok=True)
    return stories


def _write_story(repo: Path, slug: str, *, surface_refs: list[str] | None = None) -> None:
    stories = _make_stories_dir(repo)
    surface_block = ""
    if surface_refs:
        lines = "\n".join(f"- `{ref}`" for ref in surface_refs)
        surface_block = f"### Surface\n{lines}\n"
    text = (
        f"---\n"
        f"schema_version: 1\n"
        f"title: {slug.replace('-', ' ').title()}\n"
        f"slug: {slug}\n"
        f"status: active\n"
        f"authority: observed\n"
        f"change_resistance: low\n"
        f"tests_applicable: true\n"
        f"---\n"
        f"\n"
        f"# {slug}\n"
        f"\n"
        f"## Intent\nIntent text.\n"
        f"\n"
        f"## Evidence\n"
        f"{surface_block}"
    )
    (stories / f"{slug}.md").write_text(text, encoding="utf-8")


# --------------------------------------------------------------------------- #
# Discovery sources
# --------------------------------------------------------------------------- #


def test_finds_cli_commands_from_typescript(tmp_path: Path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "cli.ts").write_text(
        'program.command("login");\nprogram.command("logout");\n',
        encoding="utf-8",
    )
    out = _run(tmp_path)
    cli = [c for c in out["candidates"] if c["kind"] == "cli-command"]
    names = sorted(c["name"] for c in cli)
    assert names == ["login", "logout"]
    # Evidence points at the source file.
    assert all("src/cli.ts" in c["evidence"] for c in cli)
    # Each has a summary.
    assert all(c["summary"] for c in cli)


def test_finds_http_routes_from_typescript(tmp_path: Path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "routes.ts").write_text(
        'app.get("/users", h);\napp.post("/users", h);\nrouter.delete("/users/:id", h);\n',
        encoding="utf-8",
    )
    out = _run(tmp_path)
    routes = sorted(
        (c["name"], tuple(c["evidence"])) for c in out["candidates"] if c["kind"] == "http-route"
    )
    names = [r[0] for r in routes]
    assert names == ["DELETE /users/:id", "GET /users", "POST /users"]


def test_finds_package_bin_and_exports(tmp_path: Path):
    (tmp_path / "package.json").write_text(
        '{"name": "mytool", "bin": {"mytool": "dist/cli.js"}, '
        '"exports": {".": "./dist/index.js", "./extra": "./dist/extra.js"}}',
        encoding="utf-8",
    )
    out = _run(tmp_path)
    bins = [c for c in out["candidates"] if c["kind"] == "bin"]
    exports = sorted(c["name"] for c in out["candidates"] if c["kind"] == "exports")
    assert [b["name"] for b in bins] == ["mytool"]
    assert exports == [".", "./extra"]
    assert all("package.json" in b["evidence"] for b in bins)


def test_finds_readme_and_design_headings(tmp_path: Path):
    (tmp_path / "README.md").write_text(
        "# Title\n\n## Authentication\n\n### Login\n\n## Setup\n",
        encoding="utf-8",
    )
    (tmp_path / "DESIGN.md").write_text(
        "# Design\n\n## Storage Layer\n",
        encoding="utf-8",
    )
    out = _run(tmp_path)
    headings = sorted(c["name"] for c in out["candidates"] if c["kind"] == "heading")
    assert headings == ["Authentication", "Login", "Setup", "Storage Layer"]


def test_finds_test_names_from_spec_files(tmp_path: Path):
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "login.spec.ts").write_text(
        'describe("login flow", () => {});\nit("should login", () => {});\n',
        encoding="utf-8",
    )
    out = _run(tmp_path)
    test_names = sorted(c["name"] for c in out["candidates"] if c["kind"] == "test")
    assert test_names == ["login flow", "should login"]


def test_finds_user_facing_scripts_skips_build_scripts(tmp_path: Path):
    (tmp_path / "package.json").write_text(
        json.dumps(
            {
                "name": "x",
                "scripts": {
                    "build": "tsc",
                    "lint": "eslint .",
                    "test": "jest",
                    "start": "node ./dist/cli.js",
                    "deploy": "scripts/deploy.sh",
                },
            }
        ),
        encoding="utf-8",
    )
    out = _run(tmp_path)
    scripts = sorted(c["name"] for c in out["candidates"] if c["kind"] == "script")
    # build, lint, test are build/infra and excluded; start/deploy kept.
    assert "build" not in scripts
    assert "lint" not in scripts
    assert "test" not in scripts
    assert "start" in scripts
    assert "deploy" in scripts


# --------------------------------------------------------------------------- #
# Subtraction
# --------------------------------------------------------------------------- #


def test_subtracts_candidates_covered_by_story_surface_ref(tmp_path: Path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "cli.ts").write_text(
        'program.command("login");\nprogram.command("logout");\n',
        encoding="utf-8",
    )
    _write_story(tmp_path, "user-login-flow", surface_refs=["cli: login"])
    out = _run(tmp_path)
    cli = sorted(c["name"] for c in out["candidates"] if c["kind"] == "cli-command")
    assert cli == ["logout"]


def test_subtracts_candidates_covered_by_story_slug(tmp_path: Path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "cli.ts").write_text('program.command("login");\n', encoding="utf-8")
    # Slug exactly equals candidate "name" — should subtract.
    _write_story(tmp_path, "login")
    out = _run(tmp_path)
    assert not [c for c in out["candidates"] if c["kind"] == "cli-command"]


def test_subtracts_http_route_by_surface_ref(tmp_path: Path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "r.ts").write_text(
        'app.get("/users", h);\napp.post("/users", h);\n',
        encoding="utf-8",
    )
    _write_story(tmp_path, "list-users-endpoint", surface_refs=["route: GET /users"])
    out = _run(tmp_path)
    routes = sorted(c["name"] for c in out["candidates"] if c["kind"] == "http-route")
    assert routes == ["POST /users"]


# --------------------------------------------------------------------------- #
# Determinism
# --------------------------------------------------------------------------- #


def test_output_is_stable_and_sorted(tmp_path: Path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "cli.ts").write_text(
        'program.command("zulu");\nprogram.command("alpha");\nprogram.command("mike");\n',
        encoding="utf-8",
    )
    out1 = _run(tmp_path)
    out2 = _run(tmp_path)
    assert out1 == out2
    # Sorted by (kind, name).
    cli = [c["name"] for c in out1["candidates"] if c["kind"] == "cli-command"]
    assert cli == sorted(cli)


def test_empty_repo_returns_empty_candidates(tmp_path: Path):
    out = _run(tmp_path)
    assert out == {"candidates": []}
