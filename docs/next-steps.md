# Next Steps

## Current foundation

JarvisOS now has a validated execution graph, SQLite checkpoints, and explicit
restart-safe continuation. A plan can declare `depends_on` step ids and an
optional `output_key`; the runtime executes the graph in deterministic
topological order and can resolve `$step.<id>.<path>` references, including
structured values such as `$step.find.records[0].id`.

`jarvis runs resume <run_id>` reconstructs the latest checkpoint. It skips all
previously attempted steps and executes only unattempted nodes whose dependencies
succeeded. `--dry-run` previews replay-protected, eligible, and blocked nodes.
The graph is still sequential.

## Recommended next slice: idempotent retry and approval continuation

Build the safety needed to retry or continue externally visible work:

1. Let tool metadata declare an idempotency-key argument where a provider
   supports it.
2. Require that key before a failed or approval-blocked external node can be
   retried.
3. Add an explicit approval continuation path that consumes an approved action
   without replaying unrelated completed steps.
4. Add crash/restart tests around a side-effectful fixture tool and an approval
   fixture tool.

Do not add broad replanning or distributed workers until this lifecycle is
deterministic on one machine.

## Following slice: bounded concurrency

After resume is reliable, allow independent graph nodes to run concurrently
behind explicit limits:

- maximum active nodes per run;
- per-node timeout and cancellation;
- model/tool/provider budgets;
- deterministic handling of partial failure;
- approval gates that pause only affected nodes;
- trace events that preserve causal ordering.

The existing `ExecutionGraph.ready_steps()` method is the intended seam for
this change.

## Integration expansion after lifecycle hardening

Once restart and concurrency semantics are tested, add integrations through the
existing contracts:

- capability manifests for tools and agents;
- `ToolSpec` schemas, hints, risk, capability, and normalized outputs;
- `ModelProvider` adapters and role-based model routing;
- MCP stdio/HTTP adapters with configurable deadlines;
- scenario evals using injected fake tools/models by default.

Provider-specific routing branches should not be added to the planner or
orchestrator. Improve adapter metadata, schemas, prompts, and eval fixtures.

## Validation commands

```powershell
$env:PYTHONPATH = "src"
python -m unittest discover -s tests -q
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\ruff.exe check src tests
python -m jarvis run "What is the current date?" --config jarvis.toml.example --model "ollama/llama3.2:3b" --json
```

Cloud-model evals are opt-in and should classify quota, auth, timeout, and
network failures as infrastructure results:

```powershell
python -m jarvis evals run examples/evals/planner-coverage.json `
  --config jarvis.toml --model "gemini/gemini-3.5-flash" `
  --allow-live-integrations --include-raw --json
```
