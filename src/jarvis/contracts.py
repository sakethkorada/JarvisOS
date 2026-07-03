"""Shared runtime contracts for the first JarvisOS slice."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Literal
from uuid import uuid4


RiskLevel = Literal["low", "medium", "high"]
PolicyStatus = Literal["allowed", "approval_required", "denied"]
StepStatus = Literal["pending", "completed", "failed", "approval_required"]
MemoryType = Literal["preference", "fact", "note", "context"]


def new_id(prefix: str) -> str:
    """Create a short readable identifier for traces and plan objects."""
    return f"{prefix}_{uuid4().hex[:12]}"


def utc_now() -> str:
    """Return the current UTC timestamp as an ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class ModelRequest:
    """Normalized request sent to any model provider."""

    goal: str
    messages: list[str] = field(default_factory=list)
    mode: str = "balanced"


@dataclass(frozen=True)
class ModelResponse:
    """Normalized response returned by any model provider."""

    text: str
    model_name: str


@dataclass(frozen=True)
class AgentSpec:
    """Configuration contract for a scoped specialist agent."""

    name: str
    description: str
    allowed_tools: tuple[str, ...]
    default_model_mode: str = "balanced"


@dataclass(frozen=True)
class ToolSpec:
    """Configuration contract for an executable capability."""

    name: str
    description: str
    risk_level: RiskLevel = "low"
    requires_approval: bool = False
    source: str = "builtin"


ToolHandler = Callable[[dict[str, Any]], dict[str, Any]]


@dataclass(frozen=True)
class ToolCall:
    """A request to execute a named tool with arguments."""

    tool_name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class ToolResult:
    """Normalized result from tool execution."""

    tool_name: str
    output: dict[str, Any]
    success: bool = True
    error: str | None = None


@dataclass(frozen=True)
class PolicyDecision:
    """Decision returned by the policy engine before tool execution."""

    status: PolicyStatus
    reason: str

    @property
    def allowed(self) -> bool:
        """Whether the action can run without further approval."""
        return self.status == "allowed"


@dataclass(frozen=True)
class PlanStep:
    """A single step in an execution plan."""

    id: str
    agent_name: str
    tool_call: ToolCall
    description: str
    status: StepStatus = "pending"


@dataclass(frozen=True)
class ExecutionPlan:
    """A structured plan produced for a user goal."""

    goal: str
    steps: tuple[PlanStep, ...]


@dataclass(frozen=True)
class TraceEvent:
    """One timestamped event recorded during a run."""

    event_type: str
    message: str
    timestamp: str = field(default_factory=utc_now)
    data: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RunResult:
    """Complete result returned by the orchestrator."""

    run_id: str
    goal: str
    plan: ExecutionPlan
    step_results: tuple[ToolResult, ...]
    trace: tuple[TraceEvent, ...]
    final_response: str
    status: str


@dataclass(frozen=True)
class MemoryRecord:
    """Durable memory stored for future runs."""

    id: str
    type: MemoryType
    content: str
    source: str
    created_at: str
    updated_at: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class MemoryCandidate:
    """Suggested memory that has not been persisted yet."""

    type: MemoryType
    content: str
    reason: str
    source: str = "run"
