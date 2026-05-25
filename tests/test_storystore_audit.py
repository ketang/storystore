"""Tests for shared/audit.py — read-only fidelity audit."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "shared" / "audit.py"


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


STORY_TEMPLATE = """\
---
title: {title}
slug: {slug}
status: {status}
authority: {authority}
change_resistance: {resistance}
---

# {title}

## Intent
{intent}

## Story
{story}

## Expected Behavior
{expected}

## Boundaries
{boundaries}

## Auditable Claims
{claims}

## Evidence
### Tests
{tests}
### Surface
{surface}
### Docs
{docs}
"""


def _write_story(
    repo: Path,
    slug: str,
    *,
    title: str | None = None,
    status: str = "active",
    authority: str = "accepted",
    resistance: str = "medium",
    intent: str = "Users can use this.",
    story: str = "User narrative.",
    expected: str = "Visible behavior.",
    boundaries: str = "Out of scope.",
    claims: list[str] | None = None,
    tests: list[str] | None = None,
    surface: list[str] | None = None,
    docs: list[str] | None = None,
) -> Path:
    stories_dir = repo / "docs" / "stories"
    stories_dir.mkdir(parents=True, exist_ok=True)
    title = title or slug.replace("-", " ").title()
    claims_text = "\n".join(f"- {c}" for c in (claims or ["The feature exists."]))
    tests_text = "\n".join(f"- `{t}`" for t in (tests or []))
    surface_text = "\n".join(f"- `{s}`" for s in (surface or []))
    docs_text = "\n".join(f"- `{d}`" for d in (docs or []))
    path = stories_dir / f"{slug}.md"
    path.write_text(
        STORY_TEMPLATE.format(
            title=title,
            slug=slug,
            status=status,
            authority=authority,
            resistance=resistance,
            intent=intent,
            story=story,
            expected=expected,
            boundaries=boundaries,
            claims=claims_text,
            tests=tests_text,
            surface=surface_text,
            docs=docs_text,
        ),
        encoding="utf-8",
    )
    return path


def _init_repo(tmp_path: Path) -> Path:
    (tmp_path / "docs" / "stories").mkdir(parents=True)
    return tmp_path


def _run(repo: Path, *args: str) -> subprocess.CompletedProcess:
    report = repo / "audit.md"
    cmd = [sys.executable, str(SCRIPT), "--repo-root", str(repo), "--report-path", str(report), *args]
    return subprocess.run(cmd, capture_output=True, text=True, check=False)


def _findings_of_kind(report_text: str, kind: str) -> list[str]:
    """Return Markdown finding-headers from the report that match the kind."""
    out: list[str] = []
    blocks = report_text.split("## Finding ")
    for block in blocks[1:]:
        header_line = block.splitlines()[0]
        body = block
        if f"kind: {kind}" in body:
            out.append(header_line)
    return out


# --------------------------------------------------------------------------- #
# Tests
# --------------------------------------------------------------------------- #


def test_clean_repo_exits_zero_and_emits_no_findings(tmp_path):
    repo = _init_repo(tmp_path)
    (repo / "tests").mkdir()
    (repo / "tests" / "login.spec.ts").write_text(
        'describe("login", () => { it("works", () => {}); });', encoding="utf-8"
    )
    (repo / "src").mkdir()
    (repo / "src" / "cli.ts").write_text('program.command("login");', encoding="utf-8")
    (repo / "package.json").write_text('{"name": "x"}', encoding="utf-8")
    _write_story(
        repo,
        slug="user-login-flow",
        tests=["tests/login.spec.ts"],
        surface=["cli: login"],
        claims=["Login command exists."],
    )
    result = _run(repo)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["findings_count"] == 0
    assert payload["performance"]["stories_scanned"] == 1
    assert "duration_ms" in payload["performance"]
    report = (repo / "audit.md").read_text()
    assert "## Language Coverage" in report
    assert "No findings." in report


def test_surface_missing_finding_when_ref_does_not_resolve(tmp_path):
    repo = _init_repo(tmp_path)
    (repo / "package.json").write_text('{"name": "x"}', encoding="utf-8")
    (repo / "src").mkdir()
    (repo / "src" / "cli.ts").write_text('program.command("login");', encoding="utf-8")
    _write_story(
        repo,
        slug="user-login-flow",
        surface=["cli: phantom"],
    )
    result = _run(repo)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["findings_count"] >= 1
    report = (repo / "audit.md").read_text()
    assert _findings_of_kind(report, "surface-missing"), report


def test_test_evidence_missing_when_glob_matches_nothing(tmp_path):
    repo = _init_repo(tmp_path)
    _write_story(
        repo,
        slug="user-login-flow",
        tests=["tests/nope.spec.ts"],
    )
    result = _run(repo)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["findings_count"] >= 1
    report = (repo / "audit.md").read_text()
    assert _findings_of_kind(report, "test-evidence-missing"), report


def test_scoped_story_audit_skips_other_stories(tmp_path):
    repo = _init_repo(tmp_path)
    _write_story(repo, slug="user-login-flow", tests=["tests/missing-a.spec.ts"])
    _write_story(repo, slug="user-logout-flow", tests=["tests/missing-b.spec.ts"])
    result = _run(repo, "--story", "user-login-flow")
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["performance"]["stories_scanned"] == 1
    report = (repo / "audit.md").read_text()
    assert "user-login-flow" in report
    assert "user-logout-flow" not in report


def test_strict_exits_one_with_findings(tmp_path):
    repo = _init_repo(tmp_path)
    _write_story(repo, slug="user-login-flow", tests=["tests/missing.spec.ts"])
    result = _run(repo, "--strict")
    assert result.returncode == 1


def test_strict_exits_zero_when_clean(tmp_path):
    repo = _init_repo(tmp_path)
    (repo / "tests").mkdir()
    (repo / "tests" / "login.spec.ts").write_text("// test\n", encoding="utf-8")
    (repo / "package.json").write_text('{"name": "x"}', encoding="utf-8")
    (repo / "src").mkdir()
    (repo / "src" / "cli.ts").write_text('program.command("login");', encoding="utf-8")
    _write_story(
        repo,
        slug="user-login-flow",
        tests=["tests/login.spec.ts"],
        surface=["cli: login"],
    )
    result = _run(repo, "--strict")
    assert result.returncode == 0, result.stderr


def test_bump_clean_writes_last_audited_only_on_zero_finding_stories(tmp_path):
    repo = _init_repo(tmp_path)
    (repo / "tests").mkdir()
    (repo / "tests" / "ok.spec.ts").write_text("// test\n", encoding="utf-8")
    (repo / "package.json").write_text('{"name": "x"}', encoding="utf-8")
    (repo / "src").mkdir()
    (repo / "src" / "cli.ts").write_text(
        'program.command("login");\nprogram.command("logout");',
        encoding="utf-8",
    )
    clean = _write_story(
        repo, slug="user-login-flow",
        tests=["tests/ok.spec.ts"], surface=["cli: login"],
    )
    dirty = _write_story(
        repo, slug="user-logout-flow",
        tests=["tests/gone.spec.ts"], surface=["cli: logout"],
    )
    result = _run(repo, "--bump-clean")
    assert result.returncode == 0, result.stderr
    assert "last_audited:" in clean.read_text(encoding="utf-8")
    assert "last_audited:" not in dirty.read_text(encoding="utf-8")


def test_agent_pointer_missing_emitted_when_agent_file_has_no_pointer(tmp_path):
    repo = _init_repo(tmp_path)
    (repo / "AGENTS.md").write_text("# Some unrelated guidance\n", encoding="utf-8")
    result = _run(repo)
    assert result.returncode == 0, result.stderr
    report = (repo / "audit.md").read_text()
    assert _findings_of_kind(report, "agent-pointer-missing"), report
    assert "story_slug: null" in report


def test_agent_pointer_missing_suppressed_by_marker(tmp_path):
    repo = _init_repo(tmp_path)
    (repo / "AGENTS.md").write_text(
        "# Some unrelated guidance\n\n<!-- storystore: no-pointer -->\n",
        encoding="utf-8",
    )
    result = _run(repo)
    assert result.returncode == 0, result.stderr
    report = (repo / "audit.md").read_text()
    assert not _findings_of_kind(report, "agent-pointer-missing"), report


def test_agent_pointer_missing_silenced_by_pointer_present(tmp_path):
    repo = _init_repo(tmp_path)
    (repo / "AGENTS.md").write_text(
        "# Guidance\n\nSee docs/stories/ for intent stories.\n",
        encoding="utf-8",
    )
    result = _run(repo)
    assert result.returncode == 0, result.stderr
    report = (repo / "audit.md").read_text()
    assert not _findings_of_kind(report, "agent-pointer-missing"), report


def test_language_coverage_block_in_report(tmp_path):
    repo = _init_repo(tmp_path)
    (repo / "package.json").write_text('{"name": "x"}', encoding="utf-8")
    (repo / "go.mod").write_text("module x\n", encoding="utf-8")
    result = _run(repo)
    assert result.returncode == 0, result.stderr
    report = (repo / "audit.md").read_text()
    assert "## Language Coverage" in report
    assert "typescript" in report
    assert "go" in report
    assert "--thorough" in report  # nudge for uncovered language


def test_stdout_json_includes_findings_count_and_performance(tmp_path):
    repo = _init_repo(tmp_path)
    _write_story(repo, slug="user-login-flow", tests=["tests/missing.spec.ts"])
    result = _run(repo)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["findings_count"] >= 1
    perf = payload["performance"]
    assert "duration_ms" in perf
    assert "stories_scanned" in perf
    assert "evidence_refs_resolved" in perf
    assert "phase_breakdown" in perf


def test_missing_repo_root_fails(tmp_path):
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--repo-root", str(tmp_path / "nope")],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 2


def test_missing_stories_dir_fails(tmp_path):
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--repo-root", str(tmp_path)],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 2


def test_unknown_story_slug_fails(tmp_path):
    repo = _init_repo(tmp_path)
    _write_story(repo, slug="user-login-flow")
    result = _run(repo, "--story", "no-such-slug")
    assert result.returncode == 2


def test_claim_unsupported_when_no_evidence(tmp_path):
    repo = _init_repo(tmp_path)
    _write_story(
        repo,
        slug="user-login-flow",
        claims=["Login works correctly."],
        tests=[],
        surface=[],
        docs=[],
    )
    result = _run(repo)
    assert result.returncode == 0, result.stderr
    report = (repo / "audit.md").read_text()
    assert _findings_of_kind(report, "claim-unsupported"), report


def test_severity_maps_from_change_resistance(tmp_path):
    repo = _init_repo(tmp_path)
    _write_story(
        repo, slug="low-story", resistance="low",
        tests=["tests/x.spec.ts"],
    )
    _write_story(
        repo, slug="high-story", resistance="high",
        tests=["tests/y.spec.ts"],
    )
    result = _run(repo)
    assert result.returncode == 0, result.stderr
    report = (repo / "audit.md").read_text()
    # Each story should produce its own severity in its surface-missing/test-evidence finding.
    # Find the high-story block; severity should be 'high'.
    assert "severity: low" in report
    assert "severity: high" in report


def test_tests_applicable_false_suppresses_test_evidence_missing(tmp_path):
    repo = _init_repo(tmp_path)
    # tests_applicable: false with empty Evidence.Tests; we'll add a surface ref
    # that won't match anything to ensure the story still runs through.
    stories_dir = repo / "docs" / "stories"
    stories_dir.mkdir(parents=True, exist_ok=True)
    (stories_dir / "no-tests.md").write_text(
        """---
title: No Tests
slug: no-tests-story
status: active
authority: accepted
change_resistance: medium
tests_applicable: false
---

# No Tests

## Intent
This is doc-only.

## Auditable Claims
- The doc exists.

## Evidence
### Docs
- `README.md`
""",
        encoding="utf-8",
    )
    (repo / "README.md").write_text("# readme\n", encoding="utf-8")
    result = _run(repo)
    assert result.returncode == 0, result.stderr
    report = (repo / "audit.md").read_text()
    assert not _findings_of_kind(report, "test-evidence-missing"), report


def test_schema_evidence_missing_when_ref_does_not_resolve(tmp_path):
    """schema-evidence-missing finding is emitted for unresolvable schema refs."""
    repo = _init_repo(tmp_path)
    stories_dir = repo / "docs" / "stories"
    stories_dir.mkdir(parents=True, exist_ok=True)
    (stories_dir / "user-schema.md").write_text(
        """\
---
title: User Schema
slug: user-schema
status: active
authority: accepted
change_resistance: medium
---

# User Schema

## Intent
Track user email in the database.

## Auditable Claims
- The users table has an email column.

## Evidence
### Schema
- `users.email`
""",
        encoding="utf-8",
    )
    result = _run(repo)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["findings_count"] >= 1
    report = (repo / "audit.md").read_text()
    assert _findings_of_kind(report, "schema-evidence-missing"), report


def test_schema_evidence_resolved_no_finding(tmp_path):
    """No schema-evidence-missing when migration defines the column."""
    repo = _init_repo(tmp_path)
    # Create a migration file.
    mig_dir = repo / "migrations"
    mig_dir.mkdir()
    (mig_dir / "001_create_users.sql").write_text(
        "CREATE TABLE users (\n  id INTEGER PRIMARY KEY,\n  email VARCHAR(255)\n);\n",
        encoding="utf-8",
    )
    stories_dir = repo / "docs" / "stories"
    stories_dir.mkdir(parents=True, exist_ok=True)
    (stories_dir / "user-schema.md").write_text(
        """\
---
title: User Schema
slug: user-schema
status: active
authority: accepted
change_resistance: medium
---

# User Schema

## Intent
Track user email in the database.

## Auditable Claims
- The users table has an email column.

## Evidence
### Schema
- `users.email`
""",
        encoding="utf-8",
    )
    result = _run(repo)
    assert result.returncode == 0, result.stderr
    report = (repo / "audit.md").read_text()
    assert not _findings_of_kind(report, "schema-evidence-missing"), report


def test_perf_warn_threshold_zero_disables(tmp_path):
    """STORYSTORE_PERF_WARN_MS=0 disables the threshold warning."""
    repo = _init_repo(tmp_path)
    _write_story(repo, slug="user-login-flow")
    cmd = [
        sys.executable, str(SCRIPT),
        "--repo-root", str(repo),
        "--report-path", str(repo / "audit.md"),
        "--perf-warn-ms", "0",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    assert result.returncode == 0
    assert "STORYSTORE_PERF_WARN" not in result.stderr
