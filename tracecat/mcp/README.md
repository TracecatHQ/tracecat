# Tracecat MCP Tools

This document lists the currently registered MCP tools in
`tracecat/mcp/server.py` (the functions decorated with `@mcp.tool()`).

## Workflow tools

- `list_workspaces()`
- `create_workflow(workspace_id, title, description="")`
- `get_workflow(workspace_id, workflow_id)`
- `get_workflow_file(workspace_id, workflow_id, draft=True)`
- `prepare_workflow_file_upload(workspace_id, relative_path, operation, workflow_id=None, update_mode="patch")`
- `create_workflow_from_uploaded_file(workspace_id, artifact_id, title=None, description="", use_workflow_id=False)`
- `update_workflow_from_uploaded_file(workspace_id, workflow_id, artifact_id, title=None, description=None, status=None, alias=None, error_handler=None, update_mode=None)`
- `update_workflow(workspace_id, workflow_id, title=None, description=None, status=None, alias=None, error_handler=None)`
- `list_workflows(workspace_id, status=None, limit=50, search=None)`
- `validate_workflow(workspace_id, workflow_id)`
- `publish_workflow(workspace_id, workflow_id)`
- `run_draft_workflow(workspace_id, workflow_id, inputs=None, title=None, description=None)`
- `run_published_workflow(workspace_id, workflow_id, inputs=None)`
- `list_workflow_executions(workspace_id, workflow_id, limit=20)`
- `get_workflow_execution(workspace_id, execution_id)`

### Workflow file transfer notes

- Remote `/mcp` clients use staged blob transfer instead of inline YAML:
  - `get_workflow_file` returns a short-lived `download_url`
  - `prepare_workflow_file_upload` returns a short-lived `upload_url` plus an opaque `artifact_id`
  - `create_workflow_from_uploaded_file` and `update_workflow_from_uploaded_file` finalize from that staged artifact
- For workflow updates, `update_mode` is bound during `prepare_workflow_file_upload`; the finalize call may omit it or repeat the same value, but it cannot override the prepared artifact.
- Staged workflow files are stored in the existing workflow blob bucket under workspace-scoped prefixes and artifact metadata is bound server-side to workspace, organization, MCP client, and MCP session to prevent cross-tenant IDOR.
- Legacy workflow CRUD tools are metadata-only; workflow definitions are only read or written through the workflow file tools.

## Action discovery and authoring context

- `list_actions(workspace_id, query=None, namespace=None, limit=50)`
- `get_action_context(workspace_id, action_name)`
- `get_workflow_authoring_context(workspace_id, action_names_json=None, query=None)`
- `prepare_template_file_upload(workspace_id, relative_path)`
- `validate_template_action(workspace_id, artifact_id, check_db=False)`

## Webhook and case trigger tools

- `get_webhook(workspace_id, workflow_id)`
- `update_webhook(workspace_id, workflow_id, status=None, methods=None, entrypoint_ref=None, allowlisted_cidrs=None)`
- `get_case_trigger(workspace_id, workflow_id)`
- `update_case_trigger(workspace_id, workflow_id, status=None, event_types=None, tag_filters=None)`

## Workflow tag tools

- `list_workflow_tags(workspace_id)`
- `create_workflow_tag(workspace_id, name, color=None)`
- `update_workflow_tag(workspace_id, tag_id, name=None, color=None)`
- `delete_workflow_tag(workspace_id, tag_id)`
- `list_tags_for_workflow(workspace_id, workflow_id)`
- `add_workflow_tag(workspace_id, workflow_id, tag_id)`
- `remove_workflow_tag(workspace_id, workflow_id, tag_id)`

## Case tag tools

- `list_case_tags(workspace_id)`
- `create_case_tag(workspace_id, name, color=None)`
- `update_case_tag(workspace_id, tag_id, name=None, color=None)`
- `delete_case_tag(workspace_id, tag_id)`
- `list_tags_for_case(workspace_id, case_id)`
- `add_case_tag(workspace_id, case_id, tag_identifier)`
- `remove_case_tag(workspace_id, case_id, tag_identifier)`

## Case field tools

- `list_case_fields(workspace_id)`
- `create_case_field(workspace_id, name, type, options=None)`
- `update_case_field(workspace_id, field_id, name=None, type=None, options=None)`
- `delete_case_field(workspace_id, field_id)`

## Table tools

- `list_tables(workspace_id)`
- `create_table(workspace_id, name, columns_json=None)`
- `get_table(workspace_id, table_id)`
- `update_table(workspace_id, table_id, name=None)`
- `insert_table_row(workspace_id, table_id, row_json, upsert=False)`
- `update_table_row(workspace_id, table_id, row_id, row_json)`
- `search_table_rows(workspace_id, table_id, search_term=None, limit=100, offset=0)`
- `export_csv(workspace_id, table_id, include_header=True)`

### Template and CSV file transfer notes

- `validate_template_action` validates a staged uploaded file via `artifact_id` for remote `/mcp` clients.
- `prepare_template_file_upload` is required for remote template validation.
- `export_csv` no longer returns inline CSV text. It returns a short-lived `download_url` for remote `/mcp` clients.

## Variable and secret metadata tools

- `list_variables(workspace_id, environment=DEFAULT_SECRETS_ENVIRONMENT)`
- `get_variable(workspace_id, variable_name, environment=DEFAULT_SECRETS_ENVIRONMENT)`
- `list_secrets_metadata(workspace_id, environment=DEFAULT_SECRETS_ENVIRONMENT)`
- `get_secret_metadata(workspace_id, secret_name, environment=DEFAULT_SECRETS_ENVIRONMENT)`
