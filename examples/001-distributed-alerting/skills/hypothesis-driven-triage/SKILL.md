---
name: hypothesis-driven-triage
description: Load before the first query when triaging or answering questions on a Tracecat case of AWS GuardDuty findings. Question driven method for triaging a Tracecat case containing one or more related AWS GuardDuty findings. Identify the finding family, ask the decision questions, answer them from the SIEM and prior cases, record what cannot be answered, and set a verdict backed by evidence. Use with business-context (when that skill exists), aws-cloud-incident-response-core, case-output, and the bundled hypothesis-library.md.
---


# Hypothesis driven triage

The aim is a verdict a small security team can trust at a glance, backed by the specific questions that decided it, and a precise question for whatever is left. You triage the **case**, not a single finding. A case may hold several findings of the same family; treat them as one story and say whether they are one event or several.

## Method, in this order

1. **Read every finding on the case in full.** Note type, severity, resource, actor, account, region, first and last seen, count, and `service.additionalInfo.sample`. Fetch the raw finding JSON with boto3 as guardduty-case-lifecycle stage 1 describes; when a finding cannot be fetched, say so in `platform_notes` and do not triage a summary.
2. **Route it with business-context**, if you have that skill. Which deployment (account plus region), which workload class, who owns it, which of the three explanations (Platform, Customer, Neither) could fit, and whether a known benign pattern applies. A pattern matches only when every condition in it is met, not when the name is similar. With no business-context skill, the deployment is `Unknown`, no benign pattern applies, and you say so.
3. **Check prior cases.** Search Tracecat cases for the same principal, resource, or finding type in the last 30 days. Note how they were closed. A prior benign closure is evidence, not a verdict.
4. **Classify the actor** as human, automation, service, or unknown, following "Classify the actor before you triage" in aws-cloud-incident-response-core: principal type, user agent family, sign in evidence, the same identity across accounts for 30 days, other GuardDuty findings for it over 90 days, and one attempt to resolve it through the identity source of record. This is the first correlation on every case, not an optional pivot.
5. **Pick the family** in hypothesis-library.md from the finding type. Unknown types use Generic.
6. **Write 3 to 6 decision questions.** Start from the family list, make each one specific to this case with real names, IPs, pods and UTC times, and drop any whose answer would not change the verdict.
7. **Answer them from the SIEM.** At least one query per question. One line per answer: what you found and where. Bound every query by time and use LIMIT. Confirm a source has rows for the account and window before treating an empty result as meaningful.
8. **Record what you could not answer** in `closure` and `actor.would_establish`, addressed to the role that can answer it (see "Who can answer" in the library). Never address a customer. Post only as guardduty-case-lifecycle and slack-case-threads describe.
9. **Set the disposition** using the rules below.

## Dispositions

| Status | Set when |
|---|---|
| Open | The source, actor, or purpose of the activity has not been established |
| Benign | The source is identified and its owner has confirmed the activity is expected, or the activity matches a documented benign pattern in business context with evidence |
| Escalate | Evidence consistent with compromise or misuse has been found |

The reason is mandatory. Confidence is stated once and is not a substitute for the status.

**Gates.** A disposition is invalid unless all of these are true:

- Business context was consulted: the deployment, workload class, the benign pattern tested, and the sections relied on are all named in the output. A disposition with `context_used` empty is invalid. With no business-context skill, `context_used` holds the single entry `none: no business-context skill`, and that passes this gate.
- Every check lists the records, the window and the resources reviewed, and a result of found, not observed, ruled out (complete coverage only) or not collected.
- Each external indicator has enrichment stated as found, not found or not collected, from a tool you called yourself. A service that has never seen the indicator answers `not found`, and that is a complete answer, not a gap: it never blocks a disposition and never becomes a platform note.
- Each finding states whether the activity succeeded, was only attempted, or was not collected.
- For findings on shared clusters, the pre SNAT pod address was recovered from the flow records or its absence stated (the network section of business-context), and, when business-context names a shared workload role, that role's cross account sessions in the window were read (the shared workload role sessions query in guardduty-case-lifecycle stage 5).
- `explanations` lists every explanation consistent with the evidence with what would settle each; `closure` names the owner, the action and the criteria for Benign, Escalate and Inconclusive.
- Prior cases were searched and `history.meaning` says what the history means.

## Judgement

- Run every check the family lists. Leaving checks unrun is a failure; leaving explanations unranked when the distinguishing evidence does not exist is correct.
- Strong evidence counts as strong. An MFA verified SSO login by the same person from the same IP shortly before the activity is strong evidence they were present.
- An absent sample flag means a real finding. Only `sample: true` marks a GuardDuty sample.
- A missing log source is a telemetry gap. State it once in `platform_notes` and base the verdict on the evidence you do have. On its own it is never the reason a case stays Open.
- A failed query is not an answer. When a query errors, read the table schema, rewrite it, and run it again before recording the question as unclear; the one retry limit applies only to identity lookups. Never report a decisive question as unanswerable because of a query error.
- Business context is evidence. When it names a schedule, a workload, a benign pattern, or a known trap that fits the finding, the verdict must say so and the questions must test it, not rediscover it.
- When the actor is a person, named or not, pivot on the session: compare the source IP, user agent and hour with the same identity's last 30 days, check what else it did in the window across every account, and check whether it has triggered this or any other finding type before. A person who is unresolved to a name is still a person; say so plainly in the summary rather than leaving the actor as a bare role.
- A verdict is invalid if `actor.identity_type` is `unknown` while the session carries a browser user agent, an SSO role, or a console sign in. Those decide it.
- When the case holds several findings, say whether they share an actor, resource, or time window, and whether the verdict applies to all of them.
- Run every query that could change the verdict, and stop when further answers would not change it.
- Enrich every external indicator, whether or not it came up in a SIEM query.
