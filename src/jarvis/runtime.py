"""Runtime factory for the default local JarvisOS setup."""

from __future__ import annotations

from jarvis.agents import default_agent_registry
from jarvis.memory import MemoryExtractor, MemoryStore
from jarvis.models import default_model_router
from jarvis.orchestrator import Orchestrator
from jarvis.plugins import load_plugins
from jarvis.policies import PolicyEngine
from jarvis.settings import JarvisSettings, load_settings
from jarvis.tools import ToolRegistry, default_tool_registry


def create_default_orchestrator(settings: JarvisSettings | None = None) -> Orchestrator:
    """Create the default local runtime with built-in registries."""
    settings = settings or load_settings()
    memory_store = MemoryStore(settings.memory.database_path)
    tools = default_tool_registry(memory_store)
    load_plugins(settings.plugins.paths, tools)
    return Orchestrator(
        agents=default_agent_registry(),
        tools=tools,
        models=default_model_router(settings),
        policies=PolicyEngine(),
        memory_extractor=MemoryExtractor() if settings.memory.auto_extract else None,
        auto_write_memory=settings.memory.auto_write,
    )


def create_default_tool_registry(settings: JarvisSettings | None = None) -> ToolRegistry:
    """Create the built-in tool registry plus configured local plugins."""
    settings = settings or load_settings()
    tools = default_tool_registry(MemoryStore(settings.memory.database_path))
    load_plugins(settings.plugins.paths, tools)
    return tools
