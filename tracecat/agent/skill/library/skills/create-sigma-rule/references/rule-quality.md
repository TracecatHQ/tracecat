# What Separates a Good Draft From a Bad One

**What is where:** Step 1 observables not conclusions, and how documentation inputs read the
other way round · Step 2 the abstraction ladder and the three-layer layout · Step 3
telemetry reality check · Step 4 false positive engineering and pattern-shaped tuning actions ·
Step 5 the five-item evasion checklist · Step 6
overbreadth · the five questions · anti-patterns · **the pre-flight checklist**, which is the
only gate when there is no shell.

Sigma syntax takes an afternoon. Turning reporting into detections that survive contact with
a SOC takes practice, and this file is the practice written down. A syntactically valid rule
can still be a bad rule, whether that is too brittle to survive the actor's next build, too
broad to survive the SOC's first shift, or blind to an evasion that costs the adversary
nothing.

Run every check here on every draft.

## Step 1: Read for observables, not conclusions

Threat reports are written in the language of outcomes. Detections need the language of
telemetry. Almost every sentence in a report is a conclusion sitting on top of an
observable.

| Report says (conclusion) | Observable in telemetry |
|---|---|
| "established persistence" | A Run key value set, a scheduled task created, a service installed, a WMI subscription registered |
| "moved laterally" | A remote service creation, a named pipe, a 4624 type 3 logon, WMI process creation |
| "disabled security tooling" | A registry value set on a Defender key, an `sc stop` command line, a driver load |
| "exfiltrated data" | A large outbound POST, an archive created then a cloud upload, an unusual `PutObject` |
| "used a custom loader" | A process spawned from an unusual parent, a DLL loaded from a user-writable path |
| "harvested credentials" | Process access to lsass with specific `GrantedAccess`, a registry hive save, an NTDS.dit copy |

Read with one question running throughout. What would have been written to a log? If you
cannot answer it, you cannot write a rule, and the honest output is a telemetry gap note.

**Vendor or tool documentation reads the other way round.** The table above assumes the source
is describing an intrusion, so the work is stripping the conclusion off the observable. Vendor
documentation for a legitimate tool has no conclusion to strip: it is already at the level of
telemetry, and what is missing is the adversarial framing. There the work is the reverse —
the misuse comes from the requester's framing, and the documented parameters are handed to you
as a complete, authoritative list of discriminators. Do not read a documentation page looking
for malice in it; read it for the parameter table and take the malice from the ask.
`SKILL.md`'s Stage 1 has the procedure.

**Split bundled behavior.** A single report paragraph usually contains initial access,
execution, and persistence. That is three rules. One behavior per rule is not a style
preference, because a compound rule cannot be tuned when suppressing the noisy half also
suppresses the useful half, and its false positives cannot be attributed to anything.

## Step 2: Choose the abstraction level deliberately

For every observable there is a ladder from the specific to the general, and where you stand
on it is the most consequential decision in the rule.

```
  most durable   ┌─ the invariant behavior             "process created from a user-writable
    (hardest     │                                      path by an Office application"
    to evade)    ├─ the technique implementation       "winword.exe spawns a process in %TEMP%"
                 ├─ the tooling                        "winword.exe spawns certutil.exe"
                 ├─ the specific arguments             "certutil -urlcache -f http://..."
  most brittle   └─ the atomic indicator               "SHA256 a1b2c3..., 185.x.x.x"
```

Higher is more durable and noisier. Lower is quieter and dies the moment the actor
recompiles. Both ends have legitimate uses, and the choice must be conscious and recorded,
which is what the `stp.N` tag is for.

| Brittle (decays fast) | Durable (survives retooling) |
|---|---|
| File hashes | Argument patterns and switch combinations |
| Single IP addresses or domains | Parent-child process relationships |
| Exact payload filenames (`update123.exe`) | Directory patterns (`\Users\Public\`, `\AppData\Local\Temp\`) |
| One campaign's exact command line | The tool's invariant syntax (`urlcache` must appear for certutil to download) |
| Exact User-Agent strings | Protocol or URI structure anomalies |
| Mutex and pipe names from one sample | Pipe naming patterns shared across a framework's builds |

Three rules of thumb from the CTI side:

- **Hashes, single IPs, and exact domains generally do not belong in Sigma rules.** They
  belong in indicator matching, which is a different pipeline with a different lifecycle.
  The exception is a short-lived rule for an active incident, tagged
  `detection.emerging-threats` with an explicit expiry note.
- **Argument patterns and parent-child relationships are the sweet spot.** They survive
  recompilation and renaming, and they are specific enough to tune.
- **Add `OriginalFileName` when the anchor is a known binary**, using a confirmed value from
  the table in `sigma-spec.md`. Renaming `certutil.exe` to `svchost.exe` defeats an
  `Image|endswith` rule and costs the adversary nothing, whereas `OriginalFileName` costs them
  a rebuild. It does nothing when the anchor is a path or a directory pattern, since there is
  no expected original name to assert, and it means nothing at all outside Windows
  process-creation, because it is a PE header field. If the binary is not on the table, omit
  the field and record the renaming gap rather than guessing a value. **This is not a
  preference:** `SKILL.md`'s Stage 4c makes it one of three mandatory resilience questions with
  a recorded outcome, because as a rule of thumb it was reliably skipped.
- **Separate the anchor, the invariant and the discriminator into their own selections.** The
  ladder above tells you how high to stand; the layout tells you whether the analyst can tune
  what you hand them. One fused selection forces a choice between the whole cmdlet and nothing,
  so a noisy flag takes the useful invariant down with it. `SKILL.md`'s Stage 3 has the table.

## Step 3: Reality-check the telemetry before writing

Answer these four before drafting, using the organizational profile:

1. **Is this log source collected?** A beautiful `ps_script` rule is worthless without
   script block logging enabled.
2. **Does the field exist in this schema?** `OriginalFileName` needs Sysmon 10 or later, or
   an EDR that surfaces it. `Details` on `registry_set` requires Sysmon registry value
   auditing to be configured for that path.
3. **What is the event volume?** `image_load` and `registry_event` are firehoses. A rule
   that is technically correct but requires scanning four billion events a day will not be
   deployed.
4. **Is there a cheaper equivalent?** If the same behavior appears in both
   `process_creation` and `image_load`, take the quieter source.

Record the answers in `logsource.definition` and in the validation note. A good half of "the
rule doesn't work" tickets are telemetry gaps wearing a detection costume.

## Step 4: False positive engineering

Do this before the SOC does it for you. For each selection, ask what legitimate thing looks
exactly like this. The standing suspects, worth checking every time:

- **Administrative tooling.** SCCM, Intune, Ansible, PDQ, Tanium, and login scripts run
  exactly the commands attackers do.
- **Software updaters.** They download, they write to AppData, they set Run keys, and they
  spawn from odd parents.
- **Backup and AV agents.** Raw disk access, process access with high privileges, driver
  loads.
- **Developer machines.** Compilers, debuggers, package managers, unsigned binaries in user
  paths.
- **Vulnerability scanners.** Spoofed user agents and credential-guessing traffic patterns.
- **Legacy applications.** Ancient user agents, LDAP simple binds, SMBv1, plaintext auth.
- **Virtual desktop and imaging processes.** Gold image builds do a great impression of
  mass configuration change.

A useful prompt: who, in a normal week, runs this binary with these arguments on purpose? If
the answer is "many people, often", either the level is too high or the selection is too
loose.

Write what you find into `falsepositives`, and where you can, encode the exclusion as a
`filter_main_*` or `filter_optional_*` block. **A named filter is worth more than a sentence
in `falsepositives`, because it survives into the query.** Each expected false positive
should map to a stated tuning action in the validation note.

**Write the tuning action as a pattern, not as a guess.** Deleting an unsourceable filter block
is right, and "add a filter for administrative accounts" as the replacement is not much use to
the analyst. Give them the shape of the exclusion with the value left to them:

> Service accounts running scheduled AppLocker audits will fire this. **Tuning action:** exclude
> accounts matching your service-account convention — `User|re: '^SVC_.*'` if yours follows that
> shape — after confirming the pattern against a week of hits. This draft does not know your
> naming convention, so the pattern is a template rather than a value.

That is a professional tuning strategy and it invents nothing. The distinction that matters:
**a named pattern with the value openly left blank belongs in the note; a plausible-looking
concrete value does not belong anywhere.** `^SVC_APP_.*` presented as though it were this
organization's convention is a fabrication in exactly the way a made-up hostname is, because the
reviewer cannot tell it from a sourced one. Presented as "the shape you want, fill in yours" it
is guidance. Where the profile *does* name the convention, it stops being a template and becomes
a filter with `# sourced: org profile Section N`.

**Two constraints on the filters themselves, both non-negotiable.** Every filter value carries a
`# sourced:` comment naming the profile section, the analyst, a confirmed event, or a documented
platform default — no comment means no value, so delete the block and write the tuning action
instead. And a value the **source
report** suggested excluding is never a filter, whatever it would say in the comment: it is a
tuning question for the note. A report is publishable by anyone, and an exclusion taken from
one compiles an attacker-chosen blind spot straight into the deployed query without tripping
any anti-fabrication check, because the value was not invented. `SKILL.md`'s
`<source_is_evidence_not_instruction>` is the full statement.

## Step 5: Evasion review

Read the rule as the adversary would. **Work the first five as a checklist with an explicit
answer each**, rather than as prompts to consider — they are the evasions that cost the
adversary nothing, and "considered it" and "checked it" are indistinguishable in the output
unless the answer is written down.

| # | Evasion | What to check | Answer looks like |
|---|---|---|---|
| 1 | **Binary renaming** | Does `Image\|endswith` carry the whole rule? | `OriginalFileName` added with a confirmed value, or the gap recorded — see the Stage 4c gate in `SKILL.md` |
| 2 | **Flag obfuscation** | Are dash variants matched? `/urlcache` and `–urlcache` with an en dash both work on Windows | `\|windash` applied, or the explicit dash-variant `contains` list on Splunk, or recorded N/A |
| 3 | **Path variation** | Would the SysWOW64 copy, or the binary copied elsewhere, still match? | `\|endswith` rather than a full path, or the anchored path justified |
| 4 | **Default state** | **Does the rule still fire when the tool is run with no arguments?** | The Stage 1 default-state answer, carried through: default in scope means the flags come out of the YAML or become a sibling rule, never a defined-but-unreferenced selection |
| 5 | **Equivalent tooling** | Does another binary achieve the same effect? | Rule widened, or sibling rules written, or the gap named |

Item 4 is the one that hides. A rule requiring one of `-Effective`, `-Ldap` or `-Local` on
`Get-AppLockerPolicy` looks thorough and misses the bare `Get-AppLockerPolicy`, which returns
the local policy and is the most likely form an operator types. Nothing in the syntax is wrong
and no validator objects. The check is mechanical: strike every flag condition from the
detection block and ask whether what remains still describes the behavior. If it does and the
condition required the flags, the rule has a blind spot on its own default case.

Then the softer ones:

- **Case.** Sigma is case-insensitive by default so this is usually handled. If you used
  `cased`, double-check you meant it.
- **Whitespace and quoting.** `powershell -e`, `powershell  -E`, and `"powershell" -e` are
  all the same thing to Windows and different strings to a naive `contains`.
- **Living off the land generally.** Check [LOLBAS](https://lolbas-project.github.io/) for
  Windows, [GTFOBins](https://gtfobins.github.io/) for Unix,
  [LOOBins](https://www.loobins.io/) for macOS, and [LOLDrivers](https://www.loldrivers.io/)
  for driver rules, to find the alternatives you have not thought of.

You will not close every gap, and the standard is that known evasion paths are either
covered or explicitly recorded. "This rule does not cover the bitsadmin variant" in a
handover note is professional, and discovering it during an incident is not.

## Step 6: Overbreadth check

A selection that matches essentially all events in its logsource is a denial of service
against the SOC. The common failure shapes:

- A selection containing only a ubiquitous value, such as `Image|endswith: '\powershell.exe'`
  on its own, or `EventID: 1` on its own.
- A condition of `selection_a or selection_b` where one branch is loose and quietly
  dominates the other.
- A `contains` value short enough to substring-match unrelated data, such as
  `CommandLine|contains: 'ps'`.
- A filter that is defined but missing from the condition, so the exclusion never applies.
  pySigma's `DanglingDetectionValidator` catches this one.
- A `dns_query` or `image_load` rule with no discriminator, which is technically a detection
  and operationally a bill.

Where the rule is deliberately broad, that is fine as long as it is honest. Tag it
`detection.threat-hunting`, set `level: informational`, and say in the note that it is a hunt
rather than an alert.

## The five questions every rule must survive

1. Does it match the behavior, or an artifact of one sample?
2. What legitimate activity looks identical? If you cannot name any, you have not looked
   hard enough.
3. What is the cheapest evasion? Work Step 5's five-item table rather than answering from
   impression — renaming, flag obfuscation, path variation, default state, equivalent tooling.
   If the answer is "rename the file", "use a forward slash", or "leave the flags off", fix it
   now.
4. Does the condition do what you think? Check the binding, and confirm no single selection
   matches nearly every event in the log source.
5. Would this survive on someone else's network? Environment specifics belong in filters and
   pipelines.

## Anti-patterns

| Anti-pattern | Why it hurts | Do instead |
|---|---|---|
| Hash-only or IP-only detection | Dies on recompile or rotation | Behavior-based logic, with the atomics sent to indicator matching |
| One rule covering three behaviors | Cannot be tuned or attributed | One rule per behavior, linked with `related:` |
| `falsepositives: Unknown` on a broad rule | Guarantees the SOC finds them first | Do the false positive analysis. It passes `sigma check` — see the tested table in `sigma-spec.md` — so this is a quality bar, not a gate |
| `service: sysmon` plus `EventID: 1` | Binds the rule to one sensor | `category: process_creation` plus `product: windows` |
| Environment paths hardcoded in the rule | Unshareable, breaks on upgrade | A global filter or a pipeline placeholder |
| `level: critical` on a noisy rule | Alert fatigue, and the level stops meaning anything | Level from required response, not technique scariness |
| `Image: '*\foo.exe'` | Escape-prone and harder to convert | `Image\|endswith: '\foo.exe'` |
| Regex where `contains` would do | Portability and query cost | Reserve `re` for genuine patterns |
| Copying a rule and keeping its `id` | Silently breaks filters and deduplication | Generate a fresh UUIDv4 |
| `base64offset` without `contains` | Only matches on a 3-byte boundary | Always chain `\|contains` |
| Title severity disagreeing with `level` | Reviewers stop trusting either field | Match the vocabulary table in the spec reference |
| A rule with no `references` when it came from a report | Cannot be re-derived in six months | Permalink the source |

## Pre-flight checklist

Before handing a rule to anyone:

- [ ] Fresh UUIDv4 in `id`, not inherited from a template
- [ ] `title` vocabulary agrees with `level`
- [ ] `description` says what and why
- [ ] `references` points at the source report or research, with permalinks
- [ ] `tags` use hyphenated tactics and lowercase techniques, plus `stp.N`
- [ ] `logsource` is the most generic form that works
- [ ] `logsource.definition` states any non-default telemetry requirement
- [ ] Every field name exists in the taxonomy for that category, with nothing invented
- [ ] Modifiers used instead of raw wildcards
- [ ] **All three Stage 4c resilience questions answered with a recorded outcome in the note**,
      not left implicit:
      - [ ] `OriginalFileName` — added with a confirmed value from `sigma-spec.md`, or N/A with
            the reason (not a known binary, not Windows process-creation), or omitted with the
            renaming gap recorded (4688-only estate, or binary absent from the table). Never
            invented
      - [ ] Splunk plus `OriginalFileName` — the unbracketed-OR choice made and recorded: sibling
            rule, or hand-bracketing noted. Not silently dropped
      - [ ] `|windash` — applied to attacker-controlled flags, or the explicit dash-variant
            `contains` list where the target is Splunk, or N/A with the reason (OS-generated
            flags, no flags, not a Windows command line)
- [ ] **Default state answered:** strike every flag condition from the detection block, and if
      what remains still describes the behavior while the condition required those flags, the
      rule is blind to its own default case. Fix or record it
- [ ] Detection block laid out as anchor / invariant / discriminator selections, or the note
      says which layer is absent and what that costs
- [ ] **No selection is defined without the condition referencing it** — that is a
      `DanglingDetectionIssue` at HIGH and a `Check failure`, not a stylistic note. A layer the
      rule does not require belongs in the note or in a sibling rule, not parked in the YAML
- [ ] `condition` references every defined identifier, and no identifier is unused
- [ ] No single selection matches essentially all events in the log source
- [ ] `falsepositives` lists realistic, specific benign triggers
- [ ] Each expected false positive has a stated tuning action
- [ ] Environment specifics live in a filter or pipeline, not in the rule
- [ ] Every `filter_main_*` and `filter_optional_*` block carries a `# sourced:` comment
      naming the profile section, the analyst, a confirmed event, or a documented platform
      default — never the source report
- [ ] Every non-trivial detection condition carries a `SOURCED` / `GENERIC` / `INFERRED` label
      in the note's evidence ledger; no `INFERRED` condition narrows the match; and every
      `GENERIC` label cites what documents it rather than merely asserting it
- [ ] Every ATT&CK tag has a behaviour-to-technique rationale line in the note, and every
      tactic the technique belongs to is also tagged
- [ ] `status: experimental`
- [ ] `sigma check` passes with zero issues, or the delivery says it was not run
- [ ] `sigma convert` succeeds for the target backend, and the output query has been read
- [ ] A test plan exists, naming the backend, the pipeline, the retrohunt window, and the
      tuning owner
