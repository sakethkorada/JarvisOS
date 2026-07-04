"""Final response synthesis for completed JarvisOS runs."""

from __future__ import annotations

import json
from dataclasses import dataclass

from jarvis.contracts import ExecutionPlan, ModelRequest, ToolResult
from jarvis.errors import ModelProviderError
from jarvis.models import ModelRouter


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

    def synthesize(
        self,
        goal: str,
        plan: ExecutionPlan,
        results: tuple[ToolResult, ...],
        status: str,
        model_name: str | None,
        model_mode: str,
    ) -> SynthesisResult:
        """Try model synthesis first, then return deterministic fallback."""
        selected_model = model_name or "default"
        if model_name == "fake-local":
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
            response = self._models.run(request, model_name)
        except ModelProviderError as exc:
            return SynthesisResult(
                text=deterministic_summary(goal, results, status),
                source="failed_then_fallback",
                model_name=selected_model,
                error=exc,
            )

        text = response.text.strip()
        if not text or not _is_supported_synthesis(text, plan, results):
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
    failed = [result for result in results if not result.success]
    lines = [
        f"Goal: {goal}",
        f"Status: {status}",
        "",
        "Completed tool calls:",
    ]
    for result in results:
        marker = "OK" if result.success else "BLOCKED"
        lines.append(f"- {marker} {result.tool_name}")
    if failed:
        lines.append("")
        lines.append("Attention needed:")
        for result in failed:
            lines.append(f"- {result.tool_name}: {result.error}")
    grounded = grounded_result_lines(results)
    if grounded:
        lines.append("")
        lines.append("Grounded results:")
        lines.extend(grounded)
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
        elif result.tool_name == "calendar.search_events":
            events = result.output.get("events", [])
            if events:
                lines.append("- Calendar events:")
                for item in events:
                    if isinstance(item, dict):
                        title = item.get("title", "Untitled")
                        time = item.get("time", "time unknown")
                        notes = item.get("notes", "")
                        line = f"  - {title} ({time})"
                        if notes:
                            line = f"{line}: {notes}"
                        lines.append(line)
                    else:
                        lines.append(f"  - {item}")
        elif result.tool_name == "task.create":
            task = result.output.get("task")
            if isinstance(task, dict):
                title = task.get("title", "Untitled task")
                task_id = task.get("id", "unknown")
                lines.append("- Created task:")
                lines.append(f"  - {title} [{task_id}]")
        elif result.output.get("text"):
            lines.append(f"- {result.tool_name}: {result.output['text']}")
    return lines


def _synthesis_context(
    plan: ExecutionPlan,
    results: tuple[ToolResult, ...],
    status: str,
) -> str:
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
        "tool_results": [
            {
                "tool_name": result.tool_name,
                "success": result.success,
                "error": result.error,
                "output": result.output,
            }
            for result in results
        ],
    }
    return "Confirmed run data:\n" + json.dumps(payload, indent=2)


def _is_supported_synthesis(
    text: str,
    plan: ExecutionPlan,
    results: tuple[ToolResult, ...],
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
    unsupported_detail_phrases = (
        "project start date",
        "key milestone",
        "upcoming deadline",
        "list of open questions",
    )
    for phrase in unsupported_detail_phrases:
        if phrase in lowered and phrase not in source_text:
            return False
    if "[" in text and "]" in text:
        return False
    return True
