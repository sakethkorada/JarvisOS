"""Runtime factory for the default local JarvisOS setup."""

from __future__ import annotations

from jarvis.approvals import ApprovalStore
from jarvis.agents import default_agent_registry
from jarvis.memory import MemoryExtractor, MemoryStore
from jarvis.models import default_model_router
from jarvis.orchestrator import Orchestrator
from jarvis.plugins import load_plugins
from jarvis.policies import PolicyEngine
from jarvis.prompts import PromptLibrary
from jarvis.settings import JarvisSettings, load_settings
from jarvis.tasks import TaskStore
from jarvis.tools import ToolRegistry, default_tool_registry
from jarvis.traces import TraceStore


def create_default_orchestrator(settings: JarvisSettings | None = None) -> Orchestrator:
    """Create the default local runtime with built-in registries."""
    settings = settings or load_settings()
    memory_store = MemoryStore(settings.memory.database_path)
    task_store = TaskStore(settings.tasks.database_path)
    approval_store = ApprovalStore(settings.approvals.database_path)
    tools = default_tool_registry(memory_store, task_store)
    load_plugins(settings.plugins.paths, tools)
    prompts = PromptLibrary(
        planner_prompt_path=settings.prompts.planner_path,
        synthesis_prompt_path=settings.prompts.synthesis_path,
    )
    return Orchestrator(
        agents=default_agent_registry(),
        tools=tools,
        models=default_model_router(settings),
        policies=PolicyEngine(),
        planner_prompt=prompts.planner_prompt(),
        synthesis_prompt=prompts.synthesis_prompt(),
        approval_store=approval_store,
        memory_extractor=MemoryExtractor() if settings.memory.auto_extract else None,
        auto_write_memory=settings.memory.auto_write,
    )


def create_default_tool_registry(
    settings: JarvisSettings | None = None,
) -> ToolRegistry:
    """Create the built-in tool registry plus configured local plugins."""
    settings = settings or load_settings()
    tools = default_tool_registry(
        MemoryStore(settings.memory.database_path),
        TaskStore(settings.tasks.database_path),
    )
    load_plugins(settings.plugins.paths, tools)
    return tools


def create_default_trace_store(settings: JarvisSettings | None = None) -> TraceStore:
    """Create the default trace store from settings."""
    settings = settings or load_settings()
    return TraceStore(settings.traces.database_path)


def create_default_approval_store(
    settings: JarvisSettings | None = None,
) -> ApprovalStore:
    """Create the default approval store from settings."""
    settings = settings or load_settings()
    return ApprovalStore(settings.approvals.database_path)


def create_default_task_store(settings: JarvisSettings | None = None) -> TaskStore:
    """Create the default local task store from settings."""
    settings = settings or load_settings()
    return TaskStore(settings.tasks.database_path)
