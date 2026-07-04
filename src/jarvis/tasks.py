"""SQLite-backed local task storage."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

from jarvis.contracts import TaskRecord, TaskStatus, new_id, utc_now


class TaskStore:
    """Stores low-risk local tasks in a SQLite database."""

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def create(
        self,
        title: str,
        source: str = "tool",
        metadata: dict[str, Any] | None = None,
    ) -> TaskRecord:
        """Create an open task and return the stored record."""
        now = utc_now()
        record = TaskRecord(
            id=new_id("task"),
            title=title.strip(),
            status="open",
            source=source,
            created_at=now,
            updated_at=now,
            metadata=metadata or {},
        )
        with closing(self._connect()) as connection:
            with connection:
                connection.execute(
                    """
                    INSERT INTO tasks (
                        id, title, status, source, created_at, updated_at,
                        metadata_json
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record.id,
                        record.title,
                        record.status,
                        record.source,
                        record.created_at,
                        record.updated_at,
                        json.dumps(record.metadata, sort_keys=True),
                    ),
                )
        return record

    def list(self, limit: int = 20) -> list[TaskRecord]:
        """Return recent tasks in newest-first order."""
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT id, title, status, source, created_at, updated_at,
                       metadata_json
                FROM tasks
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [_record_from_row(row) for row in rows]

    def get(self, task_id: str) -> TaskRecord | None:
        """Return one task by id."""
        with closing(self._connect()) as connection:
            row = connection.execute(
                """
                SELECT id, title, status, source, created_at, updated_at,
                       metadata_json
                FROM tasks
                WHERE id = ?
                """,
                (task_id,),
            ).fetchone()
        if row is None:
            return None
        return _record_from_row(row)

    def complete(self, task_id: str) -> TaskRecord:
        """Mark a task as done and return the updated record."""
        return self._set_status(task_id, "done")

    def _set_status(self, task_id: str, status: TaskStatus) -> TaskRecord:
        existing = self.get(task_id)
        if existing is None:
            raise KeyError(f"Unknown task id: {task_id}")
        now = utc_now()
        with closing(self._connect()) as connection:
            with connection:
                connection.execute(
                    """
                    UPDATE tasks
                    SET status = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (status, now, task_id),
                )
        updated = self.get(task_id)
        assert updated is not None
        return updated

    def _ensure_schema(self) -> None:
        with closing(self._connect()) as connection:
            with connection:
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS tasks (
                        id TEXT PRIMARY KEY,
                        title TEXT NOT NULL,
                        status TEXT NOT NULL,
                        source TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        metadata_json TEXT NOT NULL DEFAULT '{}'
                    )
                    """
                )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        return connection


def _record_from_row(row: sqlite3.Row) -> TaskRecord:
    metadata = json.loads(str(row["metadata_json"] or "{}"))
    return TaskRecord(
        id=str(row["id"]),
        title=str(row["title"]),
        status=row["status"],
        source=str(row["source"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
        metadata=metadata,
    )
