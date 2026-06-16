#!/usr/bin/env python3
"""Mechanical stories-impact trigger — warn when an edit touches story evidence.

This is the *mechanical* counterpart to the description-driven
``stories-impact-check`` skill. Where that skill matches prose, surfaces, and
resolved test evidence, this trigger does one deterministic thing: given the
repo-relative paths a change will touch, it matches each path against every
story's **evidence refs treated as path prefixes / globs** and prints a
non-blocking warning naming the affected stories.

Why a separate, dumber artifact: ``stories-impact-check``'s prose "hard
trigger" relies on an agent remembering to run it. That trigger never fired in
a real repo for three weeks, and a file rename corrupted a story because
nothing mechanical caught it. This script is meant to be wired into something
automatic (a PreToolUse hook, a pre-commit hook, or a CI step) so the warning
fires without anyone remembering.

Design contract:

* **Fails open, always.** A missing ``docs/stories/`` directory, an
  unreadable or malformed story file, a corrupt index, or any internal error
  yields *no block* — the script exits 0 (or, in ``--exit-code`` mode, still
  never raises) and simply reports fewer or no matches. It must never stand
  between an agent and an unrelated edit.
* **Stdlib only, self-contained.** It deliberately does not import the rest of
  the storystore runtime so that a partially-broken install still degrades to
  a quiet no-op rather than a crash.
* **Read-only.** It inspects ``docs/stories/`` and never writes anywhere.

Matching semantics (a story matches a changed path if any of its evidence refs
matches):

* ``exact``  — the changed path equals the ref.
* ``prefix`` — the ref names a directory (or directory prefix): the changed
  path is ``<ref>/...``. A trailing slash on the ref is optional; ``web/src/pages``
  and ``web/src/pages/`` both prefix-match ``web/src/pages/Closet.tsx``.
* ``glob``   — the ref contains ``*``/``?``/``[`` and matches via ``fnmatch``,
  against the full repo-relative path or the basename.

Surface-style refs such as ``cli: login`` simply never match a real file path,
so collecting every evidence ref and applying path matching is safe.

Usage::

    # CLI: pass changed paths as arguments (or on stdin, one per line)
    impact_trigger.py --repo-root . web/src/pages/Closet.tsx
    git diff --name-only | impact_trigger.py --repo-root .

    # Hook: read a Claude Code PreToolUse JSON payload on stdin
    impact_trigger.py --repo-root . --hook < payload.json

Exit codes:

* ``0`` — default. Always, including when matches are found (non-blocking).
* ``1`` — only under ``--exit-code`` and only when matches are found. Internal
  errors still never produce a non-zero exit; failing open wins.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Optional


# --------------------------------------------------------------------------- #
# Story parsing (intentionally lenient — every parse failure is swallowed)
# --------------------------------------------------------------------------- #

_FRONTMATTER_RE = re.compile(r"^---\s*$")
_BACKTICK_RE = re.compile(r"`([^`]+)`")
_LIST_ITEM_RE = re.compile(r"^\s*[-*]\s+(\S.*?)\s*$")
_EVIDENCE_HEADING_RE = re.compile(r"^##\s+Evidence\b", re.IGNORECASE)
_SECTION_HEADING_RE = re.compile(r"^##\s+")  # a new top-level (## ) section


def _parse_frontmatter(lines: list[str]) -> dict[str, str]:
    """Parse a leading ``--- ... ---`` block into a flat ``key: value`` dict."""
    if not lines or not _FRONTMATTER_RE.match(lines[0]):
        return {}
    fields: dict[str, str] = {}
    for line in lines[1:]:
        if _FRONTMATTER_RE.match(line):
            break
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        fields[key.strip().lower()] = value.strip()
    return fields


def _evidence_refs(lines: list[str]) -> list[str]:
    """Collect every ref under the ``## Evidence`` section.

    Picks up backtick-delimited tokens and bare list items. ``### Tests`` /
    ``### Docs`` subsection headings (which start with ``###``) do not end the
    section; only the next ``## `` heading does.
    """
    refs: list[str] = []
    in_evidence = False
    for line in lines:
        if _EVIDENCE_HEADING_RE.match(line):
            in_evidence = True
            continue
        if in_evidence and _SECTION_HEADING_RE.match(line):
            # Reached the next top-level section.
            break
        if not in_evidence:
            continue
        backticked = _BACKTICK_RE.findall(line)
        if backticked:
            refs.extend(backticked)
            continue
        m = _LIST_ITEM_RE.match(line)
        if m:
            refs.append(m.group(1))
    return refs


def parse_story(path: Path) -> Optional[dict[str, Any]]:
    """Parse one story file into a dict, or ``None`` on any error (fail open)."""
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return None
    try:
        lines = text.splitlines()
        fm = _parse_frontmatter(lines)
        refs = _evidence_refs(lines)
        slug = fm.get("slug") or path.stem
        return {
            "slug": slug,
            "title": fm.get("title") or slug,
            "status": fm.get("status") or "",
            "authority": fm.get("authority") or "",
            "change_resistance": fm.get("change_resistance") or "",
            "intent": _intent_excerpt(lines),
            "refs": refs,
        }
    except Exception:
        return None


def _intent_excerpt(lines: list[str], limit: int = 160) -> str:
    """Best-effort first paragraph under ``## Intent``. Never raises."""
    try:
        collecting = False
        buf: list[str] = []
        for line in lines:
            if re.match(r"^##\s+Intent\b", line, re.IGNORECASE):
                collecting = True
                continue
            if collecting:
                if _SECTION_HEADING_RE.match(line):
                    break
                if line.strip():
                    buf.append(line.strip())
                elif buf:
                    break
        text = " ".join(buf)
        if len(text) > limit:
            text = text[: limit - 1].rstrip() + "…"
        return text
    except Exception:
        return ""


def load_stories(stories_dir: Path) -> list[dict[str, Any]]:
    """Load every parseable story under ``stories_dir``. Skips on any error."""
    stories: list[dict[str, Any]] = []
    try:
        candidates = sorted(stories_dir.glob("*.md"))
    except Exception:
        return stories
    skip = {"index.md", "readme.md"}
    for path in candidates:
        if path.name.lower() in skip:
            continue
        story = parse_story(path)
        if story is not None:
            stories.append(story)
    return stories


# --------------------------------------------------------------------------- #
# Path matching
# --------------------------------------------------------------------------- #

_GLOB_CHARS = "*?["


def _normalize_changed(changed: str, repo_root: Path) -> Optional[str]:
    """Return a repo-relative POSIX path for ``changed``, or ``None`` to skip."""
    p = (changed or "").strip().strip('"').strip("'")
    if not p:
        return None
    p = p.replace("\\", "/")
    if os.path.isabs(p):
        try:
            p = os.path.relpath(p, repo_root)
        except Exception:
            return None
        p = p.replace("\\", "/")
    while p.startswith("./"):
        p = p[2:]
    p = p.lstrip("/")
    # A path that escapes the repo root is not story-relevant.
    if p.startswith("../") or p == "..":
        return None
    return p or None


def match_ref(changed: str, ref: str) -> Optional[str]:
    """Return the match kind (``exact``/``prefix``/``glob``) or ``None``."""
    ref = (ref or "").strip().replace("\\", "/")
    if not ref or not changed:
        return None
    if any(c in ref for c in _GLOB_CHARS):
        if fnmatch.fnmatch(changed, ref):
            return "glob"
        if fnmatch.fnmatch(os.path.basename(changed), ref):
            return "glob"
        return None
    if changed == ref:
        return "exact"
    prefix = ref if ref.endswith("/") else ref + "/"
    if changed.startswith(prefix):
        return "prefix"
    return None


def find_affected(
    repo_root: Path, changed_paths: list[str]
) -> list[dict[str, Any]]:
    """Return affected stories with their match reasons. Always fail-open."""
    stories_dir = repo_root / "docs" / "stories"
    if not stories_dir.is_dir():
        return []
    normalized = [
        n for n in (_normalize_changed(c, repo_root) for c in changed_paths) if n
    ]
    if not normalized:
        return []
    stories = load_stories(stories_dir)

    affected: list[dict[str, Any]] = []
    for story in stories:
        reasons: list[str] = []
        for changed in normalized:
            for ref in story["refs"]:
                kind = match_ref(changed, ref)
                if kind is not None:
                    reasons.append(
                        f"{changed} {'==' if kind == 'exact' else '⊂'} {ref} ({kind})"
                    )
                    break  # one reason per changed path is enough
        if reasons:
            affected.append({**story, "match_reasons": reasons})
    affected.sort(key=lambda s: s["slug"])
    return affected


# --------------------------------------------------------------------------- #
# Hook payload extraction
# --------------------------------------------------------------------------- #


def paths_from_hook_payload(payload: dict[str, Any]) -> list[str]:
    """Extract candidate changed paths from a Claude Code PreToolUse payload.

    Handles Write/Edit/MultiEdit (``file_path``), notebooks (``notebook_path``),
    and Bash renames/moves (every whitespace token in the command is treated as
    a candidate path, so ``git mv old new`` surfaces both). Always fail-open:
    any malformed shape yields an empty list.
    """
    try:
        tool_input = payload.get("tool_input") or {}
        if not isinstance(tool_input, dict):
            return []
        paths: list[str] = []
        for key in ("file_path", "notebook_path"):
            val = tool_input.get(key)
            if isinstance(val, str) and val.strip():
                paths.append(val)
        command = tool_input.get("command")
        if isinstance(command, str) and command.strip():
            # Treat each token that looks like a path as a candidate.
            for tok in command.split():
                tok = tok.strip().strip('"').strip("'")
                if "/" in tok or tok.endswith((".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs", ".md")):
                    paths.append(tok)
        return paths
    except Exception:
        return []


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #


def render_text(affected: list[dict[str, Any]]) -> str:
    if not affected:
        return ""
    n = len(affected)
    noun = "story" if n == 1 else "stories"
    out: list[str] = [
        f"⚠ stories-impact: {n} {noun} may be affected by this change:"
    ]
    for s in affected:
        attrs = ", ".join(
            x for x in (s["status"], s["authority"],
                        f"change_resistance={s['change_resistance']}"
                        if s["change_resistance"] else "")
            if x
        )
        out.append(f"  - {s['slug']}" + (f" [{attrs}]" if attrs else ""))
        for reason in s["match_reasons"]:
            out.append(f"      matched: {reason}")
        if s["intent"]:
            out.append(f"      intent: {s['intent']}")
    out.append(
        "Run stories-impact-check and confirm with the user before proceeding."
    )
    return "\n".join(out)


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #


def _read_stdin_paths() -> list[str]:
    try:
        data = sys.stdin.read()
    except Exception:
        return []
    return [line for line in data.splitlines() if line.strip()]


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Mechanical stories-impact trigger: warn when changed paths match "
            "story evidence refs (as path prefixes/globs). Fails open; never "
            "blocks unrelated edits."
        ),
        add_help=True,
    )
    parser.add_argument(
        "--repo-root",
        default=".",
        help="Repo root containing docs/stories/ (default: current directory).",
    )
    parser.add_argument(
        "paths",
        nargs="*",
        help="Repo-relative or absolute changed paths. If none and not --hook, "
        "paths are read from stdin, one per line.",
    )
    parser.add_argument(
        "--hook",
        action="store_true",
        help="Read a Claude Code PreToolUse JSON payload on stdin and extract "
        "the changed path(s) from it.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit a JSON object instead of a human-readable warning.",
    )
    parser.add_argument(
        "--exit-code",
        action="store_true",
        help="Exit 1 when any story is affected (for CI/composition). Internal "
        "errors still exit 0 — failing open always wins.",
    )
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    try:
        args = _parse_args(list(argv) if argv is not None else sys.argv[1:])
    except SystemExit:
        # argparse errors (bad flags) should not block an edit.
        return 0

    try:
        repo_root = Path(args.repo_root).resolve()

        if args.hook:
            raw = sys.stdin.read()
            try:
                payload = json.loads(raw) if raw.strip() else {}
            except Exception:
                payload = {}
            changed_paths = paths_from_hook_payload(payload)
        elif args.paths:
            changed_paths = list(args.paths)
        else:
            changed_paths = _read_stdin_paths()

        affected = find_affected(repo_root, changed_paths)
    except Exception as exc:  # pragma: no cover - last-resort fail-open net
        sys.stderr.write(f"impact_trigger: ignoring internal error: {exc}\n")
        return 0

    if args.json:
        sys.stdout.write(
            json.dumps({"affected": affected}, indent=2, sort_keys=True) + "\n"
        )
    else:
        report = render_text(affected)
        if report:
            # Warnings go to stderr so the trigger composes cleanly and a hook
            # surfaces the message without polluting tool stdout.
            sys.stderr.write(report + "\n")

    if args.exit_code and affected:
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
