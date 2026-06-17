#!/usr/bin/env python3
"""Storystore coverage: software-to-story coverage and completeness report.

Stdlib-only. Used by ``stories-coverage``. See ``shared/spec.md`` for the
authoritative behavior contract.

Question answered:

    What user-facing software behavior lacks story coverage?

Default command::

    coverage.py --repo-root <repo-root> --report-path <path>

If ``--report-path`` is omitted, a timestamped path under ``/tmp/`` is used.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional


# --------------------------------------------------------------------------- #
# Shared-module loading
# --------------------------------------------------------------------------- #


def _load_sibling(name: str, filename: str):
    """Load a sibling module (``storystore_lib`` / ``inventory``) by file path.

    Materialized copies live alongside this script when packaged into a skill;
    the canonical copies live under the repo's ``shared/`` directory during
    development.
    """
    here = Path(__file__).resolve().parent
    candidate = here / filename
    if not candidate.is_file():
        raise RuntimeError(f"required sibling module not found: {candidate}")
    spec = importlib.util.spec_from_file_location(name, candidate)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"failed to load module: {candidate}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


lib = _load_sibling("storystore_lib", "storystore_lib.py")
inv = _load_sibling("storystore_inventory", "inventory.py")


# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #


DEFAULT_PERF_WARN_MS = 5000
PLACEHOLDER_INTENT = "Inferred from code; not human-confirmed."

DEFAULT_SURFACE_KINDS: frozenset[str] = frozenset({"cli-command", "http-route", "bin", "schema", "copy"})

# Severity derived from change_resistance.
SEVERITY_BY_RESISTANCE: dict[str, str] = {
    "low": "low",
    "medium": "medium",
    "high": "high",
    "immutable": "high",
}

RATINGS: tuple[tuple[int, int, str], ...] = (
    (0, 9, "skeletal"),
    (10, 24, "sparse"),
    (25, 34, "partial"),
    (35, 44, "substantial"),
    (45, 50, "complete"),
)

RATING_RANK: dict[str, int] = {name: idx for idx, (_, _, name) in enumerate(RATINGS)}

PLACEHOLDER_MARKERS = ("TODO", "FIXME", "XXX", "TBD")


# --------------------------------------------------------------------------- #
# Surface ref normalization
# --------------------------------------------------------------------------- #


_SURFACE_REF_RE = re.compile(r"^([a-zA-Z][a-zA-Z0-9_-]*)\s*:\s*(.+?)\s*$")


def surface_key(kind: str, *, name: str = "", method: str = "", path: str = "") -> Optional[str]:
    """Return a canonical key for matching surface refs.

    Returns ``None`` when the surface has no canonical representation
    (e.g. heading without a recognized prefix).
    """
    if kind == "cli-command":
        return f"cli:{name.strip()}"
    if kind == "http-route":
        return f"route:{method.strip().upper()} {path.strip()}"
    if kind == "bin":
        return f"bin:{name.strip()}"
    if kind == "exports":
        return f"exports:{name.strip()}"
    if kind == "skill":
        return f"skill:{name.strip()}"
    if kind == "schema":
        return f"schema:{name.strip()}"
    if kind == "copy":
        return f"copy:{name.strip()}"
    return None


def ref_to_key(ref: str) -> Optional[str]:
    """Normalize a story-evidence surface ref into a key for matching."""
    m = _SURFACE_REF_RE.match(ref.strip())
    if not m:
        return None
    prefix = m.group(1).lower()
    rest = m.group(2).strip()
    if not rest:
        return None
    if prefix == "cli":
        return f"cli:{rest}"
    if prefix == "route":
        # Expect "<METHOD> <path>"
        parts = rest.split(None, 1)
        if len(parts) != 2:
            return None
        method, path = parts[0].upper(), parts[1].strip()
        return f"route:{method} {path}"
    if prefix == "bin":
        return f"bin:{rest}"
    if prefix in ("exports", "export"):
        return f"exports:{rest}"
    if prefix == "skill":
        return f"skill:{rest}"
    if prefix == "schema":
        return f"schema:{rest}"
    if prefix == "copy":
        return f"copy:{rest}"
    return None


# --------------------------------------------------------------------------- #
# Completeness scoring
# --------------------------------------------------------------------------- #


@dataclass
class CompletenessBreakdown:
    story_prose: int = 0
    expected_behavior: int = 0
    boundaries: int = 0
    auditable_claims: int = 0
    evidence: int = 0

    def total(self) -> int:
        return (
            self.story_prose
            + self.expected_behavior
            + self.boundaries
            + self.auditable_claims
            + self.evidence
        )

    def as_dict(self) -> dict[str, int]:
        return {
            "story_prose": self.story_prose,
            "expected_behavior": self.expected_behavior,
            "boundaries": self.boundaries,
            "auditable_claims": self.auditable_claims,
            "evidence": self.evidence,
            "total": self.total(),
        }


def _has_placeholder_marker(text: str) -> bool:
    upper = text.upper()
    for marker in PLACEHOLDER_MARKERS:
        # Match marker as word; cheap check.
        if re.search(rf"\b{marker}\b", upper):
            return True
    return False


def _word_count(text: str) -> int:
    return len([w for w in re.split(r"\s+", text.strip()) if w])


def _bullet_count(text: str) -> int:
    n = 0
    for line in text.splitlines():
        s = line.lstrip()
        if s.startswith(("- ", "* ")):
            n += 1
    return n


def _score_words(text: str, low: int, mid: int) -> int:
    if not text.strip() or _has_placeholder_marker(text):
        return 0
    wc = _word_count(text)
    if wc < low:
        return 1
    if wc < mid:
        return 4
    return 10


def _score_bullets(text: str) -> int:
    if not text.strip() or _has_placeholder_marker(text):
        return 0
    n = _bullet_count(text)
    if n <= 0:
        return 0
    if n == 1:
        return 1
    if n == 2:
        return 4
    return 10


def _score_evidence(story: Any) -> int:
    """Score the Evidence dimension.

    Counts the total declared refs across Tests/Surface/Docs and how many
    of those subsections are non-empty.

    1 ref → minimal (1)
    2 refs → weak (4)
    3+ refs OR refs in 2+ subsections → sufficient (10)
    """
    tests = list(getattr(story, "evidence_tests", []) or [])
    surface = list(getattr(story, "evidence_surface", []) or [])
    docs = list(getattr(story, "evidence_docs", []) or [])
    schema = list(getattr(story, "evidence_schema", []) or [])
    flag = list(getattr(story, "evidence_flag", []) or [])
    copy = list(getattr(story, "evidence_copy", []) or [])
    section_text = story.sections.get("Evidence", "") if hasattr(story, "sections") else ""
    if section_text and _has_placeholder_marker(section_text):
        return 0
    total = len(tests) + len(surface) + len(docs) + len(schema) + len(flag) + len(copy)
    subsections = sum(1 for group in (tests, surface, docs, schema, flag, copy) if group)
    if total == 0:
        return 0
    if total >= 3 or subsections >= 2:
        return 10
    if total == 2:
        return 4
    return 1


def score_story(story: Any) -> CompletenessBreakdown:
    sections = getattr(story, "sections", {}) or {}
    breakdown = CompletenessBreakdown(
        story_prose=_score_words(sections.get("Story", ""), 20, 50),
        expected_behavior=_score_words(sections.get("Expected Behavior", ""), 15, 30),
        boundaries=_score_words(sections.get("Boundaries", ""), 10, 20),
        auditable_claims=_score_bullets(sections.get("Auditable Claims", "")),
        evidence=_score_evidence(story),
    )
    return breakdown


def rating_for_score(score: int) -> str:
    for lo, hi, name in RATINGS:
        if lo <= score <= hi:
            return name
    if score < 0:
        return "skeletal"
    return "complete"


# --------------------------------------------------------------------------- #
# Evidence-resolution gate
# --------------------------------------------------------------------------- #
#
# Volume (word/claim/ref counts) is necessary but not sufficient for the
# Complete rating: a story can declare three fabricated refs and still score
# Evidence=10. The gate caps the rating below Complete for any story that
# declares deterministically-checkable evidence which does not resolve against
# the repository. See ``shared/spec.md`` ("Evidence-Resolution Gate") for the
# authoritative policy, including the treatment of unverified evidence kinds.

# Surface-ref prefixes storystore can only syntax-check — there is no
# deterministic inventory to resolve them against. An unresolved ref of one of
# these kinds is "unverified", not "failed", so it never blocks Complete.
UNVERIFIED_SURFACE_PREFIXES: frozenset[str] = frozenset({"test", "heading", "doc"})

# Rating the gate caps a would-be-Complete story down to.
GATE_CAPPED_RATING = "substantial"


def _surface_prefix(ref: str) -> Optional[str]:
    m = _SURFACE_REF_RE.match(ref.strip())
    if not m:
        return None
    return m.group(1).lower()


def all_inventory_keys(
    inventory: dict[str, Any],
    inferred_surfaces: Optional[list[dict[str, Any]]] = None,
) -> set[str]:
    """Canonical keys for every inventory surface, across all kinds.

    Unlike the surface-uncovered scan (which filters to a configurable set of
    ``surface_kinds``), the gate must resolve any deterministic surface ref, so
    this collects keys for every kind that has a canonical representation.
    """
    keys: set[str] = set()
    surfaces = list(inventory.get("surfaces", []) or [])
    if inferred_surfaces:
        surfaces = surfaces + list(inferred_surfaces)
    for surface in surfaces:
        if not isinstance(surface, dict):
            continue
        key = surface_key(
            str(surface.get("kind", "")),
            name=str(surface.get("name", "")),
            method=str(surface.get("method", "")),
            path=str(surface.get("path", "")),
        )
        if key is not None:
            keys.add(key)
    return keys


def unresolved_deterministic_refs(
    story: Any,
    resolved: dict[str, Any],
    inventory_keys: set[str],
) -> list[str]:
    """Return the story's evidence refs that deterministically fail to resolve.

    Consumes the output of ``inventory.resolve_evidence`` — it does not re-run
    resolution. A ref counts as a deterministic failure when storystore can
    mechanically check it and the check fails:

      * Tests / Docs / Schema / Flag / Copy refs that did not resolve.
      * Surface refs that are malformed, or that name an inventory-backed
        surface (``cli:``/``route:``/``bin:``/``exports:``/``skill:``/
        ``schema:``/``copy:``) absent from the repository.

    Surface refs whose prefix is syntax-only (``test:``/``heading:``/``doc:``)
    are *unverified*, not failed, and are deliberately excluded so they never
    block the Complete rating.
    """
    out: list[str] = []
    if getattr(story, "tests_applicable", True):
        out.extend(resolved.get("tests_missing", []) or [])
    out.extend(resolved.get("docs_missing", []) or [])
    out.extend(resolved.get("schema_missing", []) or [])
    out.extend(resolved.get("flag_missing", []) or [])
    out.extend(resolved.get("copy_missing", []) or [])
    for entry in resolved.get("surface_refs", []) or []:
        ref = entry.get("ref", "")
        if not entry.get("valid"):
            # Malformed refs definitively do not resolve.
            out.append(ref)
            continue
        if _surface_prefix(ref) in UNVERIFIED_SURFACE_PREFIXES:
            continue
        key = ref_to_key(ref)
        if key is None:
            # Recognized but with no canonical inventory mapping — treat as
            # unverified rather than as a failure.
            continue
        if key not in inventory_keys:
            out.append(ref)
    return out


# --------------------------------------------------------------------------- #
# Findings
# --------------------------------------------------------------------------- #


@dataclass
class Finding:
    kind: str
    story_slug: Optional[str]
    severity: str
    suggested_action: str
    title: str
    body: str
    sort_key: tuple = field(default=())

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "story_slug": self.story_slug,
            "severity": self.severity,
            "suggested_action": self.suggested_action,
            "title": self.title,
            "body": self.body,
        }


def severity_for_story(story: Any) -> str:
    return SEVERITY_BY_RESISTANCE.get(getattr(story, "change_resistance", "medium"), "medium")


# --------------------------------------------------------------------------- #
# Coverage analysis
# --------------------------------------------------------------------------- #


def _surface_label(surface: dict[str, Any]) -> str:
    kind = surface["kind"]
    if kind == "cli-command":
        return f'cli "{surface.get("name", "")}"'
    if kind == "http-route":
        return f'route {surface.get("method", "")} {surface.get("path", "")}'
    if kind == "bin":
        return f'bin "{surface.get("name", "")}"'
    if kind == "exports":
        return f'exports "{surface.get("name", "")}"'
    if kind == "schema":
        return f'schema "{surface.get("name", "")}"'
    if kind == "copy":
        return f'copy "{surface.get("name", "")}"'
    return f'{kind} {surface.get("name", "")}'


def collect_findings(
    repo_root: Path,
    stories: list[Any],
    inventory: dict[str, Any],
    *,
    surface_kinds: Iterable[str] = DEFAULT_SURFACE_KINDS,
    completeness_min_rating: str = "substantial",
    completeness_limit: int = 20,
    inferred_surfaces: Optional[list[dict[str, Any]]] = None,
    perf: Optional[Any] = None,
) -> tuple[list[Finding], dict[str, Any]]:
    """Run the three deterministic coverage analyses and return findings.

    Returns (findings, metrics) where ``metrics`` has counters used by both
    the report header and the stdout JSON.
    """
    kinds = set(surface_kinds)
    if perf is not None:
        perf.start("coverage_surfaces")

    # ---- surface-uncovered -------------------------------------------------
    story_keys: set[str] = set()
    for story in stories:
        if getattr(story, "status", "") != "active":
            continue
        for ref in getattr(story, "evidence_surface", []) or []:
            key = ref_to_key(ref)
            if key is not None:
                story_keys.add(key)

    surfaces_scanned = 0
    surface_findings: list[Finding] = []
    seen_surface_keys: set[str] = set()

    all_surfaces = list(inventory.get("surfaces", []) or [])
    if inferred_surfaces:
        for entry in inferred_surfaces:
            if not isinstance(entry, dict) or "kind" not in entry:
                continue
            decorated = dict(entry)
            decorated["_inferred"] = True
            all_surfaces.append(decorated)

    for surface in all_surfaces:
        if surface.get("kind") not in kinds:
            continue
        key = surface_key(
            surface["kind"],
            name=str(surface.get("name", "")),
            method=str(surface.get("method", "")),
            path=str(surface.get("path", "")),
        )
        if key is None:
            continue
        # De-duplicate inventory entries that emit the same key.
        if key in seen_surface_keys:
            continue
        seen_surface_keys.add(key)
        surfaces_scanned += 1
        if key in story_keys:
            continue
        label = _surface_label(surface)
        inferred_note = " [inferred]" if surface.get("_inferred") else ""
        body_lines = [
            f"No active story declares this surface in `Evidence.Surface`{inferred_note}.",
            "",
            f"- surface: `{label}`",
            f"- source: `{surface.get('source', '')}`",
            f"- match key: `{key}`",
        ]
        surface_findings.append(
            Finding(
                kind="surface-uncovered",
                story_slug=None,
                severity="medium",
                suggested_action="update-story",
                title=f"surface-uncovered: {label}",
                body="\n".join(body_lines),
                sort_key=(0, key),
            )
        )

    if perf is not None:
        perf.stop("coverage_surfaces")
        perf.start("coverage_stories")

    # ---- story-untested + story-incomplete ---------------------------------
    untested_findings: list[Finding] = []
    incomplete_candidates: list[tuple[int, Finding]] = []
    evidence_unresolved_findings: list[Finding] = []

    gate_inventory_keys = all_inventory_keys(
        inventory, inferred_surfaces if inferred_surfaces else None
    )

    placeholder_slugs: list[str] = []

    for story in stories:
        slug = getattr(story, "slug", "")
        if getattr(story, "status", "") != "active":
            continue

        sections = getattr(story, "sections", {}) or {}
        intent = sections.get("Intent", "").strip()
        if intent == PLACEHOLDER_INTENT:
            placeholder_slugs.append(slug)

        resolved = inv.resolve_evidence(repo_root, story)

        # story-untested
        if getattr(story, "tests_applicable", True):
            if not resolved["tests_resolved"]:
                sev = severity_for_story(story)
                untested_findings.append(
                    Finding(
                        kind="story-untested",
                        story_slug=slug,
                        severity=sev,
                        suggested_action="add-evidence",
                        title=f"story-untested: {slug}",
                        body=(
                            f"Active story `{slug}` has no resolvable test evidence.\n"
                            "\n"
                            "Add a `### Tests` entry under `## Evidence` that points at "
                            "a real test file, or set `tests_applicable: false` if no "
                            "automated coverage is feasible."
                        ),
                        sort_key=(1, slug),
                    )
                )

        # story-incomplete
        breakdown = score_story(story)
        rating = rating_for_score(breakdown.total())

        # Evidence-resolution gate: a story that scores Complete on volume
        # alone cannot be rated Complete while it declares deterministically-
        # checkable evidence that does not resolve. Cap the rating and name the
        # offending refs. Unverified evidence kinds (test:/heading:/doc:) are
        # excluded by unresolved_deterministic_refs, so a story whose only
        # unresolved refs are unverified keeps its Complete rating.
        if rating == "complete":
            unresolved = unresolved_deterministic_refs(
                story, resolved, gate_inventory_keys
            )
            if unresolved:
                rating = GATE_CAPPED_RATING
                ref_lines = "\n".join(f"- `{r}`" for r in unresolved)
                evidence_unresolved_findings.append(
                    Finding(
                        kind="story-evidence-unresolved",
                        story_slug=slug,
                        severity=severity_for_story(story),
                        suggested_action="fix-code",
                        title=f"story-evidence-unresolved: {slug}",
                        body=(
                            f"Active story `{slug}` scores "
                            f"{breakdown.total()}/50 on volume (Complete) but "
                            f"declares evidence that does not resolve against "
                            f"the repository, so its completeness rating is "
                            f"capped at **{GATE_CAPPED_RATING}**. Complete "
                            f"requires every deterministically-checkable "
                            f"evidence ref to resolve.\n"
                            "\n"
                            "Unresolved evidence refs:\n"
                            f"{ref_lines}\n"
                            "\n"
                            "Fix the code or the references so they resolve, "
                            "or remove the fabricated refs."
                        ),
                        sort_key=(2, -1, slug),
                    )
                )

        min_rank = RATING_RANK.get(completeness_min_rating, RATING_RANK["substantial"])
        if RATING_RANK.get(rating, 0) < min_rank:
            sev = severity_for_story(story)
            body_lines = [
                f"Active story `{slug}` is rated **{rating}** "
                f"(score {breakdown.total()}/50; minimum: {completeness_min_rating}).",
                "",
                "Per-dimension scores:",
                f"- Story prose: {breakdown.story_prose}",
                f"- Expected Behavior: {breakdown.expected_behavior}",
                f"- Boundaries: {breakdown.boundaries}",
                f"- Auditable Claims: {breakdown.auditable_claims}",
                f"- Evidence: {breakdown.evidence}",
            ]
            finding = Finding(
                kind="story-incomplete",
                story_slug=slug,
                severity=sev,
                suggested_action="update-story",
                title=f"story-incomplete: {slug} ({rating})",
                body="\n".join(body_lines),
                sort_key=(2, breakdown.total(), slug),
            )
            incomplete_candidates.append((breakdown.total(), finding))

    # cap incomplete findings: worst first
    incomplete_candidates.sort(key=lambda pair: (pair[0], pair[1].story_slug or ""))
    incomplete_findings = [f for _, f in incomplete_candidates[:completeness_limit]]

    if perf is not None:
        perf.stop("coverage_stories")

    findings: list[Finding] = []
    findings.extend(sorted(surface_findings, key=lambda f: f.sort_key))
    findings.extend(sorted(untested_findings, key=lambda f: f.sort_key))
    findings.extend(
        sorted(evidence_unresolved_findings, key=lambda f: (f.story_slug or ""))
    )
    findings.extend(incomplete_findings)

    metrics = {
        "surfaces_scanned": surfaces_scanned,
        "stories_scanned": len(stories),
        "placeholder_intent_slugs": placeholder_slugs,
    }
    return findings, metrics


# --------------------------------------------------------------------------- #
# Report rendering
# --------------------------------------------------------------------------- #


def render_report(
    *,
    repo_root: Path,
    languages: dict[str, list[str]],
    placeholder_slugs: list[str],
    findings: list[Finding],
    thorough: bool,
) -> str:
    out: list[str] = []
    out.append(f"# storystore coverage report")
    out.append("")
    out.append(f"Repo: `{repo_root}`")
    out.append("")
    out.append("## Language Coverage")
    out.append("")
    out.append(f"Detected: {', '.join(languages.get('detected', [])) or 'none'}")
    out.append(f"Extracted: {', '.join(languages.get('extracted', [])) or 'none'}")
    uncovered = sorted(set(languages.get("detected", [])) - set(languages.get("extracted", [])))
    if uncovered and not thorough:
        out.append(
            f"Note: {', '.join(uncovered)} detected but not covered by built-in "
            "extractors. Re-run with --thorough to author inferred surface coverage."
        )
    out.append("")

    if placeholder_slugs:
        out.append(
            f"{len(placeholder_slugs)} active stories have placeholder Intent: "
            + ", ".join(placeholder_slugs)
        )
        out.append("")

    out.append(f"## Findings ({len(findings)})")
    out.append("")
    if not findings:
        out.append("No coverage findings.")
        out.append("")
    else:
        for i, finding in enumerate(findings, start=1):
            out.append(f"## Finding {i}: {finding.title}")
            out.append("")
            out.append(f"- kind: {finding.kind}")
            out.append(f"- story_slug: {finding.story_slug if finding.story_slug else 'null'}")
            out.append(f"- severity: {finding.severity}")
            out.append(f"- suggested_action: {finding.suggested_action}")
            out.append("")
            out.append(finding.body)
            out.append("")
    return "\n".join(out).rstrip() + "\n"


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def _default_report_path() -> str:
    return f"/tmp/stories-coverage-{int(time.time())}.md"


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="coverage.py",
        description="storystore coverage: software-to-story coverage and completeness.",
    )
    p.add_argument("--repo-root", required=True)
    p.add_argument("--report-path", default=None)
    p.add_argument("--strict", action="store_true")
    p.add_argument("--thorough", action="store_true")
    p.add_argument("--inferred-surface", default=None)
    p.add_argument("--source-root", default=None)
    p.add_argument("--include-dir", action="append", default=[])
    p.add_argument("--surface-kind", action="append", default=[])
    p.add_argument(
        "--completeness-min-rating",
        default="substantial",
        choices=sorted(RATING_RANK),
    )
    p.add_argument("--completeness-limit", type=int, default=20)
    p.add_argument("--perf-warn-ms", type=int, default=None)
    return p.parse_args(argv)


def _resolve_perf_threshold(args: argparse.Namespace) -> int:
    if args.perf_warn_ms is not None:
        return max(0, int(args.perf_warn_ms))
    env = os.environ.get("STORYSTORE_PERF_WARN_MS")
    if env is not None:
        try:
            return max(0, int(env))
        except ValueError:
            return DEFAULT_PERF_WARN_MS
    return DEFAULT_PERF_WARN_MS


def _load_inferred_surfaces(path: Optional[str]) -> list[dict[str, Any]]:
    if not path:
        return []
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(data, dict) and "surfaces" in data:
        data = data["surfaces"]
    if not isinstance(data, list):
        raise ValueError("inferred-surface file must be a JSON list (or {surfaces: [...]})")
    return [d for d in data if isinstance(d, dict)]


def main(argv: Optional[list[str]] = None) -> int:
    args = _parse_args(list(sys.argv[1:] if argv is None else argv))
    perf = lib.PerfTimer()

    repo_root = Path(args.repo_root).resolve()
    if not repo_root.is_dir():
        print(f"coverage.py: repo-root is not a directory: {repo_root}", file=sys.stderr)
        return 2

    stories_dir = repo_root / "docs" / "stories"
    if not stories_dir.is_dir():
        print(
            f"coverage.py: stories directory not found: {stories_dir}",
            file=sys.stderr,
        )
        return 2

    perf.start("load_stories")
    try:
        stories = lib.load_stories(stories_dir)
    except lib.ParseError as exc:
        print(f"coverage.py: {exc}", file=sys.stderr)
        return getattr(exc, "exit_code", 2)
    perf.stop("load_stories")

    perf.start("build_inventory")
    inventory = inv.build_inventory(
        repo_root,
        source_root=args.source_root,
        include_dirs=args.include_dir or None,
    )
    perf.stop("build_inventory")

    surface_kinds = set(args.surface_kind) if args.surface_kind else set(DEFAULT_SURFACE_KINDS)

    try:
        inferred = _load_inferred_surfaces(args.inferred_surface)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"coverage.py: failed to read --inferred-surface: {exc}", file=sys.stderr)
        return 2

    if inferred and not args.thorough:
        print(
            "coverage.py: --inferred-surface requires --thorough",
            file=sys.stderr,
        )
        return 2

    findings, metrics = collect_findings(
        repo_root,
        stories,
        inventory,
        surface_kinds=surface_kinds,
        completeness_min_rating=args.completeness_min_rating,
        completeness_limit=max(0, int(args.completeness_limit)),
        inferred_surfaces=inferred if args.thorough else None,
        perf=perf,
    )

    report_path = Path(args.report_path) if args.report_path else Path(_default_report_path())
    report = render_report(
        repo_root=repo_root,
        languages=inventory.get("languages", {"detected": [], "extracted": []}),
        placeholder_slugs=metrics["placeholder_intent_slugs"],
        findings=findings,
        thorough=args.thorough,
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report, encoding="utf-8")

    threshold_ms = _resolve_perf_threshold(args)
    duration_ms = perf.total_ms()
    if threshold_ms and duration_ms > threshold_ms:
        print(
            f"coverage.py: perf warning — {duration_ms}ms exceeds threshold {threshold_ms}ms",
            file=sys.stderr,
        )

    summary = {
        "report_path": str(report_path),
        "findings_count": len(findings),
        "performance": {
            "duration_ms": duration_ms,
            "stories_scanned": metrics["stories_scanned"],
            "surfaces_scanned": metrics["surfaces_scanned"],
            "phase_breakdown": perf.phases_ms(),
        },
    }
    json.dump(summary, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")

    if args.strict and findings:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
