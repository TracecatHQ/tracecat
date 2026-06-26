# Navigation map

All paths are workspace-scoped: `/workspaces/<workspace_id>/<path>`. When directing a
user, name the **sidebar item** (and optionally the path). Don't dictate clicks.

## Sidebar

**Workspace:** Chat · Workflows · Cases · Agents · Tables · Variables · Credentials ·
Integrations · MCP servers · Skills · Actions
**Monitor:** Runs · Inbox
**Manage:** Members · Service accounts · MCP access

## Core setup

| Path | What's there |
|---|---|
| `/credentials` | Workspace secrets / API keys |
| `/integrations` | OAuth provider integrations |
| `/variables` | Non-secret config variables |
| `/tables` | Tables list |
| `/tables/<table_id>` | Browse rows, configure columns |
| `/cases/custom-fields`, `/cases/dropdowns`, `/cases/durations`, `/cases/tags` | Case field/tag configuration |
| `/cases/closure-requirements` | Case closure policies |

## Workflows & runs

| Path | What's there |
|---|---|
| `/workflows` | Workflow list; create |
| `/workflows/<id>` | Workflow builder — design actions and configure triggers (webhooks, schedules, case triggers) |
| `/workflows/<id>/executions` | Execution history for one workflow |
| `/workflows/<id>/executions/<exec_id>` | One run's step-by-step log |
| `/runs` | Runs across all workflows |
| `/workflows/tags` | Workflow tags |

## Agents & MCP

| Path | What's there |
|---|---|
| `/agents` | Agent presets list |
| `/agents/<preset_id>` | Preset builder: instructions, tools, model, MCP |
| `/skills` | Reusable agent skills (entitlement-gated) |
| `/mcp-servers` | Connect/manage MCP servers |
| `/mcp` | MCP access controls |

## Cases & navigation

| Path | What's there |
|---|---|
| `/cases` | Case list, filtering, bulk actions |
| `/cases/<case_id>` | Case detail: timeline, comments, tasks, fields |
| `/chat` | Workspace agent chat (entitlement-gated) |
| `/inbox` | Approval queue for pending agent actions |

## Manage

| Path | What's there |
|---|---|
| `/members`, `/members/groups`, `/members/roles` | Users, groups, roles |
| `/service-accounts` | Service account credentials |
| `/actions` | Browse the org action registry (read-only) |
| `/workspaces` | Switch workspace |
