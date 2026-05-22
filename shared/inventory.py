"""Storystore inventory: language detection, surface extraction, evidence resolution.

Stdlib-only. Used by ``stories-audit`` and ``stories-coverage`` to:

- detect repository languages by marker files at depth <= 2;
- extract user-facing surface inventory (TypeScript-first);
- resolve story evidence references (tests/surface/docs).

See ``shared/spec.md`` for the authoritative behavior contract.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Iterable, Optional


# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #

DEFAULT_SKIP_DIRS: frozenset[str] = frozenset(
    {
        ".git",
        "node_modules",
        "vendor",
        "dist",
        "build",
        "out",
        "target",
        "__pycache__",
        ".venv",
        "venv",
        ".tox",
        ".next",
        ".nuxt",
        ".cache",
        ".pytest_cache",
        "coverage",
        ".idea",
        ".vscode",
    }
)

# Map marker filename -> list of language identifiers.
LANGUAGE_MARKERS: dict[str, tuple[str, ...]] = {
    "package.json": ("typescript", "javascript"),
    "go.mod": ("go",),
    "Cargo.toml": ("rust",),
    "pyproject.toml": ("python",),
    "setup.py": ("python",),
    "Gemfile": ("ruby",),
    "pom.xml": ("java",),
}

# Languages for which we ship bundled inventory extractors.
EXTRACTED_LANGUAGES: frozenset[str] = frozenset({"typescript", "javascript"})

# File-extension globs we treat as TypeScript/JavaScript source.
TS_SOURCE_SUFFIXES: frozenset[str] = frozenset({".ts", ".tsx", ".mts", ".cts", ".js", ".mjs", ".cjs", ".jsx"})

# Test-file suffix patterns (matched against full filename).
TS_TEST_SUFFIXES: tuple[str, ...] = (
    ".spec.ts",
    ".test.ts",
    ".e2e.ts",
    ".spec.tsx",
    ".test.tsx",
    ".spec.js",
    ".test.js",
    ".e2e.js",
)

# Doc filenames whose H2/H3 headings we surface.
HEADING_DOC_NAMES: frozenset[str] = frozenset({"README.md", "DESIGN.md", "ARCHITECTURE.md"})

HTTP_METHODS: tuple[str, ...] = ("get", "post", "put", "patch", "delete", "head", "options", "all")


# --------------------------------------------------------------------------- #
# Regexes
# --------------------------------------------------------------------------- #

# commander-style: .command("name"  or  .command('name'
_CLI_COMMAND_RE = re.compile(r"""\.command\(\s*['"]([^'"\s]+)['"]""")

# Express/Koa/Fastify route-ish:  app.get("/path"  router.post('/x'
_HTTP_ROUTE_RE = re.compile(
    r"""\.(?P<method>get|post|put|patch|delete|head|options|all)\(\s*['"](?P<path>/[^'"]*)['"]""",
    re.IGNORECASE,
)

# describe / it / test  string names
_TEST_NAME_RE = re.compile(
    r"""(?:^|[\s.;{}(])(?:describe|it|test)\(\s*['"]([^'"]+)['"]""",
)

# Markdown H2/H3 headings (no trailing #s required).
_HEADING_RE = re.compile(r"^(#{2,3})\s+(.+?)\s*#*\s*$")


# --------------------------------------------------------------------------- #
# Language detection
# --------------------------------------------------------------------------- #


def detect_languages(repo_root: Path) -> dict[str, list[str]]:
    """Detect languages by walking the tree at depth <= 2 for marker files.

    Returns ``{"detected": [...], "extracted": [...]}`` where ``extracted`` is
    the subset of detected languages that bundled extractors cover.
    """
    repo_root = Path(repo_root)
    detected: set[str] = set()
    if not repo_root.is_dir():
        return {"detected": [], "extracted": []}

    # Depth 0 — repo root itself.
    _scan_markers_at(repo_root, detected)
    # Depth 1 and 2.
    for first in _iter_subdirs(repo_root):
        _scan_markers_at(first, detected)
        for second in _iter_subdirs(first):
            _scan_markers_at(second, detected)

    extracted = sorted(detected & EXTRACTED_LANGUAGES)
    return {"detected": sorted(detected), "extracted": extracted}


def _iter_subdirs(parent: Path) -> Iterable[Path]:
    try:
        entries = list(parent.iterdir())
    except (OSError, PermissionError):
        return
    for entry in entries:
        if entry.is_dir() and entry.name not in DEFAULT_SKIP_DIRS:
            yield entry


def _scan_markers_at(directory: Path, detected: set[str]) -> None:
    for marker, langs in LANGUAGE_MARKERS.items():
        if (directory / marker).is_file():
            detected.update(langs)


# --------------------------------------------------------------------------- #
# Walk helpers
# --------------------------------------------------------------------------- #


def _effective_skip_dirs(include_dirs: Optional[Iterable[str]]) -> set[str]:
    skip = set(DEFAULT_SKIP_DIRS)
    if include_dirs:
        for name in include_dirs:
            skip.discard(name)
    return skip


def _walk_source(root: Path, include_dirs: Optional[Iterable[str]]) -> Iterable[Path]:
    """Yield files under ``root`` honoring the effective skip set."""
    skip = _effective_skip_dirs(include_dirs)
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in skip)
        for name in sorted(filenames):
            yield Path(dirpath) / name


# --------------------------------------------------------------------------- #
# Surface extractors
# --------------------------------------------------------------------------- #


def _extract_ts_surfaces(file_path: Path, rel_source: str) -> list[dict[str, Any]]:
    surfaces: list[dict[str, Any]] = []
    try:
        text = file_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return surfaces

    for match in _CLI_COMMAND_RE.finditer(text):
        surfaces.append({"kind": "cli-command", "name": match.group(1), "source": rel_source})

    for match in _HTTP_ROUTE_RE.finditer(text):
        method = match.group("method").upper()
        path = match.group("path")
        surfaces.append(
            {"kind": "http-route", "method": method, "path": path, "source": rel_source}
        )

    return surfaces


def _is_ts_test_file(name: str) -> bool:
    return any(name.endswith(suffix) for suffix in TS_TEST_SUFFIXES)


def _extract_ts_tests(file_path: Path, rel_source: str) -> list[dict[str, Any]]:
    try:
        text = file_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    return [
        {"kind": "test", "name": match.group(1), "source": rel_source}
        for match in _TEST_NAME_RE.finditer(text)
    ]


def _extract_package_json(file_path: Path, rel_source: str) -> list[dict[str, Any]]:
    surfaces: list[dict[str, Any]] = []
    try:
        data = json.loads(file_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return surfaces

    bin_field = data.get("bin")
    if isinstance(bin_field, str):
        # `"bin": "path"` — name defaults to package name.
        name = data.get("name")
        if isinstance(name, str) and name:
            surfaces.append({"kind": "bin", "name": name, "source": rel_source})
    elif isinstance(bin_field, dict):
        for name in sorted(bin_field):
            if isinstance(name, str) and name:
                surfaces.append({"kind": "bin", "name": name, "source": rel_source})

    exports_field = data.get("exports")
    if isinstance(exports_field, str):
        surfaces.append({"kind": "exports", "name": ".", "source": rel_source})
    elif isinstance(exports_field, dict):
        for name in sorted(exports_field):
            if isinstance(name, str):
                surfaces.append({"kind": "exports", "name": name, "source": rel_source})

    return surfaces


def _extract_doc_headings(file_path: Path, rel_source: str) -> list[dict[str, Any]]:
    try:
        text = file_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    out: list[dict[str, Any]] = []
    for line in text.splitlines():
        match = _HEADING_RE.match(line)
        if match:
            level = len(match.group(1))
            out.append(
                {
                    "kind": "heading",
                    "text": match.group(2).strip(),
                    "level": level,
                    "source": rel_source,
                }
            )
    return out


# --------------------------------------------------------------------------- #
# Inventory build
# --------------------------------------------------------------------------- #


def build_inventory(
    repo_root: Path,
    source_root: Optional[Path | str] = None,
    include_dirs: Optional[Iterable[str]] = None,
) -> dict[str, Any]:
    """Build a user-facing inventory for ``repo_root``.

    ``source_root`` narrows the walk to a subtree (relative to repo_root).
    ``include_dirs`` removes named directories from the default skip set
    for this run.
    """
    repo_root = Path(repo_root).resolve()
    languages = detect_languages(repo_root)

    if source_root is not None:
        walk_root = (repo_root / source_root).resolve()
    else:
        walk_root = repo_root

    surfaces: list[dict[str, Any]] = []

    if walk_root.is_dir():
        for file_path in _walk_source(walk_root, include_dirs):
            name = file_path.name
            rel = file_path.relative_to(repo_root).as_posix()

            if name == "package.json":
                surfaces.extend(_extract_package_json(file_path, rel))
                continue

            suffix = file_path.suffix
            if suffix in TS_SOURCE_SUFFIXES:
                if _is_ts_test_file(name):
                    surfaces.extend(_extract_ts_tests(file_path, rel))
                else:
                    surfaces.extend(_extract_ts_surfaces(file_path, rel))
                continue

            if name in HEADING_DOC_NAMES:
                surfaces.extend(_extract_doc_headings(file_path, rel))

    return {"languages": languages, "surfaces": surfaces}


# --------------------------------------------------------------------------- #
# Evidence resolution
# --------------------------------------------------------------------------- #


# Recognized surface-ref prefixes; values are tuples (kind, validator).
_SURFACE_REF_RE = re.compile(r"^(?P<prefix>[a-zA-Z][a-zA-Z0-9_-]*)\s*:\s*(?P<rest>.+?)\s*$")
_ROUTE_REST_RE = re.compile(r"^(?P<method>[A-Z]+)\s+(?P<path>/\S*)\s*$")


def _validate_surface_ref(ref: str) -> bool:
    match = _SURFACE_REF_RE.match(ref)
    if not match:
        return False
    prefix = match.group("prefix").lower()
    rest = match.group("rest").strip()
    if not rest:
        return False
    if prefix == "cli":
        return bool(rest)
    if prefix == "route":
        rmatch = _ROUTE_REST_RE.match(rest)
        if not rmatch:
            return False
        return rmatch.group("method").lower() in HTTP_METHODS
    if prefix == "bin":
        return bool(rest)
    if prefix in ("exports", "export"):
        return bool(rest)
    if prefix in ("test", "heading", "doc"):
        return bool(rest)
    # Unknown prefix.
    return False


def _resolve_test_ref(repo_root: Path, ref: str) -> list[str]:
    """Resolve a single test ref against ``repo_root``.

    A ref is either an exact path or a glob. Returns a list of matching
    relative paths (POSIX form).
    """
    ref = ref.strip()
    if not ref:
        return []
    # Exact file path.
    candidate = (repo_root / ref)
    if candidate.is_file():
        return [Path(ref).as_posix()]
    # Glob path. Always relative to repo_root; reject absolute.
    if ref.startswith("/"):
        return []
    matches = sorted(p.relative_to(repo_root).as_posix() for p in repo_root.glob(ref) if p.is_file())
    return matches


def resolve_evidence(repo_root: Path, story: Any) -> dict[str, Any]:
    """Resolve a story's declared evidence refs against the repo.

    ``story`` is expected to expose ``evidence_tests``, ``evidence_surface``,
    and ``evidence_docs`` (as produced by ``storystore_lib.parse_story``).
    """
    repo_root = Path(repo_root).resolve()

    tests_resolved: list[str] = []
    tests_missing: list[str] = []
    seen_tests: set[str] = set()
    for ref in getattr(story, "evidence_tests", []) or []:
        matches = _resolve_test_ref(repo_root, ref)
        if matches:
            for m in matches:
                if m not in seen_tests:
                    seen_tests.add(m)
                    tests_resolved.append(m)
        else:
            tests_missing.append(ref)

    surface_refs: list[dict[str, Any]] = []
    for ref in getattr(story, "evidence_surface", []) or []:
        surface_refs.append({"ref": ref, "valid": _validate_surface_ref(ref)})

    docs_resolved: list[str] = []
    docs_missing: list[str] = []
    for ref in getattr(story, "evidence_docs", []) or []:
        ref = ref.strip()
        if not ref:
            continue
        candidate = repo_root / ref
        if candidate.is_file():
            docs_resolved.append(Path(ref).as_posix())
        else:
            docs_missing.append(ref)

    return {
        "tests_resolved": tests_resolved,
        "tests_missing": tests_missing,
        "surface_refs": surface_refs,
        "docs_resolved": docs_resolved,
        "docs_missing": docs_missing,
    }


# --------------------------------------------------------------------------- #
# Debug entry point
# --------------------------------------------------------------------------- #


def _debug_main() -> int:  # pragma: no cover - convenience only
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="Print inventory JSON for a repo.")
    parser.add_argument("--repo-root", required=True)
    parser.add_argument("--source-root", default=None)
    parser.add_argument("--include-dir", action="append", default=[])
    args = parser.parse_args()
    inv = build_inventory(
        Path(args.repo_root),
        source_root=args.source_root,
        include_dirs=args.include_dir or None,
    )
    json.dump(inv, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_debug_main())
