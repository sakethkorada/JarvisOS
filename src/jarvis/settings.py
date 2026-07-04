"""Settings loading for JarvisOS.

The settings layer is provider-agnostic. It names model providers, modes, and
future plugin paths without caring whether a provider is local or cloud-backed.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


DEFAULT_CONFIG_PATHS = (
    Path("jarvis.toml"),
    Path("config/jarvis.toml"),
)


@dataclass(frozen=True)
class ModelSettings:
    """Model defaults and named routing modes."""

    default: str | None = None
    modes: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ProviderSettings:
    """Provider-specific settings used below the router layer."""

    ollama_host: str = "http://localhost:11434"
    ollama_models: tuple[str, ...] = ()


@dataclass(frozen=True)
class PluginSettings:
    """Local plugin paths that future loaders can scan."""

    paths: tuple[Path, ...] = ()


@dataclass(frozen=True)
class McpToolSettings:
    """Per-tool policy override for one MCP server tool."""

    name: str
    risk_level: str | None = None
    requires_approval: bool | None = None


@dataclass(frozen=True)
class McpServerSettings:
    """Configuration for one MCP stdio server."""

    name: str
    command: str
    args: tuple[str, ...] = ()
    enabled: bool = True
    risk_level: str = "low"
    requires_approval: bool = False
    tools: tuple[McpToolSettings, ...] = ()


@dataclass(frozen=True)
class McpSettings:
    """Configured MCP servers that can expose tools."""

    servers: tuple[McpServerSettings, ...] = ()


@dataclass(frozen=True)
class MemorySettings:
    """Memory persistence and extraction settings."""

    database_path: Path = Path(".jarvis/memory.sqlite3")
    auto_extract: bool = True
    auto_write: bool = False


@dataclass(frozen=True)
class TaskSettings:
    """Local task persistence settings."""

    database_path: Path = Path(".jarvis/tasks.sqlite3")


@dataclass(frozen=True)
class TraceSettings:
    """Trace persistence settings."""

    database_path: Path = Path(".jarvis/traces.sqlite3")
    enabled: bool = True


@dataclass(frozen=True)
class ApprovalSettings:
    """Approval queue persistence settings."""

    database_path: Path = Path(".jarvis/approvals.sqlite3")


@dataclass(frozen=True)
class PromptSettings:
    """Optional prompt override paths for configurable agent behavior."""

    planner_path: Path | None = None
    synthesis_path: Path | None = None


@dataclass(frozen=True)
class JarvisSettings:
    """Resolved application settings from config files and environment."""

    models: ModelSettings = field(default_factory=ModelSettings)
    providers: ProviderSettings = field(default_factory=ProviderSettings)
    plugins: PluginSettings = field(default_factory=PluginSettings)
    mcp: McpSettings = field(default_factory=McpSettings)
    memory: MemorySettings = field(default_factory=MemorySettings)
    tasks: TaskSettings = field(default_factory=TaskSettings)
    traces: TraceSettings = field(default_factory=TraceSettings)
    approvals: ApprovalSettings = field(default_factory=ApprovalSettings)
    prompts: PromptSettings = field(default_factory=PromptSettings)
    loaded_from: Path | None = None

    def resolve_model(
        self,
        explicit_model: str | None,
        mode: str = "balanced",
    ) -> str | None:
        """Resolve the model name using CLI, mode, then default precedence."""
        if explicit_model:
            return explicit_model
        mode_model = self.models.modes.get(mode)
        if mode_model:
            return mode_model
        return self.models.default


def load_settings(config_path: Path | None = None) -> JarvisSettings:
    """Load settings from a TOML file and apply environment overrides."""
    path = config_path or _first_existing_config_path()
    file_data: dict[str, Any] = {}
    if path is not None:
        with path.open("rb") as config_file:
            file_data = tomllib.load(config_file)

    settings = _settings_from_data(file_data, loaded_from=path)
    return _settings_with_environment(settings)


def _first_existing_config_path() -> Path | None:
    for path in DEFAULT_CONFIG_PATHS:
        if path.exists():
            return path
    return None


def _settings_from_data(
    data: dict[str, Any],
    loaded_from: Path | None,
) -> JarvisSettings:
    model_data = _table(data, "models")
    provider_data = _table(data, "providers")
    ollama_data = _table(provider_data, "ollama")
    plugin_data = _table(data, "plugins")
    mcp_data = _table(data, "mcp")
    memory_data = _table(data, "memory")
    task_data = _table(data, "tasks")
    trace_data = _table(data, "traces")
    approval_data = _table(data, "approvals")
    prompt_data = _table(data, "prompts")

    modes = _string_map(_table(model_data, "modes"))
    default_model = _optional_string(model_data.get("default"))
    ollama_host = _optional_string(ollama_data.get("host")) or "http://localhost:11434"
    ollama_models = tuple(_string_list(ollama_data.get("models")))
    plugin_paths = tuple(
        _resolve_config_path(path, loaded_from)
        for path in _string_list(plugin_data.get("paths"))
    )
    mcp_servers = tuple(_mcp_servers_from_data(mcp_data))
    memory_database_path = _resolve_config_path(
        _optional_string(memory_data.get("database_path")) or ".jarvis/memory.sqlite3",
        loaded_from,
    )
    auto_extract = _optional_bool(memory_data.get("auto_extract"), default=True)
    auto_write = _optional_bool(memory_data.get("auto_write"), default=False)
    task_database_path = _resolve_config_path(
        _optional_string(task_data.get("database_path")) or ".jarvis/tasks.sqlite3",
        loaded_from,
    )
    trace_database_path = _resolve_config_path(
        _optional_string(trace_data.get("database_path")) or ".jarvis/traces.sqlite3",
        loaded_from,
    )
    traces_enabled = _optional_bool(trace_data.get("enabled"), default=True)
    approval_database_path = _resolve_config_path(
        _optional_string(approval_data.get("database_path"))
        or ".jarvis/approvals.sqlite3",
        loaded_from,
    )
    planner_prompt_path = _optional_path(prompt_data.get("planner"), loaded_from)
    synthesis_prompt_path = _optional_path(prompt_data.get("synthesis"), loaded_from)

    return JarvisSettings(
        models=ModelSettings(default=default_model, modes=modes),
        providers=ProviderSettings(
            ollama_host=ollama_host,
            ollama_models=ollama_models,
        ),
        plugins=PluginSettings(paths=plugin_paths),
        mcp=McpSettings(servers=mcp_servers),
        memory=MemorySettings(
            database_path=memory_database_path,
            auto_extract=auto_extract,
            auto_write=auto_write,
        ),
        tasks=TaskSettings(database_path=task_database_path),
        traces=TraceSettings(
            database_path=trace_database_path,
            enabled=traces_enabled,
        ),
        approvals=ApprovalSettings(database_path=approval_database_path),
        prompts=PromptSettings(
            planner_path=planner_prompt_path,
            synthesis_path=synthesis_prompt_path,
        ),
        loaded_from=loaded_from,
    )


def _settings_with_environment(settings: JarvisSettings) -> JarvisSettings:
    default_model = os.getenv("JARVIS_MODEL") or settings.models.default
    modes = dict(settings.models.modes)
    mode = os.getenv("JARVIS_MODEL_MODE")
    mode_model = os.getenv("JARVIS_MODE_MODEL")
    if mode and mode_model:
        modes[mode] = mode_model

    ollama_host = os.getenv("OLLAMA_HOST") or settings.providers.ollama_host
    ollama_models = list(settings.providers.ollama_models)
    ollama_model = os.getenv("OLLAMA_MODEL")
    if ollama_model and ollama_model not in ollama_models:
        ollama_models.append(ollama_model)

    return JarvisSettings(
        models=ModelSettings(default=default_model, modes=modes),
        providers=ProviderSettings(
            ollama_host=ollama_host,
            ollama_models=tuple(ollama_models),
        ),
        plugins=settings.plugins,
        mcp=settings.mcp,
        memory=settings.memory,
        tasks=settings.tasks,
        traces=settings.traces,
        approvals=settings.approvals,
        prompts=settings.prompts,
        loaded_from=settings.loaded_from,
    )


def _resolve_config_path(path: str, loaded_from: Path | None) -> Path:
    """Resolve relative config paths against the config file directory."""
    plugin_path = Path(path)
    if plugin_path.is_absolute() or loaded_from is None:
        return plugin_path
    return loaded_from.parent / plugin_path


def _optional_path(value: Any, loaded_from: Path | None) -> Path | None:
    path = _optional_string(value)
    if path is None:
        return None
    return _resolve_config_path(path, loaded_from)


def _mcp_servers_from_data(data: dict[str, Any]) -> list[McpServerSettings]:
    servers: list[McpServerSettings] = []
    raw_servers = data.get("servers", [])
    if not isinstance(raw_servers, list):
        raise ValueError("Expected [mcp].servers to be a list of tables.")
    for item in raw_servers:
        if not isinstance(item, dict):
            raise ValueError("Expected each MCP server to be a table.")
        name = _optional_string(item.get("name"))
        command = _optional_string(item.get("command"))
        if name is None or command is None:
            raise ValueError("MCP servers require name and command.")
        args = tuple(_string_list(item.get("args")))
        enabled = _optional_bool(item.get("enabled"), default=True)
        risk_level = _optional_string(item.get("risk_level")) or "low"
        requires_approval = _optional_bool(
            item.get("requires_approval"),
            default=False,
        )
        tool_overrides = tuple(_mcp_tools_from_data(item.get("tools", [])))
        servers.append(
            McpServerSettings(
                name=name,
                command=command,
                args=args,
                enabled=enabled,
                risk_level=risk_level,
                requires_approval=requires_approval,
                tools=tool_overrides,
            )
        )
    return servers


def _mcp_tools_from_data(value: Any) -> list[McpToolSettings]:
    tools: list[McpToolSettings] = []
    if value is None:
        return tools
    if not isinstance(value, list):
        raise ValueError("Expected MCP server tools to be a list of tables.")
    for item in value:
        if not isinstance(item, dict):
            raise ValueError("Expected each MCP server tool override to be a table.")
        name = _optional_string(item.get("name"))
        if name is None:
            raise ValueError("MCP server tool overrides require name.")
        requires_approval = (
            _optional_bool(item["requires_approval"], default=False)
            if "requires_approval" in item
            else None
        )
        tools.append(
            McpToolSettings(
                name=name,
                risk_level=_optional_string(item.get("risk_level")),
                requires_approval=requires_approval,
            )
        )
    return tools


def _table(data: dict[str, Any], key: str) -> dict[str, Any]:
    value = data.get(key, {})
    if not isinstance(value, dict):
        raise ValueError(f"Expected [{key}] to be a TOML table.")
    return value


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"Expected string value, got {type(value).__name__}.")
    value = value.strip()
    return value or None


def _optional_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if not isinstance(value, bool):
        raise ValueError(f"Expected boolean value, got {type(value).__name__}.")
    return value


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"Expected list of strings, got {type(value).__name__}.")
    items: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise ValueError("Expected list of strings.")
        stripped = item.strip()
        if stripped:
            items.append(stripped)
    return items


def _string_map(data: dict[str, Any]) -> dict[str, str]:
    values: dict[str, str] = {}
    for key, value in data.items():
        if not isinstance(value, str):
            raise ValueError(f"Expected string value for key {key}.")
        stripped = value.strip()
        if stripped:
            values[key] = stripped
    return values
