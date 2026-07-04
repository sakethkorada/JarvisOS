"""Command line interface for the first JarvisOS slice."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from jarvis.agents import default_agent_registry
from jarvis.contracts import ApprovalRecord, MemoryRecord, TaskRecord
from jarvis.models import default_model_router
from jarvis.runtime import (
    create_default_approval_store,
    create_default_orchestrator,
    create_default_task_store,
    create_default_tool_registry,
    create_default_trace_store,
)
from jarvis.settings import load_settings
from jarvis.storage.approvals import apply_approved_record
from jarvis.storage.auth import AuthStore
from jarvis.storage.memory import MemoryStore
from jarvis.storage.traces import TraceSummary


GOOGLE_TOKENINFO_URL = "https://oauth2.googleapis.com/tokeninfo"


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
    tasks_show = tasks_subparsers.add_parser("show", help="Show one task.")
    tasks_show.add_argument("task_id", help="Task id to inspect.")
    tasks_show.add_argument("--config", type=Path, help="Path to config.")
    tasks_complete = tasks_subparsers.add_parser(
        "complete",
        help="Mark a task complete.",
    )
    tasks_complete.add_argument("task_id", help="Task id to complete.")
    tasks_complete.add_argument("--config", type=Path, help="Path to config.")

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

    auth_parser = subparsers.add_parser("auth", help="Manage integration auth.")
    auth_subparsers = auth_parser.add_subparsers(
        dest="auth_command",
        required=True,
    )
    auth_list = auth_subparsers.add_parser("list", help="List stored auth tokens.")
    auth_list.add_argument("--config", type=Path, help="Path to config.")
    auth_set = auth_subparsers.add_parser(
        "set-token",
        help="Store a bearer access token for an OAuth provider.",
    )
    auth_set.add_argument("provider", help="Provider name from config.")
    auth_set.add_argument("access_token", help="Bearer access token.")
    auth_set.add_argument("--refresh-token", help="Optional refresh token.")
    auth_set.add_argument("--expires-at", help="Optional token expiry timestamp.")
    auth_set.add_argument("--config", type=Path, help="Path to config.")
    auth_clear = auth_subparsers.add_parser("clear", help="Clear stored provider auth.")
    auth_clear.add_argument("provider", help="Provider name to clear.")
    auth_clear.add_argument("--config", type=Path, help="Path to config.")
    auth_debug = auth_subparsers.add_parser(
        "debug",
        help="Inspect provider auth metadata without printing tokens.",
    )
    auth_debug.add_argument("provider", help="Provider name from config.")
    auth_debug.add_argument(
        "--json",
        action="store_true",
        help="Print debug metadata as JSON.",
    )
    auth_debug.add_argument("--config", type=Path, help="Path to config.")
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
                _print_task_record(record)
            return
        if args.tasks_command == "show":
            record = task_store.get(args.task_id)
            if record is None:
                parser.error(f"Unknown task id: {args.task_id}")
                return
            _print_task_record(record, verbose=True)
            return
        if args.tasks_command == "complete":
            try:
                record = task_store.complete(args.task_id)
            except KeyError as exc:
                parser.error(str(exc))
                return
            _print_task_record(record)
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

    if args.command == "auth":
        settings = load_settings(args.config)
        auth_store = AuthStore(settings.auth.database_path)
        if args.auth_command == "list":
            for record in auth_store.list_tokens():
                refresh = "yes" if record.refresh_token else "no"
                expires = record.expires_at or "unknown"
                print(
                    f"{record.provider}: token=stored "
                    f"refresh_token={refresh} expires_at={expires}"
                )
            return
        if args.auth_command == "set-token":
            record = auth_store.set_token(
                args.provider,
                args.access_token,
                refresh_token=args.refresh_token,
                expires_at=args.expires_at,
            )
            print(f"{record.provider}: token=stored updated_at={record.updated_at}")
            return
        if args.auth_command == "clear":
            deleted = auth_store.clear_token(args.provider)
            status = "cleared" if deleted else "not found"
            print(f"{args.provider}: {status}")
            return
        if args.auth_command == "debug":
            debug = _auth_debug(settings, auth_store, args.provider)
            if args.json:
                print(json.dumps(debug, default=_json_default, indent=2))
            else:
                _print_auth_debug(debug)
            return

    parser.error(f"Unknown command: {args.command}")


def _print_memory_record(record: MemoryRecord) -> None:
    """Print one memory record in a compact CLI format."""
    print(f"{record.id} [{record.type}] {record.content}")
    print(f"  source={record.source} updated_at={record.updated_at}")


def _print_task_record(record: TaskRecord, verbose: bool = False) -> None:
    """Print one task record in a compact CLI format."""
    print(f"{record.id} [{record.status}] {record.title}")
    print(f"  source={record.source} updated_at={record.updated_at}")
    if verbose and record.metadata:
        print("  metadata:")
        for key, value in record.metadata.items():
            print(f"    {key}={value}")


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


def _auth_debug(
    settings: Any,
    auth_store: AuthStore,
    provider_name: str,
) -> dict[str, Any]:
    """Return redacted OAuth debug metadata for one provider."""
    provider = _oauth_provider(settings, provider_name)
    record = auth_store.get_token(provider_name)
    debug: dict[str, Any] = {
        "provider": provider_name,
        "auth_database_path": settings.auth.database_path,
        "provider_configured": provider is not None,
        "token_stored": record is not None,
        "refresh_token_stored": bool(record and record.refresh_token),
        "expires_at": record.expires_at if record else None,
        "token_expired": auth_store.token_is_expired(record) if record else None,
        "configured_scopes": list(provider.scopes) if provider else [],
        "tokeninfo": None,
    }
    if provider is None or record is None:
        return debug

    tokeninfo_url = _tokeninfo_url(provider)
    debug["tokeninfo_url_configured"] = tokeninfo_url is not None
    if tokeninfo_url is None:
        return debug

    tokeninfo = _fetch_tokeninfo(tokeninfo_url, record.access_token)
    debug["tokeninfo"] = tokeninfo
    granted_scopes = _scope_set(tokeninfo.get("scope"))
    configured_scopes = set(provider.scopes)
    if granted_scopes:
        debug["granted_scopes"] = sorted(granted_scopes)
        debug["missing_configured_scopes"] = sorted(configured_scopes - granted_scopes)
        debug["extra_granted_scopes"] = sorted(granted_scopes - configured_scopes)
    if provider.client_id:
        audience = tokeninfo.get("aud")
        authorized_party = tokeninfo.get("azp")
        debug["client_id_matches_audience"] = provider.client_id in {
            audience,
            authorized_party,
        }
    return debug


def _oauth_provider(settings: Any, provider_name: str) -> Any | None:
    for provider in settings.auth.oauth_providers:
        if provider.name == provider_name:
            return provider
    return None


def _tokeninfo_url(provider: Any) -> str | None:
    if provider.tokeninfo_url:
        return provider.tokeninfo_url
    if provider.name == "google":
        return GOOGLE_TOKENINFO_URL
    return None


def _fetch_tokeninfo(tokeninfo_url: str, access_token: str) -> dict[str, Any]:
    """Call a provider token-info endpoint without exposing token material."""
    url = f"{tokeninfo_url}?{urlencode({'access_token': access_token})}"
    request = Request(url, headers={"Accept": "application/json"})
    try:
        with urlopen(request, timeout=15) as response:
            payload = response.read().decode("utf-8")
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace").strip()
        return {
            "ok": False,
            "status": exc.code,
            "error": _safe_http_detail(detail),
        }
    except URLError as exc:
        return {"ok": False, "error": f"Network error: {exc.reason}"}
    data = json.loads(payload)
    if not isinstance(data, dict):
        return {"ok": False, "error": "Token info response was not an object."}
    return _redacted_tokeninfo(data)


def _redacted_tokeninfo(data: dict[str, Any]) -> dict[str, Any]:
    redacted: dict[str, Any] = {"ok": True}
    safe_keys = (
        "aud",
        "azp",
        "scope",
        "expires_in",
        "access_type",
        "token_type",
        "issued_to",
    )
    for key in safe_keys:
        if key in data:
            redacted[key] = data[key]
    if "email" in data:
        redacted["email_present"] = True
    if "sub" in data:
        redacted["subject_present"] = True
    return redacted


def _safe_http_detail(detail: str) -> str:
    if not detail:
        return "Token info request failed."
    try:
        data = json.loads(detail)
    except json.JSONDecodeError:
        return detail[:500]
    if not isinstance(data, dict):
        return detail[:500]
    safe = {
        key: data[key]
        for key in ("error", "error_description")
        if key in data
    }
    return json.dumps(safe or {"error": "Token info request failed."}, sort_keys=True)


def _scope_set(scope_value: Any) -> set[str]:
    if not isinstance(scope_value, str):
        return set()
    return {scope for scope in scope_value.split() if scope}


def _print_auth_debug(debug: dict[str, Any]) -> None:
    """Print redacted OAuth debug information."""
    print(f"Provider: {debug['provider']}")
    print(f"Configured: {_yes_no(debug['provider_configured'])}")
    print(f"Auth database: {debug['auth_database_path']}")
    print(f"Token stored: {_yes_no(debug['token_stored'])}")
    print(f"Refresh token stored: {_yes_no(debug['refresh_token_stored'])}")
    print(f"Expires at: {debug['expires_at'] or 'unknown'}")
    expired = debug["token_expired"]
    print(f"Token expired: {'unknown' if expired is None else _yes_no(expired)}")

    configured_scopes = debug.get("configured_scopes", [])
    if configured_scopes:
        print("Configured scopes:")
        for scope in configured_scopes:
            print(f"- {scope}")

    tokeninfo = debug.get("tokeninfo")
    if tokeninfo is None:
        print("Token info: unavailable")
        return
    if not tokeninfo.get("ok"):
        print(f"Token info error: {tokeninfo.get('error', 'unknown error')}")
        return
    print("Token info: ok")
    for key in ("aud", "azp", "expires_in", "access_type", "token_type"):
        if key in tokeninfo:
            print(f"{key}: {tokeninfo[key]}")
    if "client_id_matches_audience" in debug:
        print(f"Client id matches token: {_yes_no(debug['client_id_matches_audience'])}")
    granted_scopes = debug.get("granted_scopes", [])
    if granted_scopes:
        print("Granted scopes:")
        for scope in granted_scopes:
            print(f"- {scope}")
    missing = debug.get("missing_configured_scopes", [])
    if missing:
        print("Missing configured scopes:")
        for scope in missing:
            print(f"- {scope}")


def _yes_no(value: bool) -> str:
    return "yes" if value else "no"
