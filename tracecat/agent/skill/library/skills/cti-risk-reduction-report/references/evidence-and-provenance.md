# Evidence, provenance and citation

A number that looks the same as every other number, but rests on a guess,
is worse than no number. This file fixes the vocabulary so the assessment
cannot hide how it knows what it claims.

## 1. The five basis values

Every row in Appendix A carries exactly one of these. They are the only
permitted values. Do not invent a sixth and do not leave a row unqualified.

| Basis | Means |
|---|---|
| `Reporting` | Scored from the source reporting, with a citation in the item 4 format. |
| `Analyst input (confirmed)` | A direct selection from a labelled OWASP option by the respondent. |
| `Analyst input (estimated)` | Inferred from a fact the respondent did confirm, but not itself a direct option selection. |
| `Analyst input (unconfirmed)` | The respondent could not answer. The value reflects a gap in their visibility, not an answer of any kind. |
| `Org context` | Read from organizational detail the user supplied at Step 0, with the supplied text quoted in the evidence cell. |

The distinction matters most inside analyst input. In testing, a single
carefully considered row was written as an unconfirmed estimate while seven
directly confirmed rows were left as bare "Analyst input", so a reviewer
scanning the table could not tell that seven rows sat on much firmer ground
than one. The taxonomy existed only in prose, only where it occurred to the
assistant to add it, and only in an appendix the numbers did not live in.
Enforcing the vocabulary in the column that carries the scores is the fix.

There is no basis value for your own inference. If the only support for a
factor is something you worked out rather than something the reporting said
or the respondent confirmed, the factor is `Analyst input (unconfirmed)` and
its evidence cell says what is missing. `Assistant inference` is not a basis
for a score.

## 2. Provenance of mitigation detail

This is the rule most likely to be broken without anyone noticing, and the
one that does the most damage when it is.

**A specific detail may appear in the assessment as something the
organization did only if the user supplied that detail.**

The failure mode is mechanical. You ask a clarifying question and, to make
it answerable, you illustrate it with specifics: "for example, blocking the
C2 addresses, or patching CVE-2023-27532". The user replies "yes, all done".
Those specifics were yours. If they now appear in Appendix C as actions
taken, the assessment asserts that named domains were blocked and a named CVE
was patched on no evidence whatever, and it reads exactly like an assessment
where the user listed them.

So:

1. Every action in Appendix C carries a **detail source** cell: `User
   stated`, `User file`, or `Reporting`. There is no fourth value. An action
   you cannot label with one of the three does not go in Appendix C.
2. Never promote your own example, illustration or proposed action into user
   input, whatever the user's agreement was attached to. Agreement to a
   category is agreement to the category.
3. Where the user confirmed a category, resolve it under Step 6b of SKILL.md
   or record it in Appendix D as category-level. Do not fill the gap.
4. Specifics that only ever existed in your own prompts, questions or
   proposals may appear in Appendix D as recommendations, clearly attributed
   to this assessment rather than to the organization, and nowhere else.

The verification checklist tests this directly, because arithmetic and rule
compliance checks do not catch it: a fabricated action can be perfectly
compliant with every movement rule and still be fabricated. Check the origin
of each scored claim, not just its arithmetic.

## 3. Wording an unconfirmed row

An unconfirmed factor records the limits of one person's visibility. It does
not record a finding about the organization.

A respondent who selects "I don't know" may not know whether the control is
strong, weak, or absent; whether a review exists at all; or who owns the
information. All of that collapses into a false finding if the evidence cell
is written as a negative fact.

- Wrong: "No network segmentation review has been performed."
- Right: "Respondent could not confirm segmentation posture. No
  organizational finding established. Owner to confirm: network team."

The pattern for every unconfirmed evidence cell: what the respondent could
not confirm, then the words "no organizational finding established", then
optionally who would know.

Where you separately asked who would know and got an answer, put the named
team or role in the cell and carry it into Appendix E as something a human
should confirm before the rating is used in a decision.

The same rule governs how the question is worded in the first place. See the
fallback wording rules in SKILL.md, which are upstream of this: if the option
the respondent selected did not assert a false fact, there is nothing false
here to write down.

## 4. Citation format

Every evidence claim from reporting carries a citation. One format, used
everywhere, so the citations can be checked.

Assign each source an identifier at Step 1, in the order they were supplied:
`S1`, `S2`, `S3`. List them once, in full, at the top of the appendix:

```
S1. "Title of the report", Publisher, YYYY-MM-DD, URL or file name.
```

Then cite inline, in Appendix A evidence cells and anywhere else a claim
rests on reporting, as:

```
[S1 §section or paragraph] "short evidence statement"
```

The short evidence statement is the quoted or closely paraphrased words the
score rests on, kept to a phrase. A citation that names a source but not a
location inside it cannot be checked, so give the section heading, the
paragraph, or the artifact-table row.

Where two sources support one factor, cite both. Where they disagree, cite
both, say which you scored from, and carry the disagreement into Appendix E.

## 5. Estimative language

Use ICD 203 terms for analytical judgments about the threat: almost
certainly, very likely, likely, roughly even chance, unlikely, very unlikely,
almost certainly not. Attach them to judgments, never to arithmetic. The mean
of four numbers is not "likely" 6.8. It is 6.8.

Do not stack an estimative term on a Provisional label. "Technical impact is
likely around 6.8, Provisional" says the same uncertainty twice in two
vocabularies. The label carries it.
