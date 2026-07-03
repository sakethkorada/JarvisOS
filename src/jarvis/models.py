"""Model provider abstraction for the first runtime slice."""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen

from jarvis.contracts import ModelRequest, ModelResponse
from jarvis.settings import JarvisSettings, load_settings


class ModelProvider(ABC):
    """Base interface for local and cloud model providers."""

    name: str

    @abstractmethod
    def generate(self, request: ModelRequest) -> ModelResponse:
        """Generate a model response."""


class FakeModelProvider(ModelProvider):
    """Deterministic provider used for tests and offline smoke checks."""

    name = "fake-local"

    def generate(self, request: ModelRequest) -> ModelResponse:
        return ModelResponse(
            model_name=self.name,
            text=f"Created a simple plan for: {request.goal}",
        )


class OllamaProvider(ModelProvider):
    """Model provider that calls an Ollama local HTTP server."""

    def __init__(
        self,
        model: str,
        host: str = "http://localhost:11434",
        timeout_seconds: float = 60,
    ) -> None:
        self.model = model
        self.host = host.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.name = f"ollama/{model}"

    def generate(self, request: ModelRequest) -> ModelResponse:
        """Send a chat request to Ollama and return normalized text."""
        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": "You are a concise planning assistant inside JarvisOS.",
                },
                {"role": "user", "content": request.goal},
            ],
            "stream": False,
        }
        response = _post_json(
            f"{self.host}/api/chat",
            payload,
            timeout_seconds=self.timeout_seconds,
        )
        message = response.get("message", {})
        content = str(message.get("content", "")).strip()
        if not content:
            content = f"Ollama model {self.model} returned an empty response."
        return ModelResponse(model_name=self.name, text=content)


class ModelRouter:
    """Routes model requests to a selected provider."""

    def __init__(
        self,
        providers: dict[str, ModelProvider],
        default_provider_name: str | None = None,
    ) -> None:
        self._providers = providers
        self._default_provider_name = default_provider_name or "fake-local"
        if self._default_provider_name not in self._providers:
            self._default_provider_name = "fake-local"

    def list(self) -> list[str]:
        """Return available provider names in stable display order."""
        return sorted(self._providers)

    def run(
        self,
        request: ModelRequest,
        provider_name: str | None = None,
    ) -> ModelResponse:
        """Run a request against an explicit or default provider."""
        selected_provider_name = provider_name or self._default_provider_name
        try:
            provider = self._providers[selected_provider_name]
        except KeyError as exc:
            available = ", ".join(self.list())
            raise KeyError(
                f"Unknown model provider: {selected_provider_name}. "
                f"Available providers: {available}"
            ) from exc
        return provider.generate(request)


def default_model_router(settings: JarvisSettings | None = None) -> ModelRouter:
    """Build the default router from settings and local provider discovery."""
    settings = settings or load_settings()
    providers: dict[str, ModelProvider] = {}
    fake_provider = FakeModelProvider()
    providers[fake_provider.name] = fake_provider

    host = settings.providers.ollama_host
    for model_name in _discover_ollama_models(host):
        provider = OllamaProvider(model=model_name, host=host)
        providers[provider.name] = provider

    for configured_model in settings.providers.ollama_models:
        provider = OllamaProvider(model=configured_model, host=host)
        providers[provider.name] = provider

    return ModelRouter(providers, default_provider_name=settings.models.default)


def _post_json(url: str, payload: dict[str, Any], timeout_seconds: float) -> dict[str, Any]:
    """Post a JSON payload using the standard library HTTP client."""
    data = json.dumps(payload).encode("utf-8")
    request = Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=timeout_seconds) as response:
        return json.loads(response.read().decode("utf-8"))


def _discover_ollama_models(host: str) -> list[str]:
    """Return model names from a running Ollama server, if available."""
    try:
        with urlopen(f"{host.rstrip('/')}/api/tags", timeout=0.5) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, URLError, json.JSONDecodeError):
        return []

    models = payload.get("models", [])
    names = [str(model.get("name", "")).strip() for model in models]
    return sorted(name for name in names if name)
