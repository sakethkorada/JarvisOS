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
