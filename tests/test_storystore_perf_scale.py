"""Performance and scale regression tests for storystore tooling.

Tests verify that audit, coverage, inventory, and impact-check handle large
synthetic repos within documented time thresholds.

Normal-size perf tests run by default. Larger scale tests run only when
STORYSTORE_SCALE_TESTS=1 is set in the environment.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
AUDIT_SCRIPT = REPO_ROOT / "shared" / "audit.py"
COVERAGE_SCRIPT = REPO_ROOT / "shared" / "coverage.py"
IMPACT_SCRIPT = REPO_ROOT / "shared" / "impact_check.py"
INV_PATH = REPO_ROOT / "shared" / "inventory.py"
LIB_PATH = REPO_ROOT / "shared" / "storystore_lib.py"

# Thresholds (milliseconds) — keep in sync with spec DEFAULT_PERF_WARN_MS.
AUDIT_THRESHOLD_MS = 5000
COVERAGE_THRESHOLD_MS = 5000
IMPACT_CHECK_THRESHOLD_MS = 500

# Scale-test gate
SCALE_TESTS = os.environ.get("STORYSTORE_SCALE_TESTS", "") == "1"
requires_scale = pytest.mark.skipif(
    not SCALE_TESTS, reason="STORYSTORE_SCALE_TESTS=1 not set"
)


# --------------------------------------------------------------------------- #
# Module loading
# --------------------------------------------------------------------------- #


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


inv = _load_module("storystore_inventory_perf", INV_PATH)


# --------------------------------------------------------------------------- #
# Synthetic repo generation
# --------------------------------------------------------------------------- #

STORY_TEMPLATE = """\
---
title: {title}
slug: {slug}
status: active
authority: accepted
change_resistance: medium
---

# {title}

## Intent
Users can use the {slug} feature.

## Story
User opens the app and uses {slug}.

## Expected Behavior
The {slug} feature works as documented.

## Boundaries
Out of scope for this story.

## Auditable Claims
- The {slug} feature exists.

## Evidence
### Tests
- `tests/{slug}.spec.ts`
### Surface
- `src/{slug}.ts`
### Docs
"""


def _generate_synthetic_repo(
    tmp_path: Path,
    *,
    num_stories: int = 50,
    num_source_files: int = 100,
    num_test_files: int = 50,
    add_skip_dirs: bool = False,
    skip_dir_file_count: int = 200,
) -> Path:
    """Generate a synthetic repo with many stories and source files."""
    repo = tmp_path / "repo"
    stories_dir = repo / "docs" / "stories"
    stories_dir.mkdir(parents=True)
    src_dir = repo / "src"
    src_dir.mkdir(parents=True)
    tests_dir = repo / "tests"
    tests_dir.mkdir(parents=True)

    # Write stories
    for i in range(num_stories):
        slug = f"feature-{i:04d}"
        title = f"Feature {i:04d}"
        story_path = stories_dir / f"{slug}.md"
        story_path.write_text(
            STORY_TEMPLATE.format(title=title, slug=slug),
            encoding="utf-8",
        )

    # Write source files
    for i in range(num_source_files):
        slug = f"feature-{i:04d}"
        src_file = src_dir / f"{slug}.ts"
        src_file.write_text(
            f'export function {slug.replace("-", "_")}() {{ return "{slug}"; }}\n',
            encoding="utf-8",
        )

    # Write test files
    for i in range(num_test_files):
        slug = f"feature-{i:04d}"
        test_file = tests_dir / f"{slug}.spec.ts"
        test_file.write_text(
            f'describe("{slug}", () => {{ it("works", () => {{}}); }});\n',
            encoding="utf-8",
        )

    # Write package.json for language detection
    (repo / "package.json").write_text('{"name": "perf-test-repo"}', encoding="utf-8")

    # Optionally add skip dirs with many files (to verify they are skipped)
    if add_skip_dirs:
        for skip_dir_name in ("node_modules", "dist", ".venv"):
            skip_dir = repo / skip_dir_name
            skip_dir.mkdir(parents=True)
            for j in range(skip_dir_file_count):
                (skip_dir / f"file-{j:04d}.ts").write_text(
                    f"// skip dir content {j}\n", encoding="utf-8"
                )

    return repo


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _run_script(script: Path, repo: Path, *extra_args: str) -> tuple[int, str, str, float]:
    """Run a script and return (returncode, stdout, stderr, elapsed_ms)."""
    report_path = repo / "report.md"
    cmd = [
        sys.executable, str(script),
        "--repo-root", str(repo),
        "--report-path", str(report_path),
        *extra_args,
    ]
    start = time.perf_counter()
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    elapsed_ms = (time.perf_counter() - start) * 1000
    return result.returncode, result.stdout, result.stderr, elapsed_ms


def _run_impact_check(repo: Path, *extra_args: str) -> tuple[int, str, str, float]:
    """Run impact_check.py with a file argument."""
    cmd = [
        sys.executable, str(IMPACT_SCRIPT),
        "--repo-root", str(repo),
        *extra_args,
    ]
    start = time.perf_counter()
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    elapsed_ms = (time.perf_counter() - start) * 1000
    return result.returncode, result.stdout, result.stderr, elapsed_ms


# --------------------------------------------------------------------------- #
# Default perf tests (run always, moderate scale: 50 stories, 100 src files)
# --------------------------------------------------------------------------- #


class TestAuditPerf:
    """Audit perf tests with moderate synthetic repos."""

    def test_audit_50_stories_under_threshold(self, tmp_path):
        repo = _generate_synthetic_repo(tmp_path, num_stories=50, num_source_files=100)
        rc, stdout, stderr, elapsed_ms = _run_script(AUDIT_SCRIPT, repo)
        assert rc == 0, f"audit failed: {stderr}"
        assert elapsed_ms < AUDIT_THRESHOLD_MS, (
            f"audit took {elapsed_ms:.0f}ms, threshold is {AUDIT_THRESHOLD_MS}ms"
        )

    def test_audit_emits_performance_json(self, tmp_path):
        repo = _generate_synthetic_repo(tmp_path, num_stories=20, num_source_files=40)
        rc, stdout, stderr, elapsed_ms = _run_script(AUDIT_SCRIPT, repo)
        assert rc == 0, f"audit failed: {stderr}"
        data = json.loads(stdout)
        assert "performance" in data
        assert "duration_ms" in data["performance"]
        assert data["performance"]["stories_scanned"] == 20

    def test_audit_skip_dirs_honored(self, tmp_path):
        """Skip dirs with many files should not significantly slow audit."""
        repo = _generate_synthetic_repo(
            tmp_path,
            num_stories=20,
            num_source_files=40,
            add_skip_dirs=True,
            skip_dir_file_count=500,
        )
        rc, stdout, stderr, elapsed_ms = _run_script(AUDIT_SCRIPT, repo)
        assert rc == 0, f"audit failed: {stderr}"
        # With skip dirs containing 1500 extra files, should still be fast.
        assert elapsed_ms < AUDIT_THRESHOLD_MS, (
            f"audit with skip dirs took {elapsed_ms:.0f}ms, threshold {AUDIT_THRESHOLD_MS}ms"
        )


class TestCoveragePerf:
    """Coverage perf tests with moderate synthetic repos."""

    def test_coverage_50_stories_under_threshold(self, tmp_path):
        repo = _generate_synthetic_repo(tmp_path, num_stories=50, num_source_files=100)
        rc, stdout, stderr, elapsed_ms = _run_script(COVERAGE_SCRIPT, repo)
        assert rc == 0, f"coverage failed: {stderr}"
        assert elapsed_ms < COVERAGE_THRESHOLD_MS, (
            f"coverage took {elapsed_ms:.0f}ms, threshold is {COVERAGE_THRESHOLD_MS}ms"
        )

    def test_coverage_emits_performance_json(self, tmp_path):
        repo = _generate_synthetic_repo(tmp_path, num_stories=20, num_source_files=40)
        rc, stdout, stderr, elapsed_ms = _run_script(COVERAGE_SCRIPT, repo)
        assert rc == 0, f"coverage failed: {stderr}"
        data = json.loads(stdout)
        assert "performance" in data
        assert "duration_ms" in data["performance"]

    def test_coverage_skip_dirs_honored(self, tmp_path):
        """Skip dirs with many files should not significantly slow coverage."""
        repo = _generate_synthetic_repo(
            tmp_path,
            num_stories=20,
            num_source_files=40,
            add_skip_dirs=True,
            skip_dir_file_count=500,
        )
        rc, stdout, stderr, elapsed_ms = _run_script(COVERAGE_SCRIPT, repo)
        assert rc == 0, f"coverage failed: {stderr}"
        assert elapsed_ms < COVERAGE_THRESHOLD_MS, (
            f"coverage with skip dirs took {elapsed_ms:.0f}ms, threshold {COVERAGE_THRESHOLD_MS}ms"
        )


class TestImpactCheckPerf:
    """Impact check perf tests with moderate synthetic repos."""

    def test_impact_check_50_stories_under_threshold(self, tmp_path):
        repo = _generate_synthetic_repo(tmp_path, num_stories=50, num_source_files=100)
        # Check impact on a file that exists in the evidence of story feature-0005
        target_file = repo / "src" / "feature-0005.ts"
        rc, stdout, stderr, elapsed_ms = _run_impact_check(
            repo, "--file", str(target_file)
        )
        assert rc == 0, f"impact_check failed: {stderr}"
        assert elapsed_ms < IMPACT_CHECK_THRESHOLD_MS, (
            f"impact_check took {elapsed_ms:.0f}ms, threshold is {IMPACT_CHECK_THRESHOLD_MS}ms"
        )

    def test_impact_check_description_mode(self, tmp_path):
        repo = _generate_synthetic_repo(tmp_path, num_stories=50, num_source_files=100)
        rc, stdout, stderr, elapsed_ms = _run_impact_check(
            repo, "--description", "change the login flow"
        )
        assert rc == 0, f"impact_check failed: {stderr}"
        assert elapsed_ms < IMPACT_CHECK_THRESHOLD_MS, (
            f"impact_check (description) took {elapsed_ms:.0f}ms, threshold {IMPACT_CHECK_THRESHOLD_MS}ms"
        )


class TestInventoryPerf:
    """Inventory module perf tests."""

    def test_inventory_scan_100_files(self, tmp_path):
        repo = _generate_synthetic_repo(tmp_path, num_stories=20, num_source_files=100)
        start = time.perf_counter()
        result = inv.build_inventory(repo)
        elapsed_ms = (time.perf_counter() - start) * 1000
        assert isinstance(result, dict)
        # Inventory should handle 100 source files well under threshold.
        assert elapsed_ms < AUDIT_THRESHOLD_MS, (
            f"inventory took {elapsed_ms:.0f}ms, threshold {AUDIT_THRESHOLD_MS}ms"
        )

    def test_inventory_skips_default_dirs(self, tmp_path):
        """Verify skip dirs are honored: files in node_modules etc. not inventoried."""
        repo = _generate_synthetic_repo(
            tmp_path,
            num_stories=5,
            num_source_files=20,
            add_skip_dirs=True,
            skip_dir_file_count=200,
        )
        start = time.perf_counter()
        result = inv.build_inventory(repo)
        elapsed_ms = (time.perf_counter() - start) * 1000
        # Should not be significantly slower with skip dirs present.
        assert elapsed_ms < 2000, (
            f"inventory with skip dirs took {elapsed_ms:.0f}ms — skip dirs may not be honored"
        )
        # Verify that files from skip dirs are not in the inventory surfaces.
        surfaces = result.get("surfaces", [])
        for entry in surfaces:
            path_str = entry.get("path", "") if isinstance(entry, dict) else str(entry)
            assert "node_modules" not in path_str
            assert "dist" not in path_str
            assert ".venv" not in path_str


# --------------------------------------------------------------------------- #
# Scale tests (behind STORYSTORE_SCALE_TESTS=1)
# --------------------------------------------------------------------------- #


@requires_scale
class TestAuditScale:
    """Large-scale audit tests: hundreds of stories."""

    def test_audit_200_stories(self, tmp_path):
        repo = _generate_synthetic_repo(
            tmp_path, num_stories=200, num_source_files=400, num_test_files=200
        )
        rc, stdout, stderr, elapsed_ms = _run_script(AUDIT_SCRIPT, repo)
        assert rc == 0, f"audit failed: {stderr}"
        data = json.loads(stdout)
        assert data["performance"]["stories_scanned"] == 200
        assert elapsed_ms < AUDIT_THRESHOLD_MS * 3, (
            f"audit at scale took {elapsed_ms:.0f}ms, "
            f"threshold is {AUDIT_THRESHOLD_MS * 3}ms (3x default)"
        )

    def test_audit_500_stories(self, tmp_path):
        repo = _generate_synthetic_repo(
            tmp_path, num_stories=500, num_source_files=800, num_test_files=500
        )
        rc, stdout, stderr, elapsed_ms = _run_script(AUDIT_SCRIPT, repo)
        assert rc == 0, f"audit failed: {stderr}"
        data = json.loads(stdout)
        assert data["performance"]["stories_scanned"] == 500
        # 500 stories is extreme; allow 10x threshold.
        assert elapsed_ms < AUDIT_THRESHOLD_MS * 10, (
            f"audit at 500 stories took {elapsed_ms:.0f}ms, "
            f"threshold is {AUDIT_THRESHOLD_MS * 10}ms (10x default)"
        )

    def test_audit_skip_dirs_at_scale(self, tmp_path):
        """With 200 stories + heavy skip dirs, audit still completes on time."""
        repo = _generate_synthetic_repo(
            tmp_path,
            num_stories=200,
            num_source_files=400,
            add_skip_dirs=True,
            skip_dir_file_count=2000,
        )
        rc, stdout, stderr, elapsed_ms = _run_script(AUDIT_SCRIPT, repo)
        assert rc == 0, f"audit failed: {stderr}"
        # Skip dirs with 6000 total files shouldn't affect timing much.
        assert elapsed_ms < AUDIT_THRESHOLD_MS * 4, (
            f"audit with heavy skip dirs took {elapsed_ms:.0f}ms"
        )


@requires_scale
class TestCoverageScale:
    """Large-scale coverage tests."""

    def test_coverage_200_stories(self, tmp_path):
        repo = _generate_synthetic_repo(
            tmp_path, num_stories=200, num_source_files=400, num_test_files=200
        )
        rc, stdout, stderr, elapsed_ms = _run_script(COVERAGE_SCRIPT, repo)
        assert rc == 0, f"coverage failed: {stderr}"
        data = json.loads(stdout)
        assert "performance" in data
        assert elapsed_ms < COVERAGE_THRESHOLD_MS * 3, (
            f"coverage at scale took {elapsed_ms:.0f}ms, "
            f"threshold is {COVERAGE_THRESHOLD_MS * 3}ms (3x default)"
        )

    def test_coverage_500_stories(self, tmp_path):
        repo = _generate_synthetic_repo(
            tmp_path, num_stories=500, num_source_files=800, num_test_files=500
        )
        rc, stdout, stderr, elapsed_ms = _run_script(COVERAGE_SCRIPT, repo)
        assert rc == 0, f"coverage failed: {stderr}"
        assert elapsed_ms < COVERAGE_THRESHOLD_MS * 10, (
            f"coverage at 500 stories took {elapsed_ms:.0f}ms"
        )


@requires_scale
class TestImpactCheckScale:
    """Large-scale impact check tests."""

    def test_impact_check_200_stories(self, tmp_path):
        repo = _generate_synthetic_repo(
            tmp_path, num_stories=200, num_source_files=400, num_test_files=200
        )
        target_file = repo / "src" / "feature-0100.ts"
        rc, stdout, stderr, elapsed_ms = _run_impact_check(
            repo, "--file", str(target_file)
        )
        assert rc == 0, f"impact_check failed: {stderr}"
        # Allow 3x for scale.
        assert elapsed_ms < IMPACT_CHECK_THRESHOLD_MS * 3, (
            f"impact_check at scale took {elapsed_ms:.0f}ms"
        )

    def test_impact_check_500_stories_multiple_files(self, tmp_path):
        repo = _generate_synthetic_repo(
            tmp_path, num_stories=500, num_source_files=800, num_test_files=500
        )
        files = [str(repo / "src" / f"feature-{i:04d}.ts") for i in range(0, 50, 10)]
        args = []
        for f in files:
            args.extend(["--file", f])
        rc, stdout, stderr, elapsed_ms = _run_impact_check(repo, *args)
        assert rc == 0, f"impact_check failed: {stderr}"
        assert elapsed_ms < IMPACT_CHECK_THRESHOLD_MS * 10, (
            f"impact_check with 5 files at 500 stories took {elapsed_ms:.0f}ms"
        )


@requires_scale
class TestInventoryScale:
    """Large-scale inventory tests."""

    def test_inventory_scan_800_files(self, tmp_path):
        repo = _generate_synthetic_repo(
            tmp_path, num_stories=200, num_source_files=800, num_test_files=400
        )
        start = time.perf_counter()
        result = inv.build_inventory(repo)
        elapsed_ms = (time.perf_counter() - start) * 1000
        assert isinstance(result, dict)
        assert elapsed_ms < AUDIT_THRESHOLD_MS * 2, (
            f"inventory at scale took {elapsed_ms:.0f}ms"
        )

    def test_inventory_skip_dirs_at_scale(self, tmp_path):
        """Verify skip dirs are still honored with large skip dir content."""
        repo = _generate_synthetic_repo(
            tmp_path,
            num_stories=100,
            num_source_files=400,
            add_skip_dirs=True,
            skip_dir_file_count=3000,
        )
        start = time.perf_counter()
        result = inv.build_inventory(repo)
        elapsed_ms = (time.perf_counter() - start) * 1000
        assert elapsed_ms < AUDIT_THRESHOLD_MS * 2, (
            f"inventory with heavy skip dirs took {elapsed_ms:.0f}ms"
        )
