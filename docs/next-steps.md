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
- Added a SQLite-backed approval queue with CLI list/show/approve/reject.
- Added `task.create` and a local SQLite task store for auto-allowed local
  writes.
- Added `tasks show/complete` and simple task-title cleanup.
- Added deterministic duplicate prevention for approved memory writes.
- Added a generic stdio MCP tool adapter and demo MCP server.
- Added `general.generate_text` as a model-backed internal language capability.
- Added minimal `$last.text` step data flow for generated text -> tool calls.
- Split runtime code into orchestration, tools, models, integrations, and
  storage packages, then removed top-level compatibility wrappers.
- Added per-tool MCP risk and approval overrides.

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

1. Choose the Google Workspace path:
   local stdio MCP server now, or official Google Workspace HTTP/OAuth MCP
   after adding HTTP transport support.
2. Add HTTP MCP transport and OAuth handling if using Google's official
   Workspace MCP servers directly.
3. Try Google Calendar read-only tools first, especially listing calendars,
   listing events, and getting one event.
4. Add resume/apply behavior for approved external or high-risk tool execution
   items.
5. Add trace filtering, timing, and basic metrics for benchmarking.
6. Expand plugin support with enable/disable state and clearer validation errors.
7. Add richer agent config files for specialists once prompt-only overrides feel
   too narrow.
8. Add named step outputs or richer workflow variables once `$last.text` becomes
   too narrow.
9. Add online plugin acquisition later as a separate installer layer.

## Near-Term Design Notes

Language generation should be an agent capability, not a provider tool
responsibility. For example, Gmail should send or draft an email, but an LLM
generalist or email specialist should compose the body from context first.

The intended pattern is:

```text
LLM agent generates or transforms language
  -> deterministic/read-write tool acts on external or local systems
  -> policy controls side effects
  -> trace records the confirmed result
```

This matters for MCP integrations. A demo echo server should only echo text; it
should not invent a fun fact. A Gmail MCP server should send or create drafts;
it should not own JarvisOS' drafting policy. JarvisOS should orchestrate those
steps explicitly.
