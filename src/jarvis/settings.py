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
GLOBAL_AUTH_PROFILE_ENV = "JARVIS_AUTH_PROFILE"
GLOBAL_AUTH_PROFILE_PATHS = (
    Path(".jarvis/auth.toml"),
    Path("config/auth.toml"),
    Path("jarvis.toml"),
    Path("config/jarvis.toml"),
)
BUILTIN_CAPABILITY_PACKS = frozenset({"google_workspace", "spotify"})


@dataclass(frozen=True)
class ModelSettings:
    """Model defaults and named routing modes."""

    default: str | None = None
    modes: dict[str, str] = field(default_factory=dict)
    roles: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class GeminiProviderSettings:
    """Configuration for Gemini API models registered with the router."""

    models: tuple[str, ...] = ()
    api_key_env: str = "GEMINI_API_KEY"
    timeout_seconds: float = 60.0


@dataclass(frozen=True)
class ProviderSettings:
    """Provider-specific settings used below the router layer."""

    ollama_host: str = "http://localhost:11434"
    ollama_models: tuple[str, ...] = ()
    gemini: GeminiProviderSettings = field(default_factory=GeminiProviderSettings)


@dataclass(frozen=True)
class PluginSettings:
    """Local plugin paths that future loaders can scan."""

    paths: tuple[Path, ...] = ()


@dataclass(frozen=True)
class McpToolSettings:
    """Per-tool policy override for one MCP server tool."""

    name: str
    argument_hints: str | None = None
    risk_level: str | None = None
    requires_approval: bool | None = None


@dataclass(frozen=True)
class McpServerSettings:
    """Configuration for one MCP server."""

    name: str
    command: str | None = None
    args: tuple[str, ...] = ()
    transport: str = "stdio"
    url: str | None = None
    headers: dict[str, str] = field(default_factory=dict)
    auth_provider: str | None = None
    bearer_token_env: str | None = None
    enabled: bool = True
    risk_level: str = "low"
    requires_approval: bool = False
    tools: tuple[McpToolSettings, ...] = ()


@dataclass(frozen=True)
class McpSettings:
    """Configured MCP servers that can expose tools."""

    servers: tuple[McpServerSettings, ...] = ()


@dataclass(frozen=True)
class CapabilitySettings:
    """Built-in capability packs enabled by the user configuration."""

    enabled: dict[str, bool] = field(default_factory=dict)

    def is_enabled(self, name: str) -> bool:
        """Return whether a named built-in capability pack is enabled."""
        return self.enabled.get(name, False)


@dataclass(frozen=True)
class OAuthProviderSettings:
    """OAuth provider metadata for HTTP integrations."""

    name: str
    client_id: str | None = None
    client_secret_env: str | None = None
    authorization_url: str | None = None
    token_url: str | None = None
    tokeninfo_url: str | None = None
    redirect_uri: str | None = None
    scopes: tuple[str, ...] = ()

    def client_secret(self) -> str | None:
        """Resolve the client secret from its configured environment variable."""
        if self.client_secret_env is None:
            return None
        return os.getenv(self.client_secret_env)


@dataclass(frozen=True)
class AuthSettings:
    """Authentication settings and token storage location."""

    database_path: Path = Path(".jarvis/auth.sqlite3")
    oauth_providers: tuple[OAuthProviderSettings, ...] = ()
    loaded_from: Path | None = None


@dataclass(frozen=True)
class MemorySettings:
    """Memory persistence and extraction settings."""

    database_path: Path = Path(".jarvis/memory.sqlite3")
    enabled: bool = True
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
    tool_use_path: Path | None = None


@dataclass(frozen=True)
class JarvisSettings:
    """Resolved application settings from config files and environment."""

    models: ModelSettings = field(default_factory=ModelSettings)
    providers: ProviderSettings = field(default_factory=ProviderSettings)
    plugins: PluginSettings = field(default_factory=PluginSettings)
    capabilities: CapabilitySettings = field(default_factory=CapabilitySettings)
    mcp: McpSettings = field(default_factory=McpSettings)
    auth: AuthSettings = field(default_factory=AuthSettings)
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
        role: str | None = None,
    ) -> str | None:
        """Resolve the model name using CLI, role, mode, then default precedence."""
        if explicit_model:
            return explicit_model
        if role:
            role_model = self.models.roles.get(role)
            if role_model:
                return role_model
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

    auth_settings = _resolved_auth_settings(file_data, loaded_from=path)
    settings = _settings_from_data(
        file_data,
        loaded_from=path,
        auth_settings=auth_settings,
    )
    return _settings_with_environment(settings)


def _first_existing_config_path() -> Path | None:
    for path in DEFAULT_CONFIG_PATHS:
        if path.exists():
            return path
    return None


def _resolved_auth_settings(
    data: dict[str, Any],
    loaded_from: Path | None,
) -> AuthSettings:
    """Resolve local auth settings with a global profile fallback."""
    local_auth_data = _table(data, "auth")
    local_auth = _auth_settings_from_data(local_auth_data, loaded_from)
    global_auth = _global_auth_settings(loaded_from)
    if global_auth is None:
        return local_auth

    has_local_database = "database_path" in local_auth_data
    has_local_providers = "oauth_providers" in local_auth_data
    has_any_local_auth = has_local_database or has_local_providers
    return AuthSettings(
        database_path=(
            local_auth.database_path
            if has_local_database
            else global_auth.database_path
        ),
        oauth_providers=(
            local_auth.oauth_providers
            if has_local_providers
            else global_auth.oauth_providers
        ),
        loaded_from=loaded_from if has_any_local_auth else global_auth.loaded_from,
    )


def _global_auth_settings(explicit_config_path: Path | None) -> AuthSettings | None:
    """Load a shared auth profile when a run config does not define auth."""
    for path in _global_auth_profile_candidates(explicit_config_path):
        if not path.exists():
            continue
        with path.open("rb") as config_file:
            data = tomllib.load(config_file)
        auth_data = _table(data, "auth")
        if not auth_data:
            continue
        return _auth_settings_from_data(auth_data, path)
    return None


def _global_auth_profile_candidates(
    explicit_config_path: Path | None,
) -> tuple[Path, ...]:
    env_path = os.getenv(GLOBAL_AUTH_PROFILE_ENV)
    candidates: list[Path] = [Path(env_path)] if env_path else []
    if explicit_config_path is None or _is_workspace_config(explicit_config_path):
        candidates.extend(GLOBAL_AUTH_PROFILE_PATHS)

    explicit_resolved = _resolved_or_none(explicit_config_path)
    unique: list[Path] = []
    seen: set[Path] = set()
    for candidate in candidates:
        resolved = _resolved_or_none(candidate)
        key = resolved or candidate
        if explicit_resolved is not None and resolved == explicit_resolved:
            continue
        if key in seen:
            continue
        seen.add(key)
        unique.append(candidate)
    return tuple(unique)


def _is_workspace_config(path: Path) -> bool:
    """Return whether an explicit config lives under the current workspace."""
    resolved = _resolved_or_none(path)
    if resolved is None:
        return False
    cwd = Path.cwd().resolve()
    try:
        resolved.relative_to(cwd)
    except ValueError:
        return False
    return True


def _resolved_or_none(path: Path | None) -> Path | None:
    if path is None:
        return None
    try:
        return path.resolve()
    except OSError:
        return None


def _settings_from_data(
    data: dict[str, Any],
    loaded_from: Path | None,
    auth_settings: AuthSettings | None = None,
) -> JarvisSettings:
    model_data = _table(data, "models")
    provider_data = _table(data, "providers")
    ollama_data = _table(provider_data, "ollama")
    gemini_data = _table(provider_data, "gemini")
    plugin_data = _table(data, "plugins")
    capability_data = _table(data, "capabilities")
    mcp_data = _table(data, "mcp")
    memory_data = _table(data, "memory")
    task_data = _table(data, "tasks")
    trace_data = _table(data, "traces")
    approval_data = _table(data, "approvals")
    prompt_data = _table(data, "prompts")

    modes = _string_map(_table(model_data, "modes"))
    roles = _string_map(_table(model_data, "roles"))
    default_model = _optional_string(model_data.get("default"))
    ollama_host = _optional_string(ollama_data.get("host")) or "http://localhost:11434"
    ollama_models = tuple(_string_list(ollama_data.get("models")))
    gemini_models = tuple(_string_list(gemini_data.get("models")))
    gemini_api_key_env = (
        _optional_string(gemini_data.get("api_key_env")) or "GEMINI_API_KEY"
    )
    gemini_timeout_seconds = _optional_positive_number(
        gemini_data.get("timeout_seconds"),
        default=60.0,
    )
    plugin_paths = tuple(
        _resolve_config_path(path, loaded_from)
        for path in _string_list(plugin_data.get("paths"))
    )
    capabilities = _capability_settings_from_data(capability_data)
    mcp_servers = _merge_mcp_servers(
        _capability_mcp_servers(capabilities),
        tuple(_mcp_servers_from_data(mcp_data)),
    )
    auth = auth_settings or _auth_settings_from_data(
        _table(data, "auth"),
        loaded_from,
    )
    memory_database_path = _resolve_config_path(
        _optional_string(memory_data.get("database_path")) or ".jarvis/memory.sqlite3",
        loaded_from,
    )
    memory_enabled = _optional_bool(memory_data.get("enabled"), default=True)
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
    tool_use_prompt_path = _optional_path(prompt_data.get("tool_use"), loaded_from)

    return JarvisSettings(
        models=ModelSettings(default=default_model, modes=modes, roles=roles),
        providers=ProviderSettings(
            ollama_host=ollama_host,
            ollama_models=ollama_models,
            gemini=GeminiProviderSettings(
                models=gemini_models,
                api_key_env=gemini_api_key_env,
                timeout_seconds=gemini_timeout_seconds,
            ),
        ),
        plugins=PluginSettings(paths=plugin_paths),
        capabilities=capabilities,
        mcp=McpSettings(servers=mcp_servers),
        auth=auth,
        memory=MemorySettings(
            database_path=memory_database_path,
            enabled=memory_enabled,
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
            tool_use_path=tool_use_prompt_path,
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
        models=ModelSettings(
            default=default_model,
            modes=modes,
            roles=settings.models.roles,
        ),
        providers=ProviderSettings(
            ollama_host=ollama_host,
            ollama_models=tuple(ollama_models),
            gemini=settings.providers.gemini,
        ),
        plugins=settings.plugins,
        capabilities=settings.capabilities,
        mcp=settings.mcp,
        auth=settings.auth,
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


def _capability_settings_from_data(data: dict[str, Any]) -> CapabilitySettings:
    enabled: dict[str, bool] = {}
    for name, value in data.items():
        if name not in BUILTIN_CAPABILITY_PACKS:
            raise ValueError(f"Unknown built-in capability pack: {name}.")
        enabled[name] = _optional_bool(value, default=False)
    return CapabilitySettings(enabled=enabled)


def _capability_mcp_servers(
    capabilities: CapabilitySettings,
) -> tuple[McpServerSettings, ...]:
    servers: list[McpServerSettings] = []
    if capabilities.is_enabled("google_workspace"):
        servers.extend(_google_workspace_mcp_servers())
    if capabilities.is_enabled("spotify"):
        servers.extend(_spotify_mcp_servers())
    return tuple(servers)


def _google_workspace_mcp_servers() -> tuple[McpServerSettings, ...]:
    return (
        McpServerSettings(
            name="google_calendar",
            command="python",
            args=("examples/mcp/google_calendar_fastmcp_server.py",),
            risk_level="low",
            requires_approval=False,
            tools=(
                McpToolSettings(
                    name="list_calendars",
                    argument_hints=(
                        "No arguments are needed. Use this before event lookup "
                        "when the user asks which calendars are available."
                    ),
                    risk_level="low",
                    requires_approval=False,
                ),
                McpToolSettings(
                    name="list_events",
                    argument_hints=(
                        "Use calendar_id='primary' unless the user named a "
                        "specific calendar. For relative ranges, return "
                        "RFC3339/ISO datetimes based on current_datetime. If "
                        "the user asks for upcoming events without a range, "
                        "use the next 7 days. This returns calendar evidence "
                        "only; add a separate available tool for any other "
                        "explicitly requested source."
                    ),
                    risk_level="low",
                    requires_approval=False,
                ),
                McpToolSettings(
                    name="free_busy",
                    argument_hints=(
                        "Use calendar_ids=['primary'] unless the user named "
                        "specific calendars. Convert relative time ranges into "
                        "RFC3339/ISO start and end datetimes."
                    ),
                    risk_level="low",
                    requires_approval=False,
                ),
            ),
        ),
        McpServerSettings(
            name="gmail",
            command="python",
            args=("examples/mcp/google_gmail_fastmcp_server.py",),
            risk_level="low",
            requires_approval=False,
            tools=(
                McpToolSettings(
                    name="list_recent",
                    argument_hints=(
                        "Use max_results between 5 and 10 unless the user asks "
                        "for a different count. Use label_ids only when the "
                        "user names labels such as inbox, important, or sent. "
                        "Use this for broad recent-email requests; use "
                        "search_messages when the user names a person, event, "
                        "organization, topic, or keyword."
                    ),
                    risk_level="low",
                    requires_approval=False,
                ),
                McpToolSettings(
                    name="search_messages",
                    argument_hints=(
                        "Use Gmail search syntax in query. For recent mail, "
                        "prefer newer_than:30d. For a named person, include "
                        "their name or email in the query. Use this when the "
                        "request asks for messages related to a person, event, "
                        "organization, topic, or keyword. Use max_results "
                        "between 5 and 10 unless specified."
                    ),
                    risk_level="low",
                    requires_approval=False,
                ),
                McpToolSettings(
                    name="get_message",
                    argument_hints=(
                        "Use this only when a prior result supplied a message "
                        "id. Set message_id exactly to that id. Do not pass "
                        "$last.text from a search/list result unless it is "
                        "exactly the message id."
                    ),
                    risk_level="low",
                    requires_approval=False,
                ),
                McpToolSettings(
                    name="get_thread",
                    argument_hints=(
                        "Use this only when a prior result supplied a thread "
                        "id. Set thread_id exactly to that id. Do not pass "
                        "$last.text from a search/list result unless it is "
                        "exactly the thread id."
                    ),
                    risk_level="low",
                    requires_approval=False,
                ),
            ),
        ),
    )


def _spotify_mcp_servers() -> tuple[McpServerSettings, ...]:
    return (
        McpServerSettings(
            name="spotify",
            command="python",
            args=("examples/mcp/spotify_fastmcp_server.py",),
            risk_level="low",
            requires_approval=False,
            tools=(
                McpToolSettings(
                    name="search",
                    argument_hints=(
                        "Use a concise Spotify catalog query. Set types to a "
                        "comma-separated subset of track,artist,album,playlist "
                        "based on the user request. Use limit 5-10 unless the "
                        "user asks otherwise."
                    ),
                    risk_level="low",
                    requires_approval=False,
                ),
                McpToolSettings(
                    name="current_playback",
                    argument_hints="No arguments are needed.",
                    risk_level="low",
                    requires_approval=False,
                ),
                McpToolSettings(
                    name="recently_played",
                    argument_hints=(
                        "Use limit between 5 and 10 unless the user asks for a "
                        "different count."
                    ),
                    risk_level="low",
                    requires_approval=False,
                ),
                McpToolSettings(
                    name="list_playlists",
                    argument_hints=(
                        "Use limit between 5 and 10 unless specified. Use offset "
                        "only when paging through more playlists."
                    ),
                    risk_level="low",
                    requires_approval=False,
                ),
            ),
        ),
    )


def _merge_mcp_servers(
    builtin_servers: tuple[McpServerSettings, ...],
    configured_servers: tuple[McpServerSettings, ...],
) -> tuple[McpServerSettings, ...]:
    merged: dict[str, McpServerSettings] = {}
    order: list[str] = []
    for server in (*builtin_servers, *configured_servers):
        if server.name not in merged:
            order.append(server.name)
        merged[server.name] = server
    return tuple(merged[name] for name in order)


def _mcp_servers_from_data(data: dict[str, Any]) -> list[McpServerSettings]:
    servers: list[McpServerSettings] = []
    raw_servers = data.get("servers", [])
    if not isinstance(raw_servers, list):
        raise ValueError("Expected [mcp].servers to be a list of tables.")
    for item in raw_servers:
        if not isinstance(item, dict):
            raise ValueError("Expected each MCP server to be a table.")
        name = _optional_string(item.get("name"))
        if name is None:
            raise ValueError("MCP servers require name.")
        transport = _optional_string(item.get("transport")) or "stdio"
        if transport not in {"stdio", "http"}:
            raise ValueError("MCP server transport must be stdio or http.")
        command = _optional_string(item.get("command"))
        url = _optional_string(item.get("url"))
        if transport == "stdio" and command is None:
            raise ValueError("MCP stdio servers require command.")
        if transport == "http" and url is None:
            raise ValueError("MCP http servers require url.")
        args = tuple(_string_list(item.get("args")))
        headers = _string_map(_table(item, "headers"))
        auth_provider = _optional_string(item.get("auth_provider"))
        bearer_token_env = _optional_string(item.get("bearer_token_env"))
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
                transport=transport,
                url=url,
                headers=headers,
                auth_provider=auth_provider,
                bearer_token_env=bearer_token_env,
                enabled=enabled,
                risk_level=risk_level,
                requires_approval=requires_approval,
                tools=tool_overrides,
            )
        )
    return servers


def _oauth_providers_from_data(data: dict[str, Any]) -> list[OAuthProviderSettings]:
    providers: list[OAuthProviderSettings] = []
    raw_providers = data.get("oauth_providers", [])
    if not isinstance(raw_providers, list):
        raise ValueError("Expected [auth].oauth_providers to be a list of tables.")
    for item in raw_providers:
        if not isinstance(item, dict):
            raise ValueError("Expected each OAuth provider to be a table.")
        name = _optional_string(item.get("name"))
        if name is None:
            raise ValueError("OAuth providers require name.")
        providers.append(
            OAuthProviderSettings(
                name=name,
                client_id=_optional_string(item.get("client_id")),
                client_secret_env=_optional_string(item.get("client_secret_env")),
                authorization_url=_optional_string(item.get("authorization_url")),
                token_url=_optional_string(item.get("token_url")),
                tokeninfo_url=_optional_string(item.get("tokeninfo_url")),
                redirect_uri=_optional_string(item.get("redirect_uri")),
                scopes=tuple(_string_list(item.get("scopes"))),
            )
        )
    return providers


def _auth_settings_from_data(
    data: dict[str, Any],
    loaded_from: Path | None,
) -> AuthSettings:
    """Parse auth settings from one TOML auth table."""
    auth_database_path = _resolve_config_path(
        _optional_string(data.get("database_path")) or ".jarvis/auth.sqlite3",
        loaded_from,
    )
    return AuthSettings(
        database_path=auth_database_path,
        oauth_providers=tuple(_oauth_providers_from_data(data)),
        loaded_from=loaded_from if data else None,
    )


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
                argument_hints=_optional_string(item.get("argument_hints")),
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


def _optional_positive_number(value: Any, default: float) -> float:
    """Return a strictly positive numeric setting or its default."""
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"Expected number value, got {type(value).__name__}.")
    number = float(value)
    if number <= 0:
        raise ValueError("Expected a positive number.")
    return number


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
