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

Early JarvisOS kept one file per subsystem under `src/jarvis/`, such as
`models.py`, `tools.py`, `plugins.py`, `settings.py`, and `orchestrator.py`.

Reason: the first slices are still small enough that a flat layout is easier to
read and change. Refactor into subpackages when files start mixing multiple
responsibilities or become hard to scan.

This decision has been superseded by decision 0024.

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

## 0021 - Keep Language Generation Separate From Provider Tools

JarvisOS should add a generalist language capability before relying on real
provider integrations for workflows that require drafting, rewriting, or
intermediate text generation.

Reason: provider tools should read or write provider state. They should not own
the reasoning or drafting layer. For example, an email specialist or generalist
LLM should compose an email body from context, then Gmail tooling should create
the draft or send after approval. The same pattern applies to simple MCP demos:
generate the text first, then pass it to an echo tool.

## 0022 - Split Packages After Generalist Capability Lands

The codebase should move from flat files into subpackages soon, but the next
behavioral slice should land first.

Reason: the generalist slice will clarify responsibility boundaries between
language agents, deterministic tools, MCP adapters, orchestration, and config.
Splitting immediately after that reduces churn and gives the new package layout
real domain pressure instead of guessing too early.

## 0023 - Add Generalist Text Generation Before Real Provider Writes

JarvisOS now exposes `general.generate_text` as a low-risk internal capability
that uses the selected model to generate intermediate text. Plan steps can pass
the previous successful text output into another tool with `$last.text`.

Reason: workflows such as drafting email, preparing agendas, or using MCP tools
need JarvisOS to generate language before provider tools act. Provider tools
should read or write provider state; the runtime's language agents should draft,
rewrite, and summarize through the model router.

Tradeoff: `$last.text` is intentionally small. It proves vertical data flow
without introducing a full workflow variable system before real integrations
create pressure for it.

## 0024 - Split Runtime Packages After the Generalist Slice

JarvisOS now groups code by subsystem: orchestration, tools, models,
integrations, and storage. Top-level compatibility wrappers for moved modules
were removed after tests and internal imports switched to canonical package
paths.

Reason: the generalist slice clarified the highest-pressure boundaries. The
package split keeps Calendar, Spotify, cloud providers, and richer sub-agent
work from crowding the flat `src/jarvis/` package.

## 0025 - Apply MCP Policy Overrides at Tool Registration

MCP servers can define server-level policy defaults and per-tool overrides.
JarvisOS applies these when registering `ToolSpec` entries.

Reason: the planner, policy engine, CLI tool listing, and trace records should
all see one consistent tool policy. Calendar reads can be auto-allowed while
Calendar writes, Gmail sends, playback controls, and destructive actions remain
approval-gated.

## 0026 - Add HTTP MCP Below the Existing Tool Contract

JarvisOS now supports both stdio and streamable HTTP MCP servers behind the same
tool registration path.

Reason: official Google Workspace MCP servers and many future hosted tools use
HTTP. The orchestrator should not care whether a tool came from a local process
or a remote endpoint; it should still see one `ToolSpec`, one policy decision,
one tool result, and one trace path.

Tradeoff: the current HTTP client handles request/response JSON and simple SSE
responses, but not richer long-lived sessions or server-initiated requests yet.

## 0027 - Treat OAuth as an Integration Substrate

JarvisOS now has provider metadata in settings and a SQLite auth store that can
supply bearer tokens to HTTP MCP servers.

Reason: Google Calendar, Gmail, Spotify, and other external providers need auth
without leaking provider details into orchestration or prompts. Token lookup and
headers belong under integrations; policies, traces, and tools should only see
sanitized execution metadata.

Tradeoff: this is not a full browser OAuth flow yet. Authorization-code login,
PKCE, refresh-token renewal, and token redaction hardening should land in the
next auth-focused slice.

## 0028 - Trigger OAuth From First Tool Use

Authenticated HTTP MCP tools now resolve tokens on demand. If no valid token is
available, JarvisOS starts an OAuth authorization-code + PKCE flow, prints and
opens the authorization URL, captures the local callback, stores tokens, and
continues the original MCP call.

Reason: a separate login command is useful as a fallback, but the comfortable
user experience is to ask JarvisOS to do work and only sign in when a required
tool actually needs access. This keeps auth tied to real capability use instead
of making users preconfigure every provider up front.

Tradeoff: the current flow requires a configured provider and redirect URI. It
does not yet implement MCP protected-resource metadata discovery, dynamic client
registration, encrypted token storage, or provider-specific account selection
UX.
