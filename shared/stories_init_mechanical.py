#!/usr/bin/env python3
"""Phase 1 mechanical setup for stories-init.

Idempotently creates docs/stories/ scaffolding in a target repo:

- Creates docs/stories/ if missing.
- Writes a README.md stub if missing; never overwrites.
- Writes an empty INDEX.md if missing; never overwrites.
- Appends docs/stories/drift-todo.md to .gitignore if not already listed.
- Detects root agent-instruction files (AGENTS.md, CLAUDE.md, GEMINI.md).

Emits a JSON result on stdout.
"""

import argparse
import json
import sys
from pathlib import Path

README_STUB = """# docs/stories/

Intent stories for this repository, maintained by the storystore plugin. Each
story under this directory describes a durable user-facing capability of the
software in prose, with code and tests treated as evidence rather than as
automatic authority to rewrite accepted intent. `INDEX.md` is auto-generated;
`drift-todo.md` is gitignored and used for transient drift notes.
"""

GITIGNORE_ENTRY = "docs/stories/drift-todo.md"

AGENT_INSTRUCTION_FILES = ("AGENTS.md", "CLAUDE.md", "GEMINI.md")


def update_gitignore(repo_root: Path) -> bool:
    path = repo_root / ".gitignore"
    if path.exists():
        existing = path.read_text()
        for line in existing.splitlines():
            if line.strip() == GITIGNORE_ENTRY:
                return False
        suffix = "" if existing.endswith("\n") or existing == "" else "\n"
        path.write_text(existing + suffix + GITIGNORE_ENTRY + "\n")
        return True
    path.write_text(GITIGNORE_ENTRY + "\n")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", required=True, type=Path)
    args = parser.parse_args()

    repo_root: Path = args.repo_root.resolve()
    if not repo_root.is_dir():
        print(f"--repo-root not a directory: {repo_root}", file=sys.stderr)
        return 2

    stories_dir = repo_root / "docs" / "stories"
    fresh_init = not stories_dir.exists()

    created: list[str] = []
    preserved: list[str] = []

    if fresh_init:
        stories_dir.mkdir(parents=True)
        created.append("docs/stories/")

    readme = stories_dir / "README.md"
    if readme.exists():
        preserved.append("docs/stories/README.md")
    else:
        readme.write_text(README_STUB)
        created.append("docs/stories/README.md")

    index = stories_dir / "INDEX.md"
    if index.exists():
        preserved.append("docs/stories/INDEX.md")
    else:
        index.write_text("")
        created.append("docs/stories/INDEX.md")

    gitignore_updated = update_gitignore(repo_root)

    agent_files = [
        name for name in AGENT_INSTRUCTION_FILES if (repo_root / name).is_file()
    ]

    result = {
        "fresh_init": fresh_init,
        "created": created,
        "preserved": preserved,
        "gitignore_updated": gitignore_updated,
        "agent_instruction_files": agent_files,
    }
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
