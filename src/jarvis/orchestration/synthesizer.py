"""Final response synthesis for completed JarvisOS runs."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from jarvis.contracts import (
    AgentSpec,
    AvailableTool,
    ExecutionPlan,
    ModelRequest,
    ToolResult,
)
from jarvis.errors import ModelProviderError
from jarvis.models import ModelRouter
from jarvis.orchestration.agent_runtime import AgentRuntime


@dataclass(frozen=True)
class SynthesisResult:
    """Final answer text plus metadata about how it was produced."""

    text: str
    source: str
    model_name: str
    error: ModelProviderError | None = None


class Synthesizer:
    """Creates the final user-facing answer from confirmed tool results."""

    def __init__(self, models: ModelRouter, system_prompt: str) -> None:
        self._models = models
        self._system_prompt = system_prompt
        self._agent_runtime = AgentRuntime(
            AgentSpec(
                name="synthesis",
                description="Writes the final answer from confirmed tool results.",
                allowed_tools=(),
                execution_role="synthesis",
                output_contract="final_answer_text",
            ),
            models,
        )

    def synthesize(
        self,
        goal: str,
        plan: ExecutionPlan,
        results: tuple[ToolResult, ...],
        available_tools: tuple[AvailableTool, ...],
        status: str,
        model_name: str | None,
        model_mode: str,
    ) -> SynthesisResult:
        """Try model synthesis first, then return deterministic fallback."""
        selected_model = self._agent_runtime.resolve_model_name(model_name, model_mode)
        if selected_model == "fake-local":
            return SynthesisResult(
                text=deterministic_summary(goal, results, status),
                source="fallback",
                model_name=selected_model,
            )

        request = ModelRequest(
            goal=goal,
            mode=model_mode,
            system_prompt=self._system_prompt,
            messages=[_synthesis_context(plan, results, status)],
        )
        try:
            response = self._agent_runtime.run(request, model_name).response
        except ModelProviderError as exc:
            return SynthesisResult(
                text=deterministic_summary(goal, results, status),
                source="failed_then_fallback",
                model_name=selected_model,
                error=exc,
            )

        text = response.text.strip()
        if not text or not _is_supported_synthesis(
            text,
            plan,
            results,
            available_tools,
        ):
            error = ModelProviderError(
                "Model returned an empty or unsupported synthesis response.",
                component=response.model_name,
            )
            return SynthesisResult(
                text=deterministic_summary(goal, results, status),
                source="failed_then_fallback",
                model_name=response.model_name,
                error=error,
            )
        return SynthesisResult(text=text, source="llm", model_name=response.model_name)


def deterministic_summary(
    goal: str,
    results: tuple[ToolResult, ...],
    status: str,
) -> str:
    """Build a deterministic user-facing summary from tool results."""
    del goal
    failed = [result for result in results if not result.success]
    grounded = grounded_result_lines(results)
    lines: list[str] = []

    if failed:
        lines.append("I couldn't complete everything.")
        for result in failed:
            lines.append(f"- {result.tool_name}: {result.error}")

    if grounded:
        if failed:
            lines.append("")
        lines.extend(grounded)
    elif not failed:
        if status == "completed":
            lines.append("Done.")
        else:
            lines.append(f"The run finished with status: {status}.")
    return "\n".join(lines)


def grounded_result_lines(results: tuple[ToolResult, ...]) -> list[str]:
    """Create concise user-facing lines from actual tool outputs."""
    lines: list[str] = []
    for result in results:
        if not result.success:
            continue
        if result.tool_name == "memory.search":
            matches = result.output.get("matches", [])
            if matches:
                lines.append("- Memory matches:")
                for item in matches:
                    lines.append(f"  - {item.get('content', '')}")
        elif result.tool_name == "notes.search":
            matches = result.output.get("matches", [])
            if matches:
                lines.append("- Notes matches:")
                for item in matches:
                    title = item.get("title", "Untitled")
                    body = item.get("body", "")
                    lines.append(f"  - {title}: {body}")
        elif result.tool_name == "task.create":
            task = result.output.get("task")
            if isinstance(task, dict):
                title = task.get("title", "Untitled task")
                task_id = task.get("id", "unknown")
                lines.append(f"Created task: {title} [{task_id}]")
        elif result.tool_name == "task.breakdown":
            steps = result.output.get("steps", [])
            if steps:
                lines.append("Suggested steps:")
                for step in steps:
                    lines.append(f"- {step}")
        elif result.tool_name == "task.create_summary":
            continue
        elif result.output.get("text"):
            lines.append(str(result.output["text"]))
    return lines


def _synthesis_context(
    plan: ExecutionPlan,
    results: tuple[ToolResult, ...],
    status: str,
) -> str:
    successful_results = [result for result in results if result.success]
    failed_results = [result for result in results if not result.success]
    completed_tools = {result.tool_name for result in results}
    payload = {
        "run_status": status,
        "plan": [
            {
                "agent_name": step.agent_name,
                "tool_name": step.tool_call.tool_name,
                "description": step.description,
                "status": step.status,
            }
            for step in plan.steps
        ],
        "successful_tool_results": [
            {
                "tool_name": result.tool_name,
                "output": result.output,
            }
            for result in successful_results
        ],
        "failed_tool_results": [
            {
                "tool_name": result.tool_name,
                "error": result.error,
                "output": result.output,
            }
            for result in failed_results
        ],
        "planned_tools_without_result": [
            step.tool_call.tool_name
            for step in plan.steps
            if step.tool_call.tool_name not in completed_tools
        ],
    }
    return "Confirmed run data:\n" + json.dumps(payload, indent=2)


def _is_supported_synthesis(
    text: str,
    plan: ExecutionPlan,
    results: tuple[ToolResult, ...],
    available_tools: tuple[AvailableTool, ...],
) -> bool:
    """Reject obvious claims that are not supported by run data."""
    lowered = text.lower()
    source_text = json.dumps(
        [result.output for result in results],
        sort_keys=True,
    ).lower()
    has_pending_approval = any(
        step.status == "approval_required" for step in plan.steps
    )
    has_blocked_result = any(not result.success for result in results)
    if "pending approval" in lowered and not has_pending_approval:
        return False
    if "approval" in lowered and not has_pending_approval and not has_blocked_result:
        return False
    runtime_headings = (
        "based on the provided tool results",
        "based on the tool results",
        "completed tool calls",
        "grounded results",
        "confirmed run data",
    )
    for phrase in runtime_headings:
        if phrase in lowered:
            return False
    unsupported_detail_phrases = (
        "project start date",
        "key milestone",
        "upcoming deadline",
        "list of open questions",
    )
    for phrase in unsupported_detail_phrases:
        if phrase in lowered and phrase not in source_text:
            return False
    if _mentions_unexecuted_tool_family(text, available_tools, results):
        return False
    if _claims_success_for_failed_tool_family(text, available_tools, results):
        return False
    speculation_markers = ("likely", "probably", "it seems", "appears to be")
    if any(marker in lowered and marker not in source_text for marker in speculation_markers):
        return False
    if "[" in text and "]" in text:
        return False
    return True


def _mentions_unexecuted_tool_family(
    text: str,
    available_tools: tuple[AvailableTool, ...],
    results: tuple[ToolResult, ...],
) -> bool:
    """Reject references to registered tool families that produced no result."""
    observed_families = {
        _tool_family(tool)
        for tool in available_tools
        if any(result.tool_name == tool.name for result in results)
    }
    for family, terms in _tool_family_terms(available_tools).items():
        if family in observed_families:
            continue
        if any(_contains_term(text, term) for term in terms):
            return True
    return False


def _claims_success_for_failed_tool_family(
    text: str,
    available_tools: tuple[AvailableTool, ...],
    results: tuple[ToolResult, ...],
) -> bool:
    """Require failure wording when a referenced tool family only failed."""
    outcomes: dict[str, list[bool]] = {}
    tool_by_name = {tool.name: tool for tool in available_tools}
    for result in results:
        tool = tool_by_name.get(result.tool_name)
        if tool is None:
            continue
        outcomes.setdefault(_tool_family(tool), []).append(result.success)

    failure_markers = (
        "couldn't",
        "could not",
        "unable",
        "unavailable",
        "failed",
        "error",
        "blocked",
        "permission",
    )
    for family, terms in _tool_family_terms(available_tools).items():
        family_outcomes = outcomes.get(family, [])
        if not family_outcomes or any(family_outcomes):
            continue
        for sentence in _sentences(text):
            if any(_contains_term(sentence, term) for term in terms):
                if not any(marker in sentence.lower() for marker in failure_markers):
                    return True
    return False


def _tool_family_terms(
    available_tools: tuple[AvailableTool, ...],
) -> dict[str, set[str]]:
    """Build display terms from tool names and declared capability metadata."""
    terms_by_family: dict[str, set[str]] = {}
    ignored_terms = {
        "call",
        "create",
        "current",
        "generate",
        "get",
        "list",
        "recent",
        "search",
        "summary",
    }
    for tool in available_tools:
        family = _tool_family(tool)
        terms = terms_by_family.setdefault(family, {family})
        for token in re.split(r"[._-]", tool.name):
            normalized = token.strip().lower()
            if len(normalized) >= 4 and normalized not in ignored_terms:
                terms.add(normalized)
    return terms_by_family


def _tool_family(tool: AvailableTool) -> str:
    """Return the semantic family for a registered tool."""
    if tool.capability is not None:
        return tool.capability.domain.lower()
    return tool.name.split(".", 1)[0].lower()


def _contains_term(text: str, term: str) -> bool:
    """Return whether a standalone metadata term appears in user-facing text."""
    return re.search(rf"\b{re.escape(term)}s?\b", text, flags=re.IGNORECASE) is not None


def _sentences(text: str) -> tuple[str, ...]:
    """Split concise synthesis text into sentences for failure-claim checks."""
    return tuple(sentence for sentence in re.split(r"(?<=[.!?])\s+", text) if sentence)
