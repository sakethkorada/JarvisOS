"""SQLite-backed trace persistence for JarvisOS runs."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jarvis.contracts import RunResult, TraceEvent


@dataclass(frozen=True)
class TraceSummary:
    """Compact stored summary of one run."""

    run_id: str
    goal: str
    status: str
    started_at: str
    finished_at: str
    selected_model: str | None


@dataclass(frozen=True)
class StoredTrace:
    """Stored run summary plus ordered trace events."""

    summary: TraceSummary
    events: tuple[TraceEvent, ...]
    final_response: str


class TraceStore:
    """Stores and retrieves run traces from SQLite."""

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def save_run(self, result: RunResult) -> None:
        """Persist a complete run result and its event timeline."""
        started_at = result.trace[0].timestamp if result.trace else ""
        finished_at = result.trace[-1].timestamp if result.trace else started_at
        selected_model = _selected_model_from_trace(result.trace)
        with closing(self._connect()) as connection:
            with connection:
                connection.execute(
                    """
                    INSERT OR REPLACE INTO runs (
                        run_id, goal, status, started_at, finished_at,
                        selected_model, final_response
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        result.run_id,
                        result.goal,
                        result.status,
                        started_at,
                        finished_at,
                        selected_model,
                        result.final_response,
                    ),
                )
                connection.execute(
                    "DELETE FROM trace_events WHERE run_id = ?",
                    (result.run_id,),
                )
                for index, event in enumerate(result.trace):
                    connection.execute(
                        """
                        INSERT INTO trace_events (
                            run_id, event_index, event_type, message, timestamp,
                            data_json
                        )
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            result.run_id,
                            index,
                            event.event_type,
                            event.message,
                            event.timestamp,
                            json.dumps(event.data, sort_keys=True),
                        ),
                    )

    def list_runs(self, limit: int = 20) -> list[TraceSummary]:
        """Return recent run summaries."""
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT run_id, goal, status, started_at, finished_at, selected_model
                FROM runs
                ORDER BY started_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [_summary_from_row(row) for row in rows]

    def get_run(self, run_id: str) -> StoredTrace | None:
        """Return a stored trace by run id."""
        with closing(self._connect()) as connection:
            run_row = connection.execute(
                """
                SELECT run_id, goal, status, started_at, finished_at,
                       selected_model, final_response
                FROM runs
                WHERE run_id = ?
                """,
                (run_id,),
            ).fetchone()
            if run_row is None:
                return None
            event_rows = connection.execute(
                """
                SELECT event_type, message, timestamp, data_json
                FROM trace_events
                WHERE run_id = ?
                ORDER BY event_index ASC
                """,
                (run_id,),
            ).fetchall()

        return StoredTrace(
            summary=_summary_from_row(run_row),
            events=tuple(_event_from_row(row) for row in event_rows),
            final_response=str(run_row["final_response"]),
        )

    def _ensure_schema(self) -> None:
        with closing(self._connect()) as connection:
            with connection:
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS runs (
                        run_id TEXT PRIMARY KEY,
                        goal TEXT NOT NULL,
                        status TEXT NOT NULL,
                        started_at TEXT NOT NULL,
                        finished_at TEXT NOT NULL,
                        selected_model TEXT,
                        final_response TEXT NOT NULL
                    )
                    """
                )
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS trace_events (
                        run_id TEXT NOT NULL,
                        event_index INTEGER NOT NULL,
                        event_type TEXT NOT NULL,
                        message TEXT NOT NULL,
                        timestamp TEXT NOT NULL,
                        data_json TEXT NOT NULL,
                        PRIMARY KEY (run_id, event_index),
                        FOREIGN KEY (run_id) REFERENCES runs(run_id)
                    )
                    """
                )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        return connection


def _summary_from_row(row: sqlite3.Row) -> TraceSummary:
    return TraceSummary(
        run_id=str(row["run_id"]),
        goal=str(row["goal"]),
        status=str(row["status"]),
        started_at=str(row["started_at"]),
        finished_at=str(row["finished_at"]),
        selected_model=row["selected_model"],
    )


def _event_from_row(row: sqlite3.Row) -> TraceEvent:
    data: dict[str, Any] = json.loads(str(row["data_json"] or "{}"))
    return TraceEvent(
        event_type=str(row["event_type"]),
        message=str(row["message"]),
        timestamp=str(row["timestamp"]),
        data=data,
    )


def _selected_model_from_trace(events: tuple[TraceEvent, ...]) -> str | None:
    for event in events:
        if event.event_type == "planner.selected":
            model = event.data.get("model")
            if isinstance(model, str):
                return model
        if event.event_type == "model.selected":
            prefix = "Using model provider "
            if event.message.startswith(prefix):
                return event.message.removeprefix(prefix).rstrip(".")
    return None
