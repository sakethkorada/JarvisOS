"""Tool registry and built-in demo tools."""

from __future__ import annotations

from typing import Any

from jarvis.contracts import ToolCall, ToolHandler, ToolResult, ToolSpec


class ToolRegistry:
    """In-memory registry that maps tool specs to executable handlers."""

    def __init__(self) -> None:
        self._specs: dict[str, ToolSpec] = {}
        self._handlers: dict[str, ToolHandler] = {}

    def register(self, spec: ToolSpec, handler: ToolHandler) -> None:
        """Register or replace a tool and its handler."""
        self._specs[spec.name] = spec
        self._handlers[spec.name] = handler

    def get(self, name: str) -> ToolSpec:
        """Return a tool specification by name."""
        try:
            return self._specs[name]
        except KeyError as exc:
            raise KeyError(f"Unknown tool: {name}") from exc

    def execute(self, call: ToolCall) -> ToolResult:
        """Execute a registered tool call and normalize failures."""
        spec = self.get(call.tool_name)
        handler = self._handlers[spec.name]
        try:
            return ToolResult(tool_name=spec.name, output=handler(call.arguments))
        except Exception as exc:  # pragma: no cover - defensive boundary
            return ToolResult(
                tool_name=spec.name,
                output={},
                success=False,
                error=str(exc),
            )

    def list(self) -> list[ToolSpec]:
        """Return registered tools in stable display order."""
        return sorted(self._specs.values(), key=lambda tool: tool.name)


def _task_breakdown(arguments: dict[str, Any]) -> dict[str, Any]:
    goal = str(arguments.get("goal", "")).strip()
    return {
        "steps": [
            "Understand the request.",
            "Identify available capabilities.",
            "Execute safe read-only steps.",
            "Summarize the result.",
        ],
        "goal": goal,
    }


def _task_create_summary(arguments: dict[str, Any]) -> dict[str, Any]:
    goal = str(arguments.get("goal", "")).strip()
    return {
        "summary": f"Prepared a lightweight response for: {goal}",
        "pending_approvals": [],
    }


def _memory_search(arguments: dict[str, Any]) -> dict[str, Any]:
    query = str(arguments.get("query", "")).strip()
    return {
        "query": query,
        "matches": [],
        "note": "No durable memory store is configured yet.",
    }


def _calendar_search_events(arguments: dict[str, Any]) -> dict[str, Any]:
    query = str(arguments.get("query", "")).strip()
    return {
        "query": query,
        "events": [],
        "note": "Calendar integration is not configured yet.",
    }


def default_tool_registry() -> ToolRegistry:
    """Create the built-in demo tools for the first runtime slice."""
    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            name="task.breakdown",
            description="Break a simple goal into generic execution steps.",
        ),
        _task_breakdown,
    )
    registry.register(
        ToolSpec(
            name="task.create_summary",
            description="Create a lightweight response from gathered context.",
        ),
        _task_create_summary,
    )
    registry.register(
        ToolSpec(
            name="memory.search",
            description="Search local memory records.",
        ),
        _memory_search,
    )
    registry.register(
        ToolSpec(
            name="calendar.search_events",
            description="Search calendar events.",
        ),
        _calendar_search_events,
    )
    return registry
