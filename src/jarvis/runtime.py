"""Runtime factory for the default local JarvisOS setup."""

from __future__ import annotations

from jarvis.agents import default_agent_registry
from jarvis.models import default_model_router
from jarvis.orchestrator import Orchestrator
from jarvis.policies import PolicyEngine
from jarvis.settings import JarvisSettings, load_settings
from jarvis.tools import default_tool_registry


def create_default_orchestrator(settings: JarvisSettings | None = None) -> Orchestrator:
    """Create the default local runtime with built-in registries."""
    settings = settings or load_settings()
    return Orchestrator(
        agents=default_agent_registry(),
        tools=default_tool_registry(),
        models=default_model_router(settings),
        policies=PolicyEngine(),
    )
