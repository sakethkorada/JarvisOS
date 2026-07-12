"""Schema-aware tool argument resolution for orchestration."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from jarvis.contracts import AgentSpec, ModelRequest, ToolResult, ToolSpec
from jarvis.errors import ModelProviderError
from jarvis.models import ModelRouter
from jarvis.orchestration.agent_runtime import AgentRuntime
from jarvis.tool_schemas import normalize_arguments_for_schema


@dataclass(frozen=True)
class ToolUseFeedback:
    """Feedback from validation or execution used to repair tool arguments."""

    stage: Literal["validation", "execution"]
    attempted_arguments: dict[str, Any]
    error: str
    output: dict[str, Any] | None = None


@dataclass(frozen=True)
class ToolUseAttempt:
    """One model-backed tool-use argument attempt for trace diagnostics."""

    attempt: int
    arguments: dict[str, Any]
    error: str | None = None
    raw_output: str | None = None


@dataclass(frozen=True)
class ArgumentResolution:
    """Resolved tool arguments or a clean resolution error."""

    arguments: dict[str, Any]
    error: str | None = None
    attempts: tuple[ToolUseAttempt, ...] = ()


class ToolUseAgent:
    """Build and repair tool arguments with model assistance plus validation."""

    def __init__(
        self,
        models: ModelRouter,
        system_prompt: str,
        max_attempts: int = 2,
        agent_runtime: AgentRuntime | None = None,
    ) -> None:
        self._models = models
        self._system_prompt = system_prompt
        self._max_attempts = max(1, max_attempts)
        self._agent_runtime = agent_runtime or AgentRuntime(
            AgentSpec(
                name="tool_use",
                description="Builds JSON arguments for one selected tool.",
                allowed_tools=("*",),
                execution_role="tool_use",
                output_contract="tool_arguments_json",
            ),
            models,
        )

    def build(
        self,
        goal: str,
        tool: ToolSpec,
        arguments: dict[str, Any],
        model_name: str | None,
        model_mode: str,
        prior_results: tuple[ToolResult, ...] = (),
        feedback: ToolUseFeedback | None = None,
        named_results: dict[str, ToolResult] | None = None,
    ) -> ArgumentResolution:
        """Return valid tool arguments or a clean model/validation error."""
        base = resolve_tool_arguments(
            goal,
            tool,
            arguments,
            prior_results=prior_results,
            named_results=named_results,
            apply_safe_defaults=False,
            validate_schema=False,
        )
        can_repair_with_model = (
            model_name != "fake-local"
            and (
                feedback is not None
                or (base.error is not None and tool.capability is not None)
                or _should_ask_model(goal, tool, arguments)
            )
        )
        if base.error is not None and not can_repair_with_model:
            return base
        selected_model = self._agent_runtime.resolve_model_name(model_name, model_mode)
        if selected_model == "fake-local":
            return resolve_tool_arguments(
                goal,
                tool,
                arguments,
                prior_results=prior_results,
                named_results=named_results,
                apply_safe_defaults=True,
            )

        candidate = base.arguments if base.error is None else dict(arguments)
        validation_error = (
            feedback.error
            if feedback is not None and feedback.stage == "validation"
            else base.error or _schema_error(tool.input_schema, candidate)
        )
        if (
            validation_error is None
            and feedback is None
            and not _should_ask_model(goal, tool, arguments)
        ):
            return ArgumentResolution(candidate)

        raw_output = None
        attempts: list[ToolUseAttempt] = []
        for attempt in range(1, self._max_attempts + 1):
            request = ModelRequest(
                goal=goal,
                mode=model_mode,
                system_prompt=self._system_prompt,
                messages=[
                    _tool_use_context(
                        goal=goal,
                        tool=tool,
                        current_arguments=candidate,
                        prior_results=prior_results,
                        named_results=named_results,
                        validation_error=validation_error,
                        feedback=feedback,
                        attempt=attempt,
                    )
                ],
            )
            try:
                response = self._agent_runtime.run(request, model_name).response
            except ModelProviderError as exc:
                return ArgumentResolution(
                    arguments={},
                    error=f"Argument builder model failed: {exc}",
                )
            raw_output = response.text
            parsed = _parse_json_object(response.text)
            if parsed is None:
                validation_error = "Argument builder did not return a JSON object."
                attempts.append(
                    ToolUseAttempt(
                        attempt=attempt,
                        arguments={},
                        error=validation_error,
                        raw_output=response.text[:500],
                    )
                )
                continue
            resolved = resolve_tool_arguments(
                goal,
                tool,
                parsed,
                prior_results=prior_results,
                named_results=named_results,
                apply_safe_defaults=False,
            )
            if resolved.error is not None:
                validation_error = resolved.error
                attempts.append(
                    ToolUseAttempt(
                        attempt=attempt,
                        arguments=parsed,
                        error=validation_error,
                        raw_output=response.text[:500],
                    )
                )
                continue
            candidate = resolved.arguments
            validation_error = _schema_error(tool.input_schema, candidate)
            if validation_error is None:
                attempts.append(
                    ToolUseAttempt(
                        attempt=attempt,
                        arguments=candidate,
                        raw_output=response.text[:500],
                    )
                )
                return ArgumentResolution(candidate, attempts=tuple(attempts))
            attempts.append(
                ToolUseAttempt(
                    attempt=attempt,
                    arguments=candidate,
                    error=validation_error,
                    raw_output=response.text[:500],
                )
            )

        detail = validation_error or "Argument builder could not create valid args."
        if raw_output:
            detail = f"{detail} Last model output: {raw_output[:500]}"
        return ArgumentResolution(arguments={}, error=detail, attempts=tuple(attempts))


class ArgumentBuilder(ToolUseAgent):
    """Compatibility wrapper for the renamed ToolUseAgent boundary."""

    def __init__(
        self,
        models: ModelRouter,
        max_attempts: int = 2,
        system_prompt: str | None = None,
    ) -> None:
        super().__init__(
            models=models,
            system_prompt=system_prompt or DEFAULT_TOOL_USE_PROMPT,
            max_attempts=max_attempts,
        )


def resolve_tool_arguments(
    goal: str,
    tool: ToolSpec,
    arguments: dict[str, Any],
    prior_results: tuple[ToolResult, ...] = (),
    named_results: dict[str, ToolResult] | None = None,
    resolve_references: bool = True,
    apply_safe_defaults: bool = True,
    validate_schema: bool = True,
) -> ArgumentResolution:
    """Resolve references, safe defaults, capability mappings, and schema shape."""
    try:
        resolved = (
            _resolve_references(arguments, prior_results, named_results or {})
            if resolve_references
            else dict(arguments)
        )
        if apply_safe_defaults:
            resolved = _fill_safe_defaults(goal, tool, resolved)
        resolved = _remove_none_values(resolved)
        if validate_schema:
            resolved = normalize_arguments_for_schema(tool.input_schema, resolved)
    except ValueError as exc:
        return ArgumentResolution(arguments={}, error=str(exc))
    return ArgumentResolution(arguments=resolved)


def _resolve_references(
    arguments: dict[str, Any],
    prior_results: tuple[ToolResult, ...],
    named_results: dict[str, ToolResult],
) -> dict[str, Any]:
    resolved: dict[str, Any] = {}
    for key, value in arguments.items():
        resolved[str(key)] = _resolve_value(value, prior_results, named_results)
    return resolved


def _resolve_value(
    value: Any,
    prior_results: tuple[ToolResult, ...],
    named_results: dict[str, ToolResult],
) -> Any:
    if isinstance(value, str):
        return _resolve_string(value, prior_results, named_results)
    if isinstance(value, list):
        return [_resolve_value(item, prior_results, named_results) for item in value]
    if isinstance(value, dict):
        return {
            str(key): _resolve_value(item, prior_results, named_results)
            for key, item in value.items()
        }
    return value


def _resolve_string(
    value: str,
    prior_results: tuple[ToolResult, ...],
    named_results: dict[str, ToolResult],
) -> Any:
    if value.startswith("$step."):
        parts = value.split(".", 2)
        if len(parts) != 3 or not parts[1] or not parts[2]:
            raise ValueError(
                f"Could not resolve {value}: use $step.<id>.<field>."
            )
        result = named_results.get(parts[1])
        if result is None or not result.success:
            raise ValueError(
                f"Could not resolve {value}: no prior successful named result."
            )
        return _resolve_output_path(value, result.output, parts[2])
    if not value.startswith("$last."):
        return value
    field_name = value.removeprefix("$last.")
    last_result = _last_successful_result(prior_results)
    if last_result is None:
        raise ValueError(
            f"Could not resolve {value}: no prior successful tool result."
        )
    return _resolve_output_path(value, last_result.output, field_name)


def _resolve_output_path(reference: str, output: dict[str, Any], path: str) -> Any:
    """Resolve a bounded dotted/indexed path through a normalized tool output."""
    current: Any = output
    for token in _reference_tokens(reference, path):
        if isinstance(token, str):
            if not isinstance(current, dict) or token not in current:
                raise ValueError(
                    f"Could not resolve {reference}: result has no '{token}' field."
                )
            current = current[token]
            continue
        if not isinstance(current, list) or token >= len(current):
            raise ValueError(
                f"Could not resolve {reference}: result has no item at index {token}."
            )
        current = current[token]
    return current


def _reference_tokens(reference: str, path: str) -> tuple[str | int, ...]:
    """Parse ``records[0].id`` without allowing arbitrary expression syntax."""
    if not path:
        raise ValueError(f"Could not resolve {reference}: reference path is empty.")
    tokens: list[str | int] = []
    position = 0
    pattern = re.compile(r"(?:^|\.)([A-Za-z_][A-Za-z0-9_]*)|\[(\d+)\]")
    for match in pattern.finditer(path):
        if match.start() != position:
            raise ValueError(
                f"Could not resolve {reference}: use dotted fields and numeric indexes."
            )
        field, index = match.groups()
        tokens.append(field if field is not None else int(index))
        position = match.end()
    if position != len(path):
        raise ValueError(
            f"Could not resolve {reference}: use dotted fields and numeric indexes."
        )
    return tuple(tokens)


def _last_successful_result(
    prior_results: tuple[ToolResult, ...],
) -> ToolResult | None:
    for result in reversed(prior_results):
        if result.success:
            return result
    return None


def _fill_safe_defaults(
    goal: str,
    tool: ToolSpec,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    normalized = dict(arguments)
    tool_name = tool.name
    if tool_name in {"memory.search", "notes.search"}:
        normalized.setdefault("query", goal)
    if tool_name in {"task.breakdown", "task.create", "task.create_summary"}:
        normalized.setdefault("goal", goal)
    if tool_name == "task.create":
        normalized.setdefault("title", goal)
    if tool_name == "general.generate_text":
        normalized.setdefault("instruction", goal)
    if tool_name.endswith(".echo"):
        normalized.setdefault("text", goal)
    return normalized


def _remove_none_values(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _remove_none_values(item)
            for key, item in value.items()
            if item is not None
        }
    if isinstance(value, list):
        return [_remove_none_values(item) for item in value if item is not None]
    return value


def _should_ask_model(
    goal: str,
    tool: ToolSpec,
    original_arguments: dict[str, Any],
) -> bool:
    """Use the model when a schema needs construction or temporal completion."""
    schema = tool.input_schema
    if schema is None:
        return False
    properties = schema.get("properties", {})
    if not isinstance(properties, dict) or not properties:
        return False
    if not original_arguments:
        return True
    if tool.capability is not None:
        return True
    temporal_fields = {
        str(name)
        for name in properties
        if any(
            marker in str(name).lower()
            for marker in ("date", "time", "start", "end", "before", "after", "min", "max")
        )
    }
    if not temporal_fields or temporal_fields.issubset(original_arguments):
        return False
    return _goal_mentions_relative_time(goal)


def _goal_mentions_relative_time(goal: str) -> bool:
    """Detect general relative-time language without choosing a provider tool."""
    normalized = goal.lower()
    markers = (
        "today",
        "tomorrow",
        "yesterday",
        "this week",
        "next week",
        "coming",
        "upcoming",
        "later",
        "tonight",
        "morning",
        "afternoon",
        "evening",
        "day",
        "week",
        "month",
    )
    return any(marker in normalized for marker in markers)


def _schema_error(
    input_schema: dict[str, Any] | None,
    arguments: dict[str, Any],
) -> str | None:
    try:
        normalize_arguments_for_schema(input_schema, arguments)
    except ValueError as exc:
        return str(exc)
    return None


def _parse_json_object(text: str) -> dict[str, Any] | None:
    try:
        payload = json.loads(_strip_code_fence(text))
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if match is None:
            return None
        try:
            payload = json.loads(match.group(0))
        except json.JSONDecodeError:
            return None
    return payload if isinstance(payload, dict) else None


def _strip_code_fence(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        return "\n".join(lines).strip()
    return stripped


def _tool_use_context(
    goal: str,
    tool: ToolSpec,
    current_arguments: dict[str, Any],
    prior_results: tuple[ToolResult, ...],
    named_results: dict[str, ToolResult] | None,
    validation_error: str | None,
    feedback: ToolUseFeedback | None,
    attempt: int,
) -> str:
    payload = {
        "goal": goal,
        "current_datetime": datetime.now().astimezone().isoformat(),
        "attempt": attempt,
        "tool": {
            "name": tool.name,
            "description": tool.description,
            "argument_hints": tool.argument_hints,
            "input_schema": tool.input_schema,
            "capability": (
                tool.capability.__dict__ if tool.capability is not None else None
            ),
        },
        "current_arguments": current_arguments,
        "prior_successful_results": [
            {
                "tool_name": result.tool_name,
                "output": result.output,
            }
            for result in prior_results
            if result.success
        ],
        "named_successful_results": {
            name: result.output
            for name, result in (named_results or {}).items()
            if result.success
        },
        "validation_error": validation_error,
        "feedback": (
            {
                "stage": feedback.stage,
                "attempted_arguments": feedback.attempted_arguments,
                "error": feedback.error,
                "output": feedback.output,
            }
            if feedback is not None
            else None
        ),
    }
    return json.dumps(payload, indent=2, default=str)


DEFAULT_TOOL_USE_PROMPT = """
You are the JarvisOS ToolUseAgent.

Return only a JSON object containing arguments for exactly one selected tool
call. You cannot change the tool name or claim the tool ran.
Use the tool input_schema exactly. Do not include keys outside the schema.
Infer values from the user goal, current_datetime, current arguments, prior
successful tool results, selected tool argument_hints, and feedback.
If validation_error or feedback is present, repair the attempted arguments.
When the user goal contains relative dates or times and the schema has date,
time, start, end, min, or max fields, convert the relative phrase into concrete
ISO-8601 values using current_datetime. Use obvious provider defaults such as a
primary calendar only when the goal does not identify a different target.
Do not explain your reasoning. Do not wrap the JSON in markdown.
""".strip()
