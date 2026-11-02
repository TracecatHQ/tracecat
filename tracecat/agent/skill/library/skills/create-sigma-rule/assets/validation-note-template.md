# Validation Note: <rule title>

**Rule id:** <the UUIDv4 from the rule file>
**File:** `<filename>.yml`
**Drafted:** YYYY-MM-DD by <author> using the create-sigma-rule skill
**Status:** experimental. This rule has not been run against production data.

## Source

<What this was drafted from: the report title and permalink, the log sample, the vendor or tool
documentation page, or the behavior description. Quote the specific sentence, parameter entry or
command line the logic came from, so the claim can be traced back in six months.>

**Where the source was tool documentation**, name the two things the documentation does not
contain: the misuse being detected, and who framed it — normally the requester, since a vendor
page documenting a supported cmdlet asserts nothing adversarial. State which documented
parameters are being used as discriminators.

## Behavior targeted

<One or two sentences describing the observable, not the conclusion. "certutil.exe invoked
with URL-fetching arguments and an HTTP URL" rather than "the actor downloads a payload".>

## Abstraction level

**Chosen:** <atomic indicator / specific arguments / tooling / technique implementation /
invariant behavior>, tagged `stp.<N>`.

**Why:** <One or two sentences. What survives if the actor recompiles, renames the binary,
or rotates infrastructure, and what does not.>

**Layers:** <which selection holds the anchor, which the invariant, which the discriminator. If
a layer is absent, say which and what it costs — no discriminator means this is a hunting query.>

## Default state

**With no arguments, the tool or behavior does:** <one line. For a documented tool this comes
from the vendor's own parameter table — `Get-AppLockerPolicy` with no parameters returns the
local policy.>

**In scope?** <yes / no / no meaningful default>

**Consequence for the condition:** <If the default is in scope, the flag selection cannot be
required by the condition — say how it was handled. If out of scope, say why, which makes
requiring the flags legitimate narrowing rather than an INFERRED tightening.>

## Resilience record

All three Stage 4c questions, answered. "Not considered" is not an outcome.

| Question | Outcome | Why |
|---|---|---|
| `OriginalFileName` | <added with confirmed value / omitted, renaming gap recorded / N/A> | <e.g. confirmed in `sigma-spec.md`'s table; or Security 4688 does not carry the field; or the anchor is a path, so there is no original name to assert> |
| Splunk OR-grouping (only if the field was added and the target is Splunk) | <sibling rule / kept, deployed query needs hand-bracketing / N/A, target is not Splunk> | <what the converted query actually showed> |
| `\|windash` | <applied / explicit dash-variant `contains` list / N/A> | <e.g. attacker-chosen flags on a non-Splunk target; or Splunk, so written out; or the flags are OS-generated> |

## Evidence ledger

Every non-trivial detection condition, and where it came from. `INFERRED` conditions may not
narrow the match — if one did, it was dropped and appears in the tuning questions below
instead. A condition with no label is a condition nobody can defend.

| Condition | Label | Evidence |
|---|---|---|
| `Image\|endswith: '\certutil.exe'` | SOURCED | Report §3, command line quoted verbatim |
| `OriginalFileName: 'certutil.exe'` | GENERIC | PE header value confirmed in `sigma-spec.md`'s table, 1 upstream rule. Requires Sysmon 10+; not present in Security 4688 |
| `CommandLine\|contains: '-urlcache'` | SOURCED | Report §3, same command line |
| | | |

**Dropped or broadened by the `INFERRED` rule:** <any condition that would have narrowed the
match on an inference rather than on the source. Say what it was and what the analyst should
confirm. Delete if none.>

**Description provenance:** <which characterisations in the rule's `description` come from the
source, and which are this draft's own framing. If the source calls something *a* persistence
mechanism, the description does not call it *the primary* one.>

## ATT&CK mapping rationale

One line per tag, so the mapping is checkable rather than asserted. `attack_check.py`
cross-checks technique against declared tactic; it cannot tell you the technique was the right
one, which is what these lines are for.

| Behaviour | Tag | Why this technique and not its neighbour |
|---|---|---|
| <e.g. Winlogon Shell value modification> | `attack.t1547.001` | <Registry Run Keys / Startup Folder. Not T1547.004, which is Winlogon Helper DLL.> |

## Assumptions

Everything the draft assumed rather than knew. Testing confirms or corrects each one.

| # | Assumption | How to confirm |
|---|---|---|
| 1 | <e.g. Sysmon 10+ is deployed, so OriginalFileName is populated> | <check a sample event> |
| 2 | | |

## Expected false positives

Each one needs a tuning action, otherwise it is just a warning.

Where the value is not known, give the exclusion's **shape** and leave the value to the analyst.
A pattern with the value openly blank is a tuning strategy; a plausible concrete value invented
to fill the gap is a fabrication.

| Benign trigger | Likelihood in a typical environment | Tuning action |
|---|---|---|
| <e.g. Administrative retrieval of CRLs from internal PKI> | <likely / possible / unlikely> | <add `filter_main_internal_pki` with the real endpoint> |
| <e.g. Service accounts running scheduled policy audits> | <likely> | <exclude accounts matching your service-account convention — `User\|re: '^SVC_.*'` if yours follows that shape — after confirming against a week of hits. This draft does not know the convention> |

## Evasion review

The five that cost the adversary nothing, each with an answer rather than a consideration.

| Evasion | Covered? | How, or why not |
|---|---|---|
| Binary renaming | <yes / no / N/A> | <`OriginalFileName` added; or the gap, per the resilience record above> |
| Flag obfuscation | <yes / no / N/A> | <`\|windash`, or the explicit dash-variant list on Splunk> |
| Path variation | <yes / no / N/A> | <`\|endswith` rather than a full path; or the anchored path justified> |
| **Default state** | <yes / no> | <does the rule fire with no flags at all? Strike every flag condition and see what remains> |
| Equivalent tooling | <yes / no> | <sibling binaries that achieve the same effect: widened, sibling rules written, or named as a gap> |

**Residual gaps:** <What this rule does not cover and why. Sibling tooling that achieves the
same effect, argument forms not matched, alternative log sources. Be specific, because the
gap the analyst knows about is manageable and the one they discover mid-incident is not.>

## Validation evidence

```
sigma-cli <version> / pySigma <version> / Sigma specification <version>
ATT&CK Enterprise <version> (taxonomy the tags were checked against)

python3 scripts/attack_check.py --rules <dir>        # `python` on Windows
  → <n> valid, <n> invalid, <n> unverified   (exit <code>)

sigma check -i <flags> <file>
  → <result>

sigma convert -t <backend> -p <pipeline> <file>
  → <result>
```

ATT&CK tags were verified against the live taxonomy by `attack_check.py`, not by hand.
**Read its exit code and report it here.** Exit 1 means the tags were checked and some were
wrong, and were fixed. **Exit 2 or 3 means the tags were never checked** — say that, in those
words, rather than describing them as verified. Anything reported `[UNV]` is taxonomically
valid with a mapping the script could not confirm; name it rather than rounding it to valid.
"Checked manually against attack.mitre.org" is not a substitute and cannot catch a tactic
rename.

**Converted query:**

```
<paste the actual converted query here so the reviewer can see what will run>
```

**Conversion caveats:** <CIDR values that went literal, windash clause expansion, anything
the backend refused or silently changed. Delete if none.>

## Test plan

1. **Convert:** `sigma convert -t <backend> -p <pipeline> <file>.yml`
2. **Retrohunt:** run the converted query over <window, for example the last 30 days> of
   production data.
3. **Triage:** review a sample of hits and classify. If the hit count is above <threshold>,
   the rule is too broad for alerting and should be re-scoped as a hunt.
4. **Tune:** add `filter_main_*` blocks for confirmed benign sources, or move the exclusion
   into the environment's global filter document so it applies across rules.
5. **Test positive detection:** <which Atomic Red Team test, EVTX sample, or lab action
   would generate a true positive, where one exists.>

   **Execution preconditions this rule assumes.** Fill these in, because a lab test that does
   not reproduce them is an invalid test rather than a failed rule, and the reader cannot tell
   those two apart otherwise:

   | Precondition | This rule assumes |
   |---|---|
   | Parent process | <e.g. spawned from an Office application, not from a shell> |
   | User context | <e.g. an interactive standard user, not SYSTEM> |
   | Launch method | <e.g. double-click, not a scheduled task or a remote exec tool> |
   | Working directory | <e.g. the user's profile, not `C:\Windows\Temp`> |
   | Session type | <interactive / service / remote> |

   If a test run does not fire and one of these was not reproduced, that is the finding: record
   which precondition was missing rather than recording the rule as not working.
6. **Promote:** move to `status: test` once the retrohunt is clean and the technique fires in
   a lab. Move to `stable` after <period> in the non-paging queue with no unexplained noise.

**What the profile's backend answer changed for this rule:** <the `-t` target and pipeline
used, any construct the backend refused or mis-grouped, the field names the query landed on,
and the artifact format shipped. Two lines. If the answer changed nothing for this rule, say
that too.>

**Tuning owner:** <name or team>
**Review date:** <when this rule should be revisited>

---

*This is a draft detection derived from intelligence reporting, not a tested detection. It
has never run against this environment's data. Promotion beyond `experimental` is the
detection engineering team's decision after the test plan above has been completed.*
