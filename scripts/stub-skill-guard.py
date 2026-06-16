#!/usr/bin/env python3
"""Loud-failure guard for intentionally-shipped stub skills.

A *stub skill* is a skill whose SKILL.md ships in a release before its
implementation exists — for example a planned capability advertised in a
pre-1.0 plugin so downstream agents can discover it. The hazard such a
skill creates is silent: an agent invokes it, the SKILL.md prints status
prose ("this is deferred"), the agent treats the clean exit as success,
and weeks of evidence drift accumulate before anyone notices the
capability never ran.

This script is the loud failure that prevents that. A stub SKILL.md's
FIRST instruction must run it. It emits an actionable error naming the
skill and the shipped plugin version, then exits non-zero so the invoking
agent gets an unambiguous signal that the capability does not exist in
this release and must not proceed as if it ran.

Usage:
    stub-skill-guard.py --skill <name> [--version <v>] [--repo-root <path>]

Exit code: always non-zero (EXIT_STUB) on success of its own job — the
whole point is that invoking a stub is never a clean exit. The only
clean exit is --help.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# A stub invocation is never a success. We use a distinct, non-1 code so
# callers and tests can tell "this skill is a stub" apart from a generic
# crash. 78 is EX_CONFIG from sysexits.h — the capability is unconfigured.
EXIT_STUB = 78

VERSION_UNKNOWN = "unknown"


def resolve_version(explicit: str | None, repo_root: Path | None) -> str:
    """Resolve the shipped plugin version.

    Order: an explicit --version wins; otherwise search upward for
    plugin-version.json starting from --repo-root (when given) and from
    this script's own location. Returns ``VERSION_UNKNOWN`` if no version
    file is found — a missing version must not turn a loud failure into a
    silent one.
    """
    if explicit:
        return explicit
    starts: list[Path] = []
    if repo_root is not None:
        starts.append(repo_root.resolve())
    starts.append(Path(__file__).resolve().parent)
    for start in starts:
        for candidate in [start, *start.parents]:
            version_file = candidate / "plugin-version.json"
            if version_file.is_file():
                try:
                    data = json.loads(version_file.read_text())
                except (json.JSONDecodeError, OSError):
                    continue
                version = data.get("version")
                if isinstance(version, str) and version:
                    return version
    return VERSION_UNKNOWN


def build_message(skill: str, version: str) -> str:
    """Compose the actionable loud-failure message sent to stderr."""
    return (
        f"STUB SKILL — NOT IMPLEMENTED: '{skill}' ships as a stub in "
        f"storystore plugin version {version}.\n"
        f"The implementation for '{skill}' is NOT present in this release. "
        f"Nothing ran and no result was produced.\n"
        f"Do NOT proceed as if this skill succeeded and do NOT fabricate "
        f"its output. Surface this to the user verbatim: the capability "
        f"named by '{skill}' does not exist in storystore {version}; it is "
        f"planned but not yet shipped.\n"
        f"If you need this capability now, ask the user to track or "
        f"prioritize its implementation rather than working around it."
    )


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="stub-skill-guard",
        description=(
            "Fail loudly when an intentionally-shipped stub skill is "
            "invoked, naming the shipped plugin version."
        ),
    )
    parser.add_argument(
        "--skill",
        required=True,
        help="name of the stub skill being invoked",
    )
    parser.add_argument(
        "--version",
        default=None,
        help="override the plugin version (default: read plugin-version.json)",
    )
    parser.add_argument(
        "--repo-root",
        default=None,
        type=Path,
        help="repo root to search for plugin-version.json",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    version = resolve_version(args.version, args.repo_root)
    print(build_message(args.skill, version), file=sys.stderr)
    return EXIT_STUB


if __name__ == "__main__":
    raise SystemExit(main())
