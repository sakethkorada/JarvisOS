import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
import sys

from jarvis.approvals import ApprovalStore, apply_approved_record
from jarvis.contracts import ToolCall
from jarvis.contracts import ModelRequest, ModelResponse
from jarvis.errors import ModelProviderError
from jarvis.memory import MemoryExtractor, MemoryStore
from jarvis.mcp import McpStdioClient
from jarvis.models import ModelProvider, ModelRouter
from jarvis.planner import Planner
from jarvis.agents import default_agent_registry
from jarvis.prompts import PromptLibrary
from jarvis.runtime import create_default_orchestrator
from jarvis.runtime import create_default_tool_registry
from jarvis.settings import McpServerSettings, load_settings
from jarvis.tasks import TaskStore
from jarvis.tools import default_tool_registry
from jarvis.traces import TraceStore


class RuntimeTests(unittest.TestCase):
    """Tests for the default local runtime loop."""

    def test_simple_goal_runs(self) -> None:
        result = create_default_orchestrator().run(
            "break this task into steps",
            model_name="fake-local",
        )

        self.assertEqual(result.status, "completed")
        self.assertIn("memory.search", [item.tool_name for item in result.step_results])
        self.assertIn("Goal: break this task into steps", result.final_response)
        trace_types = [event.event_type for event in result.trace]
        self.assertIn("synthesis.completed", trace_types)

    def test_meeting_goal_uses_calendar_capability(self) -> None:
        result = create_default_orchestrator().run(
            "prepare me for my meeting tomorrow",
            model_name="fake-local",
        )

        tool_names = [item.tool_name for item in result.step_results]
        self.assertIn("memory.search", tool_names)
        self.assertIn("calendar.search_events", tool_names)

    def test_plugin_notes_tool_runs_when_configured(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            plugin_path = _write_notes_plugin(root)
            config_path = root / "jarvis.toml"
            config_path.write_text(
                f"""
[plugins]
paths = ["{plugin_path.name}"]
""".strip(),
                encoding="utf-8",
            )

            settings = load_settings(config_path)
            result = create_default_orchestrator(settings).run(
                "find notes about Jordan",
                model_name="fake-local",
            )

        tool_names = [item.tool_name for item in result.step_results]
        self.assertIn("notes.search", tool_names)
        self.assertIn("Jordan meeting", result.final_response)

    def test_meeting_prep_demo_uses_calendar_and_notes(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            plugin_path = _write_notes_plugin(root)
            config_path = root / "jarvis.toml"
            config_path.write_text(
                f"""
[plugins]
paths = ["{plugin_path.name}"]
""".strip(),
                encoding="utf-8",
            )

            settings = load_settings(config_path)
            result = create_default_orchestrator(settings).run(
                "Prepare me for my meeting with Jordan tomorrow",
                model_name="fake-local",
            )

        tool_names = [item.tool_name for item in result.step_results]
        self.assertIn("calendar.search_events", tool_names)
        self.assertIn("notes.search", tool_names)
        self.assertIn("Jordan project sync", result.final_response)
        self.assertIn("Jordan meeting", result.final_response)

    def test_runtime_memory_search_reads_store(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "jarvis.toml"
            memory_path = root / "memory.sqlite3"
            config_path.write_text(
                f"""
[memory]
database_path = "{memory_path.name}"
auto_extract = false
""".strip(),
                encoding="utf-8",
            )
            settings = load_settings(config_path)
            MemoryStore(settings.memory.database_path).add(
                "User prefers meetings after 10 AM.",
                memory_type="preference",
            )

            result = create_default_orchestrator(settings).run(
                "meeting preferences",
                model_name="fake-local",
            )

        memory_result = next(
            item for item in result.step_results if item.tool_name == "memory.search"
        )
        self.assertEqual(
            memory_result.output["matches"][0]["content"],
            "User prefers meetings after 10 AM.",
        )

    def test_memory_candidate_creates_pending_approval(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "jarvis.toml"
            memory_path = root / "memory.sqlite3"
            approval_path = root / "approvals.sqlite3"
            config_path.write_text(
                f"""
[memory]
database_path = "{memory_path.name}"
auto_extract = true

[approvals]
database_path = "{approval_path.name}"
""".strip(),
                encoding="utf-8",
            )
            settings = load_settings(config_path)

            result = create_default_orchestrator(settings).run(
                "Remember that I prefer meetings after 10 AM.",
                model_name="fake-local",
            )
            approvals = ApprovalStore(settings.approvals.database_path).list()

        self.assertEqual(len(approvals), 1)
        self.assertEqual(approvals[0].type, "memory.add")
        self.assertEqual(approvals[0].run_id, result.run_id)
        self.assertIn(approvals[0].id, result.final_response)

    def test_task_create_goal_writes_local_task(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "jarvis.toml"
            task_path = root / "tasks.sqlite3"
            config_path.write_text(
                f"""
[tasks]
database_path = "{task_path.name}"
""".strip(),
                encoding="utf-8",
            )
            settings = load_settings(config_path)

            result = create_default_orchestrator(settings).run(
                "Create a task to ask Jordan about API migration",
                model_name="fake-local",
            )
            tasks = TaskStore(settings.tasks.database_path).list()

        self.assertEqual(result.status, "completed")
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0].title, "Ask Jordan about API migration")
        self.assertIn("task.create", [item.tool_name for item in result.step_results])

    def test_compound_goal_creates_clean_task_title(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "jarvis.toml"
            config_path.write_text(
                """
[tasks]
database_path = "tasks.sqlite3"
""".strip(),
                encoding="utf-8",
            )
            settings = load_settings(config_path)

            create_default_orchestrator(settings).run(
                "Prepare me for my meeting with Jordan tomorrow and "
                "create a task to ask Jordan about API migration",
                model_name="fake-local",
            )
            tasks = TaskStore(settings.tasks.database_path).list()

        self.assertEqual(tasks[0].title, "Ask Jordan about API migration")


class SettingsTests(unittest.TestCase):
    """Tests for provider-agnostic settings resolution."""

    def test_loads_model_defaults_from_toml(self) -> None:
        with TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "jarvis.toml"
            config_path.write_text(
                """
[models]
default = "fake-local"

[models.modes]
private = "fake-local"

[providers.ollama]
host = "http://localhost:11434"
models = ["llama3.2:3b"]
""".strip(),
                encoding="utf-8",
            )

            settings = load_settings(config_path)

        self.assertEqual(settings.models.default, "fake-local")
        self.assertEqual(settings.models.modes["private"], "fake-local")
        self.assertEqual(settings.providers.ollama_models, ("llama3.2:3b",))

    def test_model_resolution_precedence(self) -> None:
        with TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "jarvis.toml"
            config_path.write_text(
                """
[models]
default = "fake-local"

[models.modes]
private = "ollama/llama3.2:3b"
""".strip(),
                encoding="utf-8",
            )

            settings = load_settings(config_path)

        self.assertEqual(
            settings.resolve_model("manual-model", "private"),
            "manual-model",
        )
        self.assertEqual(
            settings.resolve_model(None, "private"),
            "ollama/llama3.2:3b",
        )
        self.assertEqual(settings.resolve_model(None, "balanced"), "fake-local")

    def test_resolves_plugin_paths_relative_to_config(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "jarvis.toml"
            config_path.write_text(
                """
[plugins]
paths = ["plugins/demo_notes"]
""".strip(),
                encoding="utf-8",
            )

            settings = load_settings(config_path)

        self.assertEqual(
            settings.plugins.paths,
            (Path(temp_dir) / "plugins" / "demo_notes",),
        )

    def test_loads_memory_settings_from_toml(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "jarvis.toml"
            config_path.write_text(
                """
[memory]
database_path = "state/memory.sqlite3"
auto_extract = true
auto_write = false
""".strip(),
                encoding="utf-8",
            )

            settings = load_settings(config_path)

        self.assertEqual(
            settings.memory.database_path,
            Path(temp_dir) / "state" / "memory.sqlite3",
        )
        self.assertTrue(settings.memory.auto_extract)
        self.assertFalse(settings.memory.auto_write)

    def test_loads_trace_settings_from_toml(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "jarvis.toml"
            config_path.write_text(
                """
[traces]
database_path = "state/traces.sqlite3"
enabled = false
""".strip(),
                encoding="utf-8",
            )

            settings = load_settings(config_path)

        self.assertEqual(
            settings.traces.database_path,
            Path(temp_dir) / "state" / "traces.sqlite3",
        )
        self.assertFalse(settings.traces.enabled)

    def test_loads_approval_settings_from_toml(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "jarvis.toml"
            config_path.write_text(
                """
[approvals]
database_path = "state/approvals.sqlite3"
""".strip(),
                encoding="utf-8",
            )

            settings = load_settings(config_path)

        self.assertEqual(
            settings.approvals.database_path,
            Path(temp_dir) / "state" / "approvals.sqlite3",
        )

    def test_loads_task_settings_from_toml(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "jarvis.toml"
            config_path.write_text(
                """
[tasks]
database_path = "state/tasks.sqlite3"
""".strip(),
                encoding="utf-8",
            )

            settings = load_settings(config_path)

        self.assertEqual(
            settings.tasks.database_path,
            Path(temp_dir) / "state" / "tasks.sqlite3",
        )

    def test_loads_mcp_server_settings_from_toml(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "jarvis.toml"
            config_path.write_text(
                """
[[mcp.servers]]
name = "demo_mcp"
command = "python"
args = ["demo_server.py"]
risk_level = "low"
requires_approval = false
""".strip(),
                encoding="utf-8",
            )

            settings = load_settings(config_path)

        self.assertEqual(len(settings.mcp.servers), 1)
        self.assertEqual(settings.mcp.servers[0].name, "demo_mcp")
        self.assertEqual(settings.mcp.servers[0].args, ("demo_server.py",))

    def test_loads_prompt_override_paths_from_toml(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "jarvis.toml"
            config_path.write_text(
                """
[prompts]
planner = "prompts/planner.md"
synthesis = "prompts/synthesis.md"
""".strip(),
                encoding="utf-8",
            )

            settings = load_settings(config_path)

        self.assertEqual(
            settings.prompts.planner_path,
            Path(temp_dir) / "prompts" / "planner.md",
        )
        self.assertEqual(
            settings.prompts.synthesis_path,
            Path(temp_dir) / "prompts" / "synthesis.md",
        )


class MemoryTests(unittest.TestCase):
    """Tests for local SQLite memory storage."""

    def test_add_search_and_list_memory(self) -> None:
        with TemporaryDirectory() as temp_dir:
            memory_store = MemoryStore(Path(temp_dir) / "memory.sqlite3")
            memory_store.add(
                "User prefers meetings after 10 AM.",
                memory_type="preference",
            )

            search_results = memory_store.search("meeting preferences")
            list_results = memory_store.list()

        self.assertEqual(len(search_results), 1)
        self.assertEqual(search_results[0].type, "preference")
        self.assertEqual(len(list_results), 1)

    def test_memory_extractor_ignores_preference_questions(self) -> None:
        extractor = MemoryExtractor()

        candidates = extractor.suggest(
            "What are my meeting preferences?",
            "No stored preference found.",
        )

        self.assertEqual(candidates, [])

    def test_memory_extractor_suggests_explicit_preference(self) -> None:
        extractor = MemoryExtractor()

        candidates = extractor.suggest(
            "Remember that I prefer meetings after 10 AM.",
            "Suggested memory only.",
        )

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].type, "preference")


class TaskTests(unittest.TestCase):
    """Tests for local SQLite task storage."""

    def test_create_list_and_complete_tasks(self) -> None:
        with TemporaryDirectory() as temp_dir:
            task_store = TaskStore(Path(temp_dir) / "tasks.sqlite3")
            created = task_store.create("Ask Jordan about API migration.")

            tasks = task_store.list()
            shown = task_store.get(created.id)
            completed = task_store.complete(created.id)

        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0].status, "open")
        self.assertEqual(tasks[0].title, "Ask Jordan about API migration.")
        self.assertIsNotNone(shown)
        assert shown is not None
        self.assertEqual(shown.id, created.id)
        self.assertEqual(completed.status, "done")


class ApprovalTests(unittest.TestCase):
    """Tests for local SQLite approval storage."""

    def test_create_approve_and_apply_memory_approval(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            approval_store = ApprovalStore(root / "approvals.sqlite3")
            memory_store = MemoryStore(root / "memory.sqlite3")
            record = approval_store.create(
                approval_type="memory.add",
                title="Save memory",
                reason="The user stated an explicit preference.",
                payload={
                    "memory_type": "preference",
                    "content": "User prefers meetings after 10 AM.",
                    "source": "run",
                },
                run_id="run_test",
            )

            approved = approval_store.decide(record.id, "approved")
            effect = apply_approved_record(approved, memory_store)
            memories = memory_store.search("meetings")

        self.assertEqual(approved.status, "approved")
        self.assertEqual(effect, "Memory saved.")
        self.assertEqual(len(memories), 1)
        self.assertEqual(memories[0].type, "preference")

    def test_apply_memory_approval_skips_normalized_duplicate(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            approval_store = ApprovalStore(root / "approvals.sqlite3")
            memory_store = MemoryStore(root / "memory.sqlite3")
            memory_store.add(
                "User prefers meetings after 10 AM.",
                memory_type="preference",
            )
            record = approval_store.create(
                approval_type="memory.add",
                title="Save memory",
                reason="The user stated an explicit preference.",
                payload={
                    "memory_type": "preference",
                    "content": "Remember that I prefer meetings after 10 AM.",
                    "source": "run",
                },
            )

            approved = approval_store.decide(record.id, "approved")
            effect = apply_approved_record(approved, memory_store)
            memories = memory_store.list()

        self.assertIn("skipped duplicate", effect)
        self.assertEqual(len(memories), 1)

    def test_reject_approval_records_decision(self) -> None:
        with TemporaryDirectory() as temp_dir:
            approval_store = ApprovalStore(Path(temp_dir) / "approvals.sqlite3")
            record = approval_store.create(
                approval_type="tool.execute",
                title="Approve tool",
                reason="High risk.",
                payload={"tool_name": "email.send"},
            )

            rejected = approval_store.decide(record.id, "rejected")

        self.assertEqual(rejected.status, "rejected")
        self.assertIsNotNone(rejected.decided_at)


class PromptTests(unittest.TestCase):
    """Tests for prompt loading and override behavior."""

    def test_prompt_library_reads_custom_prompt_paths(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            planner_path = root / "planner.md"
            synthesis_path = root / "synthesis.md"
            planner_path.write_text("custom planner prompt", encoding="utf-8")
            synthesis_path.write_text("custom synthesis prompt", encoding="utf-8")

            prompts = PromptLibrary(planner_path, synthesis_path)

            self.assertEqual(prompts.planner_prompt(), "custom planner prompt")
            self.assertEqual(prompts.synthesis_prompt(), "custom synthesis prompt")


class TraceTests(unittest.TestCase):
    """Tests for SQLite trace persistence."""

    def test_save_list_and_show_run_trace(self) -> None:
        with TemporaryDirectory() as temp_dir:
            trace_store = TraceStore(Path(temp_dir) / "traces.sqlite3")
            result = create_default_orchestrator().run(
                "break this task into steps",
                model_name="fake-local",
            )

            trace_store.save_run(result)
            summaries = trace_store.list_runs()
            stored_trace = trace_store.get_run(result.run_id)

        self.assertEqual(len(summaries), 1)
        self.assertEqual(summaries[0].run_id, result.run_id)
        self.assertEqual(summaries[0].selected_model, "fake-local")
        self.assertIsNotNone(stored_trace)
        assert stored_trace is not None
        self.assertEqual(stored_trace.summary.goal, "break this task into steps")
        self.assertEqual(stored_trace.events[0].event_type, "run.started")


class PlannerTests(unittest.TestCase):
    """Tests for LLM-assisted planning validation."""

    def test_llm_plan_uses_validated_tool_steps(self) -> None:
        provider = StaticModelProvider(
            """
{
  "steps": [
    {
      "tool_name": "memory.search",
      "arguments": {"query": "Jordan"},
      "description": "Search memory."
    },
    {
      "tool_name": "task.create_summary",
      "arguments": {"goal": "Jordan"},
      "description": "Summarize results."
    }
  ]
}
""".strip()
        )
        with TemporaryDirectory() as temp_dir:
            tools = default_tool_registry(
                MemoryStore(Path(temp_dir) / "memory.sqlite3")
            )
            planner = Planner(
                default_agent_registry(),
                tools,
                ModelRouter(
                    {provider.name: provider},
                    default_provider_name=provider.name,
                ),
                PromptLibrary().planner_prompt(),
            )

            plan, source, raw_output = planner.create_plan(
                "Jordan",
                model_name=provider.name,
                model_mode="balanced",
            )

        self.assertEqual(source, "llm")
        self.assertIsNotNone(raw_output)
        self.assertEqual(plan.steps[0].tool_call.tool_name, "memory.search")

    def test_llm_plan_fills_builtin_default_arguments(self) -> None:
        provider = StaticModelProvider(
            """
{
  "steps": [
    {
      "tool_name": "task.create_summary",
      "arguments": {},
      "description": "Summarize results."
    }
  ]
}
""".strip()
        )
        with TemporaryDirectory() as temp_dir:
            planner = Planner(
                default_agent_registry(),
                default_tool_registry(MemoryStore(Path(temp_dir) / "memory.sqlite3")),
                ModelRouter(
                    {provider.name: provider},
                    default_provider_name=provider.name,
                ),
                PromptLibrary().planner_prompt(),
            )

            plan, source, _ = planner.create_plan(
                "summarize Jordan notes",
                model_name=provider.name,
                model_mode="balanced",
            )

        self.assertEqual(source, "llm")
        self.assertEqual(
            plan.steps[0].tool_call.arguments["goal"],
            "summarize Jordan notes",
        )


class SynthesisTests(unittest.TestCase):
    """Tests for final response synthesis and fallback behavior."""

    def test_orchestrator_uses_llm_synthesis_after_tool_execution(self) -> None:
        provider = SequencedModelProvider(
            [
                """
{
  "steps": [
    {
      "tool_name": "memory.search",
      "arguments": {"query": "Jordan"},
      "description": "Search memory."
    }
  ]
}
""".strip(),
                "Jordan context was synthesized from confirmed results.",
            ]
        )
        with TemporaryDirectory() as temp_dir:
            memory_store = MemoryStore(Path(temp_dir) / "memory.sqlite3")
            memory_store.add("Jordan owns the API migration.", memory_type="fact")
            orchestrator = _orchestrator_with_provider(provider, memory_store)

            result = orchestrator.run("prepare Jordan context", provider.name)

        self.assertEqual(
            result.final_response,
            "Jordan context was synthesized from confirmed results.",
        )
        synthesis_event = next(
            event for event in result.trace if event.event_type == "synthesis.completed"
        )
        self.assertEqual(synthesis_event.data["source"], "llm")

    def test_orchestrator_falls_back_when_synthesis_model_fails(self) -> None:
        provider = FailingAfterFirstModelProvider(
            """
{
  "steps": [
    {
      "tool_name": "memory.search",
      "arguments": {"query": "Jordan"},
      "description": "Search memory."
    }
  ]
}
""".strip()
        )
        with TemporaryDirectory() as temp_dir:
            memory_store = MemoryStore(Path(temp_dir) / "memory.sqlite3")
            memory_store.add("Jordan owns the API migration.", memory_type="fact")
            orchestrator = _orchestrator_with_provider(provider, memory_store)

            result = orchestrator.run("prepare Jordan context", provider.name)

        self.assertIn("Goal: prepare Jordan context", result.final_response)
        self.assertIn("Jordan owns the API migration.", result.final_response)
        synthesis_event = next(
            event for event in result.trace if event.event_type == "synthesis.completed"
        )
        self.assertEqual(synthesis_event.data["source"], "failed_then_fallback")
        self.assertEqual(
            synthesis_event.data["error"]["error_type"],
            "ModelProviderError",
        )

    def test_orchestrator_rejects_unsupported_synthesis_claims(self) -> None:
        provider = SequencedModelProvider(
            [
                """
{
  "steps": [
    {
      "tool_name": "memory.search",
      "arguments": {"query": "Jordan"},
      "description": "Search memory."
    }
  ]
}
""".strip(),
                "Jordan context is ready. There are pending approvals.",
            ]
        )
        with TemporaryDirectory() as temp_dir:
            memory_store = MemoryStore(Path(temp_dir) / "memory.sqlite3")
            memory_store.add("Jordan owns the API migration.", memory_type="fact")
            orchestrator = _orchestrator_with_provider(provider, memory_store)

            result = orchestrator.run("prepare Jordan context", provider.name)

        self.assertIn("Goal: prepare Jordan context", result.final_response)
        synthesis_event = next(
            event for event in result.trace if event.event_type == "synthesis.completed"
        )
        self.assertEqual(synthesis_event.data["source"], "failed_then_fallback")


class PluginTests(unittest.TestCase):
    """Tests for local plugin manifest loading."""

    def test_plugin_tool_is_registered_from_manifest(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            plugin_path = _write_notes_plugin(root)
            config_path = root / "jarvis.toml"
            config_path.write_text(
                f"""
[plugins]
paths = ["{plugin_path.name}"]
""".strip(),
                encoding="utf-8",
            )

            settings = load_settings(config_path)
            registry = create_default_tool_registry(settings)
            result = registry.execute(
                call=ToolCall("notes.search", {"query": "Jordan"})
            )

        self.assertTrue(result.success)
        self.assertEqual(result.tool_name, "notes.search")
        self.assertEqual(result.output["matches"][0]["title"], "Jordan meeting")


class McpTests(unittest.TestCase):
    """Tests for MCP stdio tool loading and execution."""

    def test_mcp_client_lists_and_calls_demo_tool(self) -> None:
        server = _demo_mcp_server_settings()
        client = McpStdioClient(server)

        tools = client.list_tools()
        result = client.call_tool("echo", {"text": "hello"})

        self.assertEqual(tools[0]["name"], "echo")
        self.assertEqual(result["content"][0]["text"], "demo echo: hello")

    def test_mcp_tool_is_registered_from_settings(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "jarvis.toml"
            command = sys.executable.replace("\\", "/")
            server_path = str(_demo_mcp_server_path()).replace("\\", "/")
            config_path.write_text(
                f"""
[[mcp.servers]]
name = "demo_mcp"
command = "{command}"
args = ["{server_path}"]
""".strip(),
                encoding="utf-8",
            )
            settings = load_settings(config_path)

            registry = create_default_tool_registry(settings)
            result = registry.execute(
                ToolCall("demo_mcp.echo", {"text": "from registry"})
            )

        self.assertTrue(result.success)
        self.assertEqual(result.output["text"], "demo echo: from registry")

    def test_orchestrator_can_execute_validated_mcp_tool_plan(self) -> None:
        provider = StaticModelProvider(
            """
{
  "steps": [
    {
      "tool_name": "demo_mcp.echo",
      "arguments": {"text": "orchestrated"},
      "description": "Call demo MCP echo."
    }
  ]
}
""".strip()
        )
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "jarvis.toml"
            command = sys.executable.replace("\\", "/")
            server_path = str(_demo_mcp_server_path()).replace("\\", "/")
            config_path.write_text(
                f"""
[[mcp.servers]]
name = "demo_mcp"
command = "{command}"
args = ["{server_path}"]
""".strip(),
                encoding="utf-8",
            )
            settings = load_settings(config_path)
            tools = create_default_tool_registry(settings)
            from jarvis.orchestrator import Orchestrator
            from jarvis.policies import PolicyEngine

            orchestrator = Orchestrator(
                agents=default_agent_registry(),
                tools=tools,
                models=ModelRouter({provider.name: provider}, provider.name),
                policies=PolicyEngine(),
                planner_prompt=PromptLibrary().planner_prompt(),
                synthesis_prompt=PromptLibrary().synthesis_prompt(),
            )

            result = orchestrator.run("call demo mcp", provider.name)

        self.assertEqual(result.status, "completed")
        self.assertEqual(result.step_results[0].tool_name, "demo_mcp.echo")
        self.assertEqual(
            result.step_results[0].output["text"],
            "demo echo: orchestrated",
        )

    def test_fake_local_fallback_can_execute_mcp_echo_tool(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "jarvis.toml"
            command = sys.executable.replace("\\", "/")
            server_path = str(_demo_mcp_server_path()).replace("\\", "/")
            config_path.write_text(
                f"""
[[mcp.servers]]
name = "demo_mcp"
command = "{command}"
args = ["{server_path}"]
""".strip(),
                encoding="utf-8",
            )
            settings = load_settings(config_path)

            result = create_default_orchestrator(settings).run(
                "echo hello from mcp",
                model_name="fake-local",
            )

        tool_names = [item.tool_name for item in result.step_results]
        self.assertIn("demo_mcp.echo", tool_names)
        self.assertIn("demo echo: echo hello from mcp", result.final_response)


def _write_notes_plugin(root: Path) -> Path:
    plugin_path = root / "demo_notes"
    plugin_path.mkdir()
    (plugin_path / "plugin.toml").write_text(
        """
name = "demo_notes"
description = "Demo local notes plugin."

[[tools]]
name = "notes.search"
description = "Search demo notes."
handler = "tools.search_notes"
risk_level = "low"
requires_approval = false
""".strip(),
        encoding="utf-8",
    )
    (plugin_path / "tools.py").write_text(
        '''
def search_notes(arguments):
    """Return a deterministic demo note match."""
    query = str(arguments.get("query", ""))
    return {
        "query": query,
        "matches": [
            {
                "title": "Jordan meeting",
                "body": "Discuss project timeline and open questions.",
            }
        ],
    }
'''.strip(),
        encoding="utf-8",
    )
    return plugin_path


def _demo_mcp_server_settings() -> McpServerSettings:
    return McpServerSettings(
        name="demo_mcp",
        command=sys.executable,
        args=(str(_demo_mcp_server_path()),),
    )


def _demo_mcp_server_path() -> Path:
    return Path(__file__).resolve().parents[1] / "examples" / "mcp" / "demo_server.py"


class StaticModelProvider(ModelProvider):
    """Model provider that returns a static response for tests."""

    name = "static-model"

    def __init__(self, text: str) -> None:
        self._text = text

    def generate(self, request: ModelRequest) -> ModelResponse:
        return ModelResponse(text=self._text, model_name=self.name)


class SequencedModelProvider(ModelProvider):
    """Model provider that returns one response per call."""

    name = "sequenced-model"

    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)

    def generate(self, request: ModelRequest) -> ModelResponse:
        text = self._responses.pop(0)
        return ModelResponse(text=text, model_name=self.name)


class FailingAfterFirstModelProvider(ModelProvider):
    """Model provider that succeeds for planning and fails for synthesis."""

    name = "failing-after-first-model"

    def __init__(self, first_response: str) -> None:
        self._first_response = first_response
        self._calls = 0

    def generate(self, request: ModelRequest) -> ModelResponse:
        self._calls += 1
        if self._calls == 1:
            return ModelResponse(text=self._first_response, model_name=self.name)
        raise ModelProviderError("synthesis failed", component=self.name)


def _orchestrator_with_provider(
    provider: ModelProvider,
    memory_store: MemoryStore,
):
    from jarvis.orchestrator import Orchestrator
    from jarvis.policies import PolicyEngine

    return Orchestrator(
        agents=default_agent_registry(),
        tools=default_tool_registry(memory_store),
        models=ModelRouter({provider.name: provider}, provider.name),
        policies=PolicyEngine(),
        planner_prompt=PromptLibrary().planner_prompt(),
        synthesis_prompt=PromptLibrary().synthesis_prompt(),
    )


if __name__ == "__main__":
    unittest.main()
