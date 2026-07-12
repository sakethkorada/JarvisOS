"""Evaluation harnesses for planner and ToolUseAgent behavior."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from time import perf_counter
from typing import Any, Literal

from jarvis.agents import AgentRegistry, default_agent_registry
from jarvis.contracts import ToolResult
from jarvis.models import FakeModelProvider, ModelRouter, default_model_router
from jarvis.orchestration.arguments import ToolUseAgent
from jarvis.orchestration.planner import Planner
from jarvis.prompts import PromptLibrary
from jarvis.runtime import create_default_tool_registry
from jarvis.settings import JarvisSettings
from jarvis.tools import default_tool_registry
from jarvis.tools.registry import ToolRegistry


EvalKind = Literal["planner", "tool_use"]
FailureType = Literal["none", "model_quality", "infrastructure"]


@dataclass(frozen=True)
class EvalCase:
    """One isolated planner or tool-use evaluation case."""

    id: str
    kind: EvalKind
    goal: str
    expected_tools: tuple[str, ...] = ()
    expected_tool_groups: tuple[tuple[str, ...], ...] = ()
    forbidden_tools: tuple[str, ...] = ()
    max_steps: int | None = None
    allow_fallback: bool = False
    tool_name: str | None = None
    rough_arguments: dict[str, Any] = field(default_factory=dict)
    prior_results: tuple[ToolResult, ...] = ()
    expected_arguments: dict[str, Any] = field(default_factory=dict)
    required_argument_keys: tuple[str, ...] = ()
    forbidden_argument_keys: tuple[str, ...] = ()


@dataclass(frozen=True)
class EvalSuite:
    """A named collection of isolated evaluation cases."""

    name: str
    description: str | None
    cases: tuple[EvalCase, ...]


@dataclass(frozen=True)
class EvalCaseResult:
    """Scored result for one evaluation case."""

    id: str
    kind: EvalKind
    passed: bool
    score: float
    errors: tuple[str, ...]
    latency_ms: float
    actual_tools: tuple[str, ...] = ()
    actual_arguments: dict[str, Any] = field(default_factory=dict)
    source: str | None = None
    raw_output: str | None = None
    # Infrastructure failures are reported separately and excluded from the
    # model-quality score.  ``passed`` and legacy fields remain compatible.
    failure_type: FailureType = "none"


@dataclass(frozen=True)
class EvalReport:
    """Complete evaluation report for a suite run."""

    suite_name: str
    model_name: str | None
    mode: str
    passed: int
    failed: int
    score: float
    results: tuple[EvalCaseResult, ...]
    infrastructure_failures: int = 0
    model_failures: int = 0


def load_eval_suite(path: Path) -> EvalSuite:
    """Load an eval suite from a JSON file."""
    with path.open("r", encoding="utf-8") as suite_file:
        payload = json.load(suite_file)
    if not isinstance(payload, dict):
        raise ValueError("Eval suite must be a JSON object.")
    cases_payload = payload.get("cases")
    if not isinstance(cases_payload, list):
        raise ValueError("Eval suite requires a cases list.")
    return EvalSuite(
        name=_required_string(payload, "name"),
        description=_optional_string(payload.get("description")),
        cases=tuple(_eval_case_from_data(item) for item in cases_payload),
    )


def run_eval_suite(
    suite: EvalSuite,
    settings: JarvisSettings,
    model_name: str | None,
    model_mode: str,
    include_raw: bool = False,
    agents: AgentRegistry | None = None,
    tools: ToolRegistry | None = None,
    models: ModelRouter | None = None,
    allow_live_integrations: bool = False,
) -> EvalReport:
    """Run a suite without executing provider tools."""
    agents = agents or default_agent_registry()
    # Evaluations should be hermetic by default: MCP discovery, plugins, and
    # Ollama probing make a suite depend on the developer's machine. Callers
    # can opt into the historical live registry, or inject registries/models.
    tools = tools or (
        create_default_tool_registry(settings)
        if allow_live_integrations
        else default_tool_registry()
    )
    models = models or (
        default_model_router(settings)
        if allow_live_integrations
        else ModelRouter({"fake-local": FakeModelProvider()}, "fake-local")
    )
    prompts = PromptLibrary(
        planner_prompt_path=settings.prompts.planner_path,
        synthesis_prompt_path=settings.prompts.synthesis_path,
        tool_use_prompt_path=settings.prompts.tool_use_path,
    )
    planner = Planner(
        agents=agents,
        tools=tools,
        models=models,
        system_prompt=prompts.planner_prompt(),
    )
    tool_use_agent = ToolUseAgent(
        models=models,
        system_prompt=prompts.tool_use_prompt(),
    )
    results = tuple(
        _run_case(
            case,
            planner,
            tool_use_agent,
            tools,
            model_name,
            model_mode,
            include_raw,
        )
        for case in suite.cases
    )
    passed = sum(1 for result in results if result.passed)
    infrastructure_failures = sum(
        1 for result in results if result.failure_type == "infrastructure"
    )
    model_failures = sum(
        1 for result in results if result.failure_type == "model_quality"
    )
    scored_results = tuple(
        result for result in results if result.failure_type != "infrastructure"
    )
    # ``failed`` remains the legacy total-failure count. Consumers that need
    # model-quality scoring should use ``model_failures`` and the score.
    failed = len(results) - passed
    score = (
        sum(result.score for result in scored_results) / len(scored_results)
        if scored_results
        else 0.0
    )
    return EvalReport(
        suite_name=suite.name,
        model_name=model_name,
        mode=model_mode,
        passed=passed,
        failed=failed,
        score=score,
        results=results,
        infrastructure_failures=infrastructure_failures,
        model_failures=model_failures,
    )


def _run_case(
    case: EvalCase,
    planner: Planner,
    tool_use_agent: ToolUseAgent,
    tools: ToolRegistry,
    model_name: str | None,
    model_mode: str,
    include_raw: bool,
) -> EvalCaseResult:
    if case.kind == "planner":
        return _run_planner_case(case, planner, model_name, model_mode, include_raw)
    if case.kind == "tool_use":
        return _run_tool_use_case(case, tool_use_agent, tools, model_name, model_mode)
    raise ValueError(f"Unsupported eval case kind: {case.kind}")


def _run_planner_case(
    case: EvalCase,
    planner: Planner,
    model_name: str | None,
    model_mode: str,
    include_raw: bool,
) -> EvalCaseResult:
    started_at = perf_counter()
    try:
        plan, source, raw_output = planner.create_plan(case.goal, model_name, model_mode)
    except Exception as exc:  # evaluation must classify provider/infrastructure faults
        return _infrastructure_result(case, _error_text(exc), _elapsed_ms(started_at))
    latency_ms = _elapsed_ms(started_at)
    actual_tools = tuple(step.tool_call.tool_name for step in plan.steps)
    errors = _planner_errors(case, actual_tools, source)
    failure_type = _classify_planner_failure(case, errors, source, raw_output)
    return EvalCaseResult(
        id=case.id,
        kind=case.kind,
        passed=not errors,
        score=1.0 if not errors else 0.0,
        errors=tuple(errors),
        latency_ms=latency_ms,
        actual_tools=actual_tools,
        source=source,
        raw_output=raw_output if include_raw else None,
        failure_type=failure_type,
    )


def _run_tool_use_case(
    case: EvalCase,
    tool_use_agent: ToolUseAgent,
    tools: ToolRegistry,
    model_name: str | None,
    model_mode: str,
) -> EvalCaseResult:
    started_at = perf_counter()
    if case.tool_name is None:
        raise ValueError(f"Tool-use eval case {case.id} requires tool_name.")
    try:
        tool = tools.get(case.tool_name)
    except KeyError as exc:
        return EvalCaseResult(
            id=case.id,
            kind=case.kind,
            passed=False,
            score=0.0,
            errors=(str(exc),),
            latency_ms=_elapsed_ms(started_at),
            actual_tools=(case.tool_name,),
            failure_type="infrastructure",
        )
    try:
        resolution = tool_use_agent.build(
            goal=case.goal,
            tool=tool,
            arguments=case.rough_arguments,
            model_name=model_name,
            model_mode=model_mode,
            prior_results=case.prior_results,
        )
    except Exception as exc:
        return _infrastructure_result(case, _error_text(exc), _elapsed_ms(started_at))
    latency_ms = _elapsed_ms(started_at)
    errors = _tool_use_errors(case, resolution.arguments, resolution.error)
    failure_type = (
        "infrastructure"
        if resolution.error and _looks_like_infrastructure(resolution.error)
        else ("model_quality" if errors else "none")
    )
    return EvalCaseResult(
        id=case.id,
        kind=case.kind,
        passed=not errors,
        score=1.0 if not errors else 0.0,
        errors=tuple(errors),
        latency_ms=latency_ms,
        actual_tools=(case.tool_name,),
        actual_arguments=resolution.arguments,
        raw_output=(
            resolution.attempts[-1].raw_output
            if resolution.attempts and resolution.attempts[-1].raw_output
            else None
        ),
        failure_type=failure_type,
    )


def _planner_errors(
    case: EvalCase,
    actual_tools: tuple[str, ...],
    source: str,
) -> list[str]:
    errors: list[str] = []
    if source == "fallback" and not case.allow_fallback:
        errors.append("Planner used fallback.")
    missing_tools = [
        tool for tool in case.expected_tools if tool not in actual_tools
    ]
    if missing_tools:
        errors.append(f"Missing expected tool(s): {', '.join(missing_tools)}.")
    for group in case.expected_tool_groups:
        if not any(tool in actual_tools for tool in group):
            errors.append(
                "Missing one required tool from group: "
                + ", ".join(group)
                + "."
            )
    if not _contains_ordered_subset(actual_tools, case.expected_tools):
        expected = " -> ".join(case.expected_tools)
        actual = " -> ".join(actual_tools)
        errors.append(f"Expected tool order {expected}; got {actual}.")
    forbidden = [tool for tool in case.forbidden_tools if tool in actual_tools]
    if forbidden:
        errors.append(f"Used forbidden tool(s): {', '.join(forbidden)}.")
    if case.max_steps is not None and len(actual_tools) > case.max_steps:
        errors.append(
            f"Expected at most {case.max_steps} step(s); got {len(actual_tools)}."
        )
    return errors


def _tool_use_errors(
    case: EvalCase,
    actual_arguments: dict[str, Any],
    resolution_error: str | None,
) -> list[str]:
    errors: list[str] = []
    if resolution_error is not None:
        errors.append(resolution_error)
    for key in case.required_argument_keys:
        if key not in actual_arguments:
            errors.append(f"Missing required output argument: {key}.")
    for key in case.forbidden_argument_keys:
        if key in actual_arguments:
            errors.append(f"Unexpected output argument: {key}.")
    for key, expected in case.expected_arguments.items():
        actual = actual_arguments.get(key)
        if actual != expected:
            errors.append(
                f"Argument {key!r} expected {expected!r}; got {actual!r}."
            )
    return errors


def _infrastructure_result(
    case: EvalCase, error: str, latency_ms: float
) -> EvalCaseResult:
    """Build a non-scoring result for provider, registry, or runtime faults."""
    return EvalCaseResult(
        id=case.id,
        kind=case.kind,
        passed=False,
        score=0.0,
        errors=(error,),
        latency_ms=latency_ms,
        actual_tools=((case.tool_name,) if case.tool_name else ()),
        failure_type="infrastructure",
    )


def _error_text(error: Exception) -> str:
    return f"Evaluation infrastructure failure: {error}"


def _looks_like_infrastructure(message: str) -> bool:
    text = message.lower()
    markers = (
        "quota",
        "rate limit",
        "timed out",
        "timeout",
        "authentication",
        "api key",
        "missing dependency",
        "connection refused",
        "connection reset",
        "mcp http request failed",
        "ollama request failed",
        "gemini request failed",
    )
    return any(marker in text for marker in markers)


def _classify_planner_failure(
    case: EvalCase,
    errors: list[str],
    source: str,
    raw_output: str | None,
) -> FailureType:
    if not errors:
        return "none"
    # Planner converts provider errors to a fallback with the exception text as
    # raw output. Invalid JSON/model choices remain model-quality failures.
    if source == "fallback" and raw_output and _looks_like_infrastructure(raw_output):
        return "infrastructure"
    return "model_quality"


def _contains_ordered_subset(
    actual_tools: tuple[str, ...],
    expected_tools: tuple[str, ...],
) -> bool:
    if not expected_tools:
        return True
    expected_index = 0
    for actual_tool in actual_tools:
        if actual_tool == expected_tools[expected_index]:
            expected_index += 1
            if expected_index == len(expected_tools):
                return True
    return False


def _eval_case_from_data(data: Any) -> EvalCase:
    if not isinstance(data, dict):
        raise ValueError("Each eval case must be a JSON object.")
    kind = _required_string(data, "kind")
    if kind not in {"planner", "tool_use"}:
        raise ValueError(f"Unsupported eval case kind: {kind}")
    expected_arguments = data.get("expected_arguments", {})
    rough_arguments = data.get("rough_arguments", {})
    if not isinstance(expected_arguments, dict):
        raise ValueError("expected_arguments must be a JSON object.")
    if not isinstance(rough_arguments, dict):
        raise ValueError("rough_arguments must be a JSON object.")
    return EvalCase(
        id=_required_string(data, "id"),
        kind=kind,  # type: ignore[arg-type]
        goal=_required_string(data, "goal"),
        expected_tools=tuple(_string_list(data.get("expected_tools"))),
        expected_tool_groups=tuple(_tool_groups(data.get("expected_tool_groups"))),
        forbidden_tools=tuple(_string_list(data.get("forbidden_tools"))),
        max_steps=_optional_int(data.get("max_steps")),
        allow_fallback=_optional_bool(data.get("allow_fallback"), default=False),
        tool_name=_optional_string(data.get("tool_name")),
        rough_arguments=rough_arguments,
        prior_results=tuple(_tool_results_from_data(data.get("prior_results"))),
        expected_arguments=expected_arguments,
        required_argument_keys=tuple(_string_list(data.get("required_argument_keys"))),
        forbidden_argument_keys=tuple(
            _string_list(data.get("forbidden_argument_keys"))
        ),
    )


def _tool_results_from_data(value: Any) -> list[ToolResult]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("prior_results must be a list of JSON objects.")
    results: list[ToolResult] = []
    for item in value:
        if not isinstance(item, dict):
            raise ValueError("Each prior result must be a JSON object.")
        output = item.get("output", {})
        if not isinstance(output, dict):
            raise ValueError("Each prior result output must be a JSON object.")
        results.append(
            ToolResult(
                tool_name=_required_string(item, "tool_name"),
                output=output,
                success=_optional_bool(item.get("success"), default=True),
                error=_optional_string(item.get("error")),
            )
        )
    return results


def _required_string(data: dict[str, Any], key: str) -> str:
    value = _optional_string(data.get(key))
    if value is None:
        raise ValueError(f"Eval suite item requires {key}.")
    return value


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"Expected string value, got {type(value).__name__}.")
    stripped = value.strip()
    return stripped or None


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("Expected a list of strings.")
    strings: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise ValueError("Expected a list of strings.")
        stripped = item.strip()
        if stripped:
            strings.append(stripped)
    return strings


def _tool_groups(value: Any) -> list[tuple[str, ...]]:
    """Parse groups where one listed tool is sufficient for an eval case."""
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("expected_tool_groups must be a list of string lists.")
    groups: list[tuple[str, ...]] = []
    for group in value:
        tools = tuple(_string_list(group))
        if not tools:
            raise ValueError("expected_tool_groups cannot contain an empty group.")
        groups.append(tools)
    return groups


def _optional_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if not isinstance(value, bool):
        raise ValueError(f"Expected boolean value, got {type(value).__name__}.")
    return value


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    if not isinstance(value, int):
        raise ValueError(f"Expected integer value, got {type(value).__name__}.")
    return value


def _elapsed_ms(started_at: float) -> float:
    return round((perf_counter() - started_at) * 1000, 3)
