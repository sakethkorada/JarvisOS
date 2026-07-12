import unittest
import importlib.util
import os
from unittest.mock import patch
from contextlib import contextmanager, redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
import sys
import time
from io import StringIO
from socket import socket
from threading import Thread
import json
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import urlopen

from jarvis.contracts import (
    AvailableTool,
    ExecutionPlan,
    ToolCall,
    ToolCapability,
    ToolExecutionContext,
    ToolResult,
    ToolSpec,
)
from jarvis.contracts import ModelRequest, ModelResponse
from jarvis.evals import EvalCase, EvalSuite, load_eval_suite, run_eval_suite
from jarvis.errors import ModelProviderError
from jarvis.models import (
    GeminiProvider,
    ModelProvider,
    ModelRouter,
    default_model_router,
)
from jarvis.orchestration.arguments import ArgumentBuilder
from jarvis.orchestration.arguments import ToolUseAgent, ToolUseFeedback
from jarvis.orchestration.arguments import resolve_tool_arguments
from jarvis.orchestration.orchestrator import Orchestrator
from jarvis.orchestration.planner import Planner
from jarvis.orchestration.synthesizer import _is_supported_synthesis, deterministic_summary
from jarvis.agents import default_agent_registry
from jarvis.policies import PolicyEngine
from jarvis.prompts import PromptLibrary
from jarvis.runtime import create_default_orchestrator
from jarvis.runtime import create_default_tool_registry
from jarvis.settings import McpServerSettings, load_settings
from jarvis.storage.approvals import ApprovalStore, apply_approved_record
from jarvis.storage.memory import MemoryExtractor, MemoryStore
from jarvis.integrations.mcp import (
    McpHttpClient,
    McpStdioClient,
    _mcp_tool_capability,
    _normalize_mcp_result,
    _resolved_command,
)
from jarvis.integrations.oauth import OAuthManager
from jarvis.integrations.oauth import _post_form
from jarvis.settings import OAuthProviderSettings
from jarvis.storage.auth import AuthStore
from jarvis.storage.tasks import TaskStore
from jarvis.tools import ToolRegistry, default_tool_registry
from jarvis.tools.results import normalize_tool_output, public_tool_output
from jarvis.storage.traces import TraceStore
from jarvis.cli import _auth_debug, _parse_args_json, _print_tool_result
from jarvis.cli import main as cli_main


class RuntimeTests(unittest.TestCase):
    """Tests for the default local runtime loop."""

    def test_simple_goal_runs(self) -> None:
        with TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "jarvis.toml"
            config_path.write_text("", encoding="utf-8")
            result = create_default_orchestrator(load_settings(config_path)).run(
                "break this task into steps",
                model_name="fake-local",
            )

        self.assertEqual(result.status, "completed")
        self.assertIn("memory.search", [item.tool_name for item in result.step_results])
        self.assertEqual(result.final_response, "Done.")
        trace_types = [event.event_type for event in result.trace]
        self.assertIn("synthesis.completed", trace_types)

    def test_registry_normalizes_generic_tool_records(self) -> None:
        registry = ToolRegistry()
        registry.register(
            ToolSpec(name="demo.records", description="Return generic records."),
            lambda arguments: {"items": [{"id": "item-1", "title": "One"}]},
        )

        result = registry.execute(ToolCall("demo.records", {}))

        self.assertTrue(result.success)
        self.assertEqual(result.output["records"], [{"id": "item-1", "title": "One"}])
        self.assertEqual(result.output["ids"], ["item-1"])
        self.assertEqual(result.output["text"], "")
        self.assertEqual(result.output["metadata"]["source"], "builtin")

    def test_public_tool_output_hides_raw_provider_payload(self) -> None:
        output = normalize_tool_output(
            {
                "text": "Found one item.",
                "records": [{"id": "item-1", "title": "One"}],
                "mcp_result": {"private": "debug payload"},
            },
            source="mcp",
        )

        self.assertEqual(
            public_tool_output(output),
            {
                "text": "Found one item.",
                "records": [{"id": "item-1", "title": "One"}],
                "ids": ["item-1"],
                "metadata": {"source": "mcp"},
            },
        )

    def test_deterministic_summary_renders_records_without_raw_payload(self) -> None:
        result = ToolResult(
            tool_name="demo.records",
            output=normalize_tool_output(
                {
                    "records": [
                        {"id": "item-1", "title": "One", "sender": "Taylor"},
                        {"id": "item-2", "title": "Two"},
                    ],
                    "mcp_result": {"large": "raw payload"},
                }
            ),
        )

        summary = deterministic_summary("Review records", (result,), "completed")

        self.assertIn("demo.records: 2 result(s)", summary)
        self.assertIn("One - Taylor", summary)
        self.assertNotIn("raw payload", summary)

    def test_meeting_goal_degrades_without_calendar_capability(self) -> None:
        with TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "jarvis.toml"
            config_path.write_text("", encoding="utf-8")
            result = create_default_orchestrator(load_settings(config_path)).run(
                "prepare me for my meeting tomorrow",
                model_name="fake-local",
            )

        tool_names = [item.tool_name for item in result.step_results]
        self.assertIn("memory.search", tool_names)
        self.assertNotIn("calendar.search_events", tool_names)
        self.assertEqual(result.status, "completed")

    def test_memory_can_be_disabled_for_runtime_runs(self) -> None:
        with TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "jarvis.toml"
            config_path.write_text(
                """
[memory]
enabled = false
auto_extract = true
""".strip(),
                encoding="utf-8",
            )
            settings = load_settings(config_path)
            tools = create_default_tool_registry(settings)
            result = create_default_orchestrator(settings).run(
                "Break this task into steps.",
                model_name="fake-local",
            )

        self.assertFalse(settings.memory.enabled)
        self.assertFalse(tools.has("memory.search"))
        self.assertNotIn(
            "memory.search",
            [item.tool_name for item in result.step_results],
        )
        self.assertNotIn(
            "memory.suggested",
            [event.event_type for event in result.trace],
        )

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
            provider = StaticModelProvider(
                """
{
  "steps": [
    {
      "tool_name": "notes.search",
      "arguments": {"query": "Jordan"},
      "description": "Search notes."
    }
  ]
}
""".strip()
            )
            result = Orchestrator(
                agents=default_agent_registry(),
                tools=create_default_tool_registry(settings),
                models=ModelRouter({provider.name: provider}, provider.name),
                policies=PolicyEngine(),
                planner_prompt=PromptLibrary().planner_prompt(),
                synthesis_prompt=PromptLibrary().synthesis_prompt(),
                tool_use_prompt=PromptLibrary().tool_use_prompt(),
            ).run("find notes about Jordan", model_name=provider.name)

        tool_names = [item.tool_name for item in result.step_results]
        self.assertIn("notes.search", tool_names)
        self.assertIn("Jordan meeting", result.final_response)

    def test_meeting_prep_uses_notes_without_fake_calendar(self) -> None:
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
            provider = StaticModelProvider(
                """
{
  "steps": [
    {
      "tool_name": "notes.search",
      "arguments": {"query": "Jordan meeting"},
      "description": "Search notes for meeting context."
    }
  ]
}
""".strip()
            )
            result = Orchestrator(
                agents=default_agent_registry(),
                tools=create_default_tool_registry(settings),
                models=ModelRouter({provider.name: provider}, provider.name),
                policies=PolicyEngine(),
                planner_prompt=PromptLibrary().planner_prompt(),
                synthesis_prompt=PromptLibrary().synthesis_prompt(),
                tool_use_prompt=PromptLibrary().tool_use_prompt(),
            ).run(
                "Prepare me for my meeting with Jordan tomorrow",
                model_name=provider.name,
            )

        tool_names = [item.tool_name for item in result.step_results]
        self.assertNotIn("calendar.search_events", tool_names)
        self.assertIn("notes.search", tool_names)
        self.assertIn("Jordan meeting", result.final_response)

    def test_orchestrator_allows_music_capability_agent(self) -> None:
        with TemporaryDirectory() as temp_dir:
            tools = default_tool_registry(MemoryStore(Path(temp_dir) / "memory.sqlite3"))
            tools.register(
                ToolSpec(
                    name="spotify.recently_played",
                    description="List recently played Spotify tracks.",
                    source="mcp:spotify",
                    input_schema={
                        "type": "object",
                        "properties": {"limit": {"type": "integer"}},
                    },
                    capability=ToolCapability(
                        domain="music",
                        operation="recently_played",
                        provider="spotify",
                    ),
                ),
                lambda arguments: {"text": "Spotify recently played: test track"},
            )
            provider = StaticModelProvider(
                """
{
  "steps": [
    {
      "tool_name": "spotify.recently_played",
      "arguments": {"limit": 10},
      "description": "Read recently played tracks."
    }
  ]
}
""".strip()
            )
            orchestrator = Orchestrator(
                agents=default_agent_registry(),
                tools=tools,
                models=ModelRouter({provider.name: provider}, provider.name),
                policies=PolicyEngine(),
                planner_prompt=PromptLibrary().planner_prompt(),
                synthesis_prompt=PromptLibrary().synthesis_prompt(),
                tool_use_prompt=PromptLibrary().tool_use_prompt(),
            )

            result = orchestrator.run(
                "Show me my recently played Spotify tracks",
                model_name=provider.name,
            )

        self.assertEqual(result.status, "completed")
        self.assertIn("Spotify recently played: test track", result.final_response)

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

            provider = StaticModelProvider(
                """
{
  "steps": [
    {
      "tool_name": "task.create",
      "arguments": {"title": "Ask Jordan about API migration"},
      "description": "Create a local task."
    }
  ]
}
""".strip()
            )
            result = Orchestrator(
                agents=default_agent_registry(),
                tools=create_default_tool_registry(settings),
                models=ModelRouter({provider.name: provider}, provider.name),
                policies=PolicyEngine(),
                planner_prompt=PromptLibrary().planner_prompt(),
                synthesis_prompt=PromptLibrary().synthesis_prompt(),
                tool_use_prompt=PromptLibrary().tool_use_prompt(),
            ).run(
                "Create a task to ask Jordan about API migration",
                model_name=provider.name,
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

            provider = StaticModelProvider(
                """
{
  "steps": [
    {
      "tool_name": "task.create",
      "arguments": {"title": "Ask Jordan about API migration"},
      "description": "Create a local task."
    }
  ]
}
""".strip()
            )
            Orchestrator(
                agents=default_agent_registry(),
                tools=create_default_tool_registry(settings),
                models=ModelRouter({provider.name: provider}, provider.name),
                policies=PolicyEngine(),
                planner_prompt=PromptLibrary().planner_prompt(),
                synthesis_prompt=PromptLibrary().synthesis_prompt(),
                tool_use_prompt=PromptLibrary().tool_use_prompt(),
            ).run(
                "Prepare me for my meeting with Jordan tomorrow and "
                "create a task to ask Jordan about API migration",
                model_name=provider.name,
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

    def test_role_model_resolution_precedence(self) -> None:
        with TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "jarvis.toml"
            config_path.write_text(
                """
[models]
default = "fake-local"

[models.modes]
balanced = "mode-model"

[models.roles]
planner = "planner-model"
tool_use = "tool-use-model"
""".strip(),
                encoding="utf-8",
            )

            settings = load_settings(config_path)

        self.assertEqual(
            settings.resolve_model("manual-model", "balanced", role="planner"),
            "manual-model",
        )
        self.assertEqual(
            settings.resolve_model(None, "balanced", role="planner"),
            "planner-model",
        )
        self.assertEqual(
            settings.resolve_model(None, "balanced", role="missing"),
            "mode-model",
        )

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
        self.assertEqual(settings.mcp.servers[0].transport, "stdio")

    def test_loads_http_mcp_server_and_oauth_settings_from_toml(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "jarvis.toml"
            config_path.write_text(
                """
[auth]
database_path = "state/auth.sqlite3"

[[auth.oauth_providers]]
name = "google"
client_id = "client-id"
client_secret_env = "GOOGLE_CLIENT_SECRET"
authorization_url = "https://accounts.google.com/o/oauth2/v2/auth"
token_url = "https://oauth2.googleapis.com/token"
tokeninfo_url = "https://oauth2.googleapis.com/tokeninfo"
redirect_uri = "http://localhost:8765/oauth/callback"
scopes = ["https://www.googleapis.com/auth/calendar.events.readonly"]

[[mcp.servers]]
name = "google_calendar"
transport = "http"
url = "https://mcp.example.com/mcp"
auth_provider = "google"
bearer_token_env = "GOOGLE_MCP_ACCESS_TOKEN"

[mcp.servers.headers]
X-Test = "1"
""".strip(),
                encoding="utf-8",
            )

            settings = load_settings(config_path)

        self.assertEqual(
            settings.auth.database_path,
            Path(temp_dir) / "state" / "auth.sqlite3",
        )
        self.assertEqual(settings.auth.oauth_providers[0].name, "google")
        self.assertEqual(
            settings.auth.oauth_providers[0].scopes,
            ("https://www.googleapis.com/auth/calendar.events.readonly",),
        )
        self.assertEqual(
            settings.auth.oauth_providers[0].tokeninfo_url,
            "https://oauth2.googleapis.com/tokeninfo",
        )
        server = settings.mcp.servers[0]
        self.assertEqual(server.transport, "http")
        self.assertEqual(server.url, "https://mcp.example.com/mcp")
        self.assertEqual(server.auth_provider, "google")
        self.assertEqual(server.bearer_token_env, "GOOGLE_MCP_ACCESS_TOKEN")
        self.assertEqual(server.headers["X-Test"], "1")

    def test_non_auth_config_inherits_global_auth_profile(self) -> None:
        previous_profile = os.environ.get("JARVIS_AUTH_PROFILE")
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            auth_profile = root / "auth.toml"
            auth_profile.write_text(
                """
[auth]
database_path = "state/auth.sqlite3"

[[auth.oauth_providers]]
name = "google"
client_id = "client-id"
client_secret_env = "GOOGLE_CLIENT_SECRET"
authorization_url = "https://accounts.google.com/o/oauth2/v2/auth"
token_url = "https://oauth2.googleapis.com/token"
redirect_uri = "http://localhost:8765/oauth/callback"
scopes = ["https://www.googleapis.com/auth/calendar.events.readonly"]
""".strip(),
                encoding="utf-8",
            )
            tool_config = root / "calendar-tools.toml"
            tool_config.write_text(
                """
[[mcp.servers]]
name = "google_calendar"
command = "python"
args = ["calendar_server.py"]
""".strip(),
                encoding="utf-8",
            )
            os.environ["JARVIS_AUTH_PROFILE"] = str(auth_profile)

            settings = load_settings(tool_config)

        if previous_profile is None:
            os.environ.pop("JARVIS_AUTH_PROFILE", None)
        else:
            os.environ["JARVIS_AUTH_PROFILE"] = previous_profile
        self.assertEqual(
            settings.auth.database_path,
            Path(temp_dir) / "state" / "auth.sqlite3",
        )
        self.assertEqual(settings.auth.oauth_providers[0].name, "google")
        self.assertEqual(settings.auth.loaded_from, auth_profile)

    def test_local_auth_overrides_global_auth_profile(self) -> None:
        previous_profile = os.environ.get("JARVIS_AUTH_PROFILE")
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            auth_profile = root / "auth.toml"
            auth_profile.write_text(
                """
[auth]
database_path = "global/auth.sqlite3"

[[auth.oauth_providers]]
name = "global"
""".strip(),
                encoding="utf-8",
            )
            config_path = root / "jarvis.toml"
            config_path.write_text(
                """
[auth]
database_path = "local/auth.sqlite3"

[[auth.oauth_providers]]
name = "local"
""".strip(),
                encoding="utf-8",
            )
            os.environ["JARVIS_AUTH_PROFILE"] = str(auth_profile)

            settings = load_settings(config_path)

        if previous_profile is None:
            os.environ.pop("JARVIS_AUTH_PROFILE", None)
        else:
            os.environ["JARVIS_AUTH_PROFILE"] = previous_profile
        self.assertEqual(
            settings.auth.database_path,
            Path(temp_dir) / "local" / "auth.sqlite3",
        )
        self.assertEqual(settings.auth.oauth_providers[0].name, "local")

    def test_loads_mcp_tool_policy_overrides_from_toml(self) -> None:
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

[[mcp.servers.tools]]
name = "echo"
argument_hints = "Pass the user's exact text in the text argument."
risk_level = "medium"
requires_approval = true
""".strip(),
                encoding="utf-8",
            )

            settings = load_settings(config_path)

        server = settings.mcp.servers[0]
        self.assertEqual(len(server.tools), 1)
        self.assertEqual(server.tools[0].name, "echo")
        self.assertEqual(
            server.tools[0].argument_hints,
            "Pass the user's exact text in the text argument.",
        )
        self.assertEqual(server.tools[0].risk_level, "medium")
        self.assertTrue(server.tools[0].requires_approval)

    def test_enables_google_workspace_capability_pack(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "jarvis.toml"
            config_path.write_text(
                """
[capabilities]
google_workspace = true
""".strip(),
                encoding="utf-8",
            )

            settings = load_settings(config_path)

        self.assertTrue(settings.capabilities.is_enabled("google_workspace"))
        servers = {server.name: server for server in settings.mcp.servers}
        self.assertIn("google_calendar", servers)
        self.assertIn("gmail", servers)
        self.assertEqual(
            servers["google_calendar"].args,
            ("examples/mcp/google_calendar_fastmcp_server.py",),
        )
        self.assertEqual(
            servers["gmail"].args,
            ("examples/mcp/google_gmail_fastmcp_server.py",),
        )
        self.assertEqual(
            {tool.name for tool in servers["gmail"].tools},
            {"list_recent", "search_messages", "get_message", "get_thread"},
        )
        self.assertIn(
            "Gmail search syntax",
            next(
                tool.argument_hints
                for tool in servers["gmail"].tools
                if tool.name == "search_messages"
            ),
        )

    def test_enables_spotify_capability_pack(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "jarvis.toml"
            config_path.write_text(
                """
[capabilities]
spotify = true
""".strip(),
                encoding="utf-8",
            )

            settings = load_settings(config_path)

        self.assertTrue(settings.capabilities.is_enabled("spotify"))
        servers = {server.name: server for server in settings.mcp.servers}
        self.assertIn("spotify", servers)
        self.assertEqual(
            servers["spotify"].args,
            ("examples/mcp/spotify_fastmcp_server.py",),
        )
        self.assertEqual(
            {tool.name for tool in servers["spotify"].tools},
            {"search", "current_playback", "recently_played", "list_playlists"},
        )
        self.assertIn(
            "Spotify catalog query",
            next(
                tool.argument_hints
                for tool in servers["spotify"].tools
                if tool.name == "search"
            ),
        )

    def test_disabled_capability_pack_does_not_add_mcp_servers(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "jarvis.toml"
            config_path.write_text(
                """
[capabilities]
google_workspace = false
""".strip(),
                encoding="utf-8",
            )

            settings = load_settings(config_path)

        self.assertFalse(settings.capabilities.is_enabled("google_workspace"))
        self.assertEqual(settings.mcp.servers, ())

    def test_configured_mcp_server_overrides_capability_pack_server(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "jarvis.toml"
            config_path.write_text(
                """
[capabilities]
google_workspace = true

[[mcp.servers]]
name = "gmail"
command = "python"
args = ["custom_gmail.py"]
""".strip(),
                encoding="utf-8",
            )

            settings = load_settings(config_path)

        servers = {server.name: server for server in settings.mcp.servers}
        gmail_servers = [
            server for server in settings.mcp.servers if server.name == "gmail"
        ]
        self.assertEqual(len(gmail_servers), 1)
        self.assertIn("google_calendar", servers)
        self.assertEqual(servers["gmail"].args, ("custom_gmail.py",))

    def test_unknown_capability_pack_fails_cleanly(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "jarvis.toml"
            config_path.write_text(
                """
[capabilities]
not_real = true
""".strip(),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(
                ValueError,
                "Unknown built-in capability pack",
            ):
                load_settings(config_path)

    def test_loads_prompt_override_paths_from_toml(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "jarvis.toml"
            config_path.write_text(
                """
[prompts]
planner = "prompts/planner.md"
synthesis = "prompts/synthesis.md"
tool_use = "prompts/tool_use.md"
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
        self.assertEqual(
            settings.prompts.tool_use_path,
            Path(temp_dir) / "prompts" / "tool_use.md",
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
            tool_use_path = root / "tool_use.md"
            planner_path.write_text("custom planner prompt", encoding="utf-8")
            synthesis_path.write_text("custom synthesis prompt", encoding="utf-8")
            tool_use_path.write_text("custom tool use prompt", encoding="utf-8")

            prompts = PromptLibrary(planner_path, synthesis_path, tool_use_path)

            self.assertEqual(prompts.planner_prompt(), "custom planner prompt")
            self.assertEqual(prompts.synthesis_prompt(), "custom synthesis prompt")
            self.assertEqual(prompts.tool_use_prompt(), "custom tool use prompt")


class TraceTests(unittest.TestCase):
    """Tests for SQLite trace persistence."""

    def test_save_list_and_show_run_trace(self) -> None:
        with TemporaryDirectory() as temp_dir:
            trace_store = TraceStore(Path(temp_dir) / "traces.sqlite3")
            config_path = Path(temp_dir) / "jarvis.toml"
            config_path.write_text("", encoding="utf-8")
            result = create_default_orchestrator(load_settings(config_path)).run(
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

    def test_planner_uses_role_model_route(self) -> None:
        provider = StaticModelProvider(
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
            tools = default_tool_registry(
                MemoryStore(Path(temp_dir) / "memory.sqlite3")
            )
            planner = Planner(
                default_agent_registry(),
                tools,
                ModelRouter(
                    {provider.name: provider, "fake-local": StaticModelProvider("")},
                    default_provider_name="fake-local",
                    role_routes={"planner": provider.name},
                ),
                PromptLibrary().planner_prompt(),
            )

            plan, source, _ = planner.create_plan(
                "find memory about Jordan",
                model_name=None,
                model_mode="balanced",
            )

        self.assertEqual(source, "llm")
        self.assertEqual(plan.steps[0].tool_call.tool_name, "memory.search")

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

    def test_llm_plan_accepts_general_generate_text(self) -> None:
        provider = StaticModelProvider(
            """
{
  "steps": [
    {
      "tool_name": "general.generate_text",
      "arguments": {"instruction": "Draft a short update."},
      "description": "Draft update text."
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
                "draft an update",
                model_name=provider.name,
                model_mode="balanced",
            )

        self.assertEqual(source, "llm")
        self.assertEqual(plan.steps[0].agent_name, "general")
        self.assertEqual(plan.steps[0].tool_call.tool_name, "general.generate_text")

    def test_fallback_does_not_provider_route_calendar_capability(self) -> None:
        with TemporaryDirectory() as temp_dir:
            tools = default_tool_registry(MemoryStore(Path(temp_dir) / "memory.sqlite3"))
            tools.register(
                ToolSpec(
                    name="external_calendar.list_calendars",
                    description="List external calendars.",
                    source="mcp:external_calendar",
                    capability=ToolCapability(
                        domain="calendar",
                        operation="list_calendars",
                        provider="external_calendar",
                    ),
                ),
                lambda arguments: {"text": "external calendars"},
            )
            planner = Planner(
                default_agent_registry(),
                tools,
                ModelRouter({}),
                PromptLibrary().planner_prompt(),
            )

            plan = planner.create_fallback_plan("Use Calendar to list my calendars")

        tool_names = [step.tool_call.tool_name for step in plan.steps]
        self.assertIn("memory.search", tool_names)
        self.assertIn("task.create_summary", tool_names)
        self.assertNotIn("external_calendar.list_calendars", tool_names)
        self.assertNotIn("calendar.search_events", tool_names)

    def test_llm_plan_selects_calendar_tool_from_catalog(self) -> None:
        provider = StaticModelProvider(
            """
{
  "steps": [
    {
      "tool_name": "external_calendar.list_events",
      "arguments": {"calendar_id": "primary"},
      "description": "List calendar events."
    }
  ]
}
""".strip()
        )
        with TemporaryDirectory() as temp_dir:
            tools = default_tool_registry(MemoryStore(Path(temp_dir) / "memory.sqlite3"))
            tools.register(
                ToolSpec(
                    name="external_calendar.list_events",
                    description="List external calendar events.",
                    source="mcp:external_calendar",
                    input_schema={
                        "type": "object",
                        "properties": {
                            "calendar_id": {"type": "string"},
                            "start_time": {"type": "string"},
                            "end_time": {"type": "string"},
                            "max_results": {"type": "integer"},
                        },
                    },
                    capability=ToolCapability(
                        domain="calendar",
                        operation="list_events",
                        provider="external_calendar",
                    ),
                ),
                lambda arguments: {"text": "external events"},
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

            plan, source, _ = planner.create_plan(
                "Use Calendar to summarize my coming events in next 1 week",
                model_name=provider.name,
                model_mode="balanced",
            )

        calendar_step = next(
            step
            for step in plan.steps
            if step.tool_call.tool_name == "external_calendar.list_events"
        )
        self.assertEqual(source, "llm")
        self.assertEqual(calendar_step.agent_name, "calendar")
        self.assertEqual(calendar_step.tool_call.arguments, {"calendar_id": "primary"})

    def test_llm_plan_selects_email_tool_from_catalog(self) -> None:
        provider = StaticModelProvider(
            """
{
  "steps": [
    {
      "tool_name": "gmail.search_messages",
      "arguments": {"query": "from:Jordan newer_than:30d"},
      "description": "Search Gmail messages."
    }
  ]
}
""".strip()
        )
        with TemporaryDirectory() as temp_dir:
            tools = default_tool_registry(MemoryStore(Path(temp_dir) / "memory.sqlite3"))
            tools.register(
                ToolSpec(
                    name="gmail.search_messages",
                    description="Search Gmail messages.",
                    source="mcp:gmail",
                    input_schema={
                        "type": "object",
                        "properties": {"query": {"type": "string"}},
                        "required": ["query"],
                    },
                    capability=ToolCapability(
                        domain="email",
                        operation="search_messages",
                        provider="gmail",
                    ),
                ),
                lambda arguments: {"text": "email results"},
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

            plan, source, _ = planner.create_plan(
                "Find emails from Jordan",
                model_name=provider.name,
                model_mode="balanced",
            )

        email_step = next(
            step
            for step in plan.steps
            if step.tool_call.tool_name == "gmail.search_messages"
        )
        self.assertEqual(source, "llm")
        self.assertEqual(email_step.agent_name, "email")
        self.assertEqual(
            email_step.tool_call.arguments["query"],
            "from:Jordan newer_than:30d",
        )

    def test_llm_plan_selects_music_tool_from_catalog(self) -> None:
        provider = StaticModelProvider(
            """
{
  "steps": [
    {
      "tool_name": "spotify.recently_played",
      "arguments": {"limit": 10},
      "description": "Read recently played tracks."
    }
  ]
}
""".strip()
        )
        with TemporaryDirectory() as temp_dir:
            tools = default_tool_registry(MemoryStore(Path(temp_dir) / "memory.sqlite3"))
            tools.register(
                ToolSpec(
                    name="spotify.recently_played",
                    description="List recently played Spotify tracks.",
                    source="mcp:spotify",
                    input_schema={
                        "type": "object",
                        "properties": {"limit": {"type": "integer"}},
                    },
                    capability=ToolCapability(
                        domain="music",
                        operation="recently_played",
                        provider="spotify",
                    ),
                ),
                lambda arguments: {"text": "recent tracks"},
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

            plan, source, _ = planner.create_plan(
                "Show me my recently played Spotify tracks",
                model_name=provider.name,
                model_mode="balanced",
            )

        music_step = next(
            step
            for step in plan.steps
            if step.tool_call.tool_name == "spotify.recently_played"
        )
        self.assertEqual(source, "llm")
        self.assertEqual(music_step.agent_name, "music")
        self.assertEqual(music_step.tool_call.arguments, {"limit": 10})

    def test_fallback_does_not_provider_route_music_capability(self) -> None:
        with TemporaryDirectory() as temp_dir:
            tools = default_tool_registry(MemoryStore(Path(temp_dir) / "memory.sqlite3"))
            tools.register(
                ToolSpec(
                    name="spotify.list_playlists",
                    description="List Spotify playlists.",
                    source="mcp:spotify",
                    input_schema={
                        "type": "object",
                        "properties": {"limit": {"type": "integer"}},
                    },
                    capability=ToolCapability(
                        domain="music",
                        operation="list_playlists",
                        provider="spotify",
                    ),
                ),
                lambda arguments: {"text": "playlists"},
            )
            planner = Planner(
                default_agent_registry(),
                tools,
                ModelRouter({}),
                PromptLibrary().planner_prompt(),
            )

            plan = planner.create_fallback_plan("List some of my Spotify playlists")

        tool_names = [step.tool_call.tool_name for step in plan.steps]
        self.assertIn("memory.search", tool_names)
        self.assertNotIn("spotify.list_playlists", tool_names)

    def test_llm_plan_strips_unknown_mcp_arguments_from_schema(self) -> None:
        provider = StaticModelProvider(
            """
{
  "steps": [
    {
      "tool_name": "external_calendar.list_calendars",
      "arguments": {"query": "my calendars"},
      "description": "List calendars."
    }
  ]
}
""".strip()
        )
        with TemporaryDirectory() as temp_dir:
            tools = default_tool_registry(MemoryStore(Path(temp_dir) / "memory.sqlite3"))
            tools.register(
                ToolSpec(
                    name="external_calendar.list_calendars",
                    description="List external calendars.",
                    source="mcp:external_calendar",
                    input_schema={
                        "type": "object",
                        "properties": {},
                    },
                ),
                lambda arguments: {"text": "external calendars"},
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

            plan, source, _ = planner.create_plan(
                "Use Google Calendar to list my calendars",
                model_name=provider.name,
                model_mode="balanced",
            )

        self.assertEqual(source, "llm")
        self.assertEqual(
            plan.steps[0].tool_call.arguments,
            {},
        )

    def test_llm_plan_repairs_missing_required_schema_argument(self) -> None:
        provider = SequencedModelProvider(
            [
                """
{
  "steps": [
    {
      "tool_name": "external_calendar.get_event",
      "arguments": {},
      "description": "Get an event."
    }
  ]
}
""".strip(),
                """
{
  "steps": [
    {
      "tool_name": "external_calendar.get_event",
      "arguments": {"eventId": "event-1"},
      "description": "Get an event."
    }
  ]
}
""".strip(),
            ]
        )
        with TemporaryDirectory() as temp_dir:
            tools = default_tool_registry(MemoryStore(Path(temp_dir) / "memory.sqlite3"))
            tools.register(
                ToolSpec(
                    name="external_calendar.get_event",
                    description="Get external calendar event.",
                    source="mcp:external_calendar",
                    input_schema={
                        "type": "object",
                        "properties": {"eventId": {"type": "string"}},
                        "required": ["eventId"],
                    },
                ),
                lambda arguments: {"text": "event"},
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

            plan, source, _ = planner.create_plan(
                "Use Google Calendar to get an event",
                model_name=provider.name,
                model_mode="balanced",
            )

        self.assertEqual(source, "llm_repaired")
        self.assertEqual(plan.steps[0].tool_call.tool_name, "external_calendar.get_event")
        self.assertEqual(plan.steps[0].tool_call.arguments, {"eventId": "event-1"})

    def test_llm_plan_repairs_unsupported_reference_syntax(self) -> None:
        provider = SequencedModelProvider(
            [
                """
{
  "steps": [
    {
      "tool_name": "gmail.get_message",
      "arguments": {"message_id": "$result.text"},
      "description": "Get a message."
    }
  ]
}
""".strip(),
                """
{
  "steps": [
    {
      "tool_name": "gmail.search_messages",
      "arguments": {"query": "meeting newer_than:7d"},
      "description": "Search messages."
    }
  ]
}
""".strip(),
            ]
        )
        with TemporaryDirectory() as temp_dir:
            tools = default_tool_registry(MemoryStore(Path(temp_dir) / "memory.sqlite3"))
            tools.register(
                ToolSpec(
                    name="gmail.get_message",
                    description="Get one Gmail message by API message id.",
                    source="mcp:gmail",
                    input_schema={
                        "type": "object",
                        "properties": {"message_id": {"type": "string"}},
                        "required": ["message_id"],
                    },
                    capability=ToolCapability(
                        domain="email",
                        operation="get_message",
                        provider="gmail",
                    ),
                ),
                lambda arguments: {"text": "message"},
            )
            tools.register(
                ToolSpec(
                    name="gmail.search_messages",
                    description="Search Gmail messages.",
                    source="mcp:gmail",
                    input_schema={
                        "type": "object",
                        "properties": {"query": {"type": "string"}},
                        "required": ["query"],
                    },
                    capability=ToolCapability(
                        domain="email",
                        operation="search_messages",
                        provider="gmail",
                    ),
                ),
                lambda arguments: {"text": "matches"},
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

            plan, source, _ = planner.create_plan(
                "Use Gmail to prep me for meetings this week",
                model_name=provider.name,
                model_mode="balanced",
            )

        self.assertEqual(source, "llm_repaired")
        self.assertEqual(plan.steps[0].tool_call.tool_name, "gmail.search_messages")
        self.assertEqual(
            plan.steps[0].tool_call.arguments,
            {"query": "meeting newer_than:7d"},
        )

    def test_llm_plan_falls_back_after_failed_repair(self) -> None:
        provider = SequencedModelProvider(
            [
                """
{
  "steps": [
    {
      "tool_name": "external_calendar.get_event",
      "arguments": {},
      "description": "Get an event."
    }
  ]
}
""".strip(),
                """
{
  "steps": [
    {
      "tool_name": "external_calendar.get_event",
      "arguments": {},
      "description": "Get an event."
    }
  ]
}
""".strip(),
            ]
        )
        with TemporaryDirectory() as temp_dir:
            tools = default_tool_registry(MemoryStore(Path(temp_dir) / "memory.sqlite3"))
            tools.register(
                ToolSpec(
                    name="external_calendar.get_event",
                    description="Get external calendar event.",
                    source="mcp:external_calendar",
                    input_schema={
                        "type": "object",
                        "properties": {"eventId": {"type": "string"}},
                        "required": ["eventId"],
                    },
                ),
                lambda arguments: {"text": "event"},
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

            plan, source, _ = planner.create_plan(
                "Use Google Calendar to get an event",
                model_name=provider.name,
                model_mode="balanced",
            )

        tool_names = [step.tool_call.tool_name for step in plan.steps]
        self.assertEqual(source, "fallback")
        self.assertIn("memory.search", tool_names)
        self.assertNotIn("external_calendar.get_event", tool_names)

    def test_bundled_prompt_uses_generic_tool_selection_rules(self) -> None:
        prompt = PromptLibrary().planner_prompt()

        self.assertIn("Choose the tools that best satisfy the user goal", prompt)
        self.assertIn("risk level", prompt)
        self.assertIn("Follow each tool's input_schema exactly", prompt)
        self.assertNotIn("For calendar requests", prompt)
        self.assertNotIn("For email or Gmail requests", prompt)
        self.assertNotIn("For music or Spotify requests", prompt)


class ArgumentResolverTests(unittest.TestCase):
    """Tests for unified tool argument resolution."""

    def test_tool_use_agent_uses_role_model_route(self) -> None:
        tool = ToolSpec(
            name="demo.lookup",
            description="Look up demo data.",
            input_schema={
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        )
        provider = StaticModelProvider('{"query": "Jordan"}')
        agent = ToolUseAgent(
            ModelRouter(
                {provider.name: provider, "fake-local": StaticModelProvider("")},
                default_provider_name="fake-local",
                role_routes={"tool_use": provider.name},
            ),
            system_prompt="tool use prompt",
        )

        resolution = agent.build(
            "Look up Jordan.",
            tool,
            {},
            model_name=None,
            model_mode="balanced",
        )

        self.assertIsNone(resolution.error)
        self.assertEqual(resolution.arguments["query"], "Jordan")

    def test_resolver_does_not_hand_build_calendar_bounds(self) -> None:
        tool = ToolSpec(
            name="google_calendar.list_events",
            description="List events.",
            input_schema={
                "type": "object",
                "properties": {
                    "calendarId": {"type": "string"},
                    "timeMin": {"type": "string"},
                    "timeMax": {"type": "string"},
                    "maxResults": {"type": "integer"},
                },
            },
            capability=ToolCapability(
                domain="calendar",
                operation="list_events",
                provider="google",
            ),
        )

        resolution = resolve_tool_arguments(
            "Use Calendar to summarize my coming events in next 1 week",
            tool,
            {"query": "ignore me"},
        )

        self.assertIsNone(resolution.error)
        self.assertEqual(resolution.arguments, {})

    def test_resolver_fills_builtin_defaults(self) -> None:
        tool = ToolSpec(
            name="general.generate_text",
            description="Generate text.",
        )

        resolution = resolve_tool_arguments("draft a note", tool, {})

        self.assertEqual(resolution.arguments["instruction"], "draft a note")
        self.assertIsNone(resolution.error)

    def test_resolver_resolves_last_text(self) -> None:
        tool = ToolSpec(name="demo.echo", description="Echo.")
        prior = (
            ToolResult(
                tool_name="general.generate_text",
                output={"text": "Generated body."},
            ),
        )

        resolution = resolve_tool_arguments(
            "echo generated text",
            tool,
            {"text": "$last.text"},
            prior_results=prior,
        )

        self.assertEqual(resolution.arguments["text"], "Generated body.")

    def test_resolver_fails_cleanly_for_missing_last_text(self) -> None:
        tool = ToolSpec(name="demo.echo", description="Echo.")

        resolution = resolve_tool_arguments(
            "echo generated text",
            tool,
            {"text": "$last.text"},
        )

        self.assertIn("no prior successful tool result", resolution.error or "")

    def test_resolver_resolves_structured_record_id_path(self) -> None:
        tool = ToolSpec(name="demo.detail", description="Read one record.")
        prior = (
            ToolResult(
                tool_name="demo.search",
                output={"records": [{"id": "record-7", "title": "Release notes"}]},
            ),
        )

        resolution = resolve_tool_arguments(
            "Read the first result.",
            tool,
            {"record_id": "$last.records[0].id"},
            prior_results=prior,
        )

        self.assertIsNone(resolution.error)
        self.assertEqual(resolution.arguments, {"record_id": "record-7"})

    def test_resolver_resolves_named_structured_record_id_path(self) -> None:
        tool = ToolSpec(name="demo.detail", description="Read one record.")
        result = ToolResult(
            tool_name="demo.search",
            output={"records": [{"id": "record-7"}]},
        )

        resolution = resolve_tool_arguments(
            "Read the selected result.",
            tool,
            {"record_id": "$step.search.records[0].id"},
            named_results={"search": result},
        )

        self.assertIsNone(resolution.error)
        self.assertEqual(resolution.arguments, {"record_id": "record-7"})

    def test_resolver_rejects_unsafe_or_missing_reference_paths(self) -> None:
        tool = ToolSpec(name="demo.detail", description="Read one record.")
        prior = (ToolResult(tool_name="demo.search", output={"records": []}),)

        resolution = resolve_tool_arguments(
            "Read the first result.",
            tool,
            {"record_id": "$last.records[0].id"},
            prior_results=prior,
        )
        unsafe = resolve_tool_arguments(
            "Read the first result.",
            tool,
            {"record_id": "$last.records['id']"},
            prior_results=prior,
        )

        self.assertIn("no item at index 0", resolution.error or "")
        self.assertIn("dotted fields and numeric indexes", unsafe.error or "")


class ArgumentBuilderTests(unittest.TestCase):
    """Tests for model-backed tool argument building and repair."""

    def test_builder_uses_model_to_create_calendar_arguments(self) -> None:
        provider = StaticModelProvider(
            """
{
  "calendar_id": "primary",
  "start_time": "2026-07-04T00:00:00-07:00",
  "end_time": "2026-07-11T00:00:00-07:00",
  "max_results": 10
}
""".strip()
        )
        tool = ToolSpec(
            name="google_calendar.list_events",
            description="List Google Calendar events.",
            input_schema={
                "type": "object",
                "properties": {
                    "calendar_id": {"type": "string"},
                    "start_time": {"type": "string"},
                    "end_time": {"type": "string"},
                    "max_results": {"type": "integer"},
                },
            },
            capability=ToolCapability(
                domain="calendar",
                operation="list_events",
                provider="google",
            ),
        )
        builder = ArgumentBuilder(
            ModelRouter({provider.name: provider}, provider.name),
        )

        resolution = builder.build(
            "Use Google Calendar to summarize my coming events in next 1 week",
            tool,
            {},
            model_name=provider.name,
            model_mode="balanced",
        )

        self.assertIsNone(resolution.error)
        self.assertEqual(resolution.arguments["calendar_id"], "primary")
        self.assertEqual(
            resolution.arguments["start_time"],
            "2026-07-04T00:00:00-07:00",
        )

    def test_builder_retries_after_validation_error(self) -> None:
        provider = SequencedModelProvider(
            [
                '{"wrong": "shape"}',
                '{"eventId": "evt-1"}',
            ]
        )
        tool = ToolSpec(
            name="google_calendar.get_event",
            description="Get a Google Calendar event.",
            input_schema={
                "type": "object",
                "properties": {"eventId": {"type": "string"}},
                "required": ["eventId"],
            },
            capability=ToolCapability(
                domain="calendar",
                operation="get_event",
                provider="google",
            ),
        )
        builder = ArgumentBuilder(
            ModelRouter({provider.name: provider}, provider.name),
        )

        resolution = builder.build(
            "Get event evt-1 from Google Calendar",
            tool,
            {},
            model_name=provider.name,
            model_mode="balanced",
        )

        self.assertIsNone(resolution.error)
        self.assertEqual(resolution.arguments, {"eventId": "evt-1"})

    def test_builder_repairs_bad_last_reference_for_model_backed_tool(self) -> None:
        provider = StaticModelProvider('{"calendar_id": "primary"}')
        tool = ToolSpec(
            name="google_calendar.list_events",
            description="List Google Calendar events.",
            input_schema={
                "type": "object",
                "properties": {"calendar_id": {"type": "string"}},
            },
            capability=ToolCapability(
                domain="calendar",
                operation="list_events",
                provider="google",
            ),
        )
        builder = ArgumentBuilder(
            ModelRouter({provider.name: provider}, provider.name),
        )

        resolution = builder.build(
            "List my Google Calendar events",
            tool,
            {"calendar_id": "$last.text"},
            model_name=provider.name,
            model_mode="balanced",
        )

        self.assertIsNone(resolution.error)
        self.assertEqual(resolution.arguments, {"calendar_id": "primary"})

    def test_builder_fails_cleanly_after_invalid_attempts(self) -> None:
        provider = StaticModelProvider('{"wrong": "shape"}')
        tool = ToolSpec(
            name="google_calendar.get_event",
            description="Get a Google Calendar event.",
            input_schema={
                "type": "object",
                "properties": {"eventId": {"type": "string"}},
                "required": ["eventId"],
            },
            capability=ToolCapability(
                domain="calendar",
                operation="get_event",
                provider="google",
            ),
        )
        builder = ArgumentBuilder(
            ModelRouter({provider.name: provider}, provider.name),
        )

        resolution = builder.build(
            "Get an event from Google Calendar",
            tool,
            {},
            model_name=provider.name,
            model_mode="balanced",
        )

        self.assertIn("Missing required argument", resolution.error or "")

    def test_argument_builder_remains_compatible_alias(self) -> None:
        provider = StaticModelProvider('{"query": "Jordan"}')
        tool = ToolSpec(
            name="memory.search",
            description="Search memory.",
            input_schema={
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        )
        builder = ArgumentBuilder(
            ModelRouter({provider.name: provider}, provider.name),
        )

        resolution = builder.build(
            "search for Jordan",
            tool,
            {},
            model_name=provider.name,
            model_mode="balanced",
        )

        self.assertIsNone(resolution.error)
        self.assertEqual(resolution.arguments, {"query": "Jordan"})

    def test_tool_use_agent_includes_execution_feedback(self) -> None:
        provider = CapturingModelProvider('{"calendar_id": "primary"}')
        tool = ToolSpec(
            name="google_calendar.list_events",
            description="List Google Calendar events.",
            input_schema={
                "type": "object",
                "properties": {"calendar_id": {"type": "string"}},
            },
            capability=ToolCapability(
                domain="calendar",
                operation="list_events",
                provider="google",
            ),
        )
        agent = ToolUseAgent(
            ModelRouter({provider.name: provider}, provider.name),
            system_prompt="tool use prompt",
        )

        resolution = agent.build(
            "List calendar events",
            tool,
            {"calendar_id": "bad"},
            model_name=provider.name,
            model_mode="balanced",
            feedback=ToolUseFeedback(
                stage="execution",
                attempted_arguments={"calendar_id": "bad"},
                error="Invalid calendar id.",
                output={"status": 400},
            ),
        )

        self.assertIsNone(resolution.error)
        self.assertEqual(resolution.arguments, {"calendar_id": "primary"})
        self.assertIn("Invalid calendar id.", provider.last_request.messages[0])
        self.assertIn('"stage": "execution"', provider.last_request.messages[0])

    def test_tool_use_agent_includes_selected_tool_argument_hints(self) -> None:
        provider = CapturingModelProvider('{"query": "from:Jordan newer_than:30d"}')
        tool = ToolSpec(
            name="gmail.search_messages",
            description="Search Gmail messages.",
            argument_hints=(
                "Use Gmail search syntax in query. For recent mail, prefer "
                "newer_than:30d."
            ),
            input_schema={
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
            capability=ToolCapability(
                domain="email",
                operation="search_messages",
                provider="google",
            ),
        )
        agent = ToolUseAgent(
            ModelRouter({provider.name: provider}, provider.name),
            system_prompt="tool use prompt",
        )

        resolution = agent.build(
            "Find recent emails from Jordan",
            tool,
            {},
            model_name=provider.name,
            model_mode="balanced",
        )

        self.assertIsNone(resolution.error)
        self.assertEqual(
            resolution.arguments,
            {"query": "from:Jordan newer_than:30d"},
        )
        self.assertIsNotNone(provider.last_request)
        assert provider.last_request is not None
        self.assertIn("argument_hints", provider.last_request.messages[0])
        self.assertIn("newer_than:30d", provider.last_request.messages[0])

    def test_tool_use_agent_records_attempt_metadata(self) -> None:
        provider = SequencedModelProvider(
            [
                '{"wrong": "shape"}',
                '{"eventId": "evt-1"}',
            ]
        )
        tool = ToolSpec(
            name="google_calendar.get_event",
            description="Get a Google Calendar event.",
            input_schema={
                "type": "object",
                "properties": {"eventId": {"type": "string"}},
                "required": ["eventId"],
            },
            capability=ToolCapability(
                domain="calendar",
                operation="get_event",
                provider="google",
            ),
        )
        agent = ToolUseAgent(
            ModelRouter({provider.name: provider}, provider.name),
            system_prompt="tool use prompt",
        )

        resolution = agent.build(
            "Get event evt-1",
            tool,
            {},
            model_name=provider.name,
            model_mode="balanced",
        )

        self.assertIsNone(resolution.error)
        self.assertEqual(len(resolution.attempts), 2)
        self.assertIsNotNone(resolution.attempts[0].error)
        self.assertIsNone(resolution.attempts[1].error)


class GeneralToolTests(unittest.TestCase):
    """Tests for model-backed general language generation."""

    def test_general_generate_text_uses_selected_model(self) -> None:
        provider = StaticModelProvider("Generated body.")
        registry = default_tool_registry()
        context = ToolExecutionContext(
            goal="draft a note",
            model_name=provider.name,
            model_mode="balanced",
            models=ModelRouter({provider.name: provider}, provider.name),
        )

        result = registry.execute(
            ToolCall("general.generate_text", {"instruction": "Draft a note."}),
            context=context,
        )

        self.assertTrue(result.success)
        self.assertEqual(result.output["text"], "Generated body.")
        self.assertEqual(result.output["model"], provider.name)

    def test_general_generate_text_fake_local_is_deterministic(self) -> None:
        registry = default_tool_registry()
        context = ToolExecutionContext(
            goal="generate a fun fact",
            model_name="fake-local",
            model_mode="balanced",
            models=ModelRouter({}),
        )

        result = registry.execute(
            ToolCall("general.generate_text", {"instruction": "Generate a fun fact."}),
            context=context,
        )

        self.assertTrue(result.success)
        self.assertEqual(
            result.output["text"],
            "Generated text for: Generate a fun fact.",
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

        self.assertIn("Jordan owns the API migration.", result.final_response)
        self.assertNotIn("Completed tool calls", result.final_response)
        self.assertNotIn("Grounded results", result.final_response)
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

        self.assertIn("Jordan owns the API migration.", result.final_response)
        self.assertNotIn("Goal: prepare Jordan context", result.final_response)
        synthesis_event = next(
            event for event in result.trace if event.event_type == "synthesis.completed"
        )
        self.assertEqual(synthesis_event.data["source"], "failed_then_fallback")

    def test_orchestrator_rejects_runtime_shaped_synthesis(self) -> None:
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
                "Completed tool calls:\n- OK memory.search\n\nGrounded results:",
            ]
        )
        with TemporaryDirectory() as temp_dir:
            memory_store = MemoryStore(Path(temp_dir) / "memory.sqlite3")
            memory_store.add("Jordan owns the API migration.", memory_type="fact")
            orchestrator = _orchestrator_with_provider(provider, memory_store)

            result = orchestrator.run("prepare Jordan context", provider.name)

        self.assertIn("Jordan owns the API migration.", result.final_response)
        self.assertNotIn("Completed tool calls", result.final_response)
        synthesis_event = next(
            event for event in result.trace if event.event_type == "synthesis.completed"
        )
        self.assertEqual(synthesis_event.data["source"], "failed_then_fallback")

    def test_synthesis_rejects_unexecuted_tool_family_claim(self) -> None:
        available_tools = (
            AvailableTool(
                name="google_calendar.list_events",
                description="List calendar events.",
                argument_hints=None,
                risk_level="low",
                requires_approval=False,
                source="test",
                capability=ToolCapability(
                    domain="calendar",
                    operation="list_events",
                ),
            ),
            AvailableTool(
                name="gmail.list_recent",
                description="List recent Gmail messages.",
                argument_hints=None,
                risk_level="low",
                requires_approval=False,
                source="test",
                capability=ToolCapability(
                    domain="email",
                    operation="list_recent",
                ),
            ),
        )
        results = (
            ToolResult(
                tool_name="google_calendar.list_events",
                output={},
                success=False,
                error="The caller does not have permission.",
            ),
        )
        plan = ExecutionPlan(goal="Review my week", steps=())

        self.assertFalse(
            _is_supported_synthesis(
                "A related recent Gmail message is available.",
                plan,
                results,
                available_tools,
            )
        )
        self.assertFalse(
            _is_supported_synthesis(
                "Your calendar shows a one-on-one meeting.",
                plan,
                results,
                available_tools,
            )
        )
        self.assertFalse(
            _is_supported_synthesis(
                "This is likely a one-on-one meeting.",
                plan,
                results,
                available_tools,
            )
        )
        self.assertTrue(
            _is_supported_synthesis(
                "Calendar data is unavailable because the caller lacks permission.",
                plan,
                results,
                available_tools,
            )
        )


class ToolUseOrchestratorTests(unittest.TestCase):
    """Tests for ToolUseAgent integration in orchestration."""

    def test_generic_multitool_record_handoff_and_grounded_fallback(self) -> None:
        provider = SequencedModelProvider(
            [
                """
{
  "steps": [
    {
      "step_id": "find",
      "tool_name": "demo.search",
      "arguments": {"query": "release"},
      "description": "Find matching records."
    },
    {
      "step_id": "detail",
      "tool_name": "demo.detail",
      "arguments": {"record_id": "$step.find.records[0].id"},
      "description": "Read the first matching record.",
      "depends_on": ["find"]
    },
    {
      "step_id": "page",
      "tool_name": "demo.page",
      "arguments": {"page": 2},
      "description": "Read the next generic page."
    }
  ]
}
""".strip(),
                '{"query": "release"}',
                '{"record_id": "record-7"}',
                '{"page": 2}',
                "",
            ]
        )
        calls: list[tuple[str, dict[str, object]]] = []
        tools = ToolRegistry()
        tools.register(
            ToolSpec(
                name="demo.search",
                description="Search generic records.",
                input_schema={
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                },
                capability=ToolCapability(domain="knowledge", operation="search"),
            ),
            lambda arguments: (
                calls.append(("search", dict(arguments)))
                or {"records": [{"id": "record-7", "title": "Release notes"}]}
            ),
        )
        tools.register(
            ToolSpec(
                name="demo.detail",
                description="Read one generic record by id.",
                input_schema={
                    "type": "object",
                    "properties": {"record_id": {"type": "string"}},
                    "required": ["record_id"],
                },
                capability=ToolCapability(domain="knowledge", operation="get"),
            ),
            lambda arguments: (
                calls.append(("detail", dict(arguments)))
                or {"records": [{"id": arguments["record_id"], "title": "Release detail"}]}
            ),
        )
        tools.register(
            ToolSpec(
                name="demo.page",
                description="Read a generic paginated result page.",
                input_schema={
                    "type": "object",
                    "properties": {"page": {"type": "integer"}},
                    "required": ["page"],
                },
                capability=ToolCapability(domain="knowledge", operation="list"),
            ),
            lambda arguments: (
                calls.append(("page", dict(arguments)))
                or {"records": [{"id": "page-2", "title": "Second page"}]}
            ),
        )
        orchestrator = Orchestrator(
            agents=default_agent_registry(),
            tools=tools,
            models=ModelRouter({provider.name: provider}, provider.name),
            policies=PolicyEngine(),
            planner_prompt=PromptLibrary().planner_prompt(),
            synthesis_prompt=PromptLibrary().synthesis_prompt(),
            tool_use_prompt=PromptLibrary().tool_use_prompt(),
        )

        result = orchestrator.run("Find a release, inspect it, and load page two.", provider.name)

        self.assertEqual(result.status, "completed")
        self.assertEqual(
            calls,
            [
                ("search", {"query": "release"}),
                ("detail", {"record_id": "record-7"}),
                ("page", {"page": 2}),
            ],
        )
        self.assertIn("demo.search: 1 result(s)", result.final_response)
        self.assertIn("Release detail", result.final_response)
        self.assertIn("Second page", result.final_response)
        self.assertNotIn("mcp_result", result.final_response)

    def test_read_only_tool_execution_error_can_be_repaired(self) -> None:
        provider = SequencedModelProvider(
            [
                """
{
  "steps": [
    {
      "tool_name": "google_calendar.list_events",
      "arguments": {"calendar_id": "bad"},
      "description": "List calendar events."
    }
  ]
}
""".strip(),
                '{"calendar_id": "bad"}',
                '{"calendar_id": "primary"}',
                "Calendar events were summarized.",
            ]
        )
        calls: list[dict[str, object]] = []

        def flaky_calendar(arguments: dict[str, object]) -> dict[str, object]:
            calls.append(dict(arguments))
            if arguments.get("calendar_id") != "primary":
                raise ValueError("Invalid JSON payload: bad calendar id.")
            return {"text": "Events for primary: standup"}

        tools = ToolRegistry()
        tools.register(
            ToolSpec(
                name="google_calendar.list_events",
                description="List Google Calendar events.",
                source="mcp:google_calendar",
                input_schema={
                    "type": "object",
                    "properties": {"calendar_id": {"type": "string"}},
                },
                capability=ToolCapability(
                    domain="calendar",
                    operation="list_events",
                    provider="google",
                    read_only=True,
                ),
            ),
            flaky_calendar,
        )
        orchestrator = Orchestrator(
            agents=default_agent_registry(),
            tools=tools,
            models=ModelRouter({provider.name: provider}, provider.name),
            policies=PolicyEngine(),
            planner_prompt=PromptLibrary().planner_prompt(),
            synthesis_prompt=PromptLibrary().synthesis_prompt(),
            tool_use_prompt=PromptLibrary().tool_use_prompt(),
        )

        result = orchestrator.run("Use Google Calendar for events", provider.name)

        self.assertEqual(result.status, "completed")
        self.assertEqual(len(result.step_results), 1)
        self.assertEqual(calls, [{"calendar_id": "bad"}, {"calendar_id": "primary"}])
        self.assertEqual(
            result.plan.steps[0].tool_call.arguments,
            {"calendar_id": "primary"},
        )
        trace_types = [event.event_type for event in result.trace]
        self.assertIn("tool_use.execution_retry.started", trace_types)
        self.assertIn("tool_use.execution_retry.completed", trace_types)

    def test_execution_error_repair_does_not_retry_medium_risk_tool(self) -> None:
        provider = SequencedModelProvider(
            [
                """
{
  "steps": [
    {
      "tool_name": "google_calendar.list_events",
      "arguments": {"calendar_id": "bad"},
      "description": "List calendar events."
    }
  ]
}
""".strip(),
                '{"calendar_id": "bad"}',
                "Tool failed.",
            ]
        )
        calls: list[dict[str, object]] = []

        def failing_calendar(arguments: dict[str, object]) -> dict[str, object]:
            calls.append(dict(arguments))
            raise ValueError("Invalid JSON payload: bad calendar id.")

        tools = ToolRegistry()
        tools.register(
            ToolSpec(
                name="google_calendar.list_events",
                description="List Google Calendar events.",
                risk_level="medium",
                source="mcp:google_calendar",
                input_schema={
                    "type": "object",
                    "properties": {"calendar_id": {"type": "string"}},
                },
                capability=ToolCapability(
                    domain="calendar",
                    operation="list_events",
                    provider="google",
                    read_only=True,
                ),
            ),
            failing_calendar,
        )
        orchestrator = Orchestrator(
            agents=default_agent_registry(),
            tools=tools,
            models=ModelRouter({provider.name: provider}, provider.name),
            policies=PolicyEngine(),
            planner_prompt=PromptLibrary().planner_prompt(),
            synthesis_prompt=PromptLibrary().synthesis_prompt(),
            tool_use_prompt=PromptLibrary().tool_use_prompt(),
        )

        result = orchestrator.run("Use Google Calendar for events", provider.name)

        self.assertEqual(result.status, "failed")
        self.assertEqual(len(calls), 1)
        trace_types = [event.event_type for event in result.trace]
        self.assertNotIn("tool_use.execution_retry.started", trace_types)

    def test_execution_error_repair_does_not_retry_auth_error(self) -> None:
        provider = SequencedModelProvider(
            [
                """
{
  "steps": [
    {
      "tool_name": "google_calendar.list_events",
      "arguments": {"calendar_id": "primary"},
      "description": "List calendar events."
    }
  ]
}
""".strip(),
                '{"calendar_id": "primary"}',
                "Tool failed.",
            ]
        )
        calls: list[dict[str, object]] = []

        def auth_failing_calendar(arguments: dict[str, object]) -> dict[str, object]:
            calls.append(dict(arguments))
            raise ValueError("OAuth token request failed: client_secret is missing.")

        tools = ToolRegistry()
        tools.register(
            ToolSpec(
                name="google_calendar.list_events",
                description="List Google Calendar events.",
                source="mcp:google_calendar",
                input_schema={
                    "type": "object",
                    "properties": {"calendar_id": {"type": "string"}},
                },
                capability=ToolCapability(
                    domain="calendar",
                    operation="list_events",
                    provider="google",
                    read_only=True,
                ),
            ),
            auth_failing_calendar,
        )
        orchestrator = Orchestrator(
            agents=default_agent_registry(),
            tools=tools,
            models=ModelRouter({provider.name: provider}, provider.name),
            policies=PolicyEngine(),
            planner_prompt=PromptLibrary().planner_prompt(),
            synthesis_prompt=PromptLibrary().synthesis_prompt(),
            tool_use_prompt=PromptLibrary().tool_use_prompt(),
        )

        result = orchestrator.run("Use Google Calendar for events", provider.name)

        self.assertEqual(result.status, "failed")
        self.assertEqual(len(calls), 1)
        trace_types = [event.event_type for event in result.trace]
        self.assertNotIn("tool_use.execution_retry.started", trace_types)

    def test_execution_error_repair_failure_keeps_step_failed(self) -> None:
        provider = SequencedModelProvider(
            [
                """
{
  "steps": [
    {
      "tool_name": "google_calendar.list_events",
      "arguments": {"calendar_id": "bad"},
      "description": "List calendar events."
    }
  ]
}
""".strip(),
                '{"calendar_id": "bad"}',
                '{"calendar_id": "still-bad"}',
                "Tool failed.",
            ]
        )
        calls: list[dict[str, object]] = []

        def failing_calendar(arguments: dict[str, object]) -> dict[str, object]:
            calls.append(dict(arguments))
            raise ValueError("Invalid JSON payload: bad calendar id.")

        tools = ToolRegistry()
        tools.register(
            ToolSpec(
                name="google_calendar.list_events",
                description="List Google Calendar events.",
                source="mcp:google_calendar",
                input_schema={
                    "type": "object",
                    "properties": {"calendar_id": {"type": "string"}},
                },
                capability=ToolCapability(
                    domain="calendar",
                    operation="list_events",
                    provider="google",
                    read_only=True,
                ),
            ),
            failing_calendar,
        )
        orchestrator = Orchestrator(
            agents=default_agent_registry(),
            tools=tools,
            models=ModelRouter({provider.name: provider}, provider.name),
            policies=PolicyEngine(),
            planner_prompt=PromptLibrary().planner_prompt(),
            synthesis_prompt=PromptLibrary().synthesis_prompt(),
            tool_use_prompt=PromptLibrary().tool_use_prompt(),
        )

        result = orchestrator.run("Use Google Calendar for events", provider.name)

        self.assertEqual(result.status, "failed")
        self.assertEqual(len(result.step_results), 1)
        self.assertEqual(calls, [{"calendar_id": "bad"}, {"calendar_id": "still-bad"}])
        self.assertEqual(
            result.step_results[0].error,
            "Invalid JSON payload: bad calendar id.",
        )
        failed_event = next(
            event for event in result.trace if event.event_type == "step.failed"
        )
        self.assertEqual(
            failed_event.data["error"],
            "Invalid JSON payload: bad calendar id.",
        )
        with TemporaryDirectory() as temp_dir:
            trace_store = TraceStore(Path(temp_dir) / "traces.sqlite3")
            trace_store.save_run(result)
            stored_trace = trace_store.get_run(result.run_id)

        self.assertIsNotNone(stored_trace)
        assert stored_trace is not None
        stored_failure = next(
            event for event in stored_trace.events if event.event_type == "step.failed"
        )
        self.assertEqual(
            stored_failure.data["error"],
            "Invalid JSON payload: bad calendar id.",
        )
        trace_types = [event.event_type for event in result.trace]
        self.assertIn("tool_use.execution_retry.completed", trace_types)


class EvalHarnessTests(unittest.TestCase):
    """Tests for isolated planner and ToolUseAgent evals."""

    def test_load_eval_suite_from_json(self) -> None:
        with TemporaryDirectory() as temp_dir:
            suite_path = Path(temp_dir) / "suite.json"
            suite_path.write_text(
                json.dumps(
                    {
                        "name": "demo suite",
                        "description": "Small planner suite.",
                        "cases": [
                            {
                                "id": "calendar-list",
                                "kind": "planner",
                                "goal": "List my calendars.",
                                "expected_tools": ["google_calendar.list_calendars"],
                                "expected_tool_groups": [
                                    ["google_calendar.list_calendars", "demo.list"]
                                ],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            suite = load_eval_suite(suite_path)

        self.assertEqual(suite.name, "demo suite")
        self.assertEqual(suite.cases[0].expected_tools, ("google_calendar.list_calendars",))
        self.assertEqual(
            suite.cases[0].expected_tool_groups,
            (("google_calendar.list_calendars", "demo.list"),),
        )

    def test_planner_eval_scores_expected_tool_choice(self) -> None:
        tools = ToolRegistry()
        tools.register(
            ToolSpec(
                name="demo.search",
                description="Search demo records.",
                input_schema={
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                },
            ),
            lambda arguments: {"text": "unused"},
        )
        provider = StaticModelProvider(
            """
{
  "steps": [
    {
      "tool_name": "demo.search",
      "arguments": {"query": "Jordan"},
      "description": "Search demo records."
    }
  ]
}
""".strip()
        )
        suite = EvalSuite(
            name="planner demo",
            description=None,
            cases=(
                EvalCase(
                    id="demo-search",
                    kind="planner",
                    goal="Find demo records about Jordan.",
                    expected_tools=("demo.search",),
                    expected_tool_groups=(("demo.search", "demo.lookup"),),
                    forbidden_tools=("task.breakdown",),
                    max_steps=1,
                ),
            ),
        )

        with TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "jarvis.toml"
            config_path.write_text("", encoding="utf-8")
            settings = load_settings(config_path)
            report = run_eval_suite(
                suite=suite,
                settings=settings,
                model_name=provider.name,
                model_mode="balanced",
                agents=default_agent_registry(),
                tools=tools,
                models=ModelRouter({provider.name: provider}, provider.name),
            )

        self.assertEqual(report.passed, 1)
        self.assertEqual(report.failed, 0)
        self.assertEqual(report.results[0].actual_tools, ("demo.search",))

    def test_planner_eval_flags_fallback_by_default(self) -> None:
        with TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "jarvis.toml"
            config_path.write_text("", encoding="utf-8")
            tools = default_tool_registry(
                MemoryStore(Path(temp_dir) / "memory.sqlite3")
            )
            suite = EvalSuite(
                name="fallback demo",
                description=None,
                cases=(
                    EvalCase(
                        id="no-fallback",
                        kind="planner",
                        goal="Find something.",
                        expected_tools=("demo.search",),
                    ),
                ),
            )
            settings = load_settings(config_path)
            report = run_eval_suite(
                suite=suite,
                settings=settings,
                model_name="fake-local",
                model_mode="balanced",
                tools=tools,
                models=ModelRouter({}, "fake-local"),
            )

        self.assertEqual(report.failed, 1)
        self.assertIn("Planner used fallback.", report.results[0].errors)

    def test_tool_use_eval_scores_arguments(self) -> None:
        tools = ToolRegistry()
        tools.register(
            ToolSpec(
                name="demo.lookup",
                description="Look up one demo record.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "limit": {"type": "integer"},
                    },
                    "required": ["query"],
                },
                argument_hints="Use a concise query and a small limit.",
            ),
            lambda arguments: {"text": "unused"},
        )
        provider = StaticModelProvider('{"query": "Jordan", "limit": 5}')
        suite = EvalSuite(
            name="tool use demo",
            description=None,
            cases=(
                EvalCase(
                    id="lookup-args",
                    kind="tool_use",
                    goal="Look up demo records about Jordan.",
                    tool_name="demo.lookup",
                    rough_arguments={},
                    expected_arguments={"query": "Jordan", "limit": 5},
                    required_argument_keys=("query",),
                    forbidden_argument_keys=("unused",),
                ),
            ),
        )

        with TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "jarvis.toml"
            config_path.write_text("", encoding="utf-8")
            settings = load_settings(config_path)
            report = run_eval_suite(
                suite=suite,
                settings=settings,
                model_name=provider.name,
                model_mode="balanced",
                tools=tools,
                models=ModelRouter({provider.name: provider}, provider.name),
            )

        self.assertEqual(report.passed, 1)
        self.assertEqual(report.results[0].actual_arguments["query"], "Jordan")

    def test_tool_use_eval_reports_missing_tool(self) -> None:
        suite = EvalSuite(
            name="missing tool demo",
            description=None,
            cases=(
                EvalCase(
                    id="missing-tool",
                    kind="tool_use",
                    goal="Use a missing tool.",
                    tool_name="missing.tool",
                    rough_arguments={},
                ),
            ),
        )

        with TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "jarvis.toml"
            config_path.write_text("", encoding="utf-8")
            settings = load_settings(config_path)
            report = run_eval_suite(
                suite=suite,
                settings=settings,
                model_name="fake-local",
                model_mode="balanced",
                tools=ToolRegistry(),
                models=ModelRouter({}, "fake-local"),
            )

        self.assertEqual(report.failed, 1)
        self.assertIn("Unknown tool: missing.tool", report.results[0].errors[0])

    def test_tool_use_eval_accepts_prior_results(self) -> None:
        tools = ToolRegistry()
        tools.register(
            ToolSpec(
                name="demo.echo",
                description="Echo demo text.",
                input_schema={
                    "type": "object",
                    "properties": {"text": {"type": "string"}},
                    "required": ["text"],
                },
            ),
            lambda arguments: {"text": "unused"},
        )
        suite = EvalSuite(
            name="prior result demo",
            description=None,
            cases=(
                EvalCase(
                    id="prior-text",
                    kind="tool_use",
                    goal="Echo the previous text.",
                    tool_name="demo.echo",
                    rough_arguments={"text": "$last.text"},
                    prior_results=(
                        ToolResult(
                            tool_name="general.generate_text",
                            output={"text": "hello from prior"},
                        ),
                    ),
                    expected_arguments={"text": "hello from prior"},
                ),
            ),
        )

        with TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "jarvis.toml"
            config_path.write_text("", encoding="utf-8")
            settings = load_settings(config_path)
            report = run_eval_suite(
                suite=suite,
                settings=settings,
                model_name="fake-local",
                model_mode="balanced",
                tools=tools,
                models=ModelRouter({}, "fake-local"),
            )

        self.assertEqual(report.passed, 1)
        self.assertEqual(report.results[0].actual_arguments["text"], "hello from prior")


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


class AuthStoreTests(unittest.TestCase):
    """Tests for local integration auth storage."""

    def test_auth_store_sets_lists_and_clears_tokens(self) -> None:
        with TemporaryDirectory() as temp_dir:
            store = AuthStore(Path(temp_dir) / "auth.sqlite3")

            record = store.set_token("google", "access", refresh_token="refresh")
            listed = store.list_tokens()
            loaded = store.get_token("google")
            cleared = store.clear_token("google")

        self.assertEqual(record.provider, "google")
        self.assertEqual(record.access_token, "access")
        self.assertEqual(len(listed), 1)
        self.assertIsNotNone(loaded)
        assert loaded is not None
        self.assertEqual(loaded.refresh_token, "refresh")
        self.assertTrue(cleared)

    def test_auth_debug_reports_scope_diff_without_token_value(self) -> None:
        secret_env = "JARVIS_TEST_CLIENT_SECRET"
        previous_secret = os.environ.pop(secret_env, None)
        with _tokeninfo_server() as tokeninfo_url:
            with TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                config_path = root / "jarvis.toml"
                config_path.write_text(
                    f"""
[auth]
database_path = "auth.sqlite3"

[[auth.oauth_providers]]
name = "demo_oauth"
client_id = "client-id"
client_secret_env = "{secret_env}"
tokeninfo_url = "{tokeninfo_url}"
scopes = ["calendar.read", "calendar.write"]
""".strip(),
                    encoding="utf-8",
                )
                settings = load_settings(config_path)
                auth_store = AuthStore(settings.auth.database_path)
                auth_store.set_token(
                    "demo_oauth",
                    _TokenInfoHandler.expected_token,
                    refresh_token="refresh-token",
                )

                debug = _auth_debug(settings, auth_store, "demo_oauth")

        if previous_secret is not None:
            os.environ[secret_env] = previous_secret
        encoded = json.dumps(debug, default=str)
        self.assertNotIn(_TokenInfoHandler.expected_token, encoded)
        self.assertTrue(debug["token_stored"])
        self.assertTrue(debug["refresh_token_stored"])
        self.assertEqual(debug["client_secret_env"], secret_env)
        self.assertFalse(debug["client_secret_present"])
        self.assertEqual(debug["granted_scopes"], ["calendar.read"])
        self.assertEqual(debug["missing_configured_scopes"], ["calendar.write"])
        self.assertTrue(debug["client_id_matches_audience"])

    def test_oauth_post_form_reports_provider_error(self) -> None:
        with _oauth_error_server() as token_url:
            with self.assertRaises(RuntimeError) as context:
                _post_form(token_url, {"grant_type": "refresh_token"})

        self.assertIn("invalid_request", str(context.exception))
        self.assertIn("client_secret is missing", str(context.exception))

    def test_auth_debug_handles_missing_provider(self) -> None:
        with TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "jarvis.toml"
            config_path.write_text("", encoding="utf-8")
            settings = load_settings(config_path)
            auth_store = AuthStore(settings.auth.database_path)

            debug = _auth_debug(settings, auth_store, "missing")

        self.assertFalse(debug["provider_configured"])
        self.assertFalse(debug["token_stored"])


class CliToolCallTests(unittest.TestCase):
    """Tests for direct tool-call debugging helpers."""

    def test_parse_args_json_requires_object(self) -> None:
        self.assertEqual(_parse_args_json('{"goal": "demo"}'), {"goal": "demo"})
        with self.assertRaises(ValueError):
            _parse_args_json("[]")

    def test_print_tool_result_prefers_text(self) -> None:
        stdout = StringIO()
        with redirect_stdout(stdout):
            _print_tool_result({"text": "hello"})

        self.assertEqual(stdout.getvalue().strip(), "hello")

    def test_tool_call_cli_executes_registered_tool(self) -> None:
        with TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "jarvis.toml"
            config_path.write_text("", encoding="utf-8")
            previous_argv = sys.argv
            sys.argv = [
                "jarvis",
                "tool",
                "call",
                "task.breakdown",
                "--args-json",
                '{"goal": "demo"}',
                "--config",
                str(config_path),
                "--json",
            ]
            stdout = StringIO()
            try:
                with redirect_stdout(stdout):
                    cli_main()
            finally:
                sys.argv = previous_argv

        payload = json.loads(stdout.getvalue())
        self.assertTrue(payload["success"])
        self.assertEqual(payload["tool_name"], "task.breakdown")
        self.assertEqual(payload["output"]["goal"], "demo")


class GoogleCalendarFastMcpWrapperTests(unittest.TestCase):
    """Tests for the local FastMCP Google Calendar REST wrapper."""

    def test_list_calendars_uses_stored_token_and_formats_results(self) -> None:
        module = _google_calendar_fastmcp_module()
        with _calendar_api_server() as api_base_url:
            with TemporaryDirectory() as temp_dir:
                auth_db = Path(temp_dir) / "auth.sqlite3"
                AuthStore(auth_db).set_token(
                    "google",
                    _CalendarApiHandler.expected_token,
                )

                text = module.list_calendars_text(
                    auth_db=auth_db,
                    config_path=None,
                    provider="google",
                    api_base_url=api_base_url,
                )

        self.assertEqual(_CalendarApiHandler.last_authorization, "Bearer test-token")
        self.assertIn("Calendars:", text)
        self.assertIn("Primary Calendar", text)
        self.assertIn("primary", text)

    def test_list_events_uses_time_bounds_and_formats_results(self) -> None:
        module = _google_calendar_fastmcp_module()
        with _calendar_api_server() as api_base_url:
            with TemporaryDirectory() as temp_dir:
                auth_db = Path(temp_dir) / "auth.sqlite3"
                AuthStore(auth_db).set_token(
                    "google",
                    _CalendarApiHandler.expected_token,
                )

                text = module.list_events_text(
                    auth_db=auth_db,
                    config_path=None,
                    provider="google",
                    api_base_url=api_base_url,
                    calendar_id="primary",
                    start_time="2026-07-04T00:00:00Z",
                    end_time="2026-07-05T00:00:00Z",
                    max_results=5,
                )

        self.assertIn("Events for primary:", text)
        self.assertIn("Planning Sync", text)
        self.assertIn("evt-1", text)
        self.assertEqual(
            _CalendarApiHandler.last_query.get("timeMin"),
            ["2026-07-04T00:00:00Z"],
        )
        self.assertEqual(
            _CalendarApiHandler.last_query.get("timeMax"),
            ["2026-07-05T00:00:00Z"],
        )

    def test_list_events_returns_normalized_records(self) -> None:
        module = _google_calendar_fastmcp_module()
        with _calendar_api_server() as api_base_url:
            with TemporaryDirectory() as temp_dir:
                auth_db = Path(temp_dir) / "auth.sqlite3"
                AuthStore(auth_db).set_token("google", _CalendarApiHandler.expected_token)
                result = module.list_events_result(
                    auth_db=auth_db,
                    config_path=None,
                    provider="google",
                    api_base_url=api_base_url,
                )

        self.assertEqual(result["ids"], ["evt-1"])
        self.assertEqual(result["records"][0]["title"], "Planning Sync")
        self.assertEqual(result["records"][0]["start_time"], "2026-07-04T09:00:00Z")


class GoogleGmailFastMcpWrapperTests(unittest.TestCase):
    """Tests for the local FastMCP Gmail REST wrapper."""

    def test_search_messages_fetches_metadata_and_formats_results(self) -> None:
        module = _google_gmail_fastmcp_module()
        with _gmail_api_server() as api_base_url:
            with TemporaryDirectory() as temp_dir:
                auth_db = Path(temp_dir) / "auth.sqlite3"
                AuthStore(auth_db).set_token(
                    "google",
                    _GmailApiHandler.expected_token,
                )

                text = module.search_messages_text(
                    auth_db=auth_db,
                    config_path=None,
                    provider="google",
                    api_base_url=api_base_url,
                    query="from:jordan",
                    max_results=5,
                )

        self.assertEqual(_GmailApiHandler.last_authorization, "Bearer test-token")
        self.assertEqual(_GmailApiHandler.last_list_query.get("q"), ["from:jordan"])
        self.assertIn('Gmail search results for "from:jordan":', text)
        self.assertIn("Project update", text)
        self.assertIn("id=msg-1", text)
        self.assertIn("thread=thread-1", text)

    def test_list_recent_uses_label_filter(self) -> None:
        module = _google_gmail_fastmcp_module()
        with _gmail_api_server() as api_base_url:
            with TemporaryDirectory() as temp_dir:
                auth_db = Path(temp_dir) / "auth.sqlite3"
                AuthStore(auth_db).set_token(
                    "google",
                    _GmailApiHandler.expected_token,
                )

                text = module.list_recent_text(
                    auth_db=auth_db,
                    config_path=None,
                    provider="google",
                    api_base_url=api_base_url,
                    max_results=2,
                    label_ids="INBOX,IMPORTANT",
                )

        self.assertEqual(
            _GmailApiHandler.last_list_query.get("labelIds"),
            ["INBOX", "IMPORTANT"],
        )
        self.assertIn("Recent Gmail messages:", text)
        self.assertIn("Project update", text)

    def test_list_recent_returns_normalized_records(self) -> None:
        module = _google_gmail_fastmcp_module()
        with _gmail_api_server() as api_base_url:
            with TemporaryDirectory() as temp_dir:
                auth_db = Path(temp_dir) / "auth.sqlite3"
                AuthStore(auth_db).set_token("google", _GmailApiHandler.expected_token)
                result = module.list_recent_result(
                    auth_db=auth_db,
                    config_path=None,
                    provider="google",
                    api_base_url=api_base_url,
                )

        self.assertEqual(result["ids"], ["msg-1"])
        self.assertEqual(result["records"][0]["subject"], "Project update")
        self.assertEqual(result["records"][0]["sender"], "Jordan <jordan@example.com>")

    def test_get_message_formats_one_message(self) -> None:
        module = _google_gmail_fastmcp_module()
        with _gmail_api_server() as api_base_url:
            with TemporaryDirectory() as temp_dir:
                auth_db = Path(temp_dir) / "auth.sqlite3"
                AuthStore(auth_db).set_token(
                    "google",
                    _GmailApiHandler.expected_token,
                )

                text = module.get_message_text(
                    auth_db=auth_db,
                    config_path=None,
                    provider="google",
                    api_base_url=api_base_url,
                    message_id="msg-1",
                )

        self.assertIn("Message:", text)
        self.assertIn("Project update", text)
        self.assertIn("from Jordan", text)

    def test_get_thread_formats_thread_messages(self) -> None:
        module = _google_gmail_fastmcp_module()
        with _gmail_api_server() as api_base_url:
            with TemporaryDirectory() as temp_dir:
                auth_db = Path(temp_dir) / "auth.sqlite3"
                AuthStore(auth_db).set_token(
                    "google",
                    _GmailApiHandler.expected_token,
                )

                text = module.get_thread_text(
                    auth_db=auth_db,
                    config_path=None,
                    provider="google",
                    api_base_url=api_base_url,
                    thread_id="thread-1",
                    max_messages=5,
                )

        self.assertIn("Thread thread-1:", text)
        self.assertIn("Project update", text)


class SpotifyFastMcpWrapperTests(unittest.TestCase):
    """Tests for the local FastMCP Spotify Web API read wrapper."""

    def test_search_formats_tracks_and_artists(self) -> None:
        module = _spotify_fastmcp_module()
        with _spotify_api_server() as api_base_url:
            with TemporaryDirectory() as temp_dir:
                auth_db = Path(temp_dir) / "auth.sqlite3"
                AuthStore(auth_db).set_token(
                    "spotify",
                    _SpotifyApiHandler.expected_token,
                )

                text = module.search_text(
                    auth_db=auth_db,
                    config_path=None,
                    provider="spotify",
                    api_base_url=api_base_url,
                    query="Daft Punk",
                    types="track,artist",
                    limit=5,
                )

        self.assertEqual(_SpotifyApiHandler.last_authorization, "Bearer test-token")
        self.assertEqual(_SpotifyApiHandler.last_query.get("q"), ["Daft Punk"])
        self.assertEqual(_SpotifyApiHandler.last_query.get("type"), ["track,artist"])
        self.assertIn('Spotify search results for "Daft Punk":', text)
        self.assertIn("One More Time by Daft Punk", text)
        self.assertIn("artist: Daft Punk", text)

    def test_current_playback_formats_current_track(self) -> None:
        module = _spotify_fastmcp_module()
        with _spotify_api_server() as api_base_url:
            with TemporaryDirectory() as temp_dir:
                auth_db = Path(temp_dir) / "auth.sqlite3"
                AuthStore(auth_db).set_token(
                    "spotify",
                    _SpotifyApiHandler.expected_token,
                )

                text = module.current_playback_text(
                    auth_db=auth_db,
                    config_path=None,
                    provider="spotify",
                    api_base_url=api_base_url,
                )

        self.assertIn("Spotify current playback: playing", text)
        self.assertIn("One More Time by Daft Punk", text)
        self.assertIn("Desk Speakers", text)

    def test_recently_played_formats_tracks(self) -> None:
        module = _spotify_fastmcp_module()
        with _spotify_api_server() as api_base_url:
            with TemporaryDirectory() as temp_dir:
                auth_db = Path(temp_dir) / "auth.sqlite3"
                AuthStore(auth_db).set_token(
                    "spotify",
                    _SpotifyApiHandler.expected_token,
                )

                text = module.recently_played_text(
                    auth_db=auth_db,
                    config_path=None,
                    provider="spotify",
                    api_base_url=api_base_url,
                    limit=3,
                )

        self.assertEqual(_SpotifyApiHandler.last_query.get("limit"), ["3"])
        self.assertIn("Spotify recently played:", text)
        self.assertIn("One More Time by Daft Punk", text)

    def test_list_playlists_formats_playlists(self) -> None:
        module = _spotify_fastmcp_module()
        with _spotify_api_server() as api_base_url:
            with TemporaryDirectory() as temp_dir:
                auth_db = Path(temp_dir) / "auth.sqlite3"
                AuthStore(auth_db).set_token(
                    "spotify",
                    _SpotifyApiHandler.expected_token,
                )

                text = module.list_playlists_text(
                    auth_db=auth_db,
                    config_path=None,
                    provider="spotify",
                    api_base_url=api_base_url,
                    limit=2,
                    offset=0,
                )

        self.assertIn("Spotify playlists:", text)
        self.assertIn("Focus Mix", text)
        self.assertIn("owner=Saket", text)


class McpTests(unittest.TestCase):
    """Tests for MCP stdio tool loading and execution."""

    def test_mcp_client_lists_and_calls_demo_tool(self) -> None:
        server = _demo_mcp_server_settings()
        client = McpStdioClient(server)

        tools = client.list_tools()
        result = client.call_tool("echo", {"text": "hello"})

        self.assertEqual(tools[0]["name"], "echo")
        self.assertEqual(result["content"][0]["text"], "demo echo: hello")

    def test_mcp_result_normalizes_structured_content(self) -> None:
        output = _normalize_mcp_result(
            {
                "content": [{"type": "text", "text": "Found one note."}],
                "structuredContent": {
                    "records": [{"id": "note-1", "title": "Project notes"}],
                    "metadata": {"record_type": "note"},
                },
            }
        )

        self.assertEqual(output["text"], "Found one note.")
        self.assertEqual(output["ids"], ["note-1"])
        self.assertEqual(output["records"][0]["title"], "Project notes")
        self.assertEqual(output["metadata"]["record_type"], "note")

    def test_mcp_result_rejects_embedded_auth_error(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "client_secret is missing"):
            _normalize_mcp_result(
                {
                    "content": [
                        {
                            "type": "text",
                            "text": '{"text":"AUTH_ERROR: client_secret is missing"}',
                        }
                    ],
                    "structuredContent": {
                        "text": "AUTH_ERROR: client_secret is missing",
                    },
                }
            )

    def test_mcp_client_reports_subprocess_stderr(self) -> None:
        with TemporaryDirectory() as temp_dir:
            server_path = Path(temp_dir) / "broken_server.py"
            server_path.write_text(
                "import sys\nsys.stderr.write('missing dependency')\nsys.exit(1)\n",
                encoding="utf-8",
            )
            server = McpServerSettings(
                name="broken",
                command=sys.executable,
                args=(str(server_path),),
            )
            client = McpStdioClient(server)

            with self.assertRaises(RuntimeError) as context:
                client.list_tools()

        self.assertIn("missing dependency", str(context.exception))

    def test_gmail_mcp_tool_capability_is_email_read_only(self) -> None:
        capability = _mcp_tool_capability(
            McpServerSettings(name="gmail", command="python"),
            "search_messages",
        )

        self.assertIsNotNone(capability)
        assert capability is not None
        self.assertEqual(capability.domain, "email")
        self.assertEqual(capability.operation, "search_messages")
        self.assertTrue(capability.read_only)

    def test_spotify_mcp_tool_capability_is_music_read_only(self) -> None:
        capability = _mcp_tool_capability(
            McpServerSettings(name="spotify", command="python"),
            "recently_played",
        )

        self.assertIsNotNone(capability)
        assert capability is not None
        self.assertEqual(capability.domain, "music")
        self.assertEqual(capability.operation, "recently_played")
        self.assertTrue(capability.read_only)

    def test_mcp_client_times_out_when_stdio_server_is_silent(self) -> None:
        with TemporaryDirectory() as temp_dir:
            server_path = Path(temp_dir) / "silent_server.py"
            server_path.write_text(
                "import sys\n"
                "import time\n"
                "sys.stderr.write('still starting\\n')\n"
                "sys.stderr.flush()\n"
                "time.sleep(60)\n",
                encoding="utf-8",
            )
            server = McpServerSettings(
                name="silent",
                command=sys.executable,
                args=(str(server_path),),
            )
            client = McpStdioClient(server, timeout_seconds=1)

            started_at = time.monotonic()
            with self.assertRaises(RuntimeError) as context:
                client.list_tools()
            elapsed = time.monotonic() - started_at

        self.assertLess(elapsed, 3)
        self.assertIn("did not respond before timeout", str(context.exception))
        self.assertIn("still starting", str(context.exception))

    def test_stdio_python_command_resolves_to_current_interpreter(self) -> None:
        self.assertEqual(_resolved_command("python"), sys.executable)
        self.assertEqual(_resolved_command("python.exe"), sys.executable)
        self.assertEqual(_resolved_command("custom-python"), "custom-python")

    def test_http_mcp_client_lists_and_calls_demo_tool(self) -> None:
        with _http_mcp_server() as server_url:
            server = McpServerSettings(
                name="http_demo",
                transport="http",
                url=server_url,
            )
            client = McpHttpClient(server)

            tools = client.list_tools()
            result = client.call_tool("echo", {"text": "hello http"})

        self.assertEqual(tools[0]["name"], "echo")
        self.assertEqual(result["content"][0]["text"], "http demo echo: hello http")

    def test_http_mcp_client_uses_stored_oauth_token(self) -> None:
        with TemporaryDirectory() as temp_dir:
            auth_store = AuthStore(Path(temp_dir) / "auth.sqlite3")
            auth_store.set_token("demo_oauth", _HttpMcpHandler.required_token)
            with _http_mcp_server(require_token=True) as server_url:
                server = McpServerSettings(
                    name="http_demo",
                    transport="http",
                    url=server_url,
                    auth_provider="demo_oauth",
                )
                client = McpHttpClient(server, auth_store)

                result = client.call_tool("echo", {"text": "authorized"})

        self.assertEqual(
            result["content"][0]["text"],
            "http demo echo: authorized",
        )

    def test_http_mcp_client_prompts_oauth_on_first_use(self) -> None:
        with TemporaryDirectory() as temp_dir:
            auth_store = AuthStore(Path(temp_dir) / "auth.sqlite3")
            callback_port = _free_port()
            with _oauth_server() as oauth_url:
                provider = OAuthProviderSettings(
                    name="demo_oauth",
                    client_id="client-id",
                    authorization_url=f"{oauth_url}/authorize",
                    token_url=f"{oauth_url}/token",
                    redirect_uri=f"http://127.0.0.1:{callback_port}/oauth/callback",
                    scopes=("calendar.readonly",),
                )
                oauth_manager = OAuthManager(
                    (provider,),
                    auth_store,
                    browser_opener=_callback_browser_opener,
                )
                with _http_mcp_server(require_token=True) as server_url:
                    server = McpServerSettings(
                        name="http_demo",
                        transport="http",
                        url=server_url,
                        auth_provider="demo_oauth",
                    )
                    client = McpHttpClient(server, auth_store, oauth_manager)

                    with redirect_stdout(StringIO()):
                        result = client.call_tool("echo", {"text": "after auth"})
                    stored = auth_store.get_token("demo_oauth")

        self.assertEqual(
            result["content"][0]["text"],
            "http demo echo: after auth",
        )
        self.assertIsNotNone(stored)
        assert stored is not None
        self.assertEqual(stored.access_token, _HttpMcpHandler.required_token)
        self.assertEqual(stored.refresh_token, "refresh-token")

    def test_oauth_manager_refreshes_expired_token(self) -> None:
        with TemporaryDirectory() as temp_dir:
            auth_store = AuthStore(Path(temp_dir) / "auth.sqlite3")
            auth_store.set_token(
                "demo_oauth",
                "expired-token",
                refresh_token="refresh-token",
                expires_at="2000-01-01T00:00:00+00:00",
            )
            callback_port = _free_port()
            with _oauth_server() as oauth_url:
                provider = OAuthProviderSettings(
                    name="demo_oauth",
                    client_id="client-id",
                    authorization_url=f"{oauth_url}/authorize",
                    token_url=f"{oauth_url}/token",
                    redirect_uri=f"http://127.0.0.1:{callback_port}/oauth/callback",
                    scopes=("calendar.readonly",),
                )
                oauth_manager = OAuthManager(
                    (provider,),
                    auth_store,
                    browser_opener=lambda url: (_OAuthHandler.opened_urls.append(url) or True),
                )

                token = oauth_manager.access_token("demo_oauth")
                stored = auth_store.get_token("demo_oauth")

        self.assertEqual(token, _HttpMcpHandler.required_token)
        self.assertIsNotNone(stored)
        assert stored is not None
        self.assertEqual(stored.refresh_token, "refresh-token")
        self.assertEqual(_OAuthHandler.last_grant_type, "refresh_token")
        self.assertEqual(_OAuthHandler.opened_urls, [])

    def test_oauth_manager_accepts_pasted_code_when_callback_does_not_arrive(
        self,
    ) -> None:
        with TemporaryDirectory() as temp_dir:
            auth_store = AuthStore(Path(temp_dir) / "auth.sqlite3")
            callback_port = _free_port()
            with _oauth_server() as oauth_url:
                provider = OAuthProviderSettings(
                    name="demo_oauth",
                    client_id="client-id",
                    authorization_url=f"{oauth_url}/authorize",
                    token_url=f"{oauth_url}/token",
                    redirect_uri=f"http://127.0.0.1:{callback_port}/oauth/callback",
                    scopes=("calendar.readonly",),
                )
                oauth_manager = OAuthManager(
                    (provider,),
                    auth_store,
                    browser_opener=lambda url: True,
                    input_reader=lambda prompt: "authorization-code",
                    timeout_seconds=3,
                )

                with redirect_stdout(StringIO()):
                    token = oauth_manager.access_token("demo_oauth")
                stored = auth_store.get_token("demo_oauth")

        self.assertEqual(token, _HttpMcpHandler.required_token)
        self.assertIsNotNone(stored)
        assert stored is not None
        self.assertEqual(stored.refresh_token, "refresh-token")

    def test_http_mcp_tool_is_registered_from_settings(self) -> None:
        with _http_mcp_server() as server_url:
            with TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                config_path = root / "jarvis.toml"
                config_path.write_text(
                    f"""
[[mcp.servers]]
name = "http_demo"
transport = "http"
url = "{server_url}"
""".strip(),
                    encoding="utf-8",
                )
                settings = load_settings(config_path)

                registry = create_default_tool_registry(settings)
                result = registry.execute(
                    ToolCall("http_demo.echo", {"text": "from http registry"})
                )

        self.assertTrue(result.success)
        self.assertEqual(
            result.output["text"],
            "http demo echo: from http registry",
        )

    def test_http_mcp_tool_drops_null_arguments(self) -> None:
        _HttpMcpHandler.last_arguments = None
        with _http_mcp_server() as server_url:
            with TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                config_path = root / "jarvis.toml"
                config_path.write_text(
                    f"""
[[mcp.servers]]
name = "http_demo"
transport = "http"
url = "{server_url}"
""".strip(),
                    encoding="utf-8",
                )
                settings = load_settings(config_path)

                registry = create_default_tool_registry(settings)
                result = registry.execute(
                    ToolCall(
                        "http_demo.echo",
                        {
                            "text": "clean",
                            "optional": None,
                            "nested": {"drop": None, "keep": "value"},
                        },
                    )
                )

        self.assertTrue(result.success)
        self.assertEqual(
            _HttpMcpHandler.last_arguments,
            {"text": "clean"},
        )

    def test_http_mcp_tool_drops_unknown_schema_arguments(self) -> None:
        _HttpMcpHandler.last_arguments = None
        with _http_mcp_server() as server_url:
            with TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                config_path = root / "jarvis.toml"
                config_path.write_text(
                    f"""
[[mcp.servers]]
name = "http_demo"
transport = "http"
url = "{server_url}"
""".strip(),
                    encoding="utf-8",
                )
                settings = load_settings(config_path)

                registry = create_default_tool_registry(settings)
                result = registry.execute(
                    ToolCall("http_demo.echo", {"text": "clean", "query": "bad"})
                )

        self.assertTrue(result.success)
        self.assertEqual(_HttpMcpHandler.last_arguments, {"text": "clean"})

    def test_http_mcp_tool_error_result_fails_tool_result(self) -> None:
        _HttpMcpHandler.force_tool_error = True
        try:
            with _http_mcp_server() as server_url:
                with TemporaryDirectory() as temp_dir:
                    root = Path(temp_dir)
                    config_path = root / "jarvis.toml"
                    config_path.write_text(
                        f"""
[[mcp.servers]]
name = "http_demo"
transport = "http"
url = "{server_url}"
""".strip(),
                        encoding="utf-8",
                    )
                    settings = load_settings(config_path)

                    registry = create_default_tool_registry(settings)
                    result = registry.execute(
                        ToolCall("http_demo.echo", {"text": "blocked"})
                    )
        finally:
            _HttpMcpHandler.force_tool_error = False

        self.assertFalse(result.success)
        self.assertEqual(result.error, "The caller does not have permission")

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

    def test_mcp_tool_policy_override_applies_to_registered_tool(self) -> None:
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
risk_level = "low"
requires_approval = false

[[mcp.servers.tools]]
name = "echo"
argument_hints = "Pass the exact text to echo."
risk_level = "medium"
requires_approval = true
""".strip(),
                encoding="utf-8",
            )
            settings = load_settings(config_path)

            registry = create_default_tool_registry(settings)
            tool = registry.get("demo_mcp.echo")

        self.assertEqual(tool.risk_level, "medium")
        self.assertEqual(tool.argument_hints, "Pass the exact text to echo.")
        self.assertTrue(tool.requires_approval)

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

    def test_orchestrator_passes_generated_text_to_mcp_tool(self) -> None:
        provider = SequencedModelProvider(
            [
                """
{
  "steps": [
    {
      "tool_name": "general.generate_text",
      "arguments": {"instruction": "Generate one short JarvisOS fact."},
      "description": "Generate text."
    },
    {
      "tool_name": "demo_mcp.echo",
      "arguments": {"text": "$last.text"},
      "description": "Echo generated text."
    }
  ]
}
""".strip(),
                "JarvisOS coordinates local tools.",
                "Echoed result: demo echo: JarvisOS coordinates local tools.",
            ]
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
            from jarvis.policies import PolicyEngine

            orchestrator = Orchestrator(
                agents=default_agent_registry(),
                tools=tools,
                models=ModelRouter({provider.name: provider}, provider.name),
                policies=PolicyEngine(),
                planner_prompt=PromptLibrary().planner_prompt(),
                synthesis_prompt=PromptLibrary().synthesis_prompt(),
            )

            result = orchestrator.run(
                "generate a JarvisOS fact and echo it",
                provider.name,
            )

        self.assertEqual(result.status, "completed")
        self.assertEqual(result.step_results[0].tool_name, "general.generate_text")
        self.assertEqual(
            result.step_results[1].output["text"],
            "demo echo: JarvisOS coordinates local tools.",
        )
        self.assertEqual(
            result.plan.steps[1].tool_call.arguments["text"],
            "JarvisOS coordinates local tools.",
        )
        self.assertIn(
            "demo echo: JarvisOS coordinates local tools.",
            result.final_response,
        )

    def test_last_text_reference_fails_without_prior_result(self) -> None:
        provider = FailingAfterFirstModelProvider(
            """
{
  "steps": [
    {
      "tool_name": "task.create_summary",
      "arguments": {"goal": "$last.text"},
      "description": "Summarize generated text."
    }
  ]
}
""".strip()
        )
        with TemporaryDirectory() as temp_dir:
            memory_store = MemoryStore(Path(temp_dir) / "memory.sqlite3")
            orchestrator = _orchestrator_with_provider(provider, memory_store)

            result = orchestrator.run("summarize previous generated text", provider.name)

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.step_results[0].tool_name, "task.create_summary")
        self.assertIn("$last.text", result.step_results[0].error or "")
        trace_types = [event.event_type for event in result.trace]
        self.assertIn("argument_resolution.failed", trace_types)

    def test_fake_local_fallback_does_not_route_mcp_echo_tool(self) -> None:
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
        self.assertNotIn("demo_mcp.echo", tool_names)
        self.assertIn("memory.search", tool_names)

    def test_fake_local_fallback_does_not_generate_text_before_mcp_echo(self) -> None:
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
                "Generate a fun fact about JarvisOS and echo it",
                model_name="fake-local",
            )

        tool_names = [item.tool_name for item in result.step_results]
        self.assertNotIn("general.generate_text", tool_names)
        self.assertNotIn("demo_mcp.echo", tool_names)
        self.assertIn("memory.search", tool_names)


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


def _google_calendar_fastmcp_module():
    path = (
        Path(__file__).resolve().parents[1]
        / "examples"
        / "mcp"
        / "google_calendar_fastmcp_server.py"
    )
    spec = importlib.util.spec_from_file_location("google_calendar_fastmcp_server", path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _google_gmail_fastmcp_module():
    path = (
        Path(__file__).resolve().parents[1]
        / "examples"
        / "mcp"
        / "google_gmail_fastmcp_server.py"
    )
    spec = importlib.util.spec_from_file_location("google_gmail_fastmcp_server", path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _spotify_fastmcp_module():
    path = (
        Path(__file__).resolve().parents[1]
        / "examples"
        / "mcp"
        / "spotify_fastmcp_server.py"
    )
    spec = importlib.util.spec_from_file_location("spotify_fastmcp_server", path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@contextmanager
def _calendar_api_server():
    _CalendarApiHandler.reset()
    server = ThreadingHTTPServer(("127.0.0.1", 0), _CalendarApiHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        yield f"http://{host}:{port}/calendar/v3"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


class _CalendarApiHandler(BaseHTTPRequestHandler):
    """Local Google Calendar REST stand-in for wrapper tests."""

    expected_token = "test-token"
    last_authorization: str | None = None
    last_query: dict[str, list[str]] = {}

    @classmethod
    def reset(cls) -> None:
        cls.last_authorization = None
        cls.last_query = {}

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        type(self).last_authorization = self.headers.get("Authorization")
        type(self).last_query = parse_qs(parsed.query)
        expected = f"Bearer {self.expected_token}"
        if self.headers.get("Authorization") != expected:
            self._write_json({"error": {"message": "unauthorized"}}, status=401)
            return
        if parsed.path == "/calendar/v3/users/me/calendarList":
            self._write_json(
                {
                    "items": [
                        {
                            "id": "primary",
                            "summary": "Primary Calendar",
                            "accessRole": "owner",
                            "primary": True,
                        }
                    ]
                }
            )
            return
        if parsed.path == "/calendar/v3/calendars/primary/events":
            self._write_json(
                {
                    "items": [
                        {
                            "id": "evt-1",
                            "summary": "Planning Sync",
                            "start": {"dateTime": "2026-07-04T09:00:00Z"},
                            "end": {"dateTime": "2026-07-04T09:30:00Z"},
                        }
                    ]
                }
            )
            return
        self.send_error(404)

    def _write_json(self, payload: dict[str, object], status: int = 200) -> None:
        encoded = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, format: str, *args) -> None:
        return


@contextmanager
def _gmail_api_server():
    _GmailApiHandler.reset()
    server = ThreadingHTTPServer(("127.0.0.1", 0), _GmailApiHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        yield f"http://{host}:{port}/gmail/v1"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


class _GmailApiHandler(BaseHTTPRequestHandler):
    """Local Gmail REST stand-in for wrapper tests."""

    expected_token = "test-token"
    last_authorization: str | None = None
    last_query: dict[str, list[str]] = {}
    last_list_query: dict[str, list[str]] = {}
    last_message_query: dict[str, list[str]] = {}

    @classmethod
    def reset(cls) -> None:
        cls.last_authorization = None
        cls.last_query = {}
        cls.last_list_query = {}
        cls.last_message_query = {}

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        type(self).last_authorization = self.headers.get("Authorization")
        type(self).last_query = parse_qs(parsed.query)
        expected = f"Bearer {self.expected_token}"
        if self.headers.get("Authorization") != expected:
            self._write_json({"error": {"message": "unauthorized"}}, status=401)
            return
        if parsed.path == "/gmail/v1/users/me/messages":
            type(self).last_list_query = parse_qs(parsed.query)
            self._write_json({"messages": [{"id": "msg-1", "threadId": "thread-1"}]})
            return
        if parsed.path == "/gmail/v1/users/me/messages/msg-1":
            type(self).last_message_query = parse_qs(parsed.query)
            self._write_json(_gmail_message("msg-1", "thread-1"))
            return
        if parsed.path == "/gmail/v1/users/me/threads/thread-1":
            self._write_json(
                {
                    "id": "thread-1",
                    "messages": [_gmail_message("msg-1", "thread-1")],
                }
            )
            return
        self.send_error(404)

    def _write_json(self, payload: dict[str, object], status: int = 200) -> None:
        encoded = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, format: str, *args) -> None:
        return


def _gmail_message(message_id: str, thread_id: str) -> dict[str, object]:
    return {
        "id": message_id,
        "threadId": thread_id,
        "snippet": "Here is the project update.",
        "payload": {
            "headers": [
                {"name": "From", "value": "Jordan <jordan@example.com>"},
                {"name": "To", "value": "Saket <saket@example.com>"},
                {"name": "Subject", "value": "Project update"},
                {"name": "Date", "value": "Sun, 5 Jul 2026 09:00:00 -0700"},
            ]
        },
    }


@contextmanager
def _spotify_api_server():
    _SpotifyApiHandler.reset()
    server = ThreadingHTTPServer(("127.0.0.1", 0), _SpotifyApiHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        yield f"http://{host}:{port}/v1"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


class _SpotifyApiHandler(BaseHTTPRequestHandler):
    """Local Spotify Web API stand-in for wrapper tests."""

    expected_token = "test-token"
    last_authorization: str | None = None
    last_query: dict[str, list[str]] = {}

    @classmethod
    def reset(cls) -> None:
        cls.last_authorization = None
        cls.last_query = {}

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        type(self).last_authorization = self.headers.get("Authorization")
        type(self).last_query = parse_qs(parsed.query)
        expected = f"Bearer {self.expected_token}"
        if self.headers.get("Authorization") != expected:
            self._write_json({"error": {"message": "unauthorized"}}, status=401)
            return
        if parsed.path == "/v1/search":
            self._write_json(_spotify_search_payload())
            return
        if parsed.path == "/v1/me/player":
            self._write_json(
                {
                    "is_playing": True,
                    "device": {"name": "Desk Speakers"},
                    "item": _spotify_track(),
                }
            )
            return
        if parsed.path == "/v1/me/player/recently-played":
            self._write_json(
                {
                    "items": [
                        {
                            "track": _spotify_track(),
                            "played_at": "2026-07-05T09:00:00Z",
                        }
                    ]
                }
            )
            return
        if parsed.path == "/v1/me/playlists":
            self._write_json(
                {
                    "items": [
                        {
                            "id": "playlist-1",
                            "name": "Focus Mix",
                            "owner": {"display_name": "Saket"},
                        }
                    ]
                }
            )
            return
        self.send_error(404)

    def _write_json(self, payload: dict[str, object], status: int = 200) -> None:
        encoded = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, format: str, *args) -> None:
        return


def _spotify_search_payload() -> dict[str, object]:
    return {
        "tracks": {"items": [_spotify_track()]},
        "artists": {
            "items": [
                {
                    "id": "artist-1",
                    "name": "Daft Punk",
                    "type": "artist",
                }
            ]
        },
    }


def _spotify_track() -> dict[str, object]:
    return {
        "id": "track-1",
        "name": "One More Time",
        "artists": [{"name": "Daft Punk"}],
        "type": "track",
    }


@contextmanager
def _http_mcp_server(require_token: bool = False):
    _HttpMcpHandler.require_token = require_token
    server = ThreadingHTTPServer(("127.0.0.1", 0), _HttpMcpHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        yield f"http://{host}:{port}/mcp"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        _HttpMcpHandler.require_token = False


class _HttpMcpHandler(BaseHTTPRequestHandler):
    """Local streamable HTTP MCP test server."""

    required_token = "test-access-token"
    require_token = False
    last_arguments: dict[str, object] | None = None
    force_tool_error = False

    def do_POST(self) -> None:
        if self.path != "/mcp":
            self.send_error(404)
            return
        if self.require_token:
            expected = f"Bearer {self.required_token}"
            if self.headers.get("Authorization") != expected:
                self.send_response(401)
                self.send_header(
                    "WWW-Authenticate",
                    'Bearer resource_metadata="http://127.0.0.1/.well-known/oauth"',
                )
                self.end_headers()
                return
        length = int(self.headers.get("Content-Length", "0"))
        request = json.loads(self.rfile.read(length).decode("utf-8"))
        method = request.get("method")
        if method == "notifications/initialized":
            self.send_response(202)
            self.end_headers()
            return
        result = self._result_for_request(method, request.get("params", {}))
        response = {
            "jsonrpc": "2.0",
            "id": request.get("id"),
            "result": result,
        }
        payload = json.dumps(response).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Mcp-Session-Id", "session-test")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args) -> None:
        return

    def _result_for_request(
        self,
        method: str | None,
        params: dict[str, object],
    ) -> dict[str, object]:
        if method == "initialize":
            return {
                "protocolVersion": "2025-03-26",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "http-demo", "version": "0.1.0"},
            }
        if method == "tools/list":
            return {
                "tools": [
                    {
                        "name": "echo",
                        "description": "Echo text over HTTP MCP.",
                        "inputSchema": {
                            "type": "object",
                            "properties": {"text": {"type": "string"}},
                        },
                    }
                ]
            }
        if method == "tools/call":
            arguments = params.get("arguments", {})
            type(self).last_arguments = arguments if isinstance(arguments, dict) else None
            if type(self).force_tool_error:
                return {
                    "content": [
                        {
                            "type": "text",
                            "text": "The caller does not have permission",
                        }
                    ],
                    "isError": True,
                }
            text = ""
            if isinstance(arguments, dict):
                text = str(arguments.get("text", ""))
            return {
                "content": [
                    {
                        "type": "text",
                        "text": f"http demo echo: {text}",
                    }
                ]
            }
        return {}


@contextmanager
def _oauth_server():
    _OAuthHandler.reset()
    server = ThreadingHTTPServer(("127.0.0.1", 0), _OAuthHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@contextmanager
def _oauth_error_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _OAuthErrorHandler)
    host, port = server.server_address
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://{host}:{port}/token"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@contextmanager
def _tokeninfo_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _TokenInfoHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        yield f"http://{host}:{port}/tokeninfo"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


class _TokenInfoHandler(BaseHTTPRequestHandler):
    """Local token-info endpoint for redacted auth debug tests."""

    expected_token = "debug-access-token"

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path != "/tokeninfo":
            self.send_error(404)
            return
        token = _first_query_value(parse_qs(parsed.query), "access_token")
        if token != self.expected_token:
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            payload = json.dumps({"error": "invalid_token"}).encode("utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        payload = json.dumps(
            {
                "aud": "client-id",
                "scope": "calendar.read",
                "expires_in": "3600",
                "email": "user@example.com",
                "sub": "subject-id",
            }
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args) -> None:
        return


class _OAuthHandler(BaseHTTPRequestHandler):
    """Local OAuth token endpoint for tests."""

    last_grant_type: str | None = None
    opened_urls: list[str] = []

    @classmethod
    def reset(cls) -> None:
        cls.last_grant_type = None
        cls.opened_urls = []

    def do_POST(self) -> None:
        if self.path != "/token":
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length).decode("utf-8")
        values = parse_qs(body)
        type(self).last_grant_type = _first_query_value(values, "grant_type")
        payload = {
            "access_token": _HttpMcpHandler.required_token,
            "refresh_token": "refresh-token",
            "expires_in": 3600,
            "token_type": "Bearer",
        }
        encoded = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, format: str, *args) -> None:
        return


class _OAuthErrorHandler(BaseHTTPRequestHandler):
    """Local OAuth endpoint that returns a redacted provider error."""

    def do_POST(self) -> None:
        payload = {
            "error": "invalid_request",
            "error_description": "client_secret is missing.",
        }
        encoded = json.dumps(payload).encode("utf-8")
        self.send_response(400)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, format: str, *args) -> None:
        return


def _callback_browser_opener(auth_url: str) -> bool:
    _OAuthHandler.opened_urls.append(auth_url)
    parsed = urlparse(auth_url)
    values = parse_qs(parsed.query)
    redirect_uri = _first_query_value(values, "redirect_uri")
    state = _first_query_value(values, "state")
    assert redirect_uri is not None
    query = urlencode({"code": "authorization-code", "state": state or ""})
    with urlopen(f"{redirect_uri}?{query}", timeout=10) as response:
        response.read()
    return True


def _first_query_value(values: dict[str, list[str]], key: str) -> str | None:
    items = values.get(key)
    if not items:
        return None
    return items[0]


def _free_port() -> int:
    with socket() as item:
        item.bind(("127.0.0.1", 0))
        return int(item.getsockname()[1])


class GeminiProviderTests(unittest.TestCase):
    """Tests for the optional Gemini model-provider adapter."""

    def test_gemini_provider_uses_stateless_interactions_api(self) -> None:
        class FakeInteractions:
            def __init__(self) -> None:
                self.calls: list[dict[str, object]] = []

            def create(self, **kwargs: object) -> object:
                self.calls.append(kwargs)
                return type("Interaction", (), {"output_text": "Gemini reply"})()

        class FakeClient:
            created_with: str | None = None
            http_options: dict[str, object] | None = None
            interactions = FakeInteractions()

            def __init__(self, api_key: str, **kwargs: object) -> None:
                type(self).created_with = api_key
                type(self).http_options = kwargs.get("http_options")

        provider = GeminiProvider("gemini-3.5-flash")
        with patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"}, clear=False):
            with patch(
                "jarvis.models.router._load_gemini_client",
                return_value=FakeClient,
            ):
                response = provider.generate(
                    ModelRequest(
                        goal="Answer the user.",
                        messages=["Relevant context."],
                        system_prompt="Be concise.",
                    )
                )

        self.assertEqual(response.text, "Gemini reply")
        self.assertEqual(response.model_name, "gemini/gemini-3.5-flash")
        self.assertEqual(FakeClient.created_with, "test-key")
        self.assertEqual(
            FakeClient.http_options,
            {"client_args": {"timeout": 60.0}},
        )
        self.assertEqual(
            FakeClient.interactions.calls,
            [
                {
                    "model": "gemini-3.5-flash",
                    "input": "Relevant context.\n\nAnswer the user.",
                    "system_instruction": "Be concise.",
                    "store": False,
                }
            ],
        )

    def test_gemini_provider_reports_missing_api_key(self) -> None:
        provider = GeminiProvider("gemini-3.5-flash")
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ModelProviderError, "GEMINI_API_KEY"):
                provider.generate(ModelRequest(goal="Hello"))

    def test_gemini_provider_reports_empty_response(self) -> None:
        class FakeInteractions:
            def create(self, **kwargs: object) -> object:
                return type("Interaction", (), {"output_text": ""})()

        class FakeClient:
            def __init__(self, api_key: str, **kwargs: object) -> None:
                self.interactions = FakeInteractions()

        provider = GeminiProvider("gemini-3.5-flash")
        with patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"}, clear=False):
            with patch(
                "jarvis.models.router._load_gemini_client",
                return_value=FakeClient,
            ):
                with self.assertRaisesRegex(ModelProviderError, "empty response"):
                    provider.generate(ModelRequest(goal="Hello"))

    def test_router_registers_only_configured_or_routed_gemini_models(self) -> None:
        with TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "jarvis.toml"
            config_path.write_text(
                """
[models.roles]
planner = "gemini/gemini-3.5-flash"

[providers.gemini]
models = ["gemini-3.1-pro-preview"]
api_key_env = "CUSTOM_GEMINI_KEY"
timeout_seconds = 30
""".strip(),
                encoding="utf-8",
            )
            router = default_model_router(load_settings(config_path))

        self.assertIn("gemini/gemini-3.5-flash", router.list())
        self.assertIn("gemini/gemini-3.1-pro-preview", router.list())

    def test_gemini_key_does_not_register_a_model_without_configuration(self) -> None:
        with TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "jarvis.toml"
            config_path.write_text("", encoding="utf-8")
            with patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"}, clear=False):
                router = default_model_router(load_settings(config_path))

        self.assertNotIn("gemini/gemini-3.5-flash", router.list())


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


class CapturingModelProvider(ModelProvider):
    """Model provider that records the last request it received."""

    name = "capturing-model"

    def __init__(self, text: str) -> None:
        self._text = text
        self.last_request: ModelRequest | None = None

    def generate(self, request: ModelRequest) -> ModelResponse:
        self.last_request = request
        return ModelResponse(text=self._text, model_name=self.name)


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
