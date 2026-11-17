# 001: Distributed alerting with one agent

Socky is one agent preset that triages security alerts. For each alert it investigates, writes a Tracecat case, and posts the case to a Slack channel as a card with a brief and an evidence table. When the records link the activity to an employee, it asks that person "was this you?" with Yes and No buttons and records the answer on the case. It also answers mentions in the case thread.

All values are placeholders.

## Files

| File | What it is |
|---|---|
| [`workflow.md`](workflow.md) | The Triage alerts workflow: a diagram, the 13 steps with their code, and a prompt for the alert intake |
| [`agent/preset.md`](agent/preset.md) | The Socky preset: settings, tools, skills and the full prompt |
| [`skills/`](skills/) | Six skills the preset loads on demand: two alert lifecycles, Slack threads, AWS evidence rules, the triage method, and the output formats |
| [`slack-app-manifest.json`](slack-app-manifest.json) | The Slack app |

## Prerequisites

- A Tracecat workspace with agents, cases and tables.
- A Slack workspace where you can create and install apps.
- An alert source that can POST each alert to a webhook.
- A read-only AWS audit role, if you triage AWS alerts. Store it as the credential the `tools.aws_boto3` actions use.
- Optional: an MCP server for your SIEM, and threat enrichment tools for IP, domain, URL and file-hash reputation.
- Case custom fields the skills write: `finding_ids`, `finding_count`, `finding_type`, `first_seen`, `last_seen`, `principal`, `region`, `resource`, `aws_account`, `disposition`, `due_date`, `linked_person`.

## Setup order

1. Create the three tables listed in `workflow.md`.
2. Create the case custom fields.
3. Replace the placeholders listed below, then add the six skills from `skills/`. Keep each skill's file names.
4. Create the agent preset from `agent/preset.md` with the slug `socky`.
5. Create and install the Slack app (Slack app setup, part 1).
6. Build the Triage alerts workflow from `workflow.md`, add a webhook trigger and publish it.
7. Add the workflow's webhook URL to the Slack app (Slack app setup, part 2).
8. Build the alert intake from the prompt at the end of `workflow.md` and point your alert source at its webhook.
9. Test: run Triage alerts by hand with `alert_ids` set to one stored alert. Check the case, the Slack thread, the details panel and both buttons.

## Slack app setup

### Part 1: before the workflow exists

1. Go to https://api.slack.com/apps, choose "Create New App", then "From a manifest", and paste `slack-app-manifest.json`.
2. Install the app to the workspace.
3. Copy the Bot User OAuth Token. In Tracecat, create a secret named `slack` with the key `SLACK_BOT_TOKEN`.
4. Invite the app to the triage channel: `/invite @socky`.

### Part 2: after the workflow is published

Copy the workflow's webhook URL from its webhook trigger. In the Slack app settings, open "App Manifest" and add this block at the top level:

```json
"settings": {
  "event_subscriptions": {
    "request_url": "https://<your-tracecat-host>/api/webhooks/<workflow-id>/<webhook-secret>?echo=true",
    "bot_events": ["app_mention", "entity_details_requested"]
  },
  "interactivity": {
    "is_enabled": true,
    "request_url": "https://<your-tracecat-host>/api/webhooks/<workflow-id>/<webhook-secret>"
  }
}
```

Save. This adds no scopes, so you do not reinstall the app.

### The Event Subscriptions URL must end in `?echo=true`

This is the most common setup mistake. Slack verifies the URL by sending a `challenge` and expecting it back. With `echo=true` the Tracecat webhook returns the request body, challenge included, in its response. Without it, Slack reports the URL as unverified. The interactivity URL does not need it.

### Scopes

| Scope | Why |
|---|---|
| `app_mentions:read` | Mentions |
| `channels:history` | Reading the thread and the channel |
| `chat:write` | Posting and updating messages, the ephemeral denial, and the optional direct message |
| `links:write` | The `chat.unfurl` fallback in the delivery skills |
| `reactions:write` | The workflow's eyes and tick reactions on a mention |
| `users:read.email`, with `users:read` | Finding the owner by email |

Add `groups:history` for a private channel.

### Work Objects

The card at the top of each case thread is a Slack Work Object. `rich_previews` in the manifest and the `entity_details_requested` event enable its details panel, which the workflow answers in `lookup_work_object` → `present_details` with `entity.presentDetails`.

- https://docs.slack.dev/messaging/work-objects-overview/
- https://docs.slack.dev/messaging/work-objects-implementation/

## Alert sources

Any source that can POST an alert works. The intake stores the alert in `detection_events` (`alert_id`, `payload`) and starts the workflow with that id. The generic `detection-event-case-lifecycle` skill handles it.

For source-specific handling, add a `<source>-case-lifecycle` skill and a routing row in the prompt. `guardduty-case-lifecycle` does this for GuardDuty.

## A `business-context` skill

Optional, and you write it yourself. The prompt and skills load a skill with this name when it exists and work without it. Put in it:

- Deployments: each account and region with a plain name, its workload class and its owner.
- Baselines: scheduled jobs and recurring workloads, with the times they run.
- Known benign patterns, each with every condition that must hold.
- Employee list: name and email for each person.
- Ownership: who owns each account, service and workload, and which team to ask.
- Shared workload roles, if many tenants or jobs share one role.

## Placeholders to replace

| Placeholder | Replace with |
|---|---|
| `#security-alerts`, `C0123456789` | Your triage channel name and its channel ID |
| `https://<your-tracecat-host>/workspaces/<workspace-id>/cases/<case_id>` | Your Tracecat host and workspace ID. Leave `<case_id>`. |
| `<your-read-only-audit-role>` | The read-only role your AWS credential assumes |
| `<siem>`, `<siem-alert-url>` | A short name for your SIEM, and the URL pattern of an alert in it |
| `<adapt to your SIEM>` | Marks field names, table names and SQL that follow one SIEM's schema. Rewrite them for yours. |
| `<your-shared-workload-role>`, `<tenant prefix length>` | Only if many tenants or jobs share one role. Otherwise delete those checks. |

`U0123456789`, `jane.doe@example.com`, `Jane Doe`, `CASE-0001` and `123456789012` are examples. Leave them.

The workflow reads `gd_confirm_yes`, `gd_confirm_no`, `gd_confirm` and the table names. Do not rename them without changing the workflow.

## Known gaps

- Without an employee list in `business-context`, Socky has no email for the person behind a session and posts no owner ask.
- The skills were written for GuardDuty and one SIEM's alert format first. Other sources need the field mapping in `detection-event-case-lifecycle` stage 1 adapted.
- `slack-case-threads` says the app subscribes only to `app_mention`. The manifest also subscribes to `entity_details_requested`, which the workflow answers without the agent.
- `hypothesis-library.md` says the workflow posts open questions. Socky posts everything itself.
- The workflow code was edited to remove polling steps and to add the three reaction steps, and has not been run since.
