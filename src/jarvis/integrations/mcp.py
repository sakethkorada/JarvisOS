"""MCP tool loading for stdio and HTTP servers."""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from jarvis.contracts import ToolSpec
from jarvis.integrations.oauth import OAuthManager
from jarvis.settings import McpServerSettings
from jarvis.storage.auth import AuthStore
from jarvis.tools.registry import ToolRegistry


PROTOCOL_VERSION = "2025-03-26"


@dataclass(frozen=True)
class McpToolBinding:
    """Mapping from a JarvisOS tool name to an MCP server tool."""

    server: McpServerSettings
    mcp_tool_name: str
    jarvis_tool_name: str
    auth_store: AuthStore | None = None
    oauth_manager: OAuthManager | None = None


class McpStdioClient:
    """Small synchronous MCP client for stdio servers."""

    def __init__(self, server: McpServerSettings, timeout_seconds: float = 10) -> None:
        self._server = server
        self._timeout_seconds = timeout_seconds

    def list_tools(self) -> list[dict[str, Any]]:
        """Return MCP tool definitions exposed by the configured server."""
        with _mcp_process(self._server, self._timeout_seconds) as session:
            session.initialize()
            response = session.request("tools/list", {})
        tools = response.get("tools", [])
        return [tool for tool in tools if isinstance(tool, dict)]

    def call_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        """Call one MCP tool and return the raw MCP result."""
        with _mcp_process(self._server, self._timeout_seconds) as session:
            session.initialize()
            return session.request(
                "tools/call",
                {
                    "name": tool_name,
                    "arguments": arguments,
                },
            )


class McpHttpClient:
    """Small synchronous MCP client for streamable HTTP servers."""

    def __init__(
        self,
        server: McpServerSettings,
        auth_store: AuthStore | None = None,
        oauth_manager: OAuthManager | None = None,
        timeout_seconds: float = 10,
    ) -> None:
        if server.url is None:
            raise ValueError("HTTP MCP server requires url.")
        self._server = server
        self._auth_store = auth_store
        self._oauth_manager = oauth_manager
        self._timeout_seconds = timeout_seconds
        self._next_id = 1
        self._session_id: str | None = None

    def list_tools(self) -> list[dict[str, Any]]:
        """Return MCP tool definitions exposed by the configured server."""
        self.initialize()
        response = self.request("tools/list", {})
        tools = response.get("tools", [])
        return [tool for tool in tools if isinstance(tool, dict)]

    def call_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        """Call one MCP tool and return the raw MCP result."""
        self.initialize()
        return self.request(
            "tools/call",
            {
                "name": tool_name,
                "arguments": arguments,
            },
        )

    def initialize(self) -> None:
        """Perform the MCP initialize handshake."""
        self.request(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {
                    "name": "JarvisOS",
                    "version": "0.1.0",
                },
            },
        )
        self.notify("notifications/initialized", {})

    def request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        """Send a JSON-RPC request over HTTP and return the result object."""
        request_id = self._next_id
        self._next_id += 1
        message = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params,
        }
        response = self._post_message(message)
        if response.get("id") != request_id:
            raise RuntimeError("MCP HTTP response id did not match request id.")
        error = response.get("error")
        if error is not None:
            raise RuntimeError(f"MCP request failed: {error}")
        result = response.get("result", {})
        if not isinstance(result, dict):
            raise RuntimeError("MCP response result must be an object.")
        return result

    def notify(self, method: str, params: dict[str, Any]) -> None:
        """Send a JSON-RPC notification over HTTP."""
        self._post_message(
            {
                "jsonrpc": "2.0",
                "method": method,
                "params": params,
            },
            allow_empty=True,
        )

    def _post_message(
        self,
        message: dict[str, Any],
        allow_empty: bool = False,
        can_authorize: bool = True,
    ) -> dict[str, Any]:
        assert self._server.url is not None
        headers = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
            **self._server.headers,
        }
        if self._session_id is not None:
            headers["Mcp-Session-Id"] = self._session_id
        token = _bearer_token(
            self._server,
            self._auth_store,
            self._oauth_manager,
        )
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"
        body = json.dumps(message, separators=(",", ":")).encode("utf-8")
        request = Request(
            self._server.url,
            data=body,
            headers=headers,
            method="POST",
        )
        try:
            with urlopen(request, timeout=self._timeout_seconds) as response:
                session_id = response.headers.get("Mcp-Session-Id")
                if session_id:
                    self._session_id = session_id
                payload = response.read().decode("utf-8")
                content_type = response.headers.get("Content-Type", "")
        except HTTPError as exc:
            if exc.code == 401 and self._oauth_manager is not None and can_authorize:
                return self._retry_after_authorization(message, allow_empty)
            raise RuntimeError(_http_error_message(exc)) from exc
        except URLError as exc:
            raise RuntimeError(f"MCP HTTP request failed: {exc.reason}") from exc
        if not payload.strip():
            if allow_empty:
                return {}
            raise RuntimeError("MCP HTTP response was empty.")
        if "text/event-stream" in content_type:
            return _parse_sse_response(payload)
        decoded = json.loads(payload)
        if not isinstance(decoded, dict):
            raise RuntimeError("MCP HTTP response must be a JSON object.")
        return decoded

    def _retry_after_authorization(
        self,
        message: dict[str, Any],
        allow_empty: bool,
    ) -> dict[str, Any]:
        if self._server.auth_provider is None:
            raise RuntimeError("MCP HTTP server requires auth but has no provider.")
        self._oauth_manager.access_token(
            self._server.auth_provider,
            force_authorize=True,
        )
        return self._post_message(message, allow_empty, can_authorize=False)


class McpSession:
    """One short-lived JSON-RPC session with an MCP stdio process."""

    def __init__(
        self,
        process: subprocess.Popen[bytes],
        timeout_seconds: float,
    ) -> None:
        self._process = process
        self._timeout_seconds = timeout_seconds
        self._next_id = 1

    def initialize(self) -> None:
        """Perform the MCP initialize handshake."""
        self.request(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {
                    "name": "JarvisOS",
                    "version": "0.1.0",
                },
            },
        )
        self.notify("notifications/initialized", {})

    def request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        """Send a JSON-RPC request and return its result object."""
        request_id = self._next_id
        self._next_id += 1
        _write_message(
            self._process,
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": method,
                "params": params,
            },
        )
        while True:
            message = _read_message(self._process)
            if message.get("id") != request_id:
                continue
            error = message.get("error")
            if error is not None:
                raise RuntimeError(f"MCP request failed: {error}")
            result = message.get("result", {})
            if not isinstance(result, dict):
                raise RuntimeError("MCP response result must be an object.")
            return result

    def notify(self, method: str, params: dict[str, Any]) -> None:
        """Send a JSON-RPC notification without waiting for a response."""
        _write_message(
            self._process,
            {
                "jsonrpc": "2.0",
                "method": method,
                "params": params,
            },
        )


class _mcp_process:
    """Context manager for a short-lived MCP stdio process."""

    def __init__(self, server: McpServerSettings, timeout_seconds: float) -> None:
        self._server = server
        self._timeout_seconds = timeout_seconds
        self._process: subprocess.Popen[bytes] | None = None

    def __enter__(self) -> McpSession:
        self._process = subprocess.Popen(
            [self._server.command, *self._server.args],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        return McpSession(self._process, self._timeout_seconds)

    def __exit__(self, exc_type, exc, traceback) -> None:
        if self._process is None:
            return
        self._process.terminate()
        try:
            self._process.wait(timeout=self._timeout_seconds)
        except subprocess.TimeoutExpired:
            self._process.kill()
            self._process.wait(timeout=self._timeout_seconds)
        for pipe in (
            self._process.stdin,
            self._process.stdout,
            self._process.stderr,
        ):
            if pipe is not None:
                pipe.close()


def load_mcp_tools(
    servers: tuple[McpServerSettings, ...],
    registry: ToolRegistry,
    auth_store: AuthStore | None = None,
    oauth_manager: OAuthManager | None = None,
) -> None:
    """Load tools from configured MCP servers into the tool registry."""
    for server in servers:
        if not server.enabled:
            continue
        client = _mcp_client(server, auth_store, oauth_manager)
        for tool in client.list_tools():
            mcp_name = str(tool.get("name", "")).strip()
            if not mcp_name:
                continue
            jarvis_name = f"{server.name}.{mcp_name}"
            description = str(tool.get("description", "")).strip()
            if not description:
                description = f"MCP tool {mcp_name} from {server.name}."
            binding = McpToolBinding(
                server=server,
                mcp_tool_name=mcp_name,
                jarvis_tool_name=jarvis_name,
                auth_store=auth_store,
                oauth_manager=oauth_manager,
            )
            risk_level, requires_approval = _mcp_tool_policy(
                server,
                mcp_name,
                jarvis_name,
            )
            registry.register(
                ToolSpec(
                    name=jarvis_name,
                    description=description,
                    risk_level=risk_level,
                    requires_approval=requires_approval,
                    source=f"mcp:{server.name}",
                ),
                lambda arguments, binding=binding: _execute_mcp_tool(
                    binding,
                    arguments,
                ),
            )


def _mcp_tool_policy(
    server: McpServerSettings,
    mcp_name: str,
    jarvis_name: str,
) -> tuple[str, bool]:
    risk_level = server.risk_level
    requires_approval = server.requires_approval
    for override in server.tools:
        if override.name not in {mcp_name, jarvis_name}:
            continue
        if override.risk_level is not None:
            risk_level = override.risk_level
        if override.requires_approval is not None:
            requires_approval = override.requires_approval
    return risk_level, requires_approval


def _execute_mcp_tool(
    binding: McpToolBinding,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    client = _mcp_client(
        binding.server,
        binding.auth_store,
        binding.oauth_manager,
    )
    result = client.call_tool(binding.mcp_tool_name, arguments)
    return _normalize_mcp_result(result)


def _mcp_client(
    server: McpServerSettings,
    auth_store: AuthStore | None,
    oauth_manager: OAuthManager | None,
) -> McpStdioClient | McpHttpClient:
    """Create the right MCP client for a configured server."""
    if server.transport == "http":
        return McpHttpClient(server, auth_store, oauth_manager)
    return McpStdioClient(server)


def _bearer_token(
    server: McpServerSettings,
    auth_store: AuthStore | None,
    oauth_manager: OAuthManager | None,
) -> str | None:
    """Resolve a bearer token from environment or local auth storage."""
    if server.bearer_token_env:
        token = os.getenv(server.bearer_token_env)
        if token:
            return token
    if server.auth_provider and oauth_manager is not None:
        return oauth_manager.access_token(server.auth_provider)
    if server.auth_provider and auth_store is not None:
        record = auth_store.get_token(server.auth_provider)
        if record is not None:
            return record.access_token
    return None


def _http_error_message(error: HTTPError) -> str:
    authenticate = error.headers.get("WWW-Authenticate", "")
    detail = error.read().decode("utf-8", errors="replace").strip()
    if authenticate:
        return (
            f"MCP HTTP request failed with {error.code}; "
            f"authentication challenge: {authenticate}"
        )
    if detail:
        return f"MCP HTTP request failed with {error.code}: {detail}"
    return f"MCP HTTP request failed with {error.code}."


def _normalize_mcp_result(result: dict[str, Any]) -> dict[str, Any]:
    content = result.get("content", [])
    return {
        "mcp_result": result,
        "text": _content_to_text(content),
    }


def _content_to_text(content: Any) -> str:
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for item in content:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "text":
            parts.append(str(item.get("text", "")))
    return "\n".join(part for part in parts if part)


def _parse_sse_response(payload: str) -> dict[str, Any]:
    """Parse the first JSON-RPC object from a simple SSE response."""
    data_lines: list[str] = []
    for line in payload.splitlines():
        if line.startswith("data:"):
            data_lines.append(line[5:].strip())
    if not data_lines:
        raise RuntimeError("MCP SSE response did not contain data lines.")
    decoded = json.loads("\n".join(data_lines))
    if not isinstance(decoded, dict):
        raise RuntimeError("MCP SSE data must contain a JSON object.")
    return decoded


def _write_message(
    process: subprocess.Popen[bytes],
    message: dict[str, Any],
) -> None:
    if process.stdin is None:
        raise RuntimeError("MCP process stdin is unavailable.")
    body = json.dumps(message, separators=(",", ":")).encode("utf-8")
    header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
    process.stdin.write(header + body)
    process.stdin.flush()


def _read_message(process: subprocess.Popen[bytes]) -> dict[str, Any]:
    if process.stdout is None:
        raise RuntimeError("MCP process stdout is unavailable.")
    headers: dict[str, str] = {}
    while True:
        line = process.stdout.readline()
        if line in (b"\r\n", b"\n", b""):
            break
        decoded = line.decode("ascii").strip()
        if ":" in decoded:
            key, value = decoded.split(":", 1)
            headers[key.lower()] = value.strip()
    length = int(headers.get("content-length", "0"))
    if length <= 0:
        raise RuntimeError("MCP message is missing Content-Length.")
    body = process.stdout.read(length)
    return json.loads(body.decode("utf-8"))
