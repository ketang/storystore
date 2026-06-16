"""Loud-failure contract for stub / deferred skills (ss-c2v).

A skill shipped in a stub state must FAIL LOUDLY when invoked: emit an
actionable error naming the shipped plugin version and exit non-zero,
rather than printing status prose and exiting clean. A clean exit lets an
invoking agent treat a missing capability as success — the failure mode
that let weeks of evidence drift accumulate in a consumer repo.

These tests:
  * prove the guard (scripts/stub-skill-guard.py) fails loudly and names
    the version;
  * prove the check actually rejects a stub that exits clean (so the test
    has teeth, not just a happy path);
  * prove the committed stub fixture invokes the guard end-to-end and
    produces a visibly non-successful outcome;
  * guard the future: any skill in skills/ that declares ``stub: true``
    must invoke the guard.

Complements TestNoStubLanguageInSkills in test_storystore_plugin_contracts,
which blocks passive stub *prose* from shipping; this file defines what an
intentionally-shipped stub must *do* when invoked.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
GUARD = REPO_ROOT / "scripts" / "stub-skill-guard.py"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "stub-skills"
SKILLS_DIR = REPO_ROOT / "skills"


def _plugin_version() -> str:
    import json

    return json.loads((REPO_ROOT / "plugin-version.json").read_text())["version"]


def _run(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, check=False)


def assert_fails_loudly(proc: subprocess.CompletedProcess, version: str) -> None:
    """Assert a stub invocation produced a loud failure.

    Loud failure = non-zero exit AND output that names the shipped version
    AND tells the agent not to proceed. This is the reusable contract
    check; a stub that exits clean fails the first assertion.
    """
    out = proc.stdout + proc.stderr
    assert proc.returncode != 0, (
        f"stub invocation exited clean (rc=0); a stub must fail loudly.\n"
        f"output:\n{out}"
    )
    assert version in out, f"loud failure must name version {version!r}; got:\n{out}"
    assert re.search(r"not\s+(?:yet\s+)?(?:implemented|shipped|present)", out, re.I), (
        f"loud failure must state the capability is not implemented; got:\n{out}"
    )
    assert re.search(r"do\s+not\s+proceed", out, re.I), (
        f"loud failure must instruct the agent not to proceed; got:\n{out}"
    )


# ── the guard fails loudly ──────────────────────────────────────────


def test_guard_exists_and_is_executable():
    assert GUARD.is_file(), "scripts/stub-skill-guard.py must exist"


def test_guard_fails_loudly_with_resolved_version():
    proc = _run([sys.executable, str(GUARD), "--skill", "stories-foo"], REPO_ROOT)
    assert_fails_loudly(proc, _plugin_version())


def test_guard_exit_code_is_distinct_nonzero():
    """Non-zero, and not the generic 1 — a stub is a config state, not a crash."""
    proc = _run([sys.executable, str(GUARD), "--skill", "stories-foo"], REPO_ROOT)
    assert proc.returncode == 78


def test_guard_version_override_is_honored():
    proc = _run(
        [sys.executable, str(GUARD), "--skill", "stories-foo", "--version", "9.9.9"],
        REPO_ROOT,
    )
    assert_fails_loudly(proc, "9.9.9")


def test_guard_reports_unknown_version_without_going_silent():
    """A missing version file must still fail loudly, never exit clean."""
    proc = _run(
        [
            sys.executable,
            str(GUARD),
            "--skill",
            "stories-foo",
            "--repo-root",
            "/nonexistent-storystore-root",
            "--version",
            "unknown",
        ],
        # cwd outside any plugin checkout so upward search can't find a real one
        Path("/"),
    )
    assert proc.returncode != 0
    assert "unknown" in (proc.stdout + proc.stderr)


def test_guard_requires_skill_name():
    proc = _run([sys.executable, str(GUARD)], REPO_ROOT)
    assert proc.returncode != 0  # argparse usage error


# ── the check has teeth: a clean-exit stub is rejected ──────────────


def test_clean_exit_stub_fixture_actually_exits_clean():
    """Sanity: the anti-pattern fixture really does exit 0."""
    script = FIXTURES / "clean-exit" / "run.sh"
    proc = _run(["sh", str(script)], REPO_ROOT)
    assert proc.returncode == 0


def test_contract_check_rejects_a_clean_exit_stub():
    """The loud-failure check must FAIL against a stub that exits clean.

    This is what proves the test would have caught the original incident:
    a stub printing status prose and exiting 0 must not pass.
    """
    script = FIXTURES / "clean-exit" / "run.sh"
    proc = _run(["sh", str(script)], REPO_ROOT)
    with pytest.raises(AssertionError):
        assert_fails_loudly(proc, _plugin_version())


# ── the committed stub fixture invokes the guard end-to-end ─────────


def _extract_bash_invocation(skill_md: Path) -> str:
    text = skill_md.read_text()
    blocks = re.findall(r"```bash\n(.*?)\n```", text, re.S)
    assert blocks, f"{skill_md} has no ```bash block"
    # the contract says the FIRST instruction is the guard invocation
    return blocks[0].strip()


def test_compliant_stub_fixture_invokes_guard():
    cmd = _extract_bash_invocation(FIXTURES / "compliant" / "SKILL.md")
    assert "stub-skill-guard.py" in cmd, (
        f"compliant stub must invoke the guard; first block was:\n{cmd}"
    )


def test_invoking_compliant_stub_fixture_fails_loudly():
    """Run the fixture's documented invocation; it must be a non-success."""
    cmd = _extract_bash_invocation(FIXTURES / "compliant" / "SKILL.md")
    proc = _run(["sh", "-c", cmd], REPO_ROOT)
    assert_fails_loudly(proc, _plugin_version())


# ── forward guard: any shipped stub skill must invoke the guard ─────


def _is_stub_skill(skill_md: Path) -> bool:
    head = skill_md.read_text()[:2000]
    m = re.search(r"^---\n(.*?)\n---", head, re.S)
    if not m:
        return False
    return re.search(r"^stub:\s*true\s*$", m.group(1), re.M) is not None


def test_any_shipped_stub_skill_invokes_the_guard():
    """Every skills/<name>/SKILL.md declaring stub: true must invoke the
    guard so it fails loudly when invoked. Vacuous today (no shipped
    stubs) but the gate is what stops a future silent stub."""
    offenders = []
    for skill_md in SKILLS_DIR.glob("*/SKILL.md"):
        if _is_stub_skill(skill_md) and "stub-skill-guard.py" not in skill_md.read_text():
            offenders.append(str(skill_md.relative_to(REPO_ROOT)))
    assert not offenders, (
        "stub skills must invoke scripts/stub-skill-guard.py to fail "
        f"loudly: {offenders}"
    )


def test_stub_marker_detection_works():
    """Ensure the stub detector is not silently matching nothing."""
    assert _is_stub_skill(FIXTURES / "compliant" / "SKILL.md")
    assert not _is_stub_skill(REPO_ROOT / "skills" / "stories-init" / "SKILL.md")
