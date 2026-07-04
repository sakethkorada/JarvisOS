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
- `--mode` resolves a model from `[models.modes]` in settings.
- `--config` loads a specific settings file.

When a real model such as Ollama is selected, JarvisOS asks the model for a JSON
tool plan, validates it against registered tools and agent permissions, then
executes the validated plan. If the model returns invalid JSON or unknown tools,
JarvisOS falls back to a deterministic safe plan.

After tool execution, the synthesis agent asks the selected model to write the
final response from confirmed tool results. If model synthesis fails or makes an
obvious unsupported claim, JarvisOS falls back to deterministic grounded output.

`general.generate_text` is the first internal language capability. The planner
can use it to generate intermediate text, then pass that text into a following
tool with `$last.text`. This keeps drafting and language generation in the
model layer while provider tools such as MCP servers perform their concrete
actions.

Planner and synthesis prompts are loaded from bundled prompt files by default.
Users can override them from config without editing Python code.

Model selection precedence:

```text
--model
> --mode from settings
> [models].default from settings
> fake-local fallback
```

### Inspect Runtime State

```powershell
python -m jarvis agents
python -m jarvis tools
python -m jarvis tools --config jarvis.toml.example
python -m jarvis models
python -m jarvis settings
python -m jarvis settings --config jarvis.toml.example
```

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

Use `--model fake-local` for deterministic smoke checks. Use Ollama for the
LLM planner and synthesis path.

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
> settings mode, such as --mode private
> settings default model
> fake-local fallback
```

`jarvis.toml` can point at local providers now and cloud providers later without
changing orchestrator code.

## Prompt Settings

JarvisOS ships bundled prompt files for planning and final synthesis:

```text
src/jarvis/prompts/planner.md
src/jarvis/prompts/synthesis.md
```

To customize them, point `[prompts]` at your own files:

```toml
[prompts]
planner = "prompts/planner.md"
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
name = "google_calendar"
transport = "http"
url = "https://calendarmcp.googleapis.com/mcp/v1"
auth_provider = "google"
bearer_token_env = "GOOGLE_MCP_ACCESS_TOKEN"
risk_level = "medium"
requires_approval = true
```

Bearer auth can come from an environment variable or the local auth store:

```powershell
$env:GOOGLE_MCP_ACCESS_TOKEN="<access-token>"
python -m jarvis auth set-token google "<access-token>" --config google-calendar.toml
python -m jarvis auth list --config google-calendar.toml
python -m jarvis auth clear google --config google-calendar.toml
```

The current auth layer stores provider tokens and supplies bearer headers to
HTTP MCP. Full browser OAuth authorization-code flow and refresh handling are
the next auth slice.

Per-tool policy overrides can make read-only tools auto-allowed while writes
remain approval-gated:

```toml
[[mcp.servers.tools]]
name = "list_events"
risk_level = "low"
requires_approval = false

[[mcp.servers.tools]]
name = "create_event"
risk_level = "medium"
requires_approval = true
```

Read-only MCP servers should usually be low risk and auto-allowed. Write,
send, post, playback, purchase, booking, or externally visible MCP tools should
be configured to require approval.

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
