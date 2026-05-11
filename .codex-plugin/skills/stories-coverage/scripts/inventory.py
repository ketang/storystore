"""Storystore inventory: evidence resolution and candidate discovery.

Full implementation ships in storystore-inventory. This stub satisfies
imports and packaging materialization for downstream skill development.
"""

from __future__ import annotations

from pathlib import Path


def resolve_evidence(repo_root: Path, story) -> dict:
    raise NotImplementedError("resolve_evidence: implemented in storystore-inventory")


def build_inventory(repo_root: Path) -> dict:
    raise NotImplementedError("build_inventory: implemented in storystore-inventory")
