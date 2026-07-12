"""Validated execution-graph primitives for JarvisOS plans."""

from __future__ import annotations

from dataclasses import dataclass

from jarvis.contracts import ExecutionPlan, PlanStep


class GraphValidationError(ValueError):
    """Raised when a plan cannot be represented as an executable DAG."""


@dataclass(frozen=True)
class ExecutionGraph:
    """Stable, validated view of an execution plan's dependency graph."""

    plan: ExecutionPlan

    def __post_init__(self) -> None:
        self._validate()

    @property
    def steps(self) -> tuple[PlanStep, ...]:
        """Return the plan steps in their authored order."""
        return self.plan.steps

    def topological_order(self) -> tuple[PlanStep, ...]:
        """Return a deterministic dependency-respecting step order."""
        by_id = {step.id: step for step in self.plan.steps}
        remaining = set(by_id)
        ordered: list[PlanStep] = []
        while remaining:
            next_step = next(
                (
                    step
                    for step in self.plan.steps
                    if step.id in remaining
                    and all(
                        dependency not in remaining for dependency in step.depends_on
                    )
                ),
                None,
            )
            if next_step is None:
                raise GraphValidationError("Execution plan contains a dependency cycle.")
            ordered.append(next_step)
            remaining.remove(next_step.id)
        return tuple(ordered)

    def ready_steps(
        self,
        completed_ids: set[str],
        active_ids: set[str] | None = None,
    ) -> tuple[PlanStep, ...]:
        """Return nodes whose dependencies are complete and not already active."""
        active_ids = active_ids or set()
        return tuple(
            step
            for step in self.topological_order()
            if step.id not in completed_ids
            and step.id not in active_ids
            and all(dependency in completed_ids for dependency in step.depends_on)
        )

    def _validate(self) -> None:
        ids = [step.id for step in self.plan.steps]
        if any(not step_id.strip() for step_id in ids):
            raise GraphValidationError("Every execution step requires a non-empty id.")
        if len(ids) != len(set(ids)):
            raise GraphValidationError("Execution plan contains duplicate step ids.")
        known_ids = set(ids)
        for step in self.plan.steps:
            if step.id in step.depends_on:
                raise GraphValidationError(
                    f"Step {step.id!r} cannot depend on itself."
                )
            unknown = set(step.depends_on) - known_ids
            if unknown:
                names = ", ".join(sorted(unknown))
                raise GraphValidationError(
                    f"Step {step.id!r} depends on unknown step(s): {names}."
                )
        output_keys = [step.output_key for step in self.plan.steps if step.output_key]
        if len(output_keys) != len(set(output_keys)):
            raise GraphValidationError("Execution plan contains duplicate output keys.")
        self.topological_order()
