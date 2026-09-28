# Tracecat MCP Tools

This document lists the currently registered MCP tools in
`tracecat/mcp/server.py` (the functions decorated with `@mcp.tool()`).

Top-level list/search tools return a paginated object with:
`items`, `next_cursor`, `prev_cursor`, `has_more`, and `has_previous`.
Object-style authoring/context tools remain object responses and document their
own embedded collection truncation behavior below.

## Workflow tools

- `list_workspaces(limit=20, cursor=None)`
- `create_workflow(workspace_id, title, description="", definition_yaml=None)`
- `get_workflow(workspace_id, workflow_id, include_definition_yaml=False)`
- `list_workflow_actions(workspace_id, workflow_id)`
- `get_workflow_action(workspace_id, workflow_id, ref)`
- `update_workflow(workspace_id, workflow_id, title=None, description=None, status=None, alias=None, error_handler=None, definition_yaml=None, update_mode="patch")`
- `edit_workflow(workspace_id, workflow_id, base_revision, patch_ops, validate_only=False)`
- `list_workflows(workspace_id, status=None, limit=50, search=None, cursor=None)`
- `list_workflow_tree(workspace_id, path="/", depth=1, include_workflows=True, limit=20, cursor=None)`
- `validate_workflow(workspace_id, workflow_id)`
- `publish_workflow(workspace_id, workflow_id)`
- `run_workflow(workspace_id, workflow_id, inputs=None, use_draft=True, version=None)`
- `list_workflow_executions(workspace_id, workflow_id, limit=20, cursor=None)`
- `get_workflow_execution(workspace_id, execution_id, action_refs=None, include_results=True)`
- `get_execution_action_result(workspace_id, execution_id, action_ref, stream_id=None, max_bytes=65536, offset=0)`

### Workflow definition editing

- For existing workflow changes, prefer `edit_workflow`. If the latest
  `draft_document` and `draft_revision` are already in context, reuse them and
  send the smallest RFC 6902 JSON Patch that changes the intended fields. Call
  `get_workflow` only when the latest draft is missing, stale, or a revision
  conflict says the draft changed.
- On large workflows, avoid `get_workflow` entirely: `list_workflow_actions`
  returns the `draft_revision` plus one `{index, ref, action, depends_on, ...}`
  row per action, and `get_workflow_action` returns a single action with its
  layout entry.
- Address actions by ref in patch paths: `/definition/actions/@<ref>` (any
  suffix, e.g. `/definition/actions/@build_alert/args/url`) and
  `/layout/actions/@<ref>`. Each op resolves the ref against the document as
  it stands when that op runs, so earlier removes do not shift later paths.
  `add` to a bare `/definition/actions/@<ref>` appends a new action whose
  `ref` must match; `remove` deletes that action; an unknown ref fails the
  patch and lists the known refs. Numeric paths still work, but the server
  re-sorts actions by ref on save.
- Both `validate_only` and applied `edit_workflow` responses include
  `actions: [{index, ref}]` in post-save order.
- Use `update_workflow` without `definition_yaml` for metadata-only updates.
- Use inline YAML on `create_workflow` and `update_workflow` only when creating
  a workflow from YAML or intentionally replacing/bulk-updating the definition.
  Inline YAML is supported up to the configured MCP input limit.
- `get_workflow(include_definition_yaml=True)` returns inline `definition_yaml`
  when the serialized workflow fits the configured MCP input limit; otherwise
  it returns `definition_transport="too_large"`.
- Template validation uploads and CSV exports still use separate staged blob
  transfer tools.

### Execution results

- `get_workflow_execution` inlines each event `result` only when its JSON is
  short and otherwise returns a cut-off `result_truncated` preview. Pass
  `action_refs` to keep only those actions (the synthetic
  `__workflow_trigger__`, `__workflow_completed__`, and `__workflow_failure__`
  events are always kept) and `include_results=False` for a status-and-timing
  timeline with no result payloads.
- `get_execution_action_result` returns one action's complete result as JSON
  text in byte windows (`result`, `total_bytes`, `offset`, `truncated`,
  `next_offset`; `max_bytes` is capped at 1 MiB). Results the engine offloaded
  to blob storage are dereferenced server-side. When a ref ran in several
  streams (scatter items), pass `stream_id`; the error lists the available ids.

## Action discovery and authoring context

- `list_actions(workspace_id, query=None, namespace=None, limit=50, cursor=None)`
- `get_action_context(workspace_id, action_name)`
- `get_workflow_authoring_context(workspace_id, action_names_json=None, query=None)`
- `prepare_template_file_upload(workspace_id, relative_path)`
- `validate_template_action(workspace_id, artifact_id, check_db=False)`

`get_workflow_authoring_context` now caps embedded collections and returns
truncation metadata under `truncation.collections`.

## Webhook and case trigger tools

- `get_webhook(workspace_id, workflow_id)`
- `update_webhook(workspace_id, workflow_id, status=None, methods=None, entrypoint_ref=None, allowlisted_cidrs=None)`
- `get_case_trigger(workspace_id, workflow_id)`
- `update_case_trigger(workspace_id, workflow_id, status=None, event_types=None, tag_filters=None)`

## Workflow tag tools

- `list_workflow_tags(workspace_id, limit=20, cursor=None)`
- `create_workflow_tag(workspace_id, name, color=None)`
- `update_workflow_tag(workspace_id, tag_id, name=None, color=None)`
- `delete_workflow_tag(workspace_id, tag_id)`
- `list_tags_for_workflow(workspace_id, workflow_id, limit=20, cursor=None)`
- `add_workflow_tag(workspace_id, workflow_id, tag_id)`
- `remove_workflow_tag(workspace_id, workflow_id, tag_id)`

## Case tag tools

- `list_case_tags(workspace_id, limit=20, cursor=None)`
- `create_case_tag(workspace_id, name, color=None)`
- `update_case_tag(workspace_id, tag_id, name=None, color=None)`
- `delete_case_tag(workspace_id, tag_id)`
- `list_tags_for_case(workspace_id, case_id, limit=20, cursor=None)`
- `add_case_tag(workspace_id, case_id, tag_identifier)`
- `remove_case_tag(workspace_id, case_id, tag_identifier)`

## Case field tools

- `list_case_fields(workspace_id, limit=20, cursor=None)` returns field objects with `id`, `type`, `description`, `nullable`, `default`, `reserved`, `options`, and optional `kind`
- `create_case_field(workspace_id, name, type, kind=None, options=None)` where `kind` is create-only; valid values are `LONG_TEXT` with `type=TEXT` and `URL` with `type=JSONB`
- `update_case_field(workspace_id, field_id, name=None, type=None, options=None)`

Case field and table `type` values are:
- `TEXT`
- `INTEGER`
- `NUMERIC`
- `BOOLEAN`
- `DATE`
- `TIMESTAMPTZ`
- `JSONB`
- `SELECT`
- `MULTI_SELECT`

## Case dropdown tools

- `list_case_dropdowns(workspace_id, limit=20, cursor=None)` returns dropdown definitions with embedded `options`
- `create_case_dropdown(workspace_id, name, ref=None, icon_name=None, is_ordered=False, required_on_closure=False, position=0, options=None)`
- `update_case_dropdown(workspace_id, dropdown_id, name=None, ref=None, icon_name=None, is_ordered=None, required_on_closure=None, position=None)`
- `delete_case_dropdown(workspace_id, dropdown_id)` deletes the definition with all its options and per-case values
- `add_case_dropdown_option(workspace_id, dropdown_id, label, ref=None, icon_name=None, color=None, position=None)`
- `update_case_dropdown_option(workspace_id, dropdown_id, option_id, label=None, ref=None, icon_name=None, color=None, position=None)`
- `delete_case_dropdown_option(workspace_id, dropdown_id, option_id)`
- `set_case_dropdown_value(workspace_id, case_id, definition_id=None, definition_ref=None, option_id=None, option_ref=None)`

Notes:

- All case dropdown tools require the case add-ons entitlement.
- Dropdown and option `ref` values default to the slugified name or label
  (e.g. `Threat Level` becomes `threat_level`).
- `set_case_dropdown_value` takes exactly one of `definition_id` or
  `definition_ref`, and at most one of `option_id` or `option_ref`; omitting
  both option arguments clears the value.
- `create_case` and `update_case` accept a `dropdown_values` list with the
  same per-item identifier rules to set dropdown values in the same call.

## Table tools

- `list_tables(workspace_id, limit=20, cursor=None)`
- `create_table(workspace_id, name, columns_json=None)`
- `get_table(workspace_id, table_id)`
- `update_table(workspace_id, table_id, name=None)`
- `create_column(workspace_id, table_id, column)`
- `insert_table_row(workspace_id, table_id, row_json, upsert=False)`
- `insert_rows(workspace_id, table_id, rows_json, upsert=False)`
- `update_table_row(workspace_id, table_id, row_id, row_json)`
- `update_rows(workspace_id, table_id, row_ids, row_json)`
- `search_table_rows(workspace_id, table_id, search_term=None, limit=100, cursor=None)`
- `export_csv(workspace_id, table_id, include_header=True)`

### Template and CSV file transfer notes

- `validate_template_action` validates a staged uploaded file via `artifact_id` for remote `/mcp` clients.
- `prepare_template_file_upload` is required for remote template validation.
- `export_csv` no longer returns inline CSV text. It returns a short-lived `download_url` for remote `/mcp` clients.

### Skill file transfer notes

- The staged flow is the only skill upload path: call `prepare_skill_upload`, send raw HTTP PUTs to the returned short-lived URLs, then call `complete_skill_upload`. Inline base64 skill uploads are not supported.
- Skill reads are draft-only. `get_skill` returns the draft manifest, and with a `path` it returns small UTF-8 text inline and otherwise a short-lived presigned download URL.
- For whole-directory hydration, call `prepare_skill_download` and pass its presigned GET plan to the local helper, which streams files to disk and verifies SHA-256 without putting file contents in model context.

## Variable and secret metadata tools

- `list_variables(workspace_id, environment=DEFAULT_SECRETS_ENVIRONMENT, limit=20, cursor=None)`
- `get_variable(workspace_id, variable_name, environment=DEFAULT_SECRETS_ENVIRONMENT)`
- `list_secrets_metadata(workspace_id, environment=DEFAULT_SECRETS_ENVIRONMENT, limit=20, cursor=None)`
- `get_secret_metadata(workspace_id, secret_name, environment=DEFAULT_SECRETS_ENVIRONMENT)`

## Integration and agent tools

- `list_integrations(workspace_id)`
- `get_agent_preset_authoring_context(workspace_id)`
- `list_skills(workspace_id, limit=20, cursor=None)`
- `list_skill_tree(workspace_id, path="/", depth=1, include_skills=True, limit=20, cursor=None)`
- `create_skill_folder(workspace_id, path, parents=False)`
- `rename_skill_folder(workspace_id, path, new_name)`
- `move_skill_folder(workspace_id, path, destination_parent_path="/")`
- `delete_skill_folder(workspace_id, path, recursive=False)`
- `move_skills(workspace_id, skill_slugs, destination_path="/", dry_run=False)`
- `get_skill(workspace_id, skill_id, path=None)`
- `prepare_skill_download(workspace_id, skill_id)`
- `prepare_skill_upload(workspace_id, files, skill_id=None, name=None, description=None)`
- `complete_skill_upload(workspace_id, skill_id, base_revision, files)`
- `publish_skill(workspace_id, skill_id)`
- `create_agent_preset(workspace_id, name, slug=None, description=None, instructions=None, model_name=None, model_provider=None, base_url=None, output_type=None, actions=None, namespaces=None, tool_approvals=None, mcp_integration_ids=None, retries=None, enable_thinking=None, enable_internet_access=None, skills=None)`
- `update_agent_preset(workspace_id, preset_slug, name=None, slug=None, description=None, instructions=None, model_name=None, model_provider=None, base_url=None, output_type=None, clear_output_type=False, actions=None, namespaces=None, tool_approvals=None, mcp_integration_ids=None, retries=None, enable_thinking=None, enable_internet_access=None, skills=None)`
- `list_agent_presets(workspace_id, limit=20, cursor=None)`
- `get_agent_preset(workspace_id, preset_slug)`
- `run_agent_preset(workspace_id, preset_slug, prompt, preset_version=None, timeout_seconds=120)`

`list_integrations` and `get_agent_preset_authoring_context` keep object
responses but cap embedded collections and expose `truncation.collections`.
