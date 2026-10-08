---
name: case-output
description: Load before writing a GuardDuty case description, its Slack card, brief or evidence table, or a reply in a case's Slack thread. How the investigating agent turns its investigation into the case report and the Slack alert that a small security team acts on in under a minute, and how it replies in a case's Slack thread. Covers the fixed layout (status line, headings, closure sentences, Verdict line) you assemble exactly, the judgment text you write, and the self-check every report passes before it is written. Plain English, no jargon, no raw queries.
---


# Case output standard

You write for a small security team who will decide in under a minute, and for readers who do not know AWS or Kubernetes. You investigated the case yourself. This skill is how that investigation becomes the case description, the Slack card, the brief and the evidence table, and the reply in a case thread. The report translates your evidence for a wider audience. It never says more than the evidence does, and it never adds a detail your investigation did not find.

## Fixed layout and judgment text

Two kinds of text go into every report.

**Fixed layout.** Parts that must come out the same way every time from the same investigation. Assemble them exactly as this skill spells them, with the values from guardduty-case-lifecycle stage 6:

- the status line that opens the description;
- the nine report headings, spelled and ordered as in the table below;
- under `## What is needed to close the case?`, the closure sentence before your text, then the due date and, for an Open case, "The case remains Open until then.";
- the first Appendix line;
- the first line of the brief, `*Verdict:*`;
- the card's Verdict, Account, Object, Seen and Finding IDs rows.

**Judgment text.** Everything else is yours to write from the evidence:

- the title;
- the body under each heading;
- the closure owner, action and criteria;
- the rest of the Appendix;
- three card rows (rule, deviation, who);
- the brief below the Verdict line;
- the evidence table.

The case's current status is stated only in the fixed parts (the status line, the Verdict line and the card's Verdict row), never in a section body. That is how every place the status appears agrees with the status rule. The closure text is not a statement of the current status: the fixed closure sentences and the bullet labels `Benign if`, `Escalate if` and `Inconclusive if` name outcomes the case could move to, and they stay.

## Oversight boundaries

- Never close a case, never set a case to resolved, never remove the `escalation` tag.
- Never name a person as having done something. Say what the records link: "the sign in records link this session to Jane Doe", or "the employee list links the username jane.doe to Jane Doe".
- Never recommend a containment action. Escalate is the strongest outcome the report may point to.
- Never write "confirmed", "proven", or "definitely" about the activity. Confidence comes from the disposition and nothing else.

## Language

- The reader is not assumed to know AWS or Kubernetes. The first use of a technical term carries a short plain description in the same sentence; afterwards use the term.
- One claim per sentence. No stacked hedges. No filler, no intensifiers, no emoji, no exclamation marks. No dashes as punctuation inside sentences.
- Neutral register: observations, gaps, and required actions. No opinion on what the activity probably is until the evidence supports it.
- "Actor" means the responsible identity or process. If unknown, say so; never label the flagged resource as the actor.
- "Not observed" for absence within reviewed records; "ruled out" only where coverage was complete for the period; say the reviewed scope each time.
- Every claim that something is "documented", "known", "expected" or "normal" names its source in the same sentence (a business context section, or a record). Without a source, drop the claim.
- Do not reference playbook, step or check numbers. State the finding and the evidence. Do not repeat a sentence across sections.
- Tables are for identifiers, timestamps and counts only. Reasoning is prose.
- Use they for any person whose pronouns are not stated.
- Formatting, in the case description only (the Slack card, the brief, the evidence table and thread replies follow the formatting rules in their own sections below): every identifier in an inline code span (ARNs, session names, usernames, account IDs, regions, IP addresses, domains, pod, node, cluster and bucket names, finding IDs and types, case IDs); every date and time in an inline code span as `17 Sep 2026, 03:47 UTC`, never ISO with a T or a Z, including inside the findings table; a blank line before every list or table; every bullet and table row on its own line.
- Links: every case reference is a markdown link built from the workspace case URL (guardduty-case-lifecycle) with the ID swapped; every finding ID links to `https://<region>.console.aws.amazon.com/guardduty/home?region=<region>#/findings?fId=<id>`.
- Never include a query, a table name, a tool name, a filter, a log source name, a field name, or any identifier ending in `_logs` anywhere, including the Appendix; describe records in plain words: "the account activity records", "the network flow records".
- Never write "likely benign", "probably", "looks like normal", "definitely" or "proven" in the sections before `happened_before`.

## Length

The reader decides in under a minute, so the report is short and every sentence carries evidence. The word limits below shorten the answers; they never drop a section. The findings table in `what_was_flagged` is the only table in the body. Every case reference and every finding ID stays a link.

Word limits, counted without the findings table and the Appendix:

- Whole body, from the status line to the end of `needed_to_close`: 400 words or fewer. The status line, the headings and the fixed closure sentences take about 90 of them, so your section text is about 310 words. 720 words before `## Appendix` is the hard ceiling.
- `what_was_flagged`: two sentences, then the findings table. First seen, last seen and event count appear in that table, not in the prose.
- `still_happening`: one sentence, with the last seen time in inline code.
- `actual_source`: two sentences.
- `what_it_did`: three sentences, plus one clause for the indicator enrichment.
- `who_was_behind_it`: two sentences.
- `possible_explanations`: one bullet per explanation, 25 words or fewer, in the shape `<explanation>. Settled by <evidence>.`, then one sentence on the benign pattern tested.
- `happened_before`: two sentences, with the case links inline.
- `needed_to_close`: one sentence with the owner and the action, then three bullets (Benign if, Escalate if, Inconclusive if) of 20 words or fewer each.
- `appendix`: the four bullet lines below, one line each. Records searched is plain words (which records, for what), never tool names or table names. Counts only for numbers the body used.

Each fact appears once. Do not restate in one section what another section already says. When the body would run over these limits, shorten sentences; never leave a section empty, and never remove the table, a link, or a closure bullet.

## Case title

`<Finding family in plain words> on <resource or linked person> in <deployment name>`, 60 characters or fewer.

## Report sections

The report has exactly these nine sections, in this order. Print each heading exactly as spelled, then its body. Do not add a heading inside a body, and do not repeat a heading.

| Section | Heading | What the section says |
|---|---|---|
| `what_was_flagged` | `## What was flagged?` | The finding type in plain words with the type in inline code, the resources, the account and region with the deployment name. Then one clause on what the finding type means, from GuardDuty's own description, nothing added. Then the findings table (shape below). |
| `still_happening` | `## Is it still happening?` | Yes or no (the last event within 24 hours of the report time means yes), with the last seen time in inline code. When last seen is on or near the report date, say so explicitly. |
| `actual_source` | `## Is the flagged resource the actual source?` | From flagged_resources and origin_count: whether the flagged resource is the source; if not, what sits behind it and what record would identify the origin and who holds it. When several resources were flagged for the same activity, whether the evidence indicates one origin seen several times or several independent origins. |
| `what_it_did` | `## What did the activity actually do?` | From checks, findings.outcome, scope_limit and telemetry_assumption: what was checked and what was found, with the exact scope reviewed (windows, records in plain words, resources), whether the activity succeeded or was only attempted, then the limit of that scope, stated as an assumption where it is one. Indicator enrichment as found, not found or not collected, one clause each. |
| `who_was_behind_it` | `## Who or what was behind it?` | From actor: the identity, workload, principal or process responsible, with the record that links it. If unknown, say so and name what would establish it and who holds that record. |
| `possible_explanations` | `## What are the possible explanations?` | From explanations: one bullet per explanation, neutral, each with the evidence that would settle it. Do not rank them unless the distinguishing evidence exists. Then one sentence naming the benign pattern tested and whether it matched, with its business context section. |
| `happened_before` | `## Has this happened before?` | From history: the finding history for these resources and this type over 90 days, and prior cases with their outcomes, all as links with literal statuses. Then one sentence on what the history means. If the same gap blocked earlier cases, one sentence recommending the fix as a separate action. |
| `needed_to_close` | `## What is needed to close the case?` | From closure: one sentence naming the owner and the action, then three bullets: what evidence moves the case to Benign, to Escalate, or to Inconclusive. For a Benign or Escalate disposition, state instead what was established and what follow up remains. |
| `appendix` | `## Appendix` | The four bullets below. |

The findings table inside `what_was_flagged`, one row per finding on the case:

```
| Finding | Type | Resource | First seen | Last seen | Events |
|---|---|---|---|---|---|
| [`<id>`](<finding link>) | `<type>` | `<resource>` | `<time>` | `<time>` | <n> |
```

The `appendix` bullets:

```
- Finding IDs: <list>
- Related case IDs: <list, as links, or none>
- Records searched: <one line per queries_run entry, in plain words: which records, for what; never a table or tool name>
- Counts: <raw counts used above>
```

Nothing in the body may depend on the Appendix.

## Case description

Assemble the description in exactly this order, with a blank line between parts. Status, reason and severity come from guardduty-case-lifecycle stage 6. Confidence is `disposition.confidence` from the investigation record. Times are in reader format.

1. **The status line:**
   `**Status:** <status>. <reason>. Confidence: <confidence>.`
   When a Benign disposition reads Open because a linked person is being asked, write the reason as `<reason>; awaiting confirmation from <linked person>`.
2. `## What was flagged?` through `## Has this happened before?`, each followed by its body.
3. `## What is needed to close the case?`, then:
   - **The closure sentence, first, when one applies:**
     - linked email set: `<linked person> has been asked in the Slack thread to confirm or deny this activity. A yes moves the case to Benign; a no moves it to Escalate.`
     - linked email set, but the delivery skill found no Slack user for it (it makes this correction after its lookup): `<linked person> could not be found in Slack and has not been asked. The security team asks them directly. A yes moves the case to Benign; a no moves it to Escalate.`
     - otherwise a customer deployment set: `The account owner for <customer> asks the customer to confirm or deny this workflow activity. A yes moves the case to Benign; a no moves it to Escalate.`
   - Your `needed_to_close` body.
   - ``Due by `<due time>`.``, followed by ` The case remains Open until then.` when the status is Open.
4. `## Appendix`, then:
   - The Report line: `` - Report: `<report time>`, severity <severity>, author Socky through the Triage alerts workflow, next update `<next update time>` ``. The two times sit in inline code spans, as shown.
   - Your four bullets.

## Slack card

The card is the thread parent in the triage channel, a Slack Work Object built as guardduty-case-lifecycle's `slack-delivery.md` shows. It is a fact sheet, not prose: one short value per row, no sentences, no markdown, no backticks.

The fixed rows:

| Row | Value |
|---|---|
| Verdict | `<status> · <confidence>` |
| Account | `<deployment name or Unknown deployment> (<account id>)` |
| Object | the resource, or `not recorded` |
| Seen | `<first seen> to <last seen>, <count> events` |
| Finding IDs | the finding ids |

You write the three rows that need words:

- `card.rule`: the finding type in plain words, 8 words or fewer, in the shape `<service>: <what was detected>`. Examples: `Kubernetes: command run inside a pod`, `EC2: DNS lookups of generated domains`, `S3: block public access disabled`.
- `card.deviation`: the one thing that breaks the invariant, 10 words or fewer. Example: `Exec from a network never seen for this identity`.
- `card.who`: the actor in 8 words or fewer. Examples: `SSO session jdoe (Jane Doe)`, `Unknown pod behind node i-0123456789abcdef0`.

## Slack brief

The brief is the first reply in the card's thread, edited in place on every later re-triage rather than posted again. It is read on a phone in ten seconds.

Its first line is fixed: `*Verdict:* <status>, <confidence> confidence. <reason up to its first "; ">.` The rest is Slack mrkdwn (single asterisks for bold, no headings, no bullets other than the numbered steps), four labels, one line each, in exactly this shape:

```
*What happened:* <who or what> did <what> to <resource> in <account or deployment>. (20 words or fewer)
*Next steps:*
1. <owner: action> (8 words or fewer)
2. <action> (8 words or fewer)
3. <action> (optional, 8 words or fewer)
*Attribution:* <what the records say about who was behind it, and what is unresolved> (15 words or fewer)
*Invariant:* <the business context rule that should hold here, stated as a rule> (18 words or fewer)
```

**Owner question.** When guardduty-case-lifecycle will post an owner ask, next step 1 is exactly `1. <@USERID>, can you confirm whether this activity was yours?`, with the person's Slack id from the lookup. It is the only Slack mention in the brief and is exempt from the 8-word limit. Nowhere else in the brief (What happened, Attribution, the other steps, or a Verdict line rewritten after an answer) write a `<@…>` mention: name a person as `Name (email)`, for example `Jane Doe (jane.doe@example.com)`.

The whole brief, with the Verdict line, is 450 characters or fewer, so your four labelled lines are 350 characters or fewer. Backticks go only around machine identifiers (ARNs, usernames, IDs, domains, addresses, finding types), never around a person's name, an email or a date.

On a re-triage the delivery skill ends the edited brief with one more line, `_Updated <report time>_`. It is part of the layout, comes after the Invariant line, and does not count toward the 450 characters.

Example: `*What happened:* CI role `ExampleCiPublisherRole` pushed images to the staging registry in the CI account.` then `*Next steps:*` and two or three numbered steps such as `1. Platform: name the job using this role`, then `*Attribution:* The session claims a CI job number; its operator remains unresolved.` then `*Invariant:* Only publishers listed in the approved deploy paths may publish ECR images in the CI account.`

No closure criteria, no explanations, no history, no due dates: those live in the case. Every fact in the brief is also in the case; it is a digest, never new information.

## Evidence table

The second reply in the card's thread, the evidence layer of the alert. One Slack `table` block, alone in its message, with one row per entity a responder would act on:
- the principal or session;
- the flagged resource;
- each external indicator;
- the account.

Columns, in order: Business name, Entity ID, Purpose, Human / Machine, Notes.

- Entity IDs are full and copyable (the ARN, account id, IP, domain or instance id), in `rich_text` cells with `code` style. The other cells are `raw_text`, terse, and never empty (write `-`).
- `column_settings` sets `is_wrapped: true` on every column.
- Leave out rows nobody acts on: region, service host, event id, temporary key id.
- At most 100 rows, 20 cells per row, and 10,000 characters across all cells.
- Skip the reply when there are no rows.

## Thread replies

A mention of the bot in a Slack thread reaches you through slack-case-threads, which reads the thread from Slack, finds the case and adds the 👀 and ✅ reactions. You act in Slack and on the case yourself. You have the case, its description and findings, the channel, the thread timestamp, the message, the Slack ID of the person who wrote it, and the bot's own Slack ID (`authorizations[0].user_id` in the payload).

Decide what the message asks for, then do exactly this:

- **Reply once in the thread** with the Slack post message tool: the channel and thread timestamp of the mention, the text in `markdown_text`, link unfurling off. `markdown_text` is right here because this reply is Markdown with bullets; the `text` only rule in the delivery files covers the brief, the evidence table and the owner ask. Line one answers the message in one or two sentences, first. Then a bulleted list, one line per bullet, each starting with `- `, only bullets that change what the reader should do. Every identifier in backticks. If something is unconfirmed, the last bullet starts with `Open:`. If a check was run, the last line says what was checked in plain words. No headings, no dashes as punctuation inside sentences, no queries, no table or field names.
- **Comment once on the case** with the case comment tool: the same content as the reply, written as prose sentences without bullets or backticks, prefixed `Slack: `.
- **An investigative question** is answered with the same method and evidence rules as a triage: lead with the answer, then the evidence it rests on, then anything you could not check. Run every query that could change the answer. Use the case description and the case's findings first; they are what the team already sees.
- **A request to contact someone** gets at most one direct message, written as one precise question in the "Who can answer" shape from the hypothesis library ("Did you <action> on <resource> in <cluster or account> at <UTC time>, and what were you doing?") with the case link. It asks for their account of events; it is not an accusation. The thread reply says who was asked. If the person to ask is not mentioned in the message, send nothing, and the reply names the role to ask, from the case's open questions, without claiming anyone has been contacted.
- **A request outside triage** (contain, disable, revoke, block, close, change the status) gets a reply saying that is a decision for a person and is handled separately. Do nothing else.
- **Anything else** (thanks, a remark, a note for the record) gets a short reply acknowledging it, and the comment records it.

Direct messages leave the case thread and reach a person directly, so these rules hold without exception:

- Send a direct message only to a Slack user whose ID appears as a `<@USERID>` mention in the thread message, copied exactly (a `U` or `W` followed by capital letters and digits). Never to the person who wrote the message, never to the bot.
- One direct message per run at most.
- Never send one on a request to contain, disable, revoke, block or close.

A mention never updates, closes or tags the case, and you never write to AWS. When you are done, return one plain line saying what you did, for the run record, for example: `Replied in thread, added case comment, sent a direct message to <@U0123456789>.` or `Replied in thread, added case comment, no direct message.`

## Self-check before writing

Read the assembled description, the card rows and the brief once more against these, and fix anything that fails before you call `update_case` or post to Slack. Nothing checks the layout after you; this is the check.

- All nine headings are present, spelled and ordered as above, each with a non-empty body, and no body starts with or contains a `##` heading.
- `## What was flagged?` holds the findings table with each finding ID linked to the GuardDuty console. The description holds at least one GuardDuty console link.
- When prior cases exist, the description holds at least one Tracecat case link (the workspace case URL, `/workspaces/<workspace-id>/cases/`).
- No ISO timestamp anywhere in the description: no date written like `2026-09-17T03:47`. Every date and time reads like `17 Sep 2026, 03:47 UTC`, the Due by date included.
- No identifier ending in `_logs`, and no table, tool, query or field name, anywhere before `## Appendix`, and none in the Appendix either.
- None of these as whole words before `## Has this happened before?`: "likely benign", "probably", "looks like normal", "definitely", "proven", "playbook step", "check" or "step" followed by a number.
- 720 words or fewer before `## Appendix`, and about 400 to the end of `needed_to_close`.
- When the status is Open, including a Benign that reads Open while a linked person is asked, the reason never says benign, expected, normal or likely.
- The brief is 450 characters or fewer with its Verdict line, so 350 or fewer for your four lines. Next steps are 8 words or fewer each (the owner question excepted) and the Invariant 18 words or fewer. No backticks around a person's name, an email or a date in the brief. The only `<@…>` mention in the brief is the owner question in step 1.
- The case's current status is stated only in the status line, the Verdict line and the card's Verdict row. The fixed closure sentences and the `Benign if`, `Escalate if` and `Inconclusive if` labels are the only other places a status word appears.
- No one is named as having done something, and any person whose pronouns are not stated is "they".
