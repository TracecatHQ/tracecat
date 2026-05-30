---
name: tracecat-automation-best-practices
description: Use when building, editing, validating, or debugging generic Tracecat automations through Tracecat MCP, including workflow DSL/YAML authoring, table design, unique indexes, run-python Tracecat imports, agent presets, ai.agent or ai.preset_agent workflows, executions, validation, and workflow best practices.
---

# Tracecat Automation Best Practices

## Workflow

Start from live MCP context rather than guessing:

1. Discover the workspace with `workspaces_list_workspaces`.
2. Read `tracecat://platform/dsl-reference` when DSL syntax or examples are needed.
3. Use `workflows_get_workflow_authoring_context` for action schemas, variables, and secrets.
4. For existing workflows, use `workflows_get_workflow`, then targeted `workflows_edit_workflow` patches against `draft_document` and `draft_revision`.
5. Validate with `workflows_validate_workflow`, run a draft or published execution when appropriate, then inspect failures with `workflows_list_workflow_executions` and `workflows_get_workflow_execution`.

Clarify production choices that change the workflow contract: workspace, integration/provider, secret source, publish/run behavior, destructive side effects, approvals, or acceptance criteria. If the request is already specific enough, proceed.

Sketch the workflow shape before authoring. Prefer readable left-to-right or top-to-bottom flows, human-readable refs, few branches, and layout refs aligned with definition refs. Use the live DSL reference for exact syntax.

## Authoring Defaults

- Prefer linear, readable graphs. Keep ordinary workflows around 20 nodes or fewer and agentic workflows around 6 nodes or fewer.
- Consolidate deterministic work with `core.script.run_python`: data shaping, API loops, retries, batching, dedupe, joins, sorting, chunked table writes, and per-item error handling.
- Prefer `ai.agent` or `ai.preset_agent` for investigation, judgment, summarization, and tool-using decisions. Use `core.http_request` for deterministic API calls.
- Do not give `core.http_request` to an agent unless the user explicitly accepts the broad network capability. Put deterministic HTTP in the workflow graph or a tightly scoped subflow.
- Use HTTP pagination actions for paginated APIs. Keep paginated results bounded before storing or returning them.
- Prefer `core.script.run_python` loops over action-level `for_each`, even for bounded lists. `for_each` can create enough scheduled work to hurt the scheduler. Use it only when the user explicitly needs separate workflow action runs per item and accepts the concurrency tradeoff.
- Prefer `core.script.run_python` loops over `core.transform.scatter` / `core.transform.gather`. Scatter/gather has the same scheduler/concurrency risk and should be reserved for explicit workflow-stream fan-out/fan-in requirements.
- Use `core.loop.start` / `core.loop.end` only when each iteration depends on prior action output.
- Split into subflows only when there is a real orchestration boundary, reusable child workflow, separate execution history, approvals, long-running actions, retries, or independent checkpointing.
- Use `core.workflow.execute` for subflows and prefer `workflow_alias` over hard-coded workflow IDs.
- For subflow bulk work, default to `loop_strategy: batch`, `batch_size: 32`, `fail_strategy: isolated`, and `wait_strategy: wait`. Use `wait_strategy: detach` only when the parent does not need child results.
- Keep run-python and agent outputs small: downstream rows, summary counts, and bounded error samples.
- Prefer agent presets when reusable behavior already exists. Use inline `ai.agent` only when the prompt should live with one workflow.
- Prefer the `model` object for inline `ai.agent`; top-level `model_name` and `model_provider` are deprecated unless the user asks for the legacy shape.

```yaml
args:
  model:
    model_name: claude-sonnet-4-6
    model_provider: anthropic
```

## Existing Workflow Edits

- Prefer `workflows_edit_workflow` over replacing full YAML for focused changes.
- Patch against the `draft_document` returned by `workflows_get_workflow`; action paths start under `/definition/actions/...`, not `/actions/...`.
- For nontrivial edits, call `workflows_edit_workflow` with `validate_only: true`, then apply the same patch with `validate_only: false` against the same `base_revision`.
- Treat `draft_revision` as sequential state. After a successful write, use the returned revision before the next patch.
- When adding or renaming actions, update `depends_on` and matching layout action refs in the same edit.

## Run-Python Imports

Use direct Tracecat imports inside `core.script.run_python` when consolidating table or HTTP side effects reduces graph noise:

```python
from tracecat_registry.core.http import http_request, http_paginate
from tracecat_registry.core.table import create_table, insert_rows, lookup, update_row

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
            upsert=False,
        )
    return {"input_rows": len(rows), "inserted": inserted}
```

Use `async def main(...)`, `await` imported actions, pass named arguments, and chunk writes. Catch expected per-item failures inside bulk actions and return counts plus a few examples instead of failing the whole workflow.

## Tables

- `core.table.create_table` creates columns but not unique indexes.
- Before using `insert_rows(..., upsert=True)`, create a unique index on the intended key column.
- Keep `insert_rows` batches at or below 1000 rows.
- Use table storage for durable workflow state, checkpoints between stages and subflows, per-item status, run IDs, timestamps, and bounded error samples.
- Keep opaque identifiers as strings, especially numeric-looking IDs that may exceed integer limits.
- Keep table names, column names, and case field names under 63 characters.

For MCP table operations, use `tables_list_tables`, `tables_get_table`, `tables_create_column_index`, `tables_search_table_rows`, and `tables_export_csv`. For workflow YAML, use `core.table.*` actions or `tracecat_registry.core.table` imports inside run-python.

## Common Mistakes

- Workflow action names are not MCP tool names. Use MCP tools such as `workflows_create_workflow` to manage workflows, and use action names such as `core.http_request` only inside workflow YAML.
- Do not invent `tools.*` action names. Discover actions with `workflows_list_actions`, then inspect exact schemas with `workflows_get_action_context`.
- Use `core.script.run_python` for ordinary collection processing; avoid `for_each` and scatter/gather for normal loops, batching, filtering, joins, or table writes.
- Do not use table `insert_rows(..., upsert=True)` unless the table has a unique index on the key column used to match existing rows.
- Do not grant `core.http_request` to an agent unless the user explicitly approves broad network access.

## Agent Presets

Before creating or updating presets, call `agents_get_agent_preset_authoring_context` and `integrations_list_integrations`. Check workspace model credentials, attachable MCP integrations, output type options, variables, and available tools.

Give presets only the tools they need. Encode routing, table names, output style, and production action rules directly in the preset instructions; the agent reads its own instructions, not repo files.
