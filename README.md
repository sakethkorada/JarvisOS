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
```

Options:

- `--json` prints the full structured run result, including plan, tool results,
  trace events, and status.
- `--model` manually selects a model provider for this run.
- `--mode` resolves a model from `[models.modes]` in settings.
- `--config` loads a specific settings file.

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

## Local Memory

JarvisOS has a small SQLite-backed memory store. Manual memory commands are real;
automatic extraction is currently suggest-only and does not silently write
memories.

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
```
