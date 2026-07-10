# Architecture Map

JarvisOS is organized around a small terminal-first runtime spine. New work
should fit one of these boundaries before adding another top-level module.

## Runtime Flow

```text
CLI
  -> runtime factory
  -> orchestration planner
  -> deterministic validation
  -> policy and approval checks
  -> tool execution
  -> synthesis
  -> traces and final response
```

## Package Boundaries

- `jarvis.cli` parses terminal commands and renders output.
- `jarvis.runtime` wires settings, stores, registries, providers, integrations,
  and the orchestrator.
- `jarvis.contracts` contains shared runtime data contracts.
- `jarvis.settings` loads TOML and environment configuration.
- `jarvis.errors` normalizes runtime boundary failures.
- `jarvis.policies` makes deterministic approval decisions.
- `jarvis.agents` defines available agent routing specs.
- `jarvis.prompts` loads bundled and configured prompt files.
- `jarvis.orchestration` owns planning, step execution, argument references,
  agent runtime wrappers, and final synthesis.
- `jarvis.tools` owns the tool registry and built-in local tools.
- `jarvis.models` owns model providers and routing.
- `jarvis.integrations` owns external adapters such as MCP, OAuth, and local
  plugins.
- `jarvis.evals` owns isolated planner and ToolUseAgent evaluation harnesses.
- `jarvis.storage` owns SQLite-backed memory, tasks, traces, approvals, and
  integration auth tokens.

## Import Rule

Runtime code should import from the package that owns the behavior, for example:

```python
from jarvis.orchestration.orchestrator import Orchestrator
from jarvis.storage.memory import MemoryStore
from jarvis.integrations.mcp import load_mcp_tools
```

Avoid reintroducing top-level compatibility wrappers such as
`jarvis.orchestrator` or `jarvis.memory`. Top-level modules should be reserved
for entrypoints, contracts, config, shared errors, and cross-cutting runtime
glue.

## Next Substrate Seams

HTTP MCP support lives under `jarvis.integrations`, next to the stdio MCP
client. OAuth authorization-code flow lives under `jarvis.integrations.oauth`.
OAuth provider configuration and token persistence are split between
`jarvis.settings` and `jarvis.storage`, with no secrets written to traces or
model prompts. Future provider auth work should extend this substrate instead
of adding provider-specific auth branches to orchestration.

Auth uses a global profile substrate. Per-run TOML config decides which models,
MCP servers, plugins, tools, prompts, and policies are enabled for a run.
Provider credentials, OAuth provider metadata, token storage, refresh behavior,
and environment-secret resolution come from the first available auth profile:
`JARVIS_AUTH_PROFILE`, `.jarvis/auth.toml`, `config/auth.toml`, `jarvis.toml`,
or `config/jarvis.toml`. Explicit `[auth]` values in a run config override the
global profile. If a configured tool requires auth that is missing or expired,
the integration layer should raise a structured runtime error with the missing
provider/env detail.

Cloud model APIs should extend `jarvis.models` through provider classes and the
existing router. Specialist sub-agents should extend `jarvis.agents`,
`jarvis.prompts`, and `jarvis.orchestration` without bypassing tool validation,
policy checks, approvals, or trace recording.

Agent model calls use a three-part substrate:

```text
AgentProfile
  -> execution_role
  -> ModelRouter role/mode/provider resolution
```

`AgentSpec` describes who is acting and what constraints apply: name,
description, allowed tools, execution role, prompt reference, output contract,
memory scope, and risk permissions. `AgentRuntime` is the narrow wrapper that
turns an agent profile plus a `ModelRequest` into a provider call. It resolves
the execution role through the model router, calls the selected local or cloud
provider, and returns normalized model output. It does not own planning,
policy, tool execution, or synthesis decisions.

`[models.roles]` is a routing policy, not the agent abstraction. Built-in roles
include `planner`, `tool_use`, `synthesis`, and `general`. User-defined agents
can reuse those roles or introduce new ones later. Concrete providers such as
Ollama, Gemini, Anthropic, OpenAI, or OpenAI-compatible endpoints belong in the
model provider layer.

Cloud model adapters follow the same provider pattern. Gemini is a
`ModelProvider` implementation named `gemini/<model>`, configured through a
nested provider block such as:

```toml
[providers.gemini]
models = ["gemini-3.5-flash"]
api_key_env = "GEMINI_API_KEY"
timeout_seconds = 60
```

The adapter resolves the API key from the configured environment variable,
constructs the Google GenAI client explicitly with that key, calls the Gemini
Interactions API with `store = false`, and returns normalized `ModelResponse`
text. Missing dependencies, missing API keys, request failures, and empty
provider output become `ModelProviderError` instances. The router registers
Gemini models when they are listed in provider config or referenced by
`[models].default`, `[models.modes]`, or `[models.roles]`; an environment
variable alone should not silently change the active model set.

Tool schemas belong on `ToolSpec`. External adapters such as MCP should preserve
provider-declared input schemas during registration so planners can see valid
argument shapes and the execution boundary can strip unsupported arguments or
fail missing required fields before provider calls.

Tool-local argument hints also belong on `ToolSpec`. These are short
selected-tool-only instructions, such as provider query syntax or conservative
defaults, that improve ToolUseAgent accuracy without bloating the global
tool-use prompt or adding provider branches to Python code.

Capability metadata also belongs on `ToolSpec`. Adapters should describe
semantic capability facts such as domain, operation, provider, read-only status,
and whether a tool is demo-only. The planner should prefer those deterministic
capability facts over prompt-only naming rules whenever it selects tools or
repairs arguments.

Planner tool selection should stay LLM-driven for real-model runs. Deterministic
code may validate, reject, trace, and degrade safely, but `planner.py` should not
accumulate provider, domain, workflow, or keyword branches just to make a demo
select the right tool. If an available tool is not being selected, improve the
tool metadata, descriptions, schemas, argument hints, planner prompt, retry
strategy, or evaluation harness instead. Any existing keyword-like fallback
logic is transitional and should be removed or generalized as the model-driven
path improves.

The planner should not be under-informed. It should receive a useful tool
catalog that includes names, descriptions, risk, approval requirements,
capability metadata, input schemas, and short tool-local hints when appropriate.
The planner prompt may teach generic selection principles, such as choosing the
tools that best satisfy the user goal, preferring read-only tools for inspection
or summarization, using write/action tools only when the user clearly asks to
change state, and returning only the selected plan JSON. This is context, not a
deterministic router. Provider-specific examples and defaults should be owned by
the registered tool metadata or hints, so adding a provider does not require a
new planner branch.

Tool and agent metadata quality is part of the runtime contract. New tools
should not be considered integrated merely because they execute. They should
describe themselves well enough for the planner and ToolUseAgent to use them:
stable name, concrete description, input schema, capability metadata, risk and
approval settings, concise argument hints, and useful structured outputs. New
agents should likewise describe purpose, allowed tools or domains, model mode,
risk expectations, memory expectations, and output contract. If planner quality
is poor for a new integration, improve this metadata before adding code-level
routing.

Model-backed tool-use argument construction lives under
`jarvis.orchestration.arguments`. The planner proposes tool choices and rough
arguments. Before execution, `ToolUseAgent` asks the selected model to produce
valid JSON arguments for one tool from the goal, current time, prior successful
results, tool metadata, `input_schema`, and `argument_hints`. Deterministic code
resolves references, validates the schema, retries the agent when validation
fails, and fails cleanly if valid arguments cannot be produced. Tool-specific
adapters may contribute better schemas, hints, and metadata, but `planner.py`
should not accumulate provider or workflow branches. The registry still applies
the same schema validation at execution time as a final boundary.

`ToolUseAgent` is generic, not provider-specific. It owns schema-grounded
argument construction and repair across all tools. It receives the goal, current
datetime, selected tool spec, declared input schema, rough planner arguments,
selected tool argument hints, prior successful tool results, validation
failures, and explicit read-only argument-like provider/tool execution errors.
It returns only JSON arguments for the selected tool. Deterministic orchestration
still chooses when to call it, validates its output, applies policy, executes
tools, records traces, and sends confirmed results to synthesis.

The intended tool-use path is:

```text
planner chooses capabilities
  -> ToolUseAgent builds or repairs JSON arguments from schemas
  -> deterministic validation and policy checks run
  -> tool executes
  -> failed validation/read-only argument errors may get a repair attempt
  -> confirmed results accumulate across steps
  -> synthesis writes the final grounded answer
```

Broader loops should be added only after this one-pass tool-use path is
reliable. The first loop is narrow: retry invalid arguments or recover once from
structured read-only provider argument errors. Auth, token, permission, and
timeout errors are not repaired by ToolUseAgent. Broader planning loops,
repeated tool calls, and multi-step search/refine behavior can come after traces
make those failures inspectable.

Multi-model routing extends the same boundaries. The model router can assign
planning, tool-argument repair, language generation, and synthesis to different
local or cloud models based on role, mode, cost, latency, privacy, and expected
difficulty. Tool and policy code should not need to know which model handled
each language step.

Evaluation is the bridge between prompt/tool metadata work and model routing.
The current `jarvis.evals` harness runs planner-only and tool-use-only cases
against a selected config/model, compares expected selected tools and valid
argument shapes, and reports scores without executing private provider actions.
Future eval work can add role-based model comparison, repair-count metrics,
trace aggregation, and opt-in end-to-end provider suites.

Local MCP integration packs are a first-class extension path. A provider wrapper
can run as a local stdio FastMCP server, call the provider's REST/API surface,
and expose tools through the same `[[mcp.servers]]` configuration that hosted
MCP servers use. JarvisOS should keep orchestration, policy, approvals, traces,
and synthesis outside those wrappers; wrappers should own provider transport and
schema details.

Built-in capability packs live in the settings layer as reusable config
fragments. Enabling `[capabilities].google_workspace = true` expands to normal
Calendar and Gmail MCP server settings before runtime construction, so the
orchestrator, ToolRegistry, policy engine, and traces treat bundled and
user-configured tools the same way. Future default packs such as Spotify,
Drive, or notes should follow this pattern: add a pack definition that produces
normal tool/plugin/MCP settings, then let explicit user config override any
server with the same name.
