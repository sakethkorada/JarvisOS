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

## 0029 - Preserve Tool Input Schemas

MCP `inputSchema` values are now stored on `ToolSpec`, exposed to the planner,
and used at the tool execution boundary for conservative argument cleanup.

Reason: LLM planners may choose the right tool but invent generic arguments such
as `query`. Provider tools should receive only arguments declared by their
schema, and missing required fields should fail before the provider call.

Tradeoff: JarvisOS currently implements only a small object-schema subset:
declared properties and required fields. A full JSON Schema validator can be
added later if real integrations need deeper type, enum, or nested validation.

## 0030 - Add Redacted OAuth Debugging

JarvisOS now exposes `jarvis auth debug <provider>` to inspect stored OAuth
metadata, configured scopes, token expiry, granted scopes from provider
token-info endpoints, and client-id matching without printing access or refresh
tokens.

Reason: real MCP/API integrations can fail because of app scopes, stale tokens,
OAuth client mismatch, provider admin policy, or application code. A redacted
debug command helps separate those causes before changing runtime logic.

Tradeoff: token-info is provider-specific. JarvisOS supports a configurable
`tokeninfo_url` and a Google default, while providers without token-info still
show local stored-token and configured-scope metadata.

## 0031 - Prefer Local FastMCP Wrappers For Provider POCs

JarvisOS now includes a local FastMCP Google Calendar wrapper example that calls
Google Calendar REST APIs but exposes them back to JarvisOS as MCP tools.

Reason: hosted provider MCP servers can be preview-gated or unavailable even
when the underlying REST API and OAuth token work. A local MCP wrapper proves
the same JarvisOS orchestration path without hardcoding provider branches into
the orchestrator.

Tradeoff: the first wrapper shares JarvisOS auth storage for convenience. Longer
term, integration packs may own their own auth or receive tokens through a more
formal local integration contract.

## 0032 - Use Newline-Delimited JSON For Stdio MCP

JarvisOS now sends stdio MCP messages as one JSON-RPC object per line and keeps
a compatibility reader for legacy `Content-Length` framed responses.

Reason: the current MCP stdio transport specifies newline-delimited JSON-RPC,
and FastMCP/Python SDK servers expect that framing. The older `Content-Length`
approach caused FastMCP servers to treat headers as invalid JSON.

Tradeoff: legacy header-framed stdio servers may need to accept newline-delimited
requests or be wrapped. JarvisOS still reads header-framed responses to preserve
some backward compatibility with the early demo server shape.

## 0033 - Remove The Built-In Calendar Demo Tool

JarvisOS no longer registers a deterministic fake calendar reader by default.
Calendar behavior now requires configured MCP/plugin tools.

Reason: once a real Calendar MCP path exists, a fake calendar tool creates false
positives and lets LLM planners choose a placeholder path that looks successful
while hiding integration problems.

Tradeoff: meeting-prep smoke tests no longer show demo calendar events unless a
calendar tool is configured. This keeps failures honest and makes integration
debugging clearer.

## 0034 - Add Tool Capability Metadata

`ToolSpec` now carries optional capability metadata such as domain, operation,
provider, read-only status, and demo status.

Reason: planner selection should rely on deterministic capability facts rather
than prompt-only naming guidance. MCP adapters can expose provider tools through
one registry while still giving the planner enough semantics to choose and
repair arguments safely.

## 0035 - Add A Direct Tool Debugger

JarvisOS now has `jarvis tool call <tool_name> --args-json "{}"` for direct
registered-tool execution after policy evaluation.

Reason: integration adapters need a small test surface that bypasses LLM
planning and final synthesis. This improves locality when debugging MCP,
plugins, REST wrappers, and future provider tools.

## 0036 - Separate Global Auth From Run Config

Provider auth should become a global JarvisOS profile rather than something each
tool config must fully repeat.

Reason: configs such as `google-calendar-fastmcp.toml` should describe enabled
tools/MCP servers and model choices, not duplicate Google OAuth metadata or
token DB setup. Once Google is connected, Calendar, Gmail, Drive, and future
Google tools should share the same auth substrate across configs.

Tradeoff: the current implementation still loads provider metadata from TOML.
The next auth slice should add a default/global auth profile lookup while
preserving explicit config overrides for testing and portable examples.

## 0037 - Move Toward Unified Schema-Aware Arguments

Tool argument construction should live in a unified resolver rather than
planner-specific heuristics.

Reason: the planner should choose or propose capabilities, while deterministic
code maps user intent, `ToolSpec.input_schema`, `ToolSpec.capability`, prior
tool outputs, and safe defaults into concrete tool arguments. This avoids
adding Gmail, Calendar, Spotify, and provider-specific branches directly to
`planner.py`.

Tradeoff: deterministic code now intentionally stays small: `$last.text`
resolution, object-schema cleanup, required-field checks, and clean failures.
Nuanced argument construction such as relative dates belongs in the model-backed
argument builder, not in hand-written provider parsers.

## 0038 - Load Auth From A Global Profile

JarvisOS now resolves auth provider metadata and token storage from a shared
auth profile when the active run config does not define `[auth]`.

Reason: configs such as `google-calendar-fastmcp.toml` should enable a set of
tools, not duplicate Google OAuth metadata. Once Google is connected, Calendar,
Gmail, Drive, and future Google tools should be able to use the same provider
profile and token database.

Lookup order is `JARVIS_AUTH_PROFILE`, `.jarvis/auth.toml`,
`config/auth.toml`, `jarvis.toml`, then `config/jarvis.toml`. Explicit auth in
the run config still wins for portable examples and tests.

Tradeoff: provider setup is still manual TOML plus environment secrets. A later
auth UX can add `jarvis auth connect` or profile initialization without changing
the runtime contract.

## 0039 - Resolve Tool Arguments Outside The Planner

JarvisOS now resolves tool arguments through `jarvis.orchestration.arguments`
using a model-backed argument builder for non-`fake-local` runs.

Reason: LLM planners should choose tools and propose rough arguments, then a
focused argument-builder prompt should produce valid JSON for one selected
tool. Deterministic runtime code should normalize references, validate declared
tool schemas, retry on validation errors, and fail cleanly. This lets new MCP
tools share one argument lifecycle instead of adding Calendar, Gmail, Spotify,
and provider-specific branches to `planner.py`.

The planner validates schema shape without resolving `$last.text`; execution
resolves `$last.text` using prior successful tool results. The same schema
normalization helper is reused by the ToolRegistry as the final execution
boundary.

Tradeoff: model quality now shows through. Local models may still produce weak
or provider-invalid arguments, but that failure is visible in traces and
synthesis instead of being hidden by deterministic Calendar-specific repair.

## 0040 - Prefer Real Local Models Over fake-local By Default

`fake-local` remains registered, but the default model router now prefers a
discovered/configured Ollama model when settings do not explicitly choose a
model.

Reason: JarvisOS' POC should exercise real model planning, argument building,
and synthesis. `fake-local` is useful for deterministic tests and debugging, but
it should not be the normal user path once a local model exists.

Tradeoff: no-config runs may become slower or less deterministic on machines
with Ollama running. Tests and smoke checks can still select `--model
fake-local` explicitly.

## 0041 - Make ToolUseAgent The Generic Tool Argument Boundary

Tool argument construction belongs in a generic `ToolUseAgent` or
`ArgumentAgent`, not provider-specific schema-filling agents.

Reason: JarvisOS needs one scalable tool-use path that can call Calendar,
Gmail, Spotify, memory, notes, plugins, and MCP tools from declared schemas.
Adding Calendar-specific or Gmail-specific planner parsing would make every new
provider a new hardcoded branch. A generic tool-use agent can receive the goal,
current datetime, selected `ToolSpec`, input schema, rough planner arguments,
prior successful results, validation feedback, and later provider errors, then
return only JSON arguments for the selected tool.

Deterministic code remains responsible for schema validation, reference
resolution, policy checks, approvals, execution, retries, traces, and final
success/failure status. The agent is responsible only for language-to-JSON
argument construction and repair.

Tradeoff: model quality becomes visible. A local model may still produce weak
or provider-invalid arguments, and the runtime should surface that rather than
hide it behind brittle deterministic parsers.

Longer term, the same boundary supports multi-model routing. Planning,
argument repair, language generation, and synthesis can each use different
models through the model router without changing tool adapters or policies.

## 0042 - Retry Only Explicit Read-Only Argument-Like Tool Failures

JarvisOS now feeds one failed tool execution back to ToolUseAgent only when the
tool is low-risk, does not require approval, and has explicit read-only
capability metadata. Auth, token, permission, and timeout failures are not
treated as argument-repair candidates.

Reason: provider errors such as Calendar HTTP 400 invalid payloads often mean
the selected tool was correct but the arguments need repair. Retrying one
read-only call improves tool-use quality without introducing a general agentic
loop.

Tradeoff: ambiguous low-risk tools without read-only capability metadata are
not retried, even if they are probably safe. Adapters should add accurate
`ToolSpec.capability.read_only` metadata when they want execution-error repair.
Writes, sends, deletes, approval-required tools, and medium/high-risk tools
must not be retried automatically. Non-argument failures must surface clearly to
the user rather than burning model calls on repairs that cannot work.

## 0043 - Add Gmail As A Read-Only Local FastMCP Tool Pack

Gmail enters JarvisOS through a local FastMCP wrapper that calls Gmail REST read
endpoints and exposes MCP tools named `gmail.list_recent`,
`gmail.search_messages`, `gmail.get_message`, and `gmail.get_thread`.

Reason: Gmail is the next highest-value personal workflow source after
Calendar, and it reuses the existing Google OAuth profile, MCP stdio adapter,
ToolRegistry, ToolUseAgent, policy, trace, and synthesis paths. Starting
read-only keeps the safety boundary simple while enabling useful queries such
as meeting prep from Calendar plus related email.

Tradeoff: this is a local wrapper around Gmail REST rather than a hosted Google
MCP server. That is acceptable for the POC because it keeps behavior inspectable
and avoids hosted MCP permission issues. Drafting and sending email should be
added later as approval-required tools.

## 0044 - Add Built-In Capability Packs As Settings Fragments

JarvisOS now supports opt-in built-in capability packs under `[capabilities]`.
The first pack, `google_workspace`, expands to the local FastMCP Google Calendar
and Gmail read-only MCP servers.

Reason: common integrations should be easy to enable from the default
`jarvis.toml` without requiring a special per-run `--config`, but the core
runtime should still see ordinary tools. Putting packs in the settings layer
keeps orchestration generic: bundled defaults, copied example configs, and
user-authored MCP servers all become the same `McpServerSettings` before
runtime construction.

Explicit `[[mcp.servers]]` entries with the same server name override bundled
pack servers. This lets a user swap in a custom Gmail wrapper, hosted MCP
server, or disabled server without adding runtime branches.

Tradeoff: bundled packs still point at local example FastMCP wrapper scripts in
this POC. A later packaging cleanup can move stable wrappers into a package
module while preserving the same `[capabilities]` contract.

## 0045 - Make Normal Run Output Answer-First

Normal `jarvis run` output is now treated as the user-facing answer, not a
debug transcript. The synthesis prompt tells the model to answer directly from
confirmed results, avoid runtime headings, and ignore internal helper outputs
when better provider/tool results exist. Deterministic fallback now produces
concise grounded answer lines instead of `Goal`, `Status`, completed-tool, and
grounded-results sections.

Reason: as real MCP tools accumulate, debug-shaped output becomes noisy and
makes the system feel less like an assistant. Detailed plans, tool results,
errors, retries, and traces are still available through `--json`,
`jarvis traces`, and `jarvis tool call`, so normal output can stay focused on
what the user asked.

Tradeoff: deterministic fallback is less verbose in normal output. Developers
should use JSON output and trace commands when they need exact tool execution
details.

## 0046 - Put Tool-Use Examples On The Tool, Not In One Big Prompt

JarvisOS now supports `argument_hints` on `ToolSpec` and MCP per-tool override
settings. The ToolUseAgent receives only the selected tool's hints alongside
the selected tool schema.

Reason: provider-specific calling advice, such as Gmail search syntax or
Calendar default time ranges, improves argument construction but should not
become hardcoded Python logic or a giant global ToolUseAgent prompt. Tool-local
hints keep context small, keep ownership near the tool/plugin/MCP config, and
let bundled defaults and user plugins tune their tools independently.

Tradeoff: hint quality becomes part of tool quality. Poor hints can bias model
arguments, but deterministic schema validation and policy checks still control
execution. Hints should stay short and should not include secrets or broad
workflow instructions.

## 0047 - Add Spotify As A Read-Only Capability Pack First

Spotify enters JarvisOS through a local FastMCP wrapper and an opt-in
`[capabilities].spotify` pack. The first tools are read-only:
`spotify.search`, `spotify.current_playback`, `spotify.recently_played`, and
`spotify.list_playlists`.

Reason: Spotify is a useful non-Google provider to test the generic capability
pack, OAuth profile, MCP, ToolUseAgent schema/hints, policy, and synthesis
path. Starting read-only lets the runtime validate a new provider family without
introducing side effects or approval complexity.

Tradeoff: playback actions such as play, pause, skip, queue, save, or playlist
mutation are intentionally deferred. They should be added only after the
approval/apply UX is stronger, because they change external user-visible state.

## 0048 - Keep Planner Tool Selection LLM-Driven

For real-model runs, the planner should choose tools from the available
`ToolSpec` metadata, descriptions, schemas, and prompts. Deterministic runtime
code should validate plans, enforce policy, normalize arguments, record traces,
and provide safe generic degradation. It should not grow provider, domain,
workflow, or keyword-routing branches as the normal way to select tools.

Reason: keyword routing makes the runtime look successful while bypassing the
agent behavior JarvisOS is trying to prove. It also does not scale to Gmail,
Calendar, Spotify, Drive, user plugins, MCP packs, and future custom tools.
When a model misses a tool, the better fix is to improve tool metadata,
descriptions, input schemas, tool-local argument hints, planner prompts,
ToolUseAgent repair, or trace-based evaluation.

Tradeoff: local models may miss obvious tools more often while the planner path
is being tuned. Those failures are useful signal. Fallback can remain as a
minimal safety/debug path, but existing keyword-like calendar/email/music
fallback behavior should be treated as transitional scaffolding to remove or
generalize, not a pattern to copy for future integrations.

## 0049 - Give Planner Tool Context Without Turning It Into Routing Logic

The planner should receive a rich catalog of registered tools, including names,
descriptions, risk levels, approval requirements, capability metadata, input
schemas, and concise tool-local hints when useful. The global planner prompt may
teach generic behavior: choose the tools that best satisfy the goal, use tool
metadata and schemas, prefer read-only tools for inspection or summarization,
use write/action tools only when the user clearly requests external state
changes, never invent tools, and return only plan JSON.

Reason: removing keyword routing should not mean starving the planner. The
model needs enough semantic context to understand what each registered tool can
do, especially when tool names are short or provider-specific. That context
should come from tool metadata and generic prompt rules rather than hidden
Python branches or a global prompt full of provider-specific instructions.

Tradeoff: richer tool catalogs use more prompt context. Keep global rules short,
push provider examples/defaults into tool-local descriptions and
`argument_hints`, and later add catalog compaction or retrieval if the tool list
gets too large.

## 0050 - Make Deterministic Planner Fallback Minimal

The deterministic planner fallback no longer routes to provider/plugin tools by
matching user words. It creates only generic safe steps, currently memory search
and a lightweight summary when those tools are registered. Calendar, Gmail,
Spotify, notes, echo, local tasks, and future provider tools must be selected by
the LLM planner from the registered tool catalog, then validated by deterministic
runtime code.

Reason: fallback routing had become a shadow planner and hid whether the model
could actually choose tools from metadata. Removing provider keyword fallback
makes the POC more honest and scalable. The model now gets richer catalog
context and one repair attempt for invalid plan JSON or schema-invalid plans.

Tradeoff: `fake-local` is less demo-like and will not exercise arbitrary MCP or
plugin tools by itself. Use model-backed tests or explicit `jarvis tool call`
for those paths. This is intentional: `fake-local` is now a debug/test model,
not a provider selection substitute.

## 0051 - Add A Safe Runtime Context Tool

JarvisOS now includes `system.current_datetime`, a low-risk built-in tool that
returns the current local date, time, timezone, and ISO datetime. The planner
prompt tells models to use system/runtime-context tools for current date/time
questions.

Reason: asking a language model for the current date without a tool produces
refusals or guesses. A tiny deterministic read-only tool gives the planner a
real capability while keeping the orchestration path generic.

Tradeoff: this is a built-in local runtime tool, not an external provider. That
is acceptable because current date/time is local runtime context and does not
need OAuth, MCP, or provider routing.

## 0052 - Reject Unsupported Planner Reference Syntax

Planner validation now rejects argument strings that start with `$` unless they
use the supported `$last.<field>` form. Invalid references trigger the existing
planner repair pass before execution.

Reason: local models sometimes invent references such as `$result.text`.
Passing those strings to provider tools creates confusing downstream failures,
especially for id fields such as `message_id` or `thread_id`. Rejecting them at
plan validation keeps the error in the planning layer where it can be repaired.

Tradeoff: only the minimal `$last.<field>` syntax is supported today. Richer
named outputs and structured references should be added deliberately rather
than accepted implicitly.

## 0053 - Treat Tool And Agent Metadata As A Quality Gate

Every new tool and agent should carry planner-usable metadata before it is
treated as integrated. Tools need stable names, concrete descriptions,
input schemas, capability metadata, risk and approval settings, concise
argument hints, and useful structured outputs. Agents need a clear purpose,
allowed tools or domains, model mode, risk expectations, memory expectations,
and an output contract.

Reason: after removing deterministic keyword routing, planner quality depends
on the catalog the model sees. Good metadata gives local and cloud models enough
context to choose tools and fill arguments without hidden provider-specific
branches.

Tradeoff: adding tools becomes slightly slower because each integration needs
metadata, not only executable code. That is the right cost: weak metadata
creates flaky planning and pushes the project back toward hardcoded shortcuts.

## 0054 - Use Evals To Compare Prompts And Models Before Routing Complexity

The next architecture layer should include a lightweight eval harness for
planner and ToolUseAgent behavior. It should run canned goals against a chosen
model/config, record selected tools, argument validity, repair counts, and final
status, then score the result against expected tools or acceptable tool sets.

Reason: JarvisOS now has enough moving parts that subjective manual testing is
not enough. Evals will show whether prompt changes, tool metadata, local model
upgrades, or API models improve planning quality.

Tradeoff: early evals should avoid executing private provider tools by default.
Planner-only and tool-use-only evals give useful signal without reading Gmail,
Calendar, or other personal data. End-to-end provider evals can be opt-in later.

## 0055 - Keep The First Eval Harness Non-Executing

The first eval harness runs isolated `planner` and `tool_use` cases from JSON
suite files. Planner cases call `Planner.create_plan()` and score selected tool
names, fallback use, forbidden tools, and step count. Tool-use cases call
`ToolUseAgent.build()` for an already-selected tool and score required keys,
forbidden keys, and expected argument values. Neither case type executes the
provider tool.

Reason: the next model-substrate work needs a way to compare local and API
models without reading private Gmail, Calendar, Spotify, or other provider
data. Isolated evals expose whether the model can choose tools from metadata
and fill schema-grounded arguments before we add role-based model routing.

Tradeoff: this does not measure full end-to-end task quality yet. It is
deliberately smaller: once planner/tool-use behavior is stable, add opt-in
provider smoke suites and richer metrics such as repair counts, latency, and
model role comparisons.

## 0056 - Separate Agent Profiles From Model Provider Routes

JarvisOS now treats agent identity, execution role, and model provider as
separate concepts. `AgentSpec` describes the profile: name, description,
allowed tools, execution role, prompt reference, output contract, memory scope,
and risk permissions. `AgentRuntime` wraps model-backed agent calls and asks the
model router to resolve that agent's `execution_role` plus the active mode into
a concrete provider. `[models.roles]` supplies role routes such as `planner`,
`tool_use`, `synthesis`, and `general`.

Reason: user-defined agents and future plugin specialists should not require
new Python branches or direct provider choices. A Calendar, Gmail, research, or
custom agent should declare what behavior it needs; the model router should
decide whether that means Ollama, Gemini, Anthropic, OpenAI, Grok, LM Studio, or
another provider under current user settings.

Tradeoff: there are now more names to keep distinct: agent name, execution
role, mode, and provider. The architecture depends on keeping these meanings
strict. `--model` remains a global override for testing, but normal daily
routing should flow through role/mode/default settings.

## 0057 - Add Gemini Through a Provider Adapter

Gemini is the first cloud/API model provider adapter. It extends
`jarvis.models` as a normal `ModelProvider` named `gemini/<model>`, not as a
Gemini-specific agent, planner branch, or tool routing shortcut. Agent code
should continue to request execution roles such as `planner`, `tool_use`,
`synthesis`, or `general`; `ModelRouter` should resolve those roles to concrete
providers from settings.

Configuration should use a nested provider block:

```toml
[providers.gemini]
models = ["gemini-3.5-flash"]
api_key_env = "GEMINI_API_KEY"
timeout_seconds = 60
```

The adapter uses the Google GenAI SDK and Gemini Interactions API, passes the
API key explicitly from `api_key_env`, and set `store = false` by default so
JarvisOS traces and memory remain the authoritative local history. The router
should register Gemini models when listed in provider config or referenced by
`[models].default`, `[models.modes]`, or `[models.roles]`; merely having
`GEMINI_API_KEY` set should not affect routing.

Reason: this proves the role/model substrate with a real cloud provider while
preserving the same extension path for OpenAI, Anthropic, Grok, and
OpenAI-compatible endpoints. Provider transports, auth, dependency checks,
timeouts, and response parsing belong below the model interface. Planner,
ToolUseAgent, and synthesis should remain provider-agnostic.

Tradeoff: Gemini requires an optional dependency and an API key, so tests mock
the SDK by default. Live calls remain opt-in smoke tests or manual commands,
not normal unit tests.

## 0058 - Persist Normalized Tool Failure Details in Traces

Every failed tool execution records the normalized `ToolResult.error` in its
trace event alongside the tool name, resolved arguments, and safe output.
Argument-resolution and agent-permission failures follow the same rule.

Reason: traces are the debugging and evaluation record for real model behavior.
A failed status without its provider or validation error makes it impossible to
distinguish auth, MCP transport, schema, policy, and model-quality failures.

Tradeoff: error text can contain provider details, so future redaction rules
must continue to prevent secrets from entering tool error messages or traces.

## 0059 - Ground Final Synthesis Against Tool Outcomes

The synthesis prompt receives successful and failed tool results as separate
sections. Runtime checks reject a response that references a registered tool
family with no result, presents data for a family whose calls all failed, or
adds unsupported speculation such as "likely" or "probably".

Reason: a language model can produce fluent but unsupported summaries even when
the execution trace is correct. Tool metadata provides generic family names for
this check, so grounding does not require Calendar, Gmail, or Spotify routing
branches.

Tradeoff: conservative checks may send some otherwise useful model responses to
the deterministic grounded fallback. That is preferable to claiming private
provider data JarvisOS did not confirm.

## 0060 - Make Runtime Memory Opt-In Per Configuration

`[memory].enabled` controls whether a runtime registers `memory.search` and
creates end-of-run memory suggestions. Disabling it leaves the configured SQLite
database and manual memory CLI commands intact.

Reason: early memory retrieval can distract from tool-planning and synthesis
evaluation before relevance, duplicate handling, and review UX are mature.
Turning it off must remove it from the actual tool catalog and generic fallback,
not merely hide its output.

Tradeoff: disabled runs cannot use stored preferences as context. This is an
intentional temporary reduction in capability while the core tool-use loop is
being hardened.

## 0061 - Retire The Hosted Google Calendar MCP Configuration

The hosted Google Calendar MCP endpoint is no longer part of the active default
configuration. Direct tool calls reached the endpoint but consistently received
permission failures, while the same Google OAuth token could access Calendar
REST through the local FastMCP wrapper.

Reason: an integration that produces opaque provider authorization failures
creates false debugging paths for planner, ToolUseAgent, policy, and synthesis.
The `google_workspace` capability pack now remains the supported Calendar and
Gmail POC path. Generic HTTP MCP support stays in the runtime for other
compatible servers.

Tradeoff: hosted Google Calendar MCP experimentation now requires an explicit
custom config instead of appearing as a working default capability.

## 0062 - Require Planner Coverage For Explicit Multi-Source Goals

The planner prompt now treats coverage as a first-class requirement: it must
identify every explicit service, information source, or requested outcome and
include an evidence-producing step for each available source. Supporting steps
do not substitute for a requested source.

Reason: real Gemini traces showed valid but incomplete plans that read Calendar
while omitting explicitly requested Gmail data. This is a planning-quality issue
that metadata and prompt context should solve before introducing another agent
loop.

Tool descriptions and selected-tool hints distinguish broad recent-item reads
from targeted searches. Planner evals can now require one tool from an allowed
group, avoiding brittle tests where multiple tools are valid for the same
source.

Tradeoff: the planner prompt is slightly more prescriptive about completeness,
but it does not contain provider keyword-routing branches or force any tool
outside the registered catalog.
