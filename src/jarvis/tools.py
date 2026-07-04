"""Tool registry and built-in demo tools."""

from __future__ import annotations

import re
from typing import Any

from jarvis.contracts import AvailableTool, ToolCall, ToolHandler, ToolResult, ToolSpec
from jarvis.memory import MemoryStore
from jarvis.tasks import TaskStore


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

    def has(self, name: str) -> bool:
        """Return whether a tool is registered."""
        return name in self._specs

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

    def available_tools(self) -> tuple[AvailableTool, ...]:
        """Return planner-safe metadata for registered tools."""
        return tuple(
            AvailableTool(
                name=tool.name,
                description=tool.description,
                risk_level=tool.risk_level,
                requires_approval=tool.requires_approval,
                source=tool.source,
            )
            for tool in self.list()
        )


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


def _task_create(arguments: dict[str, Any], task_store: TaskStore) -> dict[str, Any]:
    raw_title = str(arguments.get("title") or arguments.get("goal") or "").strip()
    title = _clean_task_title(raw_title)
    if not title:
        raise ValueError("Task title is required.")
    record = task_store.create(
        title=title,
        source=str(arguments.get("source", "tool")),
        metadata={"input": arguments},
    )
    return {
        "task": {
            "id": record.id,
            "title": record.title,
            "status": record.status,
            "source": record.source,
            "created_at": record.created_at,
            "updated_at": record.updated_at,
            "metadata": record.metadata,
        }
    }


def _clean_task_title(title: str) -> str:
    """Remove common command phrasing from local task titles."""
    cleaned = title.strip()
    lowered = cleaned.lower()
    markers = (
        "create a task to ",
        "create task to ",
        "add a task to ",
        "add task to ",
        "create a todo to ",
        "add a todo to ",
    )
    for marker in markers:
        index = lowered.find(marker)
        if index >= 0:
            cleaned = cleaned[index + len(marker) :].strip()
            lowered = cleaned.lower()
            break
    patterns = (
        r"^create\s+(?:a\s+)?task\s+to\s+",
        r"^add\s+(?:a\s+)?task\s+to\s+",
        r"^create\s+(?:a\s+)?todo\s+to\s+",
        r"^add\s+(?:a\s+)?todo\s+to\s+",
        r"^todo:\s*",
    )
    for pattern in patterns:
        cleaned = re.sub(pattern, "", cleaned, flags=re.IGNORECASE).strip()
    if cleaned:
        return cleaned[:1].upper() + cleaned[1:]
    return cleaned


def _memory_search(
    arguments: dict[str, Any],
    memory_store: MemoryStore,
) -> dict[str, Any]:
    query = str(arguments.get("query", "")).strip()
    limit = int(arguments.get("limit", 5))
    records = memory_store.search(query, limit=limit)
    return {
        "query": query,
        "matches": [
            {
                "id": record.id,
                "type": record.type,
                "content": record.content,
                "source": record.source,
                "created_at": record.created_at,
                "updated_at": record.updated_at,
                "metadata": record.metadata,
            }
            for record in records
        ],
    }


def _calendar_search_events(arguments: dict[str, Any]) -> dict[str, Any]:
    query = str(arguments.get("query", "")).strip()
    normalized = query.lower()
    if "jordan" in normalized or "meeting" in normalized:
        return {
            "query": query,
            "events": [
                {
                    "title": "Jordan project sync",
                    "time": "tomorrow at 2:00 PM",
                    "attendees": ["Jordan", "User"],
                    "notes": "Review project timeline and open questions.",
                }
            ],
            "source": "demo-calendar",
        }
    return {
        "query": query,
        "events": [],
        "note": "Calendar integration is not configured yet.",
    }


def default_tool_registry(
    memory_store: MemoryStore | None = None,
    task_store: TaskStore | None = None,
) -> ToolRegistry:
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
    if task_store is not None:
        registry.register(
            ToolSpec(
                name="task.create",
                description="Create a low-risk local task.",
                risk_level="low",
                requires_approval=False,
            ),
            lambda arguments: _task_create(arguments, task_store),
        )
    if memory_store is not None:
        registry.register(
            ToolSpec(
                name="memory.search",
                description="Search local memory records.",
            ),
            lambda arguments: _memory_search(arguments, memory_store),
        )
    registry.register(
        ToolSpec(
            name="calendar.search_events",
            description="Search calendar events.",
        ),
        _calendar_search_events,
    )
    return registry
