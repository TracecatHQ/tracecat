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
- `get_workflow_graph(workspace_id, workflow_id)`
- `update_workflow(workspace_id, workflow_id, title=None, description=None, status=None, alias=None, error_handler=None, definition_yaml=None, update_mode="patch")`
- `edit_workflow(workspace_id, workflow_id, base_revision, patch_ops, validate_only=False)`
- `list_workflows(workspace_id, status=None, limit=50, search=None, cursor=None)`
- `list_workflow_tree(workspace_id, path="/", depth=1, include_workflows=True, limit=20, cursor=None)`
- `create_workflow_folder(workspace_id, path, parents=False)`
- `rename_workflow_folder(workspace_id, path, new_name)`
- `move_workflow_folder(workspace_id, path, destination_parent_path="/")`
- `delete_workflow_folder(workspace_id, path, recursive=False)`
- `move_workflows(workspace_id, workflow_ids, destination_path="/", dry_run=False)`
- `validate_workflow(workspace_id, workflow_id)`
- `publish_workflow(workspace_id, workflow_id)`
- `run_workflow(workspace_id, workflow_id, inputs=None, use_draft=True, version=None)`
- `rerun_workflow_execution(workspace_id, execution_id, use_draft=True, version=None, fixed_action_refs=None, rerun_from_ref=None, dry_run=False)`
- `list_workflow_action_runs(workspace_id, workflow_id, ref, limit=10)`
- `lock_workflow_action(workspace_id, workflow_id, ref, execution_id)`
- `list_workflow_executions(workspace_id, workflow_id, limit=20, cursor=None)`
- `get_workflow_execution(workspace_id, execution_id, action_refs=None, include_results=True)`
- `get_execution_action_result(workspace_id, execution_id, action_ref, stream_id=None, max_bytes=65536, offset=0)`
- `cancel_workflow_execution(workspace_id, execution_id)`

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
- Call `get_workflow_graph` before adding or editing `run_if`, `depends_on`,
  or joins. It returns a Mermaid `flowchart TD` with refs, action types, loops,
  joins, and conditions (no `args`), plus a per-action `gated_by` list of
  upstream refs whose `run_if` already guards that action.
- The canvas is auto-formatted after `create_workflow` with YAML, and after
  `edit_workflow` or `update_workflow` changes that add actions or change any
  `depends_on`. Supplied layout positions are ignored for those changes.
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
- `cancel_workflow_execution` requests graceful cancellation and needs the
  `workflow:terminate` scope. It returns once the request is sent; call
  `get_workflow_execution` to confirm the run closed as `CANCELED`. Cancelling
  a run that already finished does nothing.

## Workspace settings tools

- `update_workspace_attachment_types(workspace_id, allowed_extensions=None, allowed_mime_types=None)`

Each list replaces the workspace allowlist for case attachments, and an empty
list restores the system defaults. Call it with no lists to read the current
and effective values. Writes need the `workspace:update` scope.

## Organization limit tools

- `get_concurrency_limits(org_id=None)`

Read-only. Returns each per-organization concurrency limit (`limit` null means
unlimited) with its observed usage (`in_use` null means unknown, not zero).
Needs the `org:read` scope. There is no write tool: the platform sets limits.

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

## Form tools

These need the `forms` entitlement, plus scopes:
`list_forms` needs `form:read`; `get_form` needs `form:read` and one of
`form:update` or `form:submission:read`, and reports a form it hides as not
found; `update_form` needs `form:read` and `form:update`, and `form:create`
when it creates the form.

- `list_forms(workspace_id, limit=..., cursor=None)`
- `get_form(workspace_id, form_id)`
- `update_form(workspace_id, workflow_id, status=None, config=None)`

`update_form` creates the form if the workflow has none. `config` replaces the
stored config whole. Submissions are not exposed over MCP.

## Trigger tools

The event source tools need the `object_triggers` entitlement. The trigger
tools are open to every plan; reading, creating or changing a trigger needs its
kind's entitlement (`object_triggers` for `object_storage`, `case_addons` for
`case_trigger`, none for `schedule` and `webhook`), and `list_triggers` leaves out the kinds the plan
lacks and counts them in `locked` (kind, entitlement, count). A `schedule` trigger also needs `schedule:read` to be read or listed,
a `webhook` trigger needs `workflow:read` (its `url` holds the webhook secret, as
in `get_webhook`), and a `case_trigger` trigger needs `workflow:read` too (as in
`get_case_trigger`). Forms are not triggers and `list_triggers` does not list
them; `list_forms` does.
Scopes: list and get tools need `trigger:read`; `create_event_source` and
`create_trigger` need `trigger:create`; the update tools and
`create_trigger_binding` need `trigger:update`. Anything that can make a target run also needs
`workflow:execute` or `agent:execute` for that target: creating a binding,
turning a binding, trigger or source online, changing an agent binding's
instruction, or changing what an online trigger or source matches. Switching
things off and renaming need only `trigger:update`, except for kinds written
through their own resource (`schedule`, `webhook`, `case_trigger`), which also
need that resource's update scope. `endpoint_url` is null
for callers without `trigger:update`.

- `list_event_sources(workspace_id, limit=..., cursor=None)`
- `get_event_source(workspace_id, source_id)`
- `create_event_source(workspace_id, name, config, status="offline")`
- `update_event_source(workspace_id, source_id, name=None, config=None, status=None)`
- `list_triggers(workspace_id, kind=None, source_id=None, workflow_id=None, agent_preset_id=None, limit=..., cursor=None)`
- `get_trigger(workspace_id, trigger_id)`
- `create_trigger(workspace_id, name, config, status=None)`
- `update_trigger(workspace_id, trigger_id, name=None, config=None, status=None)`
- `create_trigger_binding(workspace_id, trigger_id, target, status="online")`
- `update_trigger_binding(workspace_id, trigger_id, binding_id, status=None, instruction=None)`

An event source is an inbound feed (kind `aws_sns`: a customer SNS topic). A
trigger is a rule over a source's events (kind `object_storage`), a workflow
schedule (kind `schedule`), a workflow's webhook (kind `webhook`) or a
workflow's case trigger (kind `case_trigger`). A binding starts a workflow or an agent preset for
each matching event. Each `config` is discriminated by `kind`. There are no
delete tools: take a source, trigger or binding offline instead. The API
deletes triggers and bindings.

A `schedule` trigger is the same schedule a workflow's `schedules` holds, so a
schedule made either way shows in both. Creating one needs `schedule:create`,
`schedule:read` and `workflow:execute` and binds it to its workflow. Renaming
it or updating its config or status needs `schedule:update` (which grants
`schedule:read`) on top of `trigger:update`, and a config replaces every
schedule setting.
Its one binding is fixed: `create_trigger_binding` and `update_trigger_binding`
refuse it.

A `webhook` trigger is the webhook `get_webhook` and `update_webhook` show and
change, so a change made either way shows in both. Every workflow has one
webhook; it is listed as a trigger once it has been turned on (or made a
trigger with `create_trigger`), and stays listed when it goes offline.
Creating one makes the workflow's existing webhook a trigger with the
methods, allowlist and header setting given (settings left out keep their
saved values, and the webhook keeps its on/off status unless `status` is
passed); its URL does not change. That needs `workflow:update` and
`workflow:execute`. Renaming it or updating its config or status needs
`workflow:update` on top of `trigger:update`, and a config sets only the
settings it names. The API key stays
on the workflow's webhook. Its one binding is fixed, as for schedules.

A `case_trigger` trigger is the case trigger `get_case_trigger` and
`update_case_trigger` show and change, the same way as a webhook: every
workflow has one, it is listed once it has been turned on (or made a trigger
with `create_trigger`), and it stays listed when it goes offline. A config sets
only the `event_types` and `tag_filters` it names, and the case trigger keeps
its on/off status unless `status` is passed. Online needs at least one event
type and a published workflow. Creating it, and renaming it or updating its
config or status, need `workflow:update` on top of `trigger:create` or
`trigger:update` (creating also `workflow:execute`), and the `case_addons`
feature; they refuse a managed workflow, as `update_case_trigger` does. Its one binding is fixed, as for
schedules.

## Workflow tag tools

- `list_workflow_tags(workspace_id, limit=20, cursor=None)`
- `create_workflow_tag(workspace_id, name, color=None)`
- `update_workflow_tag(workspace_id, tag_id, name=None, color=None)`
- `delete_workflow_tag(workspace_id, tag_id)`
- `list_tags_for_workflow(workspace_id, workflow_id, limit=20, cursor=None)`
- `add_workflow_tag(workspace_id, workflow_id, tag_id)`
- `remove_workflow_tag(workspace_id, workflow_id, tag_id)`

## Case search

- `search_cases(..., search_mode="full_text", cap_broad_matches=True, include_count=False)` ranks only the 10,000 newest matches when a keyword matches more cases than that, in relevance order. Broad keywords then return in milliseconds instead of seconds at large case counts, but older matches are left out. Pass `order_by` (for example `created_at`) to page through every match, and pass the flag again with each cursor. `include_count`'s `total_estimate` stays exact, so it still reads every match.

## Case verdict

- `create_case(..., verdict=None)` and `update_case(..., verdict=None)` take an OCSF Incident Finding verdict: `unknown`, `false_positive`, `true_positive`, `disregard`, `suspicious`, `benign`, or `test`. Cases have no verdict until one is set; pass `verdict="none"` to `update_case` to clear it.
- `search_cases(filters=...)` and `order_by="verdict"` filter and sort by verdict. Use `{"field": "verdict", "op": "is_null"}` for cases without a verdict.

## Sub-case tools

- `search_cases(..., parent_id=None, hierarchy="all")` lists a parent's sub-cases with `parent_id`, or only top-level cases with `hierarchy="top_level"`
- `create_case(..., parent_id=None)` creates the case as a sub-case of a top-level case
- `set_case_parent(workspace_id, case_ids, parent_id)` groups cases under a top-level parent case
- `clear_case_parent(workspace_id, case_ids)` returns sub-cases to the top level

Notes:

- `get_case`, `list_cases`, and `search_cases` return `parent` and `num_sub_cases` on every case.
- Sub-cases are one level deep. Closing or resolving a parent case also closes or resolves its open sub-cases unless the workspace's `close_sub_cases_with_parent` setting is off.

## Case view tools

Case views are an enterprise feature: these tools, and `search_cases(view_id=...)`, need the case add-ons entitlement.

- `list_case_views(workspace_id, limit=..., cursor=None, pinned=False, include_builtins=True)` lists the built-in views separately from a page of saved views
- `search_cases(workspace_id, view_id=...)` runs a saved or built-in view. The view supplies the matching criteria: a `filters` tree narrows it further, and any other matching argument (such as `status` or `tags`) is rejected. Paging and result options still apply, and `order_by`/`sort` override the view's saved order
- `create_case_view(workspace_id, name, query=None, visibility="personal", order_by=None, sort=None, columns=None)` saves a case search as a view
- `update_case_view(workspace_id, view_id, changes)` renames, re-queries, shares, or unshares a view you can edit
- `pin_case_view(workspace_id, view_id, position=None)` and `unpin_case_view(workspace_id, view_id)` change your own sidebar. Saved and built-in views can both be pinned, and share one pin order

Notes:

- Personal views are visible only to their owner. Sharing a view with the workspace requires `case:update`.
- Built-in views, such as `assigned_to_me`, cannot be changed or deleted. They appear in the sidebar only once pinned.
- A view's `query` takes the same matching fields as `search_cases`, including relative windows (`created_within`) and `"me"`.

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
- `list_table_tree(workspace_id, path="/", depth=1, include_tables=True, limit=20, cursor=None)`
- `create_table_folder(workspace_id, path, parents=False)`
- `rename_table_folder(workspace_id, path, new_name)`
- `move_table_folder(workspace_id, path, destination_parent_path="/")`
- `delete_table_folder(workspace_id, path, recursive=False)`
- `move_tables(workspace_id, table_ids, destination_path="/", dry_run=False)`
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
- `create_variable(workspace_id, name, values, description=None, environment=DEFAULT_SECRETS_ENVIRONMENT, tags=None)`
- `update_variable(workspace_id, variable_name, environment=DEFAULT_SECRETS_ENVIRONMENT, new_name=None, description=None, values=None, tags=None, new_environment=None)`
- `delete_variable(workspace_id, variable_name, environment=DEFAULT_SECRETS_ENVIRONMENT)`
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
- `create_agent_preset(workspace_id, name, slug=None, description=None, instructions=None, model_name=None, model_provider=None, base_url=None, output_type=None, actions=None, namespaces=None, tool_approvals=None, tool_tunnels=None, mcp_integration_ids=None, retries=None, enable_thinking=None, enable_internet_access=None, skills=None, subagents=None)`
- `update_agent_preset(workspace_id, preset_slug, name=None, slug=None, description=None, instructions=None, model_name=None, model_provider=None, base_url=None, output_type=None, clear_output_type=False, actions=None, namespaces=None, tool_approvals=None, tool_tunnels=None, mcp_integration_ids=None, retries=None, enable_thinking=None, enable_internet_access=None, skills=None, subagents=None)`
- `list_agent_presets(workspace_id, limit=20, cursor=None)`
- `get_agent_preset(workspace_id, preset_slug)`
- `list_agent_preset_approver_options(workspace_id, search=None, limit=20, cursor=None)`
- `list_agent_preset_approvers(workspace_id, preset_slug, limit=20, cursor=None)`
- `add_agent_preset_approvers(workspace_id, preset_slug, approvers)`
- `remove_agent_preset_approvers(workspace_id, preset_slug, approvers)`
- `list_agent_tree(workspace_id, path="/", depth=1, include_presets=True, limit=20, cursor=None)`
- `create_agent_folder(workspace_id, path, parents=False)`
- `rename_agent_folder(workspace_id, path, new_name)`
- `move_agent_folder(workspace_id, path, destination_parent_path="/")`
- `delete_agent_folder(workspace_id, path, recursive=False)`
- `move_agent_presets(workspace_id, preset_slugs, destination_path="/", dry_run=False)`
- `run_agent_preset(workspace_id, preset_slug, prompt, preset_version=None, timeout_seconds=120)`

`list_integrations` and `get_agent_preset_authoring_context` keep object
responses but cap embedded collections and expose `truncation.collections`.

`get_agent_preset` also returns `channel_tools`: tools the agent's channel
connections grant (for example Slack), grouped by provider. They are not
selected in `actions`; set their approvals per tool name through
`update_agent_preset`'s `tool_approvals`. While connected, registry actions
under a group's `supersedes` namespaces are left out at run time.
`channel_tools_failed_plugins` names plugins whose groups could not be read.
