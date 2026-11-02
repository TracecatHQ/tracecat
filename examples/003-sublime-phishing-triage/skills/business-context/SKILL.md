---
name: business-context
description: What this organisation is, which email is expected, and who works here. Load it before you judge a sender, a sign-in, or an account change. This file is a template. Replace the example rows before you publish it.
---

# business-context

Use this skill before you call mail or account activity abnormal. It is the only record of what this organisation expects. No other skill holds that.

> **This file is a template.** The rows below use the reserved `example.com` domain. Replace them with your own organisation before you publish the skill. Until you do, nothing in those tables is evidence. Say "no baseline recorded" rather than treating an example row as fact.

## Known benign email

A pattern matches only when every condition in the row holds. A similar sender or a similar subject is not a match.

| Pattern | Every condition that must hold |
| --- | --- |
| Vendor notifications | The sender's root domain is `example.com`, and the message is a notification the organisation already expects from that vendor. |
| Internal notices | The sender's root domain is one of the organisation's own domains, and the display name is a person in the employee list. |

## Employee list

Match a mailbox to a person by the full address, in lower case. Do not build an address from a name.

| Name | Email addresses |
| --- | --- |
| Jane Doe | `jane.doe@example.com` |
| Alex Kim | `alex.kim@example.com` |

## Normal account activity

- Sign-ins from the organisation's usual country, during working hours, are the baseline. A country the account has not used is not.
- A new OAuth grant, a new recovery address, or a change to 2-Step Verification is not normal unless a row above says it is.
- No shared mailbox and no team address is a person in the employee list.

## When a section does not answer

Say which section you read and that it does not cover the question. Do not fill the gap with a plausible guess. A missing row is "not recorded", not "abnormal" and not "benign".
