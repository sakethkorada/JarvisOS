"""SQLite-backed approval queue for user-reviewed actions."""

from __future__ import annotations

import json
import re
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

from jarvis.contracts import ApprovalRecord, ApprovalStatus, MemoryType, new_id, utc_now
from jarvis.memory import MemoryStore


class ApprovalStore:
    """Stores pending approval items and records user decisions."""

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def create(
        self,
        approval_type: str,
        title: str,
        reason: str,
        payload: dict[str, Any],
        run_id: str | None = None,
    ) -> ApprovalRecord:
        """Create a pending approval record."""
        record = ApprovalRecord(
            id=new_id("appr"),
            type=approval_type,
            status="pending",
            title=title,
            reason=reason,
            payload=payload,
            run_id=run_id,
            created_at=utc_now(),
        )
        with closing(self._connect()) as connection:
            with connection:
                connection.execute(
                    """
                    INSERT INTO approvals (
                        id, type, status, title, reason, payload_json, run_id,
                        created_at, decided_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record.id,
                        record.type,
                        record.status,
                        record.title,
                        record.reason,
                        json.dumps(record.payload, sort_keys=True),
                        record.run_id,
                        record.created_at,
                        record.decided_at,
                    ),
                )
        return record

    def list(
        self,
        status: ApprovalStatus | None = "pending",
        limit: int = 20,
    ) -> list[ApprovalRecord]:
        """Return approval records in newest-first order."""
        with closing(self._connect()) as connection:
            if status is None:
                rows = connection.execute(
                    """
                    SELECT *
                    FROM approvals
                    ORDER BY created_at DESC
                    LIMIT ?
                    """,
                    (limit,),
                ).fetchall()
            else:
                rows = connection.execute(
                    """
                    SELECT *
                    FROM approvals
                    WHERE status = ?
                    ORDER BY created_at DESC
                    LIMIT ?
                    """,
                    (status, limit),
                ).fetchall()
        return [_record_from_row(row) for row in rows]

    def get(self, approval_id: str) -> ApprovalRecord | None:
        """Return one approval record by id."""
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT * FROM approvals WHERE id = ?",
                (approval_id,),
            ).fetchone()
        if row is None:
            return None
        return _record_from_row(row)

    def decide(
        self,
        approval_id: str,
        status: ApprovalStatus,
    ) -> ApprovalRecord:
        """Mark an approval as approved or rejected."""
        if status not in ("approved", "rejected"):
            raise ValueError("Approval decision must be approved or rejected.")
        record = self.get(approval_id)
        if record is None:
            raise KeyError(f"Unknown approval id: {approval_id}")
        if record.status != "pending":
            raise ValueError(f"Approval {approval_id} is already {record.status}.")

        decided_at = utc_now()
        with closing(self._connect()) as connection:
            with connection:
                connection.execute(
                    """
                    UPDATE approvals
                    SET status = ?, decided_at = ?
                    WHERE id = ?
                    """,
                    (status, decided_at, approval_id),
                )
        updated = self.get(approval_id)
        assert updated is not None
        return updated

    def _ensure_schema(self) -> None:
        with closing(self._connect()) as connection:
            with connection:
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS approvals (
                        id TEXT PRIMARY KEY,
                        type TEXT NOT NULL,
                        status TEXT NOT NULL,
                        title TEXT NOT NULL,
                        reason TEXT NOT NULL,
                        payload_json TEXT NOT NULL,
                        run_id TEXT,
                        created_at TEXT NOT NULL,
                        decided_at TEXT
                    )
                    """
                )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        return connection


def apply_approved_record(
    record: ApprovalRecord,
    memory_store: MemoryStore,
) -> str:
    """Apply side effects for an approved record when supported."""
    if record.type == "memory.add":
        content = str(record.payload["content"])
        memory_type = _memory_type(record.payload.get("memory_type"))
        duplicate = _find_duplicate_memory(memory_store, content, memory_type)
        if duplicate is not None:
            return f"Memory already exists; skipped duplicate {duplicate.id}."
        memory_store.add(
            content=content,
            memory_type=memory_type,
            source=str(record.payload.get("source", "approval")),
            metadata={
                "approval_id": record.id,
                "reason": record.reason,
                "run_id": record.run_id,
            },
        )
        return "Memory saved."
    return "Approval recorded. This item does not have an apply action yet."


def _record_from_row(row: sqlite3.Row) -> ApprovalRecord:
    payload = json.loads(str(row["payload_json"] or "{}"))
    return ApprovalRecord(
        id=str(row["id"]),
        type=str(row["type"]),
        status=row["status"],
        title=str(row["title"]),
        reason=str(row["reason"]),
        payload=payload,
        run_id=row["run_id"],
        created_at=str(row["created_at"]),
        decided_at=row["decided_at"],
    )


def _memory_type(value: Any) -> MemoryType:
    if value in ("preference", "fact", "note", "context"):
        return value
    return "note"


def _find_duplicate_memory(
    memory_store: MemoryStore,
    content: str,
    memory_type: MemoryType,
):
    normalized_content = _normalize_memory_content(content)
    for record in memory_store.list(limit=200):
        if record.type != memory_type:
            continue
        if _normalize_memory_content(record.content) == normalized_content:
            return record
    return None


def _normalize_memory_content(content: str) -> str:
    normalized = content.lower().strip()
    normalized = re.sub(r"[^a-z0-9\s]", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    replacements = (
        ("remember that i prefer", "user prefers"),
        ("remember that i", "user"),
        ("my preference is", "user prefers"),
        ("i prefer", "user prefers"),
    )
    for old, new in replacements:
        if normalized.startswith(old):
            normalized = new + normalized[len(old) :]
            normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized
