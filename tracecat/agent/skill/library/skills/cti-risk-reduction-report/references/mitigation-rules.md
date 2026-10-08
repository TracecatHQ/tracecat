# Mitigation rules: how a factor is allowed to move

The deterministic parts of this method are reliable. The judgment layer is
where it fails, and it fails in one direction: toward more apparent risk
reduction. Every rule in this file exists to constrain that. When a rule and
a plausible-sounding narrative disagree, the rule wins.

## 1. Three states, not two

Produce three ratings. Each is a full OWASP rating in its own right, scored
against the same sixteen factors and read off the same matrix.

| State | Actions counted |
|---|---|
| **Pre-action baseline** | No assessed actions. The estate as the reporting alone would rate it. |
| **Current residual risk** | Implemented and verified actions only. |
| **Target residual risk** | Current, plus unverified implementations and dated commitments falling inside the assessment horizon. |

A target date establishes accountability. It does not establish control
effectiveness. Combining implemented and planned work into a single figure
labelled "residual risk" invites a CISO or a board to read forecast control
state as current control state, and the size of that error is not small: an
assessment carrying several future-dated reductions can publish a residual
rating a full severity level better than the organization's actual position
today.

So the word "residual" never appears in the output unqualified. It is always
"Current residual risk" or "Target residual risk". If only two states can be
shown for space reasons, the second is named **projected residual risk**,
never residual risk. The financial exposure section carries the same three-way
split.

Current residual risk is the figure that belongs in board reporting as the
organization's position. Target residual risk is only ever quoted alongside
its horizon and the commitments behind it.

## 2. The status ladder

Five statuses. Each action carries exactly one. Do not improvise a sixth. The
dated-commitment status splits in the table below according to whether its
date falls inside the assessment horizon, which is a property of the date and
not a separate status.

| Status | Current residual | Target residual | Recorded in |
|---|---|---|---|
| Implemented and verified | Counts | Counts | Appendix C |
| Implemented, not yet verified | Does not count | Counts | Appendix C, flagged |
| Planned and committed, dated, inside horizon | Does not count | Counts | Appendix C, with date |
| Planned and committed, dated, beyond horizon | Does not count | Does not count | Appendix D |
| Conditional: planned, no date | Does not count | Does not count | Appendix D |
| Not planned | Does not count | Does not count | Appendix D |

**Verified** means someone confirmed the control is in place and working:
the detection fired in a test, the patch was confirmed deployed against an
asset inventory, the block was checked against the indicator. A user saying
an action is done is an implementation claim, not a verification claim. Where
the user has not distinguished the two, ask once at Step 6d and take
"Implemented, not yet verified" as the answer if they cannot say.

**Conditional** exists so the skill can show potentially reducible risk
without claiming it as residual. A conditional action is named in Appendix D
with the factor it would move and the change it would produce, and the
Appendix D header line says how many options of movement are sitting in
conditional status. That is honest, and it is useful to whoever owns the
control.

A commitment with no date is not a commitment. It is Conditional. Do not
describe it as Not planned, because that is not what the user said.

## 3. Which factors can move, and what moves them

**3a.** A factor may only move if a specific named posture action changes what
that factor's OWASP definition measures. Name the action next to every
change. A CTI action that produced intelligence, a rating or a recommendation
moves nothing on its own.

**3b.** These six factors describe the threat and the outside world, not your
defences. Never move them: Skill Level, Motive, Size, Ease of Discovery, Ease
of Exploit, Awareness. Holding them constant is what keeps the delta honest.

**3c.** These are the factors defensive action can move, and what moves them.
This table is the authority. An action that is not in the right-hand cell for
a factor cannot move that factor, however reasonable the argument sounds.

| Factor | What moves it |
|---|---|
| Opportunity | patching, removing exposure, segmentation, MFA, blocking infrastructure, virtual patching or WAF rules |
| Intrusion Detection | new detection content, log source onboarding, EDR coverage, a completed hunt, alerting that is actually reviewed |
| Loss of Confidentiality | DLP, encryption, access review, credential rotation, reducing what the targeted system can reach |
| Loss of Integrity | immutable or offline backups, change control, code signing, file integrity monitoring |
| Loss of Availability | tested failover, offline backups, redundancy, a rehearsed recovery playbook |
| Loss of Accountability | better logging and forensic retention, which makes the attacker more traceable and moves the score down |
| Financial Damage | cyber insurance, fraud controls, payment verification |
| Reputation Damage | a tested crisis communications plan, pre-agreed holding statements, a customer notification process |
| Non-compliance | regulator notification readiness, closing a control gap tied to this threat |
| Privacy Violation | data minimisation, shorter retention, tokenisation, reducing the records held |

Faster containment is deliberately absent from the Financial Damage row. It is
bought by detection work, which is already counted against Intrusion
Detection, and counting it twice is what makes a money figure indefensible.

Customer notification, advisories and crisis communications move **Reputation
Damage**. They do not move Financial Damage, Loss of Accountability, or
Non-compliance. This has been the single most common misattribution in
testing, so check it explicitly.

## 4. The attribution gate

Before attributing any score change to any action, run this gate. It is a
required step, not a guideline, and it happens before the arithmetic.

1. Name the action and the factor you intend to move.
2. Find the factor's row in the table at item 3c. Read the right-hand cell.
3. Does this action appear there, as itself and not as an analogy?
   - **No:** do not move the factor. Record the attempted attribution in
     Appendix D with the reason "action is not a permitted mover for this
     factor", naming the factor it does move if there is one.
   - **Yes:** continue to item 5 to size the move.
4. Check the action has not already been counted against another factor in a
   way item 8 forbids.

Most scoring errors in testing were not missing rules. The rules were
present and the table was not consulted. The gate exists because "verify
before attributing" has to be a step with an output, not an intention.

## 5. Sizing the move

**5a. One option is the default, and it is almost always the answer.** One
action moves a factor by one option on the OWASP scale. Two weak actions do
not add up to a big move. Three weak actions do not either.

**5b. Anything larger requires the elimination test.** A move of more than one
option is permitted only where the action removes the enabling condition
itself, across the estate in scope, and not merely one instance of it.

- Patching the vulnerable component out of the entire estate removes the
  condition. Opportunity may reach 0.
- Blocking the infrastructure named in one report removes known instances.
  The underlying opportunity, for example an adversary-in-the-middle phishing
  capability or a commodity RMM abuse path, is untouched. This moves
  Opportunity by at most one option and may never take it to 0.
- Decommissioning the targeted system removes the condition. Monitoring it
  does not.

Write the elimination justification into the Appendix C row. If you cannot
write one sentence naming the condition and why it is now absent rather than
reduced, the move is one option.

**5c. Large single-option gaps need the same justification.** The OWASP scales
are not evenly spaced. Some single-option moves are worth four points or more,
which makes them larger in effect than most multi-option moves and lets a
weak action produce a very large reduction while technically obeying 5a. Every
move in this table requires an elimination-grade justification under 5b, even
though it is only one option:

| Factor | Move | Points | Typical bad justification |
|---|---|---|---|
| Opportunity | 4 to 0 | 4 | Blocking indicators from one report |
| Intrusion Detection | 8 to 3 | 5 | A detection written but not reviewed in practice |
| Loss of Confidentiality | 6 to 2 | 4 | An access review that narrowed one system |
| Loss of Availability | 5 to 1 | 4 | Backups that exist but were never restore-tested |
| Loss of Accountability | 7 to 1 | 6 | Additional logging without confirmed retention and coverage |
| Financial Damage | 7 to 3 | 4 | Insurance whose coverage against this scenario is unconfirmed |
| Reputation Damage | 9 to 5 | 4 | Client advisories or a notification process |

The Reputation Damage row deserves naming. Advisories, holding statements and
notification processes improve how the organization communicates during an
incident. They do not materially change the reputational consequence of the
incident itself. A managed service provider compromised through its own
remote management platform suffers brand damage whether or not it notified
clients promptly. Move it one option only where the crisis communications plan
is tested and the underlying scenario is genuinely less brand-damaging as a
result, and expect not to move it at all in most runs.

The Loss of Accountability row is the other one to watch: 7 to 1 is a
six-point move on a three-option scale, so it can single-handedly carry a
technical impact figure. It requires confirmed log coverage of the specific
activity this threat performs, plus confirmed retention long enough to
investigate it.

**5d. Financial Damage is capped absolutely.** It may never move by more than
one option in total, across every action combined, because with loss bands
attached one option can be two orders of magnitude of money. The elimination
clause in 5b does not apply to it. Where more than one action would move it,
apply one option and list the rest in Appendix C as supporting.

## 6. The challenge pass

Before writing the output, re-read every move you have made and run this
pass. Record its result in Appendix E.

1. Count the total options moved across all factors, and count how many of
   those moves are large-gap moves from the table at 5c.
2. For each move, ask: **would a reviewer who wanted the residual figure to
   be higher accept this?** Not "is it defensible", which is a low bar, but
   "does the evidence compel it".
3. Where the answer is no, reduce the move to one option, or to no move, and
   say so in the Appendix C row.
4. Recalculate. State in Appendix E how many moves the challenge pass
   reduced and by how many options in total.

Every disputed judgment in testing pushed in the same direction, toward a
larger delta. None pushed the other way. That is a systematic bias, not
scatter, so the correction has to be a deliberate step rather than a hope
that each individual call is made carefully.

If the challenge pass reduces nothing across a run with several large moves,
that is a signal you have not run it properly.

## 7. Factors outside reach

Some factors cannot be moved by this organization against this threat,
whatever the team does. A threat whose attack surface is a remote employee's
home router, a customer's ISP-managed DNS, or a supplier's platform sits
partly or wholly outside the estate. OWASP's Opportunity factor is written
from the defender's side, as how much access an attacker needs, but the
surface that grants that access may not be the defender's to change.

Decide this at Step 5, before you see the mitigation list, and record it in
Appendix H with three columns: the factor, why it is outside reach, and who
would have to act.

Where a factor is outside reach, the assessment must say so at the point the
flat score appears. A flat factor caused by a surface nobody in the
organization controls is a scoping finding. A flat factor caused by nobody
having done the work is a delivery finding. They read identically in a table
and mean opposite things, so the appendix has to separate them.

Where the assessment distinguishes internal from external opportunity, say
which one the score and any movement refer to. Do not move Opportunity on the
strength of an action that only touches the half the organization does not
own.

## 8. One action, one business impact factor

An action moves exactly one business impact factor, and nothing may be counted
against both a likelihood factor and a business impact factor.

Where an action could be argued into two, name the one it moves as primary,
and record the second in the same Appendix C row by writing the second factor
in the Score change cell as "Secondary, recorded, not scored". Do not give an
action two rows.

A tested crisis communications plan plausibly touches both Reputation Damage
and Non-compliance. Record Non-compliance as the secondary and leave it
unscored. Scoring a secondary factor requires a separate named action of its
own. This keeps the secondary visible to whoever reads the appendix, without
letting one piece of work be counted twice, which is the mechanism that
inflates a delta fastest.

A technical action that genuinely moves two technical factors, such as
immutable offline backups against both Loss of Integrity and Loss of
Availability, is not covered by this rule and may move both.

## 9. What does not move residual risk

**9a.** An action the user marked "Not planned", or a recommendation they
never confirmed, does not move either residual state. This rule is not
negotiable, and applying it is what makes the residual numbers credible.

**9b.** A category-level answer moves nothing until a specific instance is
named. "We do patching" is not a patch. See Step 6b of SKILL.md.

**9c.** An action whose specifics you supplied yourself moves nothing. See
item 2 of `evidence-and-provenance.md`.

**9d.** A dated commitment falling beyond the assessment horizon moves
nothing in either state, and goes in Appendix D naming its date and the
horizon.

## 10. The clamp, and the honest null result

**10a.** Neither residual state may score higher than the pre-action baseline
on any factor, and Target residual may not score worse than Current residual
on any factor. If an action made something worse, that is a finding, not a
score change. Note it in the appendix.

**10b.** If no action moves any factor, say so plainly. Both residual states
equal the baseline, and the honest finding is that the intelligence has not
yet been actioned. Where the reason is that the movable factors sit outside
reach, say that instead, and point at Appendix H.

A run that refuses to move factors and explains why is more useful than one
that produces a large delta it cannot defend. In testing, the assessments'
most valuable conclusions came from factors the method declined to move. Treat
that as the skill working, and write it up as a finding rather than an
absence.
