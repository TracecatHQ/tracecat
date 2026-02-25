# Tracecat MCP Tools

This document lists the currently registered MCP tools in
`tracecat/mcp/server.py` (the functions decorated with `@mcp.tool()`).

## Workflow tools

- `list_workspaces()`
- `create_workflow(workspace_id, title, description="", definition_yaml=None)`
- `get_workflow(workspace_id, workflow_id)`
- `update_workflow(workspace_id, workflow_id, title=None, description=None, status=None, alias=None, error_handler=None, definition_yaml=None, update_mode="patch")`
- `list_workflows(workspace_id, status=None, limit=50, search=None)`
- `validate_workflow(workspace_id, workflow_id)`
- `publish_workflow(workspace_id, workflow_id)`
- `run_draft_workflow(workspace_id, workflow_id, inputs=None, title=None, description=None)`
- `run_published_workflow(workspace_id, workflow_id, inputs=None)`
- `list_workflow_executions(workspace_id, workflow_id, limit=20)`
- `get_workflow_execution(workspace_id, execution_id)`

## Action discovery and authoring context

- `list_actions(workspace_id, query=None, namespace=None, limit=50)`
- `get_action_context(workspace_id, action_name)`
- `get_workflow_authoring_context(workspace_id, action_names_json=None, query=None)`

## Webhook and case trigger tools

- `get_webhook(workspace_id, workflow_id)`
- `update_webhook(workspace_id, workflow_id, status=None, methods=None, entrypoint_ref=None, allowlisted_cidrs=None)`
- `get_case_trigger(workspace_id, workflow_id)`
- `update_case_trigger(workspace_id, workflow_id, status=None, event_types=None, tag_filters=None)`

## Table tools

- `list_tables(workspace_id)`
- `create_table(workspace_id, name, columns_json=None)`
- `get_table(workspace_id, table_id)`
- `update_table(workspace_id, table_id, name=None)`
- `insert_table_row(workspace_id, table_id, row_json, upsert=False)`
- `update_table_row(workspace_id, table_id, row_id, row_json)`
- `search_table_rows(workspace_id, table_id, search_term=None, limit=100, offset=0)`
- `import_csv(workspace_id, csv_content, table_name=None)`
- `export_csv(workspace_id, table_id, include_header=True)`

## Variable and secret metadata tools

- `list_variables(workspace_id, environment=DEFAULT_SECRETS_ENVIRONMENT)`
- `get_variable(workspace_id, variable_name, environment=DEFAULT_SECRETS_ENVIRONMENT)`
- `list_secrets_metadata(workspace_id, environment=DEFAULT_SECRETS_ENVIRONMENT)`
- `get_secret_metadata(workspace_id, secret_name, environment=DEFAULT_SECRETS_ENVIRONMENT)`
