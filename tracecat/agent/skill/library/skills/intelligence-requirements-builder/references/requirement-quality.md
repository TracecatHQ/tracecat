# Writing Requirements That Pass the Quality Tests

Read this before decomposing any stakeholder ask into requirements. A requirement set lives or dies on the quality of its questions: a compound question cannot be cleanly answered, an unanswerable question cannot be collected against, and either one in front of a seasoned intelligence professional damages the credibility of the whole set.

## The standard

According to CTI best practices from both private sector and military sources, a strong intelligence requirement has these qualities:

1. **Singular.** It focuses on one question and only one question.
2. **Atomic.** It is specific to a particular fact, event, or activity.
3. **Decision-centric.** It provides the intelligence required to support a single decision.
4. **Timely.** It captures the timeframe for usable intelligence.

This skill adds a fifth test that commercial CTI practice makes necessary:

5. **Answerable with CTI.** The question must be answerable through collection and analysis of observable evidence. If no combination of sources could ever produce the answer, it is not an intelligence requirement, however important the underlying concern.

## Applying the tests

### Singular: split on the "and"

If a question joins two distinct asks with "and", it is two requirements wearing one ID. Split it. The tell is that the two halves would be collected from different sources, answered on different timelines, or assigned to different analysts.

**Fails:** "Which threat actors and campaigns are compromising NPM packages to target enterprises, and through what techniques?"

This asks two things: who is attacking, and how. The "who" is answered from attribution reporting and campaign tracking; the "how" is answered from technical analysis of compromised packages. They are separate facts with separate collection paths.

**Passes (as two requirements):**

- "Which threat actors and campaigns have compromised NPM packages to target enterprises in the past 12 months?"
- "Which techniques are being used to compromise NPM packages (account takeover, typosquatting, dependency confusion, malicious install scripts) in the past 12 months?"

Note the second question's parenthetical: enumerating the known categories of a single fact is fine. It is one question (what techniques?) with its answer space sketched, not two questions joined together.

### Atomic: a fact, not a topic and not a judgment

The requirement must point at something specific enough that an analyst knows when it has been established. Watch for abstract nouns like "overlap", "relevance", "exposure", or "posture" sitting where the fact should be; these are usually analytical judgments built on top of facts, and the requirement should target the facts.

**Fails:** "Which NPM compromise techniques overlap with our dependency footprint and build pipeline exposure?"

Three problems in one sentence. It is compound (footprint *and* pipeline). "Overlap" is a judgment, not a collectible fact. And it silently bundles an external question (what techniques exist) that belongs to another requirement.

**Passes:** "At which points do NPM packages enter our products and build pipeline?"

This is singular, atomic, and answerable from internal sources (dependency manifests, SBOMs, CI/CD configuration). The overlap analysis still happens, but it happens when an analyst lays this answer alongside the techniques requirement; it is the analytical product, not the requirement.

### Decision-centric: one requirement, one decision

Every requirement links back to a decision identified at intake. If a requirement supports no decision, it is trivia and should be cut. When the ask spans several stakeholder questions, the set can serve more than one decision; each requirement must still map cleanly to exactly one of them, recorded in its linked-decision field, so a reader can trace which decision it serves.

### Time-bound: the timeframe lives inside the question

Put the timeframe in the requirement text itself, not only in metadata fields. "Which operations have claimed victims in our sector?" cannot be judged answered, current, or stale; "...in the past 12 months?" can. For PIRs, the timeframe is typically anchored to the decision date ("before the Q3 board decision").

### Answerable with CTI: collection cannot predict your future

The most common unanswerable pattern is the self-prediction question: a likelihood estimate about the requesting organization itself.

**Fails:** "How likely is a malicious NPM package to reach our production builds in the next 12 months, given current controls?"

No source can be tasked against this. It asks the team to predict the organization's own future, which is a risk judgment that blends threat evidence with internal knowledge of controls, engineering practice, and appetite. Writing it as a requirement sets the team up to either fail or fabricate.

The correct move is to decompose the judgment into the observable evidence it needs, then state explicitly that the final call belongs to the stakeholder. For the example above:

- "What proportion of organizations with comparable NPM usage have publicly reported a malicious package reaching their builds in the past 12 months?" (base rate, answerable from vendor annual reports and incident reporting)
- "Is there evidence from the past 12 months of malicious NPM packages defeating controls equivalent to ours (lockfiles, registry pinning, SCA scanning)?" (control efficacy evidence, answerable from technical incident write-ups)

The requirement set should then record, usually in the linked-decision field or an assumptions note, that the stakeholder's likelihood judgment is their synthesis of these answers. This is not a dodge; it is the correct division of labor between intelligence and risk management, and stakeholders respect it when it is stated plainly.

Other unanswerable patterns to catch and rewrite:

- **"Should we..." questions.** "Should we invest in supply chain controls?" is the decision itself, not a requirement. The requirements are the evidence the decision needs.
- **Unbounded hypotheticals.** "What would happen if we were targeted?" has no collection path. Reframe to observed consequences: "What operational impact have sector peers reported from incidents of this type in the past 24 months?"
- **Attacker mind-reading.** "Does [actor] intend to target us?" is rarely collectible for a single organization. Reframe to observable targeting evidence: "Is there evidence of [actor] conducting reconnaissance against, or claiming victims among, organizations in our sector and regions in the past 6 months?"

## Quick self-check before finalizing a set

Run every drafted requirement through these five questions. Any "no" means rewrite or split.

1. Does it ask exactly one question?
2. Does it target a specific fact, event, or activity (not a topic, not a judgment word)?
3. Does answering it support the single decision from intake?
4. Does the question text contain its own timeframe?
5. Could a named set of sources realistically produce the answer?
