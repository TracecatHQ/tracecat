---
name: tracecat-slackbot-best-practices
description: Use when building, editing, validating, or debugging Tracecat Slack bots and Slack-facing automations through Tracecat MCP, including Slack app mentions, interactive messages, event subscriptions, webhooks, thread replies, Slack tools, ai.agent or ai.preset_agent bots, Slack tone, and Slack smoke tests.
---

# Tracecat Slackbot Best Practices

## When To Use

Use this skill only for Slack-facing Tracecat automations: Slack bots, app mentions, Slack interactivity, button callbacks, thread replies, message reads, Slack event subscriptions, or Slack smoke tests.

For generic Tracecat workflow, table, run-python, or agent-preset guidance, use `$tracecat-automation-best-practices` instead.

## Bot Architecture

Build Slack bots with `ai.agent` or `ai.preset_agent`.

- Use inline `ai.agent` when the Slack behavior is specific to one workflow and should travel with that workflow.
- Use `ai.preset_agent` when the bot persona, tools, and instructions should be reusable across workflows.
- Give the agent Slack send/post message tools plus list/read message or reply tools when it needs thread context.
- Fetch the Slack thread before invoking the agent, and tell the agent that thread context is required input.
- Post visible replies back to the original channel and thread. Do not answer only in the agent transcript.

**The agent owns the message — composition and posting.** Composing or sending a Slack
message is agentic work, not data plumbing. Reserve deterministic nodes for data plumbing
around the agent (fetching the thread, redacting, upserting state).

- WRONG: a `core.script.run_python` or `core.http_request` node formats the text and posts to
  Slack, and the agent only returns a string. This buries the wording in a script, makes
  Block Kit and tone hard to iterate, and splits responsibility.
- RIGHT: the agent is given the Slack send/reply tool and owns both composing the message
  (mrkdwn/Block Kit, tone) and posting it to the original thread, per its instructions.

If a deterministic step formats the agent's output for Slack, that is the smell — move the
formatting and posting into the agent.

Prefer the `model` object for `ai.agent`; top-level `model_name` and `model_provider` are deprecated unless the user explicitly asks for the legacy shape.

```yaml
args:
  model:
    model_name: claude-sonnet-4-6
    model_provider: anthropic
```

## Slack App Setup

For setup:

- Configure the Tracecat workflow webhook.
- Add the webhook to Slack interactivity at `https://api.slack.com/apps/{app_id}/interactive-messages`.
- For @mention back-and-forth chat, configure Slack event subscriptions for app mentions and put the webhook URL in Slack with `?echo=true` appended for reliable mention-thread loops.

Typical event handling:

1. Receive Slack `event_callback` for `app_mention`.
2. Extract `event.channel`, `event.ts`, `event.thread_ts || event.ts`, `event.user`, and `event.text`.
3. Add a lightweight processing reaction if desired.
4. Fetch the whole Slack thread with a Slack list replies/messages tool.
5. Build an agent prompt that includes the sanitized thread JSON.
6. Invoke the agent.
7. Remove the processing reaction after the agent finishes.

## Interactivity

For Slack buttons and block actions:

- Parse the `payload` string when Slack sends URL-encoded interactivity.
- Extract `actions[0].action_id`, `actions[0].value`, `channel.id`, `message.ts`, `message.thread_ts || message.ts`, and `user.id`.
- Button clicks should post a visible thread reply unless the product requirement is to update the original message.
- Do not add or remove reactions for button clicks unless the workflow explicitly requires it.

## Prompting Rules

Slack-facing agents need explicit production posting rules in their own instructions:

- Always post to the original Slack channel and thread.
- Use Slack mrkdwn, not generic Markdown, when posting text.
- Use Block Kit only when the response needs buttons, links, compact review layout, or structured blocks.
- Use a reasonable, calm, critical tone. Keep risk language grounded in evidence rather than inflated.
- Prefer positive, preferred-vocabulary instructions over avoid-word blocklists. Naming the exact words to avoid (e.g. "suspicious", "critical", "breach") seeds those tokens into context and can prime them; instead state the phrasing you want and require risk claims to be evidence-backed.
- State each rule once. Don't restate rules in a large end-of-prompt validation checklist — duplication bloats the prompt and drifts out of sync.
- Avoid emojis unless they make the point clearer or are part of a deliberate lightweight status convention.
- Keep style rules in the preset or `ai.agent` instructions. The agent reads its own instructions, not repo files.
- If a Slack post fails, return a concise failure reason and enough context for workflow debugging.

## Testing

After publishing a Slack-facing automation, do a live smoke test:

1. Send a normal Slack message that mentions the bot.
2. Confirm the bot posts a visible reply in the original thread.
3. Confirm the Tracecat run was `trigger_type: webhook` and `execution_type: published`.
4. Inspect the execution timeline and failed action payloads with Tracecat MCP if the reply is missing or delayed.
5. Confirm processing reactions are cleaned up after completion.

Do not paste secret-bearing Slack payloads or Tracecat action outputs into docs or chat. Summarize routing, status, action refs, timing, and non-sensitive behavior.
