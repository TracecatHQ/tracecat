# Editing a workflow draft with JSON Patch

`core.workflow.edit_workflow` applies RFC 6902 JSON Patch operations to the workflow draft document
returned by `core.workflow.get_workflow`. Prefer targeted patches over replacing whole sections.

## The read → patch → write loop

1. Call `get_workflow(workflow_id)`. Keep its `draft_revision` and `draft_document`.
2. Build `patch_ops` against that `draft_document`. Action indexes are positions in
   `draft_document.definition.actions`, starting at 0.
3. Call `edit_workflow(workflow_id, base_revision=<draft_revision>, patch_ops=[...])`.
4. The response includes a NEW `draft_revision`. Use it for the next edit. For anything
   non-trivial, call `get_workflow` again to confirm the result before continuing.

Treat `draft_revision` as sequential state. After every successful write, use the returned
revision. Never reuse a stale index list after another edit — re-read first.

## Editable paths

Top-level editable paths: `/metadata`, `/definition`, `/layout`, `/schedules`, `/case_trigger`.

Common action paths (N is the 0-based index in `draft_document.definition.actions`):

- `/definition/actions/-` — append a new action (RFC 6902 `-` means "end of array").
- `/definition/actions/N` — replace an entire action.
- `/definition/actions/N/ref` — rename an action.
- `/definition/actions/N/args` — replace an action's args.
- `/definition/actions/N/args/<field>` — change one argument.
- `/definition/actions/N/depends_on` — change an action's dependency edges.
- `/layout/actions/N/ref` — the matching layout entry's ref.

Not editable: `/definition/config/scheduler` and an action's `/definition/actions/N/id`. Schedule
`status` and `case_trigger.status` cannot be removed (set them instead of deleting).

## Operation semantics

- Supported ops: `add`, `remove`, `replace`, `move`, `copy`, `test`.
- Operations apply in order. RFC 6902 array semantics: `/-` appends; indexes shift after `add`,
  `remove`, and `move`. If you remove index 1, the old index 2 becomes index 1 for later ops in the
  same patch — account for the shift, or order removals from highest index to lowest.
- `add`/`replace`/`test` require a `value`. `move`/`copy` require a `from`.

## Adding or renaming actions — keep edges and layout in sync

When you add, remove, or rename an action, do it all in ONE patch:

- Set the action `ref`.
- Update every dependent action's `depends_on` edges that reference the old/new ref.
- Add or update the matching `/layout/actions` entry so the node appears in the builder. New nodes
  need a layout entry with `ref`, `x`, `y` (pick non-overlapping coordinates).

A new action value looks like:

```json
{
  "ref": "post_to_slack",
  "action": "core.http_request",
  "args": { "url": "https://hooks.slack.example/...", "method": "POST", "payload": { "text": "done" } },
  "depends_on": ["fetch_indicator"]
}
```

## Validate before you commit

For non-trivial edits, first call `edit_workflow(..., validate_only=true)` with your patch. If it
reports valid, repeat the SAME patch against the SAME `base_revision` with `validate_only=false`.
`validate_only` checks the patch (including DSL validation when the definition changed) without
persisting.

## Handling conflicts

If `edit_workflow` returns a conflict (the draft changed since you read it — for example the user
edited it in the builder), your `base_revision` is stale. Call `get_workflow` again, rebuild your
patch against the fresh `draft_document`, and retry with the new `draft_revision`. Do not force the
old revision.

## Trigger inputs

Declare `definition.entrypoint.expects` for every trigger input the workflow reads as
`${{ TRIGGER.<name> }}`. A patch can pass validation even if a later run omits an undeclared trigger
value, so keep `expects` complete.
