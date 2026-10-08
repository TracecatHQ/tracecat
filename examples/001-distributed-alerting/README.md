# 001: Distributed alerting with one agent

Socky is a single agent preset that triages security alerts. For each alert it investigates, writes a Tracecat case, publishes the case to a Slack channel, asks the person linked to the activity "was this you?" with Yes and No buttons, and records the answer on the case.

This folder holds everything needed to rebuild it: the workflow, the agent prompt, six skills and the Slack app manifest. All values are placeholders. Nothing here is specific to one organisation.

## The pieces

| File | What it is |
|---|---|
| [`workflow.md`](workflow.md) | The Triage alerts workflow: a diagram, then one section per step with the exact code, and the contract for the alert intake |
| [`agent/preset.md`](agent/preset.md) | The Socky preset: settings, tools, skills and the full prompt |
| [`skills/`](skills/) | Six skills the preset loads on demand |
| [`slack-app-manifest.json`](slack-app-manifest.json) | The Slack app, ready to paste into "From a manifest" |

The skills:

| Skill | What it covers |
|---|---|
| `detection-event-case-lifecycle` | A SIEM alert from intake to a published case |
| `guardduty-case-lifecycle` | An AWS GuardDuty finding from fetch to a published case |
| `slack-case-threads` | Mentions in a case thread and the owner's Yes/No buttons |
| `aws-cloud-incident-response-core` | AWS evidence rules, identity resolution, wording and the investigation record |
| `hypothesis-driven-triage` | The triage method and the question library per finding family |
| `case-output` | The case report layout, the Slack card, the brief, the evidence table and thread replies |

## How it fits together

1. Your alert source sends each alert to a webhook. A small intake workflow stores it in a table and starts Triage alerts with that one alert id. One alert, one run, one agent call.
2. Triage alerts runs Socky on the alert. Socky deduplicates against open cases, investigates with read-only tools, writes the case, and posts one Slack thread: a Work Object card, a short brief, an evidence table and, when the records link the activity to an employee, the Yes/No ask.
3. Slack sends mentions and button clicks to the same workflow. A click by the owner is acknowledged at once, then Socky records the answer: Yes sets the case to Benign, No sets it to Escalate. A click by anyone else gets a private refusal.
4. When someone opens the card's details panel, the workflow answers from a table. No agent runs.

The workflow does the fast, deterministic parts: routing, deduplicating Slack events, checking who clicked, serving the details panel. The agent does everything that needs judgment.

## Prerequisites

- A Tracecat workspace with agents, cases and tables.
- A coding agent connected to the Tracecat MCP, if you want to build the workflow from the prompts in `workflow.md`.
- A Slack workspace where you can create and install apps.
- An alert source that can POST each alert to a webhook: a SIEM rule, a cloud threat detection service such as GuardDuty through its event or notification route, or your own tooling.
- AWS access through a read-only audit role, if you triage AWS alerts. Store it as the AWS credential the `tools.aws_boto3` actions use.
- Optional: an MCP server for your SIEM, so Socky can read the events behind an alert.
- Optional: threat enrichment tools for IP, domain, URL and file-hash reputation.

Case custom fields the skills write: `finding_ids`, `finding_count`, `finding_type`, `first_seen`, `last_seen`, `principal`, `region`, `resource`, `aws_account`, `disposition`, `due_date`, `linked_person`. Create them in the workspace's case settings.

## Setup order

1. Create the three tables listed in `workflow.md`: `detection_events`, `slack_events`, `slack_work_objects`. Each needs its unique index.
2. Create the case custom fields above.
3. Add the six skills from `skills/`. Keep each skill's file names. Replace the placeholders listed below first.
4. Create the agent preset from `agent/preset.md` with the slug `socky`. Grant the tools, bind the skills, attach your SIEM MCP if you have one.
5. Build the Triage alerts workflow from `workflow.md`. Add a webhook trigger and publish it.
6. Create the Slack app (next section) and store its token.
7. Build the alert intake workflow from the contract at the end of `workflow.md`, and point your alert source at its webhook.
8. Test: run Triage alerts by hand with `alert_ids` set to one stored alert. Check the case, the Slack thread, the details panel, and both buttons.

## Slack app setup

Build and publish the workflow first. The Slack app needs the workflow's webhook URL, and Slack checks that URL when you save the app.

1. In Tracecat, open the Triage alerts workflow, open the webhook trigger and copy the webhook URL. It looks like `https://<your-tracecat-host>/api/webhooks/<workflow-id>/<webhook-secret>`.
2. Go to https://api.slack.com/apps, choose "Create New App", then "From a manifest". Pick your workspace and paste `slack-app-manifest.json`.
3. Replace both request URLs in the manifest with your webhook URL:
   - `settings.event_subscriptions.request_url`: the webhook URL followed by `?echo=true`
   - `settings.interactivity.request_url`: the webhook URL as it is
4. Create the app and install it to the workspace. The `incoming-webhook` scope makes Slack ask for a channel during install. Pick the triage channel.
5. Copy the Bot User OAuth Token from "OAuth & Permissions". In Tracecat, create a secret named `slack` with the key `SLACK_BOT_TOKEN` and that token as the value. The `tools.slack` and `tools.slack_sdk` actions read this secret.
6. Invite the app to the triage channel: `/invite @socky`.

### The Event Subscriptions URL must end in `?echo=true`

This is the most common setup mistake.

Slack verifies an Event Subscriptions URL before it accepts it. It sends a POST with a `challenge` value and expects that value back in the response. A Tracecat webhook does not return the request body by default. Adding `?echo=true` makes the webhook return the request body, which contains the challenge, so the check passes. The code is in `tracecat/webhooks/router.py`.

Without `?echo=true`, Slack shows the URL as unverified and sends no events. Mentions and the details panel then do nothing, while the buttons still work.

The interactivity URL does not need it. Slack does not verify that URL.

### Work Objects

The Slack card at the top of each case thread is a Work Object: a structured card with fields, buttons and a details panel, not a plain message.

Two parts of the manifest turn this on:

- `features.rich_previews.entity_types` lists the entity types the app may post. The card uses `slack#/entities/incident`.
- The `entity_details_requested` bot event. Slack sends it when someone opens the details panel of a card.

The workflow answers that event with `entity.presentDetails`, in the steps `lookup_work_object` and `present_details`. The entity it returns is the one Socky stored in `slack_work_objects` when it posted the card.

Slack's documentation:

- https://docs.slack.dev/messaging/work-objects-overview/
- https://docs.slack.dev/messaging/work-objects-implementation/

## Placeholders to replace

| Placeholder | Where | Replace with |
|---|---|---|
| `#security-alerts` | Prompt, both `slack-delivery.md` files, both lifecycle skills | Your triage channel name. It is a placeholder, not a required name. |
| `C0123456789` | Both `slack-delivery.md` files, both lifecycle skills | The channel ID of your triage channel |
| `https://<your-tracecat-host>/workspaces/<workspace-id>/cases/<case_id>` | Both lifecycle skills | Your Tracecat host and workspace ID. Leave `<case_id>` as it is. |
| `<your-read-only-audit-role>` | Both lifecycle skills, `aws-cloud-incident-response-core` | The read-only role your AWS credential assumes |
| `<siem>`, `<siem-alert-url>` | `detection-event-case-lifecycle` | A short name for your SIEM, used in tags, and the URL pattern of an alert in it |
| `<adapt to your SIEM>` | Skills | Marks field names, table names and SQL that follow one SIEM's schema. Rewrite them for yours. |
| `<your-shared-workload-role>`, `<tenant prefix length>` | Lifecycle skills, `hypothesis-library.md` | Only if many tenants or jobs share one role in your AWS accounts. Otherwise delete those checks. |
| `U0123456789`, `jane.doe@example.com`, `Jane Doe`, `CASE-0001`, `123456789012` | Skills | Examples only. Leave them. |

The identifiers `gd_confirm_yes`, `gd_confirm_no`, `gd_confirm`, `gd_open_case` and the table names are read by workflow expressions. Do not rename them unless you change the workflow as well.

## Nice-to-haves

### A `business-context` skill

Write this one yourself. It is the difference between "an unknown role did something" and "the nightly backup job ran on schedule". The shipped skills load a skill named `business-context` when it exists and work without it.

Without it, Socky reports every deployment as `Unknown`, matches no benign pattern, and has no email for the person behind a session. That last point matters: the owner ask is posted only when the employee list gives an email for the session's username.

What to put in it, as named sections the other skills refer to:

| Section | Content |
|---|---|
| Baselines | Platform-owned schedules, recurring workloads with the times they run, and what normal looks like for the roles and people who show up in alerts |
| Deployments | Each AWS account and region with a plain name, its workload class and its owner |
| Network | How traffic leaves your clusters, for example which address a pod has before NAT, and which nodes host cluster DNS |
| Known benign patterns | Named patterns, each with every condition that must hold. A pattern matches only when all its conditions are met. |
| Shared workload roles | Any role that many tenants or jobs share, and how its session names identify the tenant |
| Employee list | Name and email for each person. Usernames are matched on the email's local part, or first initial plus surname. |
| Ownership | Who reports to whom, and who owns what: which team or person owns each account, service and workload |
| Who can answer | Which team or role to ask about each workload class |

Where it is loaded:

- The prompt: in the shared skills list, in step 3 of the generic path, and under "Every investigation".
- `guardduty-case-lifecycle` and `detection-event-case-lifecycle`: stage 5, before investigating.
- `aws-cloud-incident-response-core`: for the deployment name, the employee list and any claim that something is documented or expected.
- `hypothesis-driven-triage` and `hypothesis-library.md`: for routing, baselines, benign patterns and schedules.
- `case-output`: the brief's Invariant line and any "documented" claim cite a business context section.

Keep it current. A stale employee list sends the Yes/No ask to nobody.

### Other additions

- Add a lifecycle or evidence skill per additional alert source (identity, SaaS, endpoint) as needed. Name a lifecycle skill `<source>-case-lifecycle` and the prompt routes to it by name. Sources without one use the generic path in the prompt.

## Known gaps in the source

The skills are sanitised copies from a working setup, and a few parts disagree with the workflow. They are kept as they were.

- `slack-case-threads` says the app subscribes only to `app_mention`. The manifest also subscribes to `entity_details_requested`, which the workflow answers before the agent is involved.
- `slack-case-threads` quotes the acknowledgement line with a user mention. The workflow writes it without one: `Answered Yes. Socky is recording it.`
- `hypothesis-library.md` says the workflow posts open questions. Socky posts everything itself.
- The skills were written for GuardDuty first. `detection-event-case-lifecycle` stage 7 lists the substitutions for other alerts.
