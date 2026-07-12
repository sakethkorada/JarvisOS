"""Provider-neutral normalization for successful tool outputs."""

from __future__ import annotations

from typing import Any


PUBLIC_OUTPUT_FIELDS = ("text", "records", "ids", "metadata")


def normalize_tool_output(
    output: dict[str, Any],
    *,
    source: str | None = None,
) -> dict[str, Any]:
    """Add the stable output fields while preserving handler-specific data."""
    normalized = dict(output)
    records = _records_from_output(normalized)
    ids = _ids_from_output(normalized, records)
    metadata = normalized.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
    if source and "source" not in metadata:
        metadata = {**metadata, "source": source}

    normalized["text"] = _text_from_output(normalized)
    normalized["records"] = records
    normalized["ids"] = ids
    normalized["metadata"] = metadata
    return normalized


def public_tool_output(output: dict[str, Any]) -> dict[str, Any]:
    """Return only normalized fields safe to send to synthesis or other agents."""
    normalized = normalize_tool_output(output)
    return {field: normalized[field] for field in PUBLIC_OUTPUT_FIELDS}


def _text_from_output(output: dict[str, Any]) -> str:
    for key in ("text", "summary", "result"):
        value = output.get(key)
        if isinstance(value, str):
            return value.strip()
    return ""


def _records_from_output(output: dict[str, Any]) -> list[dict[str, Any]]:
    records = output.get("records")
    if isinstance(records, list):
        return [dict(item) for item in records if isinstance(item, dict)]

    discovered: list[dict[str, Any]] = []
    for key, value in output.items():
        if key in {"mcp_result", "metadata", "ids"} or not isinstance(value, list):
            continue
        discovered.extend(dict(item) for item in value if isinstance(item, dict))
    return discovered


def _ids_from_output(
    output: dict[str, Any],
    records: list[dict[str, Any]],
) -> list[str]:
    values = output.get("ids", [])
    ids = [str(value) for value in values if isinstance(value, (str, int))]
    for record in records:
        for key in ("id", "message_id", "thread_id", "event_id"):
            value = record.get(key)
            if isinstance(value, (str, int)):
                ids.append(str(value))
                break
    return list(dict.fromkeys(item for item in ids if item))
