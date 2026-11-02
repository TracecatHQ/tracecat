---
name: intelligence-requirements-builder
description: Turns a vague, high-level stakeholder ask into a structured set of intelligence requirements for a CTI team, complete with Essential Elements of Information, collection guidance, success criteria, deliverables, and a criticality rating, producing the intelligence requirements set as markdown, as a Word document, and as a CSV intelligence requirements list. Use this skill whenever the user mentions intelligence requirements, IRs, PIRs, GIRs, SIRs, stakeholder requests, RFIs, requirements gathering, or says things like "my CISO asked me to look into X", "leadership wants to know about Y", "help me write requirements for Z", or "turn this request into something my team can action". Also use it when a user wants to build, review, or prioritize an intelligence requirements list.
---

# Intelligence Requirements Builder

This skill helps a CTI analyst convert an informal stakeholder request into formally codified intelligence requirements. The output should be precise enough that a CTI team could begin collection against it immediately, structured enough to slot into an existing intelligence requirements list, and concise enough that a busy analyst can scan it in under a minute. Favor brevity throughout: short phrases over full sentences in structured fields, and no filler.

Before producing requirements, read `references/ir-definitions.md` to ensure correct terminology, and read `references/requirement-quality.md` to ensure every requirement is written to the quality standard. Intelligence requirements have specific meanings inherited from military and government intelligence practice, and using the wrong term (for example, conflating prioritized intelligence requirements with Priority Intelligence Requirements) undermines the credibility of the output. Equally, a requirement that asks two questions at once, or asks a question that collection cannot answer, undermines the credibility of the whole set.

## Workflow

Follow these six stages in order. Do not skip the clarification stage unless the user's request already contains the answers.

### Stage 1: Intake

Parse the stakeholder ask and extract what is already known:

- **Stakeholder**: Who is asking, and what is their role? (CISO, SOC lead, fraud team, executive leadership, product team, etc.)
- **Decision**: What decision or action will this intelligence support? Every requirement must link to a decision; intelligence without a decision attached is trivia.
- **Timeframe**: Is this a one-off, time-bound need or an ongoing concern?
- **Scope**: Which assets, business units, geographies, technologies, or threat types are implicated?

State your initial read back to the user in two or three sentences before asking anything. This shows the analyst what you have inferred and lets them correct you early.

### Stage 2: Clarify

Ask only the questions you cannot answer from the request itself, and ask no more than five. Prioritize in this order:

1. What decision will this intelligence inform, and who makes it?
2. When is the decision being made (or is this an enduring concern)?
3. What does the stakeholder already know or believe about this topic?
4. What would a useful answer look like to them (a briefing, a report, an alert, a recommendation)?
5. Are there constraints on scope (specific regions, subsidiaries, technologies, or threat actors)?

If the user cannot answer a question, proceed with a documented assumption rather than blocking. Mark every assumption clearly in the final output so it can be validated with the stakeholder later.

### Stage 3: Classify

Determine whether the need is enduring or priority, and say which explicitly:

- **Enduring requirement (GIR or SIR)**: An ongoing intelligence support need with no fixed end date. Example: "What ransomware groups are targeting the financial services sector in North America?"
- **Priority Intelligence Requirement (PIR)**: A time-bound need with a short shelf life, usually landing as an unscheduled RFI, that pulls the team's attention and resourcing toward a decision that has to be made soon. Example: "Is the vulnerability disclosed yesterday in our VPN appliance being actively exploited in the wild?"

Apply this test: if the requirement still matters in 90 days in roughly its current form, it is likely enduring. If it expires once a specific decision is made or event passes, it is likely a PIR.

Do not label an enduring requirement as a PIR simply because the stakeholder is senior or the topic feels urgent. A team that ranks its enduring requirements has *prioritized* intelligence requirements; that is a different concept from a PIR, and the distinction matters. See `references/ir-definitions.md` for the full explanation.

### Stage 4: Decompose

Decompose the ask into candidate requirements, then keep the final set small. If the stakeholder asked a single question, fully enrich only the 2 to 4 requirements most load-bearing for the decision identified in Stage 1, and never more than 6. If the stakeholder asked more than one distinct question, keep all the resulting requirements in one combined set and fully enrich 1 to 3 requirements per question; this budget applies per question, so three questions may yield up to nine enriched requirements in the single set. The questions stay distinct inside that one set: each requirement records which stakeholder question and decision it traces to (via its linked-decision field and, in the CSV, its Stakeholder and Decision_Supported columns), so context is never lost even though everything lives in one place. List any remaining valid questions in a single "Candidate requirements" section at the end of the set so the team can promote them later if capacity allows. Candidates get the question and a one-line rationale only, with no EEIs, collection guidance, or criticality scores, and they never enter the CSV ledger. Before writing any requirement, read `references/requirement-quality.md` for the full standard and worked examples of bad questions rewritten into good ones.

Every requirement must pass all five tests below. If it fails any one, rewrite or split it before moving on. According to CTI best practices, a strong requirement is singular, atomic, decision-centric, and timely: it asks only one question, focuses on a specific fact, event, or activity, and supports a single decision. This skill adds a fifth test, answerability, which commercial CTI practice makes necessary.

1. **Singular.** One question per requirement, asking one thing. If the question contains "and" joining two distinct asks (actors *and* techniques, footprint *and* pipeline), split it into separate requirements. A parenthetical enumeration of categories within a single fact is acceptable; a second question clause is not.
2. **Atomic.** The requirement targets a specific fact, event, or activity. "What is happening with ransomware?" is a topic. "Which ransomware operations have claimed victims in our sector in the past 12 months?" is a fact to establish.
3. **Decision-centric.** Answering the requirement supports one decision identified in Stage 1. Note the linkage explicitly in the requirement's linked-decision field. If a requirement supports no decision, cut it. If the ask spanned several questions, the set may serve several decisions, but each individual requirement must still map to exactly one of them.
4. **Time-bound.** The question states its timeframe inside the requirement itself ("over the past 12 months", "before the Q3 board decision"). A requirement without a timeframe cannot be judged answered, current, or stale.
5. **Answerable with CTI.** The question must be answerable through collection and analysis of observable evidence, internal or external. Questions that ask the team to predict the organization's own future ("How likely are we to be breached?") are risk judgments, not intelligence requirements. Do not write them. Instead, decompose them into the observable questions that feed the judgment (incident frequency among comparable organizations, evidence of targeting, evidence of techniques defeating controls equivalent to ours) and state in the requirement set that the final risk call is the stakeholder's synthesis of that evidence.

In all cases, a requirement is phrased as a question. "Monitor ransomware" is an activity, not a requirement.

Under each requirement, list 2 to 4 **Essential Elements of Information (EEIs)**: the individual facts an analyst must establish before the requirement can be considered answered. EEIs are what make a requirement collectible. **Keep each EEI to a short noun phrase of roughly 5 to 10 words, not a full sentence.** For example, for the requirement "Which threat actors have targeted our sector in LATAM in the past 12 months?", EEIs might be: "Actors with confirmed sector victims in LATAM"; "Their observed targeting of new market entrants"; "Attribution confidence per actor".

Where the user indicates a mature team, also suggest **Collection Requirements** (what data sources need to be tasked) and **Production Requirements** (what intelligence products will be created and on what cadence). Do not force these on users who only need the requirements themselves.

### Stage 5: Enrich

For each requirement, provide four things:

**Collection guidance.** Name specific source types and, where possible, specific starting points. "Monitor the dark web" is not collection guidance. "Track affiliate recruitment posts on RAMP and Exploit forums, ransomware leak sites for victims in [sector], and vendor reporting on [actor] campaigns" is. Prefer free and open sources where they exist, and say which sources require paid access.

**Success criteria.** Define how the stakeholder will know the requirement has been answered. Phrase as observable outcomes: "The stakeholder can name the three most likely initial access techniques and has a prioritized mitigation list" rather than "stakeholder is informed."

**Deliverable and cadence.** Name the intelligence product or service that will answer the requirement (for example, a one-time briefing, a monthly threat landscape report, a quarterly stakeholder review, or an alerting service) and how often it will be delivered (One-time, Ad hoc, Weekly, Monthly, Quarterly, or Continuous). Base this on the Stage 2 answer about what a useful output looks like to the stakeholder. PIRs usually take a one-time or ad hoc deliverable; enduring requirements usually take a recurring one.

**Criticality rating.** Score the requirement using the matrix below and assign Critical, High, Medium, or Low. Show the component scores so the rating is defensible rather than asserted.

#### Criticality matrix

Score each dimension from 1 (low) to 5 (high), then average:

| Dimension | What to assess |
|---|---|
| Decision impact | How consequential is the decision this supports? Revenue, safety, regulatory, or executive-level decisions score high. |
| Time sensitivity | How soon must the decision be made? Imminent deadlines score high. |
| Stakeholder breadth | Do multiple teams ask similar or overlapping questions? Requirements serving several stakeholders score high. |
| Feasibility | Can the team realistically answer this with current skills, data access, and tooling? Easily answerable scores high. |

Mapping: average 4.0 or above = Critical; 3.0 to 3.9 = High; 2.0 to 2.9 = Medium; below 2.0 = Low.

Feasibility is included deliberately. Early in an intelligence program, demonstrating value quickly matters, so a moderately impactful requirement the team can answer this week may rank above a highly impactful one it cannot answer at all.

### Stage 6: Output

Produce exactly three files in total: one markdown record, one Word document, and one CSV list. This holds no matter how many questions the stakeholder asked. Do not split the output into a separate file, set, or trio per question. Where the environment supports file creation, write all three as actual downloadable files; otherwise render the markdown in the conversation and say so.

When the stakeholder asked more than one distinct question, all of those questions and their requirements go into the same three files. Use one shared summary block at the top that covers the whole set, then list every enriched requirement together under a single Requirements section, and put every row in the one CSV. The distinct questions do not fragment the output; they are preserved inside it. Give the summary block a single row for the requesting stakeholder(s) and a "Questions addressed" row that lists each stakeholder question with the decision it supports, so a reader sees at a glance which decisions the set serves. Every requirement then carries its own context through its linked-decision field (naming which of those questions and decisions it answers), and every CSV row carries it through the Stakeholder and Decision_Supported columns. Keep IDs unique across the whole set and grouped in a contiguous run per originating question (see the ID rule below) so a reader can still tell which question a requirement came from without needing separate files.

**Output 1: The intelligence requirements set (markdown).** Use the template in `assets/requirements-template.md`. This is the analytical record and must include:

1. A single summary block (stakeholder, decision supported, classification, date, review date). When the ask covered more than one question, keep this one block and use its "Questions addressed" row to list each stakeholder question with the decision it supports; set classification to Mixed if the set spans enduring and priority requirements, and set the review date to the earliest date at which any part of the set needs revalidating.
2. The full requirements detail for every requirement across all questions, in one Requirements section (ID, requirement, type, EEIs, collection guidance, success criteria, deliverable and cadence, criticality with component scores). Each requirement's linked-decision field names which stakeholder question it answers.
3. An assumptions list, if any assumptions were made.
4. A single "Candidate requirements" section, if any valid questions were deprioritized in Stage 4: the question plus a one-line rationale each, nothing more.

Keep the prose tight. Collection guidance and success criteria should be one short bullet each per point; do not pad fields to fill the template.

**Output 2: The intelligence requirements set (Word document).** The same content as the markdown record, produced as a single .docx file covering the whole set, for analysts who need to circulate the requirements to stakeholders or attach them to an approval workflow. Mirror the markdown structure: one shared summary table, then one section per requirement across all questions, assumptions at the end. Where .docx creation is not available in the environment, say so and offer the markdown file as the alternative.

**Output 3: The intelligence requirements list (CSV).** Use the column structure in `assets/requirements-list-template.csv`. This is the working file the team appends to their existing intelligence requirements list, uses to guide operations, and imports into security tooling. Rules:

- One row per requirement, exactly these 14 columns, in this order: `IR_ID, Type, Requirement, Stakeholder, Decision_Supported, Criticality, Status, EEIs, Intelligence_Deliverable, Delivery_Cadence, Success_Criteria, Owner, Date_Created, Review_Date`.
- Keep cell contents condensed. The CSV is deliberately a simplified view; full collection guidance, matrix scores, and assumptions live in the markdown and Word records only.
- Separate multiple values within a cell using semicolons (notably EEIs), never commas.
- Use controlled vocabulary: Type is GIR, SIR, or PIR; Criticality is Critical, High, Medium, or Low; Status is Proposed, Active, or Retired; Delivery_Cadence is One-time, Ad hoc, Weekly, Monthly, Quarterly, or Continuous.
- Always set Status to Proposed and leave Owner blank. Approving requirements and assigning analysts are the team's decisions, not the skill's.
- Candidate requirements never enter the CSV; only fully enriched requirements get a row.
- Use ISO 8601 dates (YYYY-MM-DD). For enduring requirements, Review_Date is the revalidation date; for PIRs, it is the expiry date tied to the decision.

Use requirement IDs in the format `IR-YYYY-NNN` for enduring requirements and `PIR-YYYY-NNN` for priority requirements, and keep IDs identical across all three files so they stay traceable to each other. When one ask covers multiple questions, all IDs still belong to the same set and must be unique across it; group each question's IDs in a contiguous run (for example IR-2026-101 to 104 for the first question, IR-2026-111 to 113 for the second) so a reader can tell at a glance which question an ID came from without needing separate files.

### Optional Stage 7: Feedly collection handoff

This stage runs only if the Feedly MCP server is connected. Detect it by checking whether Feedly MCP tools (for example `search_entities`, `get_threat_actor_relationships`, `search_ttps`) are available in the session. If they are not, skip this stage entirely; the three Stage 6 files are the complete output, and no collection or monitoring queries are emitted in any form.

If Feedly is connected, use it to begin answering the requirements immediately rather than leaving collection as a manual follow-up. Read `references/feedly-integration.md` for the tool-routing table and operating rules before running anything.

Run this stage in two steps.

**Step 1: Assess, route, and get sign-off.** For each fully enriched requirement (candidates are excluded), read its EEIs, not just its headline, and classify what it is asking for: named-actor attribution, technique or TTP, malware or targeting relationships, vulnerability or CVE, sector or region landscape, base rate or scale, or current-activity grounding. Select the single best-fit Feedly tool for that class, plus at most one pivot where a relationship expansion is warranted; do not run every tool against every requirement. Present the routing plan in the chat as a table and stop for the user to verify and approve it. Every row must show the requirement code together with its full requirement question (not the code alone), the requirement's EEIs, what the lookup will help answer, and the tool chosen. Do not run any lookups until the user OKs the plan.

**Step 2: Collect, report, and offer to enrich.** Once the user approves the plan, run the lookups. Resolve names to entity IDs with `search_entities` before entity-based tools, and match each tool's time period to the timeframe stated in the requirement. Then present, in the chat, both the approved routing plan and the findings. Format the findings as parent bullets with indented sub-bullets under a subheading per requirement, where the subheading is the requirement code plus its full requirement question. Use a parent bullet for each point and indented sub-bullets for its supporting detail; for example, a parent bullet stating that attribution points to a named cluster, with each associated actor listed as its own indented sub-bullet beneath it. For each requirement, state the date range of the Feedly data used (for example, "Data range: past 12 months"), give the findings as nested bullets with inline article citations, and flag any EEI the lookups could not answer as an open collection gap rather than inventing an answer. Finally, offer to fold the results back into the three Stage 6 files in place: seed EEIs with the named entities found, add entity-backed sources to collection guidance, and revise criticality where current activity justifies it. Update the files only if the user accepts.

If the connected Feedly MCP also exposes a feed-creation tool, additionally offer, after reporting, to create one monitoring feed per requirement. Confirm feed names and logic before creating anything, and never create more feeds than requirements.

## Worked example

**Input (vague ask):** "Our CISO came out of a board meeting and wants to know how worried we should be about NPM supply chain attacks."

**Stage 1 read-back:** Stakeholder is the CISO, audience is likely the board. The decision is probably about software supply chain risk posture and potential control investment, but the timeframe, the trigger for the ask, and the organization's actual NPM exposure are unclear.

**Stage 2 questions asked:** What decision is leadership weighing (control investment, risk acceptance, awareness only)? When does the CISO report back? How exposed is the engineering practice to NPM?

**Sample output (abbreviated):**

> **IR-2026-021 (Enduring, GIR)**
> *Requirement:* Which threat actors and campaigns have compromised NPM packages to target enterprises in the past 12 months?
> *EEIs:* Campaigns with confirmed enterprise victims, past 12 months; attribution confidence per campaign; opportunistic versus sector-targeted pattern.
> *Collection guidance:* npm/GitHub security advisories and the GitHub Advisory Database (free); vendor research on registry compromises (Socket, ReversingLabs, Snyk, Sonatype, free); CISA advisories (free); OSV.dev and the OpenSSF malicious packages repository (free).
> *Success criteria:* CISO can name the campaigns active against the NPM ecosystem and state whether targeting appears opportunistic or directed at our sector, in board-ready language.
> *Criticality:* High (decision impact 4, time sensitivity 3, stakeholder breadth 3, feasibility 5; average 3.75).

> **IR-2026-022 (Enduring, GIR)**
> *Requirement:* Which techniques are being used to compromise NPM packages (account takeover, typosquatting, dependency confusion, malicious install scripts) in the past 12 months?
> *EEIs:* Prevalence of each technique in observed incidents; emerging techniques outside the known categories; techniques observed defeating common registry controls.
> *Note:* Split from IR-2026-021 because "who is attacking" and "how they compromise packages" are two facts, collected from different sources and answered on different timelines. The parenthetical enumerates categories of a single fact; it does not make the question compound.

> **PIR-2026-007 (Priority)**
> *Requirement:* Is there evidence from the past 12 months of malicious NPM packages defeating controls equivalent to ours (lockfiles, registry pinning, SCA scanning)?
> *Note:* Time-bound to the next board cycle; expires once the board endorses a posture. The board's actual question ("how worried should we be?") is a risk judgment, not an intelligence requirement; this PIR collects the control-efficacy evidence that judgment needs, and the requirement set states that the worry level is the CISO's synthesis of IR-2026-021, IR-2026-022, and this evidence.

> **Candidate requirements (not enriched):**
> *At which points do NPM packages currently enter our products and build pipeline?* Valid and collectible from internal sources, but deprioritized to keep the initial set focused; promote if the team has capacity to task engineering sources.

## Tone and standards

- Every requirement must pass the five Stage 4 quality tests: singular, atomic, decision-centric, time-bound, and answerable with CTI. One compound or unanswerable question undermines the credibility of the whole set.
- Use words of estimative probability ("likely", "there is evidence suggesting", "we assess with moderate confidence") rather than absolutes. This is standard CTI tradecraft and the output should model it.
- Never fabricate threat data. If the user wants current threat actor information populated into the requirements, use available search tools or state clearly that examples are illustrative.
- Keep the output scannable and concise: tables, short paragraphs, bolded key terms, and no padding. If a field can be said in eight words, do not use twenty.
