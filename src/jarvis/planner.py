"""LLM-assisted planning with deterministic validation and fallback."""

from __future__ import annotations

import json
from typing import Any

from jarvis.agents import AgentRegistry
from jarvis.contracts import (
    AvailableTool,
    ExecutionPlan,
    ModelRequest,
    PlanStep,
    ToolCall,
    new_id,
)
from jarvis.errors import ModelProviderError
from jarvis.models import ModelRouter
from jarvis.tools import ToolRegistry


class Planner:
    """Creates execution plans from available tools and an optional LLM."""

    def __init__(
        self,
        agents: AgentRegistry,
        tools: ToolRegistry,
        models: ModelRouter,
        system_prompt: str,
    ) -> None:
        self._agents = agents
        self._tools = tools
        self._models = models
        self._system_prompt = system_prompt

    def create_plan(
        self,
        goal: str,
        model_name: str | None,
        model_mode: str,
    ) -> tuple[ExecutionPlan, str, str | None]:
        """Create a validated plan and return the source plus raw model output."""
        if model_name == "fake-local":
            return self.create_fallback_plan(goal), "fallback", None

        available_tools = self._tools.available_tools()
        request = ModelRequest(
            goal=goal,
            mode=model_mode,
            system_prompt=self._system_prompt,
            messages=[_planner_context(available_tools)],
        )
        try:
            model_response = self._models.run(request, model_name)
        except ModelProviderError as exc:
            return self.create_fallback_plan(goal), "fallback", str(exc)
        parsed_steps = _parse_model_steps(model_response.text)
        if parsed_steps is None:
            return self.create_fallback_plan(goal), "fallback", model_response.text

        plan = self._plan_from_steps(goal, parsed_steps)
        if plan is None:
            return self.create_fallback_plan(goal), "fallback", model_response.text
        return plan, "llm", model_response.text

    def create_fallback_plan(self, goal: str) -> ExecutionPlan:
        """Create a deterministic plan from simple available-tool heuristics."""
        normalized = goal.lower()
        steps: list[PlanStep] = [
            PlanStep(
                id=new_id("step"),
                agent_name="memory",
                tool_call=ToolCall("memory.search", {"query": goal}),
                description="Search memory for relevant context.",
            )
        ]

        if any(word in normalized for word in ("meeting", "calendar", "schedule")):
            steps.append(
                PlanStep(
                    id=new_id("step"),
                    agent_name="calendar",
                    tool_call=ToolCall("calendar.search_events", {"query": goal}),
                    description="Check calendar context.",
                )
            )

        should_search_notes = any(
            word in normalized
            for word in ("note", "notes", "jordan", "project", "meeting")
        )
        if should_search_notes and self._tools.has("notes.search"):
            steps.append(
                PlanStep(
                    id=new_id("step"),
                    agent_name="plugin",
                    tool_call=ToolCall("notes.search", {"query": goal}),
                    description="Search configured notes plugin.",
                )
            )

        should_create_task = any(
            phrase in normalized
            for phrase in (
                "create a task",
                "add a task",
                "todo",
                "to-do",
                "track a task",
            )
        )
        if should_create_task and self._tools.has("task.create"):
            steps.append(
                PlanStep(
                    id=new_id("step"),
                    agent_name="orchestrator",
                    tool_call=ToolCall("task.create", {"title": goal}),
                    description="Create a local task.",
                )
            )

        should_generate_text = any(
            phrase in normalized
            for phrase in (
                "generate",
                "draft",
                "compose",
                "rewrite",
                "write",
                "fun fact",
            )
        )
        if should_generate_text and self._tools.has("general.generate_text"):
            steps.append(
                PlanStep(
                    id=new_id("step"),
                    agent_name="general",
                    tool_call=ToolCall(
                        "general.generate_text",
                        {"instruction": goal},
                    ),
                    description="Generate requested text.",
                )
            )

        if "echo" in normalized:
            echo_tool = _first_matching_tool(self._tools, suffix=".echo")
            if echo_tool is not None:
                echo_text = "$last.text" if should_generate_text else goal
                steps.append(
                    PlanStep(
                        id=new_id("step"),
                        agent_name="plugin",
                        tool_call=ToolCall(echo_tool, {"text": echo_text}),
                        description="Call configured echo tool.",
                    )
                )

        if self._tools.has("task.create_summary"):
            steps.append(
                PlanStep(
                    id=new_id("step"),
                    agent_name="orchestrator",
                    tool_call=ToolCall("task.create_summary", {"goal": goal}),
                    description="Create the final lightweight summary.",
                )
            )
        return ExecutionPlan(goal=goal, steps=tuple(steps))

    def _plan_from_steps(
        self,
        goal: str,
        steps: list[dict[str, Any]],
    ) -> ExecutionPlan | None:
        plan_steps: list[PlanStep] = []
        for step in steps:
            tool_name = step.get("tool_name")
            arguments = step.get("arguments", {})
            description = step.get("description")
            if not isinstance(tool_name, str) or not self._tools.has(tool_name):
                return None
            if not isinstance(arguments, dict):
                return None
            if not isinstance(description, str) or not description.strip():
                return None
            arguments = _normalize_arguments(tool_name, arguments, goal)

            agent_name = _agent_for_tool(tool_name)
            if not _agent_can_use_tool(self._agents, agent_name, tool_name):
                return None
            plan_steps.append(
                PlanStep(
                    id=new_id("step"),
                    agent_name=agent_name,
                    tool_call=ToolCall(tool_name, arguments),
                    description=description.strip(),
                )
            )

        if not plan_steps:
            return None
        return ExecutionPlan(goal=goal, steps=tuple(plan_steps))


def _planner_context(available_tools: tuple[AvailableTool, ...]) -> str:
    tools = [
        {
            "name": tool.name,
            "description": tool.description,
            "risk_level": tool.risk_level,
            "requires_approval": tool.requires_approval,
            "source": tool.source,
        }
        for tool in available_tools
    ]
    return "Available tools:\n" + json.dumps(tools, indent=2)


def _parse_model_steps(text: str) -> list[dict[str, Any]] | None:
    try:
        payload = json.loads(_strip_code_fence(text))
    except json.JSONDecodeError:
        return None
    steps = payload.get("steps") if isinstance(payload, dict) else None
    if not isinstance(steps, list):
        return None
    return [step for step in steps if isinstance(step, dict)]


def _strip_code_fence(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        return "\n".join(lines).strip()
    return stripped


def _agent_for_tool(tool_name: str) -> str:
    if tool_name.startswith("general."):
        return "general"
    if tool_name.startswith("memory."):
        return "memory"
    if tool_name.startswith("calendar."):
        return "calendar"
    if tool_name.startswith("task."):
        return "orchestrator"
    return "plugin"


def _agent_can_use_tool(
    agents: AgentRegistry,
    agent_name: str,
    tool_name: str,
) -> bool:
    agent = agents.get(agent_name)
    return "*" in agent.allowed_tools or tool_name in agent.allowed_tools


def _normalize_arguments(
    tool_name: str,
    arguments: dict[str, Any],
    goal: str,
) -> dict[str, Any]:
    """Fill safe defaults for known built-in tools."""
    normalized = dict(arguments)
    if tool_name in {"memory.search", "calendar.search_events", "notes.search"}:
        normalized.setdefault("query", goal)
    if tool_name in {"task.breakdown", "task.create", "task.create_summary"}:
        normalized.setdefault("goal", goal)
    if tool_name == "task.create":
        normalized.setdefault("title", goal)
    if tool_name == "general.generate_text":
        normalized.setdefault("instruction", goal)
    if tool_name.endswith(".echo"):
        normalized.setdefault("text", goal)
    return normalized


def _first_matching_tool(tools: ToolRegistry, suffix: str) -> str | None:
    for tool in tools.list():
        if tool.name.endswith(suffix):
            return tool.name
    return None
