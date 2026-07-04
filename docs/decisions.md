# Decision Log

This is a lightweight record of implementation decisions. Keep entries short:
what changed, why, and what tradeoff we accepted.

## 0001 - Start With Vertical Slices

JarvisOS will be built through small runnable CLI slices instead of building a
large abstract platform first.

Reason: each slice should prove one piece of the architecture while keeping the
project testable from the terminal.

## 0002 - Keep Defaults Configurable, Not Hardcoded

Default workflows such as meeting prep are reference scenarios, not privileged
core behavior.

Reason: JarvisOS is a personal orchestration runtime where users can bring their
own agents, plugins, MCP servers, tools, models, and workflows.

## 0003 - Use Fake Provider for Tests

`fake-local` remains the default deterministic provider for tests and offline
runtime checks.

Reason: tests should not require local models, API keys, network services, or
paid providers.

## 0004 - Add Ollama Before Cloud APIs

Ollama is the first real model provider.

Reason: it supports local-first iteration with no API-key setup, no token cost,
and a simple local HTTP API.

Tradeoff: local model quality and latency may vary by machine, so the LLM is
currently used only as a provider smoke test and trace input.

## 0005 - CLI Model Selection Before Settings

The first selection mechanism is `jarvis run --model <provider>`.

Reason: it is the smallest way to test provider routing.

Next step: add settings-based defaults so users do not need to pass `--model`
every time.

## 0006 - Provider-Agnostic Model Settings

Model defaults and modes live in a settings layer that names providers with
strings such as `fake-local`, `ollama/llama3.2:3b`, or future cloud provider
names.

Reason: the orchestrator should not care whether the selected model is local or
cloud-backed. Provider-specific auth and transport belong below the model router.

Precedence is:

```text
CLI --model
> settings mode
> settings default
> fake-local fallback
```

## 0007 - Simple Python Documentation Standard

Public modules, classes, functions, and methods should use standard Python
docstrings. Comments should be minimal and explain intent, constraints, or
non-obvious behavior.

Reason: JarvisOS is expected to grow across agents, tools, providers, and
plugins, so future contributors need enough context to understand seams without
reading a large external design document first.

## 0008 - Local Plugin Runtime Before Online Acquisition

The runtime loads plugins from local folders declared in settings. Future online
plugin support should download or sync plugins into local folders first, then use
the same manifest loader.

Reason: loading and executing tools is a runtime concern, while downloading,
trust, version pinning, and updates are acquisition concerns. Keeping those
separate lets local custom plugins and downloaded plugins share one execution
path.

## 0009 - Keep Package Flat Until Responsibility Pressure Appears

The current package keeps one file per early subsystem under `src/jarvis/`, such
as `models.py`, `tools.py`, `plugins.py`, `settings.py`, and `orchestrator.py`.

Reason: the first slices are still small enough that a flat layout is easier to
read and change. Refactor into subpackages when files start mixing multiple
responsibilities or become hard to scan.

Likely future split points:

- `models.py` into `models/base.py`, `models/router.py`, `models/ollama.py`.
- `tools.py` into `tools/registry.py`, `tools/builtins.py`, `tools/executor.py`.
- `plugins.py` into `plugins/manifest.py`, `plugins/loader.py`, and later
  `plugins/acquisition.py`.
- `cli.py` into a `cli/` package once commands grow.

## 0010 - Build Memory Store Before Automatic Memory Writes

JarvisOS now has a local SQLite memory store and manual memory commands.
Automatic memory extraction is suggest-only and does not silently write durable
memory.

Reason: the runtime needs a real memory substrate before smarter memory
behavior, but personal memory should remain conservative until approval and
review flows are stronger.

## 0011 - Persist Run Traces Locally

JarvisOS stores run summaries and ordered trace events in SQLite when tracing is
enabled.

Reason: trace persistence gives the project a durable debugging and benchmarking
substrate. It allows later tools to inspect model choices, tool calls, failures,
latency, and run behavior without requiring the user to save `--json` output
manually.

## 0012 - Validate LLM Plans Before Execution

JarvisOS can ask a non-fake model for a JSON execution plan, but the runtime
validates every step before execution.

Reason: model output is useful for flexible planning, but tool names, agent
permissions, and policy checks must remain deterministic. Invalid plans fall
back to the safe deterministic planner.

## 0013 - Use a Synthesis Agent With Deterministic Fallback

Final responses are produced by a synthesis agent after tool execution. The
agent can use the selected model, but it receives only the validated plan and
confirmed tool results.

Reason: language models are useful for writing a clear final answer, but they
must not decide what happened. Empty, failed, or obviously unsupported synthesis
falls back to deterministic grounded output.

## 0014 - Normalize Provider Errors at Runtime Boundaries

Model provider failures are wrapped in `ModelProviderError` before the runtime
decides whether to fall back or record an error trace.

Reason: MCP servers, APIs, plugins, and model providers will fail in different
ways. A small shared error shape gives traces and user-facing diagnostics a
common language without building a large exception framework too early.

## 0015 - Move Core Agent Prompts Out of Code

Planner and synthesis prompts now live in bundled markdown files, with optional
config override paths under `[prompts]`.

Reason: prompts are part of runtime behavior and should be editable without
modifying Python code. Bundled defaults keep first-run setup simple, while
override paths give users and developers a clean customization seam.

## 0016 - Add a Durable Approval Queue Before Risky Writes

JarvisOS now stores approval records in SQLite. Memory suggestions and
policy-gated tool calls become pending approval items that can be listed,
inspected, approved, or rejected from the CLI.

Reason: approvals need to be deterministic and inspectable before the runtime
can safely support email sends, calendar writes, MCP side effects, or automatic
memory writes. The first slice applies approved `memory.add` items immediately;
approved tool execution is recorded but not auto-resumed yet.

## 0017 - Allow Low-Risk Local Writes Without Approval

JarvisOS now includes `task.create`, a low-risk local write tool backed by a
SQLite task store. It runs automatically because it only writes to local state
and is visible through `jarvis tasks list`.

Reason: not every write should require user approval. The approval queue should
protect risky, external, or sensitive actions while simple local productivity
actions remain pleasant to use.

## 0018 - Keep Deterministic Guardrails Around LLM Orchestration

The local POC should use LLM planning and synthesis when a real model is
selected, while keeping tool execution, policy, approvals, storage, validation,
and fallback behavior deterministic.

Reason: JarvisOS should become a flexible non-hardcoded orchestration runtime,
but reliable state changes and safety decisions need inspectable code paths.
This lets us integrate MCP tools, A2A agents, ADK agents, and external APIs
later without letting model output bypass contracts.

## 0019 - Start Memory Dedupe Deterministically

Approved memory writes now skip obvious normalized duplicates before inserting a
new record.

Reason: duplicate memory is already visible in local testing. A simple
deterministic duplicate check reduces clutter now. More nuanced merge/update
behavior can come later through a memory review layer.

## 0020 - Load MCP Tools Into the Shared Tool Registry

JarvisOS now supports configured MCP stdio servers and registers their exposed
tools as normal `ToolSpec` entries.

Reason: Gmail, Calendar, Spotify, and future integrations should use existing
MCP servers when good ones are available. JarvisOS should provide the client and
policy/trace/orchestration layer instead of rebuilding every provider adapter
by hand. Read-only MCP tools can be low-risk by default, while writes remain
approval-controlled through configuration and policy.
