"""Small generic orchestrator for the first runnable JarvisOS slice."""

from __future__ import annotations

from dataclasses import replace

from jarvis.approvals import ApprovalStore
from jarvis.agents import AgentRegistry
from jarvis.contracts import (
    MemoryCandidate,
    PlanStep,
    RunResult,
    ToolCall,
    ToolExecutionContext,
    ToolResult,
    TraceEvent,
    new_id,
)
from jarvis.memory import MemoryExtractor
from jarvis.models import ModelRouter
from jarvis.planner import Planner
from jarvis.policies import PolicyEngine
from jarvis.synthesizer import Synthesizer
from jarvis.tools import ToolRegistry


class Orchestrator:
    """Coordinates planning, policy checks, tool execution, and traces."""

    def __init__(
        self,
        agents: AgentRegistry,
        tools: ToolRegistry,
        models: ModelRouter,
        policies: PolicyEngine,
        planner_prompt: str,
        synthesis_prompt: str,
        approval_store: ApprovalStore | None = None,
        memory_extractor: MemoryExtractor | None = None,
        auto_write_memory: bool = False,
    ) -> None:
        self._agents = agents
        self._tools = tools
        self._models = models
        self._policies = policies
        self._approval_store = approval_store
        self._memory_extractor = memory_extractor
        self._auto_write_memory = auto_write_memory
        self._planner = Planner(agents, tools, models, planner_prompt)
        self._synthesizer = Synthesizer(models, synthesis_prompt)

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

        plan, plan_source, raw_planner_output = self._planner.create_plan(
            goal,
            model_name,
            model_mode,
        )
        trace.append(
            TraceEvent(
                "planner.selected",
                f"Created plan with {plan_source} planner.",
                data={
                    "model": model_name or "default",
                    "source": plan_source,
                    "raw_planner_output": raw_planner_output,
                },
            )
        )
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
            resolved_arguments, resolution_error = _resolve_arguments(
                step.tool_call.arguments,
                tuple(results),
            )
            executable_step = replace(
                step,
                tool_call=ToolCall(step.tool_call.tool_name, resolved_arguments),
            )

            if resolution_error is not None:
                result = ToolResult(
                    tool_name=tool.name,
                    output={},
                    success=False,
                    error=resolution_error,
                )
                results.append(result)
                completed_steps.append(replace(executable_step, status="failed"))
                status = "failed"
                trace.append(
                    TraceEvent(
                        "argument_resolution.failed",
                        resolution_error,
                        data={"tool": tool.name, "arguments": step.tool_call.arguments},
                    )
                )
                continue

            if "*" not in agent.allowed_tools and tool.name not in agent.allowed_tools:
                result = ToolResult(
                    tool_name=tool.name,
                    output={},
                    success=False,
                    error=f"{agent.name} is not allowed to use {tool.name}.",
                )
                results.append(result)
                completed_steps.append(replace(executable_step, status="failed"))
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
                approval_id = None
                if self._approval_store is not None:
                    approval = self._approval_store.create(
                        approval_type="tool.execute",
                        title=f"Approve tool execution: {tool.name}",
                        reason=decision.reason,
                        payload={
                            "tool_name": tool.name,
                            "arguments": executable_step.tool_call.arguments,
                            "risk_level": tool.risk_level,
                            "requires_approval": tool.requires_approval,
                        },
                        run_id=run_id,
                    )
                    approval_id = approval.id
                result = ToolResult(
                    tool_name=tool.name,
                    output={
                        "policy": decision.reason,
                        "approval_id": approval_id,
                    },
                    success=False,
                    error="Approval required.",
                )
                results.append(result)
                completed_steps.append(
                    replace(executable_step, status="approval_required")
                )
                status = "pending_approval"
                trace.append(
                    TraceEvent(
                        "approval.required",
                        f"Approval required for {tool.name}.",
                        data={"approval_id": approval_id, "tool": tool.name},
                    )
                )
                continue

            execution_context = ToolExecutionContext(
                goal=goal,
                model_name=model_name,
                model_mode=model_mode,
                models=self._models,
                prior_results=tuple(results),
            )
            result = self._tools.execute(
                executable_step.tool_call,
                context=execution_context,
            )
            results.append(result)
            step_status = "completed" if result.success else "failed"
            completed_steps.append(replace(executable_step, status=step_status))
            trace.append(
                TraceEvent(
                    f"step.{step_status}",
                    executable_step.description,
                    data={
                        "tool": result.tool_name,
                        "arguments": executable_step.tool_call.arguments,
                        "output": result.output,
                    },
                )
            )
            if not result.success:
                status = "failed"

        final_plan = replace(plan, steps=tuple(completed_steps))
        synthesis_agent = self._agents.get("synthesis")
        trace.append(
            TraceEvent(
                "synthesis.started",
                f"{synthesis_agent.name} agent is preparing the final response.",
                data={"agent": synthesis_agent.name},
            )
        )
        synthesis = self._synthesizer.synthesize(
            goal=goal,
            plan=final_plan,
            results=tuple(results),
            status=status,
            model_name=model_name,
            model_mode=model_mode,
        )
        synthesis_data = {
            "agent": synthesis_agent.name,
            "source": synthesis.source,
            "model": synthesis.model_name,
        }
        if synthesis.error is not None:
            synthesis_data["error"] = synthesis.error.to_trace_data()
        trace.append(
            TraceEvent(
                "synthesis.completed",
                f"Final response created with {synthesis.source} synthesis.",
                data=synthesis_data,
            )
        )
        final_response = synthesis.text
        memory_candidates = self._suggest_memory(goal, final_response)
        if memory_candidates:
            approval_ids = self._queue_memory_approvals(run_id, memory_candidates)
            trace.append(
                TraceEvent(
                    "memory.suggested",
                    "Memory candidates were queued for approval.",
                    data={
                        "auto_write_requested": self._auto_write_memory,
                        "approval_ids": approval_ids,
                        "candidates": [
                            {
                                "type": candidate.type,
                                "content": candidate.content,
                                "reason": candidate.reason,
                                "source": candidate.source,
                            }
                            for candidate in memory_candidates
                        ],
                    },
                )
            )
            final_response = "\n".join(
                [
                    final_response,
                    "",
                    "Pending memory approvals:",
                    *[
                        f"- {candidate.content} ({candidate.reason}) "
                        f"[approval: {approval_id}]"
                        for candidate, approval_id in zip(
                            memory_candidates,
                            approval_ids,
                        )
                    ],
                ]
            )
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

    def _suggest_memory(self, goal: str, final_response: str):
        """Suggest memory candidates after a run without persisting them."""
        if self._memory_extractor is None:
            return []
        return self._memory_extractor.suggest(goal, final_response)

    def _queue_memory_approvals(
        self,
        run_id: str,
        candidates: list[MemoryCandidate],
    ) -> list[str | None]:
        """Queue memory candidates for explicit user approval."""
        approval_ids: list[str | None] = []
        for candidate in candidates:
            if self._approval_store is None:
                approval_ids.append(None)
                continue
            approval = self._approval_store.create(
                approval_type="memory.add",
                title=f"Save memory: {candidate.type}",
                reason=candidate.reason,
                payload={
                    "memory_type": candidate.type,
                    "content": candidate.content,
                    "source": candidate.source,
                },
                run_id=run_id,
            )
            approval_ids.append(approval.id)
        return approval_ids


def _resolve_arguments(
    arguments: dict[str, object],
    prior_results: tuple[ToolResult, ...],
) -> tuple[dict[str, object], str | None]:
    resolved: dict[str, object] = {}
    for key, value in arguments.items():
        resolved_value, error = _resolve_value(value, prior_results)
        if error is not None:
            return resolved, error
        resolved[key] = resolved_value
    return resolved, None


def _resolve_value(
    value: object,
    prior_results: tuple[ToolResult, ...],
) -> tuple[object, str | None]:
    if isinstance(value, str):
        return _resolve_string(value, prior_results)
    if isinstance(value, list):
        items: list[object] = []
        for item in value:
            resolved_item, error = _resolve_value(item, prior_results)
            if error is not None:
                return None, error
            items.append(resolved_item)
        return items, None
    if isinstance(value, dict):
        resolved_dict: dict[str, object] = {}
        for key, item in value.items():
            resolved_item, error = _resolve_value(item, prior_results)
            if error is not None:
                return None, error
            resolved_dict[str(key)] = resolved_item
        return resolved_dict, None
    return value, None


def _resolve_string(
    value: str,
    prior_results: tuple[ToolResult, ...],
) -> tuple[object, str | None]:
    if not value.startswith("$last."):
        return value, None
    field_name = value.removeprefix("$last.")
    last_result = _last_successful_result(prior_results)
    if last_result is None:
        return None, f"Could not resolve {value}: no prior successful tool result."
    if field_name not in last_result.output:
        return (
            None,
            f"Could not resolve {value}: previous result has no '{field_name}' field.",
        )
    return last_result.output[field_name], None


def _last_successful_result(
    prior_results: tuple[ToolResult, ...],
) -> ToolResult | None:
    for result in reversed(prior_results):
        if result.success:
            return result
    return None
