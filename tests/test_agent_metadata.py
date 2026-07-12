from __future__ import annotations

import unittest

from jarvis.agents import AgentRegistry
from jarvis.contracts import AgentSpec, ModelRequest, ToolCapability, ToolSpec
from jarvis.models import FakeModelProvider, ModelRouter
from jarvis.orchestration.agent_runtime import AgentRuntime
from jarvis.orchestration.orchestrator import _agent_can_use_tool
from jarvis.orchestration.planner import _agent_for_tool
from jarvis.tools.registry import ToolRegistry


class AgentMetadataTests(unittest.TestCase):
    def test_capability_domain_metadata_routes_without_provider_names(self) -> None:
        agents = AgentRegistry()
        agents.register(
            AgentSpec(
                name="research",
                description="Handles research capabilities.",
                allowed_tools=(),
                capability_domains=("research",),
            )
        )
        tools = ToolRegistry()
        tools.register(
            ToolSpec(
                name="vendor.lookup",
                description="Look up a vendor record.",
                capability=ToolCapability(domain="research", operation="lookup"),
            ),
            lambda _: {"text": "ok"},
        )

        self.assertEqual(_agent_for_tool(agents, tools, "vendor.lookup"), "research")
        self.assertTrue(
            _agent_can_use_tool(agents.get("research"), tools.get("vendor.lookup"))
        )

    def test_invalid_profile_metadata_fails_at_contract_boundary(self) -> None:
        with self.assertRaises(ValueError):
            AgentSpec(name="", description="bad", allowed_tools=())
        with self.assertRaises(ValueError):
            AgentSpec(
                name="ok",
                description="bad",
                allowed_tools=(),
                capability_domains=("",),
            )

    def test_agent_runtime_uses_profile_default_when_mode_is_empty(self) -> None:
        agent = AgentSpec(
            name="specialist",
            description="Specialist agent.",
            allowed_tools=(),
            default_model_mode="fast",
        )
        models = ModelRouter(
            {"fake-local": FakeModelProvider(), "fast-model": FakeModelProvider()},
            mode_routes={"fast": "fast-model"},
        )
        result = AgentRuntime(agent, models).run(
            ModelRequest(goal="hello", mode=""),
            explicit_model=None,
        )
        self.assertEqual(result.provider_name, "fast-model")


if __name__ == "__main__":
    unittest.main()
