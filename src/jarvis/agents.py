"""Agent registry and default scoped agents."""

from __future__ import annotations

from jarvis.contracts import AgentSpec


class AgentRegistry:
    """In-memory registry of available agent specifications."""

    def __init__(self) -> None:
        self._agents: dict[str, AgentSpec] = {}

    def register(self, agent: AgentSpec) -> None:
        """Register or replace an agent by name."""
        self._agents[agent.name] = agent

    def get(self, name: str) -> AgentSpec:
        """Return an agent by name or raise a clear lookup error."""
        try:
            return self._agents[name]
        except KeyError as exc:
            raise KeyError(f"Unknown agent: {name}") from exc

    def list(self) -> list[AgentSpec]:
        """Return registered agents in stable display order."""
        return sorted(self._agents.values(), key=lambda agent: agent.name)


def default_agent_registry() -> AgentRegistry:
    """Create the built-in agents for the barebones local runtime."""
    registry = AgentRegistry()
    registry.register(
        AgentSpec(
            name="planner",
            description="Chooses a validated plan from registered capabilities.",
            allowed_tools=("*",),
            execution_role="planner",
            output_contract="execution_plan_json",
        )
    )
    registry.register(
        AgentSpec(
            name="tool_use",
            description="Builds JSON arguments for one selected tool.",
            allowed_tools=("*",),
            execution_role="tool_use",
            output_contract="tool_arguments_json",
        )
    )
    registry.register(
        AgentSpec(
            name="orchestrator",
            description="Creates simple execution plans and summarizes results.",
            allowed_tools=("task.breakdown", "task.create", "task.create_summary"),
            execution_role="orchestrator",
        )
    )
    registry.register(
        AgentSpec(
            name="general",
            description="Generates and transforms text with the selected model.",
            allowed_tools=("general.generate_text",),
            execution_role="general",
        )
    )
    registry.register(
        AgentSpec(
            name="memory",
            description="Retrieves lightweight local context.",
            allowed_tools=("memory.search",),
            execution_role="memory",
        )
    )
    registry.register(
        AgentSpec(
            name="system",
            description="Reads safe local runtime context.",
            allowed_tools=("system.current_datetime",),
            execution_role="system",
        )
    )
    registry.register(
        AgentSpec(
            name="calendar",
            description="Handles configured calendar tools.",
            allowed_tools=(),
            execution_role="tool_use",
            capability_domains=("calendar",),
        )
    )
    registry.register(
        AgentSpec(
            name="email",
            description="Handles configured email tools.",
            allowed_tools=(),
            execution_role="tool_use",
            capability_domains=("email",),
        )
    )
    registry.register(
        AgentSpec(
            name="music",
            description="Handles configured music tools.",
            allowed_tools=(),
            execution_role="tool_use",
            capability_domains=("music",),
        )
    )
    registry.register(
        AgentSpec(
            name="plugin",
            description="Runs tools provided by user-managed local plugins.",
            allowed_tools=("*",),
            execution_role="tool_use",
        )
    )
    registry.register(
        AgentSpec(
            name="synthesis",
            description="Writes the final answer from confirmed tool results.",
            allowed_tools=(),
            execution_role="synthesis",
            output_contract="final_answer_text",
        )
    )
    return registry
