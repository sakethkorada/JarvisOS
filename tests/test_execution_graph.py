"""Tests for dependency validation and durable run checkpoints."""

from __future__ import annotations

import tempfile
import unittest
import json
from pathlib import Path

from jarvis.contracts import (
    ExecutionPlan,
    PlanStep,
    ToolCall,
    ToolResult,
    ToolSpec,
    TraceEvent,
)
from jarvis.cli import _build_parser
from jarvis.orchestration.orchestrator import Orchestrator
from jarvis.policies import PolicyEngine
from jarvis.prompts import PromptLibrary
from jarvis.orchestration.arguments import resolve_tool_arguments
from jarvis.orchestration.graph import ExecutionGraph, GraphValidationError
from jarvis.runtime import create_default_orchestrator
from jarvis.settings import load_settings
from jarvis.storage.journal import RunJournal
from jarvis.agents import default_agent_registry
from jarvis.models import FakeModelProvider, ModelRouter
from jarvis.orchestration.planner import Planner
from jarvis.tools import ToolRegistry, default_tool_registry


def _step(step_id: str, depends_on: tuple[str, ...] = (), output_key=None) -> PlanStep:
    return PlanStep(
        id=step_id,
        agent_name="orchestrator",
        tool_call=ToolCall("task.create_summary", {"goal": step_id}),
        description=step_id,
        depends_on=depends_on,
        output_key=output_key,
    )


class ExecutionGraphTests(unittest.TestCase):
    def test_topological_order_is_deterministic(self) -> None:
        plan = ExecutionPlan(
            goal="graph",
            steps=(_step("second", ("first",)), _step("first"), _step("third")),
        )
        graph = ExecutionGraph(plan)
        self.assertEqual(
            [step.id for step in graph.topological_order()],
            ["first", "second", "third"],
        )
        self.assertEqual(
            [step.id for step in graph.ready_steps({"first"})],
            ["second", "third"],
        )

    def test_invalid_dependencies_and_cycles_fail_before_execution(self) -> None:
        with self.assertRaises(GraphValidationError):
            ExecutionGraph(ExecutionPlan("unknown", (_step("a", ("missing",)),)))
        with self.assertRaises(GraphValidationError):
            ExecutionGraph(ExecutionPlan("cycle", (_step("a", ("b",)), _step("b", ("a",)))))
        with self.assertRaises(GraphValidationError):
            ExecutionGraph(
                ExecutionPlan("duplicate", (_step("a", output_key="result"), _step("b", output_key="result")))
            )

    def test_named_step_reference_resolves_from_successful_output(self) -> None:
        tool = ToolSpec(
            name="demo.consume",
            description="Consume an id.",
            input_schema={
                "type": "object",
                "properties": {"item_id": {"type": "string"}},
                "required": ["item_id"],
            },
        )
        resolution = resolve_tool_arguments(
            "consume",
            tool,
            {"item_id": "$step.lookup.item_id"},
            named_results={
                "lookup": ToolResult(
                    "demo.lookup",
                    {"item_id": "item-123", "text": "found"},
                )
            },
        )
        self.assertEqual(resolution.arguments, {"item_id": "item-123"})

    def test_planner_rejects_unknown_graph_reference_without_raising(self) -> None:
        planner = Planner(
            default_agent_registry(),
            default_tool_registry(),
            ModelRouter({"fake-local": FakeModelProvider()}),
            "planner",
        )
        plan, error = planner._plan_from_model_output(
            "graph",
            json.dumps(
                {
                    "steps": [
                        {
                            "step_id": "summary",
                            "tool_name": "task.create_summary",
                            "arguments": {"goal": "graph"},
                            "description": "Summarize",
                            "depends_on": ["current_time"],
                        }
                    ]
                }
            ),
        )
        self.assertIsNone(plan)
        self.assertIn("unknown step", error or "")


class RunJournalTests(unittest.TestCase):
    def test_latest_checkpoint_reconstructs_plan_progress(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            journal = RunJournal(Path(directory) / "state.sqlite3")
            plan = ExecutionPlan("checkpoint", (_step("a", output_key="answer"),))
            journal.checkpoint(
                "run_1",
                "checkpoint",
                plan,
                (plan.steps[0],),
                (),
                "completed",
                (),
            )
            latest = journal.latest("run_1")
        self.assertIsNotNone(latest)
        self.assertEqual(latest.checkpoint_index, 0)
        self.assertEqual(latest.payload["plan"]["steps"][0]["output_key"], "answer")
        self.assertEqual(latest.payload["completed_steps"][0]["id"], "a")

    def test_orchestrator_writes_checkpoints_when_configured(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "jarvis.toml"
            config.write_text(
                """
                [memory]
                enabled = false
                [traces]
                enabled = false
                """,
                encoding="utf-8",
            )
            settings = load_settings(config)
            journal = RunJournal(root / "journal.sqlite3")
            result = create_default_orchestrator(settings, journal=journal).run(
                "Return a short summary",
                model_name="fake-local",
            )
            latest = journal.latest(result.run_id)
        self.assertIsNotNone(latest)
        self.assertEqual(latest.status, "completed")
        self.assertGreaterEqual(latest.payload["trace_length"], 1)
        self.assertTrue(any(event.event_type == "graph.validated" for event in result.trace))

    def test_resume_skips_completed_step_and_uses_its_record(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            journal = RunJournal(Path(directory) / "state.sqlite3")
            plan = ExecutionPlan(
                "Continue a record lookup.",
                (
                    PlanStep(
                        id="find",
                        agent_name="plugin",
                        tool_call=ToolCall("demo.find", {"query": "release"}),
                        description="Find a record.",
                    ),
                    PlanStep(
                        id="detail",
                        agent_name="plugin",
                        tool_call=ToolCall(
                            "demo.detail",
                            {"record_id": "$step.find.records[0].id"},
                        ),
                        description="Read the record.",
                        depends_on=("find",),
                    ),
                ),
            )
            first = plan.steps[0]
            journal.checkpoint(
                "run_resume",
                plan.goal,
                plan,
                (first,),
                (
                    ToolResult(
                        "demo.find",
                        {"records": [{"id": "record-7", "title": "Release"}]},
                    ),
                ),
                "planned",
                (TraceEvent("run.started", "Run started."),),
            )
            calls: list[tuple[str, dict[str, object]]] = []
            tools = ToolRegistry()
            tools.register(
                ToolSpec(name="demo.find", description="Find a record."),
                lambda arguments: calls.append(("find", dict(arguments))) or {},
            )
            tools.register(
                ToolSpec(
                    name="demo.detail",
                    description="Read one record.",
                    input_schema={
                        "type": "object",
                        "properties": {"record_id": {"type": "string"}},
                        "required": ["record_id"],
                    },
                ),
                lambda arguments: calls.append(("detail", dict(arguments)))
                or {"records": [{"id": arguments["record_id"], "title": "Detail"}]},
            )
            orchestrator = Orchestrator(
                agents=default_agent_registry(),
                tools=tools,
                models=ModelRouter({"fake-local": FakeModelProvider()}),
                policies=PolicyEngine(),
                planner_prompt=PromptLibrary().planner_prompt(),
                synthesis_prompt=PromptLibrary().synthesis_prompt(),
                journal=journal,
            )

            result = orchestrator.resume("run_resume", model_name="fake-local")

        self.assertEqual(calls, [("detail", {"record_id": "record-7"})])
        self.assertEqual(result.run_id, "run_resume")
        self.assertEqual(result.status, "completed")
        self.assertEqual([step.status for step in result.plan.steps], ["completed", "completed"])
        self.assertIn("run.resumed", [event.event_type for event in result.trace])
        self.assertIn("resume.step_skipped", [event.event_type for event in result.trace])

    def test_resume_never_replays_attempted_or_approval_blocked_steps(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            journal = RunJournal(Path(directory) / "state.sqlite3")
            plan = ExecutionPlan(
                "Resume safely.",
                (
                    PlanStep(
                        id="external",
                        agent_name="plugin",
                        tool_call=ToolCall("demo.external", {"value": "done"}),
                        description="External action.",
                    ),
                    PlanStep(
                        id="dependent",
                        agent_name="plugin",
                        tool_call=ToolCall("demo.dependent", {}),
                        description="Dependent action.",
                        depends_on=("external",),
                    ),
                ),
            )
            journal.checkpoint(
                "run_blocked",
                plan.goal,
                plan,
                (plan.steps[0],),
                (
                    ToolResult(
                        "demo.external",
                        {"approval_id": "approval_1"},
                        success=False,
                        error="Approval required.",
                    ),
                ),
                "pending_approval",
                (),
            )
            calls: list[str] = []
            tools = ToolRegistry()
            tools.register(
                ToolSpec(name="demo.external", description="External action."),
                lambda arguments: calls.append("external") or {},
            )
            tools.register(
                ToolSpec(name="demo.dependent", description="Dependent action."),
                lambda arguments: calls.append("dependent") or {},
            )
            orchestrator = Orchestrator(
                agents=default_agent_registry(),
                tools=tools,
                models=ModelRouter({"fake-local": FakeModelProvider()}),
                policies=PolicyEngine(),
                planner_prompt=PromptLibrary().planner_prompt(),
                synthesis_prompt=PromptLibrary().synthesis_prompt(),
                journal=journal,
            )

            preview = orchestrator.resume_preview("run_blocked")
            with self.assertRaisesRegex(ValueError, "no eligible"):
                orchestrator.resume("run_blocked", model_name="fake-local")

        self.assertEqual(calls, [])
        self.assertEqual([step.id for step in preview.replay_protected_steps], ["external"])
        self.assertEqual([step.id for step in preview.blocked_steps], ["dependent"])

    def test_resume_treats_in_flight_step_as_replay_protected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            journal = RunJournal(Path(directory) / "state.sqlite3")
            plan = ExecutionPlan(
                "Protect an interrupted action.",
                (
                    PlanStep(
                        id="external",
                        agent_name="plugin",
                        tool_call=ToolCall("demo.external", {"value": "once"}),
                        description="External action.",
                    ),
                    PlanStep(
                        id="dependent",
                        agent_name="plugin",
                        tool_call=ToolCall("demo.dependent", {}),
                        description="Dependent action.",
                        depends_on=("external",),
                    ),
                ),
            )
            journal.checkpoint(
                "run_in_flight",
                plan.goal,
                plan,
                (),
                (),
                "planned",
                (),
                in_flight_step=plan.steps[0],
            )
            state = journal.reconstruct("run_in_flight")

        assert state is not None
        from jarvis.orchestration.resume import preview_resume

        preview = preview_resume(state)
        self.assertEqual(state.in_flight_step.id, "external")
        self.assertEqual([step.id for step in preview.replay_protected_steps], ["external"])
        self.assertEqual([step.id for step in preview.blocked_steps], ["dependent"])
        self.assertEqual(preview.eligible_steps, ())

    def test_runs_resume_dry_run_cli_arguments(self) -> None:
        args = _build_parser().parse_args(
            ["runs", "resume", "run_123", "--dry-run", "--json"]
        )
        self.assertEqual(args.run_id, "run_123")
        self.assertTrue(args.dry_run)
        self.assertTrue(args.json)


if __name__ == "__main__":
    unittest.main()
