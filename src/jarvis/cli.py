"""Command line interface for the first JarvisOS slice."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

from jarvis.agents import default_agent_registry
from jarvis.models import default_model_router
from jarvis.runtime import create_default_orchestrator
from jarvis.settings import load_settings
from jarvis.tools import default_tool_registry


def _json_default(value: Any) -> Any:
    """Serialize dataclasses and paths for CLI JSON output."""
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def _build_parser() -> argparse.ArgumentParser:
    """Build the command-line parser."""
    parser = argparse.ArgumentParser(
        prog="jarvis",
        description="Terminal-first personal agent orchestration runtime.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="Run a goal through JarvisOS.")
    run_parser.add_argument("goal", help="Natural-language goal to run.")
    run_parser.add_argument(
        "--json",
        action="store_true",
        help="Print the full structured run result as JSON.",
    )
    run_parser.add_argument(
        "--model",
        help="Model provider to use, such as fake-local or ollama/llama3.2:3b.",
    )
    run_parser.add_argument(
        "--mode",
        default="balanced",
        help="Model routing mode to resolve from settings, such as balanced or private.",
    )
    run_parser.add_argument(
        "--config",
        type=Path,
        help="Path to a JarvisOS TOML config file.",
    )

    subparsers.add_parser("agents", help="List available agents.")
    subparsers.add_parser("tools", help="List available tools.")
    subparsers.add_parser("models", help="List available model providers.")
    settings_parser = subparsers.add_parser("settings", help="Show resolved settings.")
    settings_parser.add_argument(
        "--config",
        type=Path,
        help="Path to a JarvisOS TOML config file.",
    )
    return parser


def main() -> None:
    """Run the JarvisOS command-line interface."""
    parser = _build_parser()
    args = parser.parse_args()

    if args.command == "run":
        settings = load_settings(args.config)
        model_name = settings.resolve_model(args.model, mode=args.mode)
        try:
            result = create_default_orchestrator(settings).run(
                args.goal,
                model_name=model_name,
                model_mode=args.mode,
            )
        except KeyError as exc:
            parser.error(str(exc))
            return
        if args.json:
            print(json.dumps(result, default=_json_default, indent=2))
        else:
            print(result.final_response)
        return

    if args.command == "agents":
        for agent in default_agent_registry().list():
            print(f"{agent.name}: {agent.description}")
        return

    if args.command == "tools":
        for tool in default_tool_registry().list():
            approval = "approval required" if tool.requires_approval else "auto"
            print(f"{tool.name}: {tool.description} [{tool.risk_level}, {approval}]")
        return

    if args.command == "models":
        for model_name in default_model_router(load_settings()).list():
            print(model_name)
        return

    if args.command == "settings":
        settings = load_settings(args.config)
        print(json.dumps(settings, default=_json_default, indent=2))
        return

    parser.error(f"Unknown command: {args.command}")
