"""SQLite checkpoints for reconstructing an in-progress run."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jarvis.contracts import (
    ExecutionPlan,
    PlanStep,
    ToolCall,
    ToolResult,
    TraceEvent,
    utc_now,
)


@dataclass(frozen=True)
class RunCheckpoint:
    """One durable snapshot of graph progress."""

    run_id: str
    checkpoint_index: int
    status: str
    payload: dict[str, Any]
    created_at: str


@dataclass(frozen=True)
class ReconstructedRun:
    """Checkpoint state required to continue a previously started run."""

    run_id: str
    goal: str
    plan: ExecutionPlan
    completed_steps: tuple[PlanStep, ...]
    results: tuple[ToolResult, ...]
    status: str
    trace: tuple[TraceEvent, ...]
    in_flight_step: PlanStep | None = None


class RunJournal:
    """Stores append-only run checkpoints in a SQLite database."""

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def checkpoint(
        self,
        run_id: str,
        goal: str,
        plan: ExecutionPlan,
        completed_steps: tuple[PlanStep, ...],
        results: tuple[ToolResult, ...],
        status: str,
        trace: tuple[TraceEvent, ...],
        in_flight_step: PlanStep | None = None,
    ) -> RunCheckpoint:
        """Append a reconstructable snapshot of the current run state."""
        payload = {
            "goal": goal,
            "plan": _plan_data(plan),
            "completed_steps": [_step_data(step) for step in completed_steps],
            "results": [_result_data(result) for result in results],
            "trace_length": len(trace),
            "trace": [_trace_data(event) for event in trace],
            "in_flight_step": _step_data(in_flight_step) if in_flight_step else None,
        }
        created_at = utc_now()
        with closing(self._connect()) as connection:
            with connection:
                row = connection.execute(
                    "SELECT COALESCE(MAX(checkpoint_index), -1) + 1 AS next_index "
                    "FROM run_checkpoints WHERE run_id = ?",
                    (run_id,),
                ).fetchone()
                checkpoint_index = int(row["next_index"])
                connection.execute(
                    "INSERT INTO run_checkpoints "
                    "(run_id, checkpoint_index, status, payload_json, created_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (
                        run_id,
                        checkpoint_index,
                        status,
                        json.dumps(payload, sort_keys=True, default=str),
                        created_at,
                    ),
                )
        return RunCheckpoint(run_id, checkpoint_index, status, payload, created_at)

    def latest(self, run_id: str) -> RunCheckpoint | None:
        """Return the newest checkpoint for a run, if one exists."""
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT run_id, checkpoint_index, status, payload_json, created_at "
                "FROM run_checkpoints WHERE run_id = ? "
                "ORDER BY checkpoint_index DESC LIMIT 1",
                (run_id,),
            ).fetchone()
        if row is None:
            return None
        return RunCheckpoint(
            run_id=str(row["run_id"]),
            checkpoint_index=int(row["checkpoint_index"]),
            status=str(row["status"]),
            payload=json.loads(str(row["payload_json"])),
            created_at=str(row["created_at"]),
        )

    def reconstruct(self, run_id: str) -> ReconstructedRun | None:
        """Rebuild the latest resumable state for a run id."""
        checkpoint = self.latest(run_id)
        if checkpoint is None:
            return None
        payload = checkpoint.payload
        plan_data = payload.get("plan", {})
        if not isinstance(plan_data, dict):
            raise ValueError("Checkpoint plan payload is invalid.")
        plan = _plan_from_data(plan_data)
        completed_data = payload.get("completed_steps", [])
        result_data = payload.get("results", [])
        trace_data = payload.get("trace", [])
        if not isinstance(completed_data, list) or not isinstance(result_data, list):
            raise ValueError("Checkpoint progress payload is invalid.")
        if len(completed_data) != len(result_data):
            raise ValueError("Checkpoint progress/result counts do not match.")
        in_flight_data = payload.get("in_flight_step")
        if in_flight_data is not None and not isinstance(in_flight_data, dict):
            raise ValueError("Checkpoint in-flight step is invalid.")
        return ReconstructedRun(
            run_id=checkpoint.run_id,
            goal=str(payload.get("goal", plan.goal)),
            plan=plan,
            completed_steps=tuple(_step_from_data(item) for item in completed_data),
            results=tuple(_result_from_data(item) for item in result_data),
            status=checkpoint.status,
            trace=tuple(
                _trace_from_data(item) for item in trace_data if isinstance(item, dict)
            ),
            in_flight_step=(
                _step_from_data(in_flight_data) if in_flight_data is not None else None
            ),
        )

    def _ensure_schema(self) -> None:
        with closing(self._connect()) as connection:
            with connection:
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS run_checkpoints ("
                    "run_id TEXT NOT NULL, checkpoint_index INTEGER NOT NULL, "
                    "status TEXT NOT NULL, payload_json TEXT NOT NULL, "
                    "created_at TEXT NOT NULL, "
                    "PRIMARY KEY (run_id, checkpoint_index))"
                )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        return connection


def _plan_data(plan: ExecutionPlan) -> dict[str, Any]:
    return {"goal": plan.goal, "steps": [_step_data(step) for step in plan.steps]}


def _step_data(step: PlanStep) -> dict[str, Any]:
    return {
        "id": step.id,
        "agent_name": step.agent_name,
        "tool_name": step.tool_call.tool_name,
        "arguments": step.tool_call.arguments,
        "description": step.description,
        "status": step.status,
        "depends_on": list(step.depends_on),
        "output_key": step.output_key,
    }


def _result_data(result: ToolResult) -> dict[str, Any]:
    return {
        "tool_name": result.tool_name,
        "output": result.output,
        "success": result.success,
        "error": result.error,
    }


def _trace_data(event: TraceEvent) -> dict[str, Any]:
    return {
        "event_type": event.event_type,
        "message": event.message,
        "timestamp": event.timestamp,
        "data": event.data,
    }


def _plan_from_data(data: dict[str, Any]) -> ExecutionPlan:
    steps = data.get("steps", [])
    if not isinstance(steps, list):
        raise ValueError("Checkpoint plan steps are invalid.")
    return ExecutionPlan(
        goal=str(data.get("goal", "")),
        steps=tuple(_step_from_data(item) for item in steps),
    )


def _step_from_data(data: Any) -> PlanStep:
    if not isinstance(data, dict):
        raise ValueError("Checkpoint step is invalid.")
    arguments = data.get("arguments", {})
    depends_on = data.get("depends_on", [])
    if not isinstance(arguments, dict) or not isinstance(depends_on, list):
        raise ValueError("Checkpoint step arguments are invalid.")
    return PlanStep(
        id=str(data.get("id", "")),
        agent_name=str(data.get("agent_name", "")),
        tool_call=ToolCall(str(data.get("tool_name", "")), arguments),
        description=str(data.get("description", "")),
        status=str(data.get("status", "pending")),  # type: ignore[arg-type]
        depends_on=tuple(str(item) for item in depends_on),
        output_key=(
            str(data["output_key"])
            if data.get("output_key") is not None
            else None
        ),
    )


def _result_from_data(data: Any) -> ToolResult:
    if not isinstance(data, dict):
        raise ValueError("Checkpoint result is invalid.")
    output = data.get("output", {})
    if not isinstance(output, dict):
        raise ValueError("Checkpoint result output is invalid.")
    return ToolResult(
        tool_name=str(data.get("tool_name", "")),
        output=output,
        success=bool(data.get("success", False)),
        error=str(data["error"]) if data.get("error") is not None else None,
    )


def _trace_from_data(data: dict[str, Any]) -> TraceEvent:
    event_data = data.get("data", {})
    return TraceEvent(
        event_type=str(data.get("event_type", "checkpoint.unknown")),
        message=str(data.get("message", "")),
        timestamp=str(data.get("timestamp", utc_now())),
        data=event_data if isinstance(event_data, dict) else {},
    )
