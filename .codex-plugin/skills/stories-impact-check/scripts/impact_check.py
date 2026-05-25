#!/usr/bin/env python3
"""Storystore impact check — match a proposed behavioral change to stories.

Reports stories affected by planned file, surface, or behavior changes so the
calling agent can alert the user before proceeding. Read-only.

See ``shared/spec.md`` and ``2026-05-01-storystore-plan-3-edits-and-impact.md``
for the authoritative behavior contract.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Optional


HERE = Path(__file__).resolve().parent


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:  # pragma: no cover
        raise ImportError(f"could not load {name} from {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


_lib = _load("storystore_lib", HERE / "storystore_lib.py")
_inv = _load("storystore_inventory", HERE / "inventory.py")


# --------------------------------------------------------------------------- #
# Tokenization for description matching
# --------------------------------------------------------------------------- #

_TOKEN_RE = re.compile(r"[A-Za-z0-9]+")

# Short, very common English words that would create noisy matches.
_STOP_WORDS: frozenset[str] = frozenset(
    {
        "a", "an", "the", "and", "or", "but", "if", "then", "of", "to", "in",
        "on", "for", "with", "by", "is", "are", "was", "were", "be", "been",
        "this", "that", "these", "those", "it", "its", "as", "at", "from",
        "into", "about", "we", "you", "they", "i", "do", "does", "did", "not",
        "no", "yes", "so", "than", "when", "while", "after", "before",
        "change", "changing", "changed", "behavior", "behaviour", "story",
        "stories", "user", "users",
    }
)


def _tokenize(text: str) -> set[str]:
    return {
        tok.lower()
        for tok in _TOKEN_RE.findall(text or "")
        if len(tok) >= 3 and tok.lower() not in _STOP_WORDS
    }


# --------------------------------------------------------------------------- #
# Surface ref helpers
# --------------------------------------------------------------------------- #


def _norm_surface_ref(ref: str) -> str:
    """Canonicalize a surface ref for comparison: lowercase prefix + trimmed rest."""
    if ":" not in ref:
        return ref.strip().lower()
    prefix, rest = ref.split(":", 1)
    return f"{prefix.strip().lower()}: {rest.strip()}"


def _surface_to_ref(surface: dict[str, Any]) -> Optional[str]:
    """Render an inventory surface dict to a canonical ``kind: name`` ref."""
    kind = surface.get("kind")
    if kind == "cli-command":
        return f"cli: {surface.get('name', '')}"
    if kind == "http-route":
        return f"route: {surface.get('method', '')} {surface.get('path', '')}"
    if kind == "bin":
        return f"bin: {surface.get('name', '')}"
    if kind == "exports":
        return f"exports: {surface.get('name', '')}"
    if kind == "test":
        return f"test: {surface.get('name', '')}"
    if kind == "heading":
        return f"heading: {surface.get('text', '')}"
    return None


# --------------------------------------------------------------------------- #
# Matching
# --------------------------------------------------------------------------- #


def _intent_excerpt(story: Any, limit: int = 240) -> str:
    text = (story.sections.get("Intent") or "").strip()
    if len(text) > limit:
        text = text[: limit - 1].rstrip() + "…"
    return text


def _story_text_for_description(story: Any) -> str:
    parts: list[str] = [story.title]
    for section in ("Intent", "Story", "Expected Behavior", "Auditable Claims"):
        if section in story.sections:
            parts.append(story.sections[section])
    return "\n".join(parts)


def _match_file(
    story: Any,
    target_files: list[str],
    repo_root: Path,
    surface_index_by_source: dict[str, list[str]],
) -> list[str]:
    """Return ``["file: <path>", ...]`` reasons this story matches any target file."""
    if not target_files:
        return []
    reasons: list[str] = []

    # Resolve story test evidence against repo so glob patterns expand to paths.
    resolved = _inv.resolve_evidence(repo_root, story)
    story_test_paths: set[str] = set(resolved["tests_resolved"])

    # Build the set of source files that *define* the surfaces this story claims.
    story_surface_refs_norm = {
        _norm_surface_ref(r) for r in (story.evidence_surface or [])
    }
    story_surface_sources: set[str] = set()
    for source, refs in surface_index_by_source.items():
        for ref in refs:
            if _norm_surface_ref(ref) in story_surface_refs_norm:
                story_surface_sources.add(source)
                break

    for tgt in target_files:
        tgt_norm = tgt.strip()
        if not tgt_norm:
            continue
        if tgt_norm in story_test_paths:
            reasons.append(f"file: {tgt_norm} (test evidence)")
            continue
        if tgt_norm in story_surface_sources:
            reasons.append(f"file: {tgt_norm} (defines claimed surface)")
            continue
        # Doc evidence: story explicitly references this file.
        if tgt_norm in (story.evidence_docs or []):
            reasons.append(f"file: {tgt_norm} (doc evidence)")
            continue

    return reasons


def _match_surface(story: Any, target_surfaces: list[str]) -> list[str]:
    if not target_surfaces:
        return []
    story_refs_norm = {
        _norm_surface_ref(r) for r in (story.evidence_surface or [])
    }
    reasons: list[str] = []
    for ref in target_surfaces:
        if _norm_surface_ref(ref) in story_refs_norm:
            reasons.append(f"surface: {ref}")
    return reasons


def _match_description(story: Any, description: Optional[str]) -> list[str]:
    if not description:
        return []
    query_tokens = _tokenize(description)
    if not query_tokens:
        return []
    story_tokens = _tokenize(_story_text_for_description(story))
    overlap = query_tokens & story_tokens
    if not overlap:
        return []
    sample = ", ".join(sorted(overlap)[:5])
    return [f"description: matched tokens [{sample}]"]


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #


def _gather_match(
    story: Any,
    files: list[str],
    surfaces: list[str],
    description: Optional[str],
    repo_root: Path,
    surface_index_by_source: dict[str, list[str]],
) -> Optional[dict[str, Any]]:
    reasons: list[str] = []
    reasons.extend(_match_file(story, files, repo_root, surface_index_by_source))
    reasons.extend(_match_surface(story, surfaces))
    reasons.extend(_match_description(story, description))
    if not reasons:
        return None

    flags: list[str] = []
    if story.authority == "observed":
        flags.append("observed-authority (non-gating)")
    if story.status == "deprecated":
        flags.append("status: deprecated (stale)")
    if story.status == "draft":
        flags.append("status: draft")

    return {
        "slug": story.slug,
        "title": story.title,
        "status": story.status,
        "authority": story.authority,
        "change_resistance": story.change_resistance,
        "locked_sections": list(story.locked_sections or []),
        "intent_excerpt": _intent_excerpt(story),
        "match_reasons": reasons,
        "flags": flags,
    }


def _build_surface_index(repo_root: Path) -> dict[str, list[str]]:
    """Return ``{source_file: [canonical_ref, ...]}`` from inventory."""
    try:
        inventory = _inv.build_inventory(repo_root)
    except Exception:
        return {}
    index: dict[str, list[str]] = {}
    for surface in inventory.get("surfaces", []):
        ref = _surface_to_ref(surface)
        source = surface.get("source")
        if not ref or not source:
            continue
        index.setdefault(source, []).append(ref)
    return index


def run(
    repo_root: Path,
    files: list[str],
    surfaces: list[str],
    description: Optional[str],
    perf_warn_ms: int,
) -> tuple[dict[str, Any], list[str]]:
    """Execute the impact check. Returns ``(result, warnings)``."""
    started = time.perf_counter()
    warnings: list[str] = []

    stories_dir = repo_root / "docs" / "stories"
    if not stories_dir.is_dir():
        result = {
            "matches": [],
            "performance": {
                "duration_ms": int((time.perf_counter() - started) * 1000),
                "stories_scanned": 0,
            },
        }
        return result, warnings

    stories = _lib.load_stories(stories_dir)
    surface_index = _build_surface_index(repo_root)

    matches: list[dict[str, Any]] = []
    for story in stories:
        m = _gather_match(story, files, surfaces, description, repo_root, surface_index)
        if m is not None:
            matches.append(m)

    matches.sort(key=lambda m: m["slug"])

    duration_ms = int((time.perf_counter() - started) * 1000)
    if perf_warn_ms > 0 and duration_ms > perf_warn_ms:
        warnings.append(
            f"impact_check: duration_ms={duration_ms} exceeded threshold {perf_warn_ms}"
        )

    return (
        {
            "matches": matches,
            "performance": {
                "duration_ms": duration_ms,
                "stories_scanned": len(stories),
            },
        },
        warnings,
    )


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Match a proposed behavioral change to affected stories.",
        add_help=True,
    )
    parser.add_argument("--repo-root", required=True)
    parser.add_argument("--file", action="append", default=[], dest="files")
    parser.add_argument("--surface", action="append", default=[], dest="surfaces")
    parser.add_argument(
        "--description",
        action="append",
        default=[],
        dest="descriptions",
        help="Free-text description; at most one. Repeating is exit 2.",
    )
    parser.add_argument(
        "--perf-warn-ms",
        type=int,
        default=None,
        help="Stderr warning threshold in ms. 0 disables. Defaults to env "
        "STORYSTORE_PERF_WARN_MS or 500.",
    )
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = _parse_args(list(argv) if argv is not None else sys.argv[1:])

    if len(args.descriptions) > 1:
        sys.stderr.write(
            "impact_check: --description may be given at most once\n"
        )
        return 2

    description = args.descriptions[0] if args.descriptions else None

    repo_root = Path(args.repo_root).resolve()
    if not repo_root.is_dir():
        sys.stderr.write(f"impact_check: repo root not a directory: {repo_root}\n")
        return 2

    if args.perf_warn_ms is not None:
        perf_warn_ms = args.perf_warn_ms
    else:
        env = os.environ.get("STORYSTORE_PERF_WARN_MS")
        if env is not None:
            try:
                perf_warn_ms = int(env)
            except ValueError:
                sys.stderr.write(
                    f"impact_check: STORYSTORE_PERF_WARN_MS must be int, got {env!r}\n"
                )
                return 2
        else:
            perf_warn_ms = 500

    try:
        result, warnings = run(
            repo_root,
            list(args.files),
            list(args.surfaces),
            description,
            perf_warn_ms,
        )
    except _lib.ParseError as exc:
        sys.stderr.write(f"impact_check: {exc}\n")
        return exc.exit_code
    except Exception as exc:  # pragma: no cover - safety net
        sys.stderr.write(f"impact_check: unexpected error: {exc}\n")
        return 4

    for w in warnings:
        sys.stderr.write(w + "\n")

    json.dump(result, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
