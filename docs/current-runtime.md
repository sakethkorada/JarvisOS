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
- The JSON output includes trace events, tool results, plan steps, and final
  status.

## What Is Still Mocked

- Memory search returns a placeholder result.
- Calendar search returns a placeholder result.
- The execution plan is created by deterministic code.
- The final response is created by deterministic code.
- The LLM response is recorded in the trace but does not yet decide the plan,
  choose tools, or synthesize the final answer.

## Useful Commands

```powershell
$env:PYTHONPATH="src"
python -m jarvis models
python -m jarvis settings
python -m jarvis run "prepare me for my meeting tomorrow" --model "ollama/llama3.2:3b"
python -m jarvis run "prepare me for my meeting tomorrow" --mode private
python -m jarvis run "prepare me for my meeting tomorrow" --model "ollama/llama3.2:3b" --json
python -m unittest discover -s tests
```
