# Architecture Map

JarvisOS is organized around a small terminal-first runtime spine. New work
should fit one of these boundaries before adding another top-level module.

## Runtime Flow

```text
CLI
  -> runtime factory
  -> orchestration planner
  -> deterministic validation
  -> policy and approval checks
  -> tool execution
  -> synthesis
  -> traces and final response
```

## Package Boundaries

- `jarvis.cli` parses terminal commands and renders output.
- `jarvis.runtime` wires settings, stores, registries, providers, integrations,
  and the orchestrator.
- `jarvis.contracts` contains shared runtime data contracts.
- `jarvis.settings` loads TOML and environment configuration.
- `jarvis.errors` normalizes runtime boundary failures.
- `jarvis.policies` makes deterministic approval decisions.
- `jarvis.agents` defines available agent routing specs.
- `jarvis.prompts` loads bundled and configured prompt files.
- `jarvis.orchestration` owns planning, step execution, argument references,
  and final synthesis.
- `jarvis.tools` owns the tool registry and built-in local tools.
- `jarvis.models` owns model providers and routing.
- `jarvis.integrations` owns external adapters such as MCP and local plugins.
- `jarvis.storage` owns SQLite-backed memory, tasks, traces, and approvals.

## Import Rule

Runtime code should import from the package that owns the behavior, for example:

```python
from jarvis.orchestration.orchestrator import Orchestrator
from jarvis.storage.memory import MemoryStore
from jarvis.integrations.mcp import load_mcp_tools
```

Avoid reintroducing top-level compatibility wrappers such as
`jarvis.orchestrator` or `jarvis.memory`. Top-level modules should be reserved
for entrypoints, contracts, config, shared errors, and cross-cutting runtime
glue.

## Next Substrate Seams

HTTP MCP support should live under `jarvis.integrations`, next to the stdio MCP
client. OAuth credential configuration and token persistence should be split
between `jarvis.settings` and `jarvis.storage`, with no secrets written to
traces or model prompts.

Cloud model APIs should extend `jarvis.models` through provider classes and the
existing router. Specialist sub-agents should extend `jarvis.agents`,
`jarvis.prompts`, and `jarvis.orchestration` without bypassing tool validation,
policy checks, approvals, or trace recording.
