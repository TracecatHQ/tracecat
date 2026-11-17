---
name: sublime-detection-engineering
description: "Load when the prompt's first line is `Sublime detection proposal <canonical_id>`. Turns one confirmed malicious email into a tested Sublime MQL detection rule proposal: read what made the message malicious, check what already catches it, author MQL, validate the syntax, prove it matches the source message, backtest it over history with a hunt job, judge the volume and the false positives, and return a proposal a person approves. You never create, enable or deploy a rule. Load with email-evidence-core and case-writing-style."
---

# Sublime detection engineering

One confirmed malicious message becomes one tested detection proposal. A person decides whether it ships.

This is the loop Sublime sells as its Autonomous Detection Engineer. You are running it on the free tier, which gives you everything that matters: MQL, rule validation, and retroactive hunting over the whole mailbox history.

## What you never do

- **Never create a rule.** The workflow calls `create_rule`, and only after a named person approves. You return a proposal.
- **Never enable a rule.** Even when the workflow creates one, it is created with `active: false`. A person enables it in Sublime.
- **Never claim a rule was created or is live.** Say "proposed".
- Never choose the rule name's case reference, the Slack destination, or the approver. The workflow derives those.
- Never run a hunt whose MQL you have not validated. A broken hunt wastes the window and returns nothing useful.

## The loop

### 1. Read the case and find the detection gap

**Read the case first, with `core.cases.get_case`.** The prompt carries its ID. The case holds a section called `## What did the hunt find?` with a table of every message the threat hunt matched, and a `Flagged` column.

That table is the most important thing you will read, because it tells you which rule is worth writing:

- A row with `Flagged` **no** is mail that matches the attacker's technique and that **no existing rule fired on**. That is a real detection gap, and a rule that catches it cannot be a duplicate. **Write the rule for these rows.**
- A row with `Flagged` **yes** is mail some rule already catches. A rule written only for these rows duplicates a rule that exists, which is why step 8 rejects it.

So state the gap to yourself before you write anything: which rows are unflagged, what they have in common with the source message, and what an existing rule would have had to test to catch them. The rule you author must match the unflagged rows **and** the source message.

When every matched row is already flagged, say so plainly. The honest answer is then usually `reject` with the reason that existing coverage is adequate, and that the real gap is remediation rather than detection. Do not invent a rule to avoid returning `reject`.

When the case holds no hunt table, or the hunt matched nothing, fall back to the source message alone and say in the proposal that you had no gap evidence to work from.

### 2. Read what made it malicious

Start from the case and the message group. You need the specific, mechanical reason this message is an attack, not a summary.

Pull the parsed message with `analyze_message_by_id`. **Every entry in `queries` is an object with a `source` key, never a bare string.** A list of strings returns `400 Invalid input type string for (expected handler_types.QuerySimple)` and costs you a call:

```yaml
id: <sublime message id>
payload:
  queries:
    - source: sender
    - source: headers.auth_summary
    - source: body.links
    - source: attachments
    - source: subject
    - source: headers.message_id
```

Ask for all of them in that one call. `email-evidence-core` 2.1.1 has the full query set.

Write down, for yourself, the two or three facts that separate this message from legitimate mail. Those facts are the rule. A rule built on "it was malicious" is not a rule.

### 3. Check what already catches it

Call `list_rules` and read the `source` of every rule that could plausibly overlap. **Compare the MQL, not the names.** Two rules with different titles often test the same predicate, and a third rule that nearly matches is better narrowed than duplicated.

The message was flagged, so rules already fired on it. Read those first with `get_rule`: your job is to catch what they missed, or to catch it earlier, not to restate them. Test each one against the **unflagged** rows from step 1: a rule that fired on the source message but not on those rows is the rule your candidate must beat, and the predicate that let those rows through is the thing to fix.

When an existing rule already covers the behaviour, say so and propose a narrowing change to that rule instead of a new one. A smaller rule set is worth more than a larger one.

### 4. Author the MQL

Sublime's MQL reads like a predicate over one message. A rule looks like this:

```
type.inbound
and length(recipients.to) == 1
and sender.email.domain.root_domain not in $org_domains
and any(body.links,
        strings.iends_with(.href_url.domain.domain, '-account.workers.dev')
        and regex.icontains(.href_url.domain.domain, '^(?:adobe|docusign|onedrive)-[a-z0-9]{3}')
)
```

The constructs you will use most:

| Construct | Use |
| --- | --- |
| `type.inbound` | Start almost every rule with it. A rule that also matches outbound mail will surprise somebody. |
| `any(collection, predicate)` | Iterate links, attachments or recipients. Inside it, `.field` refers to the current item. |
| `strings.icontains`, `strings.iends_with`, `strings.starts_with` | Case-insensitive string tests. Prefer these to regex when they are enough. |
| `regex.icontains` | Use only when a string test cannot express it. Anchor it and bound it. |
| `length(...)`, `strings.count(...)` | Counts, for structural tests. |
| `html.xpath(body.html, '...')` | Structural tests on the rendered body. |
| `sender.email.domain.root_domain` | Always prefer the root domain to the full domain when testing ownership. |
| `headers.auth_summary.dmarc.pass` | Authentication results, when they genuinely discriminate. |

Rules that hold:

- **Anchor on the mechanism, not the indicator.** A rule matching `dasrong.org` dies the moment the attacker buys another domain. A rule matching "display name equals an employee name, sender domain is not ours, message has no prior thread" survives.
- **Never put a single literal indicator in a rule** unless the proposal says plainly that it is temporary and names what replaces it.
- **Never inline a list the platform already maintains for you.** Writing out employee names, organisation domains or brand strings as a literal list inside the MQL is the same mistake as a single literal indicator, just repeated. The list is correct on the day you write it and silently wrong the moment somebody joins, leaves or changes their display name, and nothing in the rule or the hunt will tell you. Use the Sublime variable instead: `$org_display_names` for people, `$org_domains` for your own domains, `$high_trust_sender_root_domains` for trusted senders. These are maintained by the platform, so the rule keeps working after a staff change.

  ```
  # wrong: correct today, stale next month, and no warning when it breaks
  and any(["jane doe", "jane", "alex kim", "alex", "sam lee", "sam"],
          strings.icontains(subject.subject, .))

  # right: the platform maintains the list
  and any($org_display_names, strings.icontains(subject.subject, .))
  ```

  **When you need a variant the variable does not cover, derive it; do not conclude it is impossible.** The common case is a bare first name when `$org_display_names` holds full names. `strings.icontains(subject.base, .)` fails here, because "Jane Doe" is not inside the subject "Jane". Reverse the containment instead and ask whether the maintained name contains the subject:

  ```
  # matches subject "Jane" and subject "Jane Doe", with no names typed
  and length(subject.base) >= 3
  and any($org_display_names, strings.icontains(., subject.base))
  ```

  The length guard matters: without it a one or two character subject is inside almost every name. This pattern is the one to use. An agent previously wrote the names out and justified it with "this MQL environment cannot derive first names from $org_display_names", which was wrong; it had not tried reversing the arguments. Reach for a reversal, a prefix test or a different field before you accept a literal list.

  If you genuinely cannot express it without one, say so in the proposal, name the maintenance burden you are creating, and say who has to update it and when. That disclosure is a last resort, not a licence.
- **Do not test the subject alone.** A subject costs an attacker nothing to change.
- **Prefer two weak signals combined over one strong one.** One signal is a false positive generator; two that co-occur rarely are a detection.

### 5. Validate the syntax

Call `validate_rule` with the candidate source before anything else touches it. A rule that does not parse cannot be backtested, and a hunt started with broken MQL returns an empty result that looks exactly like a clean backtest.

If it fails, fix it and validate again. Report the final source, not the attempts.

### 6. Prove it matches the source message

Call `analyze_message_by_id` with the source message's ID and your candidate rule in the `rules` field. The rule must match. If it does not, your rule does not describe the message you are trying to catch, whatever it looks like on paper.

A rule that fails this step is not proposed. Rewrite it.

### 7. Backtest it over history

**A hunt job is a batch job and takes minutes. On an interactive path it is banned.** When the prompt gives you a time budget, or says a person is waiting on a button press, you do not start a hunt job at all. You prove the rule with the two instant tests below and you report the volume as not measured. A proposal a person reads in two minutes and can approve is worth more than a volume figure they waited twenty minutes for.

**The two instant tests, both required:**

1. **Grammar.** `validate_rule` with your candidate source. It returns immediately. A rule that does not parse is not a proposal.
2. **Positive case.** `analyze_message_by_id` with the source message and your candidate source. It returns immediately and proves the rule fires on the real phish. A rule that does not match the message it was written for is not a proposal either.

Both must pass before you write anything. Neither needs a hunt.

**What you must then say, plainly, in the proposal:** the rule is syntax checked and proven against the source message, and its volume over history has not been measured. Never call it tested, never give a backtest count, and never imply a figure you did not obtain. The person approving decides whether to run a backtest in the Sublime console before they enable it, and you say that is the next step.

**Judging coverage without a backtest.** You still have to say whether the rule earns its place, and you can, from the rule inventory in step 4 and the hunt table already in the case. Apply the durable-versus-indicator test below. When every existing rule that fires is indicator-bound and your candidate tests the method behind them, the candidate is worth proposing even with no volume figure, because you are outliving those rules rather than repeating them. Say exactly that.

**When a hunt is allowed.** Only on a path with no person waiting, such as a scheduled review, and only when the prompt says so. Then use a 7 day window, poll `get_hunt_job` at most five times with real work between the polls, and read partial results as a floor: an unflagged group in a partial read is a real unflagged group, though a partial total is not a volume figure and must be reported as "at least N, job still running".

What to read:

- `total_group_count` — how many message groups the rule would have fired on. This is the volume number.
- `results_truncated` on the job — when true, the count is a floor, not the answer. Say so.
- The returned `message_groups` — your false positive sample. Read them one by one.

### 8. Judge it

This is the part that is yours, and it is the reason a person is reading your proposal rather than the MQL.

Classify every historical hit you sampled:

- **The source message** and other copies of the same campaign. These are true positives and they are the point.
- **Other messages that are also attacks.** Excellent: the rule generalises.
- **Legitimate mail.** Each one is a false positive and each one costs an analyst time, every week, forever.

Then give a verdict:

| Verdict | When |
| --- | --- |
| `ready` | It catches the campaign, the historical hits are all attacks or explainable, and the volume is one a person can absorb. |
| `needs_narrowing` | It works but it also catches legitimate mail. Say exactly which predicate to add, and why. |
| `reject` | It cannot be narrowed without becoming an indicator match, or an existing rule already fires on **every** message it matches. |

#### Coverage is not the same as durable coverage

Before you reject for duplication, read the MQL of the rules that already fired and ask what each one actually tests.

- A rule that tests **a technique** is durable: the display name holds an employee name, the link URL embeds the recipient's own address, the sender has no prior history with us. An attacker cannot avoid it without abandoning the method.
- A rule that tests **an indicator** is not: one shortener domain, one path shape, one hosting provider, one file type. The attacker changes that string for a few pounds and the rule is silent, with nothing in the hunt to warn you, because the variant has not arrived yet.

When every rule that fired is indicator-bound and your candidate tests the method behind them, the candidate is worth adding **even when the backtest found nothing unflagged**. You are not duplicating those rules, you are outliving them. Say that in the proposal: name the indicator each existing rule depends on, name the cheap change that would defeat it, and say what your rule still catches afterwards.

A zero-unflagged hunt tells you nothing slipped through in the window. It does not tell you the coverage will hold. Do not use it as proof that no rule is needed.

Reject for duplication only when an existing rule tests the **same method** your candidate tests, not merely when it happened to catch the same messages.

Weigh the existing coverage against the gap from step 1, and do not collapse the two. An existing rule that fires on the source message does **not** cover the candidate when the candidate also matches rows that went unflagged. Those rows are the whole reason to add it, so a candidate that catches real mail nobody caught is `ready` even though a rule with a similar name already exists. Only reject for duplication when the existing rule fires on every message your candidate matched, the unflagged rows included.

#### Thin history: precision decides it

A rule with no hits beyond the source message is thin on evidence. Whether that is fatal depends entirely on how precisely the MQL expresses the method.

- **A precise rule can be `ready` on thin history.** Every predicate tests part of the attacker's method, so the rule cannot fire on much else, and the precision is itself the evidence. Say plainly that history is thin and why the rule is still safe: name what each predicate forces the attacker to keep doing.
- **A loose proxy cannot, however durable the idea behind it.** A predicate that merely correlates with the method, such as a length test, a field being present, or a domain simply being external, will fire on ordinary mail the backtest did not happen to cover. Thin history then tells you nothing, because you have not seen the volume it would really produce. The verdict is `needs_narrowing`, and you say which predicate is the proxy and what would replace it.

The test to apply, predicate by predicate: **does this express part of the attacker's method, or is it a stand-in that happens to correlate with it?** `length(.href_url.fragment) >= 8` is a stand-in. `the fragment decodes to the recipient's address` is the method. The first catches the campaign by accident and a lot else besides; the second catches it on purpose.

**Your rule's name must describe what the MQL tests, not what you wish it tested.** When the name claims a precise technique and the MQL tests a loose proxy, the proposal misleads the person approving it, because they read the name and the summary and trust that the source matches. Either tighten the MQL to the name, or rename the rule to the weaker thing it really does. Never ship the gap between them.

**Zero historical hits does not mean a clean rule.** It means the rule is untested against reality. Say that plainly rather than reporting it as a pass. A rule whose only evidence is the message it was written from has been tested once, on itself. Combined with the rules above: precise and untested can still be `ready`, with the thinness stated; loose and untested never is.

Never express the false positive rate as a ratio from a sample you did not count. Give the numbers you have.

### 9. Return the proposal

The workflow posts it and owns the approval. Return exactly this, as text:

```
Rule: <name>
Severity: <informational | low | medium | high | critical>
Attack types: <Sublime attack types>
ATT&CK: <tactics and techniques, or none>

Source:
<the validated MQL>

What it catches:
<two sentences on the mechanism, not the indicator>

Backtest: <n> groups over <the window Sublime applied, and the window you asked for when they differ>
- True positives: <n>, including the source message
- Other attacks: <n>
- Legitimate mail: <n> <list them>
- Truncated: <yes | no>

False positives to expect:
<the legitimate mail shapes that would match, or "none observed in the window searched">

Verdict: <ready | needs_narrowing | reject>
<one sentence saying why, and for needs_narrowing the exact predicate to add>
```

Naming: `<Attack type>: <mechanism in plain words>`. Examples: `BEC: employee display name from external domain`, `Credential phishing: voicemail lure with redirector link`. Not the sender, not the campaign, not the case number.

#### The `source` field is MQL, or it is empty

**`source` carries validated MQL and nothing else. A later step may send it to Sublime exactly as you wrote it.** Never put a message ID, a group ID, a case reference, an explanation or any other prose in that field. Text that is not MQL in `source` becomes a broken rule in the detection platform.

There are two different outcomes and they must not be confused:

- **You wrote a candidate and judged it should not be created.** `verdict` is `reject` or `needs_narrowing`, and `source` holds the MQL you actually wrote and validated. A person can read it and overrule you.
- **You wrote no candidate at all**, because existing rules already cover the technique and the hunt found no unflagged message, so there is nothing a new rule would add. Then `verdict` is `reject`, **`source` is an empty string**, and `backtest_count` is `0`. Say in `proposal_text` that you authored nothing and why. There is nothing to approve, and the workflow will not offer an approval button.

Declining to author a rule is a legitimate and often correct answer. Return it honestly as an empty `source` rather than filling the field to satisfy the schema.

## Severity

Match the severity Sublime would assign a rule of this kind, not the severity of the one case you saw. A rule firing on a whole class of mail is not as severe as the worst message in that class.

Use `high` or `critical` only when a match alone justifies waking somebody. Credential phishing against anyone is `high`. A broad attack-surface rule that catches a lot of low-value mail is `low` or `medium` regardless of how bad the original message was.
