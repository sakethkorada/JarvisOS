"""Tiny MCP stdio server for JarvisOS smoke tests."""

from __future__ import annotations

import json
import sys
from typing import Any


def main() -> None:
    """Run a minimal MCP stdio server."""
    while True:
        message = _read_message()
        if message is None:
            return
        response = _handle_message(message)
        if response is not None:
            _write_message(response)


def _handle_message(message: dict[str, Any]) -> dict[str, Any] | None:
    method = message.get("method")
    request_id = message.get("id")
    if request_id is None:
        return None
    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "protocolVersion": "2025-03-26",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "demo-mcp", "version": "0.1.0"},
            },
        }
    if method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "tools": [
                    {
                        "name": "echo",
                        "description": "Echo text from a demo MCP server.",
                        "inputSchema": {
                            "type": "object",
                            "properties": {"text": {"type": "string"}},
                        },
                    }
                ]
            },
        }
    if method == "tools/call":
        params = message.get("params", {})
        arguments = params.get("arguments", {})
        text = str(arguments.get("text", ""))
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "content": [{"type": "text", "text": f"demo echo: {text}"}],
                "isError": False,
            },
        }
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": -32601, "message": f"Unknown method: {method}"},
    }


def _read_message() -> dict[str, Any] | None:
    line = sys.stdin.buffer.readline()
    if line == b"":
        return None
    decoded = line.decode("utf-8").strip()
    if not decoded:
        return None
    if decoded.lower().startswith("content-length:"):
        return _read_header_framed_message(decoded)
    return json.loads(decoded)


def _write_message(message: dict[str, Any]) -> None:
    body = json.dumps(message, separators=(",", ":")).encode("utf-8")
    sys.stdout.buffer.write(body + b"\n")
    sys.stdout.buffer.flush()


def _read_header_framed_message(first_header: str) -> dict[str, Any]:
    headers: dict[str, str] = {}
    key, value = first_header.split(":", 1)
    headers[key.lower()] = value.strip()
    while True:
        line = sys.stdin.buffer.readline()
        if line in (b"\r\n", b"\n", b""):
            break
        decoded = line.decode("ascii").strip()
        if ":" in decoded:
            key, value = decoded.split(":", 1)
            headers[key.lower()] = value.strip()
    length = int(headers.get("content-length", "0"))
    if length <= 0:
        raise RuntimeError("MCP message is missing Content-Length.")
    body = sys.stdin.buffer.read(length)
    return json.loads(body.decode("utf-8"))


if __name__ == "__main__":
    main()
