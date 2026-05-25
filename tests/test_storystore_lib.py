"""Tests for shared/storystore_lib.py frontmatter and story parsing."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
LIB_PATH = REPO_ROOT / "shared" / "storystore_lib.py"


def _load_lib():
    spec = importlib.util.spec_from_file_location("storystore_lib", LIB_PATH)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules["storystore_lib"] = mod
    spec.loader.exec_module(mod)
    return mod


lib = _load_lib()


VALID_FM = """\
---
schema_version: 1
title: Example Story
slug: example-story
status: active
authority: accepted
change_resistance: medium
tests_applicable: true
locked_sections:
  - Intent
last_audited: 2026-05-22
---

# Example Story

## Intent
One clear sentence.

## Story
Some prose.

## Expected Behavior
Visible things.

## Boundaries
Things excluded.

## Auditable Claims
- A claim.

## Evidence
### Tests
- `tests/test_example.py`
### Surface
- `cli: example`
### Docs
- `README.md`
"""


def test_parse_valid_frontmatter():
    data = lib.parse_frontmatter(VALID_FM)
    assert data["title"] == "Example Story"
    assert data["slug"] == "example-story"
    assert data["status"] == "active"
    assert data["authority"] == "accepted"
    assert data["change_resistance"] == "medium"
    assert data["tests_applicable"] is True
    assert data["locked_sections"] == ["Intent"]
    assert data["last_audited"] == "2026-05-22"
    assert data["schema_version"] == 1


def test_missing_frontmatter_is_parse_error():
    with pytest.raises(lib.ParseError) as exc:
        lib.parse_frontmatter("# No frontmatter here\n")
    assert exc.value.exit_code == 2


def test_unterminated_frontmatter():
    with pytest.raises(lib.ParseError, match="unterminated"):
        lib.parse_frontmatter("---\ntitle: x\n")


def test_unknown_key_is_error():
    bad = VALID_FM.replace("schema_version: 1\n", "schema_version: 1\nmystery_key: hi\n")
    with pytest.raises(lib.ParseError, match="unknown frontmatter key"):
        lib.parse_frontmatter(bad)


def test_invalid_status_enum():
    bad = VALID_FM.replace("status: active", "status: superseded")
    with pytest.raises(lib.ParseError, match="invalid status"):
        lib.parse_frontmatter(bad)


def test_invalid_authority_enum():
    bad = VALID_FM.replace("authority: accepted", "authority: proposed")
    with pytest.raises(lib.ParseError, match="invalid authority"):
        lib.parse_frontmatter(bad)


def test_invalid_change_resistance_enum():
    bad = VALID_FM.replace("change_resistance: medium", "change_resistance: extreme")
    with pytest.raises(lib.ParseError, match="invalid change_resistance"):
        lib.parse_frontmatter(bad)


def test_missing_required_field():
    bad = VALID_FM.replace("title: Example Story\n", "")
    with pytest.raises(lib.ParseError, match="missing required"):
        lib.parse_frontmatter(bad)


def test_observed_high_violates_validity_matrix():
    bad = VALID_FM.replace("authority: accepted", "authority: observed").replace(
        "change_resistance: medium", "change_resistance: high"
    )
    with pytest.raises(lib.ParseError) as exc:
        lib.parse_frontmatter(bad)
    assert exc.value.exit_code == 3
    assert "validity matrix" in str(exc.value)


def test_observed_immutable_violates_validity_matrix():
    bad = VALID_FM.replace("authority: accepted", "authority: observed").replace(
        "change_resistance: medium", "change_resistance: immutable"
    )
    with pytest.raises(lib.ParseError) as exc:
        lib.parse_frontmatter(bad)
    assert exc.value.exit_code == 3


def test_locked_sections_flow_list():
    fm = VALID_FM.replace("locked_sections:\n  - Intent\n", "locked_sections: [Intent, Boundaries]\n")
    data = lib.parse_frontmatter(fm)
    assert data["locked_sections"] == ["Intent", "Boundaries"]


def test_nested_mapping_rejected():
    bad = VALID_FM.replace(
        "tests_applicable: true\n",
        "tests_applicable:\n  nested: bad\n",
    )
    with pytest.raises(lib.ParseError, match="indentation|list item"):
        lib.parse_frontmatter(bad)


def test_anchor_rejected():
    bad = VALID_FM.replace("title: Example Story", "title: &anchor x")
    with pytest.raises(lib.ParseError, match="anchors"):
        lib.parse_frontmatter(bad)


def test_quoted_string_rejected():
    bad = VALID_FM.replace("title: Example Story", 'title: "Example"')
    with pytest.raises(lib.ParseError, match="quoted"):
        lib.parse_frontmatter(bad)


def test_duplicate_key_rejected():
    bad = VALID_FM.replace("title: Example Story\n", "title: Example Story\ntitle: Other\n")
    with pytest.raises(lib.ParseError, match="duplicate"):
        lib.parse_frontmatter(bad)


def test_error_reports_line_column():
    bad = VALID_FM.replace(
        "tests_applicable: true\n",
        "tests_applicable:\n  nested: bad\n",
    )
    with pytest.raises(lib.ParseError) as exc:
        lib.parse_frontmatter(bad)
    assert exc.value.line > 0
    assert exc.value.column > 0


def test_parse_story_sections_and_evidence(tmp_path):
    p = tmp_path / "example-story.md"
    p.write_text(VALID_FM, encoding="utf-8")
    story = lib.parse_story(p)
    assert story.slug == "example-story"
    assert "Intent" in story.sections
    assert "One clear sentence" in story.sections["Intent"]
    assert story.evidence_tests == ["tests/test_example.py"]
    assert story.evidence_surface == ["cli: example"]
    assert story.evidence_docs == ["README.md"]
    assert story.evidence_flag == []


def test_parse_story_evidence_flag_subsection(tmp_path):
    """Flag evidence subsection is parsed into evidence_flag."""
    text = VALID_FM.rstrip() + "\n### Flag\n- `experimental_collab`\n- `dark_mode`\n"
    p = tmp_path / "with-flags.md"
    p.write_text(text, encoding="utf-8")
    story = lib.parse_story(p)
    assert story.evidence_flag == ["experimental_collab", "dark_mode"]


def test_parse_story_missing_intent_is_error(tmp_path):
    text = VALID_FM.replace("## Intent\nOne clear sentence.\n\n", "")
    p = tmp_path / "no-intent.md"
    p.write_text(text, encoding="utf-8")
    with pytest.raises(lib.ParseError, match="Intent"):
        lib.parse_story(p)


def test_tests_applicable_false_with_test_evidence_conflicts(tmp_path):
    text = VALID_FM.replace("tests_applicable: true", "tests_applicable: false")
    p = tmp_path / "conflict.md"
    p.write_text(text, encoding="utf-8")
    with pytest.raises(lib.ParseError, match="tests_applicable"):
        lib.parse_story(p)


def test_tests_applicable_false_without_tests_is_ok(tmp_path):
    text = VALID_FM.replace("tests_applicable: true", "tests_applicable: false").replace(
        "### Tests\n- `tests/test_example.py`\n", ""
    )
    p = tmp_path / "ok.md"
    p.write_text(text, encoding="utf-8")
    story = lib.parse_story(p)
    assert story.tests_applicable is False
    assert story.evidence_tests == []


def test_locked_block_parsed(tmp_path):
    body = VALID_FM + "\n<!-- lock:begin -->\nimportant prose\n<!-- lock:end -->\n"
    p = tmp_path / "with-lock.md"
    p.write_text(body, encoding="utf-8")
    story = lib.parse_story(p)
    assert len(story.locked_blocks) == 1
    assert "important prose" in story.locked_blocks[0].text


def test_unterminated_locked_block(tmp_path):
    body = VALID_FM + "\n<!-- lock:begin -->\nimportant prose\n"
    p = tmp_path / "bad-lock.md"
    p.write_text(body, encoding="utf-8")
    with pytest.raises(lib.ParseError, match="unterminated locked block"):
        lib.parse_story(p)


def test_stray_lock_end(tmp_path):
    body = VALID_FM + "\nstuff\n<!-- lock:end -->\n"
    p = tmp_path / "stray.md"
    p.write_text(body, encoding="utf-8")
    with pytest.raises(lib.ParseError, match="stray"):
        lib.parse_story(p)


def test_load_stories_skips_index_and_readme(tmp_path):
    (tmp_path / "README.md").write_text("readme\n", encoding="utf-8")
    (tmp_path / "INDEX.md").write_text("index\n", encoding="utf-8")
    (tmp_path / "drift-todo.md").write_text("drift\n", encoding="utf-8")
    (tmp_path / "notes.txt").write_text("ignored\n", encoding="utf-8")
    (tmp_path / "a-story.md").write_text(VALID_FM, encoding="utf-8")
    (tmp_path / "b-story.md").write_text(
        VALID_FM.replace("slug: example-story", "slug: b-story").replace(
            "title: Example Story", "title: B Story"
        ),
        encoding="utf-8",
    )
    stories = lib.load_stories(tmp_path)
    slugs = sorted(s.slug for s in stories)
    assert slugs == ["b-story", "example-story"]


def test_load_stories_propagates_parse_error_with_path(tmp_path):
    (tmp_path / "broken.md").write_text("not a story\n", encoding="utf-8")
    with pytest.raises(lib.ParseError) as exc:
        lib.load_stories(tmp_path)
    assert exc.value.path is not None
    assert exc.value.path.name == "broken.md"


def test_load_stories_missing_dir(tmp_path):
    with pytest.raises(lib.ParseError, match="does not exist"):
        lib.load_stories(tmp_path / "nope")


def test_default_optional_fields(tmp_path):
    minimal = """\
---
title: Minimal
slug: minimal
status: draft
authority: accepted
change_resistance: low
---

# Minimal

## Intent
One thing.
"""
    p = tmp_path / "minimal.md"
    p.write_text(minimal, encoding="utf-8")
    story = lib.parse_story(p)
    assert story.schema_version == 1
    assert story.tests_applicable is True
    assert story.locked_sections == []
    assert story.last_audited is None


def test_parse_evidence_copy_section(tmp_path):
    """Copy evidence items are parsed from ### Copy subsection."""
    text = """\
---
title: Copy Test
slug: copy-test
status: active
authority: accepted
change_resistance: medium
---

# Copy Test

## Intent
Test copy evidence parsing.

## Evidence
### Copy
- `en/messages.json#errors.permission_denied`
- `locales/fr.yaml#nav.home`
"""
    p = tmp_path / "copy-test.md"
    p.write_text(text, encoding="utf-8")
    story = lib.parse_story(p)
    assert story.evidence_copy == [
        "en/messages.json#errors.permission_denied",
        "locales/fr.yaml#nav.home",
    ]
