# Next Steps

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

1. Add approval flow for suggested memory candidates before enabling any
   automatic writes.
2. Add trace filtering, timing, and basic metrics for benchmarking.
3. Expand plugin support with enable/disable state and clearer validation errors.
4. Add richer agent config files for specialists once prompt-only overrides feel
   too narrow.
5. Add online plugin acquisition later as a separate installer layer.
