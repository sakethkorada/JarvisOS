"""Runtime factory for the default local JarvisOS setup."""

from __future__ import annotations

from jarvis.agents import default_agent_registry
from jarvis.integrations.mcp import load_mcp_tools
from jarvis.integrations.oauth import OAuthManager
from jarvis.integrations.plugins import load_plugins
from jarvis.models import default_model_router
from jarvis.orchestration.orchestrator import Orchestrator
from jarvis.policies import PolicyEngine
from jarvis.prompts import PromptLibrary
from jarvis.settings import JarvisSettings, load_settings
from jarvis.storage.auth import AuthStore
from jarvis.storage.approvals import ApprovalStore
from jarvis.storage.memory import MemoryExtractor, MemoryStore
from jarvis.storage.tasks import TaskStore
from jarvis.storage.traces import TraceStore
from jarvis.tools import ToolRegistry, default_tool_registry


def create_default_orchestrator(settings: JarvisSettings | None = None) -> Orchestrator:
    """Create the default local runtime with built-in registries."""
    settings = settings or load_settings()
    memory_store = (
        MemoryStore(settings.memory.database_path)
        if settings.memory.enabled
        else None
    )
    task_store = TaskStore(settings.tasks.database_path)
    approval_store = ApprovalStore(settings.approvals.database_path)
    tools = default_tool_registry(memory_store, task_store)
    load_plugins(settings.plugins.paths, tools)
    auth_store = _mcp_auth_store(settings)
    load_mcp_tools(
        settings.mcp.servers,
        tools,
        auth_store,
        _oauth_manager(settings, auth_store),
    )
    prompts = PromptLibrary(
        planner_prompt_path=settings.prompts.planner_path,
        synthesis_prompt_path=settings.prompts.synthesis_path,
        tool_use_prompt_path=settings.prompts.tool_use_path,
    )
    return Orchestrator(
        agents=default_agent_registry(),
        tools=tools,
        models=default_model_router(settings),
        policies=PolicyEngine(),
        planner_prompt=prompts.planner_prompt(),
        synthesis_prompt=prompts.synthesis_prompt(),
        tool_use_prompt=prompts.tool_use_prompt(),
        approval_store=approval_store,
        memory_extractor=(
            MemoryExtractor()
            if settings.memory.enabled and settings.memory.auto_extract
            else None
        ),
        auto_write_memory=settings.memory.auto_write,
    )


def create_default_tool_registry(
    settings: JarvisSettings | None = None,
) -> ToolRegistry:
    """Create the built-in tool registry plus configured local plugins."""
    settings = settings or load_settings()
    tools = default_tool_registry(
        (
            MemoryStore(settings.memory.database_path)
            if settings.memory.enabled
            else None
        ),
        TaskStore(settings.tasks.database_path),
    )
    load_plugins(settings.plugins.paths, tools)
    auth_store = _mcp_auth_store(settings)
    load_mcp_tools(
        settings.mcp.servers,
        tools,
        auth_store,
        _oauth_manager(settings, auth_store),
    )
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


def _mcp_auth_store(settings: JarvisSettings) -> AuthStore | None:
    """Create auth storage only when configured MCP servers need it."""
    for server in settings.mcp.servers:
        if server.enabled and server.auth_provider:
            return AuthStore(settings.auth.database_path)
    return None


def _oauth_manager(
    settings: JarvisSettings,
    auth_store: AuthStore | None,
) -> OAuthManager | None:
    """Create an OAuth manager when HTTP MCP servers can use provider auth."""
    if auth_store is None or not settings.auth.oauth_providers:
        return None
    return OAuthManager(settings.auth.oauth_providers, auth_store)
