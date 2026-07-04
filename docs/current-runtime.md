# Current Runtime

This document describes what the runtime does right now. It should be updated
whenever a slice changes the shape of execution.

## Current Flow

```text
CLI command
  -> default runtime factory
  -> orchestrator
  -> planner
  -> bundled or user-configured planner prompt
  -> selected model provider for optional LLM planning
  -> deterministic plan validation or fallback
  -> deterministic policy checks
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
- `memory.search` uses a local SQLite-backed memory store.
- `jarvis memory add/search/list` manage local memory records.
- Memory records persist across sessions for the configured SQLite database.
- Run traces persist to SQLite when `[traces].enabled = true`.
- `jarvis traces list/show` inspect stored runs and event timelines.
- End-of-run memory extraction is suggest-only and does not auto-save memories.
- The JSON output includes trace events, tool results, plan steps, and final
  status.

## What Is Still Mocked

- Calendar search is still a deterministic demo tool. It returns a sample
  Jordan meeting for meeting-prep smoke tests and placeholder output otherwise.
- Deterministic synthesis is still simple, but it includes grounded lines from
  actual tool outputs and acts as the fallback path.
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
- `traces show --json` prints a stored run trace as JSON.
