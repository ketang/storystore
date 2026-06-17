#!/usr/bin/env python3
"""Lock-state report for a single story.

Reads ``docs/stories/<slug>.md`` and reports which edit operations would
be refused vs. allowed. Pure read; performs no diff comparison.

Stdout JSON:

    {
      "story_slug": "login",
      "locked_sections": ["Intent"],
      "inline_locked_blocks": [],
      "immutable": false,
      "change_resistance": "medium",
      "auditable_claims_count": 3
    }

Exit codes (per shared/spec.md):
    0  success
    2  invalid input or malformed story
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
LIB_PATH = SCRIPT_DIR / "storystore_lib.py"


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
    """Count top-level ``- `` bullets in an Auditable Claims section."""
    count = 0
    for raw in section_text.splitlines():
        stripped = raw.lstrip()
        # only top-level bullets (no leading indentation)
        if raw[: len(raw) - len(stripped)] == "" and stripped.startswith("- "):
            count += 1
    return count


def build_report(story) -> dict[str, Any]:
    claims_text = story.sections.get("Auditable Claims", "")
    return {
        "story_slug": story.slug,
        "title": story.title,
        "status": story.status,
        "authority": story.authority,
        "change_resistance": story.change_resistance,
        "locked_sections": list(story.locked_sections),
        "inline_locked_blocks": [
            {"start_line": b.start_line, "end_line": b.end_line}
            for b in story.locked_blocks
        ],
        "immutable": story.change_resistance == "immutable",
        "auditable_claims_count": _count_claims(claims_text),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", required=True, type=Path)
    parser.add_argument("--slug", required=True)
    parser.add_argument(
        "--section",
        default=None,
        help="Optional: include section_locked flag for this section.",
    )
    args = parser.parse_args(argv)

    repo_root: Path = args.repo_root.resolve()
    if not repo_root.is_dir():
        _die(f"--repo-root not a directory: {repo_root}", 2)

    story_path = repo_root / "docs" / "stories" / f"{args.slug}.md"
    if not story_path.is_file():
        _die(f"story not found: {story_path}", 2)

    lib = _load_lib()
    try:
        story = lib.parse_story(story_path)
    except lib.ParseError as exc:
        _die(str(exc), exc.exit_code)

    report = build_report(story)
    if args.section is not None:
        report["section"] = args.section
        report["section_locked"] = args.section in story.locked_sections

    print(json.dumps(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
