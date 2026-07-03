# Next Steps

## Completed Slices

- Added the barebones runtime and CLI.
- Added provider-agnostic model settings.
- Added local plugin loading.
- Added SQLite-backed memory with manual commands.
- Added suggest-only memory extraction.

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

1. Add SQLite trace persistence so runs can be inspected after completion.
2. Add approval flow for suggested memory candidates before enabling any
   automatic writes.
3. Expand plugin support with enable/disable state and clearer validation errors.
4. Move from deterministic plan creation to optional LLM-assisted planning.
5. Keep deterministic validation after any LLM-generated plan.
6. Add online plugin acquisition later as a separate installer layer.
