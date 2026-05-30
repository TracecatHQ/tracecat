---
name: tracecat-automation-best-practices
description: Use when building, editing, validating, or debugging generic Tracecat automations through Tracecat MCP, including workflow DSL/YAML authoring, table design, unique indexes, run-python Tracecat imports, agent presets, ai.agent or ai.preset_agent workflows, executions, validation, and workflow best practices. Do not use for Slack bot-specific setup unless the user asks for Slack, app mentions, interactive messages, or event subscriptions.
---

# Tracecat Automation Best Practices

## Workflow

Use this skill for generic Tracecat automation work. Start from live MCP context rather than guessing:

1. Discover the workspace with `workspaces_list_workspaces`.
2. Read `tracecat://platform/dsl-reference` if DSL syntax or examples are needed.
3. Use `workflows_get_workflow_authoring_context` for action schemas, variables, and secrets.
4. For existing workflows, use `workflows_get_workflow`, then targeted `workflows_edit_workflow` patches against `draft_document` and `draft_revision`.
5. Validate with `workflows_validate_workflow`, run a draft or published execution when appropriate, then inspect failures with `workflows_list_workflow_executions` and `workflows_get_workflow_execution`.

When stuck on DSL behavior, Tracecat is open source at https://github.com/TracecatHQ/tracecat and the repo has sample workflows. Treat `platform/automations/001_google_oauth/workflow.yaml` as the clean reference for a readable production automation.

Before building, ask the user to clarify any missing production choice that
changes the workflow contract: target workspace, provider/integration, secret or
credential source, publish/run behavior, destructive side effects, approval
requirements, or acceptance criteria. If the request is already specific enough,
proceed.

Sketch the workflow shape before authoring it. Prefer a visually readable
left-to-right or top-to-bottom flow, keep refs human-readable, minimize branch
fan-out, and keep layout action refs aligned with definition action refs. Use
the live MCP DSL reference for exact syntax instead of copying examples from
this skill.

## Authoring Defaults

- Prefer linear, readable workflow graphs by default. Visual ease and fewer branches are usually more valuable than maximum parallelism.
- Keep a workflow to roughly 20 nodes or fewer. First try to simplify or consolidate with `core.script.run_python` or an agent/preset; split into named subflows only when the remaining work is a real orchestration boundary with clear inputs and outputs.
- Keep agentic workflows to roughly 6 nodes or fewer. The graph should set context, call the agent or preset, and handle the result; move deterministic collection, joins, filtering, and batching into scripts unless subflows are clearly needed.
- Use workflow folders to group related parent workflows, subflows, and support utilities.
- Do not use `core.transform.scatter` / `core.transform.gather` for ordinary data transforms. Normalize, dedupe, join, filter, sort, and batch upsert inside `core.script.run_python`.
- Prefer agents or agent presets for judgment, summarization, investigation, and tool-using decisions. If the task is just one deterministic API call, use `core.http_request` instead of an agent.
- Do not give `core.http_request` to an agent unless the user explicitly accepts the risk. It effectively gives the agent unbounded curl-like access. Put deterministic HTTP calls in the workflow graph or a tightly scoped subflow instead.
- Use HTTP pagination actions for paginated APIs. For transforms, especially nested loops, joins, grouping, dedupe, or large collection shaping, use `core.script.run_python` instead of expression chains.
- Keep run-python outputs small: downstream rows, summary counts, and bounded error samples.
- Prefer agent presets when an appropriate preset already exists. Use inline `ai.agent` only when the behavior is tightly coupled to one workflow and the prompt should travel with that workflow.
- Prefer the `model` object for inline `ai.agent`; top-level `model_name` and `model_provider` are deprecated unless the user explicitly asks for the legacy shape.

```yaml
args:
  model:
    model_name: claude-sonnet-4-6
    model_provider: anthropic
```

Use `ai.preset_agent` when a reusable agent should be maintained separately from a workflow.

## Existing Workflow Edits

- Prefer `workflows_edit_workflow` over replacing full YAML for focused changes.
- Patch against the `draft_document` returned by `workflows_get_workflow`; action paths start under `/definition/actions/...`, not `/actions/...`.
- For nontrivial edits, call `workflows_edit_workflow` with `validate_only: true`, then apply the same patch with `validate_only: false` and the revision returned by validation.
- Treat `draft_revision` as sequential state. Do not run parallel edits against the same workflow draft; use the returned revision before the next patch.
- When adding or renaming actions, update `depends_on` and matching layout action refs in the same edit so the graph stays valid and readable.

## Workflow Architecture

- Start with a linear workflow and consolidate deterministic work before adding branches. Prefer `core.script.run_python` for data shaping, API loops, table writes, batching, dedupe, joins, retries, and per-item error handling. Prefer `ai.agent` or `ai.preset_agent` for investigation, judgment, summarization, and tool-using decisions.
- Break large automations into a small orchestrator workflow plus focused subflows only when consolidation would hide important runtime boundaries or when each child workflow is independently useful. The parent should route, checkpoint, and call subflows; subflows should do one coherent job.
- Use `core.workflow.execute` for subflows. Prefer `workflow_alias` over hard-coded workflow IDs when aliases are available.
- Do not use subflow fan-out just to avoid writing a compact script. Use subflow fan-out when each item needs the workflow runtime boundary, separate execution history, retries, approvals, long-running actions, or independent checkpointing.
- For collection fan-out that really is a subflow use case, prefer looped `core.workflow.execute` over building a large parent workflow graph. Keep the parent responsible for preparing inputs and aggregating/checkpointing results.
- Use subflow `loop_strategy: batch` for most bulk work. It is safer than fully parallel fan-out.
- Default subflow loop options are `loop_strategy: batch`, `batch_size: 32`, `fail_strategy: isolated`, and `wait_strategy: wait`. Set `wait_strategy: detach` explicitly when the parent should not wait.
- For batched subflows, start with `batch_size: 32`. Lower it for rate-limited APIs or expensive actions; raise it only when the downstream system and Tracecat tier can handle the concurrency.
- Use `wait_strategy: detach` for fire-and-forget fan-out where the parent only needs child workflow IDs. Use `wait_strategy: wait` when the parent must aggregate child results or gate downstream actions on subflow success.
- Keep `fail_strategy: isolated` for bulk work so one failed item does not fail the whole batch. Use `fail_strategy: all` only when any subflow failure should fail the parent action.
- Use tables as checkpoints between stages and subflows. Store normalized inputs, per-item status, run IDs, timestamps, and bounded error samples so reruns can resume or explain partial progress.

## Run-Python Imports

Use direct Tracecat imports inside `core.script.run_python` when consolidating table or HTTP side effects reduces graph noise:

```python
from tracecat_registry.core.http import http_request
from tracecat_registry.core.table import create_table, insert_row, insert_rows, lookup, update_row

async def main(rows):
    await create_table(
        name="automation_inventory",
        columns=[
            {"name": "external_id", "type": "TEXT"},
            {"name": "payload", "type": "JSONB"},
        ],
        raise_on_duplicate=False,
    )

    inserted = 0
    for start in range(0, len(rows), 1000):
        inserted += await insert_rows(
            table="automation_inventory",
            rows_data=rows[start:start + 1000],
            upsert=True,
        )
    return {"input_rows": len(rows), "inserted": inserted}
```

Use `async def main(...)`, `await` imported actions, pass named arguments, and chunk writes. Catch expected per-item failures inside bulk actions and return counts plus a few examples instead of failing the whole workflow.

## Tables

- `core.table.create_table` creates columns but not unique indexes.
- Before using `insert_rows(..., upsert=True)`, create a unique index on the intended key column.
- Keep `insert_rows` batches at or below 1000 rows.
- Use table storage for durable workflow state and agent-readable inventory.
- Keep opaque identifiers as strings, especially numeric-looking IDs that may exceed integer limits.

For MCP table operations, use `tables_list_tables`, `tables_get_table`, `tables_create_column_index`, `tables_search_table_rows`, and `tables_export_csv` as needed. For workflow YAML, use `core.table.*` actions or `tracecat_registry.core.table` imports inside run-python.

## Agent Presets

Before creating or updating presets, call `agents_get_agent_preset_authoring_context` and `integrations_list_integrations`. Check workspace model credentials, attachable MCP integrations, output type options, variables, and available tools.

Give presets only the tools they need. Encode routing, table names, output style, and production action rules directly in the preset instructions; the agent reads its own instructions, not repo files.
