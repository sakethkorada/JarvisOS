# Next Steps

## Completed Immediate Slice

Added a small settings layer for model defaults.

Target behavior:

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

## After Settings

- Move from deterministic plan creation to an optional LLM-assisted planner.
- Keep deterministic validation after any LLM-generated plan.
- Add a simple plugin manifest format and local plugin loader.
- Add SQLite trace persistence.
