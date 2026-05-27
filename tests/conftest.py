"""Pytest configuration for the storystore test suite.

Fixture mini-repos under tests/fixtures/ ship sample Python files (sources
and tests) that simulate downstream projects. They are data, not part of
this repo's test suite, so exclude them from pytest collection.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

import pytest

from .llm_provider import ZolemProvider

collect_ignore_glob = ["fixtures/*"]


def _json_request(
    url: str,
    payload: dict[str, object],
    *,
    timeout: int,
) -> dict[str, object]:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="PUT",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def require_zolem(provider: ZolemProvider) -> ZolemProvider:
    """Return a configured Zolem provider or skip with an actionable reason."""
    available, reason = provider.is_available()
    if not available:
        pytest.skip(f"Zolem unavailable: {reason}")
    if not provider.listener_base_url:
        pytest.skip("Zolem unavailable: listener was not configured")
    return provider


@pytest.fixture(scope="session")
def zolem_provider() -> ZolemProvider:
    """Session-scoped Zolem fixture provider for LLM-backed tests."""
    admin_url = ZolemProvider.DEFAULT_ADMIN_URL
    provider = ZolemProvider(admin_url=admin_url)
    available, reason = provider.is_available()
    if not available:
        pytest.skip(f"Zolem unavailable: {reason}")

    try:
        _json_request(
            f"{admin_url}/_zolem/profiles/storystore-fixture",
            {"backend": "fixture"},
            timeout=5,
        )
        listener = _json_request(
            f"{admin_url}/_zolem/listeners/storystore",
            {
                "addr": "127.0.0.1:0",
                "provider": "anthropic",
                "profile": "storystore-fixture",
            },
            timeout=5,
        )
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
        pytest.skip(f"Zolem fixture listener unavailable: {exc}")

    addr = listener.get("addr")
    if not isinstance(addr, str) or not addr:
        pytest.skip(f"Zolem listener response missing addr: {listener!r}")
    return ZolemProvider(admin_url=admin_url, listener_base_url=f"http://{addr}")
