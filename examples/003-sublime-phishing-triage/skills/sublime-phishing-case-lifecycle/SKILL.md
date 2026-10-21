---
name: sublime-phishing-case-lifecycle
description: "Load when the prompt's first line is `Phishing message <canonical_id>`, followed by one Sublime message group as JSON. Takes one flagged or user-reported email from Sublime to a triaged case and a published Slack card. You read the group, pull its indicators with MQL, deduplicate it against existing cases, scope the campaign across the tenant, place each recipient in Google Workspace, look for interaction and account abuse in the audit log, score it, write the case and post the card. Also load when the first line is `Phishing resolution <canonical_id>`, which carries an analyst's resolved verdict that the workflow has already pushed to Sublime; on that path you record the outcome and, when containment is still outstanding, write the manual quarantine handoff. Load with email-evidence-core, business-context, hypothesis-driven-triage, case-output, case-writing-style, slack-case-threads and org-chart."
---

# Sublime phishing case lifecycle

One Sublime message group, from the alert to a case a person can act on in under a minute.

A message group is the unit of work, not a message. Sublime groups the messages of one campaign, so a campaign that reached 40 mailboxes is one group, one case and one Slack card. Never open a case per recipient.

## Which path you are on

| The first line of the prompt | The path |
| --- | --- |
| `Phishing message <canonical_id>` | Triage. Section 2. |
| `Phishing group to investigate` | Triage. Section 2. The workflow uses this first line for the same path. |
| `Phishing resolution <canonical_id>` | Record an analyst's resolution. Section 5. |

The prompt also carries, as JSON, the values the workflow derived. Use them exactly as given and never recompute them:

- `canonical_id` — the Sublime message group canonical ID
- `case_tag` — the dedupe tag, always `phish-<canonical_id>`
- `source_line` — the line you put in the case description verbatim
- `slack_channel` — the only channel you post to on this run
- `sublime_base_url` — the API host for every Sublime call on this run
- `case_url_base` — the base for a case link, with a trailing slash. Append the case ID after you create the case. Without it there is no case link and no working Open case button, so never leave the Case field as plain text when this value is present.
- `sublime_console_url` and `google_admin_url` — the targets for two of the three link buttons

Load `email-evidence-core` before your first read. It says which tools you may call and what each indicator proves. This skill says what order to do things in and what to produce.

## Two constraints to set for your tenant

Both describe a tenant where the Autonomous Security Analyst is off and Sublime cannot quarantine. They shape every case you write. Change them before you publish the skill if your tenant differs.

1. **The Autonomous Security Analyst does not run here.** `asa_verdict` is always null. Never wait for it, never ask for its report, and never describe an analyst verdict in a case. Sublime's analysis reaches you as the flagged rules and the attack score verdict.
2. **Sublime cannot contain anything here.** No quarantine, no release, no warning banner. Containment happens in Google Workspace and a person decides it. Your job on every path is to make that person's decision easy, never to take it.

## 1. Before you read anything

Load `email-evidence-core`, `business-context` and `hypothesis-driven-triage`.

The triage method is the one in `hypothesis-driven-triage`: state what you would expect to see if the message were a real attack, state what you would expect if it were benign, then read for the difference. The hypotheses for this family are:

1. **A real attack that reached a person.** The message was delivered, it has a credential-harvesting URL or a macro-enabled attachment, and the recipient's account did something new afterwards.
2. **A real attack that was stopped.** The recipient reported it without acting, or the message was never delivered.
3. **A broad campaign.** The same URL or attachment hash reached several mailboxes, and at least one of them is privileged.
4. **A legitimate message that failed a check.** Authentication failed because of forwarding or a configuration fault, and nothing else is wrong.
5. **A known pattern.** `business-context` already describes this sender, this vendor or this notification as expected.

Hypothesis 5 is checked against the business context, never assumed. Hypothesis 4 is the most common and the easiest to get wrong in the alarming direction.

## 2. Triage

Do these in order. Do not write anything for a reader until step 7.

### 2.1 Read the group, then pull its indicators

Call `get_message_group` with `canonical_id`, even when the prompt already holds the group. The prompt is a snapshot and the record may have moved.

Note, from the record:

- Sender address, sender display name, sender domain
- Subject, delivery time, message count
- Every recipient mailbox, and each message's Sublime message ID
- Delivery state, and `message_links_clicked`
- Flagged rules, each with its severity
- The attack score verdict
- `review_status`, which tells you whether a verdict has already been pushed to Sublime
- Whether anybody reported it, and who

Then take one message ID from the group and call `analyze_message_by_id` once, asking for every field you need in a single `queries` list. `email-evidence-core` section 2.1.1 gives the query set and what each one returns. **Always include `headers.message_id`**: it is the only exact handle on this mail in Google Workspace, a person needs it to quarantine, and it must reach the case.

Call `get_rule` only when a flagged rule's name does not tell you what it detects.

**Always establish the sender domain's age.** Call `tools.virustotal.lookup_domain` on the registered domain and read the creation date, on every case, including one with no links. A domain registered days or hours before the message is among the strongest signals phishing offers, it costs one call, and it is the fact a reader acts on. When the lookup returns nothing usable, say so in the indicator table rather than leaving the row blank.

### 2.2 Deduplicate, and verify the tag

Search cases for `case_tag`.

**A non-empty result is not a match.** `core.cases.search_cases` silently drops a tag filter whose ref does not resolve to a tag that already exists, and then returns the newest case in the workspace instead. A new phishing message has no `phish-<canonical_id>` tag yet, so this is the normal case, and the result arrives looking like a clean hit.

Therefore, for every case the search returns:

1. Read that case's own tag list.
2. Confirm `case_tag` is in it.
3. Treat only a confirmed tag as a match.

If no returned case carries the tag, the group is new. Go to 2.3.

If a case carries the tag:

- If it is open, add one comment saying what is new since the case was last written: new recipients, a higher message count, a new flagged rule, a change in delivery state. Then **stop**. Do not post a new Slack card, and do not rewrite the case description.
- If a person has already decided it, resolved it or closed it, add one comment recording that the group was seen again, and stop. Never reopen it and never remove a tag.
- If nothing has changed at all, add no comment. Say so in your closing line.

This step is the only thing that stops a repeated poll from duplicating work, so never skip it, and never skip the tag readback.

Then search the sender domain and each attachment SHA256 across cases from the last 7 days. A hit here is a related case, not the same case. Note it as context and keep going.

### 2.3 Scope the campaign, then hunt the technique

This step has two halves. The first finds the campaign Sublime already flagged. The second finds the part it did not. Do both on a malicious verdict.

#### 2.3.1 The flagged campaign

Sublime can see the whole tenant, so this is where the first answer comes from. Search message groups from the last 7 days by, in order:

1. Attachment SHA256, when the group has one. This is the strongest link.
2. Each URL, or its registered domain.
3. Sender email, then sender domain.
4. The flagged rule ID.

Use `search_message_groups` or `list_message_groups` with the matching filter. Do not search on the subject; a subject is trivial for an attacker to change and it produces false links.

Record:

- How many distinct mailboxes received the campaign
- How many messages in total
- Which indicator links them, named exactly
- Whether the newest message is recent enough that the campaign is still arriving
- How many were delivered

Then stop and read the limit of what you just did. Every one of those searches reads **flagged** groups. An attacker who changed the sender, the link or the subject is not in that result, and the count you recorded is therefore a floor.

#### 2.3.2 The technique hunt

**When the prompt says a later step runs the hunt, do not start one here.** The workflow's investigate step says this. On that run, skip the rest of this subsection, write `not run` in the Hunt field, and say the result will arrive in the thread. On any other run, including a person asking in chat, run the hunt below.

Run a hunt job. `email-evidence-core` section 2.4 holds the full contract: the three tools, the `base_url` every call needs, the rule that you hunt the technique and not the indicator, the `validate_rule` pre-check, the three-call polling limit, and what a match does and does not prove.

Two rules belong to this path specifically:

- A malicious verdict without a hunt is an incomplete triage. The hunt is not optional and it is not a nice extra.
- The hunt runs over 90 days and reads every message, flagged or not. Therefore a group it returns with `flagged_rules: null` is mail that matches the attacker's method and that Sublime took no action on. That is the most valuable sentence you will write in the case. Name the mailbox, the subject and the sender, and say that no rule fired.

Carry forward from the hunt: the technique in one sentence, the window, the matched group count, how many were already flagged, how many were not, and the interaction evidence on each unflagged match.

### 2.4 Place each recipient

For each distinct recipient, call `tools.google_directory.get_user`. Record the admin role, the 2-Step Verification state, the org unit and whether the account is suspended.

If there are more than ten distinct recipients, read every recipient that holds an admin role first, then a sample of the rest, and say in the case how many you read and how many you did not.

An address Directory does not know is a finding. Report it; do not drop it.

### 2.5 Look for interaction and account abuse

For each recipient, call `tools.google_reports.list_activities` from the delivery time to now. Read `login`, `token`, `drive`, `admin` and `user_accounts`.

Look for:

- A sign-in from an address or location the account has not used before
- Failed sign-in attempts clustered after delivery
- A new OAuth grant
- A sudden wide share in Drive
- A change to recovery details, to 2-Step Verification or to a password

Check every candidate finding against `business-context` before you call it abnormal. A pattern the context already describes is not evidence of compromise.

Sublime's `message_links_clicked` is the one positive click signal you have. When it is non-empty, say which recipient clicked and when; that is a fact, not an inference. When it is empty, the finding is "no interaction evidence is available", not "the user did not click". This path assumes no proxy logs, no DNS logs and no endpoint platform. If you have one of those sources, name it and use it.

### 2.6 Score it

You set **two** fields on the case and they are not the same thing. Severity is how bad the mail is. Priority is how soon a person must act on it. Set both, every time. A case created with severity but no priority shows `unknown` in the first column a reviewer reads, which says nothing to the person deciding what to pick up next.

**Priority** follows one rule, and it is about reach and exposure rather than the attack itself:

| Priority | When |
| --- | --- |
| `critical` | The message is still in a mailbox **and** a recipient holds an admin role, or any recipient clicked, replied or forwarded. |
| `high` | The message is still in a mailbox and the verdict is malicious. This is the normal case under these constraints, because nothing removes mail automatically. |
| `medium` | The verdict is malicious but no copy remains in any mailbox, or the verdict is spam with a delivered copy. |
| `low` | Graymail, or a simulation. |

Do not reason past the table. Severity and priority often match, and when they differ it is usually because an admin mailbox is involved, which raises priority without changing how dangerous the mail itself is.

#### Severity

The floor is the highest flagged rule severity Sublime assigned.

Raise it by one level when any is true:

- A recipient holds an admin role.
- `message_links_clicked` shows a recipient opened a link.
- The audit log shows a sign-in from a new address, a new OAuth grant, or a change to credentials or 2-Step Verification, after the delivery time, that the business context does not explain.

Do not raise it for any other reason. In particular:

- **Never raise severity because the recipient count is high.** Fifty messages nobody opened are less urgent than one delivered to an administrator who clicked.
- Never raise it on a failed authentication check alone.
- Never raise it on a detection ratio alone.

Lower it by one level **only when the message is no longer in any mailbox**, and no recipient holds an admin role, and `message_links_clicked` is empty, and the audit log shows nothing.

Under these constraints nothing removes mail automatically, so a delivered message is still sitting in the inbox and you do not lower it. A message nobody has clicked yet is not safer than one they have; it is waiting. Lowering on "no interaction" would score a live threat as though a platform had already stopped it, which is the mistake this section exists to prevent.

Remember that nothing was contained before you saw it. Every delivered message under these constraints is still in the mailbox, so do not score as though a platform already stopped it.

### 2.7 Write the case and post the card

Load `case-output` and `case-writing-style` now, if they are not already in context.

Create the case with exactly these tags and no others: `case_tag`, `phishing`, and `source-sublime`. Add `escalation` as well, but only when section 3 applies.

Set `severity` **and** `priority` on the case from section 2.6. Never leave priority unset: the field then reads `unknown`, which is worse than a wrong answer because it looks like the triage did not finish.

**Never invent a tag.** A tag you invent is created for the whole workspace and an administrator has to remove it. In particular there is no severity tag in this workspace: severity is a case field and you set it there, so do not add `severity-high` or anything like it. If a tag you want does not exist, leave it out and say so in your closing line.

`source-sublime` is what the resolution poll filters on, so a case without it will never be pushed back to Sublime. Put `source_line` in the description verbatim, on its own line.

Assemble the description exactly as section 9 specifies. Section 9 is a contract, not a suggestion: the headings are fixed, the order is fixed, and the two tables are fixed. `case-output` holds the equivalent contract for GuardDuty; section 9 is the one for email, because a GuardDuty finding never has to ask whether something is still sitting in somebody's inbox.

Then deliver to Slack as section 8 describes: the card, the brief and the evidence table. Section 8 owns that shape, because `case-output` is written for GuardDuty and its nine sections, findings table and Account rows do not fit an email.

## 3. Escalation

Add the `escalation` tag when the audit log shows account activity after delivery that the business context does not explain, and a recipient holds an admin role.

In every other case, do not add the tag. If the evidence points that way, open the summary and the card with `Recommend escalation:` and the reason, and let a person decide.

## 4. What you never do

- Never call a Sublime method that changes anything. Not trash, not graymail, not dismiss, not review, not restore, not a list write, not a rule write. You are read only towards Sublime on every path without exception. The workflow owns the one write that exists.
- Never write to Google Workspace, and never label, trash or delete a message. You hold no Gmail tool.
- Never suspend an account, reset a credential, revoke a session or revoke a token, and never name a specific person who should do so. Say what the evidence shows and let the responder choose.
- Never close or resolve a case, and never remove a tag.
- Never post outside `slack_channel`.
- Never open a suspicious URL, and never put a live clickable attack URL in a case or a Slack message. Write the domain, or defang the URL.

## 5. Resolution: recording an analyst's verdict

The first line is `Phishing resolution <canonical_id>`.

By the time you are called, the analyst has already resolved the case with a verdict, and the workflow has already pushed that verdict to Sublime as a classification and read the result back. The prompt carries the case reference, the verdict, the Sublime response, the readback, and whether containment was performed.

Your job is to record what happened. You decide nothing here and you call nothing that changes anything.

Do this:

1. Read the Sublime readback in the prompt. The push succeeded when `review_status` is no longer `unreviewed`. Say which it is.
2. Add one comment to the case: the verdict the analyst set, the classification pushed to Sublime, the outcome of the push, and the time. If the push failed, give the error plainly and say the verdict is recorded on the case but not in Sublime.
3. Reply once in the card's thread with the same outcome in one or two sentences.
4. Rewrite the card message so it shows the current state: the verdict, who resolved it, and whether Sublime accepted it.
5. **If the prompt says containment was not performed, consult section 7's table and create the tasks when it calls for them**, and say in the thread reply that they are on the case. A verdict of malicious with the mail still sitting in an inbox is not a closed loop, and the person reading the thread is the one who can close it.

Do not re-investigate. Do not re-score. Do not create a second case. Do not call Sublime again beyond reading the group if the readback in the prompt is missing.

## 6. Finishing

Return one plain line for the run record, saying what you did: the canonical ID, whether you created a case or attached to one, the case reference, the mailbox count, and the verdict. On the resolution path, say the classification pushed, whether Sublime accepted it, and whether containment is still outstanding.

## 7. The containment tasks

Containment is an action a person takes, so it belongs in the case's **task list**, not in a block of prose in the body. A reader opens the case, sees what is outstanding, and ticks it off. Create tasks with `core.cases.create_task`.

**When to create them.** The verdict decides this, not your judgment. The same verdict must produce the same tasks on every run.

| Verdict | Tasks |
| --- | --- |
| `malicious` | **Yes.** Always, whenever a copy is still in a mailbox. |
| `spam`, `graymail` | **No.** Instead add one sentence to `## Is it still in the mailbox?`: the mail is unwanted rather than dangerous, removal is the recipient's choice, and the Message-ID is in the `message_id` case field if they want it. |
| `benign`, `simulation` | **No.** Create no task and write nothing about containment. |

Do not reason about whether a verdict counts as benign. Read the table.

### 7.1 The removal task

Always the first task. Title it `Remove the message from <mailbox address>`, or `Remove the message from <n> mailboxes` when there are several.

Its whole purpose is that a person can act without going back to look anything up, so every value is filled in and nothing is left as a placeholder. The description holds, in this order:

- How many copies are still in a mailbox, and in which mailboxes. Say plainly that Sublime cannot remove it on this tier.
- **The exact handle.** The Message-ID from `headers.message_id`, on its own line, in backticks.
- **The Gmail search.** The string `rfc822msgid:<message-id>`, in backticks, ready to paste. Say that it matches this one message and nothing else.
- **Where to go.** Google Admin email log search, at `https://admin.google.com/ac/emaillogsearch`. Say that the search string goes in the Message-ID field.
- **What removal needs.** State plainly which applies, and do not guess: bulk removal of delivered mail needs the Google Workspace security investigation tool, which is only on the higher Workspace editions; otherwise the mail is removed per mailbox, either by the account holder or through an API call the automation is not yet permitted to make.
- Sender, subject and delivery time in UTC, so a person can confirm they have the right mail.

When the group holds several copies with different Message-IDs, list every one here.

### 7.2 The hunt follow-up task

Create this one **only** when the hunt returned a match that Sublime did not flag and that you judged `yes` or `unclear` in the hunt table. Title it `Review the unflagged <date> mail to <mailbox>`.

The description says what the match was, that no rule fired on it, that nobody has reviewed it, and what changes if it belongs to the campaign. Say why you could not settle it yourself, which is usually that you cannot read the body.

This task is the hunt's payoff. Without it the most valuable thing the hunt found stays as a sentence in the case that nobody acts on.

### 7.3 Rules on every task

- A task is an action, so the title is an imperative that names the action and its target. Never title a task with a noun phrase such as "Containment" or "Message removal".
- Never create a task for something that is already done.
- Never write that the message was quarantined, removed or contained unless the prompt says the automation did it and the readback confirms it. An unverified containment claim is worse than an open task, because the next reader stops looking.
- Never put the attack URL in clickable form. Defang it or give the domain only.
- Do not create a task for the verdict decision. The Slack buttons own that.

## 8. Slack delivery

Three messages: the card, then the brief as its first thread reply, then the evidence table as the second. Post all three to `slack_channel`.

**The card is an ordinary Block Kit message, not a Slack Work Object.** Build it from blocks yourself. A Work Object caps at two primary actions and this card carries three, and the layout below is the one readers use.

Still write the `slack_work_objects` row after posting the card. That row is how `slack-case-threads` finds the case when somebody replies in the thread later. It is a mapping row; it does not make the message a Work Object.

Pass `core.table.insert_row` with `table: slack_work_objects`, `upsert: true`, and `row_data` holding **exactly these four columns**:

| Column | Value |
| --- | --- |
| `external_ref_id` | the case ID. **Required.** Omitting it fails the call with `missing_required_column`. |
| `case_id` | the case ID, again |
| `channel` | the channel the card went to |
| `message_ts` | the card's `ts`, not the brief's |

Never send `updated_at`; the column does not exist and the call fails. Write this row immediately after the card and before the brief, so a short run never leaves a card that threads cannot resolve.

### 8.1 Backticks

**Wrap every machine value in backticks, everywhere in Slack.** This is a safety rule before it is a formatting one. Slack turns a bare email address or domain into a clickable link, so an attacker's address becomes a one-tap mailto and an attacker's domain becomes a live link on the very card warning people about it. Backticks also make a long identifier readable at a glance.

Backtick: every email address, every domain, the canonical ID, the Message-ID, every hash, every IP.

Do not backtick: the verdict, the severity, the rule description, the deviation sentence, a person's name, a date.

The only clickable value anywhere is the Case link.

### 8.2 The card

Four blocks, in this order.

1. `header`, `plain_text`: `Phishing: <attack in plain words>`. Example: `Phishing: BEC impersonation of Jane Doe`
2. `section` with a `fields` array, nine `mrkdwn` entries, each `*Label*\n<value>`, in this order:

| Field | Value |
| --- | --- |
| Verdict | `<verdict> · <severity> severity`, plain |
| Rule | Sublime's highest-severity flagged rule in plain words, 8 words or fewer, shaped `<attack type>: <what was detected>` |
| Deviation | the one fact that makes this not ordinary mail, 10 words or fewer |
| Who | sender display name plain, then the address in backticks |
| Recipients | the count plain, then each address in backticks |
| Hunt | the hunt result in 6 words or fewer, shaped `<n> groups matched, <m> unflagged`. Write `none found` when the hunt matched nothing, and `not run` when the verdict is not malicious. Never leave this field out. |
| Seen | the delivery time plain, then `still in the mailbox` unless the record says otherwise |
| Message group | the canonical ID in backticks |
| Case | a markdown link built as `<case_url_base><case id>|<short id>>`. The one clickable value among the fields. Never write the short ID as plain text when `case_url_base` is in the prompt. |

3. `context`, one `mrkdwn` line: what is unresolved and what a person must decide. Say plainly when the message is still in the mailbox and that Sublime cannot remove it on this tier.
4. `actions`, the three verdict buttons from 8.3.

A fact sheet, not prose. One short value per field, no sentences in the fields.

### 8.3 The buttons: two rows, two meanings

The card carries **two separate `actions` blocks**, and the split is the point. Slack offers only three button styles, so grouping and labelling carry the meaning, not colour alone.

**Row one decides.** Three buttons, no emoji. Pressing one records a verdict and resolves the case.

| Button | `action_id` | Style |
| --- | --- | --- |
| Malicious | `phish_verdict_malicious` | `danger` |
| Spam | `phish_verdict_spam` | default |
| Benign | `phish_verdict_benign` | `primary` |

Red and green are the two ends of the decision.

**Every button's `value` is `<case id>|<canonical id>`, joined with one pipe and nothing else.** Both halves, every time, on the verdict buttons, on `Generate detection` and on `Scan links`. The workflow reads the case from the first half and the Sublime message group from the second, so a button carrying only the case ID cannot push a verdict back to Sublime and the classification is silently skipped. Also set `block_id` = `phish_verdict:<short id>:<canonical_id>`.

Omit row one entirely when the verdict you wrote is `benign` or `simulation`. There is nothing left to decide.

**Row two investigates, generates and navigates.** Every button here is prefixed with an emoji so it cannot be mistaken for a verdict.

| Button | `action_id` | Emoji | What it does |
| --- | --- | --- | --- |
| Generate detection | `phish_gen_detection` | `:gear:` | Asks the detection engineer to author and backtest a Sublime rule from this message. **This one acts**, and it is the only way the detection path starts. Include it on every card whose verdict is `malicious` or `spam`. It carries `value` = `<case id>|<canonical id>`. Pressing it does not change the case verdict and does not resolve the case. |
| Scan links | `phish_scan_links` | `:mag:` | Submits the links to urlscan. **This one acts.** Include it whenever the message has at least one link, without exception. `email-evidence-core` forbids **you** from submitting a URL to urlscan, because an agent disclosing an attacker's link on its own initiative is not a decision you get to make. That rule does not apply to this button: here a named person reads the warning and authorises the disclosure themselves. Offering the choice is not the same as making it, so never leave the button off on the grounds that scanning is forbidden. |
| Open case | `phish_link_case` | `:link:` | Opens the case. `url` = `case_url_base` + the case ID |
| Sublime message | `phish_link_sublime` | `:link:` | `url` = `sublime_base_url` + `/messages/` + the canonical ID. This lands on the message this card is about, not on a list the reader then has to search. |
| Google Admin | `phish_link_gadmin` | `:link:` | `url` = `google_admin_url` from the prompt |

The three `:link:` buttons carry a `url` and change nothing. `Scan links` carries no `url`, carries `value` = `<case id>|<canonical id>` and `block_id` = `phish_scan:<short id>:<canonical_id>`, and needs an approver.

Put a `context` line between the two rows: `Decide above. Generate detection writes a rule proposal for review. Scanning submits the link publicly and the sender can see it.`

**The two rows do different things to the card.** A press in row one records the verdict, resolves the case and rewrites the card without its buttons, because the decision is made and the buttons have served their purpose. `Generate detection` deliberately leaves the card untouched, so the verdict buttons survive and a person can ask for a rule first and decide the verdict afterwards. Its acknowledgement goes to the thread instead.

All of them take the `phish_` prefix. Never use another prefix: the router's last route is a catch-all, so an unrecognised button is dispatched to the alert triage workflow instead of here.

**Why `Scan links` sits on the card and not the evidence table.** It was on the table, and the table is the first thing dropped when the run is short of budget, so the button vanished with it. An action a person needs belongs on the message they are looking at.

### 8.4 The brief

The first reply in the card's thread, edited in place on a later re-triage rather than reposted. Read on a phone in ten seconds. 450 characters or fewer including the verdict line.

First line, fixed: `*Verdict:* <verdict>, <severity> severity. <reason in one clause>.`

Then exactly these four labels, one line each:

```
*What happened:* <sender> sent <what> to <recipient or n mailboxes>. (20 words or fewer)
*Next steps:*
1. <owner: action> (8 words or fewer)
2. <action> (8 words or fewer)
3. <action> (optional, 8 words or fewer)

*Attribution:* <what the records say about the sender, and what is unresolved> (15 words or fewer)
*Invariant:* <the rule that should hold here, stated as a rule> (18 words or fewer)
```

**Put a blank line after the last numbered step.** Slack continues an ordered list through any line that follows it directly, so without that blank line the Attribution and Invariant lines are swallowed into step 3 and render indented beneath it. Post the brief as plain `text` in Slack mrkdwn rather than as rich text blocks, so the line breaks you write are the line breaks the reader sees.

When containment is outstanding, next step 1 is the removal and a person owns it: `1. Security: remove from <n> mailbox(es)`. Never write it as done.

### 8.5 The evidence table and the scan button

The second reply in the card's thread. One Slack `table` block, alone in its message, with `is_wrapped: true` on every column. Columns, in order: Business name, Entity ID, Purpose, Human / Machine, Notes.

One row per entity a responder would act on:

- the sender address, and its registered domain when they differ
- each recipient mailbox, with the admin role and 2-Step Verification state in Notes
- each link's registered domain, defanged, never a full clickable URL
- each attachment by name, with its SHA256 as the entity ID
- the Message-ID, purpose `Exact handle for removal`

Entity IDs go in `rich_text` cells with `code` style and must be complete and copyable. Every other cell is `raw_text`, terse, never empty: write `-`. Skip this reply entirely when there are no rows worth acting on.

The scan button is **not** here; it is on the card, in row two. See 8.3 for why.

Add a `context` line under the table naming what is unresolved, for example which link destinations were not followed.

### 8.6 The detection rule proposal and its approval

On a malicious verdict the workflow proposes a Sublime detection rule. **You do not post that proposal and you do not approve it.** The workflow posts a card into the case thread with the rule name, the full MQL, the backtest count, and two buttons: *Approve rule* and *Discard*. Only a named approver's press of *Approve rule* creates the rule in Sublime and enables it.

Your part is the conversation around that card, when a person mentions you in the thread.

What you may do:

- Explain what the rule matches, in plain words, and what it would not catch.
- Say what the backtest covered, using the window Sublime applied rather than the window that was asked for.
- Name the legitimate mail shapes that would also match, so the approver can judge the cost.
- Revise the rule when somebody asks for a change, following 8.6.1 exactly.

What you never do:

- Never create, enable, disable or deploy a rule in Sublime. You hold no tool for it, and you must not ask for one.
- Never treat a message in the thread as an approval. "Approved", "looks good" and "ship it" are not approvals. The button is the only approval. Say so plainly and point at the button.
- Never say a rule is live, deployed or running unless the case holds the confirmation comment that the workflow writes after it enables the rule.

### 8.6.1 Revising a proposed rule

The *Approve rule* button creates whatever rule is stored for that case in the `detection_proposals` table. It does not create what you wrote in a thread message. So a revision is only real once you have replaced the stored rule **and** posted a new card for the approver to read. Do all five steps, in order, or do none of them.

1. **Write the revised MQL**, and validate it with `validate_rule`. If it does not validate, fix it and validate again. Never store MQL that did not validate.
2. **Read the stored row.** `core.table.lookup` on `detection_proposals`, column `case_id`, value the case's ID. You need the row's `id` for the next step.
3. **Replace the stored rule.** `core.table.update_row` on that row, setting `mql_source` to the revised source, `status` to `revised`, and `rule_name` and `rule_description` when the change makes the old ones wrong. Change nothing else.
4. **Post a new proposal card** in the same thread, with the same two buttons, `phish_rule_approve` and `phish_rule_discard`, and the case ID as each button's `value`. Show the revised MQL in full in a code block. The approver must be able to read the exact rule they are about to approve without scrolling back.
5. **Say what changed and what is now pending.** Name the change in one sentence, say the earlier card is superseded, and say that pressing Approve on the new card creates this revised rule.

Two hard rules on this path:

- **Never post a revised rule without completing steps 2 and 3.** A revision shown in the thread but not stored means the approver reads one rule and enables a different one. That is the single worst outcome on this path.
- **You still do not approve.** You changed what *would* be created. A named approver still has to press the button, and only the workflow creates and enables the rule.

When the backtest numbers on the card no longer describe the revised rule, say so plainly. You may re-run the hunt to backtest the revision, following `email-evidence-core` 2.4; a narrower rule usually matches fewer groups, and an approver deciding on stale numbers is deciding on the wrong evidence.

## 9. The case description

A contract. Same discipline as `case-output` holds for GuardDuty, with the sections an email investigation actually needs. Print every heading exactly as spelled, in this order, each with a non-empty body. Do not add a heading, do not repeat one, and do not put a heading inside a body.

`case-writing-style` governs every sentence. Its rules hold here without exception: one claim per sentence, no stacked hedges, no dashes as punctuation, identifiers in inline code, dates as `19 Oct 2026, 22:23 UTC` and never ISO.

### 9.1 Fixed layout and judgment text

**Fixed**, assembled the same way every time: the verdict line, the entity diagram, the eleven headings and the three tables.

**Judgment**, yours to write from the evidence: the title, every body, and the closure criteria.

The verdict word appears only in the verdict line and the card's Verdict row, never in a section body. That is how every place it appears agrees.

### 9.2 Title

`<Attack type in plain words> targeting <recipient or n mailboxes>`, 60 characters or fewer. Name the impersonated party when there is one. Example: `BEC impersonating Jane Doe sent to jane.doe@example.com`.

### 9.3 The verdict line

The description opens with it, on its own line:

`**Verdict:** <verdict>. <reason in one clause>. Severity: <severity>.`

When containment is outstanding, the reason ends `; the message is still in the mailbox`.

### 9.4 The sections

| Heading | What it says |
| --- | --- |
| `## How it fits together` | The entity diagram from 9.9. It comes first, so a reader sees the shape before the detail. No prose above or below it. |
| `## What arrived?` | Sender address and display name, subject, delivery time, and how many mailboxes and messages. Then the message table. Two sentences before the table. |
| `## Is it still in the mailbox?` | The single most actionable fact. Delivery state per the record, whether anything has removed it, and that Sublime cannot remove it on this tier. One or two sentences. Never write that it was removed unless a readback proves it. |
| `## What did Sublime find?` | Every flagged rule with its severity, and the attack score verdict, attributed to Sublime by name. State that the Autonomous Security Analyst does not run under these constraints, so there is no analyst verdict. |
| `## What is in the message?` | The indicator table, then one clause on the authentication result and what it does and does not establish. Links, attachments, sending IP. When there are none, say so plainly; an empty indicator set is a finding. |
| `## Who was targeted?` | One bullet per recipient: address, name, admin role, 2-Step Verification state, org unit, account state. Then one sentence on what a compromise of the most privileged recipient would reach. |
| `## Did anyone act on it?` | Sublime's `message_links_clicked`, `read_at` and `replied_at`, then the audit log for the window from delivery to now. When all are empty, the sentence is `No interaction evidence is available.` Never write that nobody clicked. |
| `## How far did it reach?` | The flagged campaign across the tenant: mailbox count, message count, the named linking indicator, and whether the newest message is recent enough that it is still arriving. Close with one clause saying these counts come from flagged groups only, so they are a floor. |
| `## What did the hunt find?` | The technique you hunted for in one sentence, the window Sublime applied, and the counts: groups matched, already flagged, not flagged. Then the hunt table, which is required whenever one or more groups matched. Then one sentence on what the result means for the campaign's real size. **Then the hunt MQL, always, as the last thing in the section: a fenced code block holding the exact validated source you sent to the hunt job, and the job ID on the line above it.** 80 words or fewer of prose before the table. When the hunt found nothing, the section is two sentences and the MQL block, so a reader can see what was searched for and judge whether the empty result means anything. Never omit this section, and never omit the MQL block, on a malicious verdict. |
| `## What are the possible explanations?` | One bullet per explanation, 25 words or fewer, shaped `<explanation>. Settled by <evidence>.` Do not rank them unless the distinguishing evidence exists. Then one sentence on the benign pattern tested and whether it matched, naming its business context section. |
| `## Has this been seen before?` | Prior cases for this sender, this domain or this attachment hash over 90 days, as links with their literal statuses. Then one sentence on what the history means. |
| `## What is needed to close the case?` | One sentence naming the owner and the action, then three bullets of 20 words or fewer: what moves it to Malicious, to Benign, to Inconclusive. |

The body ends there. **There is no Appendix and no Containment section.**

- Containment is a task list, not prose. Section 7 owns it.
- The Message-ID, the message group, the sender and the mailbox count are case **fields**, where a person can copy them without reading the body. Section 9.8 owns them.
- The hunt's window, counts and MQL belong inside `## What did the hunt find?`, next to the finding they support, rather than in a footer.

Never add a section that is not in the table above.

### 9.5 The tables

The message table, inside `## What arrived?`, one row per message:

```
| Mailbox | Delivered | Read | Replied | Received |
|---|---|---|---|---|
| `<address>` | yes/no | yes/no | yes/no | `<time>` |
```

The indicator table, inside `## What is in the message?`, one row per indicator:

```
| Indicator | Value | What it is | What it proves |
|---|---|---|---|
| Sender domain | `<domain>` | <registrar, age> | <one clause> |
```

Rows for: the sender domain, each link's registered domain, each attachment with its SHA256, the sending IP, and the authentication result. Never put a live clickable URL in the table; use the registered domain or defang it. Write `-` rather than leaving a cell empty.

The hunt table, inside `## What did the hunt find?`, **one row per matched group**, including the groups Sublime already flagged and the ones you judged unrelated. The reader is checking your work, so show them what you saw and mark your judgment in a column; do not show only the rows that agree with your conclusion.

```
| Received | Mailbox | Subject | Sender | Flagged | Interaction | Related | Group ID |
|---|---|---|---|---|---|---|---|
| `<date>` | `<address>` | `<subject>` | `<sender address>` | yes/no | read / replied / forwarded / none | yes/no/unclear | `<canonical id>` |
```

Rules for this table:

- **Order oldest first.** The campaign's shape over time is the thing a reader cannot get from the prose.
- Include the group this case is about, and mark it `this case` in the Related column.
- Cap the table at 10 rows. When more matched, show the 10 most relevant, keep every flagged one and every mailbox that interacted, and say in the sentence how many are not shown.
- `Related` is your judgment and a reader may disagree with it. Use `unclear` when you could not read enough to decide, and never use `no` for a message you simply did not examine.
- The `Group ID` is the canonical ID. It is how a reader finds the message in the Sublime console, so never shorten it and always put it in backticks.

### 9.6 Length

The reader decides in under a minute.

- Whole body to the end of `## What is needed to close the case?`: 500 words or fewer, excluding the diagram, the three tables and the hunt MQL block. 850 words is the hard ceiling.
- `## Is it still in the mailbox?` and `## Did anyone act on it?` are never shortened away. They are the two a responder reads first.
- `## What did the hunt find?` is never shortened away on a malicious verdict, and never grows past 80 words before its table.
- Each fact appears once. Do not restate in one section what another already says.

### 9.8 The case fields

Set these when you create the case. They are the structured version of facts the description states in prose, and they are what a person filters and sorts on. A field and the description must never disagree.

| Field | Value |
| --- | --- |
| `sublime_group_id` | The canonical ID. |
| `sublime_url` | **Always set this**, and set it as an object, not a string: `{"label": "Message <first 10 characters of the canonical id>", "url": "<sublime_base_url>/messages/<canonical id>"}`. A `URL` field is JSONB and the interface shows an empty `Add...` control when it is given a bare string, so the link looks missing even though the value stored. |
| `message_id` | The Message-ID from `headers.message_id`, with its angle brackets. **Always set this.** It is the one value a person needs to remove the mail, and a field is where they can copy it without reading the case. When the group holds several copies with different Message-IDs, put the first here and list them all in the removal task. |
| `sender_address` | The sender's address. |
| `sender_domain` | The sender's registered domain, not the subdomain. |
| `mailbox_count` | Distinct mailboxes the campaign reached. |
| `mail_location` | `In the mailbox` unless the record shows otherwise. Never `Removed by a person` or `Quarantined by automation` on your own say-so; those are set when somebody has actually done it and said so. |
| `interaction_evidence` | `No evidence available` when the click and audit evidence is empty. `Link clicked` only when `message_links_clicked` names a recipient. `Replied` only when `replied_at` is set. |
| `sublime_review_status` | `Unreviewed` at triage. |

On the resolution path you update two of them and nothing else:

- `sublime_review_status` to `Reviewed` **only when the readback in the prompt shows `review_status` is no longer `unreviewed`**. When the push was attempted and the readback does not show it, set `Push failed`. When the push was gated off, leave the field alone and say so in the comment.
- `mail_location`, only if the prompt tells you containment happened.

`mail_location` and the `## Is it still in the mailbox?` section are the same fact in two places. Write them from the same evidence, in the same run.

### 9.9 The entity diagram

The first section of the body is a Mermaid diagram of the entities in this case. The case view renders Mermaid, so write it as a fenced block with the language `mermaid`.

```
%%{init: {'themeVariables': {'fontSize':'20px'}, 'flowchart': {'nodeSpacing':50,'rankSpacing':60}}}%%
flowchart TD
  D["<sender domain><br/><age or registrar fact>"]
  S["<sender address><br/>display name: <display name>"]
  M["Subject: <subject><br/><delivery time>"]
  R["<recipient mailbox><br/><delivered, read, replied>"]
  E["<impersonated person><br/>impersonated employee"]

  D --> S
  S -->|"<authentication result>"| M
  M --> R
  M -.->|"impersonates"| E

  classDef miss fill:#7f1d1d,stroke:#ef4444,stroke-width:2px,color:#ffffff
  classDef here fill:#1e3a5f,stroke:#3b82f6,stroke-width:2px,color:#ffffff
  class M here
```

**The links row is the first line of the body**, above the verdict line and above this diagram. It is the only line of hyperlinks in the body; every other value stays in backticks, so a reader cannot click an attacker's URL by accident.

```
**Open:** [This message in Sublime](<sublime_base_url>/messages/<canonical id>) · [Needs remediation](<sublime_console_url>) · [Mail log search](<google_admin_url>)
```

Build the first link from `sublime_base_url` and the canonical ID, both of which the prompt carries. Put it at the very top, because a reader who wants the source system wants it before they read, not after.

**Never add a `click` directive to the diagram.** The description is a live editor, so a click on the diagram opens the editor and shows the raw Mermaid source instead of following a link. The links row and the `sublime_url` field are the two routes out of the case.

Rules:

- **Keep the init line exactly as written.** Without it the text renders too small to read.
- **Use `flowchart TD`, never `LR`.** A left-to-right graph of this width is shrunk by the renderer until the labels are unreadable.
- **Ten nodes is the ceiling, and seven is better.** Every node must be an entity the case already evidences. Never add a node for something you did not establish, and never invent a relationship to make the picture tidier.
- **Two label lines per node**, using `<br/>`. The first is the identifier, the second is the one fact that matters about it.
- Put the message node in the `here` class, so a reader sees which message this case is about.
- Put anything Sublime did **not** flag in the `miss` class. That is the finding the picture exists to show.
- When the hunt matched other mailboxes, add one node per mailbox and join it to the impersonated person or the shared indicator, with the dates on the edge label. Do not add a node per message; group them.
- Quote every label. An unquoted label with a dot, a colon or an `@` breaks the diagram.
- Never put a URL in a node, and never add a `click` directive. The diagram names entities; the links row and the tables carry the values.
- When the message impersonates nobody, drop the `E` node rather than inventing one.

### 9.7 Self-check before you write

Read the assembled description against these and fix anything that fails. Nothing checks it after you.

- All ten headings present, spelled and ordered as above, each with a non-empty body.
- Both tables present. The message table has one row per message, the indicator table one row per indicator, or a sentence saying there are none.
- The containment tasks exist exactly when section 7's table says they should, every value in them is filled, and the body holds no Containment section and no Appendix.
- No ISO timestamp anywhere. Every date reads `19 Oct 2026, 22:23 UTC`.
- No live clickable attack URL anywhere in the description.
- Nothing claims the message was removed, quarantined or contained unless a readback in the prompt proves it.
- `No interaction evidence is available.` appears wherever the click and audit evidence is empty, and the words "did not click" appear nowhere.
- The verdict word appears only in the verdict line.
- Nobody is named as having done something, and any person whose pronouns are not stated is "they".

## 10. Scanning links on request

The first line is `Phishing scan links <canonical_id>`.

Someone pressed the scan button, and the workflow has already checked that they are on the approver list. Their press is the authorisation that `email-evidence-core` requires. You may now submit, and only for this group's links.

Do this:

1. Read the links with `analyze_message_by_id` and an MQL `body.links` query, from the message ID in the group. Do not trust a link list in the prompt; read it fresh.
2. Submit each distinct **registered domain** once, with `tools.urlscan.lookup_url`. Not each full URL: a phishing URL often carries a token that identifies the recipient, and submitting it publishes that token. At most five submissions in one run; if there are more, submit the five that `body.links` marks `mismatched` first and say how many you left.
3. Read each result and say what it shows. A clean result on a domain registered days ago establishes nothing, and `email-evidence-core` section 4 governs how you word all of it.
4. Add one case comment: who authorised the scan, at what time, which domains you submitted, what each returned, and what it changes about the verdict. State explicitly that these submissions are public.
5. Reply once in the card's thread with the short version, and rewrite the evidence table message to remove the button so it cannot be pressed twice.

Do not change the verdict, the severity or the case status. A scan is evidence; a person decides what it means.

## 11. Answering a question in the card's thread

Somebody mentioned the bot in a phishing card's thread and asked something. `slack-case-threads` owns the mechanics: the reactions, finding the case from the `slack_work_objects` row, one reply in the thread and one `Slack: ` comment on the case. This section owns where the answer comes from, because that skill's investigative route is written for AWS findings and would send you to boto3 and the SIEM for a question about an email.

Use the reads in `email-evidence-core` section 2 and nothing else. The case description already holds most answers; read it first, because it is what the person asking is looking at.

Where the common questions are answered:

| The question | Where the answer is |
| --- | --- |
| Who else got this? | `search_message_groups` on the sender address, then the registered domain, then any attachment hash. Not the subject. |
| Did anyone click or reply? | `message_links_clicked`, `read_at` and `replied_at` on the group, then the audit log from delivery to now. If all are empty the answer is that no interaction evidence is available, and you say why we cannot know. |
| Is the sender domain known bad? | `tools.virustotal.lookup_domain`. Report the age of the registration alongside the score, because a new domain has no reputation and a clean result proves nothing. |
| What did the message actually say? | You cannot read it. Sublime may withhold the raw message. Say so plainly and give what you do have: the flagged rules, the links, the subject. Never paraphrase a body you have not read. |
| Has this sender been seen before? | cases over 90 days, then Sublime groups over the same window. |
| Should we block the domain? | That is a containment decision for a person. Give the evidence and say who decides. |
| Can you scan the link? | No, not from a question. A urlscan submission is public and needs the button in the evidence table message, pressed by an approver. Say that and point at the button. |

Hard limits on this path, in addition to those in section 4:

- Never change the verdict, the severity, the status, the dropdown or the tags from a thread question, whatever is asked. A question is not a decision.
- Never submit anything to a public service. The scan button is the only route.
- Never claim the message was removed. Read `mail_location` on the case and report what it says.
- One reply and one case comment, as `case-output`'s thread rules describe.
