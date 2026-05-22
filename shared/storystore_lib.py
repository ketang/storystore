"""Storystore shared library: frontmatter parsing and story loading.

Stdlib-only implementation of the strict YAML subset described in
``shared/spec.md``. See that spec for the authoritative schema.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

REQUIRED_FIELDS = ("title", "slug", "status", "authority", "change_resistance")
OPTIONAL_FIELDS = (
    "schema_version",
    "tests_applicable",
    "locked_sections",
    "last_audited",
)
KNOWN_FIELDS = set(REQUIRED_FIELDS) | set(OPTIONAL_FIELDS)

STATUS_VALUES = ("draft", "active", "deprecated")
AUTHORITY_VALUES = ("observed", "accepted")
RESISTANCE_VALUES = ("low", "medium", "high", "immutable")

LOADER_SKIP = {"README.md", "INDEX.md", "drift-todo.md"}

SECTION_NAMES = (
    "Intent",
    "Story",
    "Expected Behavior",
    "Boundaries",
    "Auditable Claims",
    "Evidence",
    "Drift Notes",
)

LOCK_BEGIN_RE = re.compile(r"<!--\s*lock:begin\s*-->")
LOCK_END_RE = re.compile(r"<!--\s*lock:end\s*-->")
ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class ParseError(Exception):
    """Raised on malformed frontmatter or story body.

    ``exit_code`` follows spec.md: 2 for parse/shape errors, 3 for
    validity-matrix violations.
    """

    def __init__(
        self,
        message: str,
        *,
        line: int = 0,
        column: int = 0,
        path: Optional[Path] = None,
        exit_code: int = 2,
    ) -> None:
        self.message = message
        self.line = line
        self.column = column
        self.path = path
        self.exit_code = exit_code
        loc = f"{path}:" if path else ""
        if line:
            loc = f"{loc}{line}:{column}: " if column else f"{loc}{line}: "
        super().__init__(f"{loc}{message}" if loc else message)


@dataclass
class LockedBlock:
    start_line: int
    end_line: int
    text: str


@dataclass
class Story:
    path: Path
    schema_version: int = 1
    title: str = ""
    slug: str = ""
    status: str = ""
    authority: str = ""
    change_resistance: str = ""
    tests_applicable: bool = True
    locked_sections: list[str] = field(default_factory=list)
    last_audited: Optional[str] = None
    sections: dict[str, str] = field(default_factory=dict)
    locked_blocks: list[LockedBlock] = field(default_factory=list)
    evidence_tests: list[str] = field(default_factory=list)
    evidence_surface: list[str] = field(default_factory=list)
    evidence_docs: list[str] = field(default_factory=list)


class PerfTimer:
    """Minimal phase timer used by runtime scripts to emit perf blocks."""

    def __init__(self) -> None:
        self._start = time.perf_counter()
        self._phases: dict[str, float] = {}
        self._marks: dict[str, float] = {}

    def start(self, phase: str) -> None:
        self._marks[phase] = time.perf_counter()

    def stop(self, phase: str) -> None:
        if phase in self._marks:
            self._phases[phase] = self._phases.get(phase, 0.0) + (
                time.perf_counter() - self._marks.pop(phase)
            )

    def total_ms(self) -> int:
        return int((time.perf_counter() - self._start) * 1000)

    def phases_ms(self) -> dict[str, int]:
        return {k: int(v * 1000) for k, v in self._phases.items()}


def _split_frontmatter(text: str, path: Optional[Path] = None) -> tuple[str, str, int]:
    """Return (frontmatter_text, body_text, body_start_line).

    body_start_line is 1-based line number of the first body line.
    """
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].rstrip("\r\n") != "---":
        raise ParseError("missing frontmatter (file must start with '---')", line=1, column=1, path=path)
    end_idx = None
    for i in range(1, len(lines)):
        if lines[i].rstrip("\r\n") == "---":
            end_idx = i
            break
    if end_idx is None:
        raise ParseError("unterminated frontmatter (missing closing '---')", line=1, column=1, path=path)
    fm = "".join(lines[1:end_idx])
    body = "".join(lines[end_idx + 1 :])
    return fm, body, end_idx + 2


def _parse_scalar(raw: str, line: int, col: int, path: Optional[Path]) -> Any:
    s = raw.strip()
    if s == "":
        raise ParseError("empty scalar value", line=line, column=col, path=path)
    if s.startswith(("'", '"', "&", "*", "{", "[", ">", "|")):
        if s.startswith(("&", "*")):
            raise ParseError("anchors and aliases are not allowed", line=line, column=col, path=path)
        if s.startswith("{"):
            raise ParseError("flow mappings are not allowed", line=line, column=col, path=path)
        if s.startswith(("|", ">")):
            raise ParseError("multi-line strings are not allowed", line=line, column=col, path=path)
        if s.startswith("["):
            # flow list — parsed at the field level; caller should not get here
            raise ParseError("inline list not allowed at this position", line=line, column=col, path=path)
        raise ParseError("quoted strings are not allowed", line=line, column=col, path=path)
    low = s.lower()
    if low in ("true", "false"):
        return low == "true"
    if ISO_DATE_RE.match(s):
        return s
    if s.lstrip("-").isdigit():
        try:
            return int(s)
        except ValueError:
            pass
    return s


def _parse_flow_list(raw: str, line: int, col: int, path: Optional[Path]) -> list[str]:
    s = raw.strip()
    if not (s.startswith("[") and s.endswith("]")):
        raise ParseError("malformed flow list", line=line, column=col, path=path)
    inner = s[1:-1].strip()
    if not inner:
        return []
    items = [p.strip() for p in inner.split(",")]
    for i, item in enumerate(items):
        if not item:
            raise ParseError("empty list item", line=line, column=col, path=path)
        if item.startswith(("'", '"', "[", "{")):
            raise ParseError("nested/quoted list items are not allowed", line=line, column=col, path=path)
    return items


def parse_frontmatter(text: str, path: Optional[Path] = None) -> dict[str, Any]:
    """Parse a story's full text and return validated frontmatter mapping.

    Raises ParseError (exit_code 2 for parse/shape, 3 for validity-matrix).
    """
    fm_text, body, _ = _split_frontmatter(text, path=path)
    data: dict[str, Any] = {}
    lines = fm_text.splitlines()
    i = 0
    while i < len(lines):
        raw = lines[i]
        # frontmatter line numbers in the original file: +2 offset (1-based; line 1 is the opening ---)
        file_line = i + 2
        stripped = raw.rstrip()
        if stripped == "" or stripped.lstrip().startswith("#"):
            i += 1
            continue
        if raw.startswith((" ", "\t")):
            raise ParseError(
                "unexpected indentation; nested mappings are not allowed",
                line=file_line, column=1, path=path,
            )
        if raw.lstrip().startswith("- "):
            raise ParseError(
                "unexpected list item at top level",
                line=file_line, column=1, path=path,
            )
        if ":" not in raw:
            raise ParseError("expected 'key: value' line", line=file_line, column=1, path=path)
        key, sep, rest = raw.partition(":")
        key = key.strip()
        if not key:
            raise ParseError("empty key", line=file_line, column=1, path=path)
        if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", key):
            raise ParseError(f"invalid key syntax: {key!r}", line=file_line, column=1, path=path)
        if key in data:
            raise ParseError(f"duplicate key: {key!r}", line=file_line, column=1, path=path)
        value_col = len(key) + 2
        value_text = rest.strip()
        if value_text == "":
            # block list expected on following lines
            block_items: list[str] = []
            j = i + 1
            while j < len(lines):
                nxt = lines[j]
                nstripped = nxt.strip()
                if nstripped == "" or nstripped.startswith("#"):
                    j += 1
                    continue
                if not nxt.startswith(("  ", "\t")) and not nstripped.startswith("- "):
                    break
                if not nstripped.startswith("- "):
                    raise ParseError(
                        "expected list item ('- value') after empty mapping value",
                        line=j + 2, column=1, path=path,
                    )
                item = nstripped[2:].strip()
                if not item:
                    raise ParseError("empty list item", line=j + 2, column=1, path=path)
                if item.startswith(("'", '"', "[", "{")):
                    raise ParseError(
                        "nested/quoted list items are not allowed",
                        line=j + 2, column=1, path=path,
                    )
                block_items.append(item)
                j += 1
            if not block_items:
                raise ParseError(
                    f"empty value for key {key!r}", line=file_line, column=value_col, path=path,
                )
            data[key] = block_items
            i = j
            continue
        if value_text.startswith("["):
            data[key] = _parse_flow_list(value_text, file_line, value_col, path)
        else:
            data[key] = _parse_scalar(value_text, file_line, value_col, path)
        i += 1

    _validate_frontmatter(data, path=path)
    return data


def _validate_frontmatter(data: dict[str, Any], *, path: Optional[Path]) -> None:
    for key in data:
        if key not in KNOWN_FIELDS:
            raise ParseError(f"unknown frontmatter key: {key!r}", path=path)
    for req in REQUIRED_FIELDS:
        if req not in data:
            raise ParseError(f"missing required frontmatter key: {req!r}", path=path)

    if not isinstance(data["title"], str) or not data["title"].strip():
        raise ParseError("title must be a non-empty string", path=path)
    if not isinstance(data["slug"], str) or not data["slug"].strip():
        raise ParseError("slug must be a non-empty string", path=path)

    if data["status"] not in STATUS_VALUES:
        raise ParseError(
            f"invalid status {data['status']!r}; expected one of {list(STATUS_VALUES)}",
            path=path,
        )
    if data["authority"] not in AUTHORITY_VALUES:
        raise ParseError(
            f"invalid authority {data['authority']!r}; expected one of {list(AUTHORITY_VALUES)}",
            path=path,
        )
    if data["change_resistance"] not in RESISTANCE_VALUES:
        raise ParseError(
            f"invalid change_resistance {data['change_resistance']!r}; "
            f"expected one of {list(RESISTANCE_VALUES)}",
            path=path,
        )

    if "schema_version" in data:
        if not isinstance(data["schema_version"], int) or isinstance(data["schema_version"], bool):
            raise ParseError("schema_version must be an integer", path=path)
    else:
        data["schema_version"] = 1

    if "tests_applicable" in data:
        if not isinstance(data["tests_applicable"], bool):
            raise ParseError("tests_applicable must be a boolean", path=path)
    else:
        data["tests_applicable"] = True

    if "locked_sections" in data:
        if not isinstance(data["locked_sections"], list):
            raise ParseError("locked_sections must be a list", path=path)
        for item in data["locked_sections"]:
            if not isinstance(item, str) or not item.strip():
                raise ParseError("locked_sections entries must be non-empty strings", path=path)
    else:
        data["locked_sections"] = []

    if "last_audited" in data:
        v = data["last_audited"]
        if not isinstance(v, str) or not ISO_DATE_RE.match(v):
            raise ParseError("last_audited must be an ISO date (YYYY-MM-DD)", path=path)

    # Validity matrix: observed authority excludes high/immutable resistance.
    if data["authority"] == "observed" and data["change_resistance"] in ("high", "immutable"):
        raise ParseError(
            "validity matrix violation: authority=observed cannot pair with "
            f"change_resistance={data['change_resistance']}",
            path=path,
            exit_code=3,
        )


def _parse_sections(body: str, body_start_line: int, path: Optional[Path]) -> dict[str, str]:
    sections: dict[str, str] = {}
    lines = body.splitlines()
    current: Optional[str] = None
    buf: list[str] = []
    for line in lines:
        if line.startswith("## "):
            if current is not None:
                sections[current] = "\n".join(buf).strip("\n")
            current = line[3:].strip()
            buf = []
        else:
            if current is not None:
                buf.append(line)
    if current is not None:
        sections[current] = "\n".join(buf).strip("\n")
    return sections


def _parse_evidence(section_text: str) -> tuple[list[str], list[str], list[str]]:
    tests: list[str] = []
    surface: list[str] = []
    docs: list[str] = []
    current: Optional[str] = None
    for line in section_text.splitlines():
        s = line.strip()
        if line.startswith("### "):
            heading = line[4:].strip().lower()
            current = heading
            continue
        if s.startswith("- ") or s.startswith("* "):
            item = s[2:].strip()
            if not item:
                continue
            # strip backticks around path
            if item.startswith("`") and item.endswith("`") and len(item) >= 2:
                item = item[1:-1]
            if current == "tests":
                tests.append(item)
            elif current == "surface":
                surface.append(item)
            elif current == "docs":
                docs.append(item)
    return tests, surface, docs


def _parse_locked_blocks(body: str, body_start_line: int, path: Optional[Path]) -> list[LockedBlock]:
    blocks: list[LockedBlock] = []
    lines = body.splitlines()
    i = 0
    while i < len(lines):
        if LOCK_BEGIN_RE.search(lines[i]):
            start_line = body_start_line + i
            j = i + 1
            inner: list[str] = []
            while j < len(lines):
                if LOCK_END_RE.search(lines[j]):
                    blocks.append(
                        LockedBlock(
                            start_line=start_line,
                            end_line=body_start_line + j,
                            text="\n".join(inner),
                        )
                    )
                    break
                if LOCK_BEGIN_RE.search(lines[j]):
                    raise ParseError(
                        "nested or unclosed locked block",
                        line=body_start_line + j, column=1, path=path,
                    )
                inner.append(lines[j])
                j += 1
            else:
                raise ParseError(
                    "unterminated locked block (missing <!-- lock:end -->)",
                    line=start_line, column=1, path=path,
                )
            i = j + 1
            continue
        if LOCK_END_RE.search(lines[i]):
            raise ParseError(
                "stray <!-- lock:end --> without matching begin",
                line=body_start_line + i, column=1, path=path,
            )
        i += 1
    return blocks


def parse_story(path: Path, text: Optional[str] = None) -> Story:
    """Parse a single story file into a Story dataclass."""
    if text is None:
        text = path.read_text(encoding="utf-8")
    fm_text, body, body_start_line = _split_frontmatter(text, path=path)
    data = parse_frontmatter(text, path=path)
    sections = _parse_sections(body, body_start_line, path=path)
    if "Intent" not in sections or not sections["Intent"].strip():
        raise ParseError("missing required Intent section", path=path)
    locked_blocks = _parse_locked_blocks(body, body_start_line, path=path)
    tests, surface, docs = _parse_evidence(sections.get("Evidence", ""))

    if data["tests_applicable"] is False and tests:
        raise ParseError(
            "tests_applicable=false conflicts with non-empty Evidence.Tests",
            path=path,
        )

    return Story(
        path=path,
        schema_version=data["schema_version"],
        title=data["title"],
        slug=data["slug"],
        status=data["status"],
        authority=data["authority"],
        change_resistance=data["change_resistance"],
        tests_applicable=data["tests_applicable"],
        locked_sections=list(data["locked_sections"]),
        last_audited=data.get("last_audited"),
        sections=sections,
        locked_blocks=locked_blocks,
        evidence_tests=tests,
        evidence_surface=surface,
        evidence_docs=docs,
    )


def load_stories(stories_dir: Path) -> list[Story]:
    """Load every story file under ``stories_dir`` (non-recursive)."""
    if not stories_dir.exists():
        raise ParseError(f"stories directory does not exist: {stories_dir}")
    if not stories_dir.is_dir():
        raise ParseError(f"stories path is not a directory: {stories_dir}")
    out: list[Story] = []
    for path in sorted(stories_dir.iterdir()):
        if not path.is_file():
            continue
        if path.suffix != ".md":
            continue
        if path.name in LOADER_SKIP:
            continue
        out.append(parse_story(path))
    return out
