# Current Prompt Cookbook

JarvisOS currently plans goals with a real model, validates the plan, executes
registered tools, checkpoints graph progress, and synthesizes a grounded answer.
Use Ollama with the example config for local testing:

```powershell
$env:PYTHONPATH = "src"
python -m jarvis run "<goal>" --config jarvis.toml.example --model "ollama/llama3.2:3b"
```

## Safe runtime context

```text
What is the current date, local time, and timezone?
```

```text
What is the current date? Return only the date and timezone.
```

These should select `system.current_datetime`.

## Local tasks and notes

```text
Create a task to ask Jordan about the API migration.
```

```text
Search my notes for Jordan and summarize the open questions.
```

The example config includes the local task tools and demo notes plugin. Task
creation is a low-risk local write; external writes still require approval.

## Intermediate language generation

```text
Draft a concise follow-up message asking Jordan for the API migration timeline,
then echo the draft with the demo MCP tool.
```

Use `mcp-demo.toml` for this prompt. It exercises a language-generation node,
text passed to a later tool, graph checkpoints, and grounded synthesis.

## Calendar, Gmail, and Spotify reads

```text
Use Google Calendar to list my calendars.
```

```text
Use Google Calendar to summarize my events over the next seven days.
```

```text
Use Gmail to find recent messages from Jordan and summarize the useful context.
```

```text
Use Spotify to show my five most recently played tracks.
```

Use the matching example config or enable the capability pack in `jarvis.toml`.
These prompts require the relevant MCP wrapper, model, and provider auth.

## Multi-source graph requests

```text
Review my upcoming calendar events and find related recent Gmail messages,
then summarize the confirmed context.
```

```text
Check my upcoming calendar, recent Gmail messages, and recently played Spotify
tracks, then give me one concise briefing.
```

The planner should create one evidence-producing step per available source.
Independent steps are currently executed in deterministic order; bounded
parallel execution is a future graph slice.

## Direct debugging and evaluation

```powershell
python -m jarvis tools --config jarvis.toml.example
python -m jarvis models --config jarvis.toml.example
python -m jarvis tool call system.current_datetime --args-json '{}' --json
python -m jarvis evals run examples/evals/planner-tool-use.json --model fake-local
python -m jarvis traces list --config jarvis.toml.example
```

For real model/plugin/MCP discovery in evals, opt in explicitly:

```powershell
python -m jarvis evals run examples/evals/planner-coverage.json `
  --config jarvis.toml --model "ollama/llama3.2:3b" `
  --allow-live-integrations --json
```

## Prompt boundaries today

- Ask for reads, summaries, drafts, or low-risk local tasks freely.
- Name the source when multiple sources are available.
- State the desired output shape: concise summary, list, draft, or briefing.
- Do not assume an external write happened; approval is required.
- Graph dependencies and named output references are supported, but the model
  must emit them explicitly; automatic replanning and resume are not yet exposed
  as user commands.
