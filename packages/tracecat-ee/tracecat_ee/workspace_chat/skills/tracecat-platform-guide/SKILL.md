---
name: tracecat-platform-guide
description: Use when orienting someone in the Tracecat product rather than authoring an automation, including what Tracecat is and what it can do, what a workflow, case, table, agent, integration, secret, or variable is and how they differ, and where a feature lives in the workspace UI.
---

# Tracecat Platform Guide

For building, editing, validating, or debugging automations, use
`$tracecat-automation-best-practices`; for Slack-facing ones,
`$tracecat-slackbot-best-practices`. This skill covers what the product *is* and where things
live — not DSL syntax. When you do not know a detail, say so or name the page; inventing steps
is worse than a short answer.

## The primitives

Tracecat is a security automation platform where AI agents and humans triage and investigate
threats. Eight things make up a workspace:

- **Workflows** — automations built as a graph of actions and started by a trigger: a webhook,
  a schedule, a case event, or a manual run. Each has a draft you edit and a published version
  that triggers actually run.
- **Actions** — the nodes inside a workflow: HTTP calls, transforms, Python scripts, case and
  table operations, AI steps, and vendor integrations.
- **Cases** — investigation records a human works: status, severity, owner, comments,
  attachments, tasks, tags, custom fields. Workflows create and enrich them over time.
- **Tables** — rows and columns for assets, allowlists, and indicators, queried by lookup and
  search across runs. Storage, not investigation state.
- **Agents** — AI tool-callers, run inline inside a workflow or saved as **presets** with their
  own instructions, tools, model, and MCP servers.
- **Integrations** — how Tracecat reaches outside systems: workspace secrets for static
  credentials, OAuth providers for tokens Tracecat refreshes, MCP servers for agent tools.
- **Secrets** — sensitive values (API keys, bot tokens, certs), scoped to an environment and
  resolved at execution time. Referenced through the `SECRETS` context.
- **Variables** — non-secret config (base URLs, project IDs, queue names), also scoped per
  environment. Referenced through the `VARS` context.

A case is a record you investigate; a workflow is the automation that does the work. A table
stores data; a secret stores a credential.

## Where things live

Everything is workspace-scoped under `/workspaces/<workspace_id>/...`. Direct users by naming
the **sidebar item**, not by scripting clicks.

| Sidebar item | Path | What's there |
|---|---|---|
| Workflows | `/workflows` | List, create, and open the builder |
| Cases | `/cases` | Case list, filters, and case detail |
| Agents | `/agents` | Agent presets and the preset builder |
| Tables | `/tables` | Create and browse tables and rows |
| Variables | `/variables` | Non-secret config values |
| Credentials | `/credentials` | API keys and workspace secrets |
| Integrations | `/integrations` | Connect OAuth providers |
| MCP servers | `/mcp-servers` | Connect and manage MCP servers |
| Skills | `/skills` | Reusable agent skills |
| Actions | `/actions` | Browse the action registry, read-only |
| Runs | `/runs` | Execution history across all workflows |
| Inbox | `/inbox` | Approval queue for pending agent actions |
| Members | `/members` | Users, groups, and roles |
| Service accounts | `/service-accounts` | Service account credentials |
| MCP access | `/mcp` | MCP access controls |

**Triggers are not separate pages.** Webhooks, schedules, and case triggers are all configured
inside the workflow builder at `/workflows/<id>`, in the trigger panel.

## Correctness guardrails

- **Secrets are not variables.** Secrets hold credentials and never reach a model through the
  secure injection path; variables hold plain config and are ordinary data. Putting a token in
  a variable moves it out of the protected path.
- **`ai.preset_agent` injects secrets server-side, so the model never sees the value.**
  `ai.agent` and `ai.action` resolve expressions into arguments the model *can* read. Never
  tell a user to put a raw secret in an `ai.agent` or `ai.action` prompt.
- **Do not invent per-vendor setup steps.** Name the credential the integration needs and the
  page to add it on — Credentials or Integrations — rather than describing a vendor console you
  cannot see.
- **Do not blame the workspace for a missing feature.** A name that does not resolve is far
  more often a typo or the wrong identifier than an absent capability. Check the exact name
  first.

An agent changes any of this through Tracecat MCP tools such as `get_workflow` and
`edit_workflow`. In Tracecat workspace chat these are exposed as `core.workflow.<name>`
registry actions; over MCP the names are bare.
