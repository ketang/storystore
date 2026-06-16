"""Generation-then-audit round-trip on a markdown-only fixture repo.

Exercises the full skill-surface path end-to-end:

1. A markdown-only repo (no TypeScript/JS source, only docs + skill dirs)
   is scanned for candidates.
2. Every candidate is authored into a story via ``write_story.py`` using a
   ``skill:`` / ``heading:`` surface ref.
3. A full ``stories-audit`` over the generated stories produces zero
   findings — proving the generator emits only refs the audit validator
   accepts AND resolves, with no structural noise.

Regression guard for ss-yoa: before the ``skill:`` prefix + skill-dir
extractor, a Python/markdown repo reported every surface ref as unmatched
("Detected: (none), Extracted: (none)"), drowning real findings in noise.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
LIST_CANDIDATES = REPO_ROOT / "shared" / "list_candidates.py"
WRITE_STORY = REPO_ROOT / "shared" / "write_story.py"
AUDIT = REPO_ROOT / "shared" / "audit.py"


def _run(args: list[str], *, stdin: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, *args],
        input=stdin,
        capture_output=True,
        text=True,
    )


def _candidates(repo: Path) -> list[dict]:
    proc = _run([str(LIST_CANDIDATES), "--repo-root", str(repo)])
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)["candidates"]


def _surface_ref(candidate: dict) -> str:
    """Map a candidate to the surface ref a generator would write for it."""
    kind, name = candidate["kind"], candidate["name"]
    if kind == "skill":
        return f"skill: {name}"
    if kind == "heading":
        return f"heading: {name}"
    raise AssertionError(f"unexpected candidate kind for markdown repo: {kind!r}")


def _slug_for(candidate: dict, idx: int) -> str:
    base = "".join(c if c.isalnum() else "-" for c in candidate["name"].lower())
    base = "-".join(p for p in base.split("-") if p) or "surface"
    # Ensure a durable multi-word slug regardless of the candidate name.
    return f"covers-{base}-surface-{idx}"


def _make_markdown_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "md-repo"
    # Two skill directories (the new language-agnostic surface).
    for name in ("deploy-app", "rotate-secrets"):
        d = repo / "skills" / name
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text(f"# {name}\n\nDoes {name}.\n", encoding="utf-8")
    # A README contributing doc-heading surfaces.
    (repo / "README.md").write_text(
        "# Toolkit\n\n## Getting Started\n\nstuff\n\n## Operations\n\nmore\n",
        encoding="utf-8",
    )
    # Initialized (empty) stories directory.
    stories = repo / "docs" / "stories"
    stories.mkdir(parents=True)
    (stories / "README.md").write_text("# stories\n", encoding="utf-8")
    (stories / "INDEX.md").write_text("", encoding="utf-8")
    return repo


def test_generation_then_audit_roundtrip_is_clean(tmp_path):
    repo = _make_markdown_repo(tmp_path)

    # The fixture is genuinely markdown-only: no extractable language.
    candidates = _candidates(repo)
    kinds = {c["kind"] for c in candidates}
    assert "skill" in kinds, candidates
    # Markdown-only: candidates are skill dirs and doc headings, nothing else.
    assert kinds <= {"skill", "heading"}, candidates

    # Author one story per candidate; the generator must accept every ref.
    for idx, candidate in enumerate(candidates):
        ref = _surface_ref(candidate)
        payload = {
            "title": f"Covers {candidate['name']}",
            "slug": _slug_for(candidate, idx),
            "intent": f"Users rely on {candidate['name']}.",
            "evidence": {"surface": [ref], "docs": ["README.md"]},
        }
        proc = _run(
            [str(WRITE_STORY), "--repo-root", str(repo), "--observed"],
            stdin=json.dumps(payload),
        )
        assert proc.returncode == 0, f"write_story rejected {ref!r}: {proc.stderr}"

    # Every candidate is now covered — the scanner subtracts them all.
    assert _candidates(repo) == []

    # Full audit over the generated stories: zero structural noise.
    # Explicit --report-path avoids the second-granularity /tmp default that
    # collides between tests running in the same wall-clock second.
    report = repo / "audit.md"
    audit = _run(
        [str(AUDIT), "--repo-root", str(repo), "--strict", "--report-path", str(report)]
    )
    payload = json.loads(audit.stdout)
    assert payload["findings_count"] == 0, audit.stdout
    assert audit.returncode == 0, audit.stderr


def test_skill_ref_resolves_against_inventoried_skill_dir(tmp_path):
    """A skill ref matching an existing SKILL.md dir produces no finding;
    one with no matching dir is reported as surface-missing."""
    repo = _make_markdown_repo(tmp_path)

    payload = {
        "title": "Deploy The Application Safely",
        "slug": "deploy-the-application-safely",
        "intent": "Operators deploy the app.",
        "evidence": {"surface": ["skill: deploy-app", "skill: nonexistent-skill"],
                     "docs": ["README.md"]},
    }
    proc = _run(
        [str(WRITE_STORY), "--repo-root", str(repo), "--observed"],
        stdin=json.dumps(payload),
    )
    assert proc.returncode == 0, proc.stderr

    report_path = repo / "audit.md"
    audit = _run(
        [str(AUDIT), "--repo-root", str(repo), "--report-path", str(report_path)]
    )
    out = json.loads(audit.stdout)
    report = Path(out["report_path"]).read_text(encoding="utf-8")
    # The matching skill ref resolves; only the non-existent one is flagged.
    assert "skill: deploy-app` does not resolve" not in report
    assert "skill: nonexistent-skill` does not resolve" in report
    assert out["findings_count"] == 1, report
