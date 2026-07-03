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

## First Barebones Runtime

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
