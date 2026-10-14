# 002: Google OAuth app lifecycle

Socky is one agent preset that reviews third-party Google OAuth apps as they are connected. A workflow polls Workspace token activity, starts one Socky run per app, writes a Tracecat case, and posts a Slack card that asks the person's manager whether they need it. Security can revoke one person's token from the thread, with a recorded reason. Socky also answers questions in the card's thread.

All values are placeholders. The skills in this folder were exported from a working workspace and sanitised: real people, Slack IDs, channel IDs, client IDs and hostnames were replaced.

This example can sit next to [001: Distributed alerting](../001-distributed-alerting/). The same Slack app and the same `slack_work_objects` table serve both. The alerting skills live in 001. The OAuth skills live here.

## Files

| File | What it is |
|---|---|
| [`workflow.md`](workflow.md) | The Google OAuth app lifecycle workflow: a diagram, the 12 steps with their code, and the inputs |
| [`agent/preset.md`](agent/preset.md) | The Socky preset: settings, tools, skills and the full prompt |
| [`skills/`](skills/) | Five skills the preset loads for this path: the lifecycle, the grant review, vendor research, the writing style, and an org-chart template |
| [`slack-app-manifest.json`](slack-app-manifest.json) | The Slack app. Same as example 001. |

## Prerequisites

- A Tracecat workspace with agents, cases and tables.
- A Slack workspace where you can create and install apps, or the Socky app from example 001.
- Google Workspace audit logs in a log store you can query for `token` `request` and `authorize` events.
- A Google service account with domain-wide delegation, and a super-admin address it impersonates. The only write it makes is `tokens.delete`.
- An Exa API key. It is Socky's only web tool, used to research who publishes an app.
- A model provider configured in Tracecat.

## Setup order

1. Create the three tables listed in `workflow.md`. Seed `oauth_scope_risk` with your own ratings. Do not guess a scope you have not rated.
2. Replace the placeholders listed below, then add the five skills from `skills/`. Keep each skill's file names. Fill in `org-chart` with your own people before you publish it.
3. Create the agent preset from `agent/preset.md` with the slug `socky`, or attach these skills to the Socky from example 001. List its tools and remove anything that can revoke, block or change access in Google. Socky must never hold a revoke tool.
4. Create and install the Slack app (Slack app setup, part 1), or reuse the app from example 001.
5. Build the workflow from `workflow.md`. Leave the schedule off, `revoke_approvers` empty and `dry_run` true. Set `admin_subject` to the super-admin you will impersonate.
6. Point Slack at the workflow, or at a router that dispatches OAuth clicks here by `action_id` (Slack app setup, part 2).
7. Test everything, change nothing: a new app, a second holder, a `request`-only app (no token, so no Revoke button), approve, decline, a refused click, and a thread question. Then add your own Slack user ID to `revoke_approvers` and test a revoke on your own account.
8. Turn the schedule on only when you say so.

## Slack app setup

Same app as example 001. If that app is already installed, skip part 1.

### Part 1: before the workflow exists

1. Go to https://api.slack.com/apps, choose "Create New App", then "From a manifest", and paste `slack-app-manifest.json`.
2. Install the app to the workspace.
3. Copy the Bot User OAuth Token. In Tracecat, create a secret named `slack` with the key `SLACK_BOT_TOKEN`.
4. Invite the app to the review channel: `/invite @socky`.

### Part 2: after the workflow is published

Slack posts every click and mention to one request URL. Two setups work:

**This workflow is the only Socky workflow.** Copy its webhook URL. In the Slack app settings, open "App Manifest" and add this block at the top level:

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

**Example 001 is already receiving Slack.** Keep 001 as the request URL. Add a router step there that sends OAuth clicks here by `action_id` (`oauth_mgr_approve`, `oauth_mgr_deny`, `oauth_revoke`, `oauth_revoke_not_needed`, `oauth_revoke_excess_scope`, `oauth_revoke_untrusted`, `oauth_revoke_compromise`), and OAuth thread mentions here by looking up the mention's `thread_ts` in `slack_work_objects`. A row whose `external_ref_id` starts `oauth:` belongs here.

Save. This adds no scopes, so you do not reinstall the app.

### The Event Subscriptions URL must end in `?echo=true`

This is the most common setup mistake. Slack verifies the URL by sending a `challenge` and expecting it back. With `echo=true` the Tracecat webhook returns the request body, challenge included, in its response. Without it, Slack reports the URL as unverified. The interactivity URL does not need it.

### Scopes

| Scope | Why |
|---|---|
| `app_mentions:read` | Mentions in a card's thread |
| `channels:history` | Reading the thread and the channel |
| `chat:write` | Posting and updating the card, the ephemeral denial, and the revoke reasons |
| `links:write` | The `chat.unfurl` fallback in `slack-delivery.md` |
| `users:read.email`, with `users:read` | Finding the manager by email |

Add `groups:history` for a private channel.

### Work Objects

The card at the top of each review thread is a Slack Work Object. `rich_previews` in the manifest and the `entity_details_requested` event enable its details panel. Socky writes the entity into `slack_work_objects`. The Work Object ref is `oauth:<short name>`, never the raw client ID: Slack rejects a dot in `external_ref.id`, and every client ID has them.

- https://docs.slack.dev/messaging/work-objects-overview/
- https://docs.slack.dev/messaging/work-objects-implementation/

## Skills

| Skill | What it owns |
|---|---|
| `google-oauth-app-lifecycle` | The path. What to do first, when to stop, the case layout, the Slack card and thread, and the keys later runs read. Carries `slack-delivery.md`. |
| `oauth-grant-review` | The judgement. The policy floor, what the agent may add to it, and the fields every assessment must produce. |
| `third-party-vendor-research` | Six questions about who publishes an app, with a source and a confidence on each. Knows nothing about Google or OAuth. |
| `case-writing-style` | How a sentence a person reads is written. |
| `org-chart` | Who approves access for whom. **A template.** Replace the example people. |

If you also run example 001, keep those skills on the same preset. The prompt below names them.

## Placeholders to replace

| Placeholder | Replace with |
|---|---|
| `#security-alerts`, `C0123456789` | Your review channel name and its channel ID |
| `https://<your-tracecat-host>/workspaces/<workspace-id>/cases/<case_id>` | Your Tracecat host and workspace ID. Leave `<case_id>`. |
| `admin@example.com` | The Google Workspace super-admin the service account impersonates |
| `<siem-query-url>`, `<workspace-token-logs>` | Your log store's query endpoint and the table that holds Workspace `token` events |
| The people in `skills/org-chart` | Your own reporting lines, on real addresses |

`U0123456789`, `j.lane@example.com` and `CASE-0001` are examples. Leave them.

The workflow reads the `oauth_` action IDs and the table names. Do not rename them without changing the workflow.

## Known gaps

- `TRIGGER.dry_run` is read by `deny_click` and `revoke_token`. Declare it as an input, default `true`, so a first run cannot call Google.
- An empty `revoke_approvers` list means nobody can revoke, which is the safe default. Fill it only after you have seen a test case.
- Blocking an app for the whole company is not in this workflow. Google's Policy API does not support changing API controls, and none of the settings targets a single app by client ID. The card links to the Admin console instead.
- `org-chart` resolves nobody until you replace the example people.
