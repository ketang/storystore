#!/usr/bin/env python3
"""Write a schema-compliant story markdown file and regenerate INDEX.md.

Usage:
    write_story.py --repo-root <repo-root> [--interview | --observed]

Reads story data as JSON on stdin. Writes ``docs/stories/<slug>.md`` to the
target repo and overwrites ``docs/stories/INDEX.md`` with a slug-sorted index.

Exit codes:
    0  success
    2  invalid input (bad slug, overwrite refusal, JSON parse error, missing
       required field, unknown mode)
    3  validity-matrix violation (e.g. authority=observed with
       change_resistance in {high, immutable})
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
INV_PATH = SCRIPT_DIR / "inventory.py"

SLUG_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

INTERVIEW_DEFAULTS = {
    "status": "draft",
    "authority": "accepted",
    "change_resistance": "medium",
    "locked_sections": ["Intent"],
}

OBSERVED_DEFAULTS = {
    "status": "draft",
    "authority": "observed",
    "change_resistance": "low",
    "locked_sections": [],
}


def _load_module(name: str, path: Path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _load_lib():
    return _load_module("storystore_lib", LIB_PATH)


def _load_inventory():
    return _load_module("storystore_inventory", INV_PATH)


def _die(msg: str, code: int = 2) -> "NoReturn":  # type: ignore[name-defined]
    print(msg, file=sys.stderr)
    sys.exit(code)


def _slug_word_count(slug: str) -> int:
    return len([p for p in slug.split("-") if p])


def _validate_slug(slug: str) -> None:
    if not isinstance(slug, str) or not slug:
        _die("slug must be a non-empty string", 2)
    if not SLUG_PATTERN.match(slug):
        _die(
            f"slug {slug!r} must be kebab-case ASCII (lowercase letters, digits, single hyphens)",
            2,
        )
    n = _slug_word_count(slug)
    if n < 2:
        _die(f"slug {slug!r} must contain at least 2 words", 2)
    if n in (2, 3) or n >= 9:
        print(
            f"STORYSTORE_SLUG_NAG: slug {slug!r} has {n} words; target 4-8 words for a durable capability.",
            file=sys.stderr,
        )


def _validate_surface_refs(evidence: dict[str, Any] | None) -> None:
    """Reject any ``Evidence.Surface`` ref the audit validator would reject.

    Enforces the generator invariant: a written story never carries a surface
    ref that ``stories-audit`` cannot parse. Exits 2 on the first bad ref.
    """
    evidence = evidence or {}
    surface = evidence.get("surface") or []
    if not isinstance(surface, list):
        _die("evidence.surface must be a list of strings", 2)
    inv = _load_inventory()
    bad = [
        ref
        for ref in surface
        if not (isinstance(ref, str) and inv.validate_surface_ref(ref))
    ]
    if bad:
        joined = ", ".join(repr(r) for r in bad)
        _die(
            f"refusing to write unparseable surface ref(s): {joined}. "
            f"Use a recognized prefix (cli:, route:, bin:, exports:, skill:, "
            f"test:, heading:, doc:, schema:, flag:, copy:).",
            2,
        )


def _apply_defaults(payload: dict[str, Any], mode: str) -> dict[str, Any]:
    defaults = INTERVIEW_DEFAULTS if mode == "--interview" else OBSERVED_DEFAULTS
    out = dict(payload)
    for key, value in defaults.items():
        out.setdefault(key, value)
    out.setdefault("schema_version", 1)
    return out


def _render_frontmatter(data: dict[str, Any]) -> str:
    lines: list[str] = ["---"]
    # schema_version first
    lines.append(f"schema_version: {int(data['schema_version'])}")
    lines.append(f"title: {data['title']}")
    lines.append(f"slug: {data['slug']}")
    lines.append(f"status: {data['status']}")
    lines.append(f"authority: {data['authority']}")
    lines.append(f"change_resistance: {data['change_resistance']}")
    locked = data.get("locked_sections") or []
    if locked:
        lines.append("locked_sections:")
        for item in locked:
            lines.append(f"  - {item}")
    else:
        lines.append("locked_sections: []")
    lines.append("---")
    return "\n".join(lines) + "\n"


def _render_evidence(evidence: dict[str, Any] | None) -> str:
    evidence = evidence or {}
    out: list[str] = ["## Evidence", ""]
    for heading, key in (("Tests", "tests"), ("Surface", "surface"), ("Docs", "docs")):
        out.append(f"### {heading}")
        items = evidence.get(key) or []
        for item in items:
            out.append(f"- `{item}`")
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def _render_body(payload: dict[str, Any]) -> str:
    parts: list[str] = []
    parts.append(f"# {payload['title']}")
    parts.append("")
    parts.append("## Intent")
    parts.append(payload.get("intent", "").strip() or "TODO")
    parts.append("")
    parts.append("## Story")
    parts.append((payload.get("story") or "").strip() or "TODO")
    parts.append("")
    parts.append("## Expected Behavior")
    parts.append((payload.get("expected_behavior") or "").strip() or "TODO")
    parts.append("")
    parts.append("## Boundaries")
    parts.append((payload.get("boundaries") or "").strip() or "TODO")
    parts.append("")
    parts.append("## Auditable Claims")
    claims = payload.get("auditable_claims") or []
    if claims:
        for claim in claims:
            parts.append(f"- {claim}")
    else:
        parts.append("- TODO")
    parts.append("")
    parts.append(_render_evidence(payload.get("evidence")))
    return "\n".join(parts)


def _regenerate_index(stories_dir: Path, lib) -> None:
    entries: list[tuple[str, str, str, str, str]] = []
    for path in sorted(stories_dir.iterdir()):
        if not path.is_file() or path.suffix != ".md":
            continue
        if path.name in lib.LOADER_SKIP:
            continue
        text = path.read_text(encoding="utf-8")
        try:
            data = lib.parse_frontmatter(text, path=path)
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
    lines = ["# Intent Story Index", ""]
    lines.append(f"{len(entries)} stories — generated {timestamp}")
    lines.append("")
    for slug, title, status, authority, resistance in entries:
        lines.append(
            f"- [{slug}]({slug}.md) — {title} *({status}, {authority}, {resistance})*"
        )
    text = "\n".join(lines)
    if not text.endswith("\n"):
        text += "\n"
    (stories_dir / "INDEX.md").write_text(text, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", required=True, type=Path)
    mode_group = parser.add_mutually_exclusive_group(required=True)
    mode_group.add_argument("--interview", action="store_true")
    mode_group.add_argument("--observed", action="store_true")
    args = parser.parse_args()

    mode = "--interview" if args.interview else "--observed"

    repo_root: Path = args.repo_root.resolve()
    if not repo_root.is_dir():
        _die(f"--repo-root not a directory: {repo_root}", 2)

    stories_dir = repo_root / "docs" / "stories"
    if not stories_dir.is_dir():
        _die(f"docs/stories directory does not exist: {stories_dir}", 2)

    raw = sys.stdin.read()
    if not raw.strip():
        _die("no story data on stdin", 2)
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        _die(f"invalid JSON on stdin: {exc}", 2)
    if not isinstance(payload, dict):
        _die("stdin JSON must be an object", 2)

    for required in ("title", "slug"):
        if not payload.get(required):
            _die(f"missing required field: {required!r}", 2)

    _validate_slug(payload["slug"])

    _validate_surface_refs(payload.get("evidence"))

    data = _apply_defaults(payload, mode)

    story_path = stories_dir / f"{data['slug']}.md"
    if story_path.exists():
        _die(f"refusing to overwrite existing story: {story_path}", 2)

    # Build full file text and validate via storystore_lib parsing.
    frontmatter = _render_frontmatter(data)
    body = _render_body(data)
    text = frontmatter + "\n" + body

    lib = _load_lib()
    try:
        lib.parse_frontmatter(text, path=story_path)
    except lib.ParseError as exc:
        # exit_code carries 3 for validity-matrix violations, 2 otherwise.
        _die(str(exc), exc.exit_code)

    story_path.write_text(text, encoding="utf-8")

    _regenerate_index(stories_dir, lib)

    result = {
        "path": f"docs/stories/{data['slug']}.md",
        "index_updated": True,
    }
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
