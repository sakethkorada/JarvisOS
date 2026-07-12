# Current Runtime

This document describes what the runtime does right now. It should be updated
whenever a slice changes the shape of execution.

See [docs/prompt-cookbook.md](prompt-cookbook.md) for runnable current goals.

See `docs/architecture.md` for the current package ownership map.

## Cloud Models

Gemini is available through the normal `ModelProvider` and `ModelRouter` path.
Install `uv pip install -e ".[gemini]"`, set `GEMINI_API_KEY`, configure
`[providers.gemini]`, then route a role to `gemini/<model>`. The adapter uses
the Gemini Interactions API with `store = false`; it does not affect Google or
Spotify OAuth. Use `python -m jarvis models --config <path>` to inspect the
models a specific config registers.

## Current Flow

```text
CLI command
  -> default runtime factory
  -> orchestrator
  -> planner
  -> graph validation and deterministic dependency ordering
  -> planner AgentRuntime resolves execution_role=planner
  -> bundled or user-configured planner prompt
  -> selected model provider for optional LLM planning
  -> deterministic plan validation or fallback
  -> step argument reference resolution
  -> deterministic policy checks
  -> approval queue for blocked actions or memory candidates
  -> local/plugin/MCP tool execution
  -> synthesis agent
  -> synthesis AgentRuntime resolves execution_role=synthesis
  -> bundled or user-configured synthesis prompt
  -> selected model provider for optional LLM synthesis
  -> deterministic synthesis fallback if needed
  -> trace events
  -> final response
```

Evaluation runs use a narrower non-executing path:

```text
eval suite JSON
  -> planner case: Planner + tool catalog + model
  -> tool_use case: ToolUseAgent + selected ToolSpec + model
  -> deterministic scorer
  -> structured eval report
```

## What Is Real

- `jarvis run "<goal>"` executes through the orchestrator.
- Non-fake models can propose JSON execution plans using registered tools.
- LLM plans are validated before execution and fall back to deterministic
  planning if invalid.
- Accepted plans are validated as dependency graphs. The current executor runs
  the graph sequentially in deterministic topological order, and optional
  SQLite run checkpoints record plan progress, results, status, and trace length
  for later resume/replay work.
- Planner, ToolUseAgent, and synthesis prompts load from bundled markdown files,
  with optional config overrides.
- Agent profiles now carry execution metadata such as `execution_role`,
  output contract, memory scope, and risk permissions. Model-backed built-ins
  use a generic `AgentRuntime` wrapper to resolve role/mode/provider routing
  before calling the model router.
- `[models.roles]` can route planner, ToolUseAgent, synthesis, and general
  language generation to different providers. CLI `--model` remains a global
  override for one run.
- Configured MCP stdio and HTTP servers can expose tools into the shared
  ToolRegistry.
- Local FastMCP wrappers can be configured as stdio MCP servers. Current
  examples wrap Google Calendar, Gmail, and Spotify read tools.
- MCP tool `inputSchema` values are preserved on `ToolSpec`, exposed to the
  planner, and used for conservative argument cleanup before execution.
- Successful tool results expose `text`, `records`, `ids`, and `metadata`.
  Raw MCP protocol payloads remain trace/debug data and are excluded from
  synthesis context; local Calendar and Gmail wrappers emit structured records.
- Tool-local `argument_hints` can be attached through MCP tool overrides or
  bundled capability packs. ToolUseAgent receives only the selected tool's hints
  when building JSON arguments.
- `ToolSpec` can carry capability metadata such as calendar/email/music domain,
  operation, provider, read-only status, and demo status. This metadata is
  exposed to the planner and ToolUseAgent so real-model runs can choose and call
  tools from explicit contracts rather than hidden provider rules.
- The deterministic fallback planner is now minimal. It does not inspect user
  wording to select Calendar, Gmail, Spotify, notes, echo, or task tools. It
  only creates generic safe steps such as memory search and a lightweight
  summary when those tools are registered.
- The planner receives a rich tool catalog with names, descriptions, risk,
  approval requirements, capability metadata, input schemas, and concise
  per-tool hints as context. Provider-specific selection should come from that
  registered tool metadata and the model's plan, not Python keyword routing.
- If a model returns invalid planner JSON or a schema-invalid plan, JarvisOS
  gives the planner one model-backed repair attempt with the validation error
  and previous output before falling back to the minimal safe plan.
- Tool arguments flow through a model-backed ToolUseAgent before execution for
  non-`fake-local` runs. It receives the goal, current time, prior successful
  results, tool metadata, argument hints, and input schema, then returns JSON
  arguments.
  Deterministic code resolves references, validates schemas, retries model
  repair on validation errors, strips unsupported schema keys, and fails cleanly
  when valid arguments cannot be produced.
- When an explicit read-only, low-risk tool fails with an argument-like
  execution error, the orchestrator can feed the failed arguments and error back
  to ToolUseAgent for one repair attempt. Auth, token, permission, timeout,
  approval-required, medium/high-risk, and ambiguous side-effect failures are
  not retried automatically.
- `jarvis tool call <tool_name> --args-json "{}"` executes one registered tool
  directly after policy evaluation, which makes adapter debugging possible
  without planner or synthesis behavior in the way.
- `jarvis evals run <suite.json>` runs isolated planner and ToolUseAgent eval
  cases without executing provider tools. The initial scorer checks expected
  tool choices, forbidden tools, fallback usage, maximum step count, required
  argument keys, forbidden argument keys, and expected argument values.
  Eval construction is hermetic by default; use
  `--allow-live-integrations` only for explicit model/plugin/MCP discovery.
- `general.generate_text` can generate intermediate text with the selected
  model before another tool uses it.
- `system.current_datetime` can answer current local date/time questions and
  gives the planner a safe runtime-context tool instead of relying on model
  guesses about today's date.
- Plan steps can pass the previous successful tool result with `$last.<field>`
  or a named successful step result with `$step.<id>.<field>`.
- Planner validation rejects unsupported reference syntax such as `$result.text`
  and gives the model one repair attempt. Supported references are
  `$last.<field>` and `$step.<id>.<field>`.
- The synthesis agent can use the selected model to write the final answer from
  confirmed tool results.
- Normal `jarvis run` output is answer-first. It avoids routine runtime
  headings such as completed tool lists or grounded-results dumps; detailed
  plan/tool/trace data remains available through `--json`, `jarvis traces`, and
  direct `jarvis tool call`.
- Synthesis falls back to concise deterministic grounded output if the model
  fails, returns empty text, produces runtime-shaped output, or makes obvious
  unsupported claims.
- Model provider failures are wrapped in structured runtime errors before being
  recorded in traces or converted into fallback behavior.
- `jarvis agents`, `jarvis tools`, and `jarvis models` list registered defaults.
- `fake-local` is available for deterministic tests and debugging. When no
  model is configured and Ollama models are discoverable, the default router
  prefers a discovered Ollama model over `fake-local`.
- Ollama models are discovered from `OLLAMA_HOST` or `http://localhost:11434`.
- `--model` can select a provider such as `ollama/llama3.2:3b`.
- `jarvis.toml` can set a default model, mode-specific model choices, and
  role-specific model choices.
- `jarvis.toml` can enable built-in capability packs. The current
  `google_workspace` pack expands to the local FastMCP Google Calendar and
  Gmail read-only MCP servers; the `spotify` pack expands to the local FastMCP
  Spotify read-only MCP server. Daily use can omit a special `--config` once
  provider OAuth is configured.
- Local plugin folders can be loaded from configured plugin paths.
- MCP server tools can be loaded from `[[mcp.servers]]` config entries.
- Explicit `[[mcp.servers]]` entries override bundled capability-pack servers
  with the same name, which keeps local experiments and custom wrappers
  possible.
- MCP tools can override server-level risk and approval settings with
  `[[mcp.servers.tools]]`.
- Tool execution strips arguments that are not declared by a tool's object
  input schema and fails cleanly when required schema fields are missing.
- HTTP MCP tools can receive bearer tokens from an environment variable or the
  local SQLite auth store.
- Authenticated HTTP MCP tools can trigger OAuth authorization-code + PKCE on
  first use, then store and reuse the returned tokens.
- Expired access tokens are refreshed when a refresh token is available.
- Run configs can inherit auth provider metadata and token storage from a
  global auth profile. JarvisOS checks `JARVIS_AUTH_PROFILE`,
  `.jarvis/auth.toml`, `config/auth.toml`, `jarvis.toml`, and
  `config/jarvis.toml` when the run config does not explicitly define auth.
- `jarvis auth list/set-token/debug/clear` manages and inspects stored provider
  access tokens without printing token values.
- `jarvis auth connect <provider>` runs the configured OAuth authorization-code
  + PKCE flow and stores the resulting token without printing token values.
- `memory.search` uses a local SQLite-backed memory store when
  `[memory].enabled = true`.
- `jarvis memory add/search/list` manage local memory records.
- `task.create` writes low-risk local tasks to SQLite without approval.
- `jarvis tasks list/show/complete` manage local tasks.
- Memory records persist across sessions for the configured SQLite database.
- Run traces persist to SQLite when `[traces].enabled = true`.
- `jarvis traces list/show` inspect stored runs and event timelines.
- `jarvis approvals list/show/approve/reject` manage pending approvals.
- Suggested memory candidates are queued for approval instead of being saved
  silently.
- Approved `memory.add` records are written to the configured memory store.
- Approved `memory.add` records skip obvious normalized duplicates.
- End-of-run memory extraction is suggest-only and does not auto-save memories.
- Setting `[memory].enabled = false` removes memory search and automatic memory
  suggestions from runtime runs without deleting stored records or disabling
  manual memory CLI commands.
- The JSON output includes trace events, tool results, plan steps, and final
  status.

## What Is Still Mocked

- Calendar reads are not mocked in the default runtime. Calendar behavior now
  requires configured MCP/plugin tools such as the local Google Calendar
  FastMCP wrapper.
- Tasks are local SQLite records, not synced to an external task app yet.
- MCP support covers stdio and basic streamable HTTP tool discovery/calls.
  MCP resources, prompts, full SSE streaming, and long-lived sessions can come
  later.
- Stdio MCP servers are started automatically as short-lived subprocesses during
  tool discovery and tool execution; users do not run them manually.
- For stdio MCP config, `command = "python"` resolves to the interpreter running
  JarvisOS so virtualenv dependencies are shared with local MCP subprocesses.
- Stdio MCP uses newline-delimited JSON-RPC. Legacy Content-Length responses can
  still be read for compatibility, but JarvisOS sends newline-delimited MCP
  messages.
- The local Google Calendar FastMCP wrapper currently exposes read-only list
  tools. It depends on optional FastMCP dependencies and an existing JarvisOS
  Google OAuth token. Its example MCP config no longer needs to pass
  `--config jarvis.toml`; the wrapper can use the global auth profile.
- The local Gmail FastMCP wrapper exposes read-only `list_recent`,
  `search_messages`, `get_message`, and `get_thread` tools through the same
  global Google auth profile.
- The local Spotify FastMCP wrapper exposes read-only `search`,
  `current_playback`, `recently_played`, and `list_playlists` tools through a
  global Spotify auth profile.
- OAuth support includes provider metadata, local callback handling,
  authorization-code + PKCE, token exchange, and refresh-token renewal.
  Dynamic MCP auth discovery, dynamic client registration, and encrypted token
  storage can come later.
- Deterministic synthesis is still simple, but it now produces user-facing
  grounded lines from actual tool outputs instead of debug-style run summaries.
- `general.generate_text` is the first model-backed internal language
  capability. Specialist prompt/config layers can become richer later.
- Step data flow supports `$last.<path>` and named `$step.<id>.<path>`
  references, including safe numeric record indexes such as
  `$step.find.records[0].id`.
- The graph is currently executed sequentially. `jarvis runs resume <run_id>`
  reconstructs the latest checkpoint and continues only never-attempted nodes
  whose dependencies succeeded. `--dry-run` displays replay-protected,
  eligible, and blocked nodes without executing them.
- Tool execution approvals can be recorded, but approved external tool calls
  are not automatically replayed. Previously attempted nodes remain
  replay-protected until explicit idempotent retry support exists.
- Online plugin acquisition is not implemented yet. Future online plugins should
  be downloaded into local plugin folders before runtime loading.

## Known Design Pressure

- Resume now protects every attempted node from replay. The next lifecycle
  pressure is explicit idempotency support for opt-in retries of external
  actions, plus richer approval continuation.
- Approved external tool calls still require an explicit apply/idempotent retry
  path.
- Independent graph nodes are ordered deterministically but not yet concurrent;
  add bounded concurrency only after restart and cancellation tests exist.
- MCP sessions are still short-lived for stdio calls and HTTP lifecycle support
  remains intentionally basic. Revisit session reuse when repeated/concurrent
  graph calls create measurable pressure.
- Auth profile initialization, encrypted token storage, dynamic MCP auth
  discovery, and online plugin acquisition remain future work.
- Provider-specific behavior must stay in adapters and metadata. Missing auth,
  unavailable tools, expired tokens, quotas, and timeouts must remain structured
  runtime/evaluation failures rather than planner branches.

## Useful Commands

```powershell
$env:PYTHONPATH="src"
python -m jarvis models
python -m jarvis settings
python -m jarvis memory add "User prefers meetings after 10 AM." --type preference
python -m jarvis memory search "meeting preferences"
python -m jarvis tools --config jarvis.toml.example
python -m jarvis run "find notes about Jordan" --config jarvis.toml.example
python -m jarvis run "find notes about Jordan and summarize what you know" --config jarvis.toml.example --model "ollama/llama3.2:3b"
python -m jarvis traces list --config jarvis.toml.example
python -m jarvis traces show <run_id> --config jarvis.toml.example
python -m jarvis approvals list --config jarvis.toml.example
python -m jarvis approvals show <approval_id> --config jarvis.toml.example
python -m jarvis auth list --config jarvis.toml.example
python -m jarvis auth connect spotify
python -m jarvis auth debug google --config google-calendar.toml
python -m jarvis auth set-token google "<access-token>" --config google-calendar.toml
python -m jarvis auth clear google --config google-calendar.toml
python -m jarvis tools --config google-calendar-fastmcp.toml
python -m jarvis tool call google_calendar.list_events --args-json '{\"calendar_id\":\"primary\"}' --config google-calendar-fastmcp.toml --json
python -m jarvis run "Use Google Calendar to list my calendars" --config google-calendar-fastmcp.toml
python -m jarvis tools --config google-gmail-fastmcp.toml
python -m jarvis tool call gmail.list_recent --args-json '{\"max_results\":5}' --config google-gmail-fastmcp.toml --json
python -m jarvis run "Use Gmail to find recent emails from Jordan" --config google-gmail-fastmcp.toml --model "ollama/llama3.2:3b"
python -m jarvis tools --config spotify-fastmcp.toml
python -m jarvis tool call spotify.search --args-json '{\"query\":\"Daft Punk\",\"types\":\"track,artist\",\"limit\":5}' --config spotify-fastmcp.toml --json
python -m jarvis tools
python -m jarvis tool call google_calendar.list_calendars --args-json '{}' --json
python -m jarvis tool call gmail.list_recent --args-json '{\"max_results\":5}' --json
python -m jarvis tool call spotify.search --args-json '{\"query\":\"Daft Punk\",\"types\":\"track,artist\",\"limit\":5}' --json
python -m jarvis evals run examples/evals/planner-tool-use.json --model "ollama/llama3.2:3b"
python -m jarvis evals run examples/evals/planner-tool-use.json --model "ollama/llama3.2:3b" --json
python -m jarvis run "Create a task to ask Jordan about API migration" --config jarvis.toml.example --model fake-local
python -m jarvis tasks list --config jarvis.toml.example
python -m jarvis tasks complete <task_id> --config jarvis.toml.example
python -m jarvis run "prepare me for my meeting tomorrow" --model "ollama/llama3.2:3b"
python -m jarvis run "prepare me for my meeting tomorrow" --mode private
python -m jarvis run "prepare me for my meeting tomorrow" --model "ollama/llama3.2:3b" --json
python -m jarvis run "Generate a fun fact about JarvisOS and echo it with the demo MCP tool" --config mcp-demo.toml --model "ollama/llama3.2:3b"
python -m unittest discover -s tests
```

## CLI Options

- `--config` loads a specific TOML settings file.
- `--json` prints the full structured run result.
- `--model` overrides model selection for one run.
- `--mode` resolves a model from settings after role-specific routes.
- `tool call <tool_name> --args-json "{}"` executes one registered tool after
  deterministic policy checks.
- `evals run <suite.json>` scores planner/tool-use behavior without executing
  provider tools.
- `evals run --include-raw --json` includes raw model outputs in the structured
  eval report for prompt/model debugging.
- `--type`, `--source`, and `--limit` configure memory commands.
- `traces show --json` prints a stored run trace as JSON.
- `approvals approve <approval_id>` applies supported approved items.
- `auth set-token <provider> <token>` stores an access token for HTTP
  integrations.
- `auth list` shows which providers have stored tokens without printing secret
  values.
- `auth debug <provider>` shows redacted token metadata, token-info scope
  checks when available, and client-id matching without printing token values.
- `auth clear <provider>` deletes a stored token.
- `tasks list --limit N` prints recent local tasks.
- `tasks show <task_id>` prints one task.
- `tasks complete <task_id>` marks one task done.
