"""Deterministic eligibility rules for restart-safe graph continuation."""

from __future__ import annotations

from dataclasses import dataclass

from jarvis.contracts import PlanStep
from jarvis.orchestration.graph import ExecutionGraph
from jarvis.storage.journal import ReconstructedRun


@dataclass(frozen=True)
class ResumePreview:
    """Replay-protected view of a checkpointed execution graph."""

    replay_protected_steps: tuple[PlanStep, ...]
    eligible_steps: tuple[PlanStep, ...]
    blocked_steps: tuple[PlanStep, ...]


def preview_resume(state: ReconstructedRun) -> ResumePreview:
    """Identify unattempted nodes that can safely continue after a restart."""
    graph = ExecutionGraph(state.plan)
    replay_protected = list(state.completed_steps)
    if state.in_flight_step is not None:
        replay_protected.append(state.in_flight_step)
    attempted_ids = {step.id for step in replay_protected}
    successful_ids = {
        step.id
        for step, result in zip(state.completed_steps, state.results)
        if result.success
    }
    pending = tuple(step for step in graph.topological_order() if step.id not in attempted_ids)
    eligible = tuple(
        step for step in pending if all(dependency in successful_ids for dependency in step.depends_on)
    )
    eligible_ids = {step.id for step in eligible}
    blocked = tuple(step for step in pending if step.id not in eligible_ids)
    return ResumePreview(
        replay_protected_steps=tuple(replay_protected),
        eligible_steps=eligible,
        blocked_steps=blocked,
    )
