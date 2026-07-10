# JarvisOS

JarvisOS is a terminal-first, model-agnostic personal agent orchestration
runtime. It is designed to coordinate user-configurable agents, tools, plugins,
MCP servers, memory, approval policies, and local or cloud models for personal
automation workflows.

The project starts small: a local CLI runtime built through runnable vertical
slices. Over time, JarvisOS should support useful default capability packs and
reference workflows while keeping the core runtime generic and configurable.

## Current Direction

- Build a simple command-based CLI first.
- Keep agents, tools, plugins, models, and policies discoverable from config.
- Ship useful defaults without hardcoding workflows into the orchestrator.
- Treat workflows like meeting prep as reference scenarios, not privileged core
  logic.
- Require approval before writes, sends, deletes, posts, purchases, bookings, or
  externally visible actions.
- Prefer deterministic validation, routing, policy, tracing, and storage.

See [AGENTS.md](AGENTS.md) for the living project context.
See [docs/architecture.md](docs/architecture.md) for the current package map.

## CLI Quickstart

The current implementation is intentionally small and dependency-free. It keeps
the architecture boundaries visible while using fake/local capabilities.

Run from the repository root:

```bash
PYTHONPATH=src python -m jarvis run "prepare me for my meeting tomorrow"
PYTHONPATH=src python -m jarvis run "prepare me for my meeting tomorrow" --json
PYTHONPATH=src python -m jarvis agents
PYTHONPATH=src python -m jarvis tools
PYTHONPATH=src python -m jarvis models
PYTHONPATH=src python -m jarvis settings
```

On PowerShell:

```powershell
$env:PYTHONPATH="src"
python -m jarvis run "prepare me for my meeting tomorrow"
```

## CLI Command Reference

All examples assume:

```powershell
$env:PYTHONPATH="src"
```

Use `--config` to point at a TOML settings file. If omitted, JarvisOS looks for
`jarvis.toml` and then `config/jarvis.toml`.

Run configs choose enabled models, capability packs, plugins, MCP servers,
prompts, policies, and state paths. Provider auth is shared: if a run config
does not define `[auth]`, JarvisOS looks for a global auth profile from
`JARVIS_AUTH_PROFILE`, `.jarvis/auth.toml`, `config/auth.toml`, `jarvis.toml`,
then `config/jarvis.toml`. This lets a capability pack or tool config enable
Calendar/Gmail tools without duplicating Google OAuth metadata.

### Run

```powershell
python -m jarvis run "prepare me for my meeting tomorrow"
python -m jarvis run "prepare me for my meeting tomorrow" --json
python -m jarvis run "prepare me for my meeting tomorrow" --model fake-local
python -m jarvis run "prepare me for my meeting tomorrow" --model "ollama/llama3.2:3b"
python -m jarvis run "prepare me for my meeting tomorrow" --mode private
python -m jarvis run "find notes about Jordan" --config jarvis.toml.example
python -m jarvis run "Prepare me for my meeting with Jordan tomorrow" --config jarvis.toml.example --model fake-local
python -m jarvis run "find notes about Jordan and summarize what you know" --config jarvis.toml.example --model "ollama/llama3.2:3b"
```

Options:

- `--json` prints the full structured run result, including plan, tool results,
  trace events, and status.
- `--model` manually selects a model provider for this run.
- `--mode` resolves a model from `[models.roles]`, then `[models.modes]`, then
  the default model when `--model` is omitted.
- `--config` loads a specific settings file.

When a real model such as Ollama is selected, JarvisOS asks the model for a JSON
tool plan, validates it against registered tools and agent permissions, then
executes the validated plan. If the model returns invalid JSON or unknown tools,
JarvisOS falls back to a deterministic safe plan.

Before execution, tool arguments pass through the model-backed ToolUseAgent. It
receives the user goal, current time, prior successful results, the selected
tool, the selected tool's input schema, and optional selected-tool
`argument_hints`. It returns JSON arguments, deterministic validation checks
them, and the agent can retry when validation fails. For explicit read-only,
low-risk tools, the orchestrator can also feed one argument-like execution
error back to ToolUseAgent for repair. Auth, token, permission, and timeout
failures are not retried as argument repairs. Deterministic code still resolves
`$last.text`, strips unsupported schema keys, applies policy, and fails cleanly
when valid arguments cannot be produced.

After tool execution, the synthesis agent asks the selected model to write an
answer-first final response from confirmed tool results. Normal `jarvis run`
output avoids runtime headings and successful-tool logs; detailed plan, tool,
and trace data stays available through `--json` and trace commands. If model
synthesis fails, returns runtime-shaped output, or makes an obvious unsupported
claim, JarvisOS falls back to a concise deterministic grounded answer.

`general.generate_text` is the first internal language capability. The planner
can use it to generate intermediate text, then pass that text into a following
tool with `$last.text`. This keeps drafting and language generation in the
model layer while provider tools such as MCP servers perform their concrete
actions.

Planner, ToolUseAgent, and synthesis prompts are loaded from bundled prompt
files by default. Users can override them from config without editing Python
code.

Model selection precedence:

```text
--model
> [models.roles], such as planner or tool_use
> [models.modes], such as private
> [models].default from settings
> fake-local fallback
```

### Inspect Runtime State

```powershell
python -m jarvis agents
python -m jarvis tools
python -m jarvis tools --config jarvis.toml.example
python -m jarvis tool call task.breakdown --args-json '{\"goal\":\"demo\"}' --config jarvis.toml.example
python -m jarvis models
python -m jarvis settings
python -m jarvis settings --config jarvis.toml.example
```

Use `jarvis tool call` to debug a registered tool directly, without planner or
synthesis noise. The command accepts only a JSON object in `--args-json`,
evaluates policy before execution, and prints the tool's `text` output when one
is available. Add `--json` to see the full `ToolResult`.

### Approvals

JarvisOS queues approval items when a run suggests durable memory or when a tool
requires explicit approval. Approval records are stored in SQLite.

```powershell
python -m jarvis run "Remember that I prefer meetings after 10 AM." --config jarvis.toml.example --model fake-local
python -m jarvis approvals list --config jarvis.toml.example
python -m jarvis approvals show <approval_id> --config jarvis.toml.example
python -m jarvis approvals approve <approval_id> --config jarvis.toml.example
python -m jarvis approvals reject <approval_id> --config jarvis.toml.example
```

Approving a `memory.add` item writes the memory to the configured memory store.
Approving a tool execution item records the decision; automatic tool resume is a
later workflow feature.

### Tasks

JarvisOS includes a low-risk local task write tool. `task.create` runs
automatically because it only writes to the configured local SQLite task store.
Task titles are lightly cleaned before storage, so command phrasing is removed
when possible.

```powershell
python -m jarvis run "Create a task to ask Jordan about API migration" --config jarvis.toml.example --model fake-local
python -m jarvis tasks list --config jarvis.toml.example
python -m jarvis tasks show <task_id> --config jarvis.toml.example
python -m jarvis tasks complete <task_id> --config jarvis.toml.example
```

## Canonical Local POC

This command exercises the current local-first vertical runtime:

```powershell
$env:PYTHONPATH="src"
python -m jarvis run "Prepare me for my meeting with Jordan tomorrow and create a task to ask Jordan about API migration" --config jarvis.toml.example --model "ollama/llama3.2:3b"
python -m jarvis tasks list --config jarvis.toml.example
python -m jarvis approvals list --config jarvis.toml.example
python -m jarvis traces list --config jarvis.toml.example
```

Use Ollama or another real model for the normal POC path. `--model fake-local`
is still available as a deterministic debug/test tool, but it should not be
treated as the standard runtime behavior.

The default runtime no longer ships a fake calendar reader. Calendar behavior
comes from configured MCP/plugin tools such as the local Google Calendar
FastMCP wrapper. Without a configured calendar tool, meeting/calendar requests
degrade to memory, notes, tasks, and summary steps instead of returning demo
events.

To prove model-generated text flowing into a tool, copy
`examples/mcp/demo.toml.example` to a local config such as `mcp-demo.toml`,
then run:

```powershell
python -m jarvis run "Generate a fun fact about JarvisOS and echo it with the demo MCP tool" --config mcp-demo.toml --model "ollama/llama3.2:3b"
```

### Traces

Every `jarvis run` stores a trace when `[traces].enabled = true`.

```powershell
python -m jarvis run "prepare me for my meeting tomorrow" --config jarvis.toml.example --model fake-local
python -m jarvis traces list --config jarvis.toml.example
python -m jarvis traces show <run_id> --config jarvis.toml.example
python -m jarvis traces show <run_id> --config jarvis.toml.example --json
```

Trace options:

- `traces list --limit N` controls how many recent runs are printed.
- `traces show <run_id>` prints a readable event timeline.
- `traces show <run_id> --json` prints the stored run and events as JSON.

Traces are useful for debugging, benchmarking, comparing model/tool behavior,
and understanding exactly what happened during a run.

Trace events include planning, policy checks, tool execution, synthesis source,
resolved tool arguments, and structured model-provider errors when fallbacks
are used.

### Planner And Tool-Use Evals

Eval suites let you compare planner and ToolUseAgent quality without executing
provider tools. A suite is a JSON file with `planner` cases for tool choice and
`tool_use` cases for schema-grounded argument construction.

```powershell
python -m jarvis evals run examples/evals/planner-tool-use.json --model "ollama/llama3.2:3b"
python -m jarvis evals run examples/evals/planner-tool-use.json --model "ollama/llama3.2:3b" --json
python -m jarvis evals run examples/evals/planner-coverage.json --config jarvis.toml --model "gemini/gemini-3.5-flash" --include-raw --json
python -m jarvis evals run examples/evals/planner-tool-use.json --model "ollama/llama3.2:3b" --json --include-raw
```

Planner cases score expected tools, forbidden tools, fallback use, and maximum
step count. Tool-use cases score required argument keys, forbidden argument
keys, and expected argument values. The harness does not call Gmail, Calendar,
Spotify, or other provider tools; it only evaluates model choices and generated
arguments against registered tool metadata. If the active config does not
enable a tool named by the suite, that case fails with an unknown-tool error.

Live provider evals can encounter quota or rate-limit responses. Treat those as
infrastructure results, not a measure of planner quality; use the JSON report
and raw output to distinguish them from an actual scoring failure.

### Memory

```powershell
python -m jarvis memory add "User prefers meetings after 10 AM." --type preference
python -m jarvis memory add "Jordan is on the API migration project." --type fact
python -m jarvis memory search "meeting preferences"
python -m jarvis memory list
python -m jarvis memory search "Jordan" --limit 10 --config jarvis.toml.example
```

Memory options:

- `--type` can be `preference`, `fact`, `note`, or `context`.
- `--source` records where the memory came from. The default is `manual`.
- `--limit` controls how many search/list results are printed.
- `--config` chooses which SQLite memory database to use.

Memory is durable for the configured SQLite database. It remains available
across terminal sessions until the database is removed or future delete/clear
commands are added and used.

Run tests:

```bash
PYTHONPATH=src python -m unittest discover -s tests
```

## Local Models With Ollama

Ollama does not need an API key for local use. JarvisOS discovers running Ollama
models from `http://localhost:11434/api/tags`.

Pull and run a model:

```powershell
ollama pull llama3.2:3b
ollama run llama3.2:3b
```

In another terminal:

```powershell
$env:PYTHONPATH="src"
python -m jarvis models
python -m jarvis run "prepare me for my meeting tomorrow" --model "ollama/llama3.2:3b"
python -m jarvis run "prepare me for my meeting tomorrow" --mode private
```

Optional environment variables:

```powershell
$env:OLLAMA_HOST="http://localhost:11434"
$env:OLLAMA_MODEL="llama3.2:3b"
```

## Model Settings

Copy `jarvis.toml.example` to `jarvis.toml` to set local defaults:

```powershell
Copy-Item jarvis.toml.example jarvis.toml
```

Model resolution is provider-agnostic:

```text
CLI --model
> settings role route, such as planner or tool_use
> settings mode, such as --mode private
> settings default model
> fake-local fallback
```

`jarvis.toml` can point at local and cloud providers without changing
orchestrator code.

Agent profiles declare an `execution_role`; they do not name providers directly.
The generic `AgentRuntime` wrapper resolves that role through the model router
and then calls the selected local or API provider. This keeps planner,
ToolUseAgent, synthesis, and future user-defined specialists on the same
substrate.

```toml
[models.roles]
planner = "ollama/llama3.2:3b"
tool_use = "ollama/llama3.2:3b"
synthesis = "ollama/llama3.2:3b"
general = "ollama/llama3.2:3b"
```

### Model API Auth vs Tool OAuth

JarvisOS has two separate auth paths:

- Model providers authenticate the model call itself. Local Ollama needs no API
  key. Gemini reads its API key from an environment variable through provider
  adapter settings; Anthropic, OpenAI, and Grok should follow the same pattern.
- Tool integrations authenticate access to user data or external accounts.
  Google Calendar, Gmail, and Spotify use OAuth provider metadata, local token
  storage, refresh tokens, and `jarvis auth connect`.

That split is intentional. Routing the planner to Gemini should not change
Google Calendar OAuth, and connecting Google Calendar should not decide which
model the planner uses. The role substrate keeps this separation intact.

Install the optional Gemini adapter and route only planning to it:

```powershell
uv pip install -e ".[gemini]"
$env:GEMINI_API_KEY = "your-key"
```

```toml
[models.roles]
planner = "gemini/gemini-3.5-flash"
tool_use = "ollama/llama3.2:3b"
synthesis = "ollama/llama3.2:3b"

[providers.gemini]
models = ["gemini-3.5-flash"]
api_key_env = "GEMINI_API_KEY"
timeout_seconds = 60
```

The Gemini adapter uses the Interactions API through `google-genai`, passes the
API key explicitly from `api_key_env`, and uses `store = false` so JarvisOS
remains the source of trace and memory history. A Gemini model is registered
only when it is listed in `[providers.gemini]` or referenced by `[models]`,
`[models.modes]`, or `[models.roles]`; merely setting an API key does not change
routing. Check the configured result with:

```powershell
python -m jarvis models --config jarvis.toml
python -m jarvis run "Summarize how JarvisOS works" --config jarvis.toml
```

## Capability Packs

Capability packs are built-in config fragments that expand into ordinary tools
or MCP servers. They are opt-in from `jarvis.toml`, and explicit
`[[mcp.servers]]` entries with the same name override the bundled default.

Enable the current Google Workspace and Spotify read-only packs after OAuth and
the optional MCP dependencies are configured:

```toml
[capabilities]
google_workspace = true
spotify = true
```

Then common commands can omit `--config`:

```powershell
python -m jarvis tools
python -m jarvis tool call google_calendar.list_calendars --args-json '{}' --json
python -m jarvis tool call gmail.list_recent --args-json '{"max_results":5}' --json
python -m jarvis tool call spotify.search --args-json '{"query":"Daft Punk","types":"track,artist","limit":5}' --json
python -m jarvis run "Use Calendar and Gmail to prep me for meetings this week" --model "ollama/llama3.2:3b"
```

The initial `google_workspace` pack enables the local FastMCP Calendar and Gmail
read wrappers. The `spotify` pack enables read-only Spotify search, current
playback, recently played, and playlist tools. Private OAuth metadata, client
secrets, and tokens still live in the global auth profile and environment
variables, not in tracked defaults.

## Prompt Settings

JarvisOS ships bundled prompt files for planning, tool use, and final synthesis:

```text
src/jarvis/prompts/planner.md
src/jarvis/prompts/tool_use.md
src/jarvis/prompts/synthesis.md
```

To customize them, point `[prompts]` at your own files:

```toml
[prompts]
planner = "prompts/planner.md"
tool_use = "prompts/tool_use.md"
synthesis = "prompts/synthesis.md"
```

Relative prompt paths are resolved from the config file location.

## Local Plugins

JarvisOS loads local plugin folders declared in `jarvis.toml`. Online plugin
support should eventually download or sync plugin folders locally first; the
runtime will load them through the same manifest path.

Try the included demo plugin:

```powershell
Copy-Item jarvis.toml.example jarvis.toml
$env:PYTHONPATH="src"
python -m jarvis tools --config jarvis.toml
python -m jarvis run "find notes about Jordan" --config jarvis.toml
```

Minimal plugin shape:

```text
my_plugin/
  plugin.toml
  tools.py
```

`plugin.toml` declares tools and points each tool at a Python handler:

```toml
name = "demo_notes"
description = "Demo local notes plugin."

[[tools]]
name = "notes.search"
description = "Search deterministic demo notes."
handler = "tools.search_notes"
risk_level = "low"
requires_approval = false
```

## MCP Tools

JarvisOS can load tools from local MCP stdio servers and remote streamable HTTP
MCP servers. MCP tools are normalized into the same internal tool registry as
built-ins and plugins, so the planner, policy engine, trace store, and synthesis
layer treat them the same way.

Example config:

```toml
[[mcp.servers]]
name = "demo_mcp"
command = "python"
args = ["examples/mcp/demo_server.py"]
risk_level = "low"
requires_approval = false
```

HTTP MCP servers use `transport = "http"` and `url`:

```toml
[[mcp.servers]]
name = "remote_example"
transport = "http"
url = "https://mcp.example.com/mcp"
risk_level = "low"
requires_approval = false
```

The hosted Google Calendar MCP endpoint is not a recommended JarvisOS path: it
returned permission failures even when direct Google Calendar REST access was
authorized. Use the built-in `google_workspace` capability pack instead, which
starts the local FastMCP Calendar and Gmail wrappers over the shared Google
OAuth profile.

When an HTTP MCP server has `auth_provider`, JarvisOS can start an OAuth
authorization-code + PKCE flow on first use. It prints and opens the provider
sign-in URL, listens on the configured local `redirect_uri`, stores the returned
tokens, and refreshes expired access tokens when a refresh token is available.
If the browser reports success but the terminal does not continue, paste the
final redirected URL or authorization code back into the waiting terminal prompt.

Manual bearer auth is still available as an escape hatch:

```powershell
$env:GOOGLE_MCP_ACCESS_TOKEN="<access-token>"
python -m jarvis auth set-token google "<access-token>" --config google-calendar.toml
python -m jarvis auth list --config google-calendar.toml
python -m jarvis auth debug google --config google-calendar.toml
python -m jarvis auth clear google --config google-calendar.toml
python -m jarvis auth connect spotify
```

The current auth layer stores provider tokens and supplies bearer headers to
HTTP MCP. The OAuth flow can be triggered by first use of a configured
authenticated HTTP MCP server, or explicitly with `jarvis auth connect
<provider>` for local stdio wrappers such as Google/Spotify FastMCP examples.
`auth debug` prints the auth profile path, redacted provider metadata, token
expiry, configured scopes, granted scopes when the provider exposes token-info,
and client-id matching details without printing access or refresh tokens.

Per-tool policy overrides can make read-only tools auto-allowed while writes
remain approval-gated:

```toml
[[mcp.servers.tools]]
name = "list_events"
argument_hints = "Use calendar_id = \"primary\" unless the user named a specific calendar."
risk_level = "low"
requires_approval = false

[[mcp.servers.tools]]
name = "create_event"
risk_level = "medium"
requires_approval = true
```

`argument_hints` are short, selected-tool-only instructions for ToolUseAgent.
They are useful for provider query syntax, conservative defaults, and examples
that should not bloat the global tool-use prompt.

Read-only MCP servers should usually be low risk and auto-allowed. Write,
send, post, playback, purchase, booking, or externally visible MCP tools should
be configured to require approval.

Local FastMCP wrappers are the preferred pattern when a provider's hosted MCP
server is unavailable, preview-gated, or not flexible enough. The wrapper runs
as a local stdio MCP server, owns provider-specific REST/API calls, and exposes
normal MCP tools back to JarvisOS. Install the optional MCP tooling with:

```powershell
uv pip install -e ".[mcp]"
```

You do not start stdio MCP servers manually. JarvisOS starts the configured
server process when it discovers tools or executes a tool call. The current MCP
client uses short-lived subprocess sessions, which is simple and reliable for
the POC; persistent MCP sessions can be added later if startup overhead matters.
For stdio servers, `command = "python"` resolves to the same Python interpreter
that is running JarvisOS, so venv-installed MCP dependencies are visible to the
subprocess.
JarvisOS uses newline-delimited JSON-RPC for stdio MCP, matching the current MCP
transport spec and FastMCP.

The local Google Calendar FastMCP example wraps Google Calendar REST reads while
still registering tools as `google_calendar.*`:

```powershell
Copy-Item examples/mcp/google-calendar-fastmcp.toml.example google-calendar-fastmcp.toml
python -m jarvis tools --config google-calendar-fastmcp.toml
python -m jarvis run "Use Google Calendar to list my calendars" --config google-calendar-fastmcp.toml
```

The Calendar config only enables the local MCP server. Google OAuth metadata and
the token database are resolved from the global auth profile, so the MCP config
does not need to point back at `jarvis.toml`. You can confirm the same path with:

```powershell
python -m jarvis auth debug google --config google-calendar-fastmcp.toml --json
python -m jarvis tool call google_calendar.list_calendars --args-json '{}' --config google-calendar-fastmcp.toml --json
```

The local Gmail FastMCP example wraps Gmail REST reads while registering tools
as `gmail.*`:

```powershell
Copy-Item examples/mcp/google-gmail-fastmcp.toml.example google-gmail-fastmcp.toml
python -m jarvis tools --config google-gmail-fastmcp.toml
python -m jarvis tool call gmail.list_recent --args-json '{"max_results":5}' --config google-gmail-fastmcp.toml --json
python -m jarvis run "Use Gmail to find recent emails from Jordan" --config google-gmail-fastmcp.toml --model "ollama/llama3.2:3b"
```

Available read-only Gmail tools are:

- `gmail.list_recent`
- `gmail.search_messages`
- `gmail.get_message`
- `gmail.get_thread`

For combined Calendar + Gmail prompts, use the Workspace example:

```powershell
Copy-Item examples/mcp/google-workspace-fastmcp.toml.example google-workspace-fastmcp.toml
python -m jarvis tools --config google-workspace-fastmcp.toml
python -m jarvis run "Use Calendar and Gmail to prep me for meetings this week" --config google-workspace-fastmcp.toml --model "ollama/llama3.2:3b"
```

For daily local use, prefer enabling `[capabilities].google_workspace = true` in
`jarvis.toml`. The standalone Calendar/Gmail/Workspace configs are still useful
for isolated debugging or for overriding the bundled defaults.

The local Spotify FastMCP example wraps Spotify Web API reads while registering
tools as `spotify.*`:

```powershell
Copy-Item examples/mcp/spotify-fastmcp.toml.example spotify-fastmcp.toml
python -m jarvis tools --config spotify-fastmcp.toml
python -m jarvis tool call spotify.search --args-json '{"query":"Daft Punk","types":"track,artist","limit":5}' --config spotify-fastmcp.toml --json
python -m jarvis run "Search Spotify for Daft Punk tracks" --config spotify-fastmcp.toml --model "ollama/llama3.2:3b"
```

Available read-only Spotify tools are:

- `spotify.search`
- `spotify.current_playback`
- `spotify.recently_played`
- `spotify.list_playlists`

For daily local use, enable `[capabilities].spotify = true` in `jarvis.toml`
after Spotify OAuth is configured.

Minimal Spotify auth profile:

```toml
[[auth.oauth_providers]]
name = "spotify"
client_id = "replace-with-spotify-client-id"
authorization_url = "https://accounts.spotify.com/authorize"
token_url = "https://accounts.spotify.com/api/token"
redirect_uri = "http://127.0.0.1:8765/oauth/callback"
scopes = [
  "user-read-playback-state",
  "user-read-currently-playing",
  "user-read-recently-played",
  "playlist-read-private",
]
```

Then connect once:

```powershell
python -m jarvis auth connect spotify
python -m jarvis auth debug spotify --json
```

Try the demo server:

```powershell
$env:PYTHONPATH="src"
python -m jarvis tools --config mcp-demo.toml
python -m jarvis run "call demo mcp echo" --config mcp-demo.toml --model fake-local
python -m jarvis run "Generate a fun fact about JarvisOS and echo it with the demo MCP tool" --config mcp-demo.toml --model "ollama/llama3.2:3b"
```

The echo server only echoes input. If the run produces a new fun fact or draft,
that text came from `general.generate_text`; the MCP tool then acted on the
generated text.

See `docs/integrations/google-workspace-mcp.md` for the current Google
Workspace MCP notes.

## Local Memory

JarvisOS has a small SQLite-backed memory store. Manual memory commands are real;
automatic extraction is currently suggest-only and does not silently write
memories.
Approved memory writes skip obvious normalized duplicates.
Set `enabled = false` to remove memory from normal runtime runs while keeping
the SQLite data and manual `jarvis memory` commands available.

```powershell
$env:PYTHONPATH="src"
python -m jarvis memory add "User prefers meetings after 10 AM." --type preference
python -m jarvis memory search "meeting preferences"
python -m jarvis memory list
python -m jarvis run "What are my meeting preferences?" --model fake-local
```

Configure the database path in `jarvis.toml`:

```toml
[memory]
database_path = ".jarvis/memory.sqlite3"
enabled = true
auto_extract = true
auto_write = false

[tasks]
database_path = ".jarvis/tasks.sqlite3"

[traces]
database_path = ".jarvis/traces.sqlite3"
enabled = true

[approvals]
database_path = ".jarvis/approvals.sqlite3"
```
