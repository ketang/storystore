"""Pytest configuration for the storystore test suite.

Fixture mini-repos under tests/fixtures/ ship sample Python files (sources
and tests) that simulate downstream projects. They are data, not part of
this repo's test suite, so exclude them from pytest collection.
"""

from __future__ import annotations

collect_ignore_glob = ["fixtures/*"]
