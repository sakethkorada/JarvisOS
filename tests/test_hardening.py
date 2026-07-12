"""Focused regression tests for reliability hardening seams."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from jarvis.integrations.mcp import _mcp_client
from jarvis.orchestration.orchestrator import _merge_run_status
from jarvis.settings import load_settings
from jarvis.cli import _build_parser


class RunStatusTests(unittest.TestCase):
    def test_pending_approval_is_not_overwritten_by_later_failure(self) -> None:
        self.assertEqual(
            _merge_run_status("pending_approval", "failed"),
            "pending_approval",
        )
        self.assertEqual(
            _merge_run_status("failed", "pending_approval"),
            "pending_approval",
        )

    def test_failure_is_preserved_until_a_pending_approval_exists(self) -> None:
        self.assertEqual(_merge_run_status("completed", "failed"), "failed")
        self.assertEqual(_merge_run_status("failed", "completed"), "failed")


class McpTimeoutSettingsTests(unittest.TestCase):
    def test_mcp_timeout_is_loaded_from_config(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "jarvis.toml"
            config.write_text(
                """
                [[mcp.servers]]
                name = "demo"
                command = "python"
                args = ["server.py"]
                timeout_seconds = 23.5
                """,
                encoding="utf-8",
            )
            settings = load_settings(config)
        self.assertEqual(settings.mcp.servers[0].timeout_seconds, 23.5)

    def test_mcp_client_uses_server_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "jarvis.toml"
            config.write_text(
                """
                [[mcp.servers]]
                name = "demo"
                command = "python"
                args = ["server.py"]
                timeout_seconds = 17.0
                """,
                encoding="utf-8",
            )
            server = load_settings(config).mcp.servers[0]
        client = _mcp_client(server, None, None)
        self.assertEqual(client._timeout_seconds, 17.0)


class EvalCliTests(unittest.TestCase):
    def test_live_integrations_are_explicit(self) -> None:
        args = _build_parser().parse_args(
            [
                "evals",
                "run",
                "suite.json",
                "--allow-live-integrations",
            ]
        )
        self.assertTrue(args.allow_live_integrations)


if __name__ == "__main__":
    unittest.main()
