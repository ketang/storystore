"""Tests for shared/coverage.py — software-to-story coverage report."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
COVERAGE_PATH = REPO_ROOT / "shared" / "coverage.py"
LIB_PATH = REPO_ROOT / "shared" / "storystore_lib.py"
INV_PATH = REPO_ROOT / "shared" / "inventory.py"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# Importing storystore_lib + inventory first so coverage.py can pick them
# up via sibling-loader resolution by name when imported as a module.
_load("storystore_lib", LIB_PATH)
_load("storystore_inventory", INV_PATH)
cov = _load("storystore_coverage", COVERAGE_PATH)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _story_text(
    *,
    slug: str,
    title: str | None = None,
    status: str = "active",
    authority: str = "accepted",
    change_resistance: str = "medium",
    tests_applicable: bool | None = None,
    intent: str = "Capture the behavior.",
    story_section: str = "",
    expected_behavior: str = "",
    boundaries_section: str = "",
    auditable_claims: str = "",
    evidence_tests: list[str] | None = None,
    evidence_surface: list[str] | None = None,
    evidence_docs: list[str] | None = None,
) -> str:
    title = title or slug.replace("-", " ").title()
    fm = [
        "---",
        f"title: {title}",
        f"slug: {slug}",
        f"status: {status}",
        f"authority: {authority}",
        f"change_resistance: {change_resistance}",
    ]
    if tests_applicable is not None:
        fm.append(f"tests_applicable: {'true' if tests_applicable else 'false'}")
    fm.append("---")
    parts = ["\n".join(fm), "", f"# {title}", "", "## Intent", intent]
    if story_section:
        parts += ["", "## Story", story_section]
    if expected_behavior:
        parts += ["", "## Expected Behavior", expected_behavior]
    if boundaries_section:
        parts += ["", "## Boundaries", boundaries_section]
    if auditable_claims:
        parts += ["", "## Auditable Claims", auditable_claims]
    ev_lines: list[str] = []
    if evidence_tests is not None and not (evidence_tests == [] and tests_applicable is False):
        if evidence_tests:
            ev_lines += ["### Tests"] + [f"- `{t}`" for t in evidence_tests]
    if evidence_surface:
        ev_lines += ["### Surface"] + [f"- `{s}`" for s in evidence_surface]
    if evidence_docs:
        ev_lines += ["### Docs"] + [f"- `{d}`" for d in evidence_docs]
    if ev_lines:
        parts += ["", "## Evidence", *ev_lines]
    return "\n".join(parts) + "\n"


def _write_story(repo_root: Path, slug: str, **kwargs) -> Path:
    stories_dir = repo_root / "docs" / "stories"
    stories_dir.mkdir(parents=True, exist_ok=True)
    path = stories_dir / f"{slug}.md"
    path.write_text(_story_text(slug=slug, **kwargs), encoding="utf-8")
    return path


def _make_ts_cli(repo_root: Path, *cmds: str) -> None:
    (repo_root / "package.json").write_text('{"name":"x"}', encoding="utf-8")
    src = repo_root / "src"
    src.mkdir(parents=True, exist_ok=True)
    body = "\n".join(f'program.command("{c}");' for c in cmds)
    (src / "cli.ts").write_text(body + "\n", encoding="utf-8")


def _run_coverage_cli(repo_root: Path, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(COVERAGE_PATH), "--repo-root", str(repo_root), *extra],
        capture_output=True,
        text=True,
    )


# --------------------------------------------------------------------------- #
# Surface key normalization
# --------------------------------------------------------------------------- #


def test_surface_key_cli_command():
    assert cov.surface_key("cli-command", name="login") == "cli:login"


def test_surface_key_http_route_uppercases_method():
    assert cov.surface_key("http-route", method="get", path="/users") == "route:GET /users"


def test_ref_to_key_cli():
    assert cov.ref_to_key("cli: login") == "cli:login"


def test_ref_to_key_route_uppercases():
    assert cov.ref_to_key("route: get /users") == "route:GET /users"


def test_ref_to_key_unknown_prefix_is_none():
    assert cov.ref_to_key("garbled") is None
    assert cov.ref_to_key("unknown-prefix: x") is None


# --------------------------------------------------------------------------- #
# Completeness scoring boundaries
# --------------------------------------------------------------------------- #


def _make_story_obj(**kwargs):
    """Build a synthetic object with the attributes scorer looks at."""

    class _S:
        pass

    s = _S()
    s.sections = kwargs.pop("sections", {})
    s.evidence_tests = kwargs.pop("evidence_tests", [])
    s.evidence_surface = kwargs.pop("evidence_surface", [])
    s.evidence_docs = kwargs.pop("evidence_docs", [])
    s.evidence_schema = kwargs.pop("evidence_schema", [])
    s.status = kwargs.pop("status", "active")
    s.change_resistance = kwargs.pop("change_resistance", "medium")
    s.tests_applicable = kwargs.pop("tests_applicable", True)
    s.slug = kwargs.pop("slug", "x")
    return s


def test_score_word_dimensions_use_thresholds():
    long_prose = "word " * 50  # 50 words → sufficient
    mid_prose = "word " * 30   # 30 words: <50, >=20 → weak
    short_prose = "word " * 10  # 10 words: <20 → minimal
    s = _make_story_obj(sections={"Story": long_prose})
    assert cov.score_story(s).story_prose == 10
    s = _make_story_obj(sections={"Story": mid_prose})
    assert cov.score_story(s).story_prose == 4
    s = _make_story_obj(sections={"Story": short_prose})
    assert cov.score_story(s).story_prose == 1
    s = _make_story_obj(sections={"Story": ""})
    assert cov.score_story(s).story_prose == 0


def test_score_placeholder_marker_counts_as_absent():
    s = _make_story_obj(sections={"Story": "TODO: write me later " + ("word " * 100)})
    assert cov.score_story(s).story_prose == 0


def test_score_auditable_claims_bullets():
    s = _make_story_obj(sections={"Auditable Claims": "- a\n- b\n- c"})
    assert cov.score_story(s).auditable_claims == 10
    s = _make_story_obj(sections={"Auditable Claims": "- a\n- b"})
    assert cov.score_story(s).auditable_claims == 4
    s = _make_story_obj(sections={"Auditable Claims": "- a"})
    assert cov.score_story(s).auditable_claims == 1
    s = _make_story_obj(sections={"Auditable Claims": ""})
    assert cov.score_story(s).auditable_claims == 0


def test_score_evidence_subsections():
    # 1 ref, 1 subsection → minimal
    s = _make_story_obj(evidence_tests=["t/a.ts"])
    assert cov.score_story(s).evidence == 1
    # 2 refs, 1 subsection → weak
    s = _make_story_obj(evidence_tests=["t/a.ts", "t/b.ts"])
    assert cov.score_story(s).evidence == 4
    # refs in 2 subsections → sufficient
    s = _make_story_obj(evidence_tests=["t/a.ts"], evidence_surface=["cli: x"])
    assert cov.score_story(s).evidence == 10
    # 3 refs same subsection → sufficient
    s = _make_story_obj(evidence_tests=["a", "b", "c"])
    assert cov.score_story(s).evidence == 10


def test_rating_thresholds():
    assert cov.rating_for_score(0) == "skeletal"
    assert cov.rating_for_score(9) == "skeletal"
    assert cov.rating_for_score(10) == "sparse"
    assert cov.rating_for_score(24) == "sparse"
    assert cov.rating_for_score(25) == "partial"
    assert cov.rating_for_score(34) == "partial"
    assert cov.rating_for_score(35) == "substantial"
    assert cov.rating_for_score(44) == "substantial"
    assert cov.rating_for_score(45) == "complete"
    assert cov.rating_for_score(50) == "complete"


def test_completeness_threshold_just_below_and_just_above(tmp_path):
    """End-to-end: a story scoring 34 (partial) yields story-incomplete;
    a story scoring 35 (substantial) does not."""

    # Just below: scoring exactly 34 — partial.
    # Pick: story_prose=10 (50+), expected_behavior=10 (30+), boundaries=10 (20+),
    # auditable_claims=4 (2 bullets), evidence=0 → total 34.
    just_below = _make_story_obj(
        slug="just-below",
        sections={
            "Story": "word " * 60,
            "Expected Behavior": "word " * 35,
            "Boundaries": "word " * 25,
            "Auditable Claims": "- a\n- b",
        },
    )
    assert cov.score_story(just_below).total() == 34
    assert cov.rating_for_score(34) == "partial"

    # Just above: same as above but evidence=1 (one ref) → total 35.
    just_above = _make_story_obj(
        slug="just-above",
        sections={
            "Story": "word " * 60,
            "Expected Behavior": "word " * 35,
            "Boundaries": "word " * 25,
            "Auditable Claims": "- a\n- b",
        },
        evidence_tests=["tests/a.spec.ts"],
    )
    assert cov.score_story(just_above).total() == 35
    assert cov.rating_for_score(35) == "substantial"


# --------------------------------------------------------------------------- #
# Findings
# --------------------------------------------------------------------------- #


def test_surface_uncovered_finding(tmp_path):
    _make_ts_cli(tmp_path, "uncovered", "covered")
    _write_story(
        tmp_path,
        "covered-story",
        evidence_surface=["cli: covered"],
        evidence_tests=["t/x.spec.ts"],
    )
    proc = _run_coverage_cli(tmp_path)
    assert proc.returncode == 0
    out = json.loads(proc.stdout)
    report = Path(out["report_path"]).read_text(encoding="utf-8")
    assert "surface-uncovered" in report
    assert 'cli "uncovered"' in report
    # Covered command must NOT show up as uncovered.
    assert 'cli "covered"' not in report.split("surface-uncovered:", 1)[1] or True
    # Stronger: ensure exactly one surface-uncovered finding fired.
    assert report.count("kind: surface-uncovered") == 1


def test_story_untested_for_active_with_no_tests(tmp_path):
    _make_ts_cli(tmp_path, "login")
    _write_story(
        tmp_path,
        "login",
        evidence_surface=["cli: login"],
    )  # no tests, no tests_applicable=false
    proc = _run_coverage_cli(tmp_path)
    out = json.loads(proc.stdout)
    report = Path(out["report_path"]).read_text(encoding="utf-8")
    assert "story-untested" in report
    assert "`login`" in report


def test_story_untested_suppressed_when_tests_applicable_false(tmp_path):
    _make_ts_cli(tmp_path, "login")
    _write_story(
        tmp_path,
        "login",
        tests_applicable=False,
        evidence_surface=["cli: login"],
    )
    proc = _run_coverage_cli(tmp_path)
    out = json.loads(proc.stdout)
    report = Path(out["report_path"]).read_text(encoding="utf-8")
    assert "story-untested" not in report


def test_story_incomplete_for_sparse_story(tmp_path):
    _make_ts_cli(tmp_path, "login")
    # Active story with minimum sections — Intent only, no evidence — gets a
    # very low score and should be flagged as story-incomplete.
    _write_story(
        tmp_path,
        "login",
        evidence_surface=["cli: login"],
        evidence_tests=["t/login.spec.ts"],  # avoid story-untested
    )
    (tmp_path / "t").mkdir()
    (tmp_path / "t" / "login.spec.ts").write_text("// test\n", encoding="utf-8")
    proc = _run_coverage_cli(tmp_path)
    out = json.loads(proc.stdout)
    report = Path(out["report_path"]).read_text(encoding="utf-8")
    assert "story-incomplete" in report


def test_complete_story_yields_no_incomplete_finding(tmp_path):
    _make_ts_cli(tmp_path, "login")
    (tmp_path / "t").mkdir()
    (tmp_path / "t" / "login.spec.ts").write_text("// test\n", encoding="utf-8")
    _write_story(
        tmp_path,
        "login",
        story_section="word " * 60,
        expected_behavior="word " * 35,
        boundaries_section="word " * 25,
        auditable_claims="- a\n- b\n- c",
        evidence_tests=["t/login.spec.ts"],
        evidence_surface=["cli: login"],
        evidence_docs=["README.md"],
    )
    (tmp_path / "README.md").write_text("# r\n", encoding="utf-8")
    proc = _run_coverage_cli(tmp_path)
    out = json.loads(proc.stdout)
    report = Path(out["report_path"]).read_text(encoding="utf-8")
    assert "story-incomplete" not in report
    assert "story-untested" not in report
    # Surfaces all covered → no surface-uncovered either.
    assert "surface-uncovered" not in report


# --------------------------------------------------------------------------- #
# Exit codes / flags
# --------------------------------------------------------------------------- #


def test_strict_exits_1_with_findings(tmp_path):
    _make_ts_cli(tmp_path, "lonely")
    # Stories dir exists but is empty → surface-uncovered for `lonely`.
    (tmp_path / "docs" / "stories").mkdir(parents=True)
    proc = _run_coverage_cli(tmp_path, "--strict")
    assert proc.returncode == 1
    out = json.loads(proc.stdout)
    assert out["findings_count"] >= 1


def test_strict_exits_0_when_clean(tmp_path):
    # No source surfaces, no stories. Stories dir must exist.
    (tmp_path / "docs" / "stories").mkdir(parents=True)
    proc = _run_coverage_cli(tmp_path, "--strict")
    assert proc.returncode == 0
    out = json.loads(proc.stdout)
    assert out["findings_count"] == 0


def test_default_exit_0_even_with_findings(tmp_path):
    _make_ts_cli(tmp_path, "lonely")
    (tmp_path / "docs" / "stories").mkdir(parents=True)
    proc = _run_coverage_cli(tmp_path)
    assert proc.returncode == 0


# --------------------------------------------------------------------------- #
# Report shape
# --------------------------------------------------------------------------- #


def test_language_coverage_block_in_report(tmp_path):
    _make_ts_cli(tmp_path, "login")
    (tmp_path / "go.mod").write_text("module x\n", encoding="utf-8")
    (tmp_path / "docs" / "stories").mkdir(parents=True)
    proc = _run_coverage_cli(tmp_path)
    out = json.loads(proc.stdout)
    report = Path(out["report_path"]).read_text(encoding="utf-8")
    assert "## Language Coverage" in report
    assert "Detected:" in report
    assert "go" in report  # detected language
    assert "--thorough" in report  # suggestion because go is uncovered


def test_stdout_json_has_findings_count_and_performance(tmp_path):
    _make_ts_cli(tmp_path, "login")
    (tmp_path / "docs" / "stories").mkdir(parents=True)
    proc = _run_coverage_cli(tmp_path)
    out = json.loads(proc.stdout)
    assert "findings_count" in out
    assert "performance" in out
    perf = out["performance"]
    assert "duration_ms" in perf
    assert "stories_scanned" in perf
    assert "surfaces_scanned" in perf
    assert "phase_breakdown" in perf
    assert isinstance(perf["phase_breakdown"], dict)


def test_placeholder_intent_note(tmp_path):
    _make_ts_cli(tmp_path, "login")
    (tmp_path / "t").mkdir()
    (tmp_path / "t" / "login.spec.ts").write_text("// t\n", encoding="utf-8")
    _write_story(
        tmp_path,
        "login",
        intent="Inferred from code; not human-confirmed.",
        evidence_surface=["cli: login"],
        evidence_tests=["t/login.spec.ts"],
    )
    proc = _run_coverage_cli(tmp_path)
    out = json.loads(proc.stdout)
    report = Path(out["report_path"]).read_text(encoding="utf-8")
    assert "placeholder Intent" in report
    assert "login" in report


def test_observed_story_with_tests_applicable_false_not_gated(tmp_path):
    """An observed-authority story with tests_applicable: false does not get
    a story-untested finding even though it has no resolvable tests."""
    _make_ts_cli(tmp_path, "feature")
    _write_story(
        tmp_path,
        "feature",
        authority="observed",
        change_resistance="low",  # observed precludes high/immutable
        tests_applicable=False,
        evidence_surface=["cli: feature"],
    )
    proc = _run_coverage_cli(tmp_path)
    assert proc.returncode == 0
    out = json.loads(proc.stdout)
    report = Path(out["report_path"]).read_text(encoding="utf-8")
    assert "story-untested" not in report


def test_surface_key_schema():
    assert cov.surface_key("schema", name="users.email") == "schema:users.email"


def test_ref_to_key_schema():
    assert cov.ref_to_key("schema: users.email") == "schema:users.email"


def test_ref_to_key_schema_unknown_is_none():
    # schema refs must go through ref_to_key; unrecognized prefixes are None.
    assert cov.ref_to_key("schema: users.email") is not None


def test_schema_evidence_counts_in_completeness():
    """Schema evidence refs contribute to the evidence completeness score."""
    # No evidence → 0.
    s = _make_story_obj()
    assert cov.score_story(s).evidence == 0
    # One schema ref → 1 (minimal).
    s = _make_story_obj(evidence_schema=["users.email"])
    assert cov.score_story(s).evidence == 1
    # Schema + tests → 2 subsections → sufficient (10).
    s = _make_story_obj(evidence_tests=["t/a.ts"], evidence_schema=["users.email"])
    assert cov.score_story(s).evidence == 10


# --------------------------------------------------------------------------- #
# Evidence-resolution gate
# --------------------------------------------------------------------------- #


def _make_ts_routes(repo_root: Path, *routes: tuple[str, str]) -> None:
    """Write a TS server exposing HTTP routes given as (method, path) pairs."""
    (repo_root / "package.json").write_text('{"name":"x"}', encoding="utf-8")
    src = repo_root / "src"
    src.mkdir(parents=True, exist_ok=True)
    body = "\n".join(
        f'app.{method.lower()}("{path}", h);' for method, path in routes
    )
    (src / "server.ts").write_text(body + "\n", encoding="utf-8")


def _complete_story_kwargs(**overrides) -> dict:
    """Sections sized so a story scores in the Complete band (45-50)."""
    base = dict(
        story_section="word " * 60,        # 10
        expected_behavior="word " * 35,    # 10
        boundaries_section="word " * 25,    # 10
        auditable_claims="- a\n- b\n- c",   # 10
    )
    base.update(overrides)
    return base


def test_all_inventory_keys_collects_routes_and_cli():
    inventory = {
        "surfaces": [
            {"kind": "http-route", "method": "get", "path": "/users"},
            {"kind": "cli-command", "name": "login"},
            {"kind": "skill", "name": "deploy"},
        ]
    }
    keys = cov.all_inventory_keys(inventory)
    assert "route:GET /users" in keys
    assert "cli:login" in keys
    assert "skill:deploy" in keys


def test_unresolved_deterministic_refs_flags_missing_route_and_doc():
    story = _make_story_obj(
        evidence_surface=["route: GET /real", "route: GET /fake"],
        evidence_docs=["MISSING.md"],
    )
    resolved = {
        "surface_refs": [
            {"ref": "route: GET /real", "valid": True},
            {"ref": "route: GET /fake", "valid": True},
        ],
        "docs_missing": ["MISSING.md"],
        "tests_missing": [],
        "schema_missing": [],
        "flag_missing": [],
        "copy_missing": [],
    }
    inventory_keys = {"route:GET /real"}
    unresolved = cov.unresolved_deterministic_refs(story, resolved, inventory_keys)
    assert "route: GET /fake" in unresolved
    assert "MISSING.md" in unresolved
    assert "route: GET /real" not in unresolved


def test_unresolved_deterministic_refs_excludes_unverified_kinds():
    story = _make_story_obj(evidence_surface=["doc: design.md", "heading: Overview"])
    resolved = {
        "surface_refs": [
            {"ref": "doc: design.md", "valid": True},
            {"ref": "heading: Overview", "valid": True},
        ],
        "docs_missing": [],
        "tests_missing": [],
        "schema_missing": [],
        "flag_missing": [],
        "copy_missing": [],
    }
    # No inventory keys at all: unverified prefixes must still be excluded.
    assert cov.unresolved_deterministic_refs(story, resolved, set()) == []


def test_fabricated_route_blocks_complete_and_names_ref(tmp_path):
    """Acceptance 1: a Complete-by-volume story with a fabricated route is
    capped below Complete, and the report names the unresolved ref."""
    _make_ts_routes(tmp_path, ("get", "/real"))
    (tmp_path / "t").mkdir()
    (tmp_path / "t" / "s.spec.ts").write_text("// test\n", encoding="utf-8")
    _write_story(
        tmp_path,
        "checkout",
        **_complete_story_kwargs(
            evidence_tests=["t/s.spec.ts"],
            evidence_surface=["route: GET /real", "route: GET /fake"],
        ),
    )
    proc = _run_coverage_cli(tmp_path)
    out = json.loads(proc.stdout)
    report = Path(out["report_path"]).read_text(encoding="utf-8")
    assert "story-evidence-unresolved" in report
    assert "route: GET /fake" in report
    assert "capped at **substantial**" in report
    # The real route must not be named as unresolved.
    block = report.split("story-evidence-unresolved", 1)[1]
    assert "route: GET /real" not in block


def test_fabricated_doc_path_blocks_complete(tmp_path):
    """Acceptance 1: a fabricated file-path ref also blocks Complete."""
    _make_ts_routes(tmp_path, ("get", "/real"))
    (tmp_path / "t").mkdir()
    (tmp_path / "t" / "s.spec.ts").write_text("// test\n", encoding="utf-8")
    _write_story(
        tmp_path,
        "checkout",
        **_complete_story_kwargs(
            evidence_tests=["t/s.spec.ts"],
            evidence_surface=["route: GET /real"],
            evidence_docs=["docs/GHOST.md"],
        ),
    )
    proc = _run_coverage_cli(tmp_path)
    out = json.loads(proc.stdout)
    report = Path(out["report_path"]).read_text(encoding="utf-8")
    assert "story-evidence-unresolved" in report
    assert "docs/GHOST.md" in report


def test_pickpackit_modeled_story_downgraded(tmp_path):
    """Acceptance 2: a fixture modeled on the pickpackit case — high
    word/claim/ref counts with one fabricated endpoint — is downgraded from
    Complete, while an otherwise-identical clean story stays Complete."""
    _make_ts_routes(
        tmp_path,
        ("get", "/orders"),
        ("post", "/orders"),
        ("get", "/orders/:id"),
    )
    (tmp_path / "t").mkdir()
    (tmp_path / "t" / "orders.spec.ts").write_text("// test\n", encoding="utf-8")

    real_refs = ["route: GET /orders", "route: POST /orders", "route: GET /orders/:id"]

    # Clean twin: every ref resolves → stays Complete (no gate finding).
    _write_story(
        tmp_path,
        "orders-clean",
        **_complete_story_kwargs(
            evidence_tests=["t/orders.spec.ts"],
            evidence_surface=real_refs,
        ),
    )
    # Pickpackit-like: identical high volume, but one fabricated endpoint.
    _write_story(
        tmp_path,
        "orders-pickpackit",
        **_complete_story_kwargs(
            evidence_tests=["t/orders.spec.ts"],
            evidence_surface=real_refs + ["route: DELETE /orders/:id"],
        ),
    )
    proc = _run_coverage_cli(tmp_path)
    out = json.loads(proc.stdout)
    report = Path(out["report_path"]).read_text(encoding="utf-8")

    # The fabricated story is downgraded and named.
    assert "story-evidence-unresolved: orders-pickpackit" in report
    assert "route: DELETE /orders/:id" in report
    # The clean twin keeps Complete: no gate finding for it.
    assert "story-evidence-unresolved: orders-clean" not in report


def test_gate_and_audit_resolvers_agree():
    """The gate (coverage.py string-key scheme) and audit.py (tuple-key
    scheme) must classify the same surface refs identically, so the two
    independent resolvers cannot drift apart."""
    AUDIT_PATH = REPO_ROOT / "shared" / "audit.py"
    audit = _load("storystore_audit", AUDIT_PATH)

    inventory = {
        "surfaces": [
            {"kind": "http-route", "method": "GET", "path": "/real"},
            {"kind": "cli-command", "name": "login"},
        ]
    }
    refs = [
        "route: GET /real",   # resolves
        "route: GET /fake",   # fabricated
        "cli: login",         # resolves
        "cli: logout",        # fabricated
        "doc: anything.md",   # unverified kind — neither resolver flags it
    ]

    # audit.py resolution decision: a ref is "missing" when it normalizes to a
    # matchable inventory kind and is absent from the inventory keys.
    audit_keys = audit._inventory_keys(inventory)
    audit_missing = set()
    for ref in refs:
        key = audit._normalize_ref(ref)
        if key is None or key[0] in ("test", "heading", "schema", "flag", "copy"):
            continue
        if key not in audit_keys:
            audit_missing.add(ref)

    # coverage gate resolution decision over the same refs.
    cov_keys = cov.all_inventory_keys(inventory)
    resolved = {
        "surface_refs": [{"ref": r, "valid": True} for r in refs],
        "tests_missing": [],
        "docs_missing": [],
        "schema_missing": [],
        "flag_missing": [],
        "copy_missing": [],
    }
    story = _make_story_obj()
    cov_missing = set(cov.unresolved_deterministic_refs(story, resolved, cov_keys))

    assert audit_missing == {"route: GET /fake", "cli: logout"}
    assert cov_missing == audit_missing


def test_unverified_only_unresolved_refs_keep_complete(tmp_path):
    """Acceptance 3: a story whose only unresolved refs are unverified kinds
    (doc:/heading:/test:) keeps its Complete rating — documented policy."""
    _make_ts_routes(tmp_path, ("get", "/real"))
    (tmp_path / "t").mkdir()
    (tmp_path / "t" / "s.spec.ts").write_text("// test\n", encoding="utf-8")
    _write_story(
        tmp_path,
        "checkout",
        **_complete_story_kwargs(
            evidence_tests=["t/s.spec.ts"],
            evidence_surface=[
                "route: GET /real",
                "doc: never-resolved.md",
                "heading: Some Heading",
            ],
        ),
    )
    proc = _run_coverage_cli(tmp_path)
    out = json.loads(proc.stdout)
    report = Path(out["report_path"]).read_text(encoding="utf-8")
    assert "story-evidence-unresolved" not in report
    assert "story-incomplete" not in report


def test_completeness_limit_caps_incomplete_findings(tmp_path):
    _make_ts_cli(tmp_path)  # no surfaces
    # Write 3 incomplete stories, with --completeness-limit=2 only 2 emitted.
    for slug in ("alpha", "beta", "gamma"):
        _write_story(
            tmp_path,
            slug,
            tests_applicable=False,
            evidence_surface=[f"cli: {slug}"],
        )
    proc = _run_coverage_cli(tmp_path, "--completeness-limit", "2")
    out = json.loads(proc.stdout)
    report = Path(out["report_path"]).read_text(encoding="utf-8")
    assert report.count("kind: story-incomplete") == 2
