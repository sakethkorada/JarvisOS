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
ApprovalStatus = Literal["pending", "approved", "rejected"]
TaskStatus = Literal["open", "done"]


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
    system_prompt: str | None = None


@dataclass(frozen=True)
class ModelResponse:
    """Normalized response returned by any model provider."""

    text: str
    model_name: str


@dataclass(frozen=True)
class AgentSpec:
    """Configuration contract for a scoped agent profile."""

    name: str
    description: str
    allowed_tools: tuple[str, ...]
    default_model_mode: str = "balanced"
    execution_role: str = "general"
    prompt_ref: str | None = None
    output_contract: str | None = None
    memory_scope: str | None = None
    risk_permissions: tuple[str, ...] = ()
    capability_domains: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """Validate profile metadata at the contract boundary.

        Agent metadata is consumed by planners and policy-aware runtimes, so
        malformed profiles should fail when registered rather than producing
        implicit, provider-specific behavior later in a run.
        """
        for field_name in ("name", "description", "default_model_mode", "execution_role"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"AgentSpec.{field_name} must be a non-empty string")
        for field_name in (
            "allowed_tools",
            "risk_permissions",
            "capability_domains",
        ):
            values = getattr(self, field_name)
            if any(not isinstance(value, str) or not value.strip() for value in values):
                raise ValueError(f"AgentSpec.{field_name} entries must be non-empty strings")
        for field_name in ("prompt_ref", "output_contract", "memory_scope"):
            value = getattr(self, field_name)
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise ValueError(f"AgentSpec.{field_name} must be a non-empty string when set")


@dataclass(frozen=True)
class ToolCapability:
    """Semantic metadata used for deterministic tool selection."""

    domain: str
    operation: str
    provider: str | None = None
    read_only: bool = True
    demo: bool = False


@dataclass(frozen=True)
class ToolSpec:
    """Configuration contract for an executable capability."""

    name: str
    description: str
    argument_hints: str | None = None
    risk_level: RiskLevel = "low"
    requires_approval: bool = False
    source: str = "builtin"
    input_schema: dict[str, Any] | None = None
    capability: ToolCapability | None = None


@dataclass(frozen=True)
class AvailableTool:
    """Tool information exposed to a planner."""

    name: str
    description: str
    argument_hints: str | None
    risk_level: RiskLevel
    requires_approval: bool
    source: str
    input_schema: dict[str, Any] | None = None
    capability: ToolCapability | None = None


ToolHandler = Callable[[dict[str, Any]], dict[str, Any]]


@dataclass(frozen=True)
class ToolExecutionContext:
    """Runtime context available to model-backed internal tools."""

    goal: str
    model_name: str | None
    model_mode: str
    models: Any
    prior_results: tuple["ToolResult", ...] = ()


ContextToolHandler = Callable[
    [dict[str, Any], ToolExecutionContext],
    dict[str, Any],
]


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
    depends_on: tuple[str, ...] = ()
    output_key: str | None = None


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


@dataclass(frozen=True)
class ApprovalRecord:
    """Durable approval item waiting for a user decision."""

    id: str
    type: str
    status: ApprovalStatus
    title: str
    reason: str
    payload: dict[str, Any]
    run_id: str | None
    created_at: str
    decided_at: str | None = None


@dataclass(frozen=True)
class TaskRecord:
    """Durable local task created by a low-risk tool."""

    id: str
    title: str
    status: TaskStatus
    source: str
    created_at: str
    updated_at: str
    metadata: dict[str, Any] = field(default_factory=dict)
