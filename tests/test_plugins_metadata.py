from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from jarvis.integrations.plugins import load_plugin_manifest, register_plugin_tools
from jarvis.tools.registry import ToolRegistry


def _write_plugin(tmp_path: Path, manifest: str) -> Path:
    plugin = tmp_path / "plugin"
    plugin.mkdir()
    (plugin / "plugin.toml").write_text(manifest, encoding="utf-8")
    (plugin / "tools.py").write_text(
        "def run(arguments):\n    return {'received': arguments}\n",
        encoding="utf-8",
    )
    return plugin


class PluginMetadataTests(unittest.TestCase):
    def test_plugin_metadata_is_registered_for_planner(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            plugin = _write_plugin(
                Path(directory),
        '''
name = "metadata"
description = "Metadata test plugin"

[[tools]]
name = "demo.search"
description = "Search demo records"
handler = "tools.run"
argument_hints = "query is a short search phrase"
input_schema = { type = "object", required = ["query"], properties = { query = { type = "string" } } }
capability = { domain = "notes", operation = "search", provider = "demo", read_only = true, demo = true }
''',
            )

            manifest = load_plugin_manifest(plugin)
            registry = ToolRegistry()
            register_plugin_tools(manifest, registry)
            spec = registry.get("demo.search")

            self.assertEqual(spec.argument_hints, "query is a short search phrase")
            self.assertEqual(spec.input_schema, {
                "type": "object",
                "required": ["query"],
                "properties": {"query": {"type": "string"}},
            })
            self.assertIsNotNone(spec.capability)
            self.assertEqual(spec.capability.domain, "notes")
            self.assertEqual(spec.capability.operation, "search")

    def test_plugin_metadata_validation_is_deterministic(self) -> None:
        cases = [
        ("input_schema", '"bad"', "input_schema must be a table"),
        ("input_schema", '{ type = "array" }', "input_schema.type"),
        ("input_schema", '{ required = [1] }', "input_schema.required"),
        ("capability", '"bad"', "capability must be a table"),
        ("capability", '{ domain = "notes" }', "capability.operation"),
        ("argument_hints", "42", "argument_hints must be a string"),
        ]
        for field, value, message in cases:
            with self.subTest(field=field):
                with tempfile.TemporaryDirectory() as directory:
                    plugin = _write_plugin(
                        Path(directory),
                        f'''
name = "metadata"
description = "Metadata test plugin"

[[tools]]
name = "demo.search"
description = "Search demo records"
handler = "tools.run"
{field} = {value}
''',
                    )
                    with self.assertRaisesRegex(ValueError, message):
                        load_plugin_manifest(plugin)

    def test_legacy_plugin_manifest_keeps_safe_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            plugin = _write_plugin(
                Path(directory),
        '''
name = "legacy"
description = "Legacy plugin"

[[tools]]
name = "demo.run"
description = "Run demo"
handler = "tools.run"
''',
            )

            manifest = load_plugin_manifest(plugin)
            tool = manifest.tools[0]
            self.assertIsNone(tool.argument_hints)
            self.assertIsNone(tool.input_schema)
            self.assertIsNone(tool.capability)


if __name__ == "__main__":
    unittest.main()
