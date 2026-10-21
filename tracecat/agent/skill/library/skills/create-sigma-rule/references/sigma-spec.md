# Sigma Rule Format: The Working Reference

Written against **Sigma specification v2.1.0**, **pySigma 1.5.0**, **sigma-cli 3.1.0**.

**What is where:** the compilation model · metadata fields, including the tested
`falsepositives` rejections and the v19 tactic list · logsources and the taxonomy, including
the verified `OriginalFileName` table · the detection section · value modifiers and their
gotchas · the condition language · **correlation rules**, for behaviours a single event cannot
carry · **global filters**, for tuning across many rules at once · YAML style · the filename
convention, and how to get the required prefix out of the tool instead of out of memory.

Read this before writing any YAML. Where a construct is not listed here and cannot be
confirmed against the installed tooling, leave it out rather than invent it. A rule with a
fabricated field name converts cleanly and returns nothing forever, which is the worst
possible failure mode because it looks like success.

## The compilation model

Most confusion about Sigma comes from not knowing which of three stages a problem lives in.

```
Sigma rule (YAML)         Processing pipeline          Backend
generic field names   →   field/logsource mapping  →   query language  →  SIEM query
category: process_creation    category → EventCode=1       SPL syntax
Image, CommandLine            Image → FolderPath           KQL syntax
```

The rule says what behavior to look for in vendor-neutral vocabulary. The pipeline says how
this environment names things. The backend says what query syntax to emit. Environment
specifics belong in the pipeline and in global filters, never in the rule, because that is
what makes a rule shareable and upgradeable.

## Metadata fields

Strictly required by the parser: `title`, `logsource`, `detection`. Required in practice and
enforced by SigmaHQ: `title`, `id`, `status`, `description`, `references`, `author`, `date`,
`tags`, `logsource`, `detection`, `falsepositives`, `level`. Write all of them.

### `title`

Keyword style, not a sentence. Do not start with "Detects", which belongs in the
description. SigmaHQ convention is `Prefix - Main Title - Suffix`, where the prefix marks a
category or attribution (`HackTool - `, `PUA - `, `APT29 - `) and the suffix disambiguates
the same logic across log sources (` - PowerShell`, ` - Security`).

**The title's severity word must agree with `level`.** This is the convention most drafts
get wrong.

| Level | Vocabulary | Example |
|---|---|---|
| informational / low | plain, no qualifier | `Net.exe Execution` |
| medium | `Potential ...` | `Potential Payload Download Via Certutil URLCache` |
| high | `Suspicious ...` | `Suspicious Run Key Pointing To User Writable Location` |
| critical | `Malware`, `Exploit`, `Attempt`, `<Actor> Activity` | `Emotet Loader Execution Attempt` |

### `id` and `related`

A UUIDv4, generated once per rule, permanent. It is the join key for global filters,
correlation references, alert deduplication, and coverage tracking. Never carry an id over
from a template or a copied rule, because that silently breaks filters downstream.

`related` records lineage, with `type` being one of `derived`, `obsolete`, `merged`,
`renamed`, `similar`, or `correlation`:

```yaml
related:
    - id: 08d9c2a0-a4d0-4d3f-9be5-b8fcbba1e5e4
      type: derived
```

### `status`

| Value | Meaning |
|---|---|
| `experimental` | New, untested in the wild. Everything drafted from a report starts and stays here. |
| `test` | Being trialled, may still be noisy. Deployed to a non-paging queue. |
| `stable` | Validated, low false positive rate, deploy widely. |
| `deprecated` | Superseded, kept for history. |
| `unsupported` | Cannot work as written. |

This skill emits `experimental` and nothing else. Promotion is the detection engineering
team's call after they have run the rule over real data.

### `level`

| Level | Intent |
|---|---|
| `informational` | Enrichment or correlation input only, never alerts. |
| `low` | Notable but rare, useful for hunting. |
| `medium` | Worth regular review, environment-dependent false positives expected. |
| `high` | Prompt internal review. |
| `critical` | Immediate investigation. |

Choose from what an analyst should do when it fires. A rule that fires 200 times a day is
not `critical` regardless of which technique it maps to.

### `tags`

Lowercase and dot-namespaced.

| Namespace | Format | Example |
|---|---|---|
| ATT&CK tactic | hyphenated tactic name | `attack.command-and-control`, `attack.privilege-escalation` |
| ATT&CK technique | `t` plus number, sub-techniques with a dot | `attack.t1059.001` |
| ATT&CK group | `g` plus number | `attack.g0016` |
| ATT&CK software | `s` plus number | `attack.s0002` |
| CVE | lowercase with dots | `cve.2021-44228` |
| CAR | id without the `CAR-` prefix | `car.2016-04-005` |
| D3FEND | `d3-` technique, `d3f-` artifact | `d3fend.d3-am` |
| Detection type | three values only | `detection.threat-hunting`, `detection.dfir`, `detection.emerging-threats` |
| STP | Summiting the Pyramid robustness score | `stp.4`, `stp.3k` |
| TLP | lowercase | `tlp.amber`, `tlp.clear` |

**The ATT&CK tactic list is not what you remember.** ATT&CK v19 retired `defense-evasion`
and redistributed its techniques, so the valid tactic shortnames are now `collection`,
`command-and-control`, `credential-access`, `defense-impairment`, `discovery`, `execution`,
`exfiltration`, `impact`, `initial-access`, `lateral-movement`, `persistence`,
`privilege-escalation`, `reconnaissance`, `resource-development`, and `stealth`. Most of what
used to be defense-evasion is now `stealth`, and the impair-defenses family is
`defense-impairment`. Several technique IDs were also merged or deprecated. Confirm both the
tactic and the technique with `scripts/attack_check.py` rather than trusting this list, which
will itself go stale.

**Every tactic a technique belongs to must also appear as a tag.**
`sigmahq_tags_techniques_without_tactics` enforces this, and it is stricter than most people
expect: T1053.005 belongs to execution, persistence *and* privilege-escalation, so a rule
carrying `attack.t1053.005` with only `attack.execution` fails with
`missing_tactic=attack.persistence`. Run `attack_check.py --lookup T1053.005` to get the full
tactic list before writing the tags. This validator is also what catches a technique paired
with an unrelated tactic, so `attack.stealth` plus `attack.t1105` fails the gate.

What it cannot catch is a wrong technique whose tactics happen to be right. T1547.001 and
T1547.004 both belong to persistence and privilege-escalation, so mis-tagging Winlogon Shell
persistence as T1547.004 passes cleanly. That is why Stage 4 asks for a written
behaviour-to-technique rationale: it is the only check that covers this.

**`attack.ds####` data-source tags are not accepted at all.** pySigma's `attacktag` validator
rejects the `ds` namespace outright, whatever the data source, so there is no valid form of the
tag. One upstream rule still carries `attack.ds0005` and fails the gate for it.

**`detection.threat-hunting`** marks a rule as intentionally too noisy to alert on, which is
how you ship a broad high-recall rule honestly.

**`stp.N`** records how hard the analytic is to evade, using the Summiting the Pyramid scale.
The validator accepts `^[1-5][auk]?$`, so `stp.3`, `stp.3k` and `stp.4a` are all legal:

| Level | What the analytic targets | Evasion cost |
|---|---|---|
| 1 | An ephemeral value: a hash, a filename, an IP | Recompile, rename, rotate |
| 2 | A value the tool happens to use: a mutex, a fixed user agent, a specific DLL name | Change a constant and rebuild |
| 3 | The tool's own behavior: its argument syntax, its API sequence | Switch tool or rewrite the tool |
| 4 | The technique implementation: the process relationship, the write location | Change technique implementation |
| 5 | The invariant core the technique cannot function without | Abandon the technique |

The optional letter narrows what the score applies to, with `a` for application, `u` for user
mode, and `k` for kernel. Omit it when you are not making that distinction.

### `falsepositives`

A list of realistic benign triggers, specific enough to be actionable.

**What the validator actually rejects**, as opposed to what is merely bad style. Three
validators run on this field, and the first is the one that surprises people:

`SigmahqFalsepositivesBannedWordValidator` splits each entry on spaces and compares each
whole token, lowercased, for **equality** against `("none", "pentest", "penetration")`. It is
not a substring match. A hyphen or a suffix defeats it, so `penetration-testing` and
`pentesting` both pass while a bare `penetration` fails. Tested:

| Entry | Result | Why |
|---|---|---|
| `Administrative certificate management by IT staff` | pass | no banned token |
| `Unknown` | pass | not on the list |
| `Red Team activity` | pass | not on the list |
| `Legitimate penetration testing tools` | **FAIL** | `penetration` is a bare token |
| `Legitimate penetration-testing tools` | pass | token is `penetration-testing` |
| `Authorized pentesting activity` | pass | token is `pentesting` |
| `Penetration testing` | **FAIL** | comparison is case-insensitive |
| `None` | **FAIL** | bare token |
| `No known false positives` | pass | `No` is not `none` |
| `Authorized red-team or security-testing activity` | pass | sidesteps the question entirely |

`SigmahqFalsepositivesCapitalValidator` requires each entry to start with a capital letter,
so `legitimate software deployment by SCCM` fails on the lowercase `l` alone.

`SigmahqFalsepositivesTypoWordValidator` rejects the whole tokens `unkown`, `ligitimate`,
`legitim` and `legitimeate`, so a misspelt `Unkown` fails where `Unknown` passes.

**So `Unknown` and `Red Team` are style problems, not gate failures.** They pass `sigma check`
and they are still weak entries: `rule-quality.md`'s anti-pattern table is the authority on
that, and `Unknown` on a broad rule guarantees the SOC finds the false positives first. Write
it only when it is true and you have genuinely looked. `Unlikely` means you thought about it
and expect none. Where the honest answer is authorised offensive activity, write
`Authorized red-team or security-testing activity`, which is accurate and passes.

### Other fields

| Field | Use |
|---|---|
| `name` | A short unique handle so correlations and filters can reference the rule without a UUID. |
| `fields` | **Do not use.** Still optional in the Sigma specification, but `SigmahqFieldsExistenceValidator` rejects any rule carrying it, and no rule under `rules/`, `rules-emerging-threats/` or `rules-threat-hunting/` upstream uses it (42 files under `deprecated/` and `unsupported/` still do). Put triage fields in the validation note instead. |
| `modified` | ISO date of last change, added whenever you edit a shipped rule. |
| `license` | SPDX identifier, for example `DRL-1.1`. |
| `scope` | Intended target scope, for example `Domain Controller`. |
| `taxonomy` | Declares a non-default field-naming taxonomy. The default is `sigma`. |

## Log sources and the taxonomy

```yaml
logsource:
    category: process_creation   # a class of event, product-independent
    product: windows             # OS or software producing the log
    service: security            # a specific channel within the product
    definition: 'Requires Sysmon with SchemaVersion >= 4.30'
```

You rarely need all three. Generic beats specific every time, because
`category: process_creation` with `product: windows` converts against Sysmon, native 4688
auditing, Defender XDR, CrowdStrike, and SentinelOne, whereas `service: sysmon` with
`EventID: 1` binds the rule to one sensor and one schema. pySigma ships a validator
(`SpecificInsteadOfGenericLogsourceValidator`) that flags this.

`definition` is not decoration. If the rule needs script block logging, a specific Sysmon
config, or a non-default audit subcategory, say so. A rule that silently requires telemetry
the consumer does not collect looks exactly like a rule that does not fire.

### Windows process_creation fields

The workhorse category, roughly 60 percent of public rules.

```
UtcTime, ProcessGuid, ProcessId, Image, FileVersion, Description, Product, Company,
CommandLine, CurrentDirectory, User, LogonGuid, LogonId, TerminalSessionId,
IntegrityLevel, imphash, md5, sha1, sha256,
ParentProcessGuid, ParentProcessId, ParentImage, ParentCommandLine, OriginalFileName
```

`OriginalFileName` comes from the PE header, is available from Sysmon 10 onwards and most
EDRs, and survives binary renaming. It is the single highest-value field for hardening a
rule against trivial evasion.

**Do not guess `OriginalFileName` values.** The string comes verbatim from the PE header and
is not what you would predict from the filename: `net.exe` reports `net.exe`, but `psexec.exe`
reports `psexec.c`, `pwsh.exe` reports `pwsh.dll`, `nltest.exe` reports `nltestrk.exe`, and
`rar.exe` reports `WinRAR.exe`. Matching is case-insensitive, so casing will not break a rule
and you do not need to reproduce it exactly, but an invented value produces a rule that
converts cleanly and never fires.

Every value below was confirmed present in the upstream SigmaHQ corpus (`rules/`,
`rules-emerging-threats/`, `rules-threat-hunting/` at commit `da9bb07`, 3,757 rules), with
the count of upstream rules using it. Upstream casing is itself inconsistent — `cmstp` and
`findstr` appear in both cases across different rules — so treat the spelling as the
authority and the casing as cosmetic.

| Binary | `OriginalFileName` | Upstream rules |
|---|---|---|
| cmd.exe | `Cmd.EXE` | 2 |
| powershell.exe | `PowerShell.exe` | 1 |
| powershell_ise.exe | `PowerShell_ISE.EXE` | 6 |
| pwsh.exe | `pwsh.dll` | 81 |
| certutil.exe | `certutil.exe` | 1 |
| certoc.exe | `CertOC.exe` | 6 |
| bitsadmin.exe | `bitsadmin.exe` | 9 |
| curl.exe | `curl.exe` | 13 |
| wget.exe | `wget.exe` | 3 |
| rundll32.exe | `rundll32.exe` | 1 |
| regsvr32.exe | `regsvr32.exe` | 4 |
| mshta.exe | `mshta.exe` | 6 |
| msiexec.exe | `msiexec.exe` | 6 |
| msdt.exe | `msdt.exe` | 6 |
| hh.exe | `HH.exe` | 5 |
| wmic.exe | `wmic.exe` | 38 |
| schtasks.exe | `schtasks.exe` | 18 |
| net.exe / net1.exe | `net.exe` / `net1.exe` | 19 |
| reg.exe | `reg.exe` | 32 |
| regedit.exe | `REGEDIT.EXE` | 4 |
| sc.exe | `sc.exe` | 11 |
| netsh.exe | `netsh.exe` | 14 |
| wevtutil.exe | `wevtutil.exe` | 5 |
| wscript.exe / cscript.exe | `wscript.exe` / `cscript.exe` | 17 |
| python.exe | `python.exe` | 1 |
| autoit3.exe | `AutoIt3.exe` | 2 |
| rar.exe | `WinRAR.exe` | 1 |
| 7z.exe / 7za.exe / 7zr.exe | `7z.exe` / `7za.exe` / `7zr.exe` | 4 |
| psexec.exe | `psexec.c` | 2 |
| psexesvc.exe | `psexesvc.exe` | 3 |
| procdump.exe | `procdump` | 2 |
| bcdedit.exe | `bcdedit.exe` | 2 |
| vssadmin.exe | `VSSADMIN.EXE` | 3 |
| wbadmin.exe | `WBADMIN.EXE` | 6 |
| diskshadow.exe | `diskshadow.exe` | 4 |
| taskkill.exe | `taskkill.exe` | 1 |
| tasklist.exe | `tasklist.exe` | 3 |
| whoami.exe | `whoami.exe` | 9 |
| nltest.exe | `nltestrk.exe` | 2 |
| ping.exe | `ping.exe` | 1 |
| findstr.exe | `findstr.exe` | 1 |
| find.exe | `find.exe` | 1 |
| attrib.exe | `ATTRIB.EXE` | 3 |
| xcopy.exe | `XCOPY.EXE` | 4 |
| robocopy.exe | `robocopy.exe` | 4 |
| forfiles.exe | `forfiles.exe` | 1 |
| ntdsutil.exe | `ntdsutil.exe` | 1 |
| esentutl.exe | `esentutl.exe` | 2 |
| makecab.exe | `makecab.exe` | 1 |
| odbcconf.exe | `odbcconf.exe` | 9 |
| msbuild.exe | `MSBuild.exe` | 1 |
| installutil.exe | `installutil.exe` | 1 |
| regasm.exe / regsvcs.exe | `RegAsm.exe` / `RegSvcs.exe` | 6 / 5 |
| addinutil.exe | `AddInUtil.exe` | 3 |
| Microsoft.Workflow.Compiler.exe | `Microsoft.Workflow.Compiler.exe` | 3 |
| ieexec.exe | `IEExec.exe` | 3 |
| ie4uinit.exe | `IE4UINIT.EXE` | 3 |
| presentationhost.exe | `PresentationHost.exe` | 2 |
| scriptrunner.exe | `ScriptRunner.exe` | 3 |
| mavinject.exe | `mavinject32.exe` / `mavinject64.exe` | 1 |
| cmstp.exe | `cmstp.exe` | 1 |
| pcalua.exe | `pcalua.exe` | 2 |
| xwizard.exe | `xwizard.exe` | 1 |
| sdbinst.exe | `sdbinst.exe` | 2 |
| fltmc.exe | `fltMC.exe` | 2 |
| appcmd.exe | `appcmd.exe` | 4 |
| mpcmdrun.exe | `MpCmdRun.exe` | 3 |
| conhost.exe | `CONHOST.EXE` | 3 |
| werfault.exe | `WerFault.exe` | 3 |
| explorer.exe | `EXPLORER.EXE` | 1 |

**Checked and absent from upstream, so unverified: do not use these.** No rule in the corpus
sets an `OriginalFileName` for `ssms.exe`, `pythonw.exe`, `w3wp.exe`, `expand.exe`,
`control.exe`, or `wmiprvse.exe`. Earlier versions of this file carried guessed values for
`control.exe` and `wmiprvse.exe`; they are listed here rather than deleted so nobody
re-derives them. Two more that earlier versions got wrong outright: `Rar.exe` (the real value
is `WinRAR.exe`) and `nltest.exe` (the real value is `nltestrk.exe`, because nltest ships in
the Resource Kit). A rule keyed on either of the wrong values would have converted cleanly
and never fired.

**If the binary is not on the table, omit the field and record the evasion gap.** Do not
attempt to verify the value from memory, and do not reason it out from the filename — the
five exceptions above are exactly what that reasoning produces. The gap is cheap: an
`Image|endswith` rule with a documented renaming weakness is worth more than a rule with a
fabricated `OriginalFileName` that silently matches nothing.

### Other Windows categories

| Category | Sysmon EID | Key fields and notes |
|---|---|---|
| `image_load` | 7 | `ImageLoaded`, `Signed`, `SignatureStatus`. Very noisy, filter hard. |
| `file_event` | 11 | `TargetFilename` |
| `file_delete` | 23 | |
| `file_change` | 2 | File creation time changed, so timestomping |
| `file_rename` | | ETW Microsoft-Windows-Kernel-File |
| `registry_add` / `registry_delete` | 12 | `TargetObject` |
| `registry_set` | 13 | `TargetObject`, `Details` |
| `registry_rename` | 14 | |
| `registry_event` | 12,13,14 | The union. Prefer the specific one. |
| `network_connection` | 3 | `DestinationIp`, `DestinationPort`, `DestinationHostname`, `Initiated` |
| `dns_query` | 22 | `QueryName`, `QueryStatus`, `QueryResults` |
| `create_remote_thread` | 8 | `SourceImage`, `TargetImage`, `StartModule` |
| `process_access` | 10 | `SourceImage`, `TargetImage`, `GrantedAccess`, `CallTrace` |
| `pipe_created` | 17,18 | `PipeName`. Good for C2 framework detection. |
| `driver_load` | 6 | `ImageLoaded`, `Signature`, `SignatureStatus` |
| `raw_access_thread` | 9 | |
| `wmi_event` | 19,20,21 | WMI subscription persistence |
| `ps_script` | | EID 4104, `ScriptBlockText`, needs script block logging |
| `ps_module` | | EID 4103, `Payload`, `ContextInfo` |
| `ps_classic_start` | | EID 400, Windows PowerShell channel |

Windows Security channel rules use `product: windows` with `service: security` and match on
`EventID` directly, for example 4624, 4625, 4688, 4697, 4698, 5140, 7045.

### Product-independent categories

| Category | Fields |
|---|---|
| `firewall` | `src_ip`, `src_port`, `dst_ip`, `dst_port`, `username` |
| `proxy` | `c-uri`, `c-uri-stem`, `c-uri-query`, `c-uri-extension`, `c-useragent`, `cs-method`, `cs-host`, `cs-referrer`, `cs-cookie`, `cs-version`, `cs-bytes`, `sc-bytes`, `sc-status`, `r-dns`, `src_ip`, `dst_ip` |
| `webserver` | `cs-method`, `cs-uri-stem`, `cs-uri-query`, `sc-status`, `c-ip`, `cs-user-agent`, `cs-host`, `cs-referer`, `cs-username`, `time-taken` |
| `dns` | Generic DNS query logs |
| `antivirus` | `Filename`, `Signature`, `Action` |
| `database` | SQL query text |
| `application` | Keyword-style rules, product-scoped |

**Network, Zeek and ECS shaped.** `category: network` with `service: connection` or
`service: dns` uses dotted ECS names such as `source.ip`, `destination.port`,
`dns.question.name`, `network.transport`, `network.community_id`.

**Cloud.** `product: aws` with `service: cloudtrail` uses the raw CloudTrail JSON paths
(`eventSource`, `eventName`, `userIdentity.type`, `requestParameters.*`). Also
`product: azure`, `gcp`, `okta`, `m365`, `google_workspace`, `onelogin`, each with their own
native field names.

**Linux.** `product: linux` with categories `process_creation`, `network_connection`,
`file_event`, or `service: auditd`, `service: syslog`, `service: sshd`, `service: cron`.
Auditd rules match on `type` plus the auditd field names.

**macOS.** `product: macos` with `category: process_creation` and `category: file_event`.

## The detection section

Everything except `condition` is a search identifier, meaning a named block of matching
logic that the condition then combines.

Two rules govern everything:

> **A map of key and value pairs is joined with AND.**
> **A list is joined with OR.**

```yaml
detection:
    selection_and:              # Image AND CommandLine must both match
        Image|endswith: '\rundll32.exe'
        CommandLine|contains: 'javascript:'

    selection_or:               # any ONE of the three values matches
        Image|endswith:
            - '\rundll32.exe'
            - '\regsvr32.exe'
            - '\mshta.exe'

    selection_list_of_maps:     # either (A and B) OR (C and D)
        - Image|endswith: '\rundll32.exe'
          CommandLine|contains: 'javascript:'
        - Image|endswith: '\mshta.exe'
          CommandLine|contains: 'http'

    condition: selection_and
```

Reading that until it is automatic prevents the most common logic bug in Sigma, which is a
list of values where AND was meant. For that case use the `all` modifier.

### Values, null, and empty

```yaml
    selection:
        FieldA: 'value'          # case-INSENSITIVE by default
        FieldB: 4625             # number, unquoted
        FieldC: null             # field is absent or null
        FieldD: ''               # field exists and is empty
        FieldE|exists: true      # field is present, whatever its value
        FieldF|exists: false     # field is absent
```

These are three different questions and backends translate them differently.

### Keyword search

A bare list with no field names searches the whole raw event. Reserve it for log sources
with no reliable field parsing, such as `category: application` or raw syslog.

```yaml
detection:
    keywords:
        - 'DROP TABLE'
        - 'UNION SELECT'
    condition: keywords
```

### Wildcards and escaping

`*` matches any number of characters and `?` matches exactly one.

| You write | It means |
|---|---|
| `\` or `\\` | a literal backslash |
| `\*` , `\?` | a literal asterisk or question mark |
| `\\*` | a literal backslash followed by a wildcard |
| `\\\*` | a literal backslash followed by a literal asterisk |
| `\\\\` | two literal backslashes |

Avoid raw wildcards wherever a modifier says the same thing. `Image|endswith: '\certutil.exe'`
is clearer, less escape-prone, and converts better than `Image: '*\certutil.exe'`. pySigma
ships `WildcardsInsteadOfModifiersValidator` and `DoubleWildcardValidator` to nag about
exactly this.

## Value modifiers

Modifiers attach to a field name with a pipe and chain left to right, so
`CommandLine|base64offset|contains` means encode the value at all three offsets, then match
each result as a substring.

### String matching

| Modifier | Effect |
|---|---|
| `contains` | Wraps the value in wildcards, matching anywhere |
| `startswith` | Match at the beginning |
| `endswith` | Match at the end |
| `cased` | Case-sensitive match, since Sigma is case-insensitive by default |
| `windash` | Expands to all permutations of `-` `/` `–` `—` `―` |
| `re` | Treat the value as a regular expression, case-sensitive by default |
| `re|i` | Case-insensitive regex |
| `re|m` | `^` and `$` match line boundaries |
| `re|s` | `.` matches newlines |

### Encoding, for detecting obfuscation

| Modifier | Effect |
|---|---|
| `base64` | Match the base64 encoding of the value |
| `base64offset` | Match all three byte-offset variants. **Always chain with `contains`.** |
| `utf16le` / `wide` | UTF-16 little-endian bytes |
| `utf16be` | UTF-16 big-endian |
| `utf16` | UTF-16 with a byte-order mark |

```yaml
    selection_encoded:
        CommandLine|base64offset|contains: 'IEX (New-Object Net.WebClient)'
```

For encoded-then-widened payloads, chain `CommandLine|utf16le|base64offset|contains`.

### Logic

| Modifier | Effect |
|---|---|
| `all` | Change list joining from OR to AND, so every value must match |
| `neq` | Field does not equal the value |
| `exists` | Boolean presence check |
| `fieldref` | Compare the field to another field's value rather than a literal |

```yaml
    selection_flags:
        CommandLine|contains|all:
            - '-nop'
            - '-w hidden'
            - '-enc'
```

`fieldref` is underused and expresses relationships instead of values, for example
`Image|fieldref: ParentImage`. Backend support is patchy, and the Splunk backend refuses
ORed fieldref matching outright.

### Numeric, network, and time

`lt`, `lte`, `gt`, `gte`, `neq` for numeric comparison. `cidr` for IPv4 and IPv6 ranges.
`minute`, `hour`, `day`, `week`, `month`, `year` extract the numeric component of a timestamp
without doing timezone conversion.

```yaml
    filter_main_internal:
        DestinationIp|cidr:
            - '10.0.0.0/8'
            - '172.16.0.0/12'
            - '192.168.0.0/16'
```

Sigma has no "is external" primitive, so a connection to a non-RFC1918 address is written as
`not 1 of filter_internal_*` with a CIDR filter.

### `expand`

Marks a value for placeholder substitution at conversion time, for example
`User|expand: '%Administrators%'`. The pipeline supplies the value, and conversion fails
without it, which is the correct behavior because it forces environment specifics into the
environment's config.

### Modifier gotchas

1. `base64offset` without `contains` is almost always a bug, since it only matches when the
   string begins on a 3-byte boundary.
2. `re` bypasses Sigma's escaping, so backslashes inside a regex are regex backslashes.
3. `re` is a portability tax. Not all backends support all regex features, and regex over a
   high-volume field is expensive at query time.
4. `cased` changes matching semantics and some backends cannot express it.
5. Invalid combinations fail at parse time, caught by `InvalidModifierCombinationsValidator`.
6. `windash` multiplies query size, turning two values into ten OR clauses. Worth it for
   command-line flags and pointless for paths.

## The condition language

Operators are `and`, `or`, `not`, brackets, and the quantifiers `1 of` and `all of`.
Binding runs from loosest to tightest as `or`, then `and`, then `not`, then quantifiers,
then brackets. This means `selection and not filter_a or filter_b` almost certainly does not
mean what you want, so use brackets when mixing `or` with anything else.

```yaml
    condition: 1 of them              # any one search identifier matches
    condition: all of them            # every search identifier matches
    condition: 1 of selection_*       # any identifier starting with "selection_"
    condition: all of selection_*     # every identifier starting with "selection_"
```

### Naming conventions that carry meaning

| Prefix | Meaning |
|---|---|
| `selection_*` | Positive matching logic |
| `filter_main_*` | Exclusions that apply everywhere, part of the rule's correctness |
| `filter_optional_*` | Exclusions only benign in some environments, which consumers may delete |

That split matters when rules are published to other teams. `filter_main_` says this is not
a false positive anywhere, and `filter_optional_` says this is your call.

### Idioms worth memorizing

```yaml
condition: selection                                     # simplest possible
condition: selection and not filter                      # one exclusion
condition: all of selection_* and not 1 of filter_*       # the standard shape
condition: 1 of selection_* and not 1 of filter_*         # any of several variants
condition: selection_parent and selection_child           # parent-child pair
condition: selection and not 1 of filter_optional_*       # tuning left to the consumer
```

## Correlation rules

Some behaviors are not suspicious in a single event. A correlation rule has no `detection:`
block, carries a `correlation:` block instead, and references base rules by their `name`.
Base rules must be supplied alongside it, normally in the same file separated by `---`.

```yaml
title: Failed Logon Attempt
name: failed_logon
id: 6f916f6f-7d80-40c6-8f5d-8fcdba1c6d06
status: experimental
description: Base rule matching a single failed interactive logon. Not intended to alert on its own.
logsource:
    product: windows
    service: security
detection:
    selection:
        EventID: 4625
    condition: selection
level: informational
---
title: Multiple Failed Logons For A Single Account
id: 9c249c9c-a0b3-43f9-9c80-bcf0ed4f9009
status: experimental
description: Ten or more failed logons for the same account within five minutes.
correlation:
    type: event_count
    rules:
        - failed_logon
    group-by:
        - TargetUserName
    timespan: 5m
    condition:
        gte: 10
level: medium
```

| Type | Question it answers |
|---|---|
| `event_count` | How many matching events in the window? |
| `value_count` | How many distinct values of a field in the window? |
| `temporal` | Did these different rules all fire in the window, in any order? |
| `temporal_ordered` | Did these rules fire in the window, in the listed order? |
| `value_sum` / `value_avg` / `value_median` / `value_percentile` | Does a statistic cross a threshold? |

Note the hyphen in `group-by`. Timespan units are `s`, `m`, `h`, `d`, `w`, `M`, `y`, where
lowercase `m` is minutes and uppercase `M` is months, so a typo turns a five-minute brute
force window into a five-month one.

**Backend support is uneven and must be tested.** `event_count` and `value_count` are
broadly portable. `temporal_ordered` is rejected outright by both the Splunk and ES|QL
backends. If a detection depends on ordering and the backend refuses it, either express it
as an unordered `temporal` and accept the extra false positives, or implement the ordering
natively in the SIEM and keep the Sigma rule as documentation.

## Global filters

A filter document applies environment tuning across many rules at once, which is how you
avoid editing forty upstream rules to exclude your named admin accounts.

```yaml
title: Filter Internal PKI And Named Admin Accounts
id: ad35ad35-b1c4-44fa-9d91-cd01fe501a10
description: Environment tuning applied across several LOLBIN download rules.
logsource:
    category: process_creation
    product: windows
filter:
    rules:
        - 1a4c1a1a-2f3b-4b71-9a0e-3a7f6b6d1e01     # by id
        - lolbin_download_rule                      # or by name
    selection:
        User|startswith: 'CORP\adm_'
    condition: selection
```

This cleanly separates upstream rules from local tuning, so upgrading a ruleset stops
meaning re-applying forty local edits. Most teams do not use it and should.

## YAML style

UTF-8, LF line endings, four-space indentation, no tabs. Lowercase keys. Single quotes for
strings and double quotes only when the string contains an apostrophe. Numbers unquoted, so
`EventID: 4625` rather than `'4625'`, since there is a validator for exactly this. Quote
anything containing a backslash or a wildcard so YAML does not eat it.

## Filename convention

Two validators police this and they check different things.

`SigmahqFilenameConventionValidator` requires the name to match `[a-z0-9_]{10,90}\.yml` and
to contain at least one underscore, so lowercase, digits and underscores only, and at least
ten characters before the extension. `rule1.yml` fails on length; `Proc-Creation.yml` fails on
both case and character set.

`SigmahqFilenamePrefixValidator` requires a prefix determined by the rule's
`product`/`category`/`service` triple, from a 108-entry map SigmaHQ publishes and pySigma
fetches at validation time. The check is `startswith`, so a longer name passes as long as it
begins with the required prefix.

**You do not have to memorise the map, and should not try.** When the prefix is wrong the
validator tells you the right one in the failure message:

```
issue=SigmahqFilenamePrefixIssue ... filename=lnx_probe.yml prefix=proc_creation_lnx_
```

So the reliable loop is: name the file, run `sigma check -i`, and if it objects take the
`prefix=` value it names. That is a mechanism; the table below is a starting point that will
drift.

The prefixes confirmed against the installed validator, which are the ones this skill's
logsources actually hit:

| Logsource | Prefix |
|---|---|
| `process_creation` + `windows` | `proc_creation_win_` |
| `process_creation` + `linux` | `proc_creation_lnx_` |
| `process_creation` + `macos` | `proc_creation_macos_` |
| `registry_set` / `registry_add` / `registry_delete` / `registry_event` + `windows` | `registry_set_` / `registry_add_` / `registry_delete_` / `registry_event_` |
| `file_event` + `windows` / `linux` / `macos` | `file_event_win_` / `file_event_lnx_` / `file_event_macos_` |
| `image_load` + `windows` | `image_load_` |
| `network_connection` + `windows` / `linux` | `net_connection_win_` / `net_connection_lnx_` |
| `dns_query` + `windows` | `dns_query_win_` |
| `pipe_created` + `windows` | `pipe_created_` |
| `process_access` + `windows` | `proc_access_win_` |
| `create_remote_thread` + `windows` | `create_remote_thread_win_` |
| `driver_load` + `windows` | `driver_load_win_` |
| `ps_script` / `ps_module` / `ps_classic_start` + `windows` | `posh_ps_` / `posh_pm_` / `posh_pc_` |
| `wmi_event` + `windows` | `sysmon_wmi_` |
| `windows` + `service: security` / `system` / `windefend` | `win_security_` / `win_system_` / `win_defender_` |
| `windows`, any other service | `win_<service>_` |
| `proxy` | `proxy_` |
| `webserver` | `web_` |
| `firewall` | `net_firewall_` |
| `dns` | `net_dns_` |
| `antivirus` | `av_` |
| `linux` + `service: auditd` / `syslog` / `sshd` / `cron` | `lnx_auditd_` / `lnx_syslog_` / `lnx_sshd_` / `lnx_cron_` |
| `linux`, no mapped category or service | `lnx_` |
| `macos`, no mapped category | `macos_` |
| `aws` / `azure` / `gcp` / `okta` / `m365` | `aws` / `azure_` / `gcp_` / `okta_` / `microsoft365_` |
| a correlation file | `correlation_` |

Four entries earlier versions of this file got wrong, all confirmed by running the validator:
`webserver_` is really `web_`, `firewall_` is really `net_firewall_`, `ps_script_` is really
`posh_ps_`, and bare `lnx_` does not satisfy a Linux `process_creation` rule, which needs
`proc_creation_lnx_`. The `*_win_` forms for `registry_set`, `image_load` and `pipe_created`
do pass, because the check is `startswith` and the required prefix is the shorter
`registry_set_` / `image_load_` / `pipe_created_`, but they diverge from upstream naming.
