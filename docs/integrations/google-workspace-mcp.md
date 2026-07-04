# Google Workspace MCP Notes

JarvisOS currently supports MCP tools over stdio. Google documents official
Google Workspace MCP servers for Gmail, Drive, Calendar, Chat, and People over
HTTP with OAuth. That means the official remote Google Calendar MCP server is
not directly usable until JarvisOS adds an HTTP MCP transport and OAuth handling.

## Current Read-Only Calendar Path

For the current stdio-only runtime, use a trusted local Google Calendar or
Google Workspace MCP server if one is installed locally. Configure it under
`[[mcp.servers]]`, then use per-tool overrides so read-only tools can run
automatically while writes require approval.

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

- HTTP MCP transport support,
- OAuth credential storage/config,
- token refresh handling,
- redaction rules for sensitive Workspace responses.

## Near-Term Recommendation

Keep the next Google slice read-only:

```text
User request
  -> planner
  -> Google Calendar MCP read tool
  -> general.generate_text for summary or agenda
  -> synthesis
  -> trace
```

Only after read-only Calendar works reliably should Gmail, Drive, and Calendar
writes be enabled, with per-tool approval overrides.
