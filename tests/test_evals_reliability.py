from pathlib import Path
import tempfile
import unittest

from jarvis.contracts import ToolSpec
from jarvis.errors import ModelProviderError
from jarvis.evals import EvalCase, EvalSuite, run_eval_suite
from jarvis.models import ModelProvider, ModelRouter
from jarvis.settings import load_settings
from jarvis.tools import ToolRegistry


class FailingProvider(ModelProvider):
    name = "failing"

    def generate(self, request):
        raise ModelProviderError("provider quota exceeded", component=self.name)


def _settings(tmp_path: Path):
    config = tmp_path / "jarvis.toml"
    config.write_text("", encoding="utf-8")
    return load_settings(config)


class EvalReliabilityTests(unittest.TestCase):
    def test_provider_failure_is_infrastructure_and_excluded_from_score(self):
        with tempfile.TemporaryDirectory() as directory:
            tools = ToolRegistry()
            tools.register(
                ToolSpec(
                    name="demo.search",
                    description="Search demo records.",
                    input_schema={"type": "object"},
                ),
                lambda arguments: {"records": []},
            ),
            suite = EvalSuite(
                name="provider reliability",
                description=None,
                cases=(
                    EvalCase(
                        id="provider-down",
                        kind="planner",
                        goal="Search records",
                        expected_tools=("demo.search",),
                    ),
                ),
            )
            report = run_eval_suite(
                suite,
                _settings(Path(directory)),
                "failing",
                "balanced",
                tools=tools,
                models=ModelRouter({"failing": FailingProvider()}, "failing"),
            )
        result = report.results[0]
        self.assertEqual(result.failure_type, "infrastructure")
        self.assertEqual(report.infrastructure_failures, 1)
        self.assertEqual(report.model_failures, 0)

    def test_default_eval_construction_is_hermetic(self):
        suite = EvalSuite(
            name="offline",
            description=None,
            cases=(
                EvalCase(
                    id="fallback",
                    kind="planner",
                    goal="Anything",
                    allow_fallback=True,
                ),
            ),
        )
        with tempfile.TemporaryDirectory() as directory:
            report = run_eval_suite(
                suite,
                _settings(Path(directory)),
                "fake-local",
                "balanced",
            )
        self.assertEqual(report.results[0].failure_type, "none")
        self.assertEqual(report.results[0].source, "fallback")


if __name__ == "__main__":
    unittest.main()
