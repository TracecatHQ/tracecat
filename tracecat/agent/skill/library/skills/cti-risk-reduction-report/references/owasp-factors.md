# OWASP factor scales, scoring arithmetic and severity matrix

Read this file before scoring anything. The option values here are the only
permitted scores. Do not invent intermediate values and do not interpolate.

## 1. The sixteen factors

Every factor is scored 0 to 9 using these options exactly.

### LIKELIHOOD, threat agent factors

| Factor | Options and scores |
|---|---|
| Skill Level | no technical skills 1 \| some technical skills 3 \| advanced computer user 5 \| network and programming skills 6 \| security penetration skills 9 |
| Motive | low or no reward 1 \| possible reward 4 \| high reward 9 |
| Opportunity | full access or expensive resources required 0 \| special access or resources required 4 \| some access or resources required 7 \| no access or resources required 9 |
| Size | developers 2 \| system administrators 2 \| intranet users 4 \| partners 5 \| authenticated users 6 \| anonymous internet users 9 |

### LIKELIHOOD, vulnerability factors

| Factor | Options and scores |
|---|---|
| Ease of Discovery | practically impossible 1 \| difficult 3 \| easy 7 \| automated tools available 9 |
| Ease of Exploit | theoretical 1 \| difficult 3 \| easy 5 \| automated tools available 9 |
| Awareness | unknown 1 \| hidden 4 \| obvious 6 \| public knowledge 9 |
| Intrusion Detection | active detection in application 1 \| logged and reviewed 3 \| logged without review 8 \| not logged 9 |

### IMPACT, technical factors

| Factor | Options and scores |
|---|---|
| Loss of Confidentiality | minimal non-sensitive data disclosed 2 \| minimal critical data disclosed 6 \| extensive non-sensitive data disclosed 6 \| extensive critical data disclosed 7 \| all data disclosed 9 |
| Loss of Integrity | minimal slightly corrupt data 1 \| minimal seriously corrupt data 3 \| extensive slightly corrupt data 5 \| extensive seriously corrupt data 7 \| all data totally corrupt 9 |
| Loss of Availability | minimal secondary services interrupted 1 \| minimal primary services interrupted 5 \| extensive secondary services interrupted 5 \| extensive primary services interrupted 7 \| all services completely lost 9 |
| Loss of Accountability | fully traceable 1 \| possibly traceable 7 \| completely anonymous 9 |

### IMPACT, business factors

| Factor | Options and scores |
|---|---|
| Financial Damage | less than the cost to fix the vulnerability 1 \| minor effect on annual profit 3 \| significant effect on annual profit 7 \| bankruptcy 9 |
| Reputation Damage | minimal damage 1 \| loss of major accounts 4 \| loss of goodwill 5 \| brand damage 9 |
| Non-compliance | minor violation 2 \| clear violation 5 \| high profile violation 7 |
| Privacy Violation | one individual 3 \| hundreds of people 5 \| thousands of people 7 \| millions of people 9 |

## 2. Factors that get misread

Three factors were consistently scored against the wrong definition in
testing. Check these before scoring them, every run.

**Size** is the size of the threat-agent population: how many people could
plausibly mount this attack, expressed as the OWASP categories. It is not the
availability of attacker tooling, not the scalability of the campaign, not the
size of the victim pool, and not the sophistication of the operation. A
capable, well-resourced actor group operating a private toolkit is a small
population, and can score 2 or 5 while every other likelihood factor scores
high. A commodity exploit any internet user can run scores 9. If your
justification for a 9 mentions the tooling rather than the population,
you have scored the wrong thing. Tooling availability belongs to Ease of
Exploit and Ease of Discovery, which already carry it.

**Opportunity** is written from the defender's side: how much access or
resource an attacker needs to reach the target. Two readings can pull apart,
and the assessment must say which one it is using.

- *Internal opportunity:* what an attacker needs to reach systems this
  organization owns and can change.
- *External opportunity:* what an attacker needs to reach the surface this
  threat actually attacks, where that surface is not the organization's to
  change. A remote employee's home router, a customer's ISP-managed DNS
  resolver, a supplier's remote management platform.

Score the factor against the surface this threat attacks, which is what
OWASP measures, then record in Appendix H whether that surface is inside the
organization's reach. An action that only touches internal opportunity does
not move a score set by an external surface. See item 7 of
`mitigation-rules.md`.

**Awareness** is the threat's visibility to defenders and the wider
community, not the organization's own awareness of it. Reading a report does
not move it.

## 3. Which factors come from where, and carrying confirmation through

Six factors are scored from the threat reporting: Skill Level, Motive, Size,
Ease of Discovery, Ease of Exploit, Awareness. Where the reporting does not
support one of them, say so and carry it into the questions rather than
guessing.

Ten factors depend on the organization, not the threat, so the reporting
cannot answer them: Opportunity, Intrusion Detection, Loss of
Confidentiality, Loss of Integrity, Loss of Availability, Loss of
Accountability, Financial Damage, Reputation Damage, Non-compliance, Privacy
Violation. Score them from the user's org context if the answer is there,
otherwise ask.

Every factor carries a basis value from item 1 of
`evidence-and-provenance.md` from the moment it is scored, and that value
travels with it into the arithmetic. Do not discard it at the mean.

## 4. Scoring arithmetic

1. Calculate three figures, each rounded to one decimal place:
   - Likelihood = the mean of the eight likelihood factor scores.
   - Technical impact = the mean of the four technical impact scores.
   - Business impact = the mean of the four business impact scores.

   Do not average technical and business impact together. OWASP keeps them
   separate and treats business impact as the more important of the two.
   Round half up, so 6.25 becomes 6.3 and 7.25 becomes 7.3. Round once, at
   the end. Never round a factor score, and never round a part-calculated
   mean.

2. **Label each sub-score with its confirmation profile.** Count the basis
   values of the factors that fed it. Where every input is `Reporting`,
   `Analyst input (confirmed)` or `Org context`, the sub-score carries no
   label. Where any input is `estimated` or `unconfirmed`, the sub-score is
   labelled **Provisional** with the counts:

   > Technical impact: 6.8 / 9, Provisional (1 of 4 unconfirmed, 2 of 4
   > estimated)

   The label goes wherever the figure goes, including the BLUF and the
   rating table, not into a footnote.

   The reason is that OWASP produces a clean-looking decimal from ordinal
   bucket choices, so a mean built from one guess and three answers prints
   identically to a mean built from four answers. A reader comparing
   technical impact 6.8 against business impact 6.5 has no way to know one
   rests on much firmer ground unless the figure says so.

   The label travels with the sub-score it affects, not as a blanket caveat
   on the overall rating, because in most runs some sub-scores are fully
   confirmed and others are not. Tainting all three with one caveat hides
   which figure is soft.

   The label is not a comment on OWASP's own rounding. 6.75 becoming 6.8 is
   inherited from the framework and happens even when every input is
   confirmed, and that is not a defect.

3. Convert each figure to a level: 0 to below 3 is LOW, 3 to below 6 is
   MEDIUM, 6 to 9 is HIGH.

4. **Read overall severity off the matrix, from business impact, and never
   switch bases silently.**

   | Impact | Likelihood LOW | Likelihood MEDIUM | Likelihood HIGH |
   |--------|----------------|-------------------|-----------------|
   | HIGH   | Medium         | High              | Critical        |
   | MEDIUM | Low            | Medium            | High            |
   | LOW    | Note           | Low               | Medium          |

   Severity is always read from likelihood and business impact. Business
   impact is collected directly from the organization, so it is the grounded
   input and it takes precedence.

   - Where all four business impact factors are `confirmed`, `Reporting` or
     `Org context`, report the severity plainly.
   - Where any business impact factor is `estimated` or `unconfirmed`, report
     the same severity word marked **provisional**, and add one line giving
     the technical-impact matrix result as a comparison: "Read from technical
     impact instead, the severity would be [word]."

   Do not substitute technical impact for business impact as the severity
   basis. A single soft factor should not flip the basis of the whole rating,
   because a rating whose basis moves between runs cannot be compared to the
   last one, and comparability is most of the point of re-running this.

5. Report the scores and the severity word separately and label them clearly.
   Present it as "Likelihood 6.4 / 9, technical impact 7.3 / 9 Provisional (1
   of 4 estimated), business impact 7.1 / 9, Severity: Critical", and state
   which impact figure drove the severity. Do not blend scores into a single
   composite number. OWASP does not define one, and inventing one makes the
   rating harder to defend.

6. Do all of this three times: once for the pre-action baseline, once for
   Current residual risk, once for Target residual risk. The confirmation
   profile is recalculated for each state, because an action can convert an
   unconfirmed factor into a confirmed one.

7. Show the arithmetic in the appendix so any score can be traced back to a
   factor, a basis and a source.

8. The financial exposure band is not part of this arithmetic. It never
   enters a mean, a level or the matrix. It is read off one factor after the
   scoring is complete.

## 5. Four-option handling for the question tool

Where an OWASP factor has five options and the question tool allows four:

- Loss of Confidentiality: merge the two options that both score 6 into one
  option reading "minimal critical, or extensive non-sensitive [6]".
- Loss of Availability: merge the two options that both score 5 into one
  option reading "minimal primary, or extensive secondary [5]".
- Loss of Integrity: offer 1, 3, 7 and 9, and state in the question text that
  "extensive but only slightly corrupt data [5]" is available via Other.
- Skill Level, if the reporting could not support it: offer 1, 3, 6 and 9, and
  state that "advanced computer user [5]" is available via Other.
- Size, if the reporting could not support it: merge the two options that both
  score 2 into one reading "developers or system administrators [2]", then
  offer 4, 6 and 9, and state that "partners [5]" is available via Other. Put
  the population definition from item 2 in the question text, because this is
  the factor respondents most often answer about tooling instead.

Every one of these questions also carries a fallback option worded under the
fallback rules in SKILL.md, which registers lack of knowledge only and never
asserts a control state. The fallback is in addition to the OWASP options,
and an answer against it produces an `unconfirmed` basis value.
