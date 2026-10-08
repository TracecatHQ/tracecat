# Financial exposure bands

This file translates one OWASP factor, Financial Damage, into money bands
sized to the organization. It is a lookup, not a loss model. Follow it
exactly.

## 1. What the band is

Financial Damage is already a money scale in OWASP wording: less than the cost
to fix the vulnerability 1, minor effect on annual profit 3, significant
effect on annual profit 7, bankruptcy 9. Those four anchors get currency
ranges from one of two places, in this order of preference: the organization's
own loss bands, where it already has them, or the table in item 2a, applied to
the currency and annual profit bracket the user gives in item 2. The skill
reports which band the threat sat in at the pre-action baseline, which band it
sits in at Current residual risk, and which band it sits in at Target residual
risk. It does not price the threat and it does not build a loss estimate.

### 1a. Read the OWASP wording literally

The wording decides what the band means. Two of the four anchors are expressed
as an effect on annual profit. So the band is the effect on annual profit this
organization would expect if this threat were realised. It is conditional on
the threat happening. It is not a per event cost, it is not an expected annual
loss, and no likelihood, frequency or probability is applied to it anywhere.
Use the phrase "if realised" every time the band appears above the appendix,
and never the phrase "per year" or "one occurrence".

### 1b. Bands sized from a figure that is not profit

Where the organization has no profit figure, the bands are derived from
annual revenue or operating budget instead, per item 2d. Those are **not
profit-impact bands** and must never be called that. Call them
**organization-size proxy bands**, name in Appendix F which figure they came
from, and keep the "effect if realised" framing without the word profit. A
public body, a charity, a pre-profit company and a cooperative all reach the
scale this way, and calling the result a profit impact would misdescribe every
one of them.

## 2. Getting the bands

If the organization's own loss bands were supplied at Step 0, use them and
skip to item 2b. An organization's own published bands always beat a derived
set. If they were not supplied, and the org context does not contain them,
derive the bands from the organization's annual profit. Ask these two
questions once, both in the same batch, using the multiple-choice question
tool, before the Financial Damage question is put. They do not count toward
the four questions in a batch. Do not ask either of them twice.

**Question 1, currency:**

"What currency does your organization report in?"

Offer the three currencies most plausible for the user's country or region as
the options, for example US dollar, euro, pound sterling. Let the tool add its
own Other for anything else. Do not supply an Other of your own.

**Question 2, annual profit:**

"Roughly what is your organization's annual profit? I use this to size the
OWASP money anchors to your organization. I do not need an exact figure and I
am not going to price the threat."

- under 1M
- 1M to under 10M
- 10M to under 100M
- 100M and above

Write the option labels in the currency chosen at question 1. Let the tool add
its own Other, and state in the question text that "1B and above" and an exact
figure are both available through it.

Four brackets are offered because the tool allows four. The ladder has five
rows, and the fifth is reached through Other. Do not drop a row from the item
2a table to make it fit.

### 2a. The band table

Turn the answer into four bands using this table and nothing else. Read the
row. Do not recalculate it. The figures are numerals applied in the currency
the user chose. Never convert a figure between currencies and never apply an
exchange rate.

| Bracket selected | Working figure | less than the cost to fix [1] | minor effect [3] | significant effect [7] | bankruptcy [9] |
|---|---|---|---|---|---|
| under 1M | 100k | under 1k | 1k to under 10k | 10k to under 100k | 100k and above |
| 1M to under 10M | 1M | under 10k | 10k to under 100k | 100k to under 1M | 1M and above |
| 10M to under 100M | 10M | under 100k | 100k to under 1M | 1M to under 10M | 10M and above |
| 100M to under 1B | 100M | under 1M | 1M to under 10M | 10M to under 100M | 100M and above |
| 1B and above | 1B | under 10M | 10M to under 100M | 100M to under 1B | 1B and above |

The working figure is the floor of the bracket the user selected, so the bands
are the most conservative that bracket supports. Each boundary is one percent,
ten percent and one hundred percent of the working figure, which is why every
row is the same shape moved by one decade.

**The bottom row has no floor.** "Under 1M" is open below, so 100k is used as
a working figure rather than read as a floor, and Appendix F must say that
plainly: the bands for this bracket are a proxy sized one decade down, not a
figure the organization gave. Prefer the exact-figure path at item 2c for any
organization in this bracket.

A sub-1M row exists because 1M to 10M is already a high annual profit for a
great many organizations, and small and medium businesses, particularly
outside the United States and Canada, cluster below it. Without this row every
one of them lands in a bracket sized for a business ten times larger.

Three things about this ladder have to be stated in Appendix F rather than
left for the reader to work out:

- The significant band closes at one hundred percent of the working figure,
  because a loss that consumes a full year of profit is where the OWASP
  wording stops calling the effect significant. The bankruptcy band is open
  ended above that.
- "Less than the cost to fix the vulnerability" is not a share of profit in
  OWASP's wording. One percent of the working figure is used as a proxy so
  that all four anchors sit on one scale. Say that plainly. Do not imply OWASP
  defined it that way.
- The bands are an order of magnitude wide because the OWASP scale has four
  rungs and movement is capped at one option. Precision beyond one significant
  figure would be false precision.

### 2b. Check the band set before you use it

Where the bands came from the user directly or from org context, confirm that
every band carries an explicit currency, that the four increase monotonically,
and that they do not overlap. If any check fails, quote the bands back in one
line, ask the user once to confirm or correct, and if that is not resolved
report scores only. That single clarification is not a second ask under item
2. Bands read from the item 2a table are ordered, non overlapping and single
currency by construction, so record that in Appendix F and check only that the
currency from question 1 carried through to every band.

Print the four bands in one line before the Financial Damage question, so the
user can see what they are about to answer against. Do not ask a separate
question to confirm them. The Financial Damage question carries the same bands
in its option labels under item 3, and answering it is the confirmation.

### 2c. Every bracket spans a decade, so offer to narrow it once

An organization at 1.1M and one at 9.9M select the same bracket and get the
same bands, which are sized for the first of them. Offer the refinement once,
in the same line that prints the bands: "These are sized from the bottom of
the bracket you chose. Give me an approximate figure and I will size them to
it." If the user gives one, apply item 2d's exact-figure ladder. If they
decline or ignore it, proceed and state in Appendix F that the bands are the
most conservative the bracket supports, and that an organization at the top of
the bracket would size them one decade higher. Do not ask again.

### 2d. What comes back through Other

Four things can come back through Other, and each has a fixed response:

- **An exact profit figure.** Use it in place of the bracket floor and apply
  the same ladder: under one percent, one to under ten percent, ten to under
  one hundred percent, then the figure itself and above. Round every boundary
  to one significant figure.
- **No profit figure exists**, for example a public body, a charity, a
  cooperative or a pre-profit company. Accept annual operating budget or
  annual revenue, apply the same ladder, name in Appendix F which figure the
  bands were derived from, and call the result organization-size proxy bands
  per item 1b. Never silently treat revenue as profit.
- **The user does not know, would rather not say, or asks to skip.** Offer the
  sector anchor at item 2e once. If that does not resolve it, omit the
  financial exposure section and Appendix F per item 9, move on without
  comment, and do not ask again.
- **The user volunteers the organization's own bands.** Use those instead of
  the derived set and record the source under item 4.

### 2e. The sector anchor, for users who cannot see the figures

Many of the people running this skill are analysts whose role does not reach
the organization's financial detail, and a blank question about annual profit
or loss impact is unanswerable for them rather than merely inconvenient. Where
the user's decision authority at Step 0 is "recommend only" or "CTI only", or
where they answer that they do not know, offer an anchor to confirm rather
than a figure to produce:

"For an organization in [sector] of roughly this size, a significant effect on
annual profit would typically sit in [band]. Does that look about right for
yours, or would you put it higher or lower?"

Offer it once. A confirmed anchor is recorded in Appendix F as source
"analyst estimate, sector anchor confirmed by the user", and the financial
exposure section states in one clause that the bands are unvalidated. An
anchor the user cannot confirm produces no bands: Financial Damage is then
scored as an estimate under item 3b, and the band is omitted.

Never present the anchor as a benchmark figure, a published breach cost, or a
number derived from anything other than the ladder in item 2a. It is a
starting point for the user to correct, and its only value is that correcting
a number is easier than inventing one.

## 3. Carry the bands into the Financial Damage question

Whether the bands were supplied or derived, carry them into the Financial
Damage question. Each option label keeps its OWASP score and gains the
matching band, for example "significant effect on annual profit [7], 100k to
under 1M". The user is then answering in money sized to their organization,
which is the point of collecting the currency and the profit bracket at all.

### 3a. When the band may be reported

The band may only be reported where the user answered the Financial Damage
question directly against those banded option labels. If Financial Damage was
estimated, inferred from org context, or answered before the bands existed,
omit the financial exposure section and Appendix F and record why in Appendix
E. Bands supplied or derived after that question has been answered are
accepted only if you re-put the one question with banded labels and the user
re-confirms. That re-put is not a second ask under item 2.

### 3b. Financial Damage as a declared estimate

Where no band can be produced, Financial Damage is still scored, because the
business impact mean needs four factors. Score it as the most defensible
option against the OWASP wording, tag it `Analyst input (estimated)` or
`Analyst input (unconfirmed)` per its actual basis, and expect the business
impact figure to carry a Provisional label as a result. State in Appendix E
that the band was omitted and what would be needed to produce one. A scored
Financial Damage factor without a band is normal and is not an error. A band
without a directly answered banded question is.

## 4. Record where the bands came from

Record the source as one of: published risk appetite or ERM framework, finance
or risk team provided, derived from the annual profit bracket the user
selected using the item 2a table, derived from an exact figure the user gave,
derived from revenue or operating budget as an organization-size proxy,
analyst estimate with a sector anchor confirmed by the user.

An analyst estimate is reported as such, and the financial exposure section
states in one clause that the bands are unvalidated. A derived set is reported
as such, and the financial exposure section states in one clause that the
bands are sized from the figure the user gave and are not the organization's
own published bands.

## 5. Open ended bands

An open ended band cannot be reported as a figure. Where the bands were
supplied, print the open ended one as the user gave it and add the clause
"upper bound not stated by the organization". Where they were derived, the
bankruptcy band is open ended by construction, so print it as the working
figure and above and add the clause "open ended, no upper bound is defined".
In neither case substitute a representative number of your own, and do not ask
again.

## 6. What you may never do to a band

Read all three bands off the Financial Damage row in Appendix A, one per risk
state. Nothing else feeds them. Never do any of the following:

- multiply or divide a band by a score, likelihood, frequency, probability,
  count of events or count of actions
- annualise, or report any figure per year
- collapse a band to a single point figure
- average, interpolate between, or blend bands
- derive a band from any factor other than Financial Damage
- call the difference between two bands savings, cost avoided, loss
  prevented, value delivered, ROI or return

A band is the effect on annual profit this organization would expect if this
threat were realised at the level the user selected. It is not probability
weighted, and it is not money the organization now has.

## 7. Reporting the three bands

The financial exposure section carries one line per risk state, each with "if
realised", then the movement line below.

If Financial Damage did not move, say so in one line and give the unchanged
band. This is the common case and it is not a failure. Most defensive action
moves Opportunity and Intrusion Detection, which are likelihood factors and
carry no money. Report that honestly rather than reaching for a different
number or implying the band moved.

Three branches need exact wording:

- Where a clamp finding under item 10a of `mitigation-rules.md` concerns
  Financial Damage, write "Financial Damage did not move down, and [N]
  action(s) were found to increase it, see Appendix A", because the clamp must
  never be reported as stability.
- Where no factor moved at all, write "Financial Damage did not move, and no
  other factor moved either".
- Where the band moved only at Target residual risk and not at Current
  residual risk, write "Financial Damage is unchanged today, and moves to
  [band] once the committed action lands on [date]". Never print a single
  moved band without saying which state it belongs to.

### 7a. When it did move

Where the band did move, state which state it moved in, how many actions moved
Financial Damage, and add the clause "movement is capped at one band".

## 8. Attribute jointly

One sentence naming the teams that implemented the actions, and stating that
CTI supplied the intelligence and the decision it changed. Never present the
movement as CTI's alone, and never present a CTI product as the thing that
moved the band.

## 9. When there are no bands at all

If no bands were supplied and the user declined the currency question, the
profit question and the item 2e anchor, omit the financial exposure section
and Appendix F entirely and add one line to Appendix E naming what would be
needed to produce them. Do not substitute an industry benchmark, a published
breach cost average, a figure inferred from the organization's sector or
headcount, or any figure the user did not give you.
