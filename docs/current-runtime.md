# Current Runtime

This document describes what the runtime does right now. It should be updated
whenever a slice changes the shape of execution.

## Current Flow

```text
CLI command
  -> default runtime factory
  -> orchestrator
  -> model router
  -> selected model provider
  -> deterministic plan creation
  -> deterministic policy checks
  -> mock/local tool execution
  -> trace events
  -> code-generated final response
```

## What Is Real

- `jarvis run "<goal>"` executes through the orchestrator.
- `jarvis agents`, `jarvis tools`, and `jarvis models` list registered defaults.
- `fake-local` is always available for deterministic tests.
- Ollama models are discovered from `OLLAMA_HOST` or `http://localhost:11434`.
- `--model` can select a provider such as `ollama/llama3.2:3b`.
- `jarvis.toml` can set a default model and mode-specific model choices.
- Local plugin folders can be loaded from configured plugin paths.
- `memory.search` uses a local SQLite-backed memory store.
- `jarvis memory add/search/list` manage local memory records.
- Memory records persist across sessions for the configured SQLite database.
- End-of-run memory extraction is suggest-only and does not auto-save memories.
- The JSON output includes trace events, tool results, plan steps, and final
  status.

## What Is Still Mocked

- Calendar search returns a placeholder result.
- The execution plan is created by deterministic code.
- The final response is created by deterministic code.
- The LLM response is recorded in the trace but does not yet decide the plan,
  choose tools, or synthesize the final answer.
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
python -m jarvis run "prepare me for my meeting tomorrow" --model "ollama/llama3.2:3b"
python -m jarvis run "prepare me for my meeting tomorrow" --mode private
python -m jarvis run "prepare me for my meeting tomorrow" --model "ollama/llama3.2:3b" --json
python -m unittest discover -s tests
```

## CLI Options

- `--config` loads a specific TOML settings file.
- `--json` prints the full structured run result.
- `--model` overrides model selection for one run.
- `--mode` resolves a model from settings.
- `--type`, `--source`, and `--limit` configure memory commands.
