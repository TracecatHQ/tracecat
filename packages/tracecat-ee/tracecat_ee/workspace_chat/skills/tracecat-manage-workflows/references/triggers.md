# Configuring workflow triggers

A trigger decides *when* a workflow runs. Configure one with its dedicated tools — never through
`edit_workflow` JSON patches. There are two configurable triggers: **webhook** and **case trigger**.

## Webhook trigger

Run the workflow when an HTTP request hits its webhook URL.

### Read

```
get_webhook(workflow_id="wf_...")
```

Returns the webhook's `status` (`"online"` or `"offline"`), the public `url` to send requests to,
the allowed `methods` (e.g. `["POST"]`), and `entrypoint_ref`.

### Enable / disable

```
update_webhook(workflow_id="wf_...", status="online")    # enable
update_webhook(workflow_id="wf_...", status="offline")   # disable
```

`status` is the only thing you set from chat. When you enable a webhook, read it back with
`get_webhook` and give the user the `url` — that is the address they POST events to. Configuring the
allowed methods, CIDR allowlist, or entrypoint is done in the workflow builder UI.

## Case trigger

Run the workflow when a case event occurs in the workspace.

### Read

```
get_case_trigger(workflow_id="wf_...")
```

Returns `status` (`"online"`/`"offline"`), `event_types` (which case events fire the workflow), and
`tag_filters` (case-tag refs that restrict which cases fire it).

### Configure

```
update_case_trigger(
    workflow_id="wf_...",
    status="online",
    event_types=["case_created", "status_changed"],
    tag_filters=["phishing"],          # optional
)
```

Omitted arguments are left unchanged. To enable the trigger (`status="online"`) you MUST provide a
non-empty `event_types` (here or already configured) — enabling with no events is rejected.

### Valid `event_types`

Use these exact strings (underscores, lowercase):

- `case_created`
- `case_updated`
- `case_closed`
- `case_reopened`
- `case_viewed`
- `priority_changed`
- `severity_changed`
- `status_changed`
- `fields_changed`
- `assignee_changed`
- `attachment_created`
- `attachment_deleted`
- `tag_added`
- `tag_removed`
- `payload_changed`
- `task_created`
- `task_deleted`
- `task_status_changed`

## Two things that trip people up

1. **The case trigger is NOT editable through `edit_workflow`.** `get_workflow` shows a
   `case_trigger` section in the draft, but a JSON patch that adds/replaces `/case_trigger` is
   rejected. That section is read-only context — `update_case_trigger` is the only way to set it.

2. **`tag_filters` must reference existing case tags.** An unknown tag ref fails with
   "Case tag(s) not found". If the user wants to filter on a tag that does not exist yet, create
   that case tag first, then set the filter. Do not invent tag refs.
