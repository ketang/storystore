#!/usr/bin/env python3
"""Read-only fidelity audit for storystore.

Walks ``docs/stories/<slug>.md`` files and reports findings about declared
evidence that no longer resolves, claims unsupported by evidence, and intent
that contradicts deterministic evidence.

See ``shared/spec.md`` and ``2026-05-01-storystore-plan-2-fidelity.md`` for the
authoritative contract. Exit codes follow that spec:

    0  success (default)
    1  findings present (--strict only)
    2  invalid input or malformed story
    3  validity-matrix violation (raised by storystore_lib)
    4+ unexpected runtime error
"""

from __future__ import annotations

import argparse
import datetime as _dt
import importlib.util
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Iterable, Optional


SCRIPT_DIR = Path(__file__).resolve().parent
LIB_PATH = SCRIPT_DIR / "storystore_lib.py"
INV_PATH = SCRIPT_DIR / "inventory.py"

DEFAULT_PERF_WARN_MS = 5000

POINTER_MARKER = "docs/stories"
SUPPRESSION_MARKER = "<!-- storystore: no-pointer -->"
AGENT_FILES = ("AGENTS.md", "CLAUDE.md", "GEMINI.md")


def _load(name: str, path: Path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _die(msg: str, code: int = 2) -> "NoReturn":  # type: ignore[name-defined]
    print(msg, file=sys.stderr)
    sys.exit(code)


# --------------------------------------------------------------------------- #
# Severity
# --------------------------------------------------------------------------- #


def _severity_for_story(story: Any) -> str:
    mapping = {"low": "low", "medium": "medium", "high": "high", "immutable": "high"}
    return mapping.get(getattr(story, "change_resistance", "low"), "low")


# --------------------------------------------------------------------------- #
# Surface ref normalization and matching
# --------------------------------------------------------------------------- #


_SURFACE_REF_RE = re.compile(r"^(?P<prefix>[a-zA-Z][a-zA-Z0-9_-]*)\s*:\s*(?P<rest>.+?)\s*$")
_ROUTE_REST_RE = re.compile(r"^(?P<method>[A-Z]+)\s+(?P<path>/\S*)\s*$")


def _normalize_ref(ref: str) -> Optional[tuple[str, ...]]:
    """Map a surface ref to a key tuple for inventory matching.

    Returns None for refs whose prefix has no inventory equivalent (eg
    ``doc:``, ``test:``) or refs whose syntax is malformed.
    """
    match = _SURFACE_REF_RE.match(ref)
    if not match:
        return None
    prefix = match.group("prefix").lower()
    rest = match.group("rest").strip()
    if not rest:
        return None
    if prefix == "cli":
        return ("cli-command", rest)
    if prefix == "route":
        rmatch = _ROUTE_REST_RE.match(rest)
        if not rmatch:
            return None
        return ("http-route", rmatch.group("method").upper(), rmatch.group("path"))
    if prefix == "bin":
        return ("bin", rest)
    if prefix in ("exports", "export"):
        return ("exports", rest)
    if prefix == "skill":
        return ("skill", rest)
    if prefix == "test":
        return ("test", rest)
    if prefix in ("heading", "doc"):
        return ("heading", rest)
    if prefix == "schema":
        return ("schema", rest)
    if prefix == "flag":
        return ("flag", rest)
    if prefix == "copy":
        return ("copy", rest)
    return None


def _inventory_keys(inventory: dict[str, Any]) -> set[tuple[str, ...]]:
    keys: set[tuple[str, ...]] = set()
    for surface in inventory.get("surfaces", []):
        kind = surface.get("kind")
        if kind == "cli-command":
            keys.add(("cli-command", surface.get("name", "")))
        elif kind == "http-route":
            keys.add(("http-route", surface.get("method", ""), surface.get("path", "")))
        elif kind == "bin":
            keys.add(("bin", surface.get("name", "")))
        elif kind == "exports":
            keys.add(("exports", surface.get("name", "")))
        elif kind == "skill":
            keys.add(("skill", surface.get("name", "")))
        elif kind == "test":
            keys.add(("test", surface.get("name", "")))
        elif kind == "heading":
            keys.add(("heading", surface.get("text", "")))
        elif kind == "schema":
            keys.add(("schema", surface.get("name", "")))
    return keys


# --------------------------------------------------------------------------- #
# Finding emission
# --------------------------------------------------------------------------- #


def _make_finding(
    kind: str,
    *,
    story_slug: Optional[str],
    severity: str,
    suggested_action: str,
    body: str,
    title: str,
) -> dict[str, Any]:
    return {
        "kind": kind,
        "story_slug": story_slug,
        "severity": severity,
        "suggested_action": suggested_action,
        "body": body,
        "title": title,
    }


# --------------------------------------------------------------------------- #
# Pointer check
# --------------------------------------------------------------------------- #


def _check_agent_pointer(repo_root: Path) -> Optional[dict[str, Any]]:
    present: list[tuple[Path, str]] = []
    for name in AGENT_FILES:
        path = repo_root / name
        if path.is_file():
            try:
                present.append((path, path.read_text(encoding="utf-8")))
            except (OSError, UnicodeDecodeError):
                present.append((path, ""))
    if not present:
        return None
    # Suppression in any file silences the finding.
    if any(SUPPRESSION_MARKER in text for _, text in present):
        return None
    # Pointer present in any file -> ok.
    if any(POINTER_MARKER in text for _, text in present):
        return None
    files_list = ", ".join(p.name for p, _ in present)
    body = (
        f"Agent-instruction file(s) present ({files_list}) do not reference "
        f"the storystore convention (looked for {POINTER_MARKER!r}). Add a "
        f"pointer to docs/stories/ so future agents discover the intent stories. "
        f"To silence this finding, add the marker {SUPPRESSION_MARKER!r} to any "
        f"present agent-instruction file."
    )
    return _make_finding(
        "agent-pointer-missing",
        story_slug=None,
        severity="low",
        suggested_action="update-story",
        body=body,
        title="agent instruction file is missing storystore pointer",
    )


# --------------------------------------------------------------------------- #
# Auditing a single story
# --------------------------------------------------------------------------- #


def _audit_story(
    story: Any,
    resolved: dict[str, Any],
    inventory_keys: Optional[set[tuple[str, ...]]],
    inferred_keys: Optional[set[tuple[str, ...]]],
    inferred_active: bool,
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    severity = _severity_for_story(story)
    slug = getattr(story, "slug", "")
    tests_applicable = getattr(story, "tests_applicable", True)

    # surface-missing: ref doesn't resolve against inventory or is malformed.
    if inventory_keys is not None:
        for entry in resolved.get("surface_refs", []):
            ref = entry["ref"]
            valid = entry["valid"]
            inferred_used = False
            if not valid:
                body = f"Surface reference `{ref}` could not be parsed."
            else:
                key = _normalize_ref(ref)
                if key is None:
                    body = f"Surface reference `{ref}` has no inventory mapping."
                elif key[0] in ("test", "heading", "schema", "flag", "copy"):
                    # not validated against inventory; skip
                    continue
                elif key in inventory_keys:
                    continue
                elif inferred_keys is not None and key in inferred_keys:
                    inferred_used = True
                    continue
                else:
                    body = (
                        f"Surface reference `{ref}` does not match any "
                        f"extracted user-facing surface in the repository."
                    )
                    if inferred_active:
                        body += " [inferred] coverage did not include it either."
            findings.append(
                _make_finding(
                    "surface-missing",
                    story_slug=slug,
                    severity=severity,
                    suggested_action="fix-code",
                    body=body,
                    title=f"surface `{ref}` does not resolve",
                )
            )

    # test-evidence-missing: declared test ref didn't resolve.
    if tests_applicable:
        for ref in resolved.get("tests_missing", []):
            findings.append(
                _make_finding(
                    "test-evidence-missing",
                    story_slug=slug,
                    severity=severity,
                    suggested_action="add-evidence",
                    body=(
                        f"Test evidence ref `{ref}` did not resolve to any files. "
                        f"Either add the test, fix the path/glob, or update the story."
                    ),
                    title=f"test evidence `{ref}` did not resolve",
                )
            )

    # schema-evidence-missing: declared schema ref didn't resolve.
    for ref in resolved.get("schema_missing", []):
        findings.append(
            _make_finding(
                "schema-evidence-missing",
                story_slug=slug,
                severity=severity,
                suggested_action="add-evidence",
                body=(
                    f"Schema evidence ref `{ref}` did not resolve to any migration files. "
                    f"Either add a migration that defines the column, fix the reference, "
                    f"or update the story."
                ),
                title=f"schema evidence `{ref}` did not resolve",
            )
        )

    # flag-evidence-missing: declared flag ref didn't resolve.
    for ref in resolved.get("flag_missing", []):
        findings.append(
            _make_finding(
                "flag-evidence-missing",
                story_slug=slug,
                severity=severity,
                suggested_action="add-evidence",
                body=(
                    f"Flag evidence ref `{ref}` did not resolve to any flag definitions "
                    f"in the repository. Either add a flag definition that matches, fix "
                    f"the reference, or update the story."
                ),
                title=f"flag evidence `{ref}` did not resolve",
            )
        )

    # copy-evidence-missing: declared copy ref didn't resolve.
    for ref in resolved.get("copy_missing", []):
        findings.append(
            _make_finding(
                "copy-evidence-missing",
                story_slug=slug,
                severity=severity,
                suggested_action="add-evidence",
                body=(
                    f"Copy evidence ref `{ref}` did not resolve to a key in the locale file. "
                    f"Either add the key to the locale file, fix the reference, "
                    f"or update the story."
                ),
                title=f"copy evidence `{ref}` did not resolve",
            )
        )

    # claim-unsupported: claims with no resolved evidence support.
    claims_section = getattr(story, "sections", {}).get("Auditable Claims", "")
    claims = _extract_bullets(claims_section)
    has_resolved_tests = bool(resolved.get("tests_resolved"))
    has_resolved_docs = bool(resolved.get("docs_resolved"))
    has_valid_surface = any(e["valid"] for e in resolved.get("surface_refs", []))
    has_resolved_schema = bool(resolved.get("schema_resolved"))
    has_resolved_flag = bool(resolved.get("flag_resolved"))
    has_resolved_copy = bool(resolved.get("copy_resolved"))
    has_any_evidence = has_resolved_tests or has_resolved_docs or has_valid_surface or has_resolved_schema or has_resolved_flag or has_resolved_copy
    if claims and not has_any_evidence:
        bullets = "\n".join(f"- {c}" for c in claims)
        findings.append(
            _make_finding(
                "claim-unsupported",
                story_slug=slug,
                severity=severity,
                suggested_action="add-evidence",
                body=(
                    f"Story declares auditable claims but no evidence resolves "
                    f"to support them.\n\n{bullets}"
                ),
                title="auditable claims have no resolved evidence",
            )
        )

    # intent-conflict is deterministic-but-rare; without a concrete signal we
    # do not emit it from the deterministic pass. Severity is hardcoded to high
    # if/when emitted by future hooks.

    return findings


_BULLET_RE = re.compile(r"^\s*[-*]\s+(.*\S)\s*$")


def _extract_bullets(section_text: str) -> list[str]:
    out: list[str] = []
    for line in section_text.splitlines():
        m = _BULLET_RE.match(line)
        if m:
            text = m.group(1).strip()
            # Treat TODO-only bullets as no real claim.
            if text.upper() in ("TODO", "FIXME", "XXX", "TBD"):
                continue
            out.append(text)
    return out


# --------------------------------------------------------------------------- #
# Report rendering
# --------------------------------------------------------------------------- #


def _render_language_block(languages: dict[str, list[str]]) -> str:
    detected = languages.get("detected") or []
    extracted = languages.get("extracted") or []
    lines = ["## Language Coverage", ""]
    lines.append(f"Detected: {', '.join(detected) if detected else '(none)'}")
    lines.append(f"Extracted: {', '.join(extracted) if extracted else '(none)'}")
    uncovered = sorted(set(detected) - set(extracted))
    if uncovered:
        lines.append(
            f"Note: {', '.join(uncovered)} detected but not covered by built-in "
            f"extractors. Re-run with --thorough to author inferred surface coverage."
        )
    lines.append("")
    return "\n".join(lines)


def _render_finding(idx: int, finding: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append(f"## Finding {idx}: {finding['title']}")
    lines.append("")
    lines.append(f"- kind: {finding['kind']}")
    lines.append(f"- story_slug: {finding['story_slug'] if finding['story_slug'] is not None else 'null'}")
    lines.append(f"- severity: {finding['severity']}")
    lines.append(f"- suggested_action: {finding['suggested_action']}")
    lines.append("")
    lines.append(finding["body"])
    lines.append("")
    return "\n".join(lines)


def _render_report(
    languages: dict[str, list[str]],
    findings: list[dict[str, Any]],
    stories_scanned: int,
    scoped_slugs: Optional[list[str]],
) -> str:
    parts: list[str] = []
    parts.append("# stories-audit report")
    parts.append("")
    timestamp = _dt.datetime.now(tz=_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    parts.append(f"Generated: {timestamp}")
    parts.append(f"Stories scanned: {stories_scanned}")
    if scoped_slugs:
        parts.append(f"Scope: {', '.join(scoped_slugs)}")
    parts.append(f"Findings: {len(findings)}")
    parts.append("")
    parts.append(_render_language_block(languages))
    if not findings:
        parts.append("No findings.\n")
    else:
        for i, finding in enumerate(findings, 1):
            parts.append(_render_finding(i, finding))
    text = "\n".join(parts)
    if not text.endswith("\n"):
        text += "\n"
    return text


# --------------------------------------------------------------------------- #
# Inferred surface loading
# --------------------------------------------------------------------------- #


def _load_inferred_surface(path: Path) -> set[tuple[str, ...]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _die(f"failed to load --inferred-surface {path}: {exc}", 2)
    keys: set[tuple[str, ...]] = set()
    if isinstance(data, dict):
        entries = data.get("surfaces", [])
    elif isinstance(data, list):
        entries = data
    else:
        entries = []
    if not isinstance(entries, list):
        _die("inferred surface JSON must be a list (or {'surfaces': [...]})", 2)
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        kind = entry.get("kind")
        if kind == "cli-command":
            keys.add(("cli-command", entry.get("name", "")))
        elif kind == "http-route":
            keys.add(("http-route", entry.get("method", ""), entry.get("path", "")))
        elif kind == "bin":
            keys.add(("bin", entry.get("name", "")))
        elif kind == "exports":
            keys.add(("exports", entry.get("name", "")))
    return keys


# --------------------------------------------------------------------------- #
# bump-clean: rewrite frontmatter with last_audited
# --------------------------------------------------------------------------- #


def _bump_last_audited(story_path: Path, today: str) -> None:
    text = story_path.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].rstrip("\r\n") != "---":
        return
    end_idx = None
    for i in range(1, len(lines)):
        if lines[i].rstrip("\r\n") == "---":
            end_idx = i
            break
    if end_idx is None:
        return
    new_line = f"last_audited: {today}\n"
    replaced = False
    out_lines: list[str] = [lines[0]]
    for i in range(1, end_idx):
        if lines[i].lstrip().startswith("last_audited:"):
            out_lines.append(new_line)
            replaced = True
        else:
            out_lines.append(lines[i])
    if not replaced:
        out_lines.append(new_line)
    out_lines.extend(lines[end_idx:])
    story_path.write_text("".join(out_lines), encoding="utf-8")


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", required=True, type=Path)
    parser.add_argument("--report-path", type=Path, default=None)
    parser.add_argument("--story", action="append", default=[],
                        help="Scope audit to one or more story slugs (repeatable).")
    parser.add_argument("--bump-clean", action="store_true",
                        help="Write last_audited for stories with zero findings.")
    parser.add_argument("--thorough", action="store_true",
                        help="Opt-in non-TS coverage; requires --inferred-surface.")
    parser.add_argument("--inferred-surface", type=Path, default=None,
                        help="JSON file with inferred surface entries; used with --thorough.")
    parser.add_argument("--strict", action="store_true",
                        help="Exit 1 if any findings exist.")
    parser.add_argument("--source-root", default=None,
                        help="Relative subtree to walk for inventory.")
    parser.add_argument("--include-dir", action="append", default=[],
                        help="Directory name to pull back into the walk (repeatable).")
    parser.add_argument("--perf-warn-ms", type=int, default=None,
                        help="Override perf-warn threshold (env STORYSTORE_PERF_WARN_MS).")
    args = parser.parse_args(argv)

    lib = _load("storystore_lib", LIB_PATH)
    inv = _load("storystore_inventory", INV_PATH)

    repo_root: Path = args.repo_root.resolve()
    if not repo_root.is_dir():
        _die(f"--repo-root not a directory: {repo_root}", 2)

    stories_dir = repo_root / "docs" / "stories"
    if not stories_dir.is_dir():
        _die(f"docs/stories directory does not exist: {stories_dir}", 2)

    timer = lib.PerfTimer()

    # Load stories.
    timer.start("load_stories")
    try:
        all_stories = lib.load_stories(stories_dir)
    except lib.ParseError as exc:
        _die(str(exc), exc.exit_code)
    timer.stop("load_stories")

    scoped_slugs: Optional[list[str]] = None
    if args.story:
        scoped = set(args.story)
        stories = [s for s in all_stories if s.slug in scoped]
        missing = sorted(scoped - {s.slug for s in stories})
        if missing:
            _die(f"unknown story slug(s): {', '.join(missing)}", 2)
        scoped_slugs = sorted(s.slug for s in stories)
    else:
        stories = list(all_stories)

    # Build inventory only for non-scoped runs.
    inventory: dict[str, Any]
    inventory_keys: Optional[set[tuple[str, ...]]]
    if scoped_slugs is None:
        timer.start("build_inventory")
        inventory = inv.build_inventory(
            repo_root,
            source_root=args.source_root,
            include_dirs=args.include_dir or None,
        )
        timer.stop("build_inventory")
        inventory_keys = _inventory_keys(inventory)
    else:
        # Detect languages so the report header is still correct.
        inventory = {
            "languages": inv.detect_languages(repo_root),
            "surfaces": [],
        }
        inventory_keys = None

    # Inferred surface keys (thorough mode).
    inferred_keys: Optional[set[tuple[str, ...]]] = None
    if args.inferred_surface is not None:
        inferred_keys = _load_inferred_surface(args.inferred_surface)
    inferred_active = bool(args.thorough)

    # Resolve evidence and emit per-story findings.
    timer.start("resolve_evidence")
    findings: list[dict[str, Any]] = []
    per_story_findings: dict[str, list[dict[str, Any]]] = {}
    evidence_refs_resolved = 0
    for story in stories:
        resolved = inv.resolve_evidence(repo_root, story)
        evidence_refs_resolved += (
            len(resolved.get("tests_resolved", []))
            + len(resolved.get("docs_resolved", []))
            + sum(1 for e in resolved.get("surface_refs", []) if e["valid"])
        )
        story_findings = _audit_story(
            story,
            resolved,
            inventory_keys,
            inferred_keys,
            inferred_active,
        )
        per_story_findings[story.slug] = story_findings
        findings.extend(story_findings)
    timer.stop("resolve_evidence")

    # Repo-level: agent-pointer-missing. Skip in scoped mode.
    if scoped_slugs is None:
        pointer_finding = _check_agent_pointer(repo_root)
        if pointer_finding is not None:
            findings.append(pointer_finding)

    # --bump-clean: write last_audited for stories with zero findings.
    if args.bump_clean:
        today = _dt.date.today().isoformat()
        for story in stories:
            if not per_story_findings.get(story.slug):
                _bump_last_audited(story.path, today)

    # Resolve report path.
    report_path = args.report_path
    if report_path is None:
        ts = _dt.datetime.now(tz=_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        report_path = Path(f"/tmp/stories-audit-{ts}.md")
    report_path = Path(report_path)
    report_path.parent.mkdir(parents=True, exist_ok=True)

    report_text = _render_report(
        inventory.get("languages", {"detected": [], "extracted": []}),
        findings,
        stories_scanned=len(stories),
        scoped_slugs=scoped_slugs,
    )
    report_path.write_text(report_text, encoding="utf-8")

    # Perf warn.
    if args.perf_warn_ms is not None:
        threshold = args.perf_warn_ms
    else:
        env_value = os.environ.get("STORYSTORE_PERF_WARN_MS")
        threshold = int(env_value) if env_value and env_value.strip() else DEFAULT_PERF_WARN_MS

    duration_ms = timer.total_ms()
    if threshold > 0 and duration_ms > threshold:
        print(
            f"STORYSTORE_PERF_WARN: audit took {duration_ms}ms (threshold {threshold}ms)",
            file=sys.stderr,
        )

    result = {
        "report_path": str(report_path),
        "findings_count": len(findings),
        "performance": {
            "duration_ms": duration_ms,
            "stories_scanned": len(stories),
            "evidence_refs_resolved": evidence_refs_resolved,
            "phase_breakdown": timer.phases_ms(),
        },
    }
    json.dump(result, sys.stdout)
    sys.stdout.write("\n")

    if args.strict and findings:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
