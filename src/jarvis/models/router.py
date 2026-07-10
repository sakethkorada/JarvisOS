"""Model provider abstraction for the first runtime slice."""

from __future__ import annotations

import json
import os
from abc import ABC, abstractmethod
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen

from jarvis.contracts import ModelRequest, ModelResponse
from jarvis.errors import JarvisError, ModelProviderError
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
        system_prompt = request.system_prompt or (
            "You are a concise planning assistant inside JarvisOS."
        )
        user_content = "\n\n".join([*request.messages, request.goal])
        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": system_prompt,
                },
                {"role": "user", "content": user_content},
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


class GeminiProvider(ModelProvider):
    """Model provider that calls Gemini through the Interactions API."""

    def __init__(
        self,
        model: str,
        api_key_env: str = "GEMINI_API_KEY",
        timeout_seconds: float = 60.0,
    ) -> None:
        self.model = model
        self.api_key_env = api_key_env
        self.timeout_seconds = timeout_seconds
        self.name = f"gemini/{model}"

    def generate(self, request: ModelRequest) -> ModelResponse:
        """Send a stateless text interaction and normalize its output."""
        api_key = os.getenv(self.api_key_env)
        if not api_key:
            raise ModelProviderError(
                f"Gemini API key is missing. Set {self.api_key_env}.",
                component=self.name,
            )

        client_factory = _load_gemini_client()
        user_content = "\n\n".join([*request.messages, request.goal])
        try:
            client = client_factory(
                api_key=api_key,
                http_options={
                    "client_args": {"timeout": self.timeout_seconds},
                },
            )
            interaction = client.interactions.create(
                model=self.model,
                input=user_content,
                system_instruction=request.system_prompt,
                store=False,
            )
        except ModelProviderError:
            raise
        except Exception as exc:
            raise ModelProviderError(
                f"Gemini request failed: {exc}",
                component=self.name,
            ) from exc

        content = str(getattr(interaction, "output_text", "") or "").strip()
        if not content:
            raise ModelProviderError(
                "Gemini returned an empty response.",
                component=self.name,
            )
        return ModelResponse(model_name=self.name, text=content)


class ModelRouter:
    """Routes model requests to a selected provider."""

    def __init__(
        self,
        providers: dict[str, ModelProvider],
        default_provider_name: str | None = None,
        mode_routes: dict[str, str] | None = None,
        role_routes: dict[str, str] | None = None,
    ) -> None:
        self._providers = providers
        self._default_provider_name = default_provider_name or "fake-local"
        if self._default_provider_name not in self._providers:
            self._default_provider_name = "fake-local"
        self._mode_routes = dict(mode_routes or {})
        self._role_routes = dict(role_routes or {})

    def list(self) -> list[str]:
        """Return available provider names in stable display order."""
        return sorted(self._providers)

    def run(
        self,
        request: ModelRequest,
        provider_name: str | None = None,
        role: str | None = None,
    ) -> ModelResponse:
        """Run a request against an explicit or default provider."""
        selected_provider_name = self.resolve_provider_name(
            explicit_provider_name=provider_name,
            mode=request.mode,
            role=role,
        )
        try:
            provider = self._providers[selected_provider_name]
        except KeyError as exc:
            available = ", ".join(self.list())
            raise KeyError(
                f"Unknown model provider: {selected_provider_name}. "
                f"Available providers: {available}"
            ) from exc
        try:
            return provider.generate(request)
        except JarvisError:
            raise
        except Exception as exc:
            raise ModelProviderError(
                str(exc),
                component=selected_provider_name,
            ) from exc

    def resolve_provider_name(
        self,
        explicit_provider_name: str | None = None,
        mode: str = "balanced",
        role: str | None = None,
    ) -> str:
        """Resolve a concrete provider from explicit, role, mode, or default."""
        if explicit_provider_name:
            return explicit_provider_name
        if role:
            role_provider = self._role_routes.get(role)
            if role_provider:
                return role_provider
        mode_provider = self._mode_routes.get(mode)
        if mode_provider:
            return mode_provider
        return self._default_provider_name


def default_model_router(settings: JarvisSettings | None = None) -> ModelRouter:
    """Build the default router from settings and local provider discovery."""
    settings = settings or load_settings()
    providers: dict[str, ModelProvider] = {}
    fake_provider = FakeModelProvider()
    providers[fake_provider.name] = fake_provider

    host = settings.providers.ollama_host
    discovered_ollama: list[str] = []
    for model_name in _discover_ollama_models(host):
        provider = OllamaProvider(model=model_name, host=host)
        providers[provider.name] = provider
        discovered_ollama.append(provider.name)

    for configured_model in settings.providers.ollama_models:
        provider = OllamaProvider(model=configured_model, host=host)
        providers[provider.name] = provider
        if provider.name not in discovered_ollama:
            discovered_ollama.append(provider.name)

    for model_name in _configured_gemini_models(settings):
        provider = GeminiProvider(
            model=model_name,
            api_key_env=settings.providers.gemini.api_key_env,
            timeout_seconds=settings.providers.gemini.timeout_seconds,
        )
        providers[provider.name] = provider

    default_provider = settings.models.default
    if default_provider is None and discovered_ollama:
        default_provider = discovered_ollama[0]
    return ModelRouter(
        providers,
        default_provider_name=default_provider,
        mode_routes=settings.models.modes,
        role_routes=settings.models.roles,
    )


def _post_json(
    url: str,
    payload: dict[str, Any],
    timeout_seconds: float,
) -> dict[str, Any]:
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


def _configured_gemini_models(settings: JarvisSettings) -> list[str]:
    """Find explicit and routed Gemini models without inspecting environment keys."""
    models = set(settings.providers.gemini.models)
    routes = [
        settings.models.default,
        *settings.models.modes.values(),
        *settings.models.roles.values(),
    ]
    for route in routes:
        provider_name, model_name = _split_provider_name(route)
        if provider_name == "gemini" and model_name:
            models.add(model_name)
    return sorted(models)


def _split_provider_name(provider_name: str | None) -> tuple[str | None, str | None]:
    """Split a configured provider name once so model IDs may contain slashes."""
    if not provider_name or "/" not in provider_name:
        return None, None
    provider, model = provider_name.split("/", 1)
    provider = provider.strip()
    model = model.strip()
    return (provider or None, model or None)


def _load_gemini_client() -> Any:
    """Load the optional Gemini SDK only when a Gemini model is used."""
    try:
        from google import genai
    except ImportError as exc:
        raise ModelProviderError(
            'Gemini support is not installed. Run: uv pip install -e ".[gemini]"',
            component="gemini",
        ) from exc
    return genai.Client
