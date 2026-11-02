# 003: Sublime phishing triage

Socky investigates one Sublime Security message group at a time. A workflow polls for flagged, high-severity, and user-reported mail, starts one Socky run per group, writes a case, and posts a Slack card with verdict buttons. A second Socky run hunts the same technique across mailboxes and replies in the card's thread. Detectee, a second preset, turns that hunt into a tested MQL proposal. A person approves the proposal. The workflow then creates the rule in Sublime and enables it. Socky never creates a rule. Detectee never creates a rule.

All values are placeholders. The skills in this folder were exported from a working workspace and sanitised: real people, mailbox addresses, account identifiers, channel names, and hostnames were replaced. `business-context` and `org-chart` are templates. Fill them in before you publish them.

This example can sit next to [001: Distributed alerting](../001-distributed-alerting/) and [002: Google OAuth app lifecycle](../002-google-oauth-app-lifecycle/). The same Slack app and the same `slack_work_objects` table serve all three.

## Files

| File | What it is |
|---|---|
| [`workflow.md`](workflow.md) | The Sublime phishing triage workflow: a diagram, the steps with their code, and the inputs |
| [`agent/preset.md`](agent/preset.md) | Two presets. Socky investigates. Detectee proposes a detection and does not deploy it |
| [`skills/`](skills/) | The phishing skills, plus the shared skills this path loads |
| [`slack-app-manifest.json`](slack-app-manifest.json) | The Slack app. Same as examples 001 and 002 |

## Prerequisites

- A workspace with agents, cases, and tables.
- A Slack workspace where you can create and install apps, or the Socky app from example 001.
- A Sublime Security API credential. Every Sublime action in the workflow passes `base_url`. The placeholder is `https://platform.sublime.security`.
- Google Workspace Directory and Reports, read only, so Socky can place a recipient and read the audit log around delivery.
- Optional: VirusTotal, AbuseIPDB, and urlscan, for the sender domain and the links.
- A model provider configured for the two presets.

## Setup order

1. Create the tables and the `phishing_verdict` dropdown listed in `workflow.md`.
2. Replace the placeholders listed below. Add the skills from `skills/`. Keep each skill's file names. Fill in `business-context` and `org-chart` with your own organisation before you publish them.
3. Create the Socky preset from `agent/preset.md` with the slug `socky`, or add the Sublime tools and the phishing skills to the Socky from example 001. Do not grant Socky `create_rule`, `activate_rule`, `review_message_groups`, or any quarantine action.
4. Create the Detectee preset from the same file with the slug `detectee`. Detectee is read only on Sublime. The workflow creates and enables a rule after a person approves it.
5. Create and install the Slack app, or reuse the app from example 001.
6. Build the workflow from `workflow.md`. Set its alias to `sublime_phishing_triage`. Leave the schedule off, `push_to_sublime` false, and `slack_channel` at `DRY-RUN-NO-POST`.
7. Point Slack at the workflow, or at a router that dispatches `phish_` clicks here by `action_id`.
8. Test everything, change nothing in Sublime: one flagged group, a second poll that finds the same group, a verdict click with `push_to_sublime` still false, Generate detection, and a thread question. Then set `push_to_sublime` true only if you want a verdict written back to Sublime, and try Approve rule on a proposal you are willing to enable.
9. Turn the schedule on only when you say so. A 5 minute schedule with `minutes` at 8 overlaps by about 3 minutes. Deduplication makes that overlap safe.

## Slack app setup

Same app as examples 001 and 002. If that app is already installed, skip part 1.

### Part 1: before the workflow exists

1. Go to https://api.slack.com/apps, choose "Create New App", then "From a manifest", and paste `slack-app-manifest.json`.
2. Install the app to the workspace.
3. Copy the Bot User OAuth Token. Create a secret named `slack` with the key `SLACK_BOT_TOKEN`.
4. Invite the app to the review channel: `/invite @socky`.

### Part 2: after the workflow is published

Slack posts every click and mention to one request URL. Two setups work.

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

**Example 001 is already receiving Slack.** Keep 001 as the request URL. Add a router step there that sends phishing clicks here by `action_id`: `phish_verdict_malicious`, `phish_verdict_spam`, `phish_verdict_benign`, `phish_scan_links`, `phish_gen_detection`, `phish_rule_approve`, `phish_rule_discard`.

Save. This adds no scopes, so you do not reinstall the app.

### The Event Subscriptions URL must end in `?echo=true`

Slack verifies the URL by sending a `challenge` and expecting it back. With `echo=true` the webhook returns the request body, challenge included, in its response. Without it, Slack reports the URL as unverified. The interactivity URL does not need it.

### Scopes

The manifest is unchanged from examples 001 and 002. Phishing cards are ordinary Block Kit messages. They do not need a scope the other examples do not already request.

| Scope | Why |
|---|---|
| `app_mentions:read` | Questions in a card's thread |
| `channels:history` | Reading the thread |
| `chat:write` | Posting and updating the card and the thread |
| `links:write` | Work Object unfurls used by examples 001 and 002 |
| `reactions:write` | The eyes and tick reactions on a mention |
| `users:read.email`, with `users:read` | Finding a person by email |

Add `groups:history` for a private channel.

## Skills

| Skill | What it owns |
|---|---|
| `sublime-phishing-case-lifecycle` | The path for one message group: the case, the card, the verdict record, and a question in the thread. Carries the button `action_id`s the workflow reads. |
| `email-evidence-core` | Which Sublime and Google reads are allowed, what each indicator proves, and the hunt-job contract. |
| `sublime-detection-engineering` | How Detectee authors, validates, and tests one MQL proposal. It does not create the rule. |
| `business-context` | Expected mail, the employee list, and normal account activity. **A template.** |
| `org-chart` | Who approves access for whom. **A template.** Same role as in example 002. |
| `hypothesis-driven-triage` | The question-driven method. Same skill as example 001. |
| `case-output` | How a case, a card, a brief, and a thread reply are shaped. Same skill as example 001. |
| `case-writing-style` | How a sentence a person reads is written. Same skill as example 002. |
| `slack-case-threads` | Mentions and the owner Yes/No buttons from example 001. The phishing buttons are not that path. A phishing thread question loads `sublime-phishing-case-lifecycle` section 11. |

If you already installed the shared skills from examples 001 and 002, keep those copies and add the four phishing skills. Do not publish a second `case-output`.

## Placeholders to replace

| Placeholder | Replace with |
|---|---|
| `#security-alerts`, `C0123456789` | Your review channel name and its channel ID |
| `https://<your-tracecat-host>/workspaces/<workspace-id>/cases/` | Your host and workspace id, with a trailing slash. This is `case_url_base`. |
| `https://platform.sublime.security` | Your Sublime API host, if it is not this one |
| `https://admin.google.com/ac/emaillogsearch` | The mail-log search in your admin console |
| `DRY-RUN-NO-POST` | Your channel id, only after a dry run has produced a case you have read |
| The rows in `business-context` and `org-chart` | Your own people, domains, and expected mail |

`jane.doe@example.com`, `CASE-0001`, and `U0123456789` are examples. Leave them.

The workflow reads the `phish_` action ids, the alias `sublime_phishing_triage`, and the table names. Do not rename them without changing the workflow.

## Known gaps

- `push_to_sublime` defaults to false. A verdict is stored on the case and is not written to Sublime until you set it true.
- `slack_channel` defaults to `DRY-RUN-NO-POST`, which is not a channel. A manual run posts nothing until you replace it.
- The investigate step does not hunt. The next step does. `sublime-phishing-case-lifecycle` says to skip the inline hunt when the prompt says a later step will run it.
- Approve creates a rule only when `mql_source` contains `type.`. An empty proposal posts no rule.
- `business-context` answers nothing until you replace the example rows. Until then Socky should say that no baseline is recorded.
