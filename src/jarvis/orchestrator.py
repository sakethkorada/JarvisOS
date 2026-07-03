"""Small generic orchestrator for the first runnable JarvisOS slice."""

from __future__ import annotations

from dataclasses import replace

from jarvis.agents import AgentRegistry
from jarvis.contracts import (
    ExecutionPlan,
    ModelRequest,
    PlanStep,
    RunResult,
    ToolCall,
    ToolResult,
    TraceEvent,
    new_id,
)
from jarvis.models import ModelRouter
from jarvis.policies import PolicyEngine
from jarvis.tools import ToolRegistry


class Orchestrator:
    """Coordinates planning, policy checks, tool execution, and traces."""

    def __init__(
        self,
        agents: AgentRegistry,
        tools: ToolRegistry,
        models: ModelRouter,
        policies: PolicyEngine,
    ) -> None:
        self._agents = agents
        self._tools = tools
        self._models = models
        self._policies = policies

    def run(
        self,
        goal: str,
        model_name: str | None = None,
        model_mode: str = "balanced",
    ) -> RunResult:
        """Run a goal through the current deterministic execution loop."""
        run_id = new_id("run")
        trace: list[TraceEvent] = [
            TraceEvent("run.started", "Run started.", data={"goal": goal}),
        ]

        model_response = self._models.run(
            ModelRequest(goal=goal, mode=model_mode),
            model_name,
        )
        trace.append(
            TraceEvent(
                "model.selected",
                f"Using model provider {model_response.model_name}.",
                data={"model_response": model_response.text},
            )
        )

        plan = self._create_plan(goal)
        trace.append(
            TraceEvent(
                "plan.created",
                f"Created {len(plan.steps)} step(s).",
                data={"steps": [step.description for step in plan.steps]},
            )
        )

        completed_steps: list[PlanStep] = []
        results: list[ToolResult] = []
        status = "completed"

        for step in plan.steps:
            agent = self._agents.get(step.agent_name)
            tool = self._tools.get(step.tool_call.tool_name)

            if tool.name not in agent.allowed_tools:
                result = ToolResult(
                    tool_name=tool.name,
                    output={},
                    success=False,
                    error=f"{agent.name} is not allowed to use {tool.name}.",
                )
                results.append(result)
                completed_steps.append(replace(step, status="failed"))
                status = "failed"
                trace.append(TraceEvent("step.failed", result.error or "Step failed."))
                continue

            decision = self._policies.evaluate(tool)
            trace.append(
                TraceEvent(
                    "policy.evaluated",
                    decision.reason,
                    data={"tool": tool.name, "status": decision.status},
                )
            )

            if not decision.allowed:
                result = ToolResult(
                    tool_name=tool.name,
                    output={"policy": decision.reason},
                    success=False,
                    error="Approval required.",
                )
                results.append(result)
                completed_steps.append(replace(step, status="approval_required"))
                status = "pending_approval"
                trace.append(
                    TraceEvent(
                        "approval.required",
                        f"Approval required for {tool.name}.",
                    )
                )
                continue

            result = self._tools.execute(step.tool_call)
            results.append(result)
            step_status = "completed" if result.success else "failed"
            completed_steps.append(replace(step, status=step_status))
            trace.append(
                TraceEvent(
                    f"step.{step_status}",
                    step.description,
                    data={"tool": result.tool_name, "output": result.output},
                )
            )
            if not result.success:
                status = "failed"

        final_plan = replace(plan, steps=tuple(completed_steps))
        final_response = self._summarize(goal, results, status)
        trace.append(TraceEvent("run.finished", f"Run finished with status {status}."))

        return RunResult(
            run_id=run_id,
            goal=goal,
            plan=final_plan,
            step_results=tuple(results),
            trace=tuple(trace),
            final_response=final_response,
            status=status,
        )

    def _create_plan(self, goal: str) -> ExecutionPlan:
        """Create a minimal plan using currently registered demo capabilities."""
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

        steps.append(
            PlanStep(
                id=new_id("step"),
                agent_name="orchestrator",
                tool_call=ToolCall("task.breakdown", {"goal": goal}),
                description="Create a simple task breakdown.",
            )
        )
        steps.append(
            PlanStep(
                id=new_id("step"),
                agent_name="orchestrator",
                tool_call=ToolCall("task.create_summary", {"goal": goal}),
                description="Create the final lightweight summary.",
            )
        )
        return ExecutionPlan(goal=goal, steps=tuple(steps))

    def _summarize(
        self,
        goal: str,
        results: list[ToolResult],
        status: str,
    ) -> str:
        """Build a simple user-facing summary from tool results."""
        failed = [result for result in results if not result.success]
        lines = [
            f"Goal: {goal}",
            f"Status: {status}",
            "",
            "Completed tool calls:",
        ]
        for result in results:
            marker = "OK" if result.success else "BLOCKED"
            lines.append(f"- {marker} {result.tool_name}")
        if failed:
            lines.append("")
            lines.append("Attention needed:")
            for result in failed:
                lines.append(f"- {result.tool_name}: {result.error}")
        return "\n".join(lines)
