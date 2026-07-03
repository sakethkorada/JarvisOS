"""Deterministic policy checks for tool execution."""

from __future__ import annotations

from jarvis.contracts import PolicyDecision, ToolSpec


class PolicyEngine:
    """Evaluates whether a tool may run automatically."""

    def evaluate(self, tool: ToolSpec) -> PolicyDecision:
        """Return the deterministic policy decision for a tool."""
        if tool.requires_approval:
            return PolicyDecision(
                status="approval_required",
                reason=f"{tool.name} requires approval by tool declaration.",
            )
        if tool.risk_level == "high":
            return PolicyDecision(
                status="approval_required",
                reason=f"{tool.name} is high risk.",
            )
        return PolicyDecision(status="allowed", reason=f"{tool.name} is allowed.")
