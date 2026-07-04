"""OAuth token persistence for HTTP integrations."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from jarvis.contracts import utc_now


@dataclass(frozen=True)
class OAuthTokenRecord:
    """Stored token material for one configured OAuth provider."""

    provider: str
    access_token: str
    refresh_token: str | None
    expires_at: str | None
    updated_at: str


class AuthStore:
    """Small SQLite store for integration access tokens."""

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def set_token(
        self,
        provider: str,
        access_token: str,
        refresh_token: str | None = None,
        expires_at: str | None = None,
        preserve_refresh_token: bool = False,
    ) -> OAuthTokenRecord:
        """Persist token material for a provider."""
        provider_name = provider.strip()
        token = access_token.strip()
        if not provider_name:
            raise ValueError("Provider name is required.")
        if not token:
            raise ValueError("Access token is required.")
        updated_at = utc_now()
        stored_refresh_token = refresh_token
        if preserve_refresh_token and stored_refresh_token is None:
            existing = self.get_token(provider_name)
            if existing is not None:
                stored_refresh_token = existing.refresh_token
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO oauth_tokens (
                    provider,
                    access_token,
                    refresh_token,
                    expires_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(provider) DO UPDATE SET
                    access_token = excluded.access_token,
                    refresh_token = excluded.refresh_token,
                    expires_at = excluded.expires_at,
                    updated_at = excluded.updated_at
                """,
                (provider_name, token, stored_refresh_token, expires_at, updated_at),
            )
        return OAuthTokenRecord(
            provider=provider_name,
            access_token=token,
            refresh_token=stored_refresh_token,
            expires_at=expires_at,
            updated_at=updated_at,
        )

    def get_token(self, provider: str) -> OAuthTokenRecord | None:
        """Return stored token material for a provider, if present."""
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT provider, access_token, refresh_token, expires_at, updated_at
                FROM oauth_tokens
                WHERE provider = ?
                """,
                (provider,),
            ).fetchone()
        if row is None:
            return None
        return OAuthTokenRecord(
            provider=str(row["provider"]),
            access_token=str(row["access_token"]),
            refresh_token=row["refresh_token"],
            expires_at=row["expires_at"],
            updated_at=str(row["updated_at"]),
        )

    def list_tokens(self) -> list[OAuthTokenRecord]:
        """List token records without exposing token values in callers."""
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT provider, access_token, refresh_token, expires_at, updated_at
                FROM oauth_tokens
                ORDER BY provider
                """
            ).fetchall()
        return [
            OAuthTokenRecord(
                provider=str(row["provider"]),
                access_token=str(row["access_token"]),
                refresh_token=row["refresh_token"],
                expires_at=row["expires_at"],
                updated_at=str(row["updated_at"]),
            )
            for row in rows
        ]

    def clear_token(self, provider: str) -> bool:
        """Delete a stored token record."""
        with self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM oauth_tokens WHERE provider = ?",
                (provider,),
            )
        return cursor.rowcount > 0

    def token_is_expired(
        self,
        record: OAuthTokenRecord,
        skew_seconds: int = 60,
    ) -> bool:
        """Return whether a token record is expired or near expiry."""
        if record.expires_at is None:
            return False
        expires_at = datetime.fromisoformat(record.expires_at)
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) + timedelta(seconds=skew_seconds) >= expires_at

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS oauth_tokens (
                    provider TEXT PRIMARY KEY,
                    access_token TEXT NOT NULL,
                    refresh_token TEXT,
                    expires_at TEXT,
                    updated_at TEXT NOT NULL
                )
                """
            )

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
