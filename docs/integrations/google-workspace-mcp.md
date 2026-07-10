# Google Workspace MCP Notes

JarvisOS supports MCP tools over stdio and streamable HTTP. Google documents
official Google Workspace MCP servers for Gmail, Drive, Calendar, Chat, and
People over HTTP with OAuth. JarvisOS can attach bearer tokens from an
environment variable or the local auth store, and it can run an on-demand
authorization-code + PKCE flow for configured providers.

Provider auth is shared across run configs. If the active config does not
define `[auth]`, JarvisOS looks for `JARVIS_AUTH_PROFILE`, `.jarvis/auth.toml`,
`config/auth.toml`, `jarvis.toml`, then `config/jarvis.toml`. This means a
Calendar-specific MCP config can only enable tools while Google OAuth metadata
and token storage stay in one global profile.

For daily local use, enable the bundled Workspace capability pack in
`jarvis.toml`:

```toml
[capabilities]
google_workspace = true
```

The pack expands to the same local Calendar and Gmail FastMCP server settings
shown in the example TOML files. The standalone example configs remain useful
for isolated testing, custom wrappers, or debugging one provider at a time.

## Current Read-Only Calendar And Gmail Path

Use Google's remote Workspace MCP servers or trusted local FastMCP wrappers.
Configure them under `[[mcp.servers]]`, then use per-tool overrides so read-only
tools can run automatically while writes, sends, deletes, and externally visible
actions require approval.

For the current POC, prefer the local FastMCP wrapper if Google's hosted
Calendar MCP server discovers tools but returns `The caller does not have
permission` during execution. The local wrapper calls Google Calendar REST with
the same JarvisOS OAuth token and exposes read tools through MCP stdio.

Useful read-only Calendar tools from Google's documented Workspace MCP surface:

- `list_calendars`
- `list_events`
- `get_event`

Calendar tools that should require approval:

- `create_event`
- `update_event`
- `delete_event`
- `respond_to_event`
- `suggest_time` if it writes, invites, holds, or proposes externally visible
  changes through the provider.

Current read-only Gmail wrapper tools:

- `list_recent`
- `search_messages`
- `get_message`
- `get_thread`

Gmail tools that should require approval:

- `create_draft`
- `send_message`
- `modify_message`
- `trash_message`
- any tool that marks read/unread, changes labels, archives, deletes, sends, or
  creates externally visible email state.

## Setup Needed For Official Google Workspace MCP

The official Google Workspace MCP servers require:

- a Google Cloud project,
- Google Workspace APIs enabled,
- Google Workspace MCP services enabled,
- an OAuth consent screen,
- OAuth client credentials,
- Calendar read-only scopes such as:
  - `https://www.googleapis.com/auth/calendar.calendarlist.readonly`
  - `https://www.googleapis.com/auth/calendar.events.freebusy`
  - `https://www.googleapis.com/auth/calendar.events.readonly`
- Gmail read-only scope:
  - `https://www.googleapis.com/auth/gmail.readonly`

JarvisOS still needs:

- redaction rules for sensitive Workspace responses.
- dynamic MCP auth discovery and dynamic client registration.

Example shared Google OAuth profile for the local wrappers:

```toml
[auth]
database_path = ".jarvis/auth.sqlite3"

[[auth.oauth_providers]]
name = "google"
client_id = "replace-with-google-oauth-client-id"
client_secret_env = "GOOGLE_OAUTH_CLIENT_SECRET"
authorization_url = "https://accounts.google.com/o/oauth2/v2/auth"
token_url = "https://oauth2.googleapis.com/token"
redirect_uri = "http://localhost:8765/oauth/callback"
scopes = [
  "https://www.googleapis.com/auth/calendar.calendarlist.readonly",
  "https://www.googleapis.com/auth/calendar.events.freebusy",
  "https://www.googleapis.com/auth/calendar.events.readonly",
  "https://www.googleapis.com/auth/gmail.readonly",
]

```

The hosted Google Calendar MCP endpoint was retired from the recommended
JarvisOS path after it returned permission failures despite a valid direct REST
Calendar token. Use the local FastMCP wrapper through `google_workspace`
instead. Generic HTTP MCP support remains available for other compatible
servers.

OAuth token setup is still shared by the local Calendar and Gmail wrappers.
Manual token entry remains available as a fallback:

```powershell
$env:GOOGLE_MCP_ACCESS_TOKEN="<access-token>"
python -m jarvis auth set-token google "<access-token>" --config google-calendar.toml
```

When Google Calendar MCP returns `The caller does not have permission`, inspect
the stored token before changing runtime code:

```powershell
python -m jarvis auth debug google --config google-calendar.toml
```

Check whether the granted scopes include the configured Calendar scopes, whether
the token is expired, and whether the token audience or authorized party matches
the configured OAuth client id. If the token is expired, also check
`client_secret_present`. When the global auth profile configures
`client_secret_env = "GOOGLE_OAUTH_CLIENT_SECRET"`, that environment variable
must be set in the same shell that launches JarvisOS so refresh-token renewal
can work. The command redacts access and refresh tokens and never prints the
client secret value.

```powershell
$env:GOOGLE_OAUTH_CLIENT_SECRET="<google-oauth-client-secret>"
python -m jarvis auth debug google --json
```

Do not commit local configs that contain client IDs, token references, or other
private integration details.

## Local FastMCP Calendar Wrapper

Install the optional MCP dependency:

```powershell
uv pip install -e ".[mcp]"
```

Copy the example config. The MCP server uses the global auth profile, so the
example does not need a back-reference to `jarvis.toml`:

```powershell
Copy-Item examples/mcp/google-calendar-fastmcp.toml.example google-calendar-fastmcp.toml
python -m jarvis auth debug google --config google-calendar-fastmcp.toml --json
python -m jarvis tools --config google-calendar-fastmcp.toml
python -m jarvis run "Use Google Calendar to list my calendars" --config google-calendar-fastmcp.toml
```

The example registers the local server as `google_calendar`, so the user-facing
tool names remain `google_calendar.list_calendars` and
`google_calendar.list_events`. Use the local wrapper instead of the hosted
Google Calendar MCP server in a single config to avoid duplicate tool names.

## Local FastMCP Gmail Wrapper

The Gmail wrapper uses the same global Google auth profile and exposes read-only
tools as `gmail.*`:

```powershell
Copy-Item examples/mcp/google-gmail-fastmcp.toml.example google-gmail-fastmcp.toml
python -m jarvis auth debug google --config google-gmail-fastmcp.toml --json
python -m jarvis tools --config google-gmail-fastmcp.toml
python -m jarvis tool call gmail.list_recent --args-json '{"max_results":5}' --config google-gmail-fastmcp.toml --json
python -m jarvis run "Use Gmail to find recent emails from Jordan" --config google-gmail-fastmcp.toml --model "ollama/llama3.2:3b"
```

For combined Calendar + Gmail prompts:

```powershell
Copy-Item examples/mcp/google-workspace-fastmcp.toml.example google-workspace-fastmcp.toml
python -m jarvis tools --config google-workspace-fastmcp.toml
python -m jarvis run "Use Calendar and Gmail to prep me for meetings this week" --config google-workspace-fastmcp.toml --model "ollama/llama3.2:3b"
```

After `[capabilities].google_workspace = true` is set in the default
`jarvis.toml`, the same tools can be tested without passing `--config`:

```powershell
python -m jarvis tools
python -m jarvis tool call google_calendar.list_calendars --args-json '{}' --json
python -m jarvis tool call gmail.list_recent --args-json '{"max_results":5}' --json
python -m jarvis run "Use Calendar and Gmail to prep me for meetings this week" --model "ollama/llama3.2:3b"
```

## Near-Term Recommendation

Keep the next Google slice read-only:

```text
User request
  -> planner
  -> Google Calendar/Gmail MCP read tool, hosted or local FastMCP
  -> general.generate_text for summary or agenda
  -> synthesis
  -> trace
```

Only after read-only Calendar and Gmail work reliably should Gmail draft/send,
Drive, and Calendar writes be enabled, with per-tool approval overrides.
