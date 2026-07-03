"""SQLite-backed memory storage and suggestion helpers."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

from jarvis.contracts import MemoryCandidate, MemoryRecord, MemoryType, new_id, utc_now


class MemoryStore:
    """Stores and searches durable memories in a local SQLite database."""

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def add(
        self,
        content: str,
        memory_type: MemoryType = "note",
        source: str = "manual",
        metadata: dict[str, Any] | None = None,
    ) -> MemoryRecord:
        """Persist a memory record and return the stored value."""
        now = utc_now()
        record = MemoryRecord(
            id=new_id("mem"),
            type=memory_type,
            content=content.strip(),
            source=source,
            created_at=now,
            updated_at=now,
            metadata=metadata or {},
        )
        with closing(self._connect()) as connection:
            with connection:
                connection.execute(
                    """
                    INSERT INTO memories (
                        id, type, content, source, created_at, updated_at,
                        metadata_json
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record.id,
                        record.type,
                        record.content,
                        record.source,
                        record.created_at,
                        record.updated_at,
                        json.dumps(record.metadata, sort_keys=True),
                    ),
                )
        return record

    def search(self, query: str, limit: int = 5) -> list[MemoryRecord]:
        """Search memories with a simple case-insensitive text match."""
        normalized = query.strip()
        if not normalized:
            return self.list(limit=limit)

        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT id, type, content, source, created_at, updated_at, metadata_json
                FROM memories
                ORDER BY updated_at DESC
                """,
            ).fetchall()
        records = [_record_from_row(row) for row in rows]
        return _filter_records(records, normalized, limit)

    def list(self, limit: int = 20) -> list[MemoryRecord]:
        """Return the most recently updated memories."""
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT id, type, content, source, created_at, updated_at, metadata_json
                FROM memories
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [_record_from_row(row) for row in rows]

    def _ensure_schema(self) -> None:
        with closing(self._connect()) as connection:
            with connection:
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS memories (
                        id TEXT PRIMARY KEY,
                        type TEXT NOT NULL,
                        content TEXT NOT NULL,
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


class MemoryExtractor:
    """Suggests memory candidates without persisting them automatically."""

    def suggest(self, goal: str, final_response: str) -> list[MemoryCandidate]:
        """Return conservative memory suggestions from a completed run."""
        del final_response
        text = goal.lower().strip()
        candidates: list[MemoryCandidate] = []
        preference_markers = ("i prefer", "my preference is", "remember that i")
        if any(marker in text for marker in preference_markers):
            candidates.append(
                MemoryCandidate(
                    type="preference",
                    content=goal.strip(),
                    reason="The user stated an explicit preference.",
                )
            )
        return candidates


def _record_from_row(row: sqlite3.Row) -> MemoryRecord:
    metadata = json.loads(str(row["metadata_json"] or "{}"))
    return MemoryRecord(
        id=str(row["id"]),
        type=row["type"],
        content=str(row["content"]),
        source=str(row["source"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
        metadata=metadata,
    )


def _filter_records(
    records: list[MemoryRecord],
    query: str,
    limit: int,
) -> list[MemoryRecord]:
    """Return records with at least one query token in searchable text."""
    tokens = [token for token in query.lower().split() if token]
    matches: list[MemoryRecord] = []
    for record in records:
        searchable = " ".join(
            [
                record.type,
                record.content,
                record.source,
                json.dumps(record.metadata, sort_keys=True),
            ]
        ).lower()
        if any(token in searchable for token in tokens):
            matches.append(record)
        if len(matches) >= limit:
            break
    return matches
