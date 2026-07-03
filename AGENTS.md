# AGENTS.md - JarvisOS Project Context

This document is the shared project context for JarvisOS. It is intentionally a
living guide, not a final blueprint. The architecture will change as the system is
built, tested, and simplified.

The most important rules are:

- Build in small, runnable vertical slices.
- Keep the core runtime generic and configurable.
- Ship useful defaults, but do not hardcode them into the core.
- Prefer explicit contracts over implicit prompt behavior.
- Use deterministic code for routing, validation, policies, storage, and traces.
- Use LLMs where language, ambiguity, or synthesis actually helps.

## 1. Project Identity

JarvisOS is a terminal-first, model-agnostic personal agent orchestration runtime.

It is not meant to be only a chatbot with integrations. It is the reusable
runtime underneath a customizable personal orchestration app.

JarvisOS should let a user run AI-powered workflows across:

- local models,
- cloud model APIs,
- MCP servers,
- REST APIs,
- local Python plugins,
- shell/browser tools where allowed,
- optional workflow backends such as n8n,
- user-installed skills and plugin packs.

The long-term goal is a personal orchestration layer where users can bring their
own specialists, plugins, MCP servers, skills, models, policies, and workflows.

## 2. Product Shape

JarvisOS should start as a simple command-based terminal application.

Example commands:

```bash
jarvis ask "What do I have today?"
jarvis run "Prepare me for my meeting with Jordan tomorrow"
jarvis agents list
jarvis tools list
jarvis models list
jarvis memory search "meeting preferences"
jarvis approvals pending
jarvis traces show <run_id>
```

A TUI, dashboard, workflow builder, plugin manager, and background service can
come later. The first version should prove the runtime loop from the terminal.

## 3. Core Runtime Idea

A user enters a goal. JarvisOS should:

1. Interpret the goal.
2. Discover available agents, tools, plugins, models, memory, and policies.
3. Create a structured plan using the capabilities that exist.
4. Route steps to specialist agents or tool-backed capabilities.
5. Select appropriate models based on user mode and task needs.
6. Execute safe actions automatically.
7. Pause for approval before risky actions.
8. Record a trace of what happened.
9. Return a clear summary of completed work, failures, and pending approvals.

The orchestrator should not contain special-case branches for specific user
workflows. Reference workflows such as meeting prep should exercise the same
public interfaces that user-created workflows use.

## 4. Defaults Without Hardcoding

JarvisOS should eventually ship with default capability packs and reference
workflows because most users will want common productivity features.

Useful default capability areas:

- calendar,
- email,
- memory,
- notes/docs,
- tasks,
- music,
- research.

Useful reference workflows:

- meeting prep,
- morning briefing,
- focus planning,
- rescheduling,
- weekly planning.

These should be implemented as normal agents, tools, plugins, skills, and
workflow templates. They should not receive privileged private shortcuts in the
runtime.

Good rule:

```text
Default workflows must use the same public interfaces as user workflows.
```

## 5. Meeting Prep as a Reference Scenario

"Meeting prep" is a reference scenario, not a hardcoded core workflow.

Example user request:

```text
Prepare me for my meeting with Jordan tomorrow.
```

The runtime might discover:

- `calendar.search_events`,
- `email.search_messages`,
- `memory.search`,
- `notes.search`,
- `email.draft_message`.

Then the orchestrator creates a temporary plan from the available capabilities.

Expected output might include:

- meeting title, time, attendees,
- recent related emails or notes,
- remembered context,
- suggested agenda,
- open questions,
- optional draft follow-up,
- pending approvals for any write/send actions.

This is a good early test because it exercises orchestration without requiring
dangerous autonomous writes.

## 6. Chunked Iteration Philosophy

JarvisOS should be built through vertical slices. Every meaningful iteration
should leave behind a runnable CLI behavior.

Avoid building a complete abstract platform before anything runs. Also avoid
building a polished demo that bypasses the architecture.

Preferred sequence:

1. Tiny CLI loop with fake model and fake tools.
2. Core schemas and contracts for models, tools, agents, policies, approvals,
   traces, and run results.
3. Dynamic registries for agents, tools, and models.
4. User-managed local plugin loading.
5. One reference scenario, such as meeting prep, using the generic runtime.
6. Approval gates for writes and externally visible actions.
7. SQLite traces and simple memory.
8. Real provider/plugin adapters one at a time.
9. Optional workflow templates and reusable automation.

Each slice should prove one more part of the system while keeping the app usable
from the terminal.

## 7. High-Level Architecture

```text
User
  ->
CLI
  ->
Orchestrator
  ->
Planner / Router
  ->
Specialist agents and tool-backed capabilities
  ->
Tool runtime
  ->
MCP / REST / local plugins / shell / browser / workflow backends
  ->
Policy and approval engine
  ->
Memory and trace storage
  ->
Final response
```

The major runtime subsystems are:

- interface layer,
- orchestrator,
- model router,
- agent registry,
- tool registry,
- plugin loader,
- policy and approval engine,
- execution engine,
- memory store,
- trace store,
- configuration system.

## 8. Model Abstraction

Agents and workflows should not call model providers directly.

They should call a common internal interface through a model router.

Target provider categories:

- cloud APIs such as Gemini, OpenAI, Anthropic, Groq, Mistral, Together,
- local providers such as Ollama, LM Studio, llama.cpp, vLLM,
- OpenAI-compatible local or hosted endpoints.

The router should eventually support modes such as:

- cheap,
- fast,
- accurate,
- private,
- balanced.

Early versions can use a very small router. The important part is that provider
choice does not leak into agent or workflow code.

## 9. Agents

Agents are scoped domain workers. They should be configured with:

- name,
- description,
- allowed tools,
- preferred model mode,
- risk permissions,
- memory access rules,
- output expectations.

Likely default agents over time:

- OrchestratorAgent,
- CalendarAgent,
- EmailAgent,
- MemoryAgent,
- NotesAgent,
- TaskAgent,
- MusicAgent,
- ResearchAgent.

Avoid creating a specialist for every workflow name. For example,
`MeetingPrepAgent` should not be necessary at first. Meeting prep should be a
plan produced by the orchestrator using reusable agents and tools.

## 10. Tools, Plugins, Skills, and MCP

JarvisOS should normalize different capability sources into one internal tool
format.

Tool sources may include:

- MCP server tools,
- REST API wrappers,
- local Python functions,
- user-installed plugins,
- shell commands,
- browser automation,
- n8n or webhook workflows.

The plugin model should be user-managed in early versions. JarvisOS can load
plugins from configured local paths, but the user is responsible for downloading,
installing, or trusting plugin code. This is similar in spirit to how developer
tools let users opt into local extensions.

Later versions can add:

- plugin installation helpers,
- plugin registries,
- manifest validation,
- permission prompts,
- sandboxing,
- reputation or signing mechanisms.

Do not design v1 around a full marketplace.

## 11. Policies and Approvals

The safety model should be conservative and deterministic.

Default rule:

```text
Read-only actions may run automatically.
Writes, sends, deletes, posts, purchases, bookings, and externally visible
actions require approval.
```

Examples that usually require approval:

- sending email,
- creating or modifying calendar events,
- deleting records,
- posting to Slack or social platforms,
- making purchases,
- booking travel,
- writing to important files,
- running shell commands with write effects.

Approval prompts should show:

- the tool/action,
- key inputs,
- expected effect,
- risk level,
- approve/reject/edit options where applicable.

The policy engine should make structured decisions. It should not rely on an LLM
to decide whether an action is safe.

## 12. Memory

Memory should be useful but simple at first.

Long-term memory may eventually include:

- semantic memory: stable facts and preferences,
- episodic memory: past runs and conversations,
- procedural memory: reusable habits and workflows.

Early memory can be SQLite-backed records with simple search. Vector retrieval,
confidence scores, memory deduplication, decay, and dashboards can come later.

Important memory rules:

- Do not store secrets as memory.
- Do not silently store sensitive private content.
- Let users inspect and delete memory.
- Do not stuff all memory into every prompt; retrieve only relevant context.

## 13. Tracing and Observability

Every run should produce a trace.

Track:

- user request,
- generated plan,
- selected models,
- agents used,
- tools called,
- policy decisions,
- approval decisions,
- errors,
- retries,
- latency,
- rough cost where available,
- final status.

Early tracing can be SQLite rows plus CLI display. Dashboards and analytics can
come later.

The system must not claim an action completed unless a tool result confirms it.

## 14. Reviewer Behavior

A full reviewer agent is not part of the near-term core.

Near-term verification should be deterministic:

- Did required steps complete?
- Did any tool fail?
- Are approvals pending?
- Did the final summary claim unconfirmed actions?
- Were blocked actions clearly reported?

An optional LLM reviewer can be explored much later, after the runtime has
enough traces and real workflow behavior to review.

## 15. Workflow Engine

Workflows should eventually be reusable templates, not hardcoded runtime logic.

Early versions can execute simple generated plans step by step. Later versions
can add:

- YAML workflow templates,
- dependency graphs,
- parallel steps,
- retries,
- scheduled triggers,
- webhook triggers,
- workflow history.

n8n should be treated as an optional backend/tool source, not the center of the
JarvisOS runtime.

## 16. Suggested Early Milestones

### Milestone 0 - Project Skeleton

- `pyproject.toml`,
- package layout,
- `jarvis` CLI entrypoint,
- config loading,
- basic tests and linting.

### Milestone 1 - Runnable Fake Runtime

- `jarvis run "<goal>"`,
- fake model provider,
- fake tool registry,
- simple orchestrator,
- structured run result,
- trace printed to terminal.

### Milestone 2 - Core Contracts

- Pydantic schemas for models, tools, agents, policy decisions, approvals,
  traces, and execution plans.
- Registries for models, tools, and agents.
- Deterministic validation for missing tools/agents.

### Milestone 3 - Plugin Loading

- Load user-managed local plugins from config.
- Validate plugin manifests.
- Register plugin tools into the tool registry.
- Show tools with `jarvis tools list`.

### Milestone 4 - Reference Scenario

- Implement meeting prep as a generic runtime scenario, not a hardcoded branch.
- Use fake or local demo tools first.
- Confirm missing tools degrade gracefully.
- Show completed work and pending approvals.

### Milestone 5 - Approval and Trace Store

- Policy file.
- Approval queue.
- SQLite trace storage.
- CLI commands for pending approvals and trace display.

### Milestone 6 - First Real Integrations

- Add one real model provider.
- Add one real local model provider or OpenAI-compatible endpoint.
- Add one real tool adapter, such as calendar, email, or notes.

## 17. Python and Engineering Standards

Use Python 3.11+.

Preferred tooling:

- `uv` for environments and dependencies,
- `typer` for CLI,
- `rich` for terminal output,
- `pydantic` for runtime schemas,
- `pytest` for tests,
- `pytest-asyncio` for async tests,
- `ruff` for formatting and linting,
- `mypy` or `pyright` later for type checking.

Standards:

- Follow PEP 8 naming and layout conventions.
- Use `snake_case` for functions, methods, variables, modules, and file names.
- Use `PascalCase` for classes and `UPPER_SNAKE_CASE` for constants.
- Use type hints on public functions and methods.
- Add standard Python docstrings for public modules, classes, functions, and
  methods.
- Keep comments minimal and useful. Explain intent, constraints, or non-obvious
  behavior rather than restating what the code already says.
- Prefer simple, readable code over clever abstractions.
- Do not add emojis, decorative comments, or fancy formatting in code.
- Keep interfaces separate from implementations.
- Keep runtime logic separate from CLI rendering.
- Do not pass raw dictionaries deeply when a schema is known.
- Do not use live LLM/API calls in normal tests.
- Mock external services by default.
- Never log API keys, OAuth tokens, passwords, or secrets.

## 18. Testing Strategy

Test deterministic components heavily:

- config loading,
- model registry/router,
- tool registry,
- plugin manifest validation,
- policy engine,
- approval flow,
- plan validation,
- trace storage,
- memory storage.

Use fake providers and fake tools for normal tests.

Integration tests should verify that the CLI can run a complete fake scenario
without requiring API keys.

Real API tests should be optional and gated behind explicit environment
variables.

## 19. Scope Boundaries

Early versions should not try to build:

- a full SaaS product,
- a plugin marketplace,
- a complex GUI,
- a drag-and-drop workflow builder,
- adaptive model-routing analytics,
- vector memory dashboards,
- autonomous high-impact actions,
- a full reviewer agent,
- every provider or every MCP server.

The early goal is to prove a small, extensible terminal runtime that can grow.

## 20. One-Sentence Description

JarvisOS is a terminal-first, model-agnostic personal orchestration runtime that
coordinates user-configurable agents, tools, plugins, MCP servers, memory,
policies, approvals, and local or cloud models to execute personalized AI
workflows safely and transparently.
