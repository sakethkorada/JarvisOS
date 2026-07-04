# Google Workspace MCP Notes

JarvisOS supports MCP tools over stdio and streamable HTTP. Google documents
official Google Workspace MCP servers for Gmail, Drive, Calendar, Chat, and
People over HTTP with OAuth. JarvisOS can attach bearer tokens from an
environment variable or the local auth store, and it can run an on-demand
authorization-code + PKCE flow for configured providers.

## Current Read-Only Calendar Path

Use Google's remote Calendar MCP server or a trusted local Calendar MCP server.
Configure it under `[[mcp.servers]]`, then use per-tool overrides so read-only
tools can run automatically while writes require approval.

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

JarvisOS still needs:

- redaction rules for sensitive Workspace responses.
- dynamic MCP auth discovery and dynamic client registration.

Example remote Calendar config:

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
]

[[mcp.servers]]
name = "google_calendar"
transport = "http"
url = "https://calendarmcp.googleapis.com/mcp/v1"
auth_provider = "google"
bearer_token_env = "GOOGLE_MCP_ACCESS_TOKEN"
risk_level = "medium"
requires_approval = true
```

On first use of `google_calendar`, JarvisOS should print and open the Google
authorization URL. After the browser redirects to the local callback URI,
JarvisOS stores the returned access and refresh tokens and continues the MCP
call.

Manual token entry still works as a fallback:

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
the configured OAuth client id. The command redacts access and refresh tokens.

Do not commit local configs that contain client IDs, token references, or other
private integration details.

## Local FastMCP Calendar Wrapper

Install the optional MCP dependency:

```powershell
uv pip install -e ".[mcp]"
```

Copy the example config and ensure its `--config` argument points to your local
JarvisOS config that contains the Google OAuth provider:

```powershell
Copy-Item examples/mcp/google-calendar-fastmcp.toml.example google-calendar-fastmcp.toml
python -m jarvis tools --config google-calendar-fastmcp.toml
python -m jarvis run "Use Google Calendar to list my calendars" --config google-calendar-fastmcp.toml
```

The example registers the local server as `google_calendar`, so the user-facing
tool names remain `google_calendar.list_calendars` and
`google_calendar.list_events`. Use the local wrapper instead of the hosted
Google Calendar MCP server in a single config to avoid duplicate tool names.

## Near-Term Recommendation

Keep the next Google slice read-only:

```text
User request
  -> planner
  -> Google Calendar MCP read tool, hosted or local FastMCP
  -> general.generate_text for summary or agenda
  -> synthesis
  -> trace
```

Only after read-only Calendar works reliably should Gmail, Drive, and Calendar
writes be enabled, with per-tool approval overrides.
