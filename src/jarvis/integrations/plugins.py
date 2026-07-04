"""Local plugin loading for JarvisOS.

Online plugin support should install or sync plugin folders locally first. The
runtime then loads those folders through this same local manifest path.
"""

from __future__ import annotations

import importlib.util
import tomllib
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any, cast

from jarvis.contracts import RiskLevel, ToolHandler, ToolSpec
from jarvis.tools.registry import ToolRegistry


PLUGIN_MANIFEST = "plugin.toml"
VALID_RISK_LEVELS: set[str] = {"low", "medium", "high"}


@dataclass(frozen=True)
class PluginToolDefinition:
    """Tool entry loaded from a plugin manifest."""

    name: str
    description: str
    handler: str
    risk_level: RiskLevel = "low"
    requires_approval: bool = False


@dataclass(frozen=True)
class PluginManifest:
    """Validated local plugin manifest."""

    name: str
    description: str
    tools: tuple[PluginToolDefinition, ...]
    path: Path


def load_plugins(plugin_paths: tuple[Path, ...], registry: ToolRegistry) -> None:
    """Load all configured local plugin directories into a tool registry."""
    for plugin_path in plugin_paths:
        manifest = load_plugin_manifest(plugin_path)
        register_plugin_tools(manifest, registry)


def load_plugin_manifest(plugin_path: Path) -> PluginManifest:
    """Read and validate a plugin manifest from a local directory."""
    manifest_path = plugin_path / PLUGIN_MANIFEST
    if not manifest_path.exists():
        raise ValueError(f"Missing plugin manifest: {manifest_path}")

    with manifest_path.open("rb") as manifest_file:
        data = tomllib.load(manifest_file)

    name = _required_string(data, "name")
    description = _required_string(data, "description")
    tool_data = data.get("tools", [])
    if not isinstance(tool_data, list):
        raise ValueError(f"Expected [[tools]] entries in {manifest_path}")

    tools = tuple(_parse_tool(tool, manifest_path) for tool in tool_data)
    return PluginManifest(
        name=name,
        description=description,
        tools=tools,
        path=plugin_path,
    )


def register_plugin_tools(manifest: PluginManifest, registry: ToolRegistry) -> None:
    """Import plugin handlers and register their declared tools."""
    for tool in manifest.tools:
        handler = _load_handler(manifest.path, tool.handler)
        registry.register(
            ToolSpec(
                name=tool.name,
                description=tool.description,
                risk_level=tool.risk_level,
                requires_approval=tool.requires_approval,
                source=f"plugin:{manifest.name}",
            ),
            handler,
        )


def _parse_tool(data: Any, manifest_path: Path) -> PluginToolDefinition:
    if not isinstance(data, dict):
        raise ValueError(f"Invalid tool entry in {manifest_path}")

    risk_level = data.get("risk_level", "low")
    if not isinstance(risk_level, str) or risk_level not in VALID_RISK_LEVELS:
        raise ValueError(f"Invalid risk_level for plugin tool in {manifest_path}")

    requires_approval = data.get("requires_approval", False)
    if not isinstance(requires_approval, bool):
        raise ValueError(f"requires_approval must be boolean in {manifest_path}")

    return PluginToolDefinition(
        name=_required_string(data, "name"),
        description=_required_string(data, "description"),
        handler=_required_string(data, "handler"),
        risk_level=cast(RiskLevel, risk_level),
        requires_approval=requires_approval,
    )


def _load_handler(plugin_path: Path, handler_path: str) -> ToolHandler:
    module_name, _, function_name = handler_path.rpartition(".")
    if not module_name or not function_name:
        raise ValueError(f"Invalid plugin handler path: {handler_path}")

    module = _load_module(plugin_path, module_name)
    handler = getattr(module, function_name, None)
    if not callable(handler):
        raise ValueError(f"Plugin handler is not callable: {handler_path}")
    return handler


def _load_module(plugin_path: Path, module_name: str) -> ModuleType:
    module_path = plugin_path / f"{module_name.replace('.', '/')}.py"
    if not module_path.exists():
        raise ValueError(f"Missing plugin module: {module_path}")

    unique_name = f"jarvis_plugin_{plugin_path.name}_{module_name}".replace("-", "_")
    spec = importlib.util.spec_from_file_location(unique_name, module_path)
    if spec is None or spec.loader is None:
        raise ValueError(f"Could not load plugin module: {module_path}")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _required_string(data: dict[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Missing required string value: {key}")
    return value.strip()
