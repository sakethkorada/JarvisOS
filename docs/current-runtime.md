# Current Runtime

This document describes what the runtime does right now. It should be updated
whenever a slice changes the shape of execution.

See `docs/architecture.md` for the current package ownership map.

## Current Flow

```text
CLI command
  -> default runtime factory
  -> orchestrator
  -> planner
  -> bundled or user-configured planner prompt
  -> selected model provider for optional LLM planning
  -> deterministic plan validation or fallback
  -> step argument reference resolution
  -> deterministic policy checks
  -> approval queue for blocked actions or memory candidates
  -> mock/local tool execution
  -> synthesis agent
  -> bundled or user-configured synthesis prompt
  -> selected model provider for optional LLM synthesis
  -> deterministic synthesis fallback if needed
  -> trace events
  -> final response
```

## What Is Real

- `jarvis run "<goal>"` executes through the orchestrator.
- Non-fake models can propose JSON execution plans using registered tools.
- LLM plans are validated before execution and fall back to deterministic
  planning if invalid.
- Planner and synthesis prompts load from bundled markdown files, with optional
  config overrides.
- Configured MCP stdio servers can expose tools into the shared ToolRegistry.
- `general.generate_text` can generate intermediate text with the selected
  model before another tool uses it.
- Plan steps can pass the previous successful tool result's `text` field with
  `$last.text`.
- The synthesis agent can use the selected model to write the final answer from
  confirmed tool results.
- Synthesis falls back to deterministic grounded output if the model fails,
  returns empty text, or makes obvious unsupported claims.
- Model provider failures are wrapped in structured runtime errors before being
  recorded in traces or converted into fallback behavior.
- `jarvis agents`, `jarvis tools`, and `jarvis models` list registered defaults.
- `fake-local` is always available for deterministic tests.
- Ollama models are discovered from `OLLAMA_HOST` or `http://localhost:11434`.
- `--model` can select a provider such as `ollama/llama3.2:3b`.
- `jarvis.toml` can set a default model and mode-specific model choices.
- Local plugin folders can be loaded from configured plugin paths.
- MCP server tools can be loaded from `[[mcp.servers]]` config entries.
- MCP tools can override server-level risk and approval settings with
  `[[mcp.servers.tools]]`.
- `memory.search` uses a local SQLite-backed memory store.
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
- The JSON output includes trace events, tool results, plan steps, and final
  status.

## What Is Still Mocked

- Calendar search is still a deterministic demo tool. It returns a sample
  Jordan meeting for meeting-prep smoke tests and placeholder output otherwise.
- Tasks are local SQLite records, not synced to an external task app yet.
- MCP support currently covers stdio tool discovery/calls. HTTP transports,
  resources, prompts, and long-lived sessions can come later.
- Official Google Workspace MCP servers are HTTP/OAuth-based, so JarvisOS needs
  HTTP MCP transport and OAuth handling before using them directly.
- Deterministic synthesis is still simple, but it includes grounded lines from
  actual tool outputs and acts as the fallback path.
- `general.generate_text` is the first model-backed internal language
  capability. Specialist prompt/config layers can become richer later.
- Step data flow only supports a minimal `$last.text` reference today. Richer
  workflow variables, named outputs, and dependency graphs can come later.
- Tool execution approvals can be recorded, but approved tool calls are not
  automatically resumed yet.
- Online plugin acquisition is not implemented yet. Future online plugins should
  be downloaded into local plugin folders before runtime loading.

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
- `--mode` resolves a model from settings.
- `--type`, `--source`, and `--limit` configure memory commands.
- `traces show --json` prints a stored run trace as JSON.
- `approvals approve <approval_id>` applies supported approved items.
- `tasks list --limit N` prints recent local tasks.
- `tasks show <task_id>` prints one task.
- `tasks complete <task_id>` marks one task done.
