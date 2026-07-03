import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from jarvis.contracts import ToolCall
from jarvis.memory import MemoryExtractor, MemoryStore
from jarvis.runtime import create_default_orchestrator
from jarvis.runtime import create_default_tool_registry
from jarvis.settings import load_settings


class RuntimeTests(unittest.TestCase):
    """Tests for the default local runtime loop."""

    def test_simple_goal_runs(self) -> None:
        result = create_default_orchestrator().run(
            "break this task into steps",
            model_name="fake-local",
        )

        self.assertEqual(result.status, "completed")
        self.assertIn("task.breakdown", [item.tool_name for item in result.step_results])
        self.assertIn("Goal: break this task into steps", result.final_response)

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

        self.assertEqual(settings.resolve_model("manual-model", "private"), "manual-model")
        self.assertEqual(settings.resolve_model(None, "private"), "ollama/llama3.2:3b")
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

        self.assertEqual(settings.memory.database_path, Path(temp_dir) / "state" / "memory.sqlite3")
        self.assertTrue(settings.memory.auto_extract)
        self.assertFalse(settings.memory.auto_write)


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


if __name__ == "__main__":
    unittest.main()
