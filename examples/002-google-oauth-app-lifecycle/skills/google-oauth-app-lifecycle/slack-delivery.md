# Slack delivery for an OAuth app review

One app is published as one thread in the channel the prompt names, in the order a reader opens it:

1. **Work Object card**: the thread parent. One per app, for the life of the app.
2. **Brief and manager ask** (Reply 1): the verdict in a few lines, the publisher finding, the approval request and the buttons.
3. **Later thread lines**: a new holder, or a wider scope set. Added as replies; they never edit the brief.

A refresh edits the card in place. It never posts a second card.

## Find what is already posted

1. The review row from stage 2 gives `message_ts` and `slack_channel`. That message is the card.
2. With a card, call `tools.slack.list_replies` on that channel and timestamp.
   - The brief is the bot's reply whose text starts `*Verdict:*`.
   - The manager ask is the bot's reply holding the actions block whose `block_id` starts `oauth:`.
3. With no `message_ts` on the row, no card exists yet. Post one.

## The entity

The card and the details panel show the same object, in the shape Slack's `entity.presentDetails` takes. Build it fresh on every post and every refresh.

```json
{
  "entity_type": "slack#/entities/incident",
  "url": "<case URL>",
  "external_ref": { "id": "oauth:<short name>", "type": "tracecat_case" },
  "entity_payload": {
    "attributes": {
      "title": { "text": "<case title>" },
      "display_id": "<case short id, e.g. CASE-0144>",
      "display_type": "OAuth app review",
      "product_name": "Tracecat",
      "metadata_last_modified": <now, integer epoch seconds>
    },
    "custom_fields": [ <rows below, blank rows left out> ],
    "display_order": ["app", "event", "severity", "granted_by", "publisher", "scopes", "summary", "client_id"],
    "fields": {},
    "actions": {
      "primary_actions": [
        { "text": "Open Tracecat case", "action_id": "oauth_open_case", "url": "<case URL>", "accessibility_label": "Open this OAuth review in Tracecat" },
        { "text": "Review in Admin Console", "action_id": "oauth_open_console", "url": "https://admin.google.com/ac/owl/list?tab=configuredApps", "accessibility_label": "Open the connected apps list in the Google Admin console" }
      ],
      "overflow_actions": []
    }
  }
}
```

Each custom field is `{"key": <key>, "label": <label>, "type": "string", "long": true, "value": <value>}`:

| key | label | value |
|---|---|---|
| `app` | App | `<app_name>`, or `No name` when the developer set none |
| `event` | Event | `Access requested`, `Connected`, or `Requested and connected` |
| `severity` | Risk | the assessed risk in capitals, for example `MEDIUM` |
| `granted_by` | Granted by | `<primary_user>`, plus ` +<n> more` when other people hold it |
| `publisher` | Publisher verified | `Yes`, `No` or `Unknown` |
| `scopes` | Scopes | the short form of up to five scopes, comma separated, then ` +<n> more` |
| `summary` | Summary | the assessment summary, 300 characters at most |
| `client_id` | Client ID | the full client ID |

`app` comes first because no reader recognises a client ID. `event` comes second because it decides what the reader is being asked: an **Access requested** app holds nothing yet, a **Connected** app already holds the access. The client ID is last because it is needed only when going to the console.

Three rules that have each cost a run:

- **Keep the whole metadata object under 4000 bytes.** Over that, Slack accepts the message with `ok: true` and silently drops the metadata, so the card renders as plain text and the details panel stays empty. The 300-character summary and the five-scope cap are what keep it under.
- **`metadata_last_modified` must be greater than the value in the stored entity**, or Slack keeps showing the old card.
- **`fields` must be present and empty.** Slack rejects the payload without the container.
- **No dot may appear in `external_ref.id`.** Slack answers `invalid_metadata_format`, and every client ID contains dots. Use `oauth:` plus the short name, as the skill's key table defines it, and write that same value to `slack_work_objects.external_ref_id` so the details panel still finds the row.
- **Work Object actions render only as URL buttons, and only two.** An action without a `url` is dropped in silence, with `ok: true` on the response. Every interactive control therefore lives in the thread reply, not on the card.

## Post or refresh the card

**The card is a Work Object, not a message with links.** It exists only when the call carries a `metadata` object. Observed on 12 Oct 2026: an agent posted the verdict as ordinary text with two markdown links, and no card rendered at all.

Two rules decide whether a card appears:

- **Use `tools.slack_sdk.call_method`, with `sdk_method` `chat_postMessage` and the `metadata` argument.** That is the only call in your toolkit that carries Work Object metadata.
- **Never use `tools.slack.post_message` for the card.** It cannot pass `metadata`. It posts text, you see `ok: true`, and the result is a plain message with no card. `tools.slack.post_message` is for the thread replies only, never for the parent.

A message that names the app, the risk and a link is **not** a card. If you cannot pass `metadata`, post nothing as the parent, say so in the run line, and stop. A wrong parent message cannot be upgraded into a card later; it has to be deleted by hand.

**No visible text.** Any text on the card message renders above the card, and the card's `Open Tracecat case` button is the only case link in the thread.

1. **No card yet.** Call `tools.slack_sdk.call_method` with `sdk_method` `chat_postMessage` and these params:
   - `channel`: the channel the prompt names
   - `metadata`: `{"entities": [<the entity, plus "app_unfurl_url": "<case URL>">]}`
   - `unfurl_links` false, `unfurl_media` false
   - no `text`

   The reply's `ts` is the card's `message_ts`. Save it on the review row.

   **Check the result before you go on.** `ok: true` alone does not prove the card rendered: Slack accepts the message and drops oversized or malformed metadata in silence. Read the response for a `warning` field, and confirm the posted message carries `metadata`. When the metadata did not survive, shorten the summary and the scope list and call `chat_update` with the same metadata, then say in the run line which attempt worked.
2. **Card exists.** Call `chat_update` with `channel`, `ts` the saved `message_ts`, `text` `" "` (one space, which renders nothing; an empty or missing text is rejected with `no_text`), and the same `metadata`.
   - When Slack rejects the metadata on `chat_update`, call `chat_unfurl` with `channel`, `ts` and `metadata`.
   - Say in the run line which call worked.
3. **Write the Work Object row** with `core.table.insert_row`, `upsert` true, table `slack_work_objects`. Pass exactly these five columns:

   ```json
   {
     "external_ref_id": "oauth:<short name>",
     "case_id": "<case_id>",
     "channel": "<channel>",
     "message_ts": "<card ts>",
     "entity": <the entity, without app_unfurl_url>
   }
   ```

   The table keeps its own `created_at` and `updated_at`. Sending `updated_at` fails with "Column 'updated_at' does not exist".

   This row is what answers the card's details panel. Write it every time you post or refresh the card.

## Post or edit the brief and the manager ask

**Slack formatting.** Send `text` and `blocks` in Slack mrkdwn: single asterisks for bold. Never pass `markdown_text`. Slack rejects a call carrying both (`markdown_text_conflict`), and `markdown_text` alone renders `*Verdict:*` in italics.

With no brief yet, call `tools.slack.post_message` with `channel`, `thread_ts` the card's `ts`, `unfurl_links` false, a `text` fallback of `Verdict: <risk> for <app_name>`, and these blocks.

Block 1, the brief:

```
*Verdict:* <RISK IN CAPITALS> · <event type>
<summary>

*What happened*
<what_happened>

*Who holds it*
<attribution>
```

The event type sits on the verdict line because it changes what the manager is deciding: whether to allow access that does not exist yet, or whether to let access that already exists stay. Do not put the access rule or any policy restatement in the thread. It reads as filler next to the decision, and the case already carries it.

Block 2, the publisher and the research, as a `context` block: `Publisher verified: <Yes | No | Unknown>. <two sentences at most.>`

Make those two sentences earn their place. The first says what settles the publisher, which is almost always whether a source names this exact client ID. The second gives the most useful other thing the search found: a stated certification, a published incident, an acquisition, or the fact that no source reports one. The full finding is in the case under Research, so this is the headline, not the report.

Keep this block short. Lead with the one fact that settles the publisher, which is almost always whether a source names this exact client ID. Certifications, funding, headcount and incident history belong in the case, not here. Use at most one link, and never repeat the same link: a reader on a phone skips a paragraph of repeated vendor links, and the fact that decides the question gets lost inside it.

Block 3, the ask, as a `section` block. Tag the manager and the person by Slack ID: `<@MANAGER> — does <@PERSON> need *<app_name>* for their work? Approve records your decision on the case. It changes nothing in Google.` Follow it with a `context` block: `Only the manager named here can approve. Security decides separately whether the access stays.`

Block 4, the buttons, as an `actions` block:

```json
{ "type": "actions", "block_id": "oauth:<user_email>:<client_id>:<manager_slack_id>", "elements": [
  { "type": "button", "action_id": "oauth_mgr_approve", "style": "primary", "text": { "type": "plain_text", "text": "They need it" }, "value": "<case_id>" },
  { "type": "button", "action_id": "oauth_mgr_deny", "text": { "type": "plain_text", "text": "They do not need it" }, "value": "<case_id>" },
  { "type": "button", "action_id": "oauth_revoke", "style": "danger", "text": { "type": "plain_text", "text": "Revoke access" }, "value": "<case_id>" }
] }
```

Rules on this block:

- Those three `action_id` values are a contract with the interaction lane. Do not rename them, do not add a fourth button, and do not put a URL button here. A URL button in an actions block also sends an interaction payload, which the lane would refuse and the reader would see as an error.
- The `block_id` keeps `oauth:`, the person, the app and the manager in that order, and stays under 255 characters.
- The `value` on every button is the case id.
- With no manager Slack ID, write `none` as part 3 and send only the revoke button.
- **On an Access requested app, leave the revoke button out.** No token exists, so the call would do nothing. Send the two decision buttons alone, and say in the ask that the person has not connected the app yet.
- **The revoke button carries no `confirm`, because it no longer revokes anything.** Revoking is two steps. This button asks the workflow to offer four reasons; the reason button is what calls Google, and the workflow puts the confirm modal there, naming the person, the app and the chosen reason. Putting a modal here as well would make an ordinary revoke three clicks and teach people to dismiss modals without reading them.
- **The four reason `action_id` values belong to the workflow, not to you.** They are `oauth_revoke_not_needed`, `oauth_revoke_excess_scope`, `oauth_revoke_untrusted` and `oauth_revoke_compromise`. Never put them on a card or a thread reply yourself. The workflow posts them as a visible message in the card's thread, so the channel can see a revoke being reasoned about; only a listed security responder can complete one.
- Never put a `confirm` on the two decision buttons. Those are reversible.

**Brief exists.** Call `tools.slack.update_message` with its `ts` and the new blocks, and add a last context line `Updated <report time, reader format>`.

**Rebuild every block, including the buttons.** Build all four blocks fresh from this file on each update. Never read the posted message and carry its blocks forward. Slack renders a button exactly as the payload describes it, so a button copied from an older message keeps that message's shape for ever, and any rule added to this file never reaches a thread that already exists. Measured on 12 Oct 2026: four cards were refreshed after the `confirm` rule was added and all four kept the old revoke button with no modal, because the agents updated the text and left the actions block alone.

**Check the block before you send it.** The actions block carries exactly the three `action_id` values this file defines, and no `confirm` on any of them. A card that carries a reason `action_id`, or a `confirm` on the revoke button, is a defect left over from an older shape: rebuild it rather than send it, and say in the run line that you corrected one.

## Later thread lines

A new holder or a wider scope set adds one reply. It never edits the brief.

- **New holder.** `tools.slack.post_message` in the card's thread. Text: `<@NEW PERSON> also connected *<app_name>* on <event time, reader format>. The risk is unchanged at <risk>.` Follow it with an `actions` block carrying **only** the revoke button, with the new person's address in part 1 of the `block_id`, so security can act on that person alone.
- **Wider scope set.** One reply naming the scopes that are new, the floor they set, and whether the risk moved. No buttons.

Then refresh the card, because the holder count and the scope list changed.
