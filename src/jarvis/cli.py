"""Command line interface for the first JarvisOS slice."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

from jarvis.approvals import apply_approved_record
from jarvis.agents import default_agent_registry
from jarvis.contracts import ApprovalRecord, MemoryRecord
from jarvis.memory import MemoryStore
from jarvis.models import default_model_router
from jarvis.runtime import (
    create_default_approval_store,
    create_default_orchestrator,
    create_default_task_store,
    create_default_tool_registry,
    create_default_trace_store,
)
from jarvis.settings import load_settings
from jarvis.traces import TraceSummary


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
        help=(
            "Model routing mode to resolve from settings, such as balanced "
            "or private."
        ),
    )
    run_parser.add_argument(
        "--config",
        type=Path,
        help="Path to a JarvisOS TOML config file.",
    )

    subparsers.add_parser("agents", help="List available agents.")
    tools_parser = subparsers.add_parser("tools", help="List available tools.")
    tools_parser.add_argument(
        "--config",
        type=Path,
        help="Path to a JarvisOS TOML config file.",
    )
    subparsers.add_parser("models", help="List available model providers.")
    settings_parser = subparsers.add_parser("settings", help="Show resolved settings.")
    settings_parser.add_argument(
        "--config",
        type=Path,
        help="Path to a JarvisOS TOML config file.",
    )
    memory_parser = subparsers.add_parser("memory", help="Manage local memory.")
    memory_subparsers = memory_parser.add_subparsers(
        dest="memory_command",
        required=True,
    )
    memory_add = memory_subparsers.add_parser("add", help="Add a memory record.")
    memory_add.add_argument("content", help="Memory content to store.")
    memory_add.add_argument(
        "--type",
        default="note",
        choices=("preference", "fact", "note", "context"),
        help="Memory type.",
    )
    memory_add.add_argument("--source", default="manual", help="Memory source.")
    memory_add.add_argument("--config", type=Path, help="Path to config.")

    memory_search = memory_subparsers.add_parser(
        "search",
        help="Search local memory records.",
    )
    memory_search.add_argument("query", help="Search query.")
    memory_search.add_argument("--limit", type=int, default=5, help="Result limit.")
    memory_search.add_argument("--config", type=Path, help="Path to config.")

    memory_list = memory_subparsers.add_parser("list", help="List recent memories.")
    memory_list.add_argument("--limit", type=int, default=20, help="Result limit.")
    memory_list.add_argument("--config", type=Path, help="Path to config.")

    tasks_parser = subparsers.add_parser("tasks", help="Manage local tasks.")
    tasks_subparsers = tasks_parser.add_subparsers(
        dest="tasks_command",
        required=True,
    )
    tasks_list = tasks_subparsers.add_parser("list", help="List recent tasks.")
    tasks_list.add_argument("--limit", type=int, default=20, help="Result limit.")
    tasks_list.add_argument("--config", type=Path, help="Path to config.")

    traces_parser = subparsers.add_parser("traces", help="Inspect stored traces.")
    traces_subparsers = traces_parser.add_subparsers(
        dest="traces_command",
        required=True,
    )
    traces_list = traces_subparsers.add_parser("list", help="List recent runs.")
    traces_list.add_argument("--limit", type=int, default=20, help="Result limit.")
    traces_list.add_argument("--config", type=Path, help="Path to config.")

    traces_show = traces_subparsers.add_parser("show", help="Show a stored run.")
    traces_show.add_argument("run_id", help="Run id to inspect.")
    traces_show.add_argument("--json", action="store_true", help="Print JSON.")
    traces_show.add_argument("--config", type=Path, help="Path to config.")

    approvals_parser = subparsers.add_parser(
        "approvals",
        help="Inspect and decide pending approvals.",
    )
    approvals_subparsers = approvals_parser.add_subparsers(
        dest="approvals_command",
        required=True,
    )
    approvals_list = approvals_subparsers.add_parser(
        "list",
        help="List approval records.",
    )
    approvals_list.add_argument(
        "--status",
        choices=("pending", "approved", "rejected", "all"),
        default="pending",
        help="Approval status to list.",
    )
    approvals_list.add_argument("--limit", type=int, default=20, help="Result limit.")
    approvals_list.add_argument("--config", type=Path, help="Path to config.")

    approvals_show = approvals_subparsers.add_parser(
        "show",
        help="Show one approval record.",
    )
    approvals_show.add_argument("approval_id", help="Approval id to inspect.")
    approvals_show.add_argument("--json", action="store_true", help="Print JSON.")
    approvals_show.add_argument("--config", type=Path, help="Path to config.")

    approvals_approve = approvals_subparsers.add_parser(
        "approve",
        help="Approve an item.",
    )
    approvals_approve.add_argument("approval_id", help="Approval id to approve.")
    approvals_approve.add_argument("--config", type=Path, help="Path to config.")

    approvals_reject = approvals_subparsers.add_parser(
        "reject",
        help="Reject an item.",
    )
    approvals_reject.add_argument("approval_id", help="Approval id to reject.")
    approvals_reject.add_argument("--config", type=Path, help="Path to config.")
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
        if settings.traces.enabled:
            create_default_trace_store(settings).save_run(result)
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
        settings = load_settings(args.config)
        for tool in create_default_tool_registry(settings).list():
            approval = "approval required" if tool.requires_approval else "auto"
            print(
                f"{tool.name}: {tool.description} "
                f"[{tool.risk_level}, {approval}, {tool.source}]"
            )
        return

    if args.command == "models":
        for model_name in default_model_router(load_settings()).list():
            print(model_name)
        return

    if args.command == "settings":
        settings = load_settings(args.config)
        print(json.dumps(settings, default=_json_default, indent=2))
        return

    if args.command == "memory":
        settings = load_settings(args.config)
        memory_store = MemoryStore(settings.memory.database_path)
        if args.memory_command == "add":
            record = memory_store.add(
                args.content,
                memory_type=args.type,
                source=args.source,
            )
            _print_memory_record(record)
            return
        if args.memory_command == "search":
            for record in memory_store.search(args.query, limit=args.limit):
                _print_memory_record(record)
            return
        if args.memory_command == "list":
            for record in memory_store.list(limit=args.limit):
                _print_memory_record(record)
            return

    if args.command == "tasks":
        settings = load_settings(args.config)
        task_store = create_default_task_store(settings)
        if args.tasks_command == "list":
            for record in task_store.list(limit=args.limit):
                print(f"{record.id} [{record.status}] {record.title}")
                print(f"  source={record.source} updated_at={record.updated_at}")
            return

    if args.command == "traces":
        settings = load_settings(args.config)
        trace_store = create_default_trace_store(settings)
        if args.traces_command == "list":
            for summary in trace_store.list_runs(limit=args.limit):
                _print_trace_summary(summary)
            return
        if args.traces_command == "show":
            stored_trace = trace_store.get_run(args.run_id)
            if stored_trace is None:
                parser.error(f"Unknown run id: {args.run_id}")
                return
            if args.json:
                print(json.dumps(stored_trace, default=_json_default, indent=2))
            else:
                _print_stored_trace(stored_trace)
            return

    if args.command == "approvals":
        settings = load_settings(args.config)
        approval_store = create_default_approval_store(settings)
        if args.approvals_command == "list":
            status = None if args.status == "all" else args.status
            for record in approval_store.list(status=status, limit=args.limit):
                _print_approval_record(record)
            return
        if args.approvals_command == "show":
            record = approval_store.get(args.approval_id)
            if record is None:
                parser.error(f"Unknown approval id: {args.approval_id}")
                return
            if args.json:
                print(json.dumps(record, default=_json_default, indent=2))
            else:
                _print_approval_record(record, verbose=True)
            return
        if args.approvals_command == "approve":
            try:
                record = approval_store.decide(args.approval_id, "approved")
            except (KeyError, ValueError) as exc:
                parser.error(str(exc))
                return
            memory_store = MemoryStore(settings.memory.database_path)
            effect = apply_approved_record(record, memory_store)
            _print_approval_record(record)
            print(f"  effect={effect}")
            return
        if args.approvals_command == "reject":
            try:
                record = approval_store.decide(args.approval_id, "rejected")
            except (KeyError, ValueError) as exc:
                parser.error(str(exc))
                return
            _print_approval_record(record)
            return

    parser.error(f"Unknown command: {args.command}")


def _print_memory_record(record: MemoryRecord) -> None:
    """Print one memory record in a compact CLI format."""
    print(f"{record.id} [{record.type}] {record.content}")
    print(f"  source={record.source} updated_at={record.updated_at}")


def _print_approval_record(
    record: ApprovalRecord,
    verbose: bool = False,
) -> None:
    """Print one approval record in a compact CLI format."""
    run_id = record.run_id or "none"
    print(f"{record.id} [{record.status}] {record.type}: {record.title}")
    print(f"  reason={record.reason}")
    print(f"  run_id={run_id} created_at={record.created_at}")
    if record.decided_at:
        print(f"  decided_at={record.decided_at}")
    if verbose:
        print("  payload:")
        for key, value in record.payload.items():
            print(f"    {key}={value}")


def _print_trace_summary(summary: TraceSummary) -> None:
    """Print one trace summary in a compact CLI format."""
    model = summary.selected_model or "unknown-model"
    print(f"{summary.run_id} [{summary.status}] {summary.goal}")
    print(f"  model={model} started_at={summary.started_at}")


def _print_stored_trace(stored_trace: Any) -> None:
    """Print a stored trace timeline."""
    summary = stored_trace.summary
    print(f"Run: {summary.run_id}")
    print(f"Goal: {summary.goal}")
    print(f"Status: {summary.status}")
    print(f"Model: {summary.selected_model or 'unknown-model'}")
    print("")
    print("Events:")
    for event in stored_trace.events:
        print(f"- [{event.event_type}] {event.message}")
    print("")
    print("Final response:")
    print(stored_trace.final_response)
