"""Shared helpers for validating tool argument schemas."""

from __future__ import annotations

from typing import Any


def normalize_arguments_for_schema(
    input_schema: dict[str, Any] | None,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """Apply a conservative subset of JSON Schema object validation."""
    if input_schema is None:
        return dict(arguments)
    if input_schema.get("type") not in (None, "object"):
        return dict(arguments)

    has_properties = "properties" in input_schema
    properties = input_schema.get("properties", {})
    required = input_schema.get("required", [])
    if not isinstance(properties, dict):
        properties = {}
    if not isinstance(required, list):
        required = []

    missing = [
        str(name)
        for name in required
        if isinstance(name, str) and name not in arguments
    ]
    if missing:
        joined = ", ".join(missing)
        raise ValueError(f"Missing required argument(s): {joined}")

    if not has_properties:
        return dict(arguments)
    return {
        key: value
        for key, value in arguments.items()
        if key in properties
    }
