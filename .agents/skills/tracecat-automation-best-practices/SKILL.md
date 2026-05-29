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

## Authoring Defaults

- Prefer linear, readable workflow graphs when parallelism is not materially useful.
- Do not use `core.transform.scatter` / `core.transform.gather` for ordinary data transforms. Normalize, dedupe, join, filter, sort, and batch upsert inside `core.script.run_python`.
- Keep run-python outputs small: downstream rows, summary counts, and bounded error samples.
- Prefer the `model` object for `ai.agent`; top-level `model_name` and `model_provider` are deprecated unless the user explicitly asks for the legacy shape.

```yaml
args:
  model:
    model_name: claude-sonnet-4-6
    model_provider: anthropic
```

Use `ai.preset_agent` when a reusable agent should be maintained separately from a workflow. Use inline `ai.agent` when the behavior is tightly coupled to one workflow and the prompt should travel with that workflow.

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
