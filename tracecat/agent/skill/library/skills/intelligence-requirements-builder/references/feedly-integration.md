# Feedly Collection Handoff (Optional)

This stage runs only if the Feedly MCP server is connected. If it is not, skip the stage entirely and do not emit collection or monitoring queries in any form; the three Stage 6 files are the complete output.

## Why this stage exists

A requirement that never connects to collection is shelfware. When Feedly is connected, the goal is to start answering each requirement straight away by routing it to the Feedly tool best fitted to what it asks, running that tool, and returning sourced findings the analyst can act on.

## Operating rules

- **Work from the EEIs, not the headline.** The requirement defines the question; the EEIs define what is observable, and observables are what the tools can collect against.
- **Resolve entities first.** Run `search_entities` to convert actor, malware, CVE, sector, and technology names into entity IDs. Prefer entity IDs over raw keywords in every downstream tool, since entity matching cuts noise sharply.
- **Match the period to the requirement.** Set each tool's time-period parameter to the timeframe written into the requirement (for example a requirement scoped to the past 12 months uses `LAST_1_YEAR`).
- **Prefer targeted, structured tools.** Use the specific tools (`get_threat_actor_relationships`, `get_malware_relationships`, `search_ttps`, `get_vulnerability_info`, trending lookups) first. Treat `search_threat_intelligence` as a synthesis fallback only, because its output can be very large (observed around 77,000 characters in one call) and may exceed the response budget. If you must use it, read the saved output in chunks or delegate the read to a subagent so it stays out of the main context.
- **Absence is signal.** If a relationship lookup returns no sector or org targets, that is evidence the activity is opportunistic or individual-targeted rather than sector-directed. Record it as a finding against the relevant EEI.
- **Never fabricate.** If a lookup returns nothing useful, say so and mark the EEI as an open collection gap.
- **Cite articles inline** in the MCP's required format when you reference article content. Do not cite non-article data types (relationships, TTPs, vulnerabilities, trending lists).
- **One primary tool per requirement, plus one optional pivot.** A pivot means following a named entity into its relationships (for example, resolving an actor, then pulling the malware relationship to surface who it targets). Do not chain further than that in this stage.
- **Always show the requirement code with its full question.** Wherever a requirement code appears (routing plan and findings), pair it with the full requirement question. Never present the code alone.
- **Include an EEIs column in the routing plan.** The routing plan table must list the requirement's EEIs alongside its code, full question, what the lookup will answer, and the tool chosen.
- **Use nested bullets in findings.** Present each requirement's findings as parent bullets with indented sub-bullets for supporting detail (for example, each associated actor as its own sub-bullet under a parent bullet about the attribution cluster), not as a flat list or prose.
- **State the date range in every finding.** For each requirement's findings, state the date range of the Feedly data used, matching the period parameter you set on the tools.
- **Get plan sign-off before collecting.** Present the routing plan and wait for the user to approve it. Do not run any lookups until the user OKs the plan.

## Tool-routing table

Classify each requirement from its EEIs, then pick the matching row.

| Requirement asks about | Best-fit primary tool | Optional pivot |
|---|---|---|
| Named-actor or campaign attribution ("who is attacking") | `search_entities` to resolve, then `get_threat_actor_relationships` (period-matched) for malware, targets, and TTPs | If no known actor, `get_trending_threat_actors` plus `search_articles` layered by sector and keyword to surface candidates |
| Technique or TTP ("how they compromise") | `search_ttps` filtered by industry, country, and malware type over the requirement's period | Read the TTP list returned by the actor or malware relationship call |
| Malware behaviour, or "who they target" | `get_malware_relationships` (period-matched) for associated actors, targeted orgs and sectors, and TTPs | `get_threat_actor_relationships` on the top associated actor |
| A specific vulnerability or CVE | `get_vulnerability_info`, or `search_vulnerabilities` for a set | `search_cpe` for affected products; `get_trending_vulnerabilities` for current activity |
| Sector or region threat landscape | `search_ttps` by industry and country, or `search_articles` with the sector topic ID plus keyword layers | `get_trending_cyber_attacks` for recent incidents |
| Base rate, scale, or coverage counting | `search_articles` (entity-based, layered) to gauge volume and coverage | `search_threat_intelligence` as a synthesis fallback, with the large-output caution above |
| Current-activity grounding for the criticality score | `get_trending_threat_actors`, `get_trending_vulnerabilities`, or `get_trending_articles` | Compare against the actor's or malware's period-matched relationship counts to distinguish a spike from sustained activity |

## How this maps to the three use cases

The routing above operationalizes the three Feedly use cases the skill supports:

1. **Seed collection and EEIs.** Entity resolution plus profile and relationship lookups pre-populate named sources and seed EEIs against the actors, CVEs, and sectors the requirement targets.
2. **Ground the prioritization score in current activity.** Trending and period-matched relationship lookups inform the time-sensitivity and decision-impact dimensions, so the score reflects what is moving now, not a static estimate. Note that a topic can be sustained over the year yet absent from the two-week trending list; treat that as a reason not to inflate time sensitivity.
3. **Map who is behind the threat, and who they target.** A relationship lookup from one named actor or malware family expands into the associated actors, the organizations and sectors hit, and the techniques in play, so the "for whom" question gets a sourced answer.

## Process

1. Build the routing plan for the fully enriched requirements and present it in chat as a table. Every row shows the requirement code with its full requirement question, the requirement's EEIs, what the lookup will help answer, and the tool chosen. Stop and wait for the user to verify and approve the plan.
2. Once approved, run the lookups. Resolve entities first, match each tool's period to the requirement timeframe, and keep to one primary tool per requirement plus at most one pivot.
3. Present both the approved routing plan and the findings in chat. Format findings as parent bullets with indented sub-bullets under a per-requirement subheading that repeats the requirement code and its full question, state the date range of the Feedly data used for each requirement, include inline article citations, and flag open collection gaps. Then offer to enrich the three Stage 6 files in place. Enrich only if the user accepts.

If the connected Feedly MCP also exposes a feed-creation tool, offer after reporting to create one monitoring feed per requirement, named after the requirement ID, confirming names and logic first and never creating more feeds than requirements.
