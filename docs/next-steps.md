# Next Steps

## Recommended Next Slice

Use the implemented Gemini adapter for a small manual role-routing and eval
comparison when quota is available:

1. Install `google-genai` with `uv pip install -e ".[gemini]"`.
2. Set `GEMINI_API_KEY` locally and route `planner` to
   `gemini/gemini-3.5-flash` in `jarvis.toml`.
3. Run the planner/tool-use eval suite against Gemini and Ollama without
   executing provider tools.

After that comparison, add the next cloud adapters behind the same
`ModelProvider` contract. Do not add provider-specific branches to agents,
planner, ToolUseAgent, or synthesis.

Before adding more tools, run `examples/evals/planner-coverage.json` against
the configured planner model. It checks compound requests for complete coverage
of every explicitly requested available source without executing private tools.
Classify provider quota and rate-limit responses separately from model-quality
failures.

The next implementation slice should use local fixtures rather than live
requests to improve two observed end-to-end issues: ToolUseAgent should produce
bounded future Calendar arguments for "upcoming" goals, and synthesis should
render compact grounded summaries from structured Calendar and Gmail records.

## Completed Slices

- Added the barebones runtime and CLI.
- Added provider-agnostic model settings.
- Added local plugin loading.
- Added SQLite-backed memory with manual commands.
- Added suggest-only memory extraction.
- Added SQLite-backed trace persistence with CLI inspection.
- Added LLM-assisted planning with deterministic validation and fallback.
- Added LLM-first final synthesis with deterministic fallback.
- Added the first structured model-provider error boundary.
- Moved planner and synthesis prompts into bundled markdown files with optional
  config overrides.
- Improved the meeting-prep demo path with deterministic Jordan calendar data.
- Added a SQLite-backed approval queue with CLI list/show/approve/reject.
- Added `task.create` and a local SQLite task store for auto-allowed local
  writes.
- Added `tasks show/complete` and simple task-title cleanup.
- Added deterministic duplicate prevention for approved memory writes.
- Added a generic stdio MCP tool adapter and demo MCP server.
- Added `general.generate_text` as a model-backed internal language capability.
- Added minimal `$last.text` step data flow for generated text -> tool calls.
- Split runtime code into orchestration, tools, models, integrations, and
  storage packages, then removed top-level compatibility wrappers.
- Added per-tool MCP risk and approval overrides.
- Added streamable HTTP MCP discovery/calls beside stdio MCP.
- Added OAuth provider settings, local token storage, and bearer-token injection
  for HTTP MCP.
- Added on-demand OAuth authorization-code + PKCE flow with local callback
  capture and refresh-token renewal.
- Added MCP input-schema preservation and conservative argument cleanup.
- Added redacted `auth debug` diagnostics for OAuth providers.
- Added a local FastMCP Google Calendar wrapper example around Calendar REST
  read tools.
- Updated stdio MCP to newline-delimited JSON-RPC and verified the local
  FastMCP Calendar wrapper against the user's real Calendar account.
- Removed the built-in deterministic calendar demo tool so Calendar behavior
  now requires configured MCP/plugin capabilities.
- Added `ToolSpec` capability metadata for deterministic planner selection.
- Added direct `jarvis tool call <tool_name> --args-json "{}"` debugging.
- Added global auth profile fallback so tool configs can inherit provider OAuth
  metadata and token storage without duplicating `[auth]`.
- Added a model-backed argument builder and removed hand-written Calendar
  relative-date parsing from the resolver.
- Promoted the argument builder into a generic ToolUseAgent with a bundled
  `tool_use` prompt, prompt override support, traceable attempts, and one repair
  pass for explicit read-only argument-like tool execution failures.
- Added a local FastMCP Gmail wrapper with read-only `list_recent`,
  `search_messages`, `get_message`, and `get_thread` tools, plus a combined
  Calendar + Gmail Workspace config example.
- Added built-in capability-pack loading. The current `google_workspace` pack
  expands to the local FastMCP Calendar and Gmail read-only MCP servers, while
  explicit `[[mcp.servers]]` config can override bundled defaults by server
  name.
- Cleaned normal `jarvis run` synthesis output so it is answer-first. Runtime
  headings and successful tool logs stay in `--json`/traces instead of normal
  final responses, and deterministic fallback is now concise and grounded.
- Added tool-local `argument_hints` on `ToolSpec` and MCP tool overrides, so
  ToolUseAgent gets selected-tool examples/defaults without a bloated global
  prompt or provider-specific Python branches.
- Added a local FastMCP Spotify wrapper and `spotify` capability pack with
  read-only `search`, `current_playback`, `recently_played`, and
  `list_playlists` tools.
- Added `jarvis auth connect <provider>` for explicit first-time OAuth setup,
  useful for local stdio wrappers that cannot trigger HTTP MCP auth on first
  use.
- Reworked deterministic planner fallback so it no longer routes provider tools
  by user-keyword matching. It now creates only generic safe fallback steps.
- Replaced Calendar/Gmail/Spotify-specific global planner prompt rules with
  generic tool-selection guidance and richer registered-tool catalog context.
- Added one model-backed planner repair attempt for invalid planner JSON or
  schema-invalid plans before falling back.
- Added `system.current_datetime` for current local date/time questions.
- Added planner validation for unsupported `$...` reference syntax so invented
  references can be repaired before execution.
- Added a lightweight non-executing eval harness for planner and ToolUseAgent
  quality, plus an example planner/tool-use smoke suite.
- Added the initial AgentProfile/AgentRuntime model substrate. Agent profiles
  now declare execution roles, and `[models.roles]` can route planner,
  ToolUseAgent, synthesis, and general language calls to different providers.

Current model behavior:

```text
CLI --model
  > settings mode
  > settings default_model
  > fake-local fallback
```

Example future config:

```toml
[models]
default = "ollama/llama3.2:3b"

[models.modes]
private = "ollama/llama3.2:3b"
fast = "ollama/llama3.2:3b"
accurate = "fake-local"
```

Why this should come before LLM-driven planning:

- It makes local-first usage pleasant.
- It establishes config precedence early.
- It gives future API providers a clean auth/config path.
- It avoids baking CLI-only assumptions into the runtime.

## Recommended Next Steps

1. Add the Gemini cloud model provider adapter as the first API model slice.
   Configure it through `[providers.gemini]`, register `gemini/<model>` entries
   through the model router, read the API key from `GEMINI_API_KEY`, call the
   Gemini Interactions API with `store = false`, and route
   `[models.roles].planner` to `gemini/gemini-3.5-flash` for eval comparison.
2. Expand the eval harness with canned local/API model comparison reports,
   repair counts, and optional end-to-end provider smoke suites gated behind
   explicit flags.
3. Add an auth profile init command so users do not hand-edit provider metadata.
4. Make MCP timeout budgets configurable per server and align stdio, OAuth
   refresh, and provider REST timeouts.
5. Strengthen synthesis grounding so final claims must be supported by
   structured tool results.
6. Try Google Calendar read-only tools beyond calendar listing, especially
   listing events and getting one event, using the generic ToolUseAgent path.
7. Add Gmail draft-only and send paths behind approval after read-only Gmail
   works reliably.
8. Add Spotify low-impact playback tools only after approval/confirmation UX is
   stronger. Playback mutation should not auto-run in the current one-shot CLI.
9. Add Google Drive read/search tools as another read-only capability pack.
10. Add dynamic MCP auth discovery and dynamic client registration if Google's
   remote MCP surface needs it.
11. Add encrypted token storage or OS keychain support before broader daily use.
12. Add resume/apply behavior for approved external or high-risk tool execution
   items.
13. Add trace filtering, timing, and basic metrics for benchmarking.
14. Expand plugin support with enable/disable state and clearer validation errors.
15. Add richer agent config files for specialists once prompt-only overrides feel
   too narrow.
16. Add named step outputs or richer workflow variables once `$last.text` becomes
   too narrow.
17. Add broader controlled tool-use loops only after one-pass tool execution is
   reliable. Current retries repair bad arguments and one read-only
   argument-like execution error; repeated search/refine loops can come later.
18. Add online plugin acquisition later as a separate installer layer.

## Near-Term Design Notes

Language generation should be an agent capability, not a provider tool
responsibility. For example, Gmail should send or draft an email, but an LLM
generalist or email specialist should compose the body from context first.

The intended pattern is:

```text
LLM agent generates or transforms language
  -> deterministic/read-write tool acts on external or local systems
  -> policy controls side effects
  -> trace records the confirmed result
```

This matters for MCP integrations. A demo echo server should only echo text; it
should not invent a fun fact. A Gmail MCP server should send or create drafts;
it should not own JarvisOS' drafting policy. JarvisOS should orchestrate those
steps explicitly.

Per-run config should not become the source of truth for auth. A normal user
flow should be:

```text
global auth profile says Google is connected
  -> run config enables google_calendar MCP tools
  -> ToolUseAgent creates valid tool arguments from schema
  -> integration layer refreshes tokens or raises a structured auth error
```

That split is now implemented at settings load time. The remaining work is UX:
make profile creation and provider connection comfortable, then keep per-run
configs focused on models, tools, prompts, and policy.

Common/default capability packs are now normal settings fragments. The default
config should not contain private client IDs or secrets, but it can declare
standard bundled tools and let the global auth profile decide whether the
provider is connected.

The near-term agent split should stay generic:

```text
orchestrator/planner: choose the next capability
ToolUseAgent/ArgumentAgent: fill or repair JSON arguments from schema
tool runtime: validate, enforce policy, execute, and trace
synthesis: combine confirmed results and failures into the final answer
```

For real-model runs, the orchestrator/planner choice should come from the model
reading available tool specs, schemas, capability metadata, and prompt guidance.
Deterministic code should catch bad plans and bad arguments; it should not
quietly force provider-specific tool choice through keyword routing.

The planner prompt should give enough context to be useful. Good context:
registered tool summaries, schema shape, capability metadata, risk level,
approval requirement, and short guidance for generic selection behavior. Bad
context: global provider-specific routing rules that say which Calendar, Gmail,
Spotify, or future Drive tool to pick for certain keywords. Provider-specific
details belong on the tool itself.

Every new tool or agent should ship with metadata good enough for another model
to understand. For tools, this means description, input schema, capability
metadata, risk/approval settings, short argument hints, and useful output
fields. For agents, this means purpose, allowed tools/domains, model mode,
risk/memory expectations, and output contract. If a planner needs code-level
special casing to use a new tool, the metadata is not done.

The next model strategy should be eval-driven. First measure planner/tool-use
quality across local models and prompts. Then add role-based model routing so a
stronger local or API model can handle planning and argument repair while a
cheaper/private model handles simpler language or synthesis tasks.

Domain specialists can come later when they add real value, such as selecting
which emails matter or interpreting calendar context. They should not be needed
just to make a valid tool call.
