"""LLM provider abstraction for opt-in LLM-backed tests.

Provides a pluggable interface for LLM backends. Tests skip cleanly when
the configured provider is unavailable.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class LLMResponse:
    """Structured response from an LLM provider."""

    text: str
    model: str
    provider: str
    raw: dict[str, Any] = field(default_factory=dict)


class LLMProvider(ABC):
    """Abstract base for LLM-backed test providers."""

    @abstractmethod
    def name(self) -> str:
        """Return the provider name (e.g. 'ollama')."""

    @abstractmethod
    def is_available(self) -> tuple[bool, str]:
        """Check whether the provider is reachable.

        Returns (available, reason). If not available, reason explains why
        so pytest.skip() can use it.
        """

    @abstractmethod
    def generate(self, prompt: str, *, system: str = "") -> LLMResponse:
        """Send a prompt and return the response text."""

    @abstractmethod
    def list_models(self) -> list[str]:
        """Return model names available on this provider."""


class OllamaProvider(LLMProvider):
    """Ollama provider using the local HTTP API at localhost:11434."""

    DEFAULT_BASE_URL = "http://localhost:11434"
    # Prefer small models suitable for structured output in tests.
    PREFERRED_MODELS = ("llama3.2:1b", "llama3.2", "llama3.1", "llama3", "gemma2", "mistral", "phi3")
    TIMEOUT_SECONDS = 120

    def __init__(self, base_url: str | None = None, model: str | None = None):
        self.base_url = (base_url or self.DEFAULT_BASE_URL).rstrip("/")
        self._explicit_model = model
        self._resolved_model: str | None = None

    def name(self) -> str:
        return "ollama"

    def is_available(self) -> tuple[bool, str]:
        # Check 1: ollama binary on PATH
        if not shutil.which("ollama"):
            return False, "ollama binary not found on PATH"
        # Check 2: ollama list succeeds (daemon running)
        try:
            result = subprocess.run(
                ["ollama", "list"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            if result.returncode != 0:
                return False, f"'ollama list' failed (exit {result.returncode}): {result.stderr.strip()}"
        except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
            return False, f"'ollama list' failed: {exc}"
        # Check 3: at least one model available
        models = self.list_models()
        if not models:
            return False, "no models available in ollama"
        return True, "ollama available"

    def list_models(self) -> list[str]:
        try:
            req = urllib.request.Request(f"{self.base_url}/api/tags")
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode())
            return [m["name"] for m in data.get("models", [])]
        except (urllib.error.URLError, OSError, json.JSONDecodeError, KeyError):
            return []

    def _resolve_model(self) -> str:
        if self._resolved_model:
            return self._resolved_model
        if self._explicit_model:
            self._resolved_model = self._explicit_model
            return self._resolved_model
        models = self.list_models()
        # Pick the first preferred model that's available
        for preferred in self.PREFERRED_MODELS:
            for available in models:
                # Match base name (e.g. "llama3.2:1b" matches "llama3.2:1b",
                # "llama3.2" matches "llama3.2:latest")
                if available == preferred or available.startswith(preferred + ":") or preferred.startswith(available.split(":")[0]):
                    self._resolved_model = available
                    return self._resolved_model
        # Fall back to first available model
        if models:
            self._resolved_model = models[0]
            return self._resolved_model
        raise RuntimeError("no models available in ollama")

    def generate(self, prompt: str, *, system: str = "") -> LLMResponse:
        model = self._resolve_model()
        body = {
            "model": model,
            "prompt": prompt,
            "stream": False,
        }
        if system:
            body["system"] = system
        req = urllib.request.Request(
            f"{self.base_url}/api/generate",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=self.TIMEOUT_SECONDS) as resp:
            data = json.loads(resp.read().decode())
        return LLMResponse(
            text=data.get("response", ""),
            model=model,
            provider="ollama",
            raw=data,
        )


class ZolemProvider(LLMProvider):
    """Zolem fixture provider using an Anthropic-compatible listener."""

    DEFAULT_ADMIN_URL = "http://127.0.0.1:18090"
    FIXTURE_MODEL = "zolem-fixture"
    ADMIN_TIMEOUT_SECONDS = 2
    GENERATE_TIMEOUT_SECONDS = 10

    def __init__(
        self,
        admin_url: str | None = None,
        listener_base_url: str = "",
    ):
        self.admin_url = (admin_url or self.DEFAULT_ADMIN_URL).rstrip("/")
        self.listener_base_url = listener_base_url.rstrip("/")

    def name(self) -> str:
        return "zolem"

    def is_available(self) -> tuple[bool, str]:
        try:
            req = urllib.request.Request(f"{self.admin_url}/_zolem/profiles")
            with urllib.request.urlopen(req, timeout=self.ADMIN_TIMEOUT_SECONDS) as resp:
                if 200 <= resp.status < 300:
                    return True, "zolem admin available"
                return False, f"zolem admin returned HTTP {resp.status}"
        except (urllib.error.URLError, OSError) as exc:
            return False, str(exc)

    def generate(self, prompt: str, *, system: str = "") -> LLMResponse:
        if not self.listener_base_url:
            raise RuntimeError("zolem listener base URL is not configured")
        body: dict[str, Any] = {
            "model": self.FIXTURE_MODEL,
            "max_tokens": 1024,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system:
            body["system"] = system
        req = urllib.request.Request(
            f"{self.listener_base_url}/v1/messages",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json", "x-api-key": "test-key"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=self.GENERATE_TIMEOUT_SECONDS) as resp:
            data = json.loads(resp.read().decode())
        return LLMResponse(
            text=data["content"][0]["text"],
            model=self.FIXTURE_MODEL,
            provider="zolem",
            raw=data,
        )

    def list_models(self) -> list[str]:
        return [self.FIXTURE_MODEL]


def get_provider(name: str = "ollama", **kwargs: Any) -> LLMProvider:
    """Factory for LLM providers."""
    if name == "ollama":
        return OllamaProvider(**kwargs)
    if name == "zolem":
        return ZolemProvider(**kwargs)
    raise ValueError(f"unknown LLM provider: {name!r}; supported: ollama, zolem")


def require_ollama() -> OllamaProvider:
    """Return an OllamaProvider or call pytest.skip() if unavailable.

    Intended for use in test fixtures.
    """
    import pytest

    provider = OllamaProvider()
    available, reason = provider.is_available()
    if not available:
        pytest.skip(f"Ollama unavailable: {reason}")
    return provider
