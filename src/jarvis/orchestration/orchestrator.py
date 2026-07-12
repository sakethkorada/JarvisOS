"""Small generic orchestrator for the first runnable JarvisOS slice."""

from __future__ import annotations

from dataclasses import replace

from jarvis.agents import AgentRegistry
from jarvis.contracts import (
    AgentSpec,
    ExecutionPlan,
    MemoryCandidate,
    PlanStep,
    RunResult,
    ToolCall,
    ToolExecutionContext,
    ToolResult,
    ToolSpec,
    TraceEvent,
    new_id,
)
from jarvis.models import ModelRouter
from jarvis.orchestration.arguments import (
    DEFAULT_TOOL_USE_PROMPT,
    ToolUseAgent,
    ToolUseFeedback,
)
from jarvis.orchestration.graph import ExecutionGraph
from jarvis.orchestration.planner import Planner
from jarvis.orchestration.resume import ResumePreview, preview_resume
from jarvis.orchestration.synthesizer import Synthesizer
from jarvis.policies import PolicyEngine
from jarvis.storage.approvals import ApprovalStore
from jarvis.storage.memory import MemoryExtractor
from jarvis.storage.journal import ReconstructedRun, RunJournal
from jarvis.tools.registry import ToolRegistry


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
        tool_use_prompt: str = DEFAULT_TOOL_USE_PROMPT,
        approval_store: ApprovalStore | None = None,
        memory_extractor: MemoryExtractor | None = None,
        auto_write_memory: bool = False,
        journal: RunJournal | None = None,
    ) -> None:
        self._agents = agents
        self._tools = tools
        self._models = models
        self._policies = policies
        self._approval_store = approval_store
        self._memory_extractor = memory_extractor
        self._auto_write_memory = auto_write_memory
        self._journal = journal
        self._planner = Planner(agents, tools, models, planner_prompt)
        self._tool_use_agent = ToolUseAgent(models, tool_use_prompt)
        self._synthesizer = Synthesizer(models, synthesis_prompt)

    def run(
        self,
        goal: str,
        model_name: str | None = None,
        model_mode: str = "balanced",
        _resume_state: ReconstructedRun | None = None,
    ) -> RunResult:
        """Run a goal through the current deterministic execution loop."""
        if _resume_state is None:
            run_id = new_id("run")
            trace: list[TraceEvent] = [
                TraceEvent("run.started", "Run started.", data={"goal": goal}),
            ]
            plan, plan_source, raw_planner_output = self._planner.create_plan(
                goal,
                model_name,
                model_mode,
            )
            planner_model = self._models.resolve_provider_name(
                explicit_provider_name=model_name,
                mode=model_mode,
                role="planner",
            )
            trace.append(
                TraceEvent(
                    "planner.selected",
                    f"Created plan with {plan_source} planner.",
                    data={
                        "model": planner_model,
                        "model_override": model_name,
                        "execution_role": "planner",
                        "source": plan_source,
                        "raw_planner_output": raw_planner_output,
                    },
                )
            )
            completed_steps: list[PlanStep] = []
            results: list[ToolResult] = []
            named_results: dict[str, ToolResult] = {}
            status = "completed"
        else:
            run_id = _resume_state.run_id
            goal = _resume_state.goal
            plan = _resume_state.plan
            trace = list(_resume_state.trace)
            completed_steps = list(_resume_state.completed_steps)
            results = list(_resume_state.results)
            named_results = _named_results(completed_steps, results)
            status = "completed" if _resume_state.status == "planned" else _resume_state.status
            if _resume_state.in_flight_step is not None:
                status = "failed"
            trace.append(
                TraceEvent(
                    "run.resumed",
                    "Run resumed from the latest durable checkpoint.",
                    data={
                        "run_id": run_id,
                        "replay_protected_step_ids": [
                            step.id for step in completed_steps
                        ]
                        + (
                            [_resume_state.in_flight_step.id]
                            if _resume_state.in_flight_step is not None
                            else []
                        ),
                    },
                )
            )

        graph = ExecutionGraph(plan)
        trace.append(
            TraceEvent(
                "graph.validated",
                f"Validated execution graph with {len(graph.steps)} node(s).",
                data={
                    "step_ids": [step.id for step in graph.steps],
                    "topological_order": [step.id for step in graph.topological_order()],
                },
            )
        )
        if _resume_state is None:
            self._checkpoint(run_id, goal, plan, (), (), "planned", trace)
            trace.append(
                TraceEvent(
                    "plan.created",
                    f"Created {len(plan.steps)} step(s).",
                    data={"steps": [step.description for step in plan.steps]},
                )
            )

        attempted_step_ids = {step.id for step in completed_steps}
        if _resume_state is not None and _resume_state.in_flight_step is not None:
            attempted_step_ids.add(_resume_state.in_flight_step.id)
        successful_step_ids = {
            step.id
            for step, result in zip(completed_steps, results)
            if result.success
        }

        for step in graph.topological_order():
            if step.id in attempted_step_ids:
                trace.append(
                    TraceEvent(
                        "resume.step_skipped",
                        f"Skipped previously attempted step {step.id}.",
                        data={"step_id": step.id, "reason": "replay_protected"},
                    )
                )
                self._checkpoint(
                    run_id,
                    goal,
                    plan,
                    tuple(completed_steps),
                    tuple(results),
                    status,
                    trace,
                )
                continue
            if any(dependency not in successful_step_ids for dependency in step.depends_on):
                trace.append(
                    TraceEvent(
                        "resume.step_blocked",
                        f"Skipped {step.id} because a dependency did not succeed.",
                        data={"step_id": step.id, "depends_on": list(step.depends_on)},
                    )
                )
                continue
            self._checkpoint(
                run_id,
                goal,
                plan,
                tuple(completed_steps),
                tuple(results),
                status,
                trace,
            )
            agent = self._agents.get(step.agent_name)
            tool = self._tools.get(step.tool_call.tool_name)
            resolution = self._tool_use_agent.build(
                goal,
                tool,
                step.tool_call.arguments,
                model_name=model_name,
                model_mode=model_mode,
                prior_results=tuple(results),
                named_results=named_results,
            )
            executable_step = replace(
                step,
                tool_call=ToolCall(step.tool_call.tool_name, resolution.arguments),
            )
            trace.append(
                TraceEvent(
                    "tool_use.resolved",
                    f"Resolved arguments for {tool.name}.",
                    data={
                        "tool": tool.name,
                        "arguments": resolution.arguments,
                        "attempts": _tool_use_attempts_data(resolution.attempts),
                    },
                )
            )

            if resolution.error is not None:
                result = ToolResult(
                    tool_name=tool.name,
                    output={},
                    success=False,
                    error=resolution.error,
                )
                results.append(result)
                _record_named_result(named_results, step, result)
                completed_steps.append(replace(executable_step, status="failed"))
                status = "failed"
                trace.append(
                    TraceEvent(
                        "argument_resolution.failed",
                        resolution.error,
                        data={
                            "tool": tool.name,
                            "arguments": step.tool_call.arguments,
                            "error": resolution.error,
                        },
                    )
                )
                self._checkpoint(
                    run_id,
                    goal,
                    plan,
                    tuple(completed_steps),
                    tuple(results),
                    status,
                    trace,
                )
                continue

            if not _agent_can_use_tool(agent, tool):
                result = ToolResult(
                    tool_name=tool.name,
                    output={},
                    success=False,
                    error=f"{agent.name} is not allowed to use {tool.name}.",
                )
                results.append(result)
                _record_named_result(named_results, step, result)
                completed_steps.append(replace(executable_step, status="failed"))
                status = "failed"
                trace.append(
                    TraceEvent(
                        "step.failed",
                        result.error or "Step failed.",
                        data={
                            "tool": result.tool_name,
                            "arguments": executable_step.tool_call.arguments,
                            "output": result.output,
                            "error": result.error,
                        },
                    )
                )
                self._checkpoint(
                    run_id,
                    goal,
                    plan,
                    tuple(completed_steps),
                    tuple(results),
                    status,
                    trace,
                )
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
                status = _merge_run_status(status, "pending_approval")
                trace.append(
                    TraceEvent(
                        "approval.required",
                        f"Approval required for {tool.name}.",
                        data={"approval_id": approval_id, "tool": tool.name},
                    )
                )
                self._checkpoint(
                    run_id,
                    goal,
                    plan,
                    tuple(completed_steps),
                    tuple(results),
                    status,
                    trace,
                )
                continue

            execution_context = ToolExecutionContext(
                goal=goal,
                model_name=model_name,
                model_mode=model_mode,
                models=self._models,
                prior_results=tuple(results),
            )
            self._checkpoint(
                run_id,
                goal,
                plan,
                tuple(completed_steps),
                tuple(results),
                status,
                trace,
                in_flight_step=executable_step,
            )
            result = self._tools.execute(
                executable_step.tool_call,
                context=execution_context,
            )
            if _can_repair_execution_error(tool, result, model_name):
                trace.append(
                    TraceEvent(
                        "tool_use.execution_retry.started",
                        f"Retrying {tool.name} after tool execution failed.",
                        data={
                            "tool": tool.name,
                            "arguments": executable_step.tool_call.arguments,
                            "error": result.error,
                            "output": result.output,
                        },
                    )
                )
                repair_resolution = self._tool_use_agent.build(
                    goal,
                    tool,
                    executable_step.tool_call.arguments,
                    model_name=model_name,
                    model_mode=model_mode,
                    prior_results=tuple(results),
                    named_results=named_results,
                    feedback=ToolUseFeedback(
                        stage="execution",
                        attempted_arguments=executable_step.tool_call.arguments,
                        error=result.error or "Tool execution failed.",
                        output=result.output,
                    ),
                )
                trace.append(
                    TraceEvent(
                        "tool_use.execution_retry.resolved",
                        f"Resolved retry arguments for {tool.name}.",
                        data={
                            "tool": tool.name,
                            "arguments": repair_resolution.arguments,
                            "error": repair_resolution.error,
                            "attempts": _tool_use_attempts_data(
                                repair_resolution.attempts
                            ),
                        },
                    )
                )
                if repair_resolution.error is None:
                    repaired_step = replace(
                        executable_step,
                        tool_call=ToolCall(
                            executable_step.tool_call.tool_name,
                            repair_resolution.arguments,
                        ),
                    )
                    retry_decision = self._policies.evaluate(tool)
                    trace.append(
                        TraceEvent(
                            "policy.evaluated",
                            retry_decision.reason,
                            data={
                                "tool": tool.name,
                                "status": retry_decision.status,
                                "retry": True,
                            },
                        )
                    )
                    if retry_decision.allowed:
                        result = self._tools.execute(
                            repaired_step.tool_call,
                            context=execution_context,
                        )
                        executable_step = repaired_step
                        trace.append(
                            TraceEvent(
                                "tool_use.execution_retry.completed",
                                f"Retried {tool.name}.",
                                data={
                                    "tool": tool.name,
                                    "arguments": repaired_step.tool_call.arguments,
                                    "success": result.success,
                                    "error": result.error,
                                    "output": result.output,
                                },
                            )
                        )
            results.append(result)
            _record_named_result(named_results, executable_step, result)
            step_status = "completed" if result.success else "failed"
            completed_steps.append(replace(executable_step, status=step_status))
            if result.success:
                successful_step_ids.add(executable_step.id)
            trace.append(
                TraceEvent(
                    f"step.{step_status}",
                    executable_step.description,
                    data={
                        "tool": result.tool_name,
                        "arguments": executable_step.tool_call.arguments,
                        "output": result.output,
                        "error": result.error,
                    },
                )
            )
            if not result.success:
                status = _merge_run_status(status, "failed")
            self._checkpoint(
                run_id,
                goal,
                plan,
                tuple(completed_steps),
                tuple(results),
                status,
                trace,
            )

        final_plan = _plan_with_progress(plan, completed_steps, results)
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
            available_tools=self._tools.available_tools(),
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
        self._checkpoint(
            run_id,
            goal,
            final_plan,
            tuple(completed_steps),
            tuple(results),
            status,
            trace,
        )

        return RunResult(
            run_id=run_id,
            goal=goal,
            plan=final_plan,
            step_results=tuple(results),
            trace=tuple(trace),
            final_response=final_response,
            status=status,
        )

    def resume(
        self,
        run_id: str,
        model_name: str | None = None,
        model_mode: str = "balanced",
    ) -> RunResult:
        """Continue unattempted eligible nodes from a durable checkpoint."""
        state = self._load_resume_state(run_id)
        preview = preview_resume(state)
        if not preview.eligible_steps:
            raise ValueError("Run has no eligible unattempted steps to resume.")
        return self.run(
            state.goal,
            model_name=model_name,
            model_mode=model_mode,
            _resume_state=state,
        )

    def resume_preview(self, run_id: str) -> ResumePreview:
        """Return replay-protected eligibility without executing a tool."""
        return preview_resume(self._load_resume_state(run_id))

    def _load_resume_state(self, run_id: str) -> ReconstructedRun:
        if self._journal is None:
            raise ValueError("Run resume requires trace/checkpoint storage.")
        state = self._journal.reconstruct(run_id)
        if state is None:
            raise ValueError(f"No checkpoint found for run: {run_id}.")
        if state.status == "completed":
            raise ValueError("Completed runs cannot be resumed.")
        return state

    def _checkpoint(
        self,
        run_id: str,
        goal: str,
        plan,
        completed_steps: tuple[PlanStep, ...],
        results: tuple[ToolResult, ...],
        status: str,
        trace: list[TraceEvent],
        in_flight_step: PlanStep | None = None,
    ) -> None:
        """Persist a checkpoint when a journal was configured."""
        if self._journal is None:
            return
        self._journal.checkpoint(
            run_id=run_id,
            goal=goal,
            plan=plan,
            completed_steps=completed_steps,
            results=results,
            status=status,
            trace=tuple(trace),
            in_flight_step=in_flight_step,
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


def _agent_can_use_tool(agent: AgentSpec, tool: ToolSpec) -> bool:
    """Return whether an agent can execute a tool spec."""
    if "*" in agent.allowed_tools or tool.name in agent.allowed_tools:
        return True
    capability = tool.capability
    return capability is not None and capability.domain in agent.capability_domains


def _merge_run_status(current: str, candidate: str) -> str:
    """Preserve the most actionable status observed during a run."""
    if current == "pending_approval" or candidate == "pending_approval":
        return "pending_approval"
    if current == "failed" or candidate == "failed":
        return "failed"
    return "completed"


def _record_named_result(
    named_results: dict[str, ToolResult],
    step: PlanStep,
    result: ToolResult,
) -> None:
    """Index a step result by id and optional output key for later bindings."""
    if not result.success:
        return
    named_results[step.id] = result
    if step.output_key:
        named_results[step.output_key] = result


def _named_results(
    completed_steps: list[PlanStep],
    results: list[ToolResult],
) -> dict[str, ToolResult]:
    """Rebuild successful named results from checkpointed paired progress."""
    named: dict[str, ToolResult] = {}
    for step, result in zip(completed_steps, results):
        _record_named_result(named, step, result)
    return named


def _plan_with_progress(
    plan: ExecutionPlan,
    completed_steps: list[PlanStep],
    results: list[ToolResult],
) -> ExecutionPlan:
    """Preserve the full graph while reflecting persisted node statuses."""
    by_id = {
        step.id: (
            replace(step, status="completed") if result.success else step
        )
        for step, result in zip(completed_steps, results)
    }
    return replace(
        plan,
        steps=tuple(by_id.get(step.id, step) for step in plan.steps),
    )


def _can_repair_execution_error(
    tool: ToolSpec,
    result: ToolResult,
    model_name: str | None,
) -> bool:
    """Return whether a failed tool execution can be safely retried."""
    if result.success or model_name == "fake-local":
        return False
    if not _looks_argument_repairable(result.error):
        return False
    if tool.requires_approval or tool.risk_level != "low":
        return False
    capability = tool.capability
    return capability is not None and capability.read_only


def _looks_argument_repairable(error: str | None) -> bool:
    """Return whether an execution error is likely fixable by new arguments."""
    if not error:
        return False
    normalized = error.lower()
    non_repairable_markers = (
        "auth",
        "oauth",
        "token",
        "client_secret",
        "permission",
        "forbidden",
        "unauthorized",
        "approval",
        "timeout",
        "timed out",
        "did not respond",
    )
    return not any(marker in normalized for marker in non_repairable_markers)


def _tool_use_attempts_data(attempts) -> list[dict[str, object]]:
    """Return trace-safe data for ToolUseAgent attempts."""
    return [
        {
            "attempt": attempt.attempt,
            "arguments": attempt.arguments,
            "error": attempt.error,
            "raw_output": attempt.raw_output,
        }
        for attempt in attempts
    ]
