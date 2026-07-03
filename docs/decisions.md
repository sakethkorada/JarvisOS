# Decision Log

This is a lightweight record of implementation decisions. Keep entries short:
what changed, why, and what tradeoff we accepted.

## 0001 - Start With Vertical Slices

JarvisOS will be built through small runnable CLI slices instead of building a
large abstract platform first.

Reason: each slice should prove one piece of the architecture while keeping the
project testable from the terminal.

## 0002 - Keep Defaults Configurable, Not Hardcoded

Default workflows such as meeting prep are reference scenarios, not privileged
core behavior.

Reason: JarvisOS is a personal orchestration runtime where users can bring their
own agents, plugins, MCP servers, tools, models, and workflows.

## 0003 - Use Fake Provider for Tests

`fake-local` remains the default deterministic provider for tests and offline
runtime checks.

Reason: tests should not require local models, API keys, network services, or
paid providers.

## 0004 - Add Ollama Before Cloud APIs

Ollama is the first real model provider.

Reason: it supports local-first iteration with no API-key setup, no token cost,
and a simple local HTTP API.

Tradeoff: local model quality and latency may vary by machine, so the LLM is
currently used only as a provider smoke test and trace input.

## 0005 - CLI Model Selection Before Settings

The first selection mechanism is `jarvis run --model <provider>`.

Reason: it is the smallest way to test provider routing.

Next step: add settings-based defaults so users do not need to pass `--model`
every time.

## 0006 - Provider-Agnostic Model Settings

Model defaults and modes live in a settings layer that names providers with
strings such as `fake-local`, `ollama/llama3.2:3b`, or future cloud provider
names.

Reason: the orchestrator should not care whether the selected model is local or
cloud-backed. Provider-specific auth and transport belong below the model router.

Precedence is:

```text
CLI --model
> settings mode
> settings default
> fake-local fallback
```

## 0007 - Simple Python Documentation Standard

Public modules, classes, functions, and methods should use standard Python
docstrings. Comments should be minimal and explain intent, constraints, or
non-obvious behavior.

Reason: JarvisOS is expected to grow across agents, tools, providers, and
plugins, so future contributors need enough context to understand seams without
reading a large external design document first.
