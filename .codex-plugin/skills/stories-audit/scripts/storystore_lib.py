"""Storystore shared library: frontmatter parsing and story loading.

Full implementation ships in storystore-lib-parser. This stub satisfies
imports and packaging materialization for downstream skill development.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


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
    last_audited: str | None = None


def parse_frontmatter(text: str) -> dict[str, Any]:
    raise NotImplementedError("parse_frontmatter: implemented in storystore-lib-parser")


def load_stories(stories_dir: Path) -> list[Story]:
    raise NotImplementedError("load_stories: implemented in storystore-lib-parser")
