#!/usr/bin/env python3
"""Apply a single guarded edit to a story file.

Command:

    edit_section.py --repo-root <repo-root> --story <slug> \
        --section <name> --content <text> \
        [--allow-claim-reduction] [--confirm-resistance-change]

``--section`` accepts either:
    * a body section heading (``Intent``, ``Story``, ``Expected Behavior``,
      ``Boundaries``, ``Auditable Claims``, ``Evidence``, ``Drift Notes``)
    * a metadata frontmatter field (``title``, ``status``, ``authority``,
      ``change_resistance``)

Policy gates (hard refusals):
    1. Section listed in ``locked_sections``                     -> exit 3
    2. Existing section body contains an inline locked block     -> exit 3
    3. Editing ``Auditable Claims`` so the bullet count drops
       without ``--allow-claim-reduction``                       -> exit 3
    4. ``change_resistance: immutable``                          -> exit 3
    5. Increasing ``change_resistance`` to a higher level without
       ``--confirm-resistance-change``                           -> exit 4

After a successful edit, regenerates ``docs/stories/INDEX.md`` only when
title/status/authority/change_resistance changed. ``last_audited`` is
never bumped by this script.

Stdout JSON on success::

    {"edited": true, "section": "Story", "index_updated": false}

Exit codes:
    0  success
    2  invalid input or malformed story
    3  policy refusal (locked, inline locked, claim reduction, immutable)
    4  resistance-change confirmation required
"""

from __future__ import annotations

import argparse
import datetime as _dt
import importlib.util
import json
import re
import sys
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
LIB_PATH = SCRIPT_DIR / "storystore_lib.py"

METADATA_FIELDS = ("title", "status", "authority", "change_resistance")
RESISTANCE_RANK = {"low": 0, "medium": 1, "high": 2, "immutable": 3}

LOCK_MARKER_RE = re.compile(r"<!--\s*lock:(begin|end)\s*-->")


def _load_lib():
    if "storystore_lib" in sys.modules:
        return sys.modules["storystore_lib"]
    spec = importlib.util.spec_from_file_location("storystore_lib", LIB_PATH)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules["storystore_lib"] = mod
    spec.loader.exec_module(mod)
    return mod


def _die(msg: str, code: int = 2) -> None:
    print(msg, file=sys.stderr)
    sys.exit(code)


def _count_claims(section_text: str) -> int:
    n = 0
    for raw in section_text.splitlines():
        if raw.startswith("- "):
            n += 1
    return n


def _replace_section(text: str, section: str, new_body: str) -> str:
    """Replace the body content of ``## <section>`` with ``new_body``.

    Preserves the file's frontmatter and the section heading line itself.
    If the section is absent, raises a parse-style error (exit 2).
    """
    lines = text.splitlines(keepends=True)
    heading = f"## {section}"
    start = None
    for i, line in enumerate(lines):
        if line.rstrip("\r\n") == heading:
            start = i
            break
    if start is None:
        _die(f"section not found in story: {section!r}", 2)

    end = len(lines)
    for j in range(start + 1, len(lines)):
        s = lines[j]
        if s.startswith("## "):
            end = j
            break

    new_body = new_body.rstrip("\n") + "\n"
    # blank line after heading, then body, then trailing blank before next section
    rebuilt = [lines[start], "\n", new_body]
    if end < len(lines):
        rebuilt.append("\n")
    return "".join(lines[:start]) + "".join(rebuilt) + "".join(lines[end:])


def _render_frontmatter(data: dict[str, Any]) -> str:
    out: list[str] = ["---"]
    out.append(f"schema_version: {int(data.get('schema_version', 1))}")
    out.append(f"title: {data['title']}")
    out.append(f"slug: {data['slug']}")
    out.append(f"status: {data['status']}")
    out.append(f"authority: {data['authority']}")
    out.append(f"change_resistance: {data['change_resistance']}")
    if data.get("tests_applicable", True) is False:
        out.append("tests_applicable: false")
    locked = data.get("locked_sections") or []
    if locked:
        out.append("locked_sections:")
        for item in locked:
            out.append(f"  - {item}")
    else:
        out.append("locked_sections: []")
    if data.get("last_audited"):
        out.append(f"last_audited: {data['last_audited']}")
    out.append("---")
    return "\n".join(out) + "\n"


def _replace_frontmatter(text: str, new_fm_text: str) -> str:
    """Replace the YAML frontmatter block (between leading --- and ---)."""
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].rstrip("\r\n") != "---":
        _die("missing frontmatter", 2)
    end_idx = None
    for i in range(1, len(lines)):
        if lines[i].rstrip("\r\n") == "---":
            end_idx = i
            break
    if end_idx is None:
        _die("unterminated frontmatter", 2)
    return new_fm_text + "".join(lines[end_idx + 1 :])


def _regenerate_index(stories_dir: Path, lib) -> None:
    entries: list[tuple[str, str, str, str, str]] = []
    for path in sorted(stories_dir.iterdir()):
        if not path.is_file() or path.suffix != ".md":
            continue
        if path.name in lib.LOADER_SKIP:
            continue
        try:
            data = lib.parse_frontmatter(path.read_text(encoding="utf-8"), path=path)
        except lib.ParseError:
            continue
        entries.append(
            (
                data["slug"],
                data["title"],
                data["status"],
                data["authority"],
                data["change_resistance"],
            )
        )
    entries.sort(key=lambda e: e[0])
    timestamp = _dt.datetime.now(tz=_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    out = ["# Intent Story Index", "", f"{len(entries)} stories — generated {timestamp}", ""]
    for slug, title, status, authority, resistance in entries:
        out.append(
            f"- [{slug}]({slug}.md) — {title} *({status}, {authority}, {resistance})*"
        )
    body = "\n".join(out)
    if not body.endswith("\n"):
        body += "\n"
    (stories_dir / "INDEX.md").write_text(body, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", required=True, type=Path)
    parser.add_argument("--story", required=True, help="Story slug (e.g. login).")
    parser.add_argument("--section", required=True)
    parser.add_argument(
        "--content",
        required=True,
        help="New content for the section (or new value for a metadata field).",
    )
    parser.add_argument("--allow-claim-reduction", action="store_true")
    parser.add_argument("--confirm-resistance-change", action="store_true")
    args = parser.parse_args(argv)

    repo_root: Path = args.repo_root.resolve()
    if not repo_root.is_dir():
        _die(f"--repo-root not a directory: {repo_root}", 2)

    stories_dir = repo_root / "docs" / "stories"
    story_path = stories_dir / f"{args.story}.md"
    if not story_path.is_file():
        _die(f"story not found: {story_path}", 2)

    lib = _load_lib()
    try:
        story = lib.parse_story(story_path)
    except lib.ParseError as exc:
        _die(str(exc), exc.exit_code)

    section = args.section
    is_metadata = section in METADATA_FIELDS
    new_content = args.content

    # Gate 4: immutable stories cannot be edited by agents.
    if story.change_resistance == "immutable":
        _die(
            f"refusing edit: story {story.slug!r} has change_resistance=immutable",
            3,
        )

    if is_metadata:
        # Re-render frontmatter with the changed field.
        current_value = getattr(story, section)
        new_value: Any = new_content
        if section == "change_resistance":
            if new_value not in RESISTANCE_RANK:
                _die(
                    f"invalid change_resistance value: {new_value!r}",
                    2,
                )
            current_rank = RESISTANCE_RANK[current_value]
            new_rank = RESISTANCE_RANK[new_value]
            if new_rank > current_rank and not args.confirm_resistance_change:
                _die(
                    "resistance increase requires --confirm-resistance-change",
                    4,
                )

        fm_data = {
            "schema_version": story.schema_version,
            "title": story.title,
            "slug": story.slug,
            "status": story.status,
            "authority": story.authority,
            "change_resistance": story.change_resistance,
            "tests_applicable": story.tests_applicable,
            "locked_sections": list(story.locked_sections),
            "last_audited": story.last_audited,
        }
        fm_data[section] = new_value

        # Re-validate via parse_frontmatter on a synthetic file.
        new_fm = _render_frontmatter(fm_data)
        synthetic = new_fm + "\n# placeholder\n\n## Intent\nx\n"
        try:
            lib.parse_frontmatter(synthetic, path=story_path)
        except lib.ParseError as exc:
            _die(str(exc), exc.exit_code)

        original = story_path.read_text(encoding="utf-8")
        new_text = _replace_frontmatter(original, new_fm)
        story_path.write_text(new_text, encoding="utf-8")

        index_updated = True
        _regenerate_index(stories_dir, lib)

        print(
            json.dumps(
                {
                    "edited": True,
                    "section": section,
                    "index_updated": index_updated,
                }
            )
        )
        return 0

    # Body section edit ----------------------------------------------------
    # Gate 1: locked sections.
    if section in story.locked_sections:
        _die(
            f"refusing edit: section {section!r} is locked",
            3,
        )

    # Gate 2: inline locked blocks within the existing section body.
    existing_body = story.sections.get(section)
    if existing_body is None:
        _die(f"section not found in story: {section!r}", 2)
    if LOCK_MARKER_RE.search(existing_body):
        _die(
            f"refusing edit: section {section!r} contains an inline locked block",
            3,
        )

    # Gate 3: auditable-claims bullet count reduction.
    if section == "Auditable Claims":
        old_count = _count_claims(existing_body)
        new_count = _count_claims(new_content)
        if new_count < old_count and not args.allow_claim_reduction:
            _die(
                "refusing edit: Auditable Claims bullet count would drop "
                f"from {old_count} to {new_count}; pass --allow-claim-reduction to override",
                3,
            )

    original = story_path.read_text(encoding="utf-8")
    new_text = _replace_section(original, section, new_content)

    # Re-parse to ensure we did not break frontmatter or required sections.
    try:
        lib.parse_story(story_path, text=new_text)
    except lib.ParseError as exc:
        _die(f"edit produced invalid story: {exc}", exc.exit_code)

    story_path.write_text(new_text, encoding="utf-8")

    print(
        json.dumps(
            {
                "edited": True,
                "section": section,
                "index_updated": False,
            }
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
