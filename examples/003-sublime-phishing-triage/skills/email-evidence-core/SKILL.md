---
name: email-evidence-core
description: "Load before the first read of any phishing or suspicious email investigation, whether you are triaging a Sublime message group or answering a question about an email in chat. Operating rules for email evidence: which Sublime and Google Workspace reads are permitted, how a mailbox resolves to a person, what each indicator does and does not prove, how to hunt a technique across every mailbox with a Sublime hunt job, and the investigation record you build before you write anything. Load with sublime-phishing-case-lifecycle, business-context and case-output."
---

# Email evidence core

Operating rules for the agent that investigates a suspicious email. This skill says where email evidence comes from, what it proves, and how it is worded. It does not say what a case looks like; `case-output` owns that. It does not say what the triage path is; `sublime-phishing-case-lifecycle` owns that.

## 1. The division of work

Sublime is the email security platform. It already analysed the message before you saw it. It parsed the headers, checked SPF, DKIM and DMARC, read the body, extracted and analysed the URLs, examined the attachments, and scored the message with its attack model.

Do not repeat that work. Read its result.

**This tenant runs the free Sublime tier.** Two consequences hold on every run:

- The Autonomous Security Analyst does not run here. `asa_verdict` and `asa_dlp_verdict` are always null on a message group, and the report endpoints are not available to you. Never wait for an analyst verdict and never describe one. Sublime's own analysis reaches you as the flagged rules and the attack score verdict.
- Sublime cannot quarantine, release from quarantine, or apply a warning banner. Containment happens in Google Workspace, and a person decides it.

Your work is the part Sublime cannot see:

- How far the attack reached inside this tenant.
- Who the recipient is, and how much a compromise of that person would cost.
- Whether the recipient acted on the message, and whether the account then behaved abnormally.
- What the evidence supports, written for a person who must decide.

## 2. Permitted reads

### 2.1 Sublime, read only

| Tool | Use it for |
| --- | --- |
| `tools.sublime.get_message_group` | The full record of one message group. Call it with the canonical ID even when a prompt already holds the group, so you work from the current record. |
| `tools.sublime.analyze_message_by_id` | The message's own parsed fields, selected by MQL query. This is your main indicator source. See 2.1.1. |
| `tools.sublime.get_message_data_model` | The whole parsed message in one call. Use it only when you need many fields at once; prefer the MQL route, which returns what you asked for and nothing else. Pass `remove_large_text_fields: true`. |
| `tools.sublime.list_message_groups` | Groups in a time window, or groups matching one indicator. |
| `tools.sublime.search_message_groups` | The same, with query parameters, for campaign scope. |
| `tools.sublime.get_message_group_siem_summary` | Attachment hashes, link clicks and source IPs across flagged groups. It cannot be scoped to one canonical ID and it returns tens of thousands of characters, so prefer MQL for one message and use this only for a tenant-wide indicator sweep. |
| `tools.sublime.get_message` | The group membership and delivery record of one message. It carries no body, headers, links or attachments. |
| `tools.sublime.list_user_reports` | What people reported, and who reported it. Bound it with `reported_at_gte`. |
| `tools.sublime.get_rule` | What a flagged rule actually detects, when its name is not enough. |
| `tools.sublime.analyze_url` | Sublime's link analysis for one URL. |

Two endpoints are often unavailable, and they are not in your tool list. Do not ask for them and do not plan a step around them:

- `get_message_eml`, the raw message, returns `403 Message access justification required`.
- `retrieve_asa_report` and `retrieve_asa_verdict` return 403 and 404, because the Autonomous Security Analyst is an Enterprise feature.

The parsed message is **not** gated. The justification gate is on the raw artefact only, so the data you need reaches you through 2.1.1.

### 2.1.1 Reading indicators with MQL

`analyze_message_by_id` takes a Sublime **message** ID, not a group ID, and a payload holding a `queries` list. Each query names one MQL source. Ask for several in one call.

```yaml
id: <sublime message id>
payload:
  queries:
    - source: headers.message_id
    - source: sender
    - source: recipients
    - source: subject
    - source: body.links
    - source: attachments
    - source: headers.auth_summary
    - source: headers.hops
```

What each one gives you:

- `headers.message_id` — the original Message-ID, such as `<GjdTFq4uSCGPS5uMnDacpg@geopod-ismtpd-3>`. **Always read this.** It is the only exact handle on the message in Google Workspace, and a person needs it to find and quarantine the mail. Put it in the case.
- `sender` — the address split into local part, domain, root domain, subdomain and TLD, with the display name.
- `body.links` — every link, each with `display_url`, `href_url`, `display_text`, `visible`, and Sublime's own `mismatched` flag. A `mismatched: true` link is the display text disagreeing with the destination. Report that flag; it is Sublime's finding, not yours.
- `attachments` — the files, with their hashes and identified types. A null result means the message had none, which is a finding, not a failure.
- `headers.auth_summary` — DMARC and SPF with a verdict, a policy and the sending IP. Read section 4 before you report it.

MQL has no `[]` projection. `body.links[].href_url.domain.root_domain` fails to parse. Select the collection and read the fields you want from the result.

### 2.2 Google Workspace, read only

| Tool | Use it for |
| --- | --- |
| `tools.google_directory.get_user` | The recipient's account state: suspended or active, admin role, org unit, 2-Step Verification. |
| `tools.google_directory.list_tokens`, `get_token` | The OAuth apps that person has granted, and their scopes. |
| `tools.google_reports.list_activities` | The audit log. |

Audit log applications, and the question each one answers:

- `login` — did this account sign in from an address or a place it has not used before, and were there failed attempts?
- `token` — did this account grant a new OAuth app after the message arrived?
- `drive` — did this account share something unusually widely after the message arrived?
- `admin` — did anything change in the account's configuration, its recovery details or its 2-Step Verification?
- `user_accounts` — did the person change their own password or recovery options?

Always bound a `list_activities` call by time. The window starts at the message delivery time and ends now. A wider window returns noise you will then have to explain.

You hold no Gmail tool. You cannot read a mailbox, and you cannot label, trash or quarantine a message. Those belong to the workflow and to the person who decides.

### 2.3 Enrichment

VirusTotal, AbuseIPDB and urlscan are available for a URL, a domain, an address or a file hash. Before you call one, check whether Sublime already answered the same question. Sublime's link analysis usually makes a public lookup unnecessary.

**Two enrichment calls are not optional on a malicious or suspicious verdict.** Make both every time, and put both results in the indicator table:

1. `tools.virustotal.lookup_domain` on the sender's registered domain. Read the detection count **and** the registration date, because a domain registered hours before delivery is the finding, and a zero detection count on a new domain proves nothing.
2. `tools.abuseipdb.lookup_ip_address` on the sending IP from `headers.auth_summary`. A message with no links and no attachments still has a sending IP, and on such a message it is often the only infrastructure indicator you hold. Writing "not enriched" in the indicator table is a gap you chose, not a limit of the tenant.

When either call fails, say which one failed and what you therefore could not establish. Never leave an indicator row saying the value was not looked up when you held the tool to look it up.

Never submit a file or a full URL with a query string to a public service. A phishing URL often carries a token that identifies the recipient, and an attachment can hold company data. Submit a domain or a hash.

**Never call `tools.urlscan.lookup_url` on a live phishing investigation.** It submits a new public scan. That is a disclosure, not a lookup: the operator of a targeted attack can see that their link was scanned, the result may be public, and each submission makes the run wait while the scan completes. You hold no passive urlscan search tool, so there is no safe urlscan route. Use these in this order instead:

1. `tools.sublime.analyze_url` — Sublime's own link analysis. Private to this tenant, and it has already seen the message.
2. `tools.virustotal.lookup_domain` on the link's registered domain — a read of what VirusTotal already holds, not a submission.

A link-tracking or shortener domain tells you about the sending platform, not the destination. Report it as such and do not follow it.

When those leave the question open, say so in the case and name a urlscan submission as the next step a person can authorise. Never submit one yourself.

**This rule binds you, not the person.** It forbids you from making the disclosure on your own initiative. It does not forbid the disclosure from happening. When a workflow offers a person a button that submits the links, that button is the sanctioned route: a named approver reads the warning and takes the decision. Never withhold or omit such a control on the grounds that scanning is forbidden, and never argue a person out of using it. Your job is to make the cost of the choice clear, which is that the submission is public and the sender can see it, and then let them choose.

### 2.4 Hunting across every mailbox

Everything in 2.1 reads mail that Sublime **flagged**. A hunt job reads **all** mail in the window, flagged or not. That difference is the point. A campaign that changed one word to get past a rule does not appear in `search_message_groups`, and it does appear in a hunt.

Run a hunt on every malicious verdict. This is required work, not an extra.

| Tool | Use it for |
| --- | --- |
| `tools.sublime.start_hunt_job` | Start the hunt. It returns a job `id`. |
| `tools.sublime.get_hunt_job` | The job `status`, with `results_truncated` and `error`. |
| `tools.sublime.get_hunt_job_results` | The findings: `message_groups` and `total_group_count`. |

Set `base_url` to `https://platform.sublime.security` on each of these three calls. If you omit it, the call fails with `UnsupportedProtocol: Request URL is missing an 'http://' or 'https://' protocol`. That message does not name the cause, so do not try to diagnose it. Add the `base_url` and call again.

#### 2.4.1 Hunt the technique, not the indicator

This single rule decides whether the hunt finds anything new.

An **indicator** hunt repeats work you have already done. The sender address, the exact subject and the link domain all point at messages Sublime has already grouped for you. Such a hunt returns the campaign you can already see.

A **technique** hunt describes the attacker's method. It finds the messages that use the same method with different indicators, which is the part nobody has seen yet.

Work from what made the message malicious, then write MQL for that behaviour:

| What made it malicious | Hunt for |
| --- | --- |
| The display name impersonates an employee | Inbound mail whose display name holds an employee name, from a sender domain that is not ours |
| The sender domain is new and looks like ours | Inbound mail from a domain that resembles our domain by a character pattern |
| A credential page behind a link | Inbound mail that carries a link to a login or verification path |
| An invoice or payment change request | Inbound mail whose subject holds payment language, from a sender outside our domains |

#### Hunt the variants, not only the form you saw

This is the single most common way a hunt comes back empty when a gap exists. You saw one message, so you write MQL for exactly that message's shape, and you find only the messages that already looked identical. Those are the ones the platform had already flagged, so the hunt tells you nothing new and you wrongly conclude there is no gap.

An attacker varies the lure. Your MQL must allow for the variation:

| You observed | Also test |
| --- | --- |
| The subject is exactly an employee's full name | The first name alone, the surname alone, and the name followed by a few words |
| The display name impersonates a person | The same name with different spacing, punctuation or a title |
| A lure phrase such as "reach you via text" | The same intent in other words: call, message, WhatsApp, a different number |
| A sender domain registered days ago | Any sender domain outside yours with no prior history |
| The subject is an employee's full name, such as `Jane Doe` | **The first name on its own**, such as `Jane`. This one matters: a platform rule that tests the full display name will not fire on the bare first name, so that variant is where the gap usually is. |

A useful test before you start the job: **would this MQL match the message in front of me if the attacker had changed one word?** When the answer is no, the hunt is too literal and will tell you only what you already know.

Prefer `strings.icontains` and `regex.icontains` over an equality test on a whole field. An anchored regex over the entire subject, such as one that requires the subject to be nothing but the exact name, is the narrowest thing you can write and is almost always a mistake in a hunt.

Widening costs you only the time to judge extra rows, and those rows are the finding. Narrowing costs you the campaign.

Useful constructs: `type.inbound`, `strings.icontains`, `strings.iends_with`, `regex.icontains`, `any(<collection>, <predicate>)`, `sender.email.domain.root_domain`, `sender.display_name`, `subject.subject`, `body.links`, `length()`.

MQL has no `[]` projection, in a hunt source as much as in a query. Select the collection and use `any()`.

#### 2.4.2 Validate before you hunt

Pass your hunt source to `tools.sublime.validate_rule` first. A hunt job is slow and a parse error wastes the whole job. Validation is one cheap call that tells you the grammar holds. It does not tell you the logic is right, so read 2.4.1 again rather than trusting a pass.

If validation fails, fix the source. Never send MQL to a hunt job that did not validate.

#### 2.4.3 Starting the job

```yaml
base_url: https://platform.sublime.security
payload:
  source: <the validated MQL>
  range_start_time: <ISO 8601 UTC>
  range_end_time: <ISO 8601 UTC>
  name: <a short name that says what you hunt for>
```

Scope rules:

- Ask for 90 days back, with the current time as the end.
- **Sublime can return a shorter window than you asked for, and it does not warn you.** Retention can be shorter than the request, so a 90-day request may come back covering fewer days. Read the window in the response and report that one. Never state the window you asked for as the window searched. When the two differ, give both and say the shorter one is what the evidence covers.
- On the triage path, one hunt per case. Run a second one only when the first returns nothing and the technique was too narrow. Never run a third. This budget is about triage depth, so it does not apply to a detection backtest, which hunts once per candidate rule and takes its budget from `sublime-detection-engineering`.
- **A large result is not a reason to narrow the hunt.** A high `total_group_count` means your technique also describes ordinary mail, which is expected: most inbound mail mentioning a person's name is legitimate. It does not mean the hunt is wrong, and rewriting it to return a small number is the most reliable way to throw the finding away. The groups the platform already flagged are the ones you can see anyway; the value is in the unflagged remainder.
- **Narrow your attention, not the query.** When the count is large, read `message_groups` and sort your reading by `flagged_rules`: go through the **unflagged** groups first and judge them. Report the ones that match the attacker's method and say how many you reviewed out of the total. "202 matched, I reviewed the 40 unflagged ones, 3 look like the same lure" is a real finding. "202 was too many so I narrowed until 4 remained, all already flagged" is not a finding at all, it is a restatement of what the platform already told you.
- Rewrite the MQL only when the **unflagged** rows are plainly unrelated to the attack, which means the technique itself was described wrongly. Never rewrite it because the total was big.

#### 2.4.4 Reading the job

Call `get_hunt_job` with the job `id`. Then:

- `status` is `COMPLETED`: read the results.
- `status` is not `COMPLETED`: call `get_hunt_job` again, at most three times in total.
- Still not `COMPLETED` after three calls: call `get_hunt_job_results` anyway. Partial results are readable before the job completes. Use them, and write in the case that the hunt was still running and the count is a floor, not a total.
- `error` is not null, or `results_truncated` is true: report that with the results. A truncated result set is a floor as well.

Never wait in a loop beyond those three calls.

#### 2.4.5 Reading the results

`get_hunt_job_results` returns `message_groups` and `total_group_count`.

Each group carries:

| Field | What it tells you |
| --- | --- |
| `id` | The group's canonical ID, which is **not** the message ID |
| `flagged_rules` | The rules that fired. **Null means Sublime flagged nothing.** |
| `classification` | Sublime's verdict, null when it never reached one |
| `state` | `no-action` means the mail is still where it was delivered |
| `review_status` | `unreviewed` means no person has looked at it |
| `messages` | The messages in the group |

Each message in `messages` carries `id`, `subject`, `sender.email`, `sender.display_name`, `mailbox.email`, `recipients[].email`, `delivered`, `read_at`, `replied_at`, `forwarded_at` and `forward_recipients`.

#### 2.4.6 What a hunt result proves

Read these three rules before you write anything about a hunt.

1. **An unflagged group is the finding.** A group with `flagged_rules: null`, `state: no-action` and `review_status: unreviewed` is mail that matches the attacker's technique and that Sublime did not act on. It is still in the recipient's mailbox. Name the mailbox and the subject, and say plainly that no rule fired on it.
2. **A match is not a verdict.** Your MQL described a behaviour, so a match shows the behaviour, not malice. A legitimate sender can match. Separate the matches you judge to be part of the campaign from the matches you judge to be unrelated, and give your reason for each. Never report the raw count as a count of malicious mail.
3. **`read_at`, `replied_at` and `forwarded_at` are interaction evidence**, and they are the only interaction evidence you hold. They describe the message, not the link. `read_at` holding a time means the provider recorded a read of the mail. It does not mean anybody clicked. `replied_at` or a populated `forward_recipients` on a credential or payment message is a serious finding, so report it first and name the mailbox.

#### 2.4.7 Reporting the hunt

Report the hunt whatever it returns, in these terms:

- The technique you hunted for, in one sentence a reader can disagree with.
- The window Sublime actually applied. When it is shorter than the window you asked for, say both, because a reader will otherwise credit the hunt with more history than it had.
- How many groups matched, how many Sublime had already flagged, and how many it had not.
- For each unflagged match you judge to be part of the campaign: the mailbox, the subject, the sender, and any interaction evidence.
- What the hunt did not cover.

A hunt that returns nothing is a result. Write that the technique found no other mail in the window, and that this raises confidence the message was isolated. Never leave the hunt out because it was empty, and never describe an empty hunt as a failure.

The hunt reports. It never decides containment, and it never widens the remediation you request.

## 3. Resolving a mailbox to a person

A recipient address is not yet a person.

1. Call `tools.google_directory.get_user` with the address. That gives the account, not the human role.
2. Read `business-context` for what the environment expects of that account.
3. Read `org-chart` when you need the manager who must answer for that person's access.

An address that Directory does not know is evidence. It can be a former employee, an alias, a group or a mistake in the message. Say which, or say you could not establish it. Do not silently drop the recipient.

## 4. What an indicator proves

This is the core of the skill. Every rule below exists because the opposite mistake is common and expensive.

**Authentication results.**

- An SPF, DKIM or DMARC failure is not proof of an attack. Legitimate forwarding breaks SPF. A misconfigured sending service breaks DKIM. Mailing lists break both.
- A message that passes all three is not safe. An attacker who owns a domain configures its authentication correctly, because that is how the message reaches the inbox.
- Report the result and what it changes. Never report it as a verdict.

**The sender.**

- A lookalike domain, a Unicode or Punycode character, a misspelled brand and a display name that disagrees with the address are each an indicator to investigate, not a finding on their own.
- A Return-Path that differs from the From address is normal for marketing and delivery platforms. It matters only beside other indicators.
- A Reply-To on a different domain from the From address is a reason to look harder. Legitimate services also do it.

**URLs.**

- A detection ratio is a count of engines that disagreed. It is not a probability that the URL is malicious, and the engines do not use independent methods.
- A clean reputation result does not establish that a URL is safe. A domain registered this morning has no reputation at all.
- HTTPS says the connection is encrypted. It says nothing about who runs the site.
- A URL on a legitimate hosting service can still serve an attack.
- A link-tracking domain in `href_url` is the sending platform's redirector, not the destination. Report the redirector and the displayed destination separately, and say that the final destination was not resolved.
- Never open a suspicious URL yourself, and never ask a person to open one.

**Attachments.**

- A file extension is a claim, not a fact. Report the type Sublime identified.
- A macro-enabled document is a reason to investigate. It is not malicious by itself.
- A reference to PowerShell or a command shell inside a document is not a verdict. The surrounding code and the context decide.
- A hash matches one exact file. A one-byte change produces a different hash, so an absent hash match does not mean the file is absent.

**Scope.**

- The number of recipients is not the severity. One message to an administrator can cost more than fifty messages that were quarantined before delivery.
- A shared URL or attachment hash across several mailboxes is strong evidence of one campaign. A shared subject is weak evidence, because a subject is trivial to change.

## 5. What we cannot see

State this plainly in every case, because it is the limit of every phishing conclusion we draw.

This path assumes no endpoint detection platform, no proxy logs and no DNS logs. If you have one of those sources, name it and use it.

Therefore:

- **A click cannot be proved or disproved.** Never write "the user did not click", and never write "the user clicked" unless the audit log or Sublime's `message_links_clicked` shows a specific action. Do not write "no interaction evidence is available" until you have read the message-level fields in 2.4.6 rule 3. `read_at`, `replied_at` and `forwarded_at` are interaction evidence about the message. They are not evidence about the link.
- **Process activity cannot be examined.** We cannot show what an attachment did after it was opened, and we cannot show a process tree.
- **A download cannot be confirmed.** We cannot see a request to the attacker's host.
- **The message body as the recipient saw it cannot be read.** Sublime may withhold the raw message. When it does, you have the parsed links, headers and attachments, which is enough for indicators. It is not enough to quote the message's wording, so never quote or paraphrase body text as though you read it.

When an investigation needs one of these, say which source is missing and what it would have answered. A named gap is useful to the reader. An unstated gap reads as a clean result.

## 6. "Not found" is evidence

A lookup that returns nothing has told you something. Report it as a result.

- Directory does not know the address. That is a finding about the address.
- The audit log holds no `login` event for that account in the window. That is a finding about the account.
- VirusTotal holds no record of the domain. That is a finding about the domain's age or obscurity.
- An MQL query returns null. The field is absent from the message, which is a fact about the message.

Never report an empty result as a tool failure, and never leave it out because it was not interesting. A real tool failure is different: say which call failed and what you could not therefore establish.

## 7. The investigation record

Build this before you write anything for a reader. It is your working notes, not the output.

1. **The message.** Sender, subject, delivery time, recipients, delivery state, and the Message-ID.
2. **What Sublime found.** Flagged rules and their severity, and the attack score verdict, in the platform's own terms.
3. **The indicators.** URLs, domains, attachment names and hashes. One line each with what it is and what, if anything, it proves.
4. **The scope.** Mailbox count, message count, the shared indicator that links them, and whether the campaign is still receiving messages. Say whether each figure came from the flagged groups or from the hunt.
4a. **The hunt.** The technique you hunted for, the window, the matched group count, and every unflagged match with its mailbox and its interaction evidence. A malicious verdict with no hunt in the record is an incomplete investigation.
5. **The recipients.** For each one: admin role, 2-Step Verification state, org unit, account state.
6. **Account activity.** What the audit log shows in the window, and what `business-context` says about whether that is normal here.
7. **The gaps.** Every question you could not answer, each with the source that would have answered it.

A claim that is not in this record does not go in the case. If you want to write it and it is not here, go and read for it, or do not write it.

## 8. Wording

`case-writing-style` owns the prose rules. Three rules are specific to email evidence:

- Attribute every platform claim to its platform. "Sublime flagged the message under <rule>" rather than "the message is malicious".
- Separate what you read from what you concluded. A reader must be able to disagree with your conclusion while still trusting your evidence.
- Give the reader the figure, not the arithmetic. "The campaign reached 12 mailboxes" rather than two numbers and a subtraction.
