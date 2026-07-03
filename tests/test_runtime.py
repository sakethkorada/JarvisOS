import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from jarvis.runtime import create_default_orchestrator
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


if __name__ == "__main__":
    unittest.main()
