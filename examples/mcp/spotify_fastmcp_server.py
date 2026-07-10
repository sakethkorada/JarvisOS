"""Local FastMCP wrapper for Spotify Web API read tools.

This server exposes low-risk Spotify read operations as MCP tools. JarvisOS
starts it over stdio and the server calls Spotify Web API using the OAuth token
stored in JarvisOS auth storage.
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


DEFAULT_API_BASE_URL = "https://api.spotify.com/v1"
DEFAULT_AUTH_DB = Path(".jarvis/auth.sqlite3")


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
    """Create a FastMCP server exposing Spotify read tools."""
    FastMCP = _fastmcp_class()
    mcp = FastMCP("JarvisOS Spotify")

    @mcp.tool()
    def search(query: str, types: str = "track,artist", limit: int = 10) -> str:
        """Search Spotify catalog items by query and comma-separated types."""
        return _tool_text(
            lambda: search_text(
                auth_db=auth_db,
                config_path=config_path,
                provider=provider,
                api_base_url=api_base_url,
                query=query,
                types=types,
                limit=limit,
            )
        )

    @mcp.tool()
    def current_playback() -> str:
        """Get the current Spotify playback state for the user."""
        return _tool_text(
            lambda: current_playback_text(
                auth_db=auth_db,
                config_path=config_path,
                provider=provider,
                api_base_url=api_base_url,
            )
        )

    @mcp.tool()
    def recently_played(limit: int = 10) -> str:
        """List the user's recently played Spotify tracks."""
        return _tool_text(
            lambda: recently_played_text(
                auth_db=auth_db,
                config_path=config_path,
                provider=provider,
                api_base_url=api_base_url,
                limit=limit,
            )
        )

    @mcp.tool()
    def list_playlists(limit: int = 10, offset: int = 0) -> str:
        """List the current user's Spotify playlists."""
        return _tool_text(
            lambda: list_playlists_text(
                auth_db=auth_db,
                config_path=config_path,
                provider=provider,
                api_base_url=api_base_url,
                limit=limit,
                offset=offset,
            )
        )

    return mcp


def search_text(
    auth_db: Path,
    config_path: Path | None,
    provider: str,
    api_base_url: str,
    query: str,
    types: str = "track,artist",
    limit: int = 10,
) -> str:
    """Return readable Spotify catalog search results."""
    token = _access_token(auth_db, config_path, provider)
    normalized_types = ",".join(_split_csv(types)) or "track"
    bounded_limit = _bounded_limit(limit)
    params = urlencode(
        {
            "q": query,
            "type": normalized_types,
            "limit": bounded_limit,
        }
    )
    data = _request_json(f"{api_base_url.rstrip('/')}/search?{params}", token)
    lines = [f'Spotify search results for "{query}":']
    lines.extend(_format_search_items(data))
    if len(lines) == 1:
        lines.append("- no results found")
    return "\n".join(lines)


def current_playback_text(
    auth_db: Path,
    config_path: Path | None,
    provider: str,
    api_base_url: str,
) -> str:
    """Return a readable current playback summary."""
    token = _access_token(auth_db, config_path, provider)
    data = _request_json(
        f"{api_base_url.rstrip('/')}/me/player",
        token,
        allow_empty=True,
    )
    if not data:
        return "Spotify current playback: nothing is currently playing."
    item = data.get("item") if isinstance(data, dict) else None
    device = data.get("device", {}) if isinstance(data, dict) else {}
    is_playing = bool(data.get("is_playing")) if isinstance(data, dict) else False
    status = "playing" if is_playing else "paused"
    item_text = _format_track(item) if isinstance(item, dict) else "unknown item"
    device_name = (
        str(device.get("name", "")).strip()
        if isinstance(device, dict)
        else ""
    )
    suffix = f" on {device_name}" if device_name else ""
    return f"Spotify current playback: {status} {item_text}{suffix}."


def recently_played_text(
    auth_db: Path,
    config_path: Path | None,
    provider: str,
    api_base_url: str,
    limit: int = 10,
) -> str:
    """Return a readable list of recently played Spotify tracks."""
    token = _access_token(auth_db, config_path, provider)
    params = urlencode({"limit": _bounded_limit(limit)})
    data = _request_json(
        f"{api_base_url.rstrip('/')}/me/player/recently-played?{params}",
        token,
    )
    items = data.get("items", [])
    if not isinstance(items, list) or not items:
        return "Spotify recently played: no tracks found."
    lines = ["Spotify recently played:"]
    for item in items:
        if not isinstance(item, dict):
            continue
        track = item.get("track")
        played_at = str(item.get("played_at", "")).strip()
        if isinstance(track, dict):
            line = f"- {_format_track(track)}"
            if played_at:
                line = f"{line} | played_at={played_at}"
            lines.append(line)
    return "\n".join(lines)


def list_playlists_text(
    auth_db: Path,
    config_path: Path | None,
    provider: str,
    api_base_url: str,
    limit: int = 10,
    offset: int = 0,
) -> str:
    """Return a readable list of the user's Spotify playlists."""
    token = _access_token(auth_db, config_path, provider)
    params = urlencode(
        {
            "limit": _bounded_limit(limit, maximum=50),
            "offset": max(0, int(offset)),
        }
    )
    data = _request_json(f"{api_base_url.rstrip('/')}/me/playlists?{params}", token)
    items = data.get("items", [])
    if not isinstance(items, list) or not items:
        return "Spotify playlists: no playlists found."
    lines = ["Spotify playlists:"]
    for item in items:
        if isinstance(item, dict):
            lines.append(_format_playlist(item))
    return "\n".join(lines)


def _format_search_items(data: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    for container_name, formatter in (
        ("tracks", _format_track),
        ("artists", _format_artist),
        ("albums", _format_album),
        ("playlists", _format_playlist),
        ("shows", _format_named_item),
        ("episodes", _format_named_item),
    ):
        container = data.get(container_name)
        if not isinstance(container, dict):
            continue
        items = container.get("items", [])
        if not isinstance(items, list):
            continue
        for item in items:
            if isinstance(item, dict):
                lines.append(f"- {formatter(item)}")
    return lines


def _format_track(track: dict[str, Any]) -> str:
    name = str(track.get("name", "unknown track"))
    artists = track.get("artists", [])
    artist_names = _names(artists) or "unknown artist"
    track_id = str(track.get("id", "")).strip()
    suffix = f" | id={track_id}" if track_id else ""
    return f"{name} by {artist_names}{suffix}"


def _format_artist(artist: dict[str, Any]) -> str:
    name = str(artist.get("name", "unknown artist"))
    artist_id = str(artist.get("id", "")).strip()
    suffix = f" | id={artist_id}" if artist_id else ""
    return f"artist: {name}{suffix}"


def _format_album(album: dict[str, Any]) -> str:
    name = str(album.get("name", "unknown album"))
    artists = _names(album.get("artists", [])) or "unknown artist"
    album_id = str(album.get("id", "")).strip()
    suffix = f" | id={album_id}" if album_id else ""
    return f"album: {name} by {artists}{suffix}"


def _format_playlist(playlist: dict[str, Any]) -> str:
    name = str(playlist.get("name", "unknown playlist"))
    owner = playlist.get("owner", {})
    owner_name = (
        str(owner.get("display_name") or owner.get("id") or "unknown owner")
        if isinstance(owner, dict)
        else "unknown owner"
    )
    playlist_id = str(playlist.get("id", "")).strip()
    suffix = f" | id={playlist_id}" if playlist_id else ""
    return f"{name} | owner={owner_name}{suffix}"


def _format_named_item(item: dict[str, Any]) -> str:
    name = str(item.get("name", "unknown item"))
    item_type = str(item.get("type", "item"))
    item_id = str(item.get("id", "")).strip()
    suffix = f" | id={item_id}" if item_id else ""
    return f"{item_type}: {name}{suffix}"


def _names(items: Any) -> str:
    if not isinstance(items, list):
        return ""
    names = [
        str(item.get("name", "")).strip()
        for item in items
        if isinstance(item, dict) and str(item.get("name", "")).strip()
    ]
    return ", ".join(names)


def _bounded_limit(limit: int, minimum: int = 1, maximum: int = 25) -> int:
    return max(minimum, min(int(limit), maximum))


def _split_csv(value: str | None) -> tuple[str, ...]:
    if not value:
        return ()
    return tuple(item.strip() for item in value.split(",") if item.strip())


def _tool_text(handler: Callable[[], str]) -> str:
    """Return tool text while keeping auth errors in-band for MCP clients."""
    try:
        return handler()
    except RuntimeError as exc:
        return f"AUTH_ERROR: {exc}"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="JarvisOS Spotify MCP server.")
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
        default="spotify",
        help="OAuth provider name in the JarvisOS auth store.",
    )
    parser.add_argument(
        "--api-base-url",
        default=os.getenv("SPOTIFY_API_BASE_URL", DEFAULT_API_BASE_URL),
        help="Spotify Web API base URL.",
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
                "Check `python -m jarvis auth debug spotify --json`, set any "
                "missing client secret environment variable, then retry Spotify."
            ) from exc
        if refreshed is not None:
            return refreshed.access_token
        raise RuntimeError(
            f"Stored token for {provider} is expired and could not be refreshed. "
            "Check `python -m jarvis auth debug spotify --json`, set any missing "
            "client secret environment variable, then retry Spotify."
        )
    return record.access_token


def _request_json(
    url: str,
    access_token: str,
    allow_empty: bool = False,
) -> dict[str, Any]:
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
            if allow_empty and response.status == 204:
                return {}
    except HTTPError as exc:
        if allow_empty and exc.code == 204:
            return {}
        detail = exc.read().decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"Spotify REST request failed: {detail}") from exc
    except URLError as exc:
        raise RuntimeError(f"Spotify REST request failed: {exc.reason}") from exc

    if allow_empty and not payload.strip():
        return {}
    data = json.loads(payload)
    if not isinstance(data, dict):
        raise RuntimeError("Spotify REST response was not a JSON object.")
    return data


if __name__ == "__main__":
    main()
