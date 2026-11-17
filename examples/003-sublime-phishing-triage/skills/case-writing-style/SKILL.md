---
name: case-writing-style
description: "Prose rules for case comments, incident summaries, Slack briefs and any analyst facing text, built on ASD-STE100 Simplified Technical English. Covers sentence construction, word choice, and the evidential qualifiers a security case must keep. Attach to any agent that writes for a case or produces analysis output. Governs how sentences are written, never the layout: the step's instructions and case-output own structure."
---

# case-writing-style

Apply these rules to case comments, incident summaries, Slack briefs and analysis text a person will read. Do not apply them to workflow titles, action refs, field values or code.

**What this skill does not do.** It does not set the layout, the heading set, the length or the order of what you write. Those belong to the step's instructions and to `case-output`. When this skill and the step's instructions disagree, the step's instructions win. This skill governs the construction of each sentence, nothing above that level.

## Simplified Technical English

ASD-STE100 is a controlled language written so that one reader, under pressure, gets one meaning. A security case has the same requirement. The licensed STE dictionary is not reproduced here; the principle it exists to serve is: use the plainest available word, and use it the same way every time.

Check each sentence against this list. These are mechanical and you can verify them by looking.

| Rule | Rewrite when |
|---|---|
| One idea per sentence | The sentence carries two facts or two instructions joined by `and`, `while` or a semicolon |
| Active voice with a named actor | The sentence is passive and the reader cannot tell who acted. `The policy was attached` → `The SSO session attached the policy` |
| Simple tense | The verb is present perfect or a compound tense. `has been observed accessing` → `accessed` |
| Short sentences | The sentence runs past about 25 words, or you have to re-read it to find the verb |
| No semicolons | A semicolon appears. Use two sentences |
| No phrasal verbs | `spin up`, `reach out`, `dive into`, `kick off`, `circle back`, `touch base`, `stand up`, `look into`. Use `start`, `contact`, `examine`, `begin`, `investigate` |
| No nominalized actions | The verb has been turned into a noun. `performed an investigation of` → `investigated`; `made a determination` → `determined` |
| No noun clusters | Three or more nouns run together and the relationship is guessable only from context. `cross account role session credential` → `the credential for the cross-account role session` |
| No marketing adjectives | `robust`, `seamless`, `comprehensive`, `critical` used as emphasis rather than as a defined severity |
| Consistent terminology | The same thing is called two different names in one document. Pick one and keep it, even when repetition reads flat |
| Complete list items | A bullet trails off, or depends on the previous bullet to parse |

Repetition is correct here. Varying a term to avoid repeating yourself is a writing habit that costs the reader accuracy.

## Evidential qualifiers are required

This is the one place where STE's instincts and a security case pull apart, and the case wins.

STE suppresses modal language because it was built for maintenance procedures, where uncertainty is a defect. In triage, uncertainty is the finding. The most valuable sentence in a case is often the one that says what the evidence does not establish.

So separate two things that look alike:

- **Hedging is banned.** Vague softening that lowers your commitment without adding information: `may possibly indicate`, `could potentially suggest`, `it seems somewhat likely that`, `appears to potentially be`. Stacked modals are always hedging. Delete them.
- **Evidential qualification is required.** A precise statement of what a specific record does or does not support: `The sign-in records link the session to `r.shaw@example.com`. They do not establish who controlled the device.` `No record of an approved change covering this action was found in the reviewed window.`

The test: does the qualifier name the evidence and its limit? Then keep it, and write it in the plainest form. Does it only soften the claim? Then cut it and state what you actually found.

Never resolve uncertainty by writing a confident sentence. Write the limit as a fact.

## Sentence rules

- No antithesis. No corrective negation ("not X, but Y").
- No paragraph pinning. No parataxis.
- No summary beats. No rhetorical crutches.
- No negative parallelisms. No negative anaphoras.
- No contrasting pairs. No rule of three.
- No em dashes.
- No throat clearing openers. No landing sentences.
- No setup and payoff constructions.
- No parallel sentence structures within one paragraph.
- Vary sentence length. Don't fall into a pattern.
- No stacked noun phrases.

## Word rules

- No filler intensifiers: genuinely, really, truly, actually.
- No corporate register verbs: leverage, underscore, reflect.
- No nominalization. Write the verb, not its noun form.
- No performed enthusiasm.
- No hedging, as defined above. Evidential qualifiers stay.

## Voice

Write for the spoken voice. Say the sentence in your head before you keep it. If a person wouldn't say it to a colleague across a desk, rewrite it.

## Examples

Before: "The alert wasn't a false positive, but rather a genuine indicator of compromise, and we're leveraging our EDR tooling to contain it."
After: "The alert is a real indicator of compromise. Containment is running through the EDR tool now."

Before: "Access appears to have potentially been obtained via a cross account role session credential that may have been misused."
After: "A session assumed the cross-account role and read two secrets. The records do not establish whether the person named on the session started it."

The second pair is the important one. The rewrite is not more confident than the original. It is more specific about what is known and what is not.

## Check before you post

Read the draft once for what it says. Read it a second time against this list alone. A sentence can be accurate and still break three or four of these rules at once, so check separately.

Last check: find every qualifier in the draft. For each one, name the record it rests on. Any qualifier that cannot name one is hedging, and goes.
