#!/usr/bin/env python3
"""Discover user-facing surfaces as story candidates.

Usage:
    list_candidates.py --repo-root <repo-root>

Calls ``inventory.build_inventory`` for surface extraction, augments with
top-level user-facing ``package.json`` scripts, then subtracts candidates
already covered by an authored story (by exact slug match against the
candidate name, or by surface-ref match against a story's ``Evidence.Surface``
declarations).

Stdout JSON:

    {
      "candidates": [
        {"kind": "cli-command", "name": "login",
         "summary": "CLI command login", "evidence": ["src/cli.ts"]}
      ]
    }

Output is deterministic: candidates are sorted by ``(kind, name)`` and each
candidate's ``evidence`` list is sorted and de-duplicated.

Exit codes:
    0  success
    2  invalid input (e.g. missing repo-root, unreadable stories dir)
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any, Iterable


SCRIPT_DIR = Path(__file__).resolve().parent
INV_PATH = SCRIPT_DIR / "inventory.py"
LIB_PATH = SCRIPT_DIR / "storystore_lib.py"


# Top-level package.json scripts to omit from candidates. These are
# build/infrastructure scripts rather than user-facing capabilities.
_SCRIPT_EXCLUDE: frozenset[str] = frozenset(
    {
        "build",
        "prebuild",
        "postbuild",
        "rebuild",
        "compile",
        "tsc",
        "typecheck",
        "type-check",
        "types",
        "lint",
        "lint:fix",
        "format",
        "fmt",
        "prettier",
        "eslint",
        "test",
        "tests",
        "test:watch",
        "test:unit",
        "test:e2e",
        "test:integration",
        "coverage",
        "clean",
        "prepare",
        "prepublish",
        "prepublishOnly",
        "preinstall",
        "postinstall",
        "install",
        "release",
        "version",
        "preversion",
        "postversion",
        "ci",
        "audit",
    }
)


def _load(name: str, path: Path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _die(msg: str, code: int = 2) -> "NoReturn":  # type: ignore[name-defined]
    print(msg, file=sys.stderr)
    sys.exit(code)


# --------------------------------------------------------------------------- #
# Candidate construction
# --------------------------------------------------------------------------- #


def _surface_ref(kind: str, name: str) -> str:
    """Return the canonical surface-ref string used for matching against
    a story's declared ``Evidence.Surface`` entries."""
    if kind == "cli-command":
        return f"cli: {name}"
    if kind == "http-route":
        # name is already "METHOD /path"
        return f"route: {name}"
    if kind == "bin":
        return f"bin: {name}"
    if kind == "exports":
        return f"exports: {name}"
    if kind == "test":
        return f"test: {name}"
    if kind == "heading":
        return f"heading: {name}"
    if kind == "script":
        return f"script: {name}"
    return f"{kind}: {name}"


def _summary(kind: str, name: str) -> str:
    if kind == "cli-command":
        return f"CLI command {name}"
    if kind == "http-route":
        return f"HTTP route {name}"
    if kind == "bin":
        return f"Package bin {name}"
    if kind == "exports":
        return f"Package export {name}"
    if kind == "test":
        return f"Test {name}"
    if kind == "heading":
        return f"Documentation heading {name}"
    if kind == "script":
        return f"Package script {name}"
    return f"{kind} {name}"


def _surfaces_to_candidates(surfaces: Iterable[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    """Group inventory surfaces into (kind, name) candidates, merging evidence."""
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for surface in surfaces:
        kind = surface.get("kind")
        if kind == "cli-command":
            name = surface.get("name", "")
        elif kind == "http-route":
            method = surface.get("method", "")
            path = surface.get("path", "")
            name = f"{method} {path}".strip()
        elif kind in ("bin", "exports", "test"):
            name = surface.get("name", "")
        elif kind == "heading":
            name = surface.get("text", "")
        else:
            continue
        if not name:
            continue
        key = (kind, name)
        source = surface.get("source")
        entry = grouped.setdefault(
            key,
            {"kind": kind, "name": name, "summary": _summary(kind, name), "evidence": set()},
        )
        if source:
            entry["evidence"].add(source)
    return grouped


def _add_scripts(repo_root: Path, grouped: dict[tuple[str, str], dict[str, Any]]) -> None:
    """Add user-facing top-level scripts from ``package.json``."""
    pkg = repo_root / "package.json"
    if not pkg.is_file():
        return
    try:
        data = json.loads(pkg.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return
    scripts = data.get("scripts")
    if not isinstance(scripts, dict):
        return
    for name in scripts:
        if not isinstance(name, str) or not name:
            continue
        if name in _SCRIPT_EXCLUDE:
            continue
        # Common prefixed build hooks (pre*/post*) — drop if the underlying
        # target is build/infra.
        bare = name
        for prefix in ("pre", "post"):
            if name.startswith(prefix) and name[len(prefix):] in _SCRIPT_EXCLUDE:
                bare = ""
                break
        if not bare:
            continue
        key = ("script", name)
        entry = grouped.setdefault(
            key,
            {
                "kind": "script",
                "name": name,
                "summary": _summary("script", name),
                "evidence": set(),
            },
        )
        entry["evidence"].add("package.json")


# --------------------------------------------------------------------------- #
# Subtraction
# --------------------------------------------------------------------------- #


def _collect_authored(repo_root: Path, lib) -> tuple[set[str], set[str]]:
    """Return ``(slugs, surface_refs)`` declared by existing stories.

    Stories that fail to parse are skipped silently — a malformed authored
    story should not crash candidate discovery.
    """
    slugs: set[str] = set()
    refs: set[str] = set()
    stories_dir = repo_root / "docs" / "stories"
    if not stories_dir.is_dir():
        return slugs, refs
    for path in sorted(stories_dir.iterdir()):
        if not path.is_file() or path.suffix != ".md":
            continue
        if path.name in getattr(lib, "LOADER_SKIP", {"README.md", "INDEX.md", "drift-todo.md"}):
            continue
        try:
            story = lib.parse_story(path)
        except Exception:
            continue
        if story.slug:
            slugs.add(story.slug)
        for ref in story.evidence_surface or ():
            refs.add(ref.strip())
    return slugs, refs


def _is_covered(candidate: dict[str, Any], authored_slugs: set[str], authored_refs: set[str]) -> bool:
    name = candidate["name"]
    kind = candidate["kind"]
    if name in authored_slugs:
        return True
    if _surface_ref(kind, name) in authored_refs:
        return True
    return False


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #


def list_candidates(repo_root: Path) -> dict[str, Any]:
    inv = _load("storystore_inventory", INV_PATH)
    lib = _load("storystore_lib", LIB_PATH)

    inventory = inv.build_inventory(repo_root)
    grouped = _surfaces_to_candidates(inventory.get("surfaces", []))
    _add_scripts(repo_root, grouped)

    authored_slugs, authored_refs = _collect_authored(repo_root, lib)

    candidates: list[dict[str, Any]] = []
    for key in sorted(grouped):
        entry = grouped[key]
        if _is_covered(entry, authored_slugs, authored_refs):
            continue
        candidates.append(
            {
                "kind": entry["kind"],
                "name": entry["name"],
                "summary": entry["summary"],
                "evidence": sorted(entry["evidence"]),
            }
        )
    return {"candidates": candidates}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Discover story candidates for a repo.")
    parser.add_argument("--repo-root", required=True)
    args = parser.parse_args(argv)

    repo_root = Path(args.repo_root).resolve()
    if not repo_root.is_dir():
        _die(f"repo-root does not exist or is not a directory: {repo_root}", 2)

    result = list_candidates(repo_root)
    json.dump(result, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
