"""LLM-assisted planning with deterministic validation and generic fallback."""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any

from jarvis.agents import AgentRegistry
from jarvis.contracts import (
    AvailableTool,
    ExecutionPlan,
    ModelRequest,
    ModelResponse,
    PlanStep,
    ToolSpec,
    ToolCall,
    new_id,
)
from jarvis.errors import ModelProviderError
from jarvis.models import ModelRouter
from jarvis.orchestration.agent_runtime import AgentRuntime
from jarvis.orchestration.arguments import resolve_tool_arguments
from jarvis.tools.registry import ToolRegistry


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
        self._agent_runtime = AgentRuntime(agents.get("planner"), models)

    def create_plan(
        self,
        goal: str,
        model_name: str | None,
        model_mode: str,
    ) -> tuple[ExecutionPlan, str, str | None]:
        """Create a validated plan and return the source plus raw model output."""
        selected_model = self._agent_runtime.resolve_model_name(model_name, model_mode)
        if selected_model == "fake-local":
            return self.create_fallback_plan(goal), "fallback", None

        available_tools = self._tools.available_tools()
        request = ModelRequest(
            goal=goal,
            mode=model_mode,
            system_prompt=self._system_prompt,
            messages=[_planner_context(available_tools)],
        )
        try:
            model_response = self._agent_runtime.run(request, model_name).response
        except ModelProviderError as exc:
            return self.create_fallback_plan(goal), "fallback", str(exc)
        plan, error = self._plan_from_model_output(goal, model_response.text)
        if plan is not None:
            return plan, "llm", model_response.text

        repair_response = self._repair_plan(
            goal,
            model_name,
            model_mode,
            available_tools,
            model_response.text,
            error or "Planner output was invalid.",
        )
        if repair_response is not None:
            repaired_plan, _ = self._plan_from_model_output(goal, repair_response.text)
            if repaired_plan is not None:
                return repaired_plan, "llm_repaired", repair_response.text
        return self.create_fallback_plan(goal), "fallback", model_response.text

    def create_fallback_plan(self, goal: str) -> ExecutionPlan:
        """Create a minimal safe fallback without provider keyword routing."""
        steps: list[PlanStep] = []
        if self._tools.has("memory.search"):
            steps.append(
                PlanStep(
                    id=new_id("step"),
                    agent_name="memory",
                    tool_call=ToolCall("memory.search", {"query": goal}),
                    description="Search memory for relevant context.",
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

    def _repair_plan(
        self,
        goal: str,
        model_name: str | None,
        model_mode: str,
        available_tools: tuple[AvailableTool, ...],
        invalid_output: str,
        error: str,
    ) -> ModelResponse | None:
        """Ask the model for one corrected plan after validation fails."""
        request = ModelRequest(
            goal=goal,
            mode=model_mode,
            system_prompt=self._system_prompt,
            messages=[
                _planner_context(available_tools),
                _planner_repair_context(invalid_output, error),
            ],
        )
        try:
            return self._agent_runtime.run(request, model_name).response
        except ModelProviderError:
            return None

    def _plan_from_model_output(
        self,
        goal: str,
        text: str,
    ) -> tuple[ExecutionPlan | None, str | None]:
        """Parse and validate model planner output."""
        parsed_steps, error = _parse_model_steps(text)
        if parsed_steps is None:
            return None, error
        return self._plan_from_steps(goal, parsed_steps)

    def _plan_from_steps(
        self,
        goal: str,
        steps: list[dict[str, Any]],
    ) -> tuple[ExecutionPlan | None, str | None]:
        plan_steps: list[PlanStep] = []
        for step in steps:
            tool_name = step.get("tool_name")
            arguments = step.get("arguments", {})
            description = step.get("description")
            if not isinstance(tool_name, str) or not self._tools.has(tool_name):
                return None, f"Unknown or invalid tool_name: {tool_name!r}."
            if not isinstance(arguments, dict):
                return None, f"Arguments for {tool_name} must be a JSON object."
            if not isinstance(description, str) or not description.strip():
                return None, f"Description for {tool_name} must be a string."
            reference_error = _unsupported_reference_error(arguments)
            if reference_error is not None:
                return None, reference_error
            tool_spec = self._tools.get(tool_name)
            resolution = resolve_tool_arguments(
                goal,
                tool_spec,
                arguments,
                resolve_references=False,
            )
            if resolution.error is not None:
                return None, resolution.error
            arguments = resolution.arguments

            agent_name = _agent_for_tool(self._tools, tool_name)
            if not _agent_can_use_tool(
                self._agents,
                agent_name,
                tool_name,
                tool_spec,
            ):
                return None, f"{agent_name} is not allowed to use {tool_name}."
            plan_steps.append(
                PlanStep(
                    id=new_id("step"),
                    agent_name=agent_name,
                    tool_call=ToolCall(tool_name, arguments),
                    description=description.strip(),
                )
            )

        if not plan_steps:
            return None, "Planner returned no executable steps."
        return ExecutionPlan(goal=goal, steps=tuple(plan_steps)), None


def _planner_context(available_tools: tuple[AvailableTool, ...]) -> str:
    tools = [
        {
            "name": tool.name,
            "description": tool.description,
            "argument_hints": tool.argument_hints,
            "risk_level": tool.risk_level,
            "requires_approval": tool.requires_approval,
            "source": tool.source,
            "input_schema": tool.input_schema,
            "capability": (
                asdict(tool.capability) if tool.capability is not None else None
            ),
        }
        for tool in available_tools
    ]
    return (
        "Registered tool catalog. Choose from these tools only. Use the "
        "description, capability metadata, risk, approval requirement, "
        "input_schema, and argument_hints to decide which tools best satisfy "
        "the goal.\n"
        + json.dumps(tools, indent=2)
    )


def _planner_repair_context(invalid_output: str, error: str) -> str:
    return (
        "The previous planner output was invalid.\n"
        f"Validation error: {error}\n"
        "Previous output:\n"
        f"{invalid_output}\n"
        "Return corrected JSON using the required planner schema. Do not invent "
        "tools. Do not explain the correction."
    )


def _parse_model_steps(text: str) -> tuple[list[dict[str, Any]] | None, str | None]:
    try:
        payload = json.loads(_strip_code_fence(text))
    except json.JSONDecodeError:
        return None, "Planner response was not valid JSON."
    steps = payload.get("steps") if isinstance(payload, dict) else None
    if not isinstance(steps, list):
        return None, "Planner response must be a JSON object with a steps list."
    if not all(isinstance(step, dict) for step in steps):
        return None, "Every planner step must be a JSON object."
    return steps, None


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


def _unsupported_reference_error(value: Any) -> str | None:
    if isinstance(value, str):
        if value.startswith("$") and not value.startswith("$last."):
            return (
                f"Unsupported planner reference {value!r}. "
                "Use only $last.<field> references."
            )
        return None
    if isinstance(value, list):
        for item in value:
            error = _unsupported_reference_error(item)
            if error is not None:
                return error
    if isinstance(value, dict):
        for item in value.values():
            error = _unsupported_reference_error(item)
            if error is not None:
                return error
    return None


def _agent_for_tool(tools: ToolRegistry, tool_name: str) -> str:
    capability = tools.get(tool_name).capability
    if capability is not None and capability.domain == "calendar":
        return "calendar"
    if capability is not None and capability.domain == "email":
        return "email"
    if capability is not None and capability.domain == "music":
        return "music"
    if tool_name.startswith("general."):
        return "general"
    if tool_name.startswith("memory."):
        return "memory"
    if tool_name.startswith("system."):
        return "system"
    if tool_name.startswith("task."):
        return "orchestrator"
    return "plugin"


def _agent_can_use_tool(
    agents: AgentRegistry,
    agent_name: str,
    tool_name: str,
    tool: ToolSpec | None = None,
) -> bool:
    agent = agents.get(agent_name)
    if agent_name == "calendar" and tool is not None:
        capability = tool.capability
        if capability is not None and capability.domain == "calendar":
            return True
    if agent_name == "email" and tool is not None:
        capability = tool.capability
        if capability is not None and capability.domain == "email":
            return True
    if agent_name == "music" and tool is not None:
        capability = tool.capability
        if capability is not None and capability.domain == "music":
            return True
    return "*" in agent.allowed_tools or tool_name in agent.allowed_tools
