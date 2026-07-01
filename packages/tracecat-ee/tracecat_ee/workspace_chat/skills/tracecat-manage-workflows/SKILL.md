---
name: tracecat-manage-workflows
description: REQUIRED whenever the user wants to build, create, scaffold, set up, read, inspect, view, change, modify, update, edit, add a step to, fix, run, test, execute, or publish a Tracecat workflow or automation. Read this SKILL.md FIRST, before calling core.workflow.create_workflow, core.workflow.get_workflow, core.workflow.edit_workflow, core.workflow.run, or core.workflow.publish. It covers creating a workflow (empty or from full YAML), reading its editable draft, editing the draft with RFC 6902 JSON Patch including the mandatory read->patch->write sequence and revision handling, and running (draft or published) and publishing the workflow.
---

# Managing Tracecat workflows

You manage workflows through these tools in the `core.workflow` namespace:

- `core.workflow.create_workflow` — create a new workflow (empty, or with a full definition).
- `core.workflow.get_workflow` — read a workflow's editable **draft** (its current working copy).
- `core.workflow.edit_workflow` — apply RFC 6902 JSON Patch operations to the draft.
- `core.workflow.get_authoring_context` — look up real action arg schemas, secrets, and examples
  before writing an action's `args:` (see "Getting action arguments right" below).
- `core.workflow.get_webhook` / `core.workflow.update_webhook` — read or enable/disable the
  workflow's **webhook** trigger (see "Triggers" below).
- `core.workflow.get_case_trigger` / `core.workflow.update_case_trigger` — read or configure the
  workflow's **case** trigger (see "Triggers" below).
- `core.workflow.run` — run the workflow to test it (the current draft by default; a published
  version with `use_draft=False`). See "Running a workflow" below.
- `core.workflow.publish` — commit the current draft as a new version (see "Running a workflow").

Do **NOT** call `core.workflow.execute` from chat. It is a workflow **action** that runs a child
workflow from inside another workflow's definition (a subflow step) — it is not a chat tool. To run
a workflow from chat, use `core.workflow.run`.

A workflow's **draft** is its current editable state — the same thing shown in the workflow
builder. Reading and editing always operate on the draft. There is no separate save step for the
draft itself; every successful `edit_workflow` updates the live draft.

## Golden rules

1. **Never guess the current shape.** Before editing, always call `get_workflow` to get the latest
   `draft_document` and `draft_revision`. Compute your patch against that exact `draft_document`.
2. **Always pass `base_revision`.** Use the `draft_revision` from the most recent `get_workflow` as
   `edit_workflow`'s `base_revision`. If the draft changed since you read it, the edit is rejected
   with a conflict — re-read and retry. Do not reuse a stale revision.
3. **One edit at a time, sequentially.** Each successful *real* `edit_workflow` returns a NEW
   `draft_revision`. Use it for the next edit. Never run edits in parallel against the same draft. A
   `validate_only` (dry-run) call does NOT persist or advance the revision — after a dry run, reuse
   the SAME `base_revision` for the real write.
4. **Prefer small, targeted patches** over replacing the whole definition. Patch the specific
   actions/fields you are changing.

## Creating a workflow

To scaffold an empty workflow (only a trigger), call `create_workflow` with a `title` (and optional
`description`). The user can then ask you to add actions.

To create a workflow that already contains actions, pass the **full definition as YAML** in
`definition_yaml`. Put the complete workflow under a top-level `definition:` key, and **always
include an `entrypoint`** naming the first action's `ref` (the action with no `depends_on`).
Omitting `entrypoint` still works (it is inferred from the graph), but set it explicitly so the
workflow starts where you intend. Example:

```yaml
definition:
  title: Enrich and notify
  description: Look up an indicator and post a summary to Slack
  entrypoint:
    ref: fetch_indicator
    expects:
      indicator:
        type: str
  actions:
    - ref: fetch_indicator
      action: core.http_request
      args:
        url: https://example.com/api/lookup
        method: GET
        params:
          q: ${{ TRIGGER.indicator }}
```

The `entrypoint.expects` block declares the workflow's **trigger input schema** — the fields a
trigger payload (or `run` `inputs`) must provide, validated before dispatch. See
[expects](references/expects.md) for the field shape, the type grammar, and how defaults make
fields optional.

`create_workflow` returns the new workflow's `id` and `title`. After creating from YAML, call
`get_workflow` to confirm the actions landed before making further edits.

## Reading a workflow

`core.workflow.get_workflow(workflow_id)` returns:

- `workflow_id` — the workflow's id.
- `draft_revision` — the optimistic-concurrency token. Pass it as `base_revision` to `edit_workflow`.
- `draft_document` — the editable draft, with these top-level sections:
  - `metadata` — `title`, `description`, `status`, `alias`, `error_handler`.
  - `definition` — `entrypoint`, `actions`, `config`, `returns`.
  - `layout` — node positions in the builder (`trigger`, `viewport`, `actions`).
  - `schedules` — workflow schedules.
  - `case_trigger` — case-trigger configuration, if any.

Always read before you edit, and read back after a non-trivial edit to confirm the result.

## Editing a workflow

Editing uses RFC 6902 JSON Patch. The full mechanics — patch paths, array semantics, adding and
renaming actions, the `validate_only` preflight, and conflict handling — are in
[editing](references/editing.md). Read it before your first edit.

Quick shape:

```
edit_workflow(
  workflow_id="wf_...",
  base_revision="<draft_revision from get_workflow>",
  patch_ops=[
    {"op": "add", "path": "/definition/actions/-", "value": { ...new action... }},
  ],
  validate_only=false,
)
```

Editable top-level paths are `/metadata`, `/definition`, `/layout`, `/schedules`, `/case_trigger`.
Action paths live under `/definition/actions/...`.

## Triggers — how a workflow runs

A workflow's actions define *what* it does; a **trigger** defines *when* it runs. Adding actions
does not make a workflow runnable on its own — set up a trigger when the user wants the workflow to
fire on an event.

The **webhook** and **case trigger** each have dedicated tools (`get_webhook`/`update_webhook`,
`get_case_trigger`/`update_case_trigger`) — **prefer those tools** to configure those two triggers.
**Schedules** have no dedicated tool: configure them by patching the `/schedules` top-level path via
`edit_workflow`.

- **Webhook** — run the workflow when an HTTP request hits its webhook URL.
  - `get_webhook(workflow_id)` returns the `status` (`"online"`/`"offline"`), the public `url`, and
    the allowed `methods`.
  - `update_webhook(workflow_id, status="online")` enables it; `status="offline"` disables it.
    Enabling makes the workflow triggerable at its `url` — surface that URL to the user.
- **Case trigger** — run the workflow when a case event occurs (e.g. a case is created).
  - `get_case_trigger(workflow_id)` returns `status`, `event_types`, and `tag_filters`.
  - `update_case_trigger(workflow_id, status=..., event_types=[...], tag_filters=[...])` configures
    it. To enable (`status="online"`) you MUST also pass a non-empty `event_types`, e.g.
    `["case_created", "status_changed"]`.

Two rules that are easy to get wrong:

1. **Prefer `update_case_trigger` to configure the case trigger.** It is the simplest way to set
   the trigger's `status`, `event_types`, and `tag_filters`. `/case_trigger` is an editable path, so
   a JSON patch that adds or replaces it via `edit_workflow` also works and is persisted, but
   `update_case_trigger` is the preferred path.
2. **`tag_filters` must reference tags that already exist.** Passing an unknown tag ref fails with
   "Case tag(s) not found". If the user wants to filter on a tag that does not exist yet, create the
   tag first (or drop the filter) — do not guess tag refs.

See [triggers](references/triggers.md) for the full event-type list and worked examples.

## Running a workflow

Triggers fire a workflow on an external event; `core.workflow.run` runs it **on demand** — use it
to test a workflow you just built or edited.

`core.workflow.run` runs the **current draft by default** (`use_draft=True`) — i.e. your
unpublished edits, exactly what `get_workflow`/`edit_workflow` operate on. This is what you almost
always want while iterating: edit the draft, then `run` it to see the result, no publish step
needed.

```
run(workflow_id="wf_...", inputs={...})            # runs the draft (default)
run(workflow_id="wf_...", use_draft=False)         # runs the current published version
run(workflow_id="wf_...", use_draft=False, version=3)  # runs a specific published version
```

- `inputs` is the trigger payload (arbitrary JSON). It is validated against the workflow's
  `entrypoint.expects` schema (see [expects](references/expects.md)) before dispatch — a mismatch
  returns a fixable error naming the bad field, so shape `inputs` to that schema.
- A **broken draft** (empty graph, invalid actions) returns a validation error instead of running.
  Fix the draft (see "Editing a workflow") and retry; you do not need to publish to run a draft.
- `run` returns `{workflow_id, workflow_execution_id, status: "STARTED"}` and returns immediately —
  it does not wait for the run to finish. Report the execution id to the user.

**Draft vs published.** `use_draft=True` (default) runs your working copy — best for testing.
`use_draft=False` runs a **published** definition (the committed, versioned snapshot that triggers
actually fire); pass `version` to pin a specific one, or omit it for the current published version.
`version` is ignored when `use_draft=True` (a draft has no version number).

**Publishing.** `core.workflow.publish(workflow_id)` commits the current draft as a new immutable
version. Publish when the user wants the workflow's **live triggers** (webhook, case, schedule) to
use the latest edits — triggers run the published definition, not the draft. Publishing validates
the draft first and returns the new `version`. Typical loop: `edit_workflow` → `run` (test the
draft) → `publish` (when it's ready to go live).

## Action namespaces — NOT everything is `core.`

Every `action:` name starts with exactly one of these three top-level namespaces. There are
no others. Do not prefix `core.` onto an action that is not a core action.

- `core.*` — built-ins: `core.http_request`, `core.transform.reshape`, `core.script.run_python`,
  `core.cases.create_case`, `core.table.*`, etc.
- `ai.*` — AI actions: **`ai.agent`** (tool-calling agent), **`ai.action`** (single LLM call),
  **`ai.preset_agent`** (saved preset, enterprise). These are `ai.agent`, NOT `core.ai.agent`.
- `tools.*` — third-party integrations: `tools.slack.post_message`, `tools.jira.create_issue`,
  etc. Add a `tools.*` action only when the user named that integration.

For an alert-triage or any investigation/enrichment workflow, the severity/judgment step is an
**`ai.agent`** action — do not replace it with an HTTP or Python placeholder.

## Connecting actions and error branches

Actions are wired together with each action's **`depends_on`** list — a list of source refs. The
graph edges (and the trigger entrypoint) come entirely from `depends_on`; there is no separate
edges section.

Each dependency is a string `"<source_ref>"` or `"<source_ref>.<edge>"`, where `<edge>` is:

- **`success`** (the default) — run after the source action **succeeds**. `"a"` and `"a.success"`
  are equivalent.
- **`error`** — run after the source action **fails**. This is the on-failure / error-handler
  branch.

So "add a step that runs **on error of** `reshape_1`" means: add the new action with
`depends_on: ["reshape_1.error"]`. There is **no `on_error` field** on an action (nor `on_success`,
`catch`, `error_handler`, etc.) — putting one in `args` or on the action will be rejected. Express
both the normal path and the error path purely through `depends_on`:

```yaml
- ref: reshape_1
  action: core.transform.reshape
  args:
    value: ${{ TRIGGER.data }}
- ref: handle_failure        # runs only if reshape_1 fails
  action: core.transform.reshape
  args:
    value: "reshape_1 failed"
  depends_on:
    - reshape_1.error
```

(Per-action **retries** are a different concept — that's the `retry_policy` field, not an edge.
The workflow-wide failure handler is `/metadata/error_handler`, which names another workflow.)

## When the registry rejects an action name

A "does not exist" / unknown-action error from `create_workflow` or `edit_workflow` means you got
the **name** wrong — almost always a wrong namespace prefix (e.g. `core.ai.agent` instead of
`ai.agent`). It does **not** mean the feature is missing, disabled, gated, or needs workspace setup,
and it is **not** a reason to ask the user to check their sidebar.

1. Re-read the namespace list above and pick the correct prefix. The fix is usually that the action
   is not a `core.` action — don't force a `core.` prefix onto an `ai.*` or `tools.*` action.
2. If you are unsure of the exact name, call `core.workflow.get_authoring_context` with a `query`
   (e.g. `"send slack message"`) to look up the real action name and schema before retrying.
3. Correct the `action:` name and retry the same `create_workflow` / `edit_workflow` call. The error
   names the offending action — fix exactly that and resubmit.

Never drop a capability, downgrade to an HTTP/Python placeholder, or ask the user to debug their
workspace because of a name error. Correct the name and keep the intended action.

## Expressions — `${{ ... }}`

Action `args` reference runtime data with template expressions like `${{ TRIGGER.indicator }}` or
`${{ ACTIONS.fetch.result.data }}`. Three rules catch people out:

1. **Namespaces are UPPERCASE.** `${{ ACTIONS.x }}`, `${{ TRIGGER.x }}`, `${{ SECRETS.x }}`,
   `${{ VARS.x }}`, `${{ ENV.x }}` — `${{ actions.x }}` (lowercase) is a parse error.

2. **Don't trust the draft read-back to "resolve" an expression.** When you write an expression and
   then `get_workflow`, the stored value may display as `None`. The platform evaluates the
   expression against an empty context at author time (there is no live trigger yet), so it renders
   as `None` even though the expression is stored correctly and resolves to the real value at
   runtime. A `None` in the read-back does NOT mean the wiring is wrong — confirm correctness by
   re-reading the action's `args` string, not by the resolved value.

3. **Typed fields need a `-> str` cast.** A field validated as a string (e.g. a `case_id`) rejects a
   bare `${{ TRIGGER.id }}`, because at author time the expression resolves to `None` and
   `None` is not a string. Cast it: `${{ TRIGGER.id -> str }}`. At author time `str(None) == "None"`
   passes the string check; at runtime `str(real_value)` yields the real value. Use `-> str` for any
   expression going into a required string field.

See [expressions](references/expressions.md) for more.

## Getting action arguments right

Workflow actions (e.g. `core.http_request`, `core.transform.reshape`, `ai.agent`, `tools.*`
integrations) each have a specific `args` schema. **Do not invent argument names.** When you are
unsure of an action's exact arguments, call `core.workflow.get_authoring_context` first:

- Pass `action_names` (e.g. `["core.http_request", "ai.agent"]`) for actions you already know, or a
  `query` (e.g. `"slack post message"`) to find the right action when you don't know its name.
- It returns each action's `parameters_json_schema` (the real arg names/types), `required_secrets`
  (and whether they are `configured`), an example `args` payload, plus the workspace's available
  `variable_hints` and `secret_hints` — so you can reference real `${{ SECRETS.* }}` / `${{ VARS.* }}`.

Build the `args` from that schema, not from memory. Name action `ref`s by their effect
(`fetch_indicator`, `post_to_slack`), not by implementation detail. If an action's required secrets
are not configured, add the action anyway and tell the user which credential to set up (and where —
see the `tracecat-platform-guide` skill) rather than dropping the step.

## Workflow, not chat

`create_workflow` / `get_workflow` / `edit_workflow` are how YOU manage workflows from chat. Names
like `core.http_request` or `core.transform.reshape` are **workflow action** names that go INSIDE a
workflow definition's `actions:` — they are not tools you call directly here.
