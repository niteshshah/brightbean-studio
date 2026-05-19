# BrightBean MCP Server

The BrightBean MCP server exposes the Studio's full feature surface (posts,
publishing, inbox, approvals, media, members, calendar) to AI tools that
speak the [Model Context Protocol](https://modelcontextprotocol.io/).

Configured AI clients can read and write your BrightBean data on behalf
of the user whose token they hold, scoped by the same RBAC the web UI
enforces.

## Quickstart

### 1. Mint an API token

Sign in to BrightBean, open **Settings → API Tokens**, click **Create token**.
Give it a label (e.g. *"Claude Desktop on laptop"*) and, optionally, scope it
to a single workspace. Copy the `bbn_…` value — it is shown only once.

### 2. Configure your AI client

#### Claude Desktop / Cursor / Claude Code (stdio)

Add this to your client's MCP server config (Claude Desktop:
`~/Library/Application Support/Claude/claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "brightbean": {
      "command": "/path/to/brightbean-studio/.venv/bin/python",
      "args": [
        "/path/to/brightbean-studio/manage.py",
        "mcp_serve",
        "--stdio"
      ],
      "env": {
        "DJANGO_SETTINGS_MODULE": "config.settings.production",
        "DATABASE_URL": "postgres://…",
        "BRIGHTBEAN_API_TOKEN": "bbn_…"
      }
    }
  }
}
```

The stdio process runs locally and talks to your BrightBean database
directly — same code path that gunicorn uses, so RBAC, encryption keys,
and workspace scoping all apply.

#### Remote clients (HTTP / streamable-http)

For setups where the AI tool runs separately from BrightBean, start an
HTTP MCP endpoint:

```bash
BRIGHTBEAN_API_TOKEN=bbn_… \
  python manage.py mcp_serve --http --host 0.0.0.0 --port 8765
```

Then point the client at `http://your-host:8765/mcp` with header
`Authorization: Bearer bbn_…`.

### 3. Verify

In Claude (or any MCP-enabled client), ask: *"What workspaces do I have
in BrightBean?"* — it should call `list_workspaces` and return your
workspace list.

## Tool catalog

| Area | Tools |
|------|-------|
| Session | `whoami`, `list_workspaces`, `select_workspace`, `get_workspace` |
| Accounts | `list_social_accounts` |
| Media | `list_media`, `get_media`, `upload_media_from_url`, `upload_media_from_base64`, `delete_media`, `list_folders`, `create_folder` |
| Composer | `list_posts`, `get_post`, `create_post`, `update_post`, `schedule_post`, `delete_post` |
| Categories/Tags | `list_categories`, `create_category`, `update_category`, `delete_category`, `list_tags`, `create_tag` |
| Templates | `list_templates`, `create_template_from_post`, `create_post_from_template`, `delete_template` |
| Ideas | `list_idea_groups`, `create_idea_group`, `list_ideas`, `create_idea`, `update_idea`, `move_idea`, `delete_idea`, `convert_idea_to_post` |
| Calendar | `list_queues`, `get_queue`, `add_to_queue`, `reorder_queue`, `list_posting_slots`, `upsert_posting_slots`, `reschedule_platform_post`, `list_custom_events`, `create_custom_event`, `delete_custom_event` |
| Publishing | `get_publish_status`, `publish_now`, `retry_failed` |
| Inbox | `list_inbox`, `get_inbox_message`, `send_reply`, `add_internal_note`, `change_status`, `assign_message`, `bulk_inbox_action`, `list_saved_replies`, `create_saved_reply`, `delete_saved_reply`, `render_saved_reply`, `get_inbox_sla`, `update_inbox_sla` |
| Approvals | `list_approval_queue`, `approve`, `request_changes`, `reject`, `submit_for_review`, `resubmit`, `bulk_approve`, `bulk_reject`, `list_post_comments`, `add_post_comment`, `list_approval_actions` |
| Members | `list_members`, `list_invitations`, `create_invitation`, `resend_invitation`, `revoke_invitation`, `update_member_role`, `remove_member` |
| Notifications | `list_notifications`, `mark_notification_read`, `mark_all_read`, `get_notification_preferences`, `update_notification_preferences` |
| Client portal | `list_magic_links`, `generate_magic_link`, `revoke_magic_link` |
| Analytics | `get_publish_metrics`, `list_publish_logs`, `get_rate_limit_status` |

## How auth works

* Every API token is hashed (sha256) before storage; the raw `bbn_…`
  value is shown to the user only once at creation.
* On every tool call, the server verifies the token, resolves the user,
  then enforces RBAC via `WorkspaceMembership.effective_permissions`
  before performing any mutation.
* Tokens may be scoped to a single workspace — in that case `select_workspace`
  is locked.
* Tokens can be revoked from **Settings → API Tokens** and stop working
  immediately.

## Architecture

The MCP server is implemented as a Django app (`apps/mcp_server`) that
imports BrightBean's existing service layer (`apps/composer/services.py`,
`apps/inbox/services.py`, etc.) directly. There is no separate REST API —
tools call the same code paths the web UI does, so behaviour stays in lock
step. See `docs/plans/lets-plan-to-create-cheeky-wilkinson.md` for the
design write-up.
