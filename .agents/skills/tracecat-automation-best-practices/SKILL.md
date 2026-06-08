---
name: tracecat-automation-best-practices
description: Use when building, editing, validating, or debugging generic Tracecat automations through Tracecat MCP, including workflow DSL/YAML authoring, table design, unique indexes, run-python Tracecat imports, agent presets, ai.agent or ai.preset_agent workflows, executions, validation, and workflow best practices.
---

# Tracecat Automation Best Practices

For Slack-facing automations, use `$tracecat-slackbot-best-practices`. Detailed guidance
lives in on-demand references: [run-python](references/run-python.md),
[workflow-editing](references/workflow-editing.md), [tables](references/tables.md).

## Workflow

Start from live MCP context rather than guessing:

1. Discover the workspace with `list_workspaces`.
2. Read `tracecat://platform/dsl-reference` when DSL syntax or examples are needed.
3. Use `get_workflow_authoring_context` for action schemas, variables, and secrets.
4. For existing workflows, use `get_workflow`, then targeted `edit_workflow` patches — see
   [workflow-editing](references/workflow-editing.md).
5. Validate with `validate_workflow`, run a draft or published execution when appropriate,
   then inspect failures with `list_workflow_executions` and `get_workflow_execution`.

Clarify production choices that change the workflow contract: workspace, integration/provider,
secret source, publish/run behavior, destructive side effects, approvals, or acceptance
criteria. If the request is already specific enough, proceed.

Sketch the workflow shape before authoring. Prefer readable left-to-right or top-to-bottom
flows, human-readable refs, few branches, and layout refs aligned with definition refs.

## Choosing between an agent and Python

Decide by the *kind* of work, and optimize the whole workflow for maintainability and
readability — **the fewest actions and the least code that does the job.**

- **Agentic work → use an agent** (`ai.agent` / `ai.preset_agent`). Anything needing
  judgment, investigation, routing, enrichment choices, summarization, composing a message,
  or posting to Slack. Give the agent the tools and trust it. Prefer agents, prompts, and
  skills — they carry the creative thinking with far fewer moving parts than a graph of
  deterministic nodes.
- **Deterministic data plumbing → use Python** (`core.script.run_python`). Transforming,
  normalizing, redacting, loading or upserting rows into tables, forwarding data between
  systems. This is exactly what Python is for — it is **not** a smell. One clear `run_python`
  action beats scattering the same work across many nodes.

The smell is using Python for the *agentic* part — for example composing or sending an
agent's Slack message from a script instead of giving the agent the Slack tool.

**Prompt-size guardrail:** push behavior into the prompt up to a reasonable size. When a
single prompt grows too large to stay readable, decompose into **subagents or a skill**
rather than inflating one mega-prompt.

## Agent-First Automation

When the user asks for an agentic workflow, an agent preset, or says to use agents, make the
agent the primary owner of the work. Give it the tools it needs and trust it to investigate,
decide, route, compose messages, and act. Default to a thin shape:
`trigger -> reshape/redact -> upsert event record -> ai.agent` (or `ai.preset_agent`).

Persist the normalized event before the agent runs. When events originate in a SIEM, log
platform, SaaS webhook stream, audit trail, or cloud service, store the normalized payload
and review state in a Tracecat table first, with a deterministic upsert. That gives retries,
duplicate webhooks, and replay a durable source of truth. Point the agent at the saved row,
so the workflow — not the agent — owns the first durable record of the event.

A deterministic node earns its place by being thin, direct, and easy to audit, and by doing
work the agent should not own: redact secrets before the agent sees data, normalize schema,
upsert the event row, enforce hard approval or authorization boundaries, guard expensive
agent runs with a dedupe check, prepare bounded batches, or checkpoint durable state. Keep
judgment, routing, enrichment, message composition, soft approval decisions, and final
notification behavior in the agent. When an approval question itself needs judgment, let the
agent decide or draft the request, and reserve deterministic gates for explicit safety or
authorization boundaries.

Favor one well-instructed agent or reusable preset over many small deterministic nodes. Put
output contracts, Slack style, dedupe rules, tenant boundaries, tool permissions, and
permitted side effects directly in the preset instructions — the agent reads those, not repo
files. When the agent needs a tool, grant it the narrow tool directly rather than wrapping
the same call in a workflow action.

A preset's runtime has more capability than `list_actions` shows. The agent runs inside a
sandbox with a real shell and file tools (`Bash`, `Read`, `Write`, `Edit`, `Glob`, `Grep`),
plus CLI utilities: Python and `uv`, `curl`, `jq`, and the DuckDB CLI for local SQL over CSV,
JSON, and Parquet. So an agent can shape data, parse JSON, and run tabular queries without a
workflow node for it. Before adding a workflow helper or preprocessing step to compensate for
a presumed limitation, inspect the preset/runtime context. When the agent can safely do the
bounded work itself, keep it in the preset instructions.

## Authoring Defaults

- Prefer linear, readable graphs. Keep ordinary workflows around 20 nodes or fewer and
  agentic workflows around 6 nodes or fewer.
- For agentic workflows, push most behavior into the agent prompt/preset. For event-driven
  workflows, a single reshape/redact/upsert action before the agent is usually the right
  deterministic boundary.
- Use `ai.agent` / `ai.preset_agent` for the agentic work; `core.http_request` for
  deterministic API calls; `core.script.run_python` for deterministic data work — details and
  loop/concurrency rules in [run-python](references/run-python.md).
- Do not give `core.http_request` to an agent unless the user explicitly accepts the broad
  network capability. Put deterministic HTTP in the workflow graph or a tightly scoped subflow.
- Split into subflows only when there is a real orchestration boundary, reusable child
  workflow, separate execution history, approvals, long-running actions, or independent
  checkpointing. Use `core.workflow.execute` and prefer `workflow_alias` over hard-coded IDs.
  Subflow bulk defaults: `loop_strategy: batch`, `batch_size: 32`, `fail_strategy: isolated`,
  `wait_strategy: wait` (use `detach` only when the parent does not need child results).
- Keep run-python and agent outputs small: downstream rows, summary counts, bounded error
  samples.
- Prefer agent presets when reusable behavior already exists. Use inline `ai.agent` only when
  the prompt should live with one workflow.
- Prefer the `model` object for inline `ai.agent`; top-level `model_name`/`model_provider` are
  deprecated unless the user asks for the legacy shape.

```yaml
args:
  model:
    model_name: claude-sonnet-4-6
    model_provider: anthropic
```

## Common Mistakes

- Using Python for the *agentic* part — composing or posting an agent's Slack message, or
  making routing/judgment calls in a script. The agent owns message composition, posting (via
  a Slack tool), and judgment; Python owns deterministic data plumbing.
- Workflow action names are not MCP tool names. Use MCP tools such as `create_workflow` to
  manage workflows, and action names such as `core.http_request` only inside workflow YAML.
- Inventing `tools.*` action names. Discover actions with `list_actions`, then inspect exact
  schemas with `get_action_context`.
- Using `for_each` or scatter/gather for normal loops, batching, filtering, joins, or table
  writes — prefer a `core.script.run_python` loop (see [run-python](references/run-python.md)).
- Using `insert_rows(..., upsert=True)` without a unique index on the key column (see
  [tables](references/tables.md)).
- Granting `core.http_request` to an agent without explicit user approval of broad network
  access.

## Agent Presets

Before creating or updating presets, call `get_agent_preset_authoring_context` and
`list_integrations`. Check workspace model credentials, attachable MCP integrations, output
type options, variables, and available tools.

Give presets only the tools they need. Encode routing, table names, output style, and
production action rules directly in the preset instructions; the agent reads its own
instructions, not repo files.
