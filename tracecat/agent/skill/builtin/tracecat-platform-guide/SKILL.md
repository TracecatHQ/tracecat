---
name: tracecat-platform-guide
description: REQUIRED whenever the user asks what Tracecat is, what it can do, how to do something in the product, or where to find a feature in the UI — e.g. "how do I add a secret", "where are my integrations", "what's the difference between a case and a workflow", "can Tracecat send Slack messages", "how do I schedule a workflow", "set up an OAuth integration". Read this SKILL.md FIRST when orienting a user, explaining a concept, or directing them to a page. It covers the platform mental model, what the product offers, where each feature lives in the UI, and the core concepts (secrets vs variables vs expressions). For building/editing workflows, use tracecat-manage-workflows instead.
---

# Tracecat platform guide

Use this to orient users, explain what Tracecat does, and direct them to the right
place in the UI. Stay accurate: when you don't know a detail, say so or point the user
to the page rather than inventing steps.

## What Tracecat is

Tracecat is a security automation platform where AI agents and humans triage and
investigate threats. The primitives:

- **Workflows** — automations built as a graph of **actions**. Started by a **trigger**
  (webhook, schedule, case event, manual run). Data flows through **expressions**
  (`${{ ... }}`).
- **Actions** — the building blocks inside a workflow: HTTP calls, transforms, Python
  scripts, case operations, AI steps, and `tools.*` integrations (Slack, Jira, etc.).
- **Cases** — persistent investigation records. Teams add comments, evidence/attachments,
  tasks, and custom fields. Workflows create and enrich cases over time.
- **Tables** — structured data rows (assets, allowlists, indicators) that workflows
  insert into and look up across runs.
- **Agents** — AI tool-callers. They run inside a workflow (`ai.agent`) or as saved
  **presets** with their own instructions, tools, and MCP servers.
- **Integrations / credentials** — how Tracecat talks to outside systems. Three models:
  workspace **secrets**, **OAuth integrations**, and **MCP** servers.
- **Secrets** — sensitive values (API keys, tokens), referenced as
  `${{ SECRETS.<name>.<KEY> }}`, resolved at execution and never shown to an LLM.
- **Variables** — non-secret config (base URLs, project IDs), referenced as
  `${{ VARS.<name>.<key> }}`.

A **case** is a record you investigate; a **workflow** is the automation that does the
work. A **table** stores data; a **secret** stores a credential.

## What we offer

- Workflow automation with webhook/schedule/case triggers, branching, loops, retries.
- Case management: cases, comments, attachments, tasks, custom fields, tags, SLAs.
- Tables: on-demand structured storage with lookup, search, and upsert.
- AI: single LLM calls (`ai.action`), tool-calling agents (`ai.agent`), and saved
  preset agents (enterprise).
- ~100+ prebuilt integrations, plus custom Python/YAML actions and MCP servers.
- Expressions and 50+ built-in functions for data shaping.

## Directing users in the UI

Everything lives under a workspace at `/workspaces/<workspace_id>/...`. Direct users by
**naming the sidebar item or page** — do not script click-by-click steps. Common pages:

| Sidebar / page | Where | What's there |
|---|---|---|
| Chat | `/chat` | Talk to the workspace agent |
| Workflows | `/workflows` | List, create, open the builder |
| Cases | `/cases` | Case list and detail |
| Tables | `/tables` | Create/browse tables and rows |
| Variables | `/variables` | Non-secret config values |
| Credentials | `/credentials` | API keys / secrets |
| Integrations | `/integrations` | Connect OAuth providers |
| MCP servers | `/mcp-servers` | Connect MCP servers |
| Agents | `/agents` | Agent presets |
| Runs | `/runs` | Execution history across workflows |
| Inbox | `/inbox` | Approval queue for pending agent actions |
| Members | `/members` | Invite users, roles |

**Triggers are not separate pages.** Webhooks, schedules, and case triggers are
configured **inside the workflow builder** (`/workflows/<id>`) in the trigger panel.

## Correctness guardrails

- **Secrets vs variables:** secrets are sensitive (`${{ SECRETS.<name>.<KEY> }}`);
  variables are plain config (`${{ VARS.<name>.<key> }}`). Don't put secrets in variables.
- **Secret safety in agents:** `ai.preset_agent` injects secrets **server-side** (the LLM
  never sees them). `ai.action` and `ai.agent` evaluate expressions where the value **can**
  reach the model — never tell users to put raw secrets in those prompts.
- **Don't invent integration setup.** Per-integration setup steps (how to get a given
  vendor's API key) are not documented here. Tell the user which credential/secret the
  integration needs and where to add it (`/credentials` or `/integrations`), not invented
  vendor instructions.
- **Enterprise features** (preset agents, agent control plane, MCP access controls, skills)
  may be gated by entitlement — if a user can't see a feature, that's likely why.

## References (read on demand)

- [concepts](references/concepts.md) — secrets, variables, expressions, environments.
- [triggers-and-credentials](references/triggers-and-credentials.md) — picking a trigger;
  the three credential models; custom actions.
- [navigation](references/navigation.md) — full route map and sidebar.
