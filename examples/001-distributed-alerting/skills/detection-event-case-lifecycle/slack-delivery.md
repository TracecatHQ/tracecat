# Slack delivery for a detection event case

A case is published in `#security-alerts` (`C0123456789`) as one thread, in the order a reader opens it:

1. **Work Object card**: the thread parent.
2. **Brief** (Reply 1): the answer.
3. **Evidence table** (Reply 2): one row per entity a responder would act on.
4. **Owner ask**: the Yes/No buttons, only when the records link the session to an employee.

A re-triage edits these messages in place; it never posts a second card or brief. What each message says is defined in case-output, read with the substitutions in this skill's stage 7. This file covers where each message goes and how it is kept current.

## Find what is already posted

1. Call `core.table.lookup` with table `slack_work_objects`, column `external_ref_id` and the case id as value. A row gives you the card's `channel` and `message_ts`, and the `entity` last posted.
2. With a card, call `tools.slack.list_replies` on its `channel` and `message_ts` to read the thread.
   - The brief is the bot's reply whose text starts `*Verdict:*`.
   - The evidence table is the bot's reply holding a `table` block.
   - The owner ask is the bot's reply holding the actions block whose `block_id` starts `gd_confirm`.

## The entity

The card and the details panel show the same entity: one object in the shape Slack's `entity.presentDetails` takes. Build it fresh on every post or refresh:

```json
{
  "entity_type": "slack#/entities/incident",
  "url": "<case URL>",
  "external_ref": { "id": "<case_id>", "type": "tracecat_case" },
  "entity_payload": {
    "attributes": {
      "title": { "text": "<case title>" },
      "display_id": "<case short id, e.g. CASE-0001>",
      "display_type": "Detection triage",
      "product_name": "Tracecat",
      "metadata_last_modified": <integer epoch seconds, see below>
    },
    "custom_fields": [ <rows below, blank rows left out> ],
    "display_order": ["seen", "rule", "verdict", "deviation", "account", "who", "object", "finding_ids"],
    "fields": {},
    "actions": {
      "primary_actions": [
        { "text": "Open Tracecat case", "action_id": "gd_open_case", "url": "<case URL>", "accessibility_label": "Open this case in Tracecat" },
        { "text": "Open alert in SIEM", "action_id": "gd_open_alert", "url": "<alert link for the first alert id>", "accessibility_label": "Open this alert in the SIEM" }
      ],
      "overflow_actions": []
    }
  }
}
```

Each custom field is `{"key": <key>, "label": <label>, "type": "string", "long": true, "value": <value>}`:

| key | label | value |
|---|---|---|
| `seen` | Seen | `<first seen> to <last seen>, <count> events`, in reader format; when first and last seen are the same instant, `<time>, <count> events`. Write `1 event` when the count is 1 |
| `rule` | Detection rule | the card rule from case-output (120 characters at most) |
| `verdict` | Verdict | `<status> · <confidence>`, for example `Open · medium` |
| `deviation` | Deviation | the card deviation from case-output; leave the row out when you have none |
| `account` | Account | `<deployment name or Unknown deployment> (<account id>)`; leave the row out when the alert names no account |
| `who` | Who | the card who from case-output (120 characters at most) |
| `object` | Object | the alert's resource, or `not recorded` |
| `finding_ids` | Alert IDs | every alert id on the case, comma separated |

The keys stay as they are, `finding_ids` included, because the thread and button handling read the entity by key. Compared with a GuardDuty card, the labels, the `display_type`, the second button (`gd_open_alert`) and the alert-specific values in the table above differ. Everything else is the GuardDuty shape.

`seen` comes first because the card's collapsed preview shows only the top rows; the time is what a responder needs before anything else.

`metadata_last_modified` must be greater than the value in the stored entity, or Slack keeps showing the old card. Use now in epoch seconds. When now is not greater than the stored value, as on two refreshes within one second, use the stored value plus 1.

## Post or refresh the card

**No visible text.** Any text on the card message, even the short id, renders above the card, and the card's `Open Tracecat case` button is the only case link in the thread. `chat_postMessage` accepts metadata alone, so leave `text` out. If Slack answers `no_text` all the same, send the call again with `text` `" "` (one space). `chat_update` rejects a missing or empty `text` (`no_text`), so pass a single space, which renders nothing.

1. **No card yet.** Call `tools.slack_sdk.call_method` with `sdk_method` `chat_postMessage`. Pass these params:
   - `channel`: `C0123456789`
   - `metadata`: `{"entities": [<entity plus "app_unfurl_url": "<case URL>">]}`
   - `unfurl_links` and `unfurl_media`: false
   - no `text` (one space only on the `no_text` retry above)

   The reply's `ts` is the card's `message_ts`.
2. **Card exists.** Call `chat_update` with `channel`, `ts` = the card's `message_ts`, `text` `" "` (one space), and the same `metadata`.
   - When Slack rejects metadata on `chat_update`, call `chat_unfurl` with `channel`, `ts` and `metadata`.
   - Note in the case comment or run line which call worked.
3. **Write the row** with `core.table.insert_row`, `upsert` true, table `slack_work_objects`. Set `row_data` to:

   ```json
   {
     "external_ref_id": "<case_id>",
     "case_id": "<case_id>",
     "channel": "<channel>",
     "message_ts": "<card ts>",
     "entity": <the entity, without app_unfurl_url>
   }
   ```

   Pass exactly these five columns. The table keeps its own `created_at` and `updated_at`; sending `updated_at` fails with "Column 'updated_at' does not exist".

   The row is what answers the card's details panel. Together with the brief, it also tells a later run that this alert is already published. Write it every time you post or refresh the card, and whenever the verdict changes.

## Post or edit the brief

**Slack formatting.** The brief, the evidence table fallback and the owner ask go out as `text` (and `blocks` where given) in Slack mrkdwn: single asterisks for bold. Never pass `markdown_text` on these messages. Slack rejects a call that carries both (`markdown_text_conflict`), and `markdown_text` alone renders `*Verdict:*` in italics.

The brief is written per case-output ("Slack brief"). The message text is the brief with a blank line between its labelled lines. It carries no case link; the card's button is the only one.

**When an owner ask will be posted** (see "Owner ask": a linked email and no ask in the thread yet), call `tools.slack.lookup_user_by_email` with the linked email *before* writing the brief. Next step 1 is then exactly `1. <@USERID>, can you confirm whether this activity was yours?`, the only Slack mention in the brief. That mention is what notifies the person. On a re-triage where the ask is already in the thread, keep step 1 as it is until the case has an `owner-confirmed` or `owner-denied` tag.

- **No brief yet:** call `tools.slack.post_message` with `channel`, `thread_ts` = the card's ts, the text, and `unfurl_links` false.
- **Brief exists:** call `tools.slack.update_message` with `channel`, `ts` = the brief's ts, and the new text ending in a last line `_Updated <report time>_`.

## Post or edit the evidence table

Follow case-output ("Evidence table"). Post it with `tools.slack.post_message`, using `blocks` holding the single `table` block and a short `text` fallback (`Entities for <short id>`), in the card's thread after the brief. On re-triage, edit it with `tools.slack.update_message`, passing `channel`, `ts` = the table reply's ts, and the new `blocks` and `text`. Skip it when there are no rows.

## Owner ask

Post the ask only when the linked email is set **and** the thread holds no owner ask yet (see "Find what is already posted"). The person is asked once per case, not on every re-triage: when the ask is in the thread, never post a second one. When an earlier run posted the brief but not the ask, because the lookup found no one or the post failed, this run posts it. A case the owner has answered stops in stage 4 and never reaches this step.

Slack mentions in a case thread appear only in the brief's next-step question and in the thank-you after an answer. The ask itself tags no one and asks nothing: the brief's step 1 already asks the person by name. The ask says why and what each answer does, then the buttons. It never restates the alert.

1. Use the user from the lookup you made before the brief (`tools.slack.lookup_user_by_email` with the linked email). When no user came back, post no ask and no question step, and say so in the run line. The description written in stage 7 then says the person has been asked, which is not true. Correct it with `core.cases.update_case`: replace the closure sentence with the no-Slack-user sentence from case-output ("Case description"). From the result take the Slack user id, the email, and the display name (`real_name`, else `profile.real_name`, else `name`), for example `U0123456789`, `jane.doe@example.com`, `Jane Doe`.
2. Call `tools.slack.post_message` in the card's thread, with `unfurl_links` false. The `text` is `Confirm or deny this activity with the buttons below.`. The `blocks` are exactly these two:

```json
[
  { "type": "section", "text": { "type": "mrkdwn", "text": "We're asking because our records link this session to you and a detection rule flagged the activity. *Yes* records the case as Benign. *No* moves it to Escalate, and the security team will treat it as a possible credential compromise." } },
  { "type": "actions", "block_id": "gd_confirm:<short id>:<user id>:<email>:<display name>", "elements": [
    { "type": "button", "action_id": "gd_confirm_yes", "style": "primary", "text": { "type": "plain_text", "text": "Yes, that was me" }, "value": "<case_id>" },
    { "type": "button", "action_id": "gd_confirm_no", "style": "danger", "text": { "type": "plain_text", "text": "No, not me" }, "value": "<case_id>" }
  ] }
]
```

No mention, no question, no fields section and no context block. The button value is the case id. The `block_id` and the two `action_id`s are the same as on a GuardDuty case, character for character, because the workflow and slack-case-threads read them: the `block_id` names the case and the owner, name last, for example `gd_confirm:CASE-0001:U0123456789:jane.doe@example.com:Jane Doe` (keep the whole `block_id` at 255 characters or fewer: drop characters from the end of the name, down to no name at all; if it is still too long, the email does not fit, so post no ask and handle it as a failed lookup). The workflow reads the owner's user id from it and ignores a click by anyone else; slack-case-threads reads the short id, name and email from it, so it can answer without reading the case first, and replaces the buttons with a status line.
