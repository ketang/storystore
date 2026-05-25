"""Golden-output regression tests for audit, coverage, and impact-check reports.

Tests lock in the report format so changes to output shape are detected
automatically. Non-deterministic fields (timestamps, durations, temp paths)
are normalized before comparison.

Set ``UPDATE_GOLDEN=1`` to regenerate golden files from the current output.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
GOLDEN_DIR = FIXTURES_DIR / "golden"

# ---------------------------------------------------------------------------
# Normalization helpers
# ---------------------------------------------------------------------------

_GENERATED_RE = re.compile(r"^Generated: .+$", re.MULTILINE)
_INDEX_GENERATED_RE = re.compile(
    r"(\d+ stories — generated )\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z"
)
_REPO_PATH_MD_RE = re.compile(r"Repo: `.+`")
_TIMESTAMP_RE = re.compile(r"\d{4}-?\d{2}-?\d{2}T\d{2}:?\d{2}:?\d{2}Z")


def normalize_markdown(text: str) -> str:
    """Strip non-deterministic fields from Markdown report text."""
    text = _GENERATED_RE.sub("Generated: <TIMESTAMP>", text)
    text = _INDEX_GENERATED_RE.sub(r"\g<1><TIMESTAMP>", text)
    text = _REPO_PATH_MD_RE.sub("Repo: `<REPO_ROOT>`", text)
    text = _TIMESTAMP_RE.sub("<TIMESTAMP>", text)
    return text


# Matches absolute paths in stderr messages (e.g. /tmp/pytest-.../fixture/...)
_ABS_PATH_RE = re.compile(r"/(?:tmp|home)/\S+")


def normalize_stderr(text: str) -> str:
    """Normalize absolute paths in stderr output."""
    return _ABS_PATH_RE.sub("<PATH>", text)


def normalize_json_obj(obj: dict) -> dict:
    """Normalize a parsed JSON object in-place and return it."""
    if "report_path" in obj:
        obj["report_path"] = "<REPORT_PATH>"
    perf = obj.get("performance", {})
    if "duration_ms" in perf:
        perf["duration_ms"] = 0
    for key in list(perf.get("phase_breakdown", {}).keys()):
        perf["phase_breakdown"][key] = 0
    return obj


def normalize_impact_json(obj: dict) -> dict:
    """Normalize impact-check JSON output."""
    perf = obj.get("performance", {})
    if "duration_ms" in perf:
        perf["duration_ms"] = 0
    return obj


# ---------------------------------------------------------------------------
# Golden file helpers
# ---------------------------------------------------------------------------


def _golden_path(name: str) -> Path:
    return GOLDEN_DIR / name


def _should_update() -> bool:
    return os.environ.get("UPDATE_GOLDEN", "").strip() in ("1", "true", "yes")


def _write_golden(name: str, content: str) -> None:
    path = _golden_path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _read_golden(name: str) -> str:
    path = _golden_path(name)
    if not path.is_file():
        pytest.fail(
            f"Golden file {path} does not exist. "
            f"Run with UPDATE_GOLDEN=1 to generate it."
        )
    return path.read_text(encoding="utf-8")


def assert_golden(name: str, actual: str) -> None:
    """Compare actual output against golden file, or update it."""
    if _should_update():
        _write_golden(name, actual)
        return
    expected = _read_golden(name)
    assert actual == expected, (
        f"Golden mismatch for {name}. "
        f"Run with UPDATE_GOLDEN=1 to update."
    )


# ---------------------------------------------------------------------------
# Fixture / script helpers
# ---------------------------------------------------------------------------


def _copy_fixture(name: str, dest: Path) -> Path:
    src = FIXTURES_DIR / name
    target = dest / name
    shutil.copytree(src, target)
    return target


def _run_script(script_rel: str, repo: Path, *args: str) -> subprocess.CompletedProcess:
    cmd = [sys.executable, str(REPO_ROOT / script_rel), "--repo-root", str(repo), *args]
    return subprocess.run(cmd, capture_output=True, text=True, check=False)


def _run_audit(repo: Path, *extra: str) -> subprocess.CompletedProcess:
    report_path = repo / "audit-report.md"
    return _run_script(
        "shared/audit.py", repo,
        "--report-path", str(report_path),
        "--perf-warn-ms", "0",
        *extra,
    )


def _run_coverage(repo: Path, *extra: str) -> subprocess.CompletedProcess:
    report_path = repo / "coverage-report.md"
    return _run_script(
        "shared/coverage.py", repo,
        "--report-path", str(report_path),
        "--perf-warn-ms", "0",
        *extra,
    )


def _run_impact(repo: Path, *extra: str) -> subprocess.CompletedProcess:
    return _run_script(
        "shared/impact_check.py", repo,
        "--perf-warn-ms", "0",
        *extra,
    )


def _run_edit_section(repo: Path, *extra: str) -> subprocess.CompletedProcess:
    return _run_script("shared/edit_section.py", repo, *extra)


# ---------------------------------------------------------------------------
# Audit golden tests
# ---------------------------------------------------------------------------


class TestAuditGolden:
    """Golden output tests for the audit report."""

    def test_audit_ts_cli(self, tmp_path: Path) -> None:
        """Audit of ts-cli fixture: report shape with findings."""
        repo = _copy_fixture("ts-cli", tmp_path)
        result = _run_audit(repo)
        assert result.returncode == 0

        payload = json.loads(result.stdout)
        normalize_json_obj(payload)
        json_text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
        assert_golden("audit_ts_cli_stdout.json", json_text)

        report = (repo / "audit-report.md").read_text(encoding="utf-8")
        normalized_md = normalize_markdown(report)
        assert_golden("audit_ts_cli_report.md", normalized_md)

    def test_audit_http_api(self, tmp_path: Path) -> None:
        """Audit of http-api fixture: locked-section story with findings."""
        repo = _copy_fixture("http-api", tmp_path)
        result = _run_audit(repo)
        assert result.returncode == 0

        payload = json.loads(result.stdout)
        normalize_json_obj(payload)
        json_text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
        assert_golden("audit_http_api_stdout.json", json_text)

        report = (repo / "audit-report.md").read_text(encoding="utf-8")
        normalized_md = normalize_markdown(report)
        assert_golden("audit_http_api_report.md", normalized_md)

    def test_audit_drift(self, tmp_path: Path) -> None:
        """Audit of drift fixture: orphaned evidence findings."""
        repo = _copy_fixture("drift", tmp_path)
        result = _run_audit(repo)
        assert result.returncode == 0

        payload = json.loads(result.stdout)
        normalize_json_obj(payload)
        json_text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
        assert_golden("audit_drift_stdout.json", json_text)

        report = (repo / "audit-report.md").read_text(encoding="utf-8")
        normalized_md = normalize_markdown(report)
        assert_golden("audit_drift_report.md", normalized_md)

    def test_audit_empty_no_stories_dir(self, tmp_path: Path) -> None:
        """Audit of empty fixture (no docs/stories/) exits 2."""
        repo = _copy_fixture("empty", tmp_path)
        result = _run_audit(repo)
        assert result.returncode == 2
        assert_golden("audit_empty_stderr.txt", normalize_stderr(result.stderr))

    def test_audit_scoped(self, tmp_path: Path) -> None:
        """Scoped audit: single story slug."""
        repo = _copy_fixture("ts-cli", tmp_path)
        result = _run_audit(repo, "--story", "cli-init-command")
        assert result.returncode == 0

        payload = json.loads(result.stdout)
        normalize_json_obj(payload)
        json_text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
        assert_golden("audit_scoped_stdout.json", json_text)

        report = (repo / "audit-report.md").read_text(encoding="utf-8")
        normalized_md = normalize_markdown(report)
        assert_golden("audit_scoped_report.md", normalized_md)

    def test_audit_strict_with_findings(self, tmp_path: Path) -> None:
        """Strict mode exits 1 when findings present; output shape unchanged."""
        repo = _copy_fixture("drift", tmp_path)
        result = _run_audit(repo, "--strict")
        assert result.returncode == 1

        payload = json.loads(result.stdout)
        normalize_json_obj(payload)
        json_text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
        assert_golden("audit_strict_findings_stdout.json", json_text)


# ---------------------------------------------------------------------------
# Coverage golden tests
# ---------------------------------------------------------------------------


class TestCoverageGolden:
    """Golden output tests for the coverage report."""

    def test_coverage_ts_cli(self, tmp_path: Path) -> None:
        """Coverage of ts-cli fixture."""
        repo = _copy_fixture("ts-cli", tmp_path)
        result = _run_coverage(repo)
        assert result.returncode == 0

        payload = json.loads(result.stdout)
        normalize_json_obj(payload)
        json_text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
        assert_golden("coverage_ts_cli_stdout.json", json_text)

        report = (repo / "coverage-report.md").read_text(encoding="utf-8")
        normalized_md = normalize_markdown(report)
        assert_golden("coverage_ts_cli_report.md", normalized_md)

    def test_coverage_http_api(self, tmp_path: Path) -> None:
        """Coverage of http-api fixture."""
        repo = _copy_fixture("http-api", tmp_path)
        result = _run_coverage(repo)
        assert result.returncode == 0

        payload = json.loads(result.stdout)
        normalize_json_obj(payload)
        json_text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
        assert_golden("coverage_http_api_stdout.json", json_text)

        report = (repo / "coverage-report.md").read_text(encoding="utf-8")
        normalized_md = normalize_markdown(report)
        assert_golden("coverage_http_api_report.md", normalized_md)

    def test_coverage_empty_no_stories_dir(self, tmp_path: Path) -> None:
        """Coverage of empty fixture (no docs/stories/) exits 2."""
        repo = _copy_fixture("empty", tmp_path)
        result = _run_coverage(repo)
        assert result.returncode == 2
        assert_golden("coverage_empty_stderr.txt", normalize_stderr(result.stderr))

    def test_coverage_strict_with_findings(self, tmp_path: Path) -> None:
        """Strict coverage exits 1 when findings exist."""
        repo = _copy_fixture("ts-cli", tmp_path)
        result = _run_coverage(repo, "--strict")
        assert result.returncode == 1

        payload = json.loads(result.stdout)
        normalize_json_obj(payload)
        json_text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
        assert_golden("coverage_strict_findings_stdout.json", json_text)


# ---------------------------------------------------------------------------
# Impact-check golden tests
# ---------------------------------------------------------------------------


class TestImpactCheckGolden:
    """Golden output tests for the impact-check report."""

    def test_impact_by_file(self, tmp_path: Path) -> None:
        """Impact check matching by file path."""
        repo = _copy_fixture("ts-cli", tmp_path)
        result = _run_impact(repo, "--file", "src/cli.ts")
        assert result.returncode == 0

        payload = json.loads(result.stdout)
        normalize_impact_json(payload)
        json_text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
        assert_golden("impact_file_stdout.json", json_text)

    def test_impact_by_surface(self, tmp_path: Path) -> None:
        """Impact check matching by surface ref."""
        repo = _copy_fixture("http-api", tmp_path)
        result = _run_impact(repo, "--surface", "POST /widgets")
        assert result.returncode == 0

        payload = json.loads(result.stdout)
        normalize_impact_json(payload)
        json_text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
        assert_golden("impact_surface_stdout.json", json_text)

    def test_impact_by_description(self, tmp_path: Path) -> None:
        """Impact check matching by description tokens."""
        repo = _copy_fixture("ts-cli", tmp_path)
        result = _run_impact(
            repo, "--description", "scaffold project initialization"
        )
        assert result.returncode == 0

        payload = json.loads(result.stdout)
        normalize_impact_json(payload)
        json_text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
        assert_golden("impact_description_stdout.json", json_text)

    def test_impact_no_match(self, tmp_path: Path) -> None:
        """Impact check with no matching stories."""
        repo = _copy_fixture("ts-cli", tmp_path)
        result = _run_impact(repo, "--file", "nonexistent/file.xyz")
        assert result.returncode == 0

        payload = json.loads(result.stdout)
        normalize_impact_json(payload)
        json_text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
        assert_golden("impact_no_match_stdout.json", json_text)

    def test_impact_http_api_with_locked(self, tmp_path: Path) -> None:
        """Impact check on http-api shows locked_sections in match output."""
        repo = _copy_fixture("http-api", tmp_path)
        result = _run_impact(
            repo, "--description", "widget creation endpoint POST"
        )
        assert result.returncode == 0

        payload = json.loads(result.stdout)
        normalize_impact_json(payload)
        if payload["matches"]:
            assert "locked_sections" in payload["matches"][0]
        json_text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
        assert_golden("impact_locked_sections_stdout.json", json_text)


# ---------------------------------------------------------------------------
# Locked-section refusal golden tests
# ---------------------------------------------------------------------------


class TestLockedSectionRefusalGolden:
    """Golden tests for edit_section locked-section refusal output."""

    def test_locked_section_refusal(self, tmp_path: Path) -> None:
        """Editing a locked section produces exit 3 with refusal message."""
        repo = _copy_fixture("http-api", tmp_path)
        result = _run_edit_section(
            repo,
            "--story", "create-widget-endpoint",
            "--section", "Intent",
            "--content", "Rewritten intent text",
        )
        assert result.returncode == 3
        assert_golden("edit_locked_section_stderr.txt", result.stderr)

    def test_immutable_story_refusal(self, tmp_path: Path) -> None:
        """Editing an immutable story produces exit 3 with refusal message."""
        repo = _copy_fixture("http-api", tmp_path)
        stories_dir = repo / "docs" / "stories"
        (stories_dir / "immutable-test.md").write_text(textwrap.dedent("""\
            ---
            schema_version: 1
            title: Immutable test story
            slug: immutable-test
            status: active
            authority: accepted
            change_resistance: immutable
            locked_sections: []
            ---

            # Immutable test story

            ## Intent
            This story is immutable.

            ## Story
            No edits allowed.

            ## Expected Behavior
            Any edit attempt is refused.

            ## Boundaries
            N/A.

            ## Auditable Claims
            - Cannot be edited.

            ## Evidence
            ### Tests
            - tests/test_widgets.py
        """), encoding="utf-8")

        result = _run_edit_section(
            repo,
            "--story", "immutable-test",
            "--section", "Story",
            "--content", "Attempted edit",
        )
        assert result.returncode == 3
        assert_golden("edit_immutable_story_stderr.txt", result.stderr)

    def test_inline_lock_refusal(self, tmp_path: Path) -> None:
        """Editing a section with inline lock markers produces exit 3."""
        repo = _copy_fixture("http-api", tmp_path)
        stories_dir = repo / "docs" / "stories"
        (stories_dir / "inline-locked.md").write_text(textwrap.dedent("""\
            ---
            schema_version: 1
            title: Inline locked story
            slug: inline-locked
            status: active
            authority: accepted
            change_resistance: medium
            locked_sections: []
            ---

            # Inline locked story

            ## Intent
            Test inline lock markers.

            ## Story
            <!-- lock:begin -->
            This content is locked inline.
            <!-- lock:end -->

            ## Expected Behavior
            Edit attempts on locked inline content are refused.

            ## Boundaries
            N/A.

            ## Auditable Claims
            - Inline locks are enforced.

            ## Evidence
            ### Tests
            - tests/test_widgets.py
        """), encoding="utf-8")

        result = _run_edit_section(
            repo,
            "--story", "inline-locked",
            "--section", "Story",
            "--content", "Attempted edit of locked content",
        )
        assert result.returncode == 3
        assert_golden("edit_inline_lock_stderr.txt", result.stderr)

    def test_claim_reduction_refusal(self, tmp_path: Path) -> None:
        """Reducing claim count without flag produces exit 3."""
        repo = _copy_fixture("http-api", tmp_path)
        result = _run_edit_section(
            repo,
            "--story", "create-widget-endpoint",
            "--section", "Auditable Claims",
            "--content", "- Single remaining claim.",
        )
        assert result.returncode == 3
        assert_golden("edit_claim_reduction_stderr.txt", result.stderr)

    def test_resistance_increase_confirmation(self, tmp_path: Path) -> None:
        """Increasing change_resistance without confirmation produces exit 4."""
        repo = _copy_fixture("ts-cli", tmp_path)
        result = _run_edit_section(
            repo,
            "--story", "cli-init-command",
            "--section", "change_resistance",
            "--content", "high",
        )
        assert result.returncode == 4
        assert_golden("edit_resistance_increase_stderr.txt", result.stderr)


# ---------------------------------------------------------------------------
# Finding shape consistency tests
# ---------------------------------------------------------------------------


class TestFindingShapeConsistency:
    """Verify that finding dicts have a consistent shape across report types."""

    def test_audit_finding_shape(self, tmp_path: Path) -> None:
        """Every audit finding has the required keys."""
        repo = _copy_fixture("drift", tmp_path)
        result = _run_audit(repo)
        assert result.returncode == 0
        report = (repo / "audit-report.md").read_text(encoding="utf-8")
        finding_blocks = re.findall(r"^## Finding \d+: .+$", report, re.MULTILINE)
        assert len(finding_blocks) > 0, "expected at least one finding"
        for block_header in finding_blocks:
            start = report.index(block_header)
            rest = report[start + len(block_header):]
            next_finding = re.search(r"^## Finding \d+:", rest, re.MULTILINE)
            block_text = rest[:next_finding.start()] if next_finding else rest
            assert "- kind:" in block_text, f"missing 'kind' in {block_header}"
            assert "- story_slug:" in block_text, f"missing 'story_slug' in {block_header}"
            assert "- severity:" in block_text, f"missing 'severity' in {block_header}"
            assert "- suggested_action:" in block_text, f"missing 'suggested_action' in {block_header}"

    def test_coverage_finding_shape(self, tmp_path: Path) -> None:
        """Every coverage finding has the required keys."""
        repo = _copy_fixture("ts-cli", tmp_path)
        result = _run_coverage(repo)
        assert result.returncode == 0
        report = (repo / "coverage-report.md").read_text(encoding="utf-8")
        finding_blocks = re.findall(r"^## Finding \d+: .+$", report, re.MULTILINE)
        assert len(finding_blocks) > 0, "expected at least one finding"
        for block_header in finding_blocks:
            start = report.index(block_header)
            rest = report[start + len(block_header):]
            next_finding = re.search(r"^## Finding \d+:", rest, re.MULTILINE)
            block_text = rest[:next_finding.start()] if next_finding else rest
            assert "- kind:" in block_text, f"missing 'kind' in {block_header}"
            assert "- story_slug:" in block_text, f"missing 'story_slug' in {block_header}"
            assert "- severity:" in block_text, f"missing 'severity' in {block_header}"
            assert "- suggested_action:" in block_text, f"missing 'suggested_action' in {block_header}"

    def test_impact_match_shape(self, tmp_path: Path) -> None:
        """Every impact-check match has the required keys."""
        repo = _copy_fixture("http-api", tmp_path)
        result = _run_impact(
            repo, "--description", "widget creation endpoint"
        )
        assert result.returncode == 0
        payload = json.loads(result.stdout)
        required_keys = {
            "slug", "title", "status", "authority",
            "change_resistance", "locked_sections",
            "intent_excerpt", "match_reasons", "flags",
        }
        for match in payload["matches"]:
            missing = required_keys - set(match.keys())
            assert not missing, f"missing keys in impact match: {missing}"
