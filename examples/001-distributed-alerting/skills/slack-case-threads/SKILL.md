---
name: slack-case-threads
description: Load when the prompt is a raw Slack payload, either an Events API envelope or an Interactivity payload. Covers how Socky handles an app_mention (acknowledge, read the thread from Slack, answer, record on the case, mark done) and the owner's Yes/No buttons on a case. Conversation history is read from Slack with list_replies and list_messages, never from stored state. For a mention, load aws-cloud-incident-response-core and case-output before answering anything that needs evidence. For the owner's Yes/No buttons, load nothing else, because this skill holds every rule that path needs.
metadata:
  tools:
    - tools.slack.add_reaction
    - tools.slack.remove_reaction
    - tools.slack.list_replies
    - tools.slack.list_messages
    - tools.slack.post_message
    - tools.slack.update_message
    - tools.slack_sdk.call_method
    - core.cases.get_case
    - core.cases.update_case
    - core.cases.add_case_tag
    - core.cases.create_comment
    - core.table.lookup
    - core.table.insert_row
---

# Slack case threads

The workflow hands you whatever Slack sent, unchanged, as JSON. The Slack app subscribes only to `app_mention` events and to interactivity, so your own posts never come back to you. Read the payload, decide which of the cases below it is, and handle exactly that one. Test the rows of the table in order; the first that matches decides. When you are done, return one plain line for the run record saying what you did.

## Read the payload

| Payload | What it is | Section |
|---|---|---|
| `type` is `url_verification` | Slack checking the Request URL | Return `url_verification: nothing to do.` |
| `payload` is a JSON string whose `type` is `block_actions` and whose `actions[0].action_id` is `gd_confirm_yes` or `gd_confirm_no` | The owner answered the ask | [Owner buttons](#owner-buttons) |
| `payload` is any other JSON string | Another button or view on a bot message | Return `Interaction <type> <action_id>: not handled.` |
| `event.bot_id` is set, or `event.user` equals `authorizations[0].user_id` | A bot, or you | Return `Ignored a bot event.` |
| `type` is `event_callback` and `event.type` is `app_mention` | Someone mentioned the bot | [Mentions](#mentions) |
| anything else | Not for you | Return `Ignored <type>/<event.type>.` |

Work out `payload` with a JSON parse of the string. Slack form-encodes interactivity, so the string is the whole interaction.

## Mentions

1. **Acknowledge.** Call `tools.slack.add_reaction` with `name` `eyes` on `event.channel` and `event.ts`.
2. **Read the conversation from Slack.** The thread is `event.thread_ts`, or `event.ts` when the mention starts a new thread.
   - Call `tools.slack.list_replies` on the channel and that thread for the whole conversation, root first.
   - When the question refers to something said elsewhere in the channel, call `tools.slack.list_messages` on the channel with `oldest` and `latest` bounding the time you need.
3. **Find the case.**
   - Call `core.table.lookup` on `slack_work_objects` with column `message_ts` and the thread's ts.
   - Failing that, take the case id from a `cases/<uuid>` link in the root message.
   - With a case id, call `core.cases.get_case` for its description, fields and tags.
4. **Answer** as case-output's "Thread replies" describes:
   - one reply in the thread;
   - one `Slack: ` case comment;
   - at most one direct message, under the rules there.

   For an investigative question, fetch the case's evidence the way its own lifecycle skill does. A case tagged `guardduty`: its findings with boto3, as guardduty-case-lifecycle stage 1 describes. A case tagged `detection-event`: the events behind its alert ids, as detection-event-case-lifecycle stage 5 describes, from the SIEM, the source's read tool, or the facts stored on the case when you have neither; its `finding_ids` are alert ids, not GuardDuty findings, and its `source-<source>` tag names the source. Test the question with the method in hypothesis-driven-triage, and say what you could not check.
5. **Outside a case thread** (no case found): answer helpfully in the thread with the same evidence rules. Add no case comment and change nothing on any case.
6. **Mark done.** The reader should be left with a single green tick, never a tick sitting beside the eyes. Do these two in this order, on the same `event.channel` and `event.ts`:
   - Call `tools.slack.add_reaction` with `name` `white_check_mark`.
   - Then call `tools.slack.remove_reaction` with `name` `eyes`, clearing the acknowledgement you added in step 1.

   Add the tick first so a failure never leaves the message unmarked. When the remove call fails because the reaction is already gone or was never added, carry on; do not retry it and do not report it as an error.

A mention never changes a case's status, severity, disposition or tags, whatever it asks. A request to contain, disable, revoke, block, close or change the status gets the reply case-output prescribes, and nothing else.

## Owner buttons

This path is timed: the person who pressed is watching the ask. Everything the first visible write needs is already in `payload`, so load no other skill, read no bundled file, and make no call before the first write turn. Do not call `get_case`, `list_replies` or a shell or Python tool first. The workflow's `ack_owner_click` has already replaced the buttons with `:hourglass_flowing_sand: Answered Yes. Socky is recording it.` (or `Answered No.`), with no mention; your first write swaps that for the final status line.

### Read everything from `payload`

| Name | Value |
|---|---|
| case id | `actions[0].value` |
| short id, owner | Split `actions[0].block_id` on `:`. Current asks read `gd_confirm:<short id>:<owner user id>:<owner email>:<owner name>` (the name is everything after the fourth colon). Legacy asks read `gd_confirm:<short id>` or plain `gd_confirm`; with no short id, write `the case` wherever `<short id>` appears below |
| person | With an owner in the `block_id`: `<owner name> (<owner email>)`, for example `Jane Doe (jane.doe@example.com)`, never a `<@…>` mention. The workflow only lets the owner's own click through, so on behalf never applies. Without an owner (a legacy ask): `<@USER>` as before, with on behalf below |
| user | `user.id`; name is `user.name`, else `user.username`, else `user.id` |
| denied | `actions[0].action_id` is `gd_confirm_no` |
| on behalf | Legacy asks only: `message.text` does not contain `<@user.id>` (someone other than the person tagged pressed it) |
| channel | `container.channel_id`, else `channel.id` |
| ask ts | `container.message_ts`, else `message.ts` |
| thread (card ts) | `message.thread_ts` |
| press time | `actions[0].action_ts`, epoch seconds, written `DD Mon YYYY, HH:MM UTC`. Work it out yourself; never call a tool to format it. `1790726400` is `30 Sep 2026, 00:00 UTC`, a day is `86400` s, an hour `3600` s. Example: `1790730000` is `30 Sep 2026, 01:00 UTC` |
| press epoch | `int(actions[0].action_ts)`: the whole seconds, no decimal part, for example `1790730000`. Used for `metadata_last_modified` |

**Duplicate.** The workflow's `claim_event` is the primary guard: it lets only the first answer per case reach you. As a secondary guard, when `message.blocks` already holds a context block starting `:white_check_mark:` or `:x:`, the answer is recorded. Change nothing, post nothing, and return `Duplicate click on <short id>: already recorded.`

**Slack formatting.** Every edit and post here passes `text` (and `blocks` where given) in Slack mrkdwn, single asterisks for bold. Never pass `markdown_text`: Slack rejects a call carrying both (`markdown_text_conflict`), and `markdown_text` alone renders `*Verdict:*` in italics.

### Turn 1: all of these in parallel, in one turn

| Call | Yes (`gd_confirm_yes`) | No (`gd_confirm_no`) |
|---|---|---|
| `tools.slack.update_message` on channel and ask ts: `blocks` = every block of `message.blocks` except the actions block and any context block, plus one context block (mrkdwn) with the status line; `text` as shown | status line Yes, exactly as in "Exact text" below; `text` `<person> confirmed this activity. The case is set to Benign.` | status line No, exactly as in "Exact text" below; `text` `<person> denied this activity. The case is set to Escalate for a person to decide.` |
| `tools.slack.post_message` in the thread, `unfurl_links` false (thank-you) | `<@USER>, thank you for confirming this activity<on behalf>. <short id> is now Benign, and the security team will review and close it.` | `<@USER>, thank you for telling us this activity was not expected<on behalf>. <short id> is now Escalate: the security team will treat it as a possible credential compromise and will be in touch. You do not need to do anything else right now.` |
| `core.cases.add_case_tag`, `create_if_missing` true | `owner-confirmed` | `owner-denied`, and a second call for `escalation` |
| `core.cases.update_case` | field `disposition` `Benign`, severity `low` | field `disposition` `Escalate`, severity `critical` |
| `core.cases.create_comment` | `Slack: <person> confirmed the activity from the Slack thread<on behalf of the tagged person> (button gd_confirm_yes). Disposition set to Benign, severity low. Owner confirmation recorded.` | `Slack: <person> denied the activity from the Slack thread<on behalf of the tagged person> (button gd_confirm_no). Disposition set to Escalate, severity critical, escalation tag added; a person decides containment.` |
| `tools.slack.list_replies` on channel and thread (read, for turn 2) | same | same |
| `core.table.lookup` on `slack_work_objects`, column `external_ref_id`, value case id (read, for turn 2) | same | same |

### Exact text

The press time is always wrapped in backticks (Slack inline code) in the status line and the Verdict line. Copy these lines character for character, replacing only the angle-bracket placeholders. With block_id `gd_confirm:CASE-0001:U0123456789:jane.doe@example.com:Jane Doe` and 30 Sep 2026, 01:00 UTC as the press time, the Yes status line and Verdict line are exactly:

```
:white_check_mark: *Confirmed by* Jane Doe (jane.doe@example.com) at `30 Sep 2026, 01:00 UTC`. The case was set to Benign and a comment was added to CASE-0001.
*Verdict:* Benign. Jane Doe (jane.doe@example.com) confirmed this activity at `30 Sep 2026, 01:00 UTC`.
```

On a current ask, Slack mentions appear only in the thank-you (and in the brief's step 1, which you leave unchanged). On a legacy ask `<person>` is `<@USER>`, so the mention also appears in the status line, the Verdict line and the case comment.

The templates:

```
Yes status line:   :white_check_mark: *Confirmed by* <person><not tagged> at `<press time>`. The case was set to Benign and a comment was added to <short id>.
No status line:    :x: *Denied by* <person><not tagged> at `<press time>`. The case was set to Escalate and a comment was added to <short id>.
Yes Verdict line:  *Verdict:* Benign. <person> confirmed this activity at `<press time>`.
No Verdict line:   *Verdict:* Escalate. <person> denied this activity at `<press time>`; treated as a possible credential compromise.
```

(The labels before the colon and the spaces after them are not part of the text.)

`<not tagged>` is ` (not the person tagged above)`, `<on behalf>` is ` on behalf of the person tagged above`, and `<on behalf of the tagged person>` is ` on behalf of the tagged person`, each only when on behalf applies, else nothing.

### Turn 2: brief, card and row, in parallel

- **Brief.** In the `list_replies` result, the brief is the bot's reply whose text starts `*Verdict:*`. Replace its first paragraph and keep every paragraph after it unchanged, then save it with `tools.slack.update_message`, passing the whole brief as `text` with the blank lines between paragraphs:
  - Yes: the Yes Verdict line from "Exact text".
  - No: the No Verdict line from "Exact text".
- **Card.** Take the row's `entity`. Set the `verdict` custom field to `Benign · owner confirmed` or `Escalate · owner denied`, and `entity_payload.attributes.metadata_last_modified` to the press epoch, an integer (`int(action_ts)`, never the decimal `action_ts`). It must be greater than the stored value, or Slack keeps the old card: when a re-triage refreshed the card after the press and the press epoch is not greater, use the stored value plus 1. The row gets the same entity. Call `tools.slack_sdk.call_method` with `sdk_method` `chat_update` and params `channel` = the row's `channel`, `ts` = the row's `message_ts`, `text` `" "` (one space: `chat_update` rejects a missing or empty text with `no_text`, and any other text shows above the card), and `metadata` `{"entities": [<entity plus "app_unfurl_url": <entity url>>]}`. When Slack rejects metadata on `chat_update`, call `chat_unfurl` with the same `channel`, `ts` and `metadata`, as the card delivery instructions do.
- **Row.** `core.table.insert_row` with `upsert` true, table `slack_work_objects`, and `row_data` with exactly these five columns: `external_ref_id` (case id), `case_id`, `channel`, `message_ts`, `entity` (the updated entity, without `app_unfurl_url`). Never send `updated_at`: it fails with "Column 'updated_at' does not exist".

When the thread has no brief or the table has no row, skip that write and say so in the return line. Only then, and only when a later write needs something the payload lacks, call `core.cases.get_case` on the case id.

Return `<short id> confirmed by <name>: Benign, low.` or `<short id> denied by <name>: Escalate, critical.`
