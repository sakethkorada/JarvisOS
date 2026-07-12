"""Built-in JarvisOS tools."""

from __future__ import annotations

from datetime import datetime
import re
from typing import Any

from jarvis.contracts import ModelRequest, ToolExecutionContext, ToolSpec
from jarvis.storage.memory import MemoryStore
from jarvis.storage.tasks import TaskStore
from jarvis.tools.registry import ToolRegistry


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


def _system_current_datetime(arguments: dict[str, Any]) -> dict[str, Any]:
    """Return the current local date and time for time-aware answers."""
    now = datetime.now().astimezone()
    iso_datetime = now.isoformat()
    return {
        "text": now.strftime("%A, %B %d, %Y %I:%M %p %Z"),
        "date": now.date().isoformat(),
        "datetime": iso_datetime,
        "iso": iso_datetime,
        "timezone": now.tzname(),
    }


def _general_generate_text(
    arguments: dict[str, Any],
    context: ToolExecutionContext,
) -> dict[str, Any]:
    instruction = str(arguments.get("instruction") or context.goal).strip()
    extra_context = str(arguments.get("context", "")).strip()
    if not instruction:
        raise ValueError("Instruction is required.")
    selected_model = context.models.resolve_provider_name(
        explicit_provider_name=context.model_name,
        mode=context.model_mode,
        role="general",
    )
    if selected_model == "fake-local":
        return {
            "text": f"Generated text for: {instruction}",
            "model": "fake-local",
        }

    messages = [
        f"User goal: {context.goal}",
        "Generate only the requested text. Do not explain the plan.",
    ]
    if extra_context:
        messages.append(f"Additional context: {extra_context}")
    request = ModelRequest(
        goal=instruction,
        messages=messages,
        mode=context.model_mode,
        system_prompt=(
            "You are the JarvisOS generalist language agent. Produce concise, "
            "useful text for the requested task using only the given context."
        ),
    )
    response = context.models.run(request, context.model_name, role="general")
    text = response.text.strip()
    if not text:
        raise ValueError("Model returned empty generated text.")
    return {
        "text": text,
        "model": response.model_name,
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


def default_tool_registry(
    memory_store: MemoryStore | None = None,
    task_store: TaskStore | None = None,
) -> ToolRegistry:
    """Create the built-in demo tools for the first runtime slice."""
    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            name="task.breakdown",
            description=(
                "Break a goal into generic execution steps. Use only when the "
                "user explicitly asks for a task breakdown or step-by-step plan; "
                "do not use as filler after provider tools."
            ),
        ),
        _task_breakdown,
    )
    registry.register(
        ToolSpec(
            name="task.create_summary",
            description=(
                "Create a lightweight fallback response when no better provider, "
                "plugin, memory, or language tool can satisfy the request. Final "
                "synthesis normally summarizes completed tool results."
            ),
        ),
        _task_create_summary,
    )
    registry.register(
        ToolSpec(
            name="system.current_datetime",
            description=(
                "Return the current local date, time, timezone, and ISO datetime. "
                "The ISO value is available as both datetime and iso. "
                "Use for questions about today's date, current time, or resolving "
                "time-sensitive context."
            ),
            input_schema={
                "type": "object",
                "properties": {},
            },
        ),
        _system_current_datetime,
    )
    registry.register_contextual(
        ToolSpec(
            name="general.generate_text",
            description="Generate or transform text with the selected model.",
        ),
        _general_generate_text,
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
                description=(
                    "Search durable local user memory such as preferences, facts, "
                    "and remembered context. This is not web search and does not "
                    "provide current time."
                ),
            ),
            lambda arguments: _memory_search(arguments, memory_store),
        )
    return registry
