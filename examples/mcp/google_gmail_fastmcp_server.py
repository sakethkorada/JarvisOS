"""Local FastMCP wrapper for Gmail REST read tools.

This server is an integration-pack example: JarvisOS starts it over stdio as an
MCP server, and the server calls Gmail REST APIs with the OAuth token stored in
JarvisOS auth storage.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen


REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_PATH = REPO_ROOT / "src"
if SRC_PATH.exists():
    sys.path.insert(0, str(SRC_PATH))

from jarvis.integrations.oauth import OAuthManager  # noqa: E402
from jarvis.settings import load_settings  # noqa: E402
from jarvis.storage.auth import AuthStore  # noqa: E402


DEFAULT_API_BASE_URL = "https://gmail.googleapis.com/gmail/v1"
DEFAULT_AUTH_DB = Path(".jarvis/auth.sqlite3")
DEFAULT_HEADERS = ("From", "To", "Subject", "Date")


def main() -> None:
    """Run the FastMCP server over stdio."""
    args = _parse_args()
    mcp = create_mcp_server(
        auth_db=args.auth_db,
        config_path=args.config,
        provider=args.provider,
        api_base_url=args.api_base_url,
    )
    try:
        mcp.run(transport="stdio")
    except TypeError:
        mcp.run()


def create_mcp_server(
    auth_db: Path,
    config_path: Path | None,
    provider: str,
    api_base_url: str,
) -> Any:
    """Create a FastMCP server exposing Gmail read tools."""
    FastMCP = _fastmcp_class()
    mcp = FastMCP("JarvisOS Gmail")

    @mcp.tool()
    def list_recent(max_results: int = 10, label_ids: str = "INBOX") -> dict[str, Any]:
        """List broadly recent Gmail messages for a general recent-email request.

        Do not use for messages related to a named person, event, or topic.
        """
        return _tool_result(
            lambda: list_recent_result(
                auth_db=auth_db,
                config_path=config_path,
                provider=provider,
                api_base_url=api_base_url,
                max_results=max_results,
                label_ids=label_ids,
            )
        )

    @mcp.tool()
    def search_messages(
        query: str,
        max_results: int = 10,
        include_spam_trash: bool = False,
    ) -> dict[str, Any]:
        """Search Gmail for messages related to a named person, event, or topic.

        Use Gmail search syntax for organizations and keywords as well.
        """
        return _tool_result(
            lambda: search_messages_result(
                auth_db=auth_db,
                config_path=config_path,
                provider=provider,
                api_base_url=api_base_url,
                query=query,
                max_results=max_results,
                include_spam_trash=include_spam_trash,
            )
        )

    @mcp.tool()
    def get_message(message_id: str) -> dict[str, Any]:
        """Get one Gmail message by API message id."""
        return _tool_result(
            lambda: get_message_result(
                auth_db=auth_db,
                config_path=config_path,
                provider=provider,
                api_base_url=api_base_url,
                message_id=message_id,
            )
        )

    @mcp.tool()
    def get_thread(thread_id: str, max_messages: int = 10) -> dict[str, Any]:
        """Get a Gmail thread by API thread id."""
        return _tool_result(
            lambda: get_thread_result(
                auth_db=auth_db,
                config_path=config_path,
                provider=provider,
                api_base_url=api_base_url,
                thread_id=thread_id,
                max_messages=max_messages,
            )
        )

    return mcp


def list_recent_text(
    auth_db: Path,
    config_path: Path | None,
    provider: str,
    api_base_url: str,
    max_results: int = 10,
    label_ids: str = "INBOX",
) -> str:
    """Return a readable list of recent Gmail messages."""
    return str(
        list_recent_result(
            auth_db=auth_db,
            config_path=config_path,
            provider=provider,
            api_base_url=api_base_url,
            max_results=max_results,
            label_ids=label_ids,
        )["text"]
    )


def list_recent_result(
    auth_db: Path,
    config_path: Path | None,
    provider: str,
    api_base_url: str,
    max_results: int = 10,
    label_ids: str = "INBOX",
) -> dict[str, Any]:
    """Return normalized records for a broad recent-message read."""
    labels = _split_csv(label_ids)
    return _list_messages_result(
        auth_db=auth_db,
        config_path=config_path,
        provider=provider,
        api_base_url=api_base_url,
        max_results=max_results,
        label_ids=labels,
        title="Recent Gmail messages",
    )


def search_messages_text(
    auth_db: Path,
    config_path: Path | None,
    provider: str,
    api_base_url: str,
    query: str,
    max_results: int = 10,
    include_spam_trash: bool = False,
) -> str:
    """Return a readable list of Gmail messages matching a search query."""
    return str(
        search_messages_result(
            auth_db=auth_db,
            config_path=config_path,
            provider=provider,
            api_base_url=api_base_url,
            query=query,
            max_results=max_results,
            include_spam_trash=include_spam_trash,
        )["text"]
    )


def search_messages_result(
    auth_db: Path,
    config_path: Path | None,
    provider: str,
    api_base_url: str,
    query: str,
    max_results: int = 10,
    include_spam_trash: bool = False,
) -> dict[str, Any]:
    """Return normalized records for a Gmail search."""
    return _list_messages_result(
        auth_db=auth_db,
        config_path=config_path,
        provider=provider,
        api_base_url=api_base_url,
        max_results=max_results,
        query=query,
        include_spam_trash=include_spam_trash,
        title=f'Gmail search results for "{query}"',
    )


def get_message_text(
    auth_db: Path,
    config_path: Path | None,
    provider: str,
    api_base_url: str,
    message_id: str,
) -> str:
    """Return a readable Gmail message summary by message id."""
    return str(
        get_message_result(
            auth_db=auth_db,
            config_path=config_path,
            provider=provider,
            api_base_url=api_base_url,
            message_id=message_id,
        )["text"]
    )


def get_message_result(
    auth_db: Path,
    config_path: Path | None,
    provider: str,
    api_base_url: str,
    message_id: str,
) -> dict[str, Any]:
    """Return one normalized Gmail message record."""
    token = _access_token(auth_db, config_path, provider)
    message = _get_message(api_base_url, token, message_id)
    record = _message_record(message)
    return {
        "text": "Message:\n" + _format_message(message),
        "records": [record],
        "ids": [record["id"]],
        "metadata": {"record_type": "email_message"},
    }


def get_thread_text(
    auth_db: Path,
    config_path: Path | None,
    provider: str,
    api_base_url: str,
    thread_id: str,
    max_messages: int = 10,
) -> str:
    """Return a readable Gmail thread summary by thread id."""
    return str(
        get_thread_result(
            auth_db=auth_db,
            config_path=config_path,
            provider=provider,
            api_base_url=api_base_url,
            thread_id=thread_id,
            max_messages=max_messages,
        )["text"]
    )


def get_thread_result(
    auth_db: Path,
    config_path: Path | None,
    provider: str,
    api_base_url: str,
    thread_id: str,
    max_messages: int = 10,
) -> dict[str, Any]:
    """Return normalized records for one Gmail thread."""
    token = _access_token(auth_db, config_path, provider)
    encoded_thread_id = quote(thread_id, safe="")
    query = urlencode(
        {
            "format": "metadata",
            "metadataHeaders": list(DEFAULT_HEADERS),
        },
        doseq=True,
    )
    data = _request_json(
        f"{api_base_url.rstrip('/')}/users/me/threads/{encoded_thread_id}?{query}",
        token,
    )
    messages = data.get("messages", [])
    if not isinstance(messages, list) or not messages:
        return {
            "text": f"No messages found in thread {thread_id}.",
            "records": [],
            "ids": [],
        }

    limit = max(1, min(int(max_messages), 25))
    lines = [f"Thread {thread_id}:"]
    records: list[dict[str, Any]] = []
    for message in messages[:limit]:
        if isinstance(message, dict):
            lines.append(_format_message(message))
            records.append(_message_record(message))
    return {
        "text": "\n".join(lines),
        "records": records,
        "ids": [record["id"] for record in records],
        "metadata": {"record_type": "email_message", "thread_id": thread_id},
    }


def _list_messages_result(
    auth_db: Path,
    config_path: Path | None,
    provider: str,
    api_base_url: str,
    max_results: int,
    title: str,
    query: str | None = None,
    label_ids: tuple[str, ...] = (),
    include_spam_trash: bool = False,
) -> dict[str, Any]:
    token = _access_token(auth_db, config_path, provider)
    limit = max(1, min(int(max_results), 25))
    params: dict[str, Any] = {
        "maxResults": limit,
        "includeSpamTrash": "true" if include_spam_trash else "false",
    }
    if query:
        params["q"] = query
    if label_ids:
        params["labelIds"] = list(label_ids)
    list_query = urlencode(params, doseq=True)
    data = _request_json(
        f"{api_base_url.rstrip('/')}/users/me/messages?{list_query}",
        token,
    )
    messages = data.get("messages", [])
    if not isinstance(messages, list) or not messages:
        return {"text": f"{title}: no messages found.", "records": [], "ids": []}

    lines = [f"{title}:"]
    records: list[dict[str, Any]] = []
    for item in messages[:limit]:
        if not isinstance(item, dict):
            continue
        message_id = str(item.get("id", "")).strip()
        if not message_id:
            continue
        message = _get_message(api_base_url, token, message_id)
        lines.append(_format_message(message))
        records.append(_message_record(message))
    return {
        "text": "\n".join(lines),
        "records": records,
        "ids": [record["id"] for record in records],
        "metadata": {"record_type": "email_message"},
    }


def _get_message(api_base_url: str, access_token: str, message_id: str) -> dict[str, Any]:
    encoded_message_id = quote(message_id, safe="")
    query = urlencode(
        {
            "format": "metadata",
            "metadataHeaders": list(DEFAULT_HEADERS),
        },
        doseq=True,
    )
    return _request_json(
        f"{api_base_url.rstrip('/')}/users/me/messages/{encoded_message_id}?{query}",
        access_token,
    )


def _format_message(message: dict[str, Any]) -> str:
    headers = _message_headers(message)
    message_id = str(message.get("id", "unknown"))
    thread_id = str(message.get("threadId", "unknown"))
    subject = headers.get("subject") or "(no subject)"
    sender = headers.get("from") or "unknown sender"
    date = headers.get("date") or "unknown date"
    snippet = str(message.get("snippet", "")).strip()
    line = f"- {subject} | from {sender} | {date} | id={message_id} thread={thread_id}"
    if snippet:
        line = f"{line}\n  snippet: {snippet}"
    return line


def _message_record(message: dict[str, Any]) -> dict[str, str]:
    """Map Gmail metadata into the shared record fields."""
    headers = _message_headers(message)
    return {
        "id": str(message.get("id", "unknown")),
        "thread_id": str(message.get("threadId", "unknown")),
        "subject": headers.get("subject") or "(no subject)",
        "sender": headers.get("from") or "unknown sender",
        "received_at": headers.get("date") or "unknown date",
        "snippet": str(message.get("snippet", "")).strip(),
    }


def _message_headers(message: dict[str, Any]) -> dict[str, str]:
    payload = message.get("payload", {})
    headers = payload.get("headers", []) if isinstance(payload, dict) else []
    result: dict[str, str] = {}
    if not isinstance(headers, list):
        return result
    for header in headers:
        if not isinstance(header, dict):
            continue
        name = str(header.get("name", "")).lower()
        value = str(header.get("value", ""))
        if name:
            result[name] = value
    return result


def _split_csv(value: str | None) -> tuple[str, ...]:
    if not value:
        return ()
    return tuple(item.strip() for item in value.split(",") if item.strip())


def _tool_result(handler: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    """Return structured data while keeping auth errors in-band for MCP clients."""
    try:
        return handler()
    except RuntimeError as exc:
        return {"text": f"AUTH_ERROR: {exc}", "records": [], "ids": []}


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="JarvisOS Gmail MCP server.")
    parser.add_argument(
        "--auth-db",
        type=Path,
        default=DEFAULT_AUTH_DB,
        help="Path to the JarvisOS auth SQLite database.",
    )
    parser.add_argument(
        "--config",
        type=Path,
        help=(
            "Optional JarvisOS config path for auth DB and silent token refresh. "
            "When omitted, the global auth profile is used."
        ),
    )
    parser.add_argument(
        "--provider",
        default="google",
        help="OAuth provider name in the JarvisOS auth store.",
    )
    parser.add_argument(
        "--api-base-url",
        default=os.getenv("GOOGLE_GMAIL_API_BASE_URL", DEFAULT_API_BASE_URL),
        help="Gmail API base URL.",
    )
    return parser.parse_args()


def _fastmcp_class() -> Any:
    try:
        from fastmcp import FastMCP

        return FastMCP
    except ImportError:
        try:
            from mcp.server.fastmcp import FastMCP

            return FastMCP
        except ImportError as exc:
            raise RuntimeError(
                "FastMCP is required. Install with: uv pip install fastmcp"
            ) from exc


def _access_token(auth_db: Path, config_path: Path | None, provider: str) -> str:
    auth_store = AuthStore(auth_db)
    provider_settings = None
    should_load_auth_profile = config_path is not None or auth_db == DEFAULT_AUTH_DB
    if should_load_auth_profile:
        settings = load_settings(config_path)
        auth_store = AuthStore(settings.auth.database_path)
        provider_settings = next(
            (
                item
                for item in settings.auth.oauth_providers
                if item.name == provider
            ),
            None,
        )

    record = auth_store.get_token(provider)
    if record is None:
        raise RuntimeError(f"No stored token for provider: {provider}")
    if (
        provider_settings is not None
        and record.refresh_token
        and auth_store.token_is_expired(record)
    ):
        try:
            refreshed = OAuthManager((provider_settings,), auth_store).refresh(
                provider_settings,
                record,
            )
        except Exception as exc:
            raise RuntimeError(
                f"Stored token for {provider} is expired and refresh failed. "
                f"{exc} "
                "Check `python -m jarvis auth debug google --json`, set any "
                "missing client secret environment variable, then retry Gmail."
            ) from exc
        if refreshed is not None:
            return refreshed.access_token
        raise RuntimeError(
            f"Stored token for {provider} is expired and could not be refreshed. "
            "Check `python -m jarvis auth debug google --json`, set any missing "
            "client secret environment variable, then retry Gmail."
        )
    return record.access_token


def _request_json(url: str, access_token: str) -> dict[str, Any]:
    request = Request(
        url,
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {access_token}",
        },
    )
    try:
        with urlopen(request, timeout=30) as response:
            payload = response.read().decode("utf-8")
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"Gmail REST request failed: {detail}") from exc
    except URLError as exc:
        raise RuntimeError(f"Gmail REST request failed: {exc.reason}") from exc

    data = json.loads(payload)
    if not isinstance(data, dict):
        raise RuntimeError("Gmail REST response was not a JSON object.")
    return data


if __name__ == "__main__":
    main()
