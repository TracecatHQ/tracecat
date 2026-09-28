---
name: phishing-triage
description: Triage a reported phishing email. Use when a user reports a suspicious email or an alert references a possible phishing message.
---

# Phishing triage

Reach a verdict on a reported email and recommend the next response step.

## Gather

1. Sender address, display name, reply-to, and return-path.
2. Authentication results: SPF, DKIM, and DMARC.
3. Every URL, attachment name, and attachment hash in the message.
4. Recipients, and whether any recipient clicked, replied, or opened an attachment.

Use only the tools available to you. If a field is unavailable, say so. Never guess.

## Assess

Weigh these signals:

- Display name or domain impersonating a known brand, executive, or vendor.
- Failed or missing SPF, DKIM, or DMARC for the sending domain.
- Reply-to that differs from the sender domain.
- Links whose visible text and destination differ, or that point to newly registered domains.
- Urgent requests for credentials, payment, gift cards, or bank detail changes.

## Verdict

Choose exactly one: `malicious`, `suspicious`, `spam`, or `benign`.

## Output

Return:

1. **Verdict** and a one-sentence reason.
2. **Evidence**: the three to five strongest signals, each tied to a message field.
3. **Indicators**: URLs, domains, IPs, and hashes, deduplicated.
4. **Exposure**: who received, clicked, or replied.
5. **Recommended action**: for example, purge from mailboxes, block the sender, reset credentials, or close as benign.

Keep the response factual. Mark any inference as an inference.
