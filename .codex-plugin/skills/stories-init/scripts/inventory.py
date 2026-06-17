"""Storystore inventory: language detection, surface extraction, evidence resolution.

Stdlib-only. Used by ``stories-audit`` and ``stories-coverage`` to:

- detect repository languages by marker files at depth <= 2;
- extract user-facing surface inventory (TypeScript-first, plus
  language-agnostic skill directories named by ``SKILL.md`` markers);
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

# Marker file whose containing directory names a skill surface. Inventoried
# regardless of detected language so ``skill:`` refs resolve in Python- and
# markdown-centric repos that ship no TypeScript surfaces.
SKILL_MARKER_NAME: str = "SKILL.md"

HTTP_METHODS: tuple[str, ...] = ("get", "post", "put", "patch", "delete", "head", "options", "all")

# Migration file patterns to search for schema evidence resolution.
MIGRATION_GLOB_PATTERNS: tuple[str, ...] = (
    "db/migrate/*.rb",
    "db/migrate/**/*.rb",
    "migrations/*.sql",
    "migrations/**/*.sql",
    "db/migrations/*.sql",
    "db/migrations/**/*.sql",
    "*/migrations/*.py",
    "*/migrations/**/*.py",
    "alembic/versions/*.py",
    "alembic/versions/**/*.py",
    "prisma/migrations/**/*.sql",
)

# Regex for validating a schema ref: table.column
_SCHEMA_REF_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*\.[a-zA-Z_][a-zA-Z0-9_]*$")

# Regex for validating a flag ref: bare identifier (letters, digits, underscores, hyphens)
_FLAG_REF_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_-]*$")

# File extensions to scan when searching for flag definitions.
_FLAG_SOURCE_EXTENSIONS: frozenset[str] = frozenset({
    ".rb", ".py", ".js", ".ts", ".tsx", ".jsx", ".mjs", ".cjs",
    ".yml", ".yaml", ".json",
})

# Compiled patterns for flag definition discovery across common frameworks.
# Each returns a regex that, given a flag identifier, matches lines defining it.
def _flag_definition_patterns(identifier: str) -> list[re.Pattern[str]]:
    """Build regexes that match common flag-definition patterns for *identifier*."""
    # Escape for use in regex.
    esc = re.escape(identifier)
    return [
        # Ruby: feature_flag :identifier  or  feature :identifier
        re.compile(rf"""(?:feature_flag|feature|flag)\s+[:'\"]?{esc}['"]?""", re.IGNORECASE),
        # Python/JS/TS dict/object key: "identifier" or 'identifier' as key
        re.compile(rf"""['\"]?{esc}['\"]?\s*[:=]"""),
        # YAML key: identifier:  (at start of line or indented)
        re.compile(rf"""^\s*{esc}\s*:""", re.MULTILINE),
    ]

# Regex for validating a copy ref: <file>#<key>
# File must end with .json, .yaml, or .yml; key is a dot-separated path.
_COPY_REF_RE = re.compile(r"^(?P<file>[^\s#]+\.(?:json|ya?ml))#(?P<key>[a-zA-Z_][a-zA-Z0-9_.]*[a-zA-Z0-9_])$")


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


def _extract_skill_dir(file_path: Path, rel_source: str) -> list[dict[str, Any]]:
    """Emit a skill surface for a ``SKILL.md`` marker file.

    The skill name is the marker's containing directory name (e.g.
    ``skills/foo/SKILL.md`` -> ``foo``). A marker at the repository root has
    no naming directory and is skipped.
    """
    # A root-level marker (``rel_source == "SKILL.md"``) has no containing
    # directory inside the repo; the parent's basename would be the arbitrary
    # repo-root directory name, so skip it.
    if "/" not in rel_source:
        return []
    name = file_path.parent.name
    if not name:
        return []
    return [{"kind": "skill", "name": name, "source": rel_source}]


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

            if name == SKILL_MARKER_NAME:
                surfaces.extend(_extract_skill_dir(file_path, rel))
                continue

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


def validate_surface_ref(ref: str) -> bool:
    """Public wrapper: return whether *ref* is an accepted surface-ref form.

    Generators call this before writing ``Evidence.Surface`` entries so they
    cannot emit a ref the audit validator would later reject.
    """
    return _validate_surface_ref(ref)


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
    if prefix == "skill":
        return bool(rest)
    if prefix in ("test", "heading", "doc"):
        return bool(rest)
    if prefix == "schema":
        return bool(_SCHEMA_REF_RE.match(rest))
    if prefix == "flag":
        return bool(_FLAG_REF_RE.match(rest))
    if prefix == "copy":
        return bool(_COPY_REF_RE.match(rest))
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


def _find_migration_files(repo_root: Path) -> list[Path]:
    """Discover migration files under ``repo_root`` using known patterns."""
    seen: set[Path] = set()
    results: list[Path] = []
    for pattern in MIGRATION_GLOB_PATTERNS:
        for p in repo_root.glob(pattern):
            if p.is_file() and p not in seen:
                seen.add(p)
                results.append(p)
    results.sort()
    return results


def _resolve_schema_ref(
    repo_root: Path, ref: str, migration_files: Optional[list[Path]] = None
) -> Optional[dict[str, Any]]:
    """Resolve a single ``schema:<table>.<column>`` ref against migrations.

    Returns ``{"file": "<relative path>", "line": <1-based>}`` for the most
    recent migration that defines or alters the column, or ``None`` when not
    found.
    """
    ref = ref.strip()
    if not _SCHEMA_REF_RE.match(ref):
        return None

    table, column = ref.split(".", 1)

    if migration_files is None:
        migration_files = _find_migration_files(repo_root)

    # Build patterns to search for in migration files.
    # We look for the column name in the context of the table name.
    # Common patterns across frameworks:
    #   SQL:   CREATE TABLE users (... email ...); ALTER TABLE users ADD COLUMN email
    #   Rails: create_table :users ... t.string :email; add_column :users, :email
    #   Django/Alembic: table references with column names
    table_lower = table.lower()
    column_lower = column.lower()

    best_match: Optional[dict[str, Any]] = None

    for mig_path in migration_files:
        try:
            text = mig_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue

        text_lower = text.lower()
        # Quick check: both table and column must appear in the file.
        if table_lower not in text_lower or column_lower not in text_lower:
            continue

        # Scan line by line for column references in the context of the table.
        lines = text.splitlines()
        in_table_context = False
        last_match_line: Optional[int] = None

        for i, line in enumerate(lines):
            ll = line.lower().strip()

            # Detect table context.
            if table_lower in ll:
                in_table_context = True

            # Look for column in a table context.
            if in_table_context and column_lower in ll:
                last_match_line = i + 1  # 1-based

            # Reset context on blank lines or end-of-statement in SQL.
            if not ll or ll == "end" or ll.endswith(";"):
                if last_match_line is not None:
                    # Keep the match; context resets but we found something.
                    pass
                in_table_context = False

        if last_match_line is not None:
            rel = mig_path.relative_to(repo_root).as_posix()
            best_match = {"file": rel, "line": last_match_line}
            # Continue to find the most recent (last in sorted order).

    return best_match


def _resolve_flag_ref(
    repo_root: Path, identifier: str
) -> Optional[dict[str, Any]]:
    """Resolve a single ``flag:<identifier>`` ref against the host repo.

    Searches source files recursively for common flag-definition patterns
    (Ruby ``feature_flag``, Python/JS dict keys, YAML keys).

    Returns ``{"file": "<relative path>", "line": <1-based>}`` for the first
    match, or ``None`` when not found.
    """
    identifier = identifier.strip()
    if not _FLAG_REF_RE.match(identifier):
        return None

    patterns = _flag_definition_patterns(identifier)
    skip = _effective_skip_dirs(None)

    for dirpath, dirnames, filenames in os.walk(repo_root):
        dirnames[:] = sorted(d for d in dirnames if d not in skip)
        for name in sorted(filenames):
            file_path = Path(dirpath) / name
            if file_path.suffix not in _FLAG_SOURCE_EXTENSIONS:
                continue
            try:
                text = file_path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            for i, line in enumerate(text.splitlines()):
                for pat in patterns:
                    if pat.search(line):
                        rel = file_path.relative_to(repo_root).as_posix()
                        return {"file": rel, "line": i + 1}

    return None


def _resolve_copy_ref(
    repo_root: Path, ref: str
) -> Optional[dict[str, Any]]:
    """Resolve a single ``copy:<file>#<key>`` ref against locale files.

    Returns ``{"file": "<relative path>", "line": <1-based>}`` when the key
    is found, or ``None`` when the file does not exist or the key path is
    missing.
    """
    ref = ref.strip()
    m = _COPY_REF_RE.match(ref)
    if not m:
        return None

    file_rel = m.group("file")
    key_path = m.group("key")
    locale_path = repo_root / file_rel

    if not locale_path.is_file():
        return None

    try:
        text = locale_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None

    suffix = locale_path.suffix.lower()
    if suffix == ".json":
        try:
            data = json.loads(text)
        except (json.JSONDecodeError, ValueError):
            return None
    elif suffix in (".yaml", ".yml"):
        # Minimal YAML-subset parser for flat/nested string mappings.
        # Avoids a PyYAML dependency; handles the common locale-file shape.
        data = _parse_simple_yaml(text)
        if data is None:
            return None
    else:
        return None

    # Navigate the dot-separated key path.
    keys = key_path.split(".")
    current = data
    for segment in keys:
        if not isinstance(current, dict) or segment not in current:
            return None
        current = current[segment]

    # Find the line number of the final key in the file.
    line_no = _find_key_line(text, keys, suffix)
    return {"file": file_rel, "line": line_no}


def _parse_simple_yaml(text: str) -> Optional[dict[str, Any]]:
    """Parse a minimal YAML subset sufficient for locale files.

    Handles nested mappings with consistent 2-space indentation and scalar
    string values.  Returns ``None`` on anything too complex.
    """
    root: dict[str, Any] = {}
    # Stack of (indent_level, dict_ref)
    stack: list[tuple[int, dict[str, Any]]] = [(-1, root)]

    for line in text.splitlines():
        stripped = line.lstrip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(line) - len(stripped)
        if ":" not in stripped:
            continue

        key_part, _, value_part = stripped.partition(":")
        key = key_part.strip()
        value = value_part.strip()

        # Pop stack to find parent at lower indent.
        while len(stack) > 1 and stack[-1][0] >= indent:
            stack.pop()

        parent = stack[-1][1]

        if value == "" or value.startswith("#"):
            # Mapping node — next indented lines are children.
            child: dict[str, Any] = {}
            parent[key] = child
            stack.append((indent, child))
        else:
            # Strip surrounding quotes from value.
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
                value = value[1:-1]
            parent[key] = value

    return root


def _find_key_line(text: str, keys: list[str], suffix: str) -> int:
    """Find the 1-based line number of the deepest key in the file.

    For JSON files, tracks brace/bracket depth to find the key at the right
    nesting level. For YAML files, tracks indentation context.
    """
    lines = text.splitlines()

    if suffix == ".json":
        # Walk through looking for each key in sequence at increasing depth.
        target_depth = 0
        current_depth = 0
        key_idx = 0
        last_line = 1

        for i, line in enumerate(lines):
            stripped = line.strip()
            for ch in stripped:
                if ch == "{":
                    current_depth += 1
                elif ch == "}":
                    current_depth -= 1
                elif ch == "[":
                    current_depth += 1
                elif ch == "]":
                    current_depth -= 1

            if key_idx < len(keys):
                # Look for the key at the expected depth.
                target_key = keys[key_idx]
                # Check for "key": pattern in JSON.
                if (f'"{target_key}"' in stripped) and current_depth == target_depth + 1:
                    last_line = i + 1
                    key_idx += 1
                    target_depth += 1

        return last_line
    else:
        # YAML: look for keys at increasing indentation.
        expected_indent = 0
        key_idx = 0
        last_line = 1

        for i, line in enumerate(lines):
            stripped = line.lstrip()
            if not stripped or stripped.startswith("#"):
                continue
            indent = len(line) - len(stripped)
            if key_idx < len(keys):
                target_key = keys[key_idx]
                if stripped.startswith(target_key + ":") and indent == expected_indent:
                    last_line = i + 1
                    key_idx += 1
                    expected_indent = indent + 2  # Assume 2-space indent.

        return last_line


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

    # Schema evidence resolution.
    schema_resolved: list[dict[str, Any]] = []
    schema_missing: list[str] = []
    schema_refs_raw = getattr(story, "evidence_schema", []) or []
    if schema_refs_raw:
        migration_files = _find_migration_files(repo_root)
        for ref in schema_refs_raw:
            ref = ref.strip()
            if not ref:
                continue
            if not _SCHEMA_REF_RE.match(ref):
                schema_missing.append(ref)
                continue
            result = _resolve_schema_ref(repo_root, ref, migration_files)
            if result is not None:
                schema_resolved.append({"ref": ref, **result})
            else:
                schema_missing.append(ref)

    # Flag evidence resolution.
    flag_resolved: list[dict[str, Any]] = []
    flag_missing: list[str] = []
    flag_refs_raw = getattr(story, "evidence_flag", []) or []
    for ref in flag_refs_raw:
        ref = ref.strip()
        if not ref:
            continue
        if not _FLAG_REF_RE.match(ref):
            flag_missing.append(ref)
            continue
        result = _resolve_flag_ref(repo_root, ref)
        if result is not None:
            flag_resolved.append({"ref": ref, **result})
        else:
            flag_missing.append(ref)

    # Copy evidence resolution.
    copy_resolved: list[dict[str, Any]] = []
    copy_missing: list[str] = []
    copy_refs_raw = getattr(story, "evidence_copy", []) or []
    for ref in copy_refs_raw:
        ref = ref.strip()
        if not ref:
            continue
        if not _COPY_REF_RE.match(ref):
            copy_missing.append(ref)
            continue
        result = _resolve_copy_ref(repo_root, ref)
        if result is not None:
            copy_resolved.append({"ref": ref, **result})
        else:
            copy_missing.append(ref)

    return {
        "tests_resolved": tests_resolved,
        "tests_missing": tests_missing,
        "surface_refs": surface_refs,
        "docs_resolved": docs_resolved,
        "docs_missing": docs_missing,
        "schema_resolved": schema_resolved,
        "schema_missing": schema_missing,
        "flag_resolved": flag_resolved,
        "flag_missing": flag_missing,
        "copy_resolved": copy_resolved,
        "copy_missing": copy_missing,
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
