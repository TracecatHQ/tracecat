# Skill learnings inbox

Append-only buffer for the **build → feedback → distill** loop. This file is **not** linked
from any `SKILL.md`, so it never auto-loads into context — it is a staging area, not policy.

## How to use it

- **Capture (after a build session):** append a raw, dated candidate learning below — what
  happened, what the user's feedback was, and the *generic* rule it might imply. Cheap and
  unbounded; do not edit the skills yet.
- **Also capture from evals:** after an eval run, mine `report.json` `key_failures` and
  `improvements` (under `.tracecat/evals/tracecat_authoring/<timestamp>/`) and drop candidate
  learnings here.
- **Distill (periodically):** fold entries into the skills as the **smallest edit that
  generalizes** — sharpen an existing rule > add a Common-Mistakes bullet > add detail to a
  `references/*.md` > (last resort) add a new `references/` file. Keep `SKILL.md` lean and
  under its eval char budget; route detail to `references/`. Then **delete the distilled
  entry** from this file.

## Rules

- Only **generic, cross-tenant** lessons may enter a skill. Tenant/tool specifics stay in the
  owning tenant tree. Slack craft is the sole tool-specific exception → it belongs in
  `tracecat-slackbot-best-practices`.
- Before committing a skill edit, run the eval static checks as a regression gate (budget,
  fenced-block parse, action-literal validity).
- Follow the Agent Skills spec for structure/naming: <https://agentskills.io/specification>.

---

## Pending learnings

<!-- Append entries below. Format:
### YYYY-MM-DD — short title
- Context:
- Feedback / signal:
- Candidate generic rule:
- Target (skill + section, or references/<file>):
-->
