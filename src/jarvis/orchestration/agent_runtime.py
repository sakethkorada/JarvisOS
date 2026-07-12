"""Generic runtime wrapper for model-backed agent profiles."""

from __future__ import annotations

from dataclasses import dataclass

from jarvis.contracts import AgentSpec, ModelRequest, ModelResponse
from jarvis.models import ModelRouter


@dataclass(frozen=True)
class AgentRunResult:
    """Model output produced through an agent profile."""

    response: ModelResponse
    agent_name: str
    execution_role: str
    provider_name: str


class AgentRuntime:
    """Routes an agent profile's model request through the model router."""

    def __init__(self, agent: AgentSpec, models: ModelRouter) -> None:
        self._agent = agent
        self._models = models

    @property
    def agent(self) -> AgentSpec:
        """Return the agent profile used by this runtime."""
        return self._agent

    def resolve_model_name(
        self,
        explicit_model: str | None,
        mode: str,
    ) -> str:
        """Resolve the concrete model provider for this agent request."""
        effective_mode = mode or self._agent.default_model_mode
        return self._models.resolve_provider_name(
            explicit_provider_name=explicit_model,
            mode=effective_mode,
            role=self._agent.execution_role,
        )

    def run(
        self,
        request: ModelRequest,
        explicit_model: str | None,
    ) -> AgentRunResult:
        """Run a model request using this agent's execution role."""
        effective_mode = request.mode or self._agent.default_model_mode
        if effective_mode != request.mode:
            request = ModelRequest(
                goal=request.goal,
                messages=request.messages,
                mode=effective_mode,
                system_prompt=request.system_prompt,
            )
        provider_name = self.resolve_model_name(explicit_model, effective_mode)
        response = self._models.run(
            request,
            provider_name=explicit_model,
            role=self._agent.execution_role,
        )
        return AgentRunResult(
            response=response,
            agent_name=self._agent.name,
            execution_role=self._agent.execution_role,
            provider_name=provider_name,
        )
