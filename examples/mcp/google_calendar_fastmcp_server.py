"""Local FastMCP wrapper for Google Calendar REST read tools.

This server is an integration-pack example: JarvisOS starts it over stdio as an
MCP server, and the server calls Google Calendar REST APIs with the OAuth token
stored in JarvisOS auth storage.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any
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


DEFAULT_API_BASE_URL = "https://www.googleapis.com/calendar/v3"


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
    """Create a FastMCP server exposing Google Calendar read tools."""
    FastMCP = _fastmcp_class()
    mcp = FastMCP("JarvisOS Google Calendar")

    @mcp.tool()
    def list_calendars(max_results: int = 50) -> str:
        """List calendars available to the authenticated Google user."""
        return list_calendars_text(
            auth_db=auth_db,
            config_path=config_path,
            provider=provider,
            api_base_url=api_base_url,
            max_results=max_results,
        )

    @mcp.tool()
    def list_events(
        calendar_id: str = "primary",
        start_time: str | None = None,
        end_time: str | None = None,
        max_results: int = 10,
    ) -> str:
        """List events from a Google Calendar using optional time bounds."""
        return list_events_text(
            auth_db=auth_db,
            config_path=config_path,
            provider=provider,
            api_base_url=api_base_url,
            calendar_id=calendar_id,
            start_time=start_time,
            end_time=end_time,
            max_results=max_results,
        )

    return mcp


def list_calendars_text(
    auth_db: Path,
    config_path: Path | None,
    provider: str,
    api_base_url: str,
    max_results: int = 50,
) -> str:
    """Return a readable list of Google calendars from REST."""
    token = _access_token(auth_db, config_path, provider)
    query = urlencode({"maxResults": max(1, min(int(max_results), 250))})
    data = _request_json(f"{api_base_url.rstrip('/')}/users/me/calendarList?{query}", token)
    calendars = data.get("items", [])
    if not isinstance(calendars, list) or not calendars:
        return "No calendars found."

    lines = ["Calendars:"]
    for calendar in calendars:
        if not isinstance(calendar, dict):
            continue
        summary = str(calendar.get("summary", "Untitled calendar"))
        calendar_id = str(calendar.get("id", "unknown"))
        role = str(calendar.get("accessRole", "unknown"))
        primary = " primary" if calendar.get("primary") is True else ""
        lines.append(f"- {summary} ({calendar_id}, {role}{primary})")
    return "\n".join(lines)


def list_events_text(
    auth_db: Path,
    config_path: Path | None,
    provider: str,
    api_base_url: str,
    calendar_id: str = "primary",
    start_time: str | None = None,
    end_time: str | None = None,
    max_results: int = 10,
) -> str:
    """Return a readable list of Google Calendar events from REST."""
    token = _access_token(auth_db, config_path, provider)
    query: dict[str, Any] = {
        "singleEvents": "true",
        "orderBy": "startTime",
        "maxResults": max(1, min(int(max_results), 50)),
    }
    if start_time:
        query["timeMin"] = start_time
    if end_time:
        query["timeMax"] = end_time
    encoded_calendar_id = quote(calendar_id, safe="")
    encoded_query = urlencode(query)
    url = f"{api_base_url.rstrip('/')}/calendars/{encoded_calendar_id}/events?{encoded_query}"
    data = _request_json(url, token)
    events = data.get("items", [])
    if not isinstance(events, list) or not events:
        return "No events found."

    lines = [f"Events for {calendar_id}:"]
    for event in events:
        if not isinstance(event, dict):
            continue
        summary = str(event.get("summary", "Untitled event"))
        start = _event_time(event.get("start"))
        end = _event_time(event.get("end"))
        event_id = str(event.get("id", "unknown"))
        lines.append(f"- {summary} ({start} to {end}, id={event_id})")
    return "\n".join(lines)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="JarvisOS Google Calendar MCP server.")
    parser.add_argument(
        "--auth-db",
        type=Path,
        default=Path(".jarvis/auth.sqlite3"),
        help="Path to the JarvisOS auth SQLite database.",
    )
    parser.add_argument(
        "--config",
        type=Path,
        help="Optional JarvisOS config path for auth DB and silent token refresh.",
    )
    parser.add_argument(
        "--provider",
        default="google",
        help="OAuth provider name in the JarvisOS auth store.",
    )
    parser.add_argument(
        "--api-base-url",
        default=os.getenv("GOOGLE_CALENDAR_API_BASE_URL", DEFAULT_API_BASE_URL),
        help="Google Calendar API base URL.",
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
    if config_path is not None:
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
        refreshed = OAuthManager((provider_settings,), auth_store).refresh(
            provider_settings,
            record,
        )
        if refreshed is not None:
            return refreshed.access_token
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
        raise RuntimeError(f"Google Calendar REST request failed: {detail}") from exc
    except URLError as exc:
        raise RuntimeError(f"Google Calendar REST request failed: {exc.reason}") from exc

    data = json.loads(payload)
    if not isinstance(data, dict):
        raise RuntimeError("Google Calendar REST response was not a JSON object.")
    return data


def _event_time(value: Any) -> str:
    if not isinstance(value, dict):
        return "unknown"
    return str(value.get("dateTime") or value.get("date") or "unknown")


if __name__ == "__main__":
    main()
