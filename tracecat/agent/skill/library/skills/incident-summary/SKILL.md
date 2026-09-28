---
name: incident-summary
description: Write a concise incident summary for stakeholders. Use when asked to summarize a case, incident, or investigation.
---

# Incident summary

Turn case notes, alerts, and investigation findings into a summary a stakeholder can read in one minute.

## Structure

1. **Status**: open, contained, or resolved, with the current severity.
2. **What happened**: two or three sentences in plain language.
3. **Impact**: affected users, systems, and data. State "unknown" when unconfirmed.
4. **Timeline**: first detection, key actions, and containment, as timestamped bullets.
5. **Next steps**: each with an owner when known.

## Rules

- Use only facts present in the provided context. Label inferences as inferences.
- Lead with the status line. Put technical detail after the plain-language summary.
- Use UTC timestamps.
- Keep it under 250 words unless asked for more detail.
- Do not include secrets, credentials, or full personal data. Refer to them generically.
