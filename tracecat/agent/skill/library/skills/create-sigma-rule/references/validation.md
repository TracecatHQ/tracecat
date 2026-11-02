# Validation: Making "Validated" Mean Something

**What is where:** Setting up, including the plugin-name vs `-t` target trap · Layer 0 ATT&CK
tags and the exit-code contract · Layer 1 syntax, the validator families, the fabricated-field
backstop and the TLP collision · Layer 2 conversion, pipeline selection, the missing-pipeline
rule and the known traps including the **open Splunk OR-bracketing question** · Layer 3 does it
fire · Layer 4 does it fire on their data · When there is no shell · Recording the evidence.

The claim this skill makes is that a rule has been machine-checked before the analyst sees
it. That claim only holds if the commands below actually ran, so this file covers how to run
them, how to read what comes back, and what to do when there is no shell.

Reference versions are Sigma specification v2.1.0, pySigma 1.5.0, sigma-cli 3.1.0. If
`sigma version` reports something newer, the installed tooling wins and the delivery should
name the version used.

## Setting up

Check first, because the environment may already have it:

```bash
sigma version
```

If it is missing and there is a shell with network access:

```bash
pipx install sigma-cli
# or, where pipx is unavailable
pip install sigma-cli --break-system-packages
```

Then install the backend and pipeline plugins named in the organizational profile. Plugins
are separate packages and `sigma convert` fails without the right one:

```bash
sigma plugin install splunk          # SPL and SPL2
sigma plugin install elasticsearch   # Lucene, EQL, ES|QL, Elastalert
sigma plugin install kusto           # Defender XDR and Sentinel
sigma plugin install sysmon          # the Sysmon pipeline
sigma plugin install windows         # windows-logsources, windows-audit
```

**The plugin name and the `-t` target are not the same string.** This is the most common
stumble in the whole stage, and the obvious move fails:

```
$ sigma convert -t elasticsearch -p sysmon rule.yml
Error: Invalid value for '--target' / '-t': 'elasticsearch' is not one of 'log_scale',
'lucene', 'eql', 'esql', 'elastalert', 'kusto', 'loki', 'secops', 'splunk', 'splunk_spl2'.
 - run sigma plugin list --plugin-type backend for a list of available plugins.
```

The list in that error is whatever is installed on the box, so it will not match this one.
The point is that `elasticsearch` is never in it.


| Profile says | `sigma plugin install` | `-t` target |
|---|---|---|
| Splunk | `splunk` | `splunk`, `splunk_spl2` |
| Elastic / Elasticsearch | `elasticsearch` | `lucene`, `eql`, `esql`, `elastalert` |
| Defender XDR / Sentinel | `kusto` | `kusto` |
| CrowdStrike | `crowdstrike` | `log_scale` |
| Google SecOps | `secops` | `secops` |
| Grafana Loki | `loki` | `loki` |

`sigma list targets` prints the real names on the installed version. Pipelines have the same
shape of problem: `-p sysmon` fails until the plugin is installed, and the error says
"Pipelines not listed here are treated as file names" rather than "install the plugin".

Other backends worth knowing about are `crowdstrike`, `sentinelone`, `cortexxdr`,
`carbonblack`, `qradar-aql`, `insightidr`, `sumologic`, `panther`, `loki`, `datadog`,
`secops` for Google SecOps, `logpoint`, `netwitness`, and `stix`. Check `sigma plugin list`
for what is available and read the state column, because a `devel` or `testing` state is a
real warning rather than a formality.

Useful inventory commands:

```bash
sigma plugin list                    # everything installable, with state
sigma list targets                   # installed backends
sigma list pipelines <backend>       # pipelines valid for that backend
sigma list formats <backend>         # output formats for that backend
sigma list validators                # the 31 built-in validators
```

## Layer 0: ATT&CK tags, which cannot be checked by eye

Run this before `sigma check`:

```bash
python3 <skill dir>/scripts/attack_check.py --rules rules/   # `python` on Windows
```

**The exit code is the contract, and the three failure modes are not interchangeable:**

| Exit | Means | What the delivery must say |
|---|---|---|
| 0 | Every tag checked, every tag valid | Tags verified against ATT&CK vNN |
| 1 | Every tag checked, at least one invalid or semantically mis-paired | Fix them, then re-run |
| 2 | The check did not complete: ATT&CK data unreachable, `--bundle` unreadable, or a rule file unreadable | **The tags were not checked.** Say so in those words |
| 3 | Unexpected error | **The tags were not checked.** Say so in those words |

Only exit 1 means "checked, and some are wrong". Without that distinction there is nothing
separating "checked, found bad tags" from "never checked anything", and both come out of a
non-zero exit. A `[UNV]` result is a third state: the technique exists and its mapping to the
behaviour could not be confirmed. Name those; do not round them to valid.

The script does the semantic half as well as the taxonomy half. A rule tagged `attack.stealth`
plus `attack.t1105` used to get a clean bill of health from it, because each tag is valid in
isolation and the script printed the technique's real tactics on the same screen without ever
comparing them. Now:

```
a_stealth.yml: T1105 requires one of ['command-and-control'], rule declares ['stealth']
```

**Be clear about what this adds over `sigma check`, because it is less than it first appears.**
On validators-sigmahq 0.21.0 the gate already catches that case:
`sigmahq_tags_techniques_without_tactics` fails the same rule and names the missing tactic, and
it is *stricter* than the script — it wants every tactic a technique belongs to, where the
script wants at least one overlap. So the cross-check is largely redundant with the gate on
current tooling. It is kept for three reasons that do hold: it runs at Stage 4, before drafting
is finished; it works from a local `--bundle` in an air-gapped environment where the gate's
ATT&CK-dependent validators cannot run at all; and it does not depend on a SigmaHQ-policy
validator that a team may have excluded.

**What the script genuinely adds is the revoked-technique successor.** 149 techniques in v19.2
carry a `revoked-by` relationship, and `attack.t1562.001` — a tag in countless pre-v19
rulesets — comes back from `sigma check` as a bare *"Invalid MITRE ATT&CK tagging"* and from
the script as *"REVOKED, superseded by T1685 'Disable or Modify Tools'"*. One of those tells you
what to do next.

**What neither tool can catch, and the reason Stage 4 asks for a written rationale**: a wrong
technique whose tactics happen to be right. T1547.001 (Registry Run Keys / Startup Folder) and
T1547.004 (Winlogon Helper DLL) both belong to persistence and privilege-escalation, so a
Winlogon `Shell` rule mis-tagged `attack.t1547.004` returns `Found 0 errors, 0 condition errors
and 0 issues` from the gate and `3 valid, 0 invalid` from the script. Verified this session.
Both gates clean, mapping wrong. Only a human comparing the behaviour to the technique
description catches that, which is what the rationale line is for.

Run against the upstream SigmaHQ corpus — 3,144 rules under `rules/`, 8,953 tags — the
cross-check produced zero false positives, so a report from it is worth acting on.

`attack.ds####` data-source tags are worth knowing about: pySigma's `attacktag` validator
rejects the `ds` namespace outright, so there is no valid form of the tag. Drop it.

It loads the current ATT&CK Enterprise STIX bundle, verifies every `attack.*` tag in the
rules, reports which tactics a technique actually belongs to, and primes pySigma's on-disk
cache so the `attacktag` validator works natively afterwards. It re-execs itself under
sigma-cli's virtualenv when pySigma is not importable from the system interpreter, which is
the usual case with a pipx install.

**Why this is a separate layer rather than a line item.** ATT&CK is versioned and it moves.
In v19 the `defense-evasion` tactic was retired and its techniques were redistributed across
`stealth` and `defense-impairment`, and several technique IDs were merged away. The tag
`attack.defense-evasion` is the single most common tag in every Sigma rule written before
that release, it reads as obviously correct, and it now fails validation. No amount of
careful manual checking catches a rename, because the thing you are checking against is your
own out-of-date memory. This was not a hypothetical during testing of this skill: a draft
that had been "verified manually against attack.mitre.org" carried exactly that tag.

The script's other useful modes:

```bash
python3 <skill dir>/scripts/attack_check.py --tactics        # the valid tactic shortnames
python3 <skill dir>/scripts/attack_check.py --lookup T1055  # which tactics a technique maps to
python3 <skill dir>/scripts/attack_check.py attack.t1105 attack.stealth
python3 <skill dir>/scripts/attack_check.py --bundle ./enterprise-attack.json --rules rules/
```

If it cannot reach the data and no local bundle is available, the tags are unverified. Say so
in those words in the delivery rather than describing them as checked.

## Layer 1: syntax and conventions

```bash
sigma check -i rules/
```

`-i` makes validation issues fail the run rather than only parse errors, which is what you
want while drafting. `sigma list validators` prints **80** on this tooling — the pySigma core
set plus everything `pySigma-validators-sigmahq` adds — so the families below are a map of the
ones you will actually trip over, not a complete listing:

| Family | What it catches |
|---|---|
| **condition** | `dangling_condition` (condition references an identifier that does not exist), `dangling_detection` (an identifier the condition never uses), `all_of_them_condition`, `them_condition_with_single_detection` |
| **logsources** | `fieldname_logsource` (a field name used as a logsource key), `specific_instead_of_generic_logsource` (Windows event IDs where a generic category exists) |
| **metadata** | `identifier_existence`, `identifier_uniqueness`, `duplicate_title`, `duplicate_filename`, `duplicate_references`, `filename_length`, `custom_attributes` |
| **modifiers** | `invalid_modifier_combinations` |
| **tags** | `attacktag`, `cartag`, `cvetag`, `d3_fendtag`, `stptag`, `detection_tag`, `namespace_tag`, `duplicate_tag`, `tag_format`, and four TLP validators that disagree with each other — `tlptag`, `tlpv1_tag`, `tlpv2_tag`, `sigmahq_tags_tlp`, see below. Also `sigmahq_tags_techniques_without_tactics`, which wants every tactic a technique belongs to |
| **fieldnames** | `sigmahq_invalid_fieldname` (HIGH) — **this is the one that catches a fabricated field name.** `ParentCommandLineFake` on a `process_creation` rule fails with `A field name do not exist`. It is a real backstop, and it only knows the taxonomies SigmaHQ has data for, so a rule on an unusual logsource still needs the convert-and-read pass |
| **values** | `control_character`, `double_wildcard`, `escaped_wildcard`, `number_as_string`, `wildcards_instead_of_modifiers` |

**Three operational notes that will cost you time otherwise.**

The `attacktag` and `d3_fendtag` validators fetch reference data over the network on first
use, and in an egress-restricted environment they raise
`RuntimeError: Failed to load MITRE ATT&CK data: HTTP Error 403` and the whole run dies
rather than degrading. Layer 0 solves this for `attacktag` by priming the cache from a
mirror. D3FEND has no equivalent mirror in the loader, so where it cannot be reached:

```bash
sigma check -i -x d3_fendtag rules/
```

Worth knowing: pySigma's default ATT&CK URL is on `github.com/.../raw/...`, which some
egress policies block, while `raw.githubusercontent.com` serves the identical file. That is
why `attack_check.py` tries the second host first.

**`sigma check` can exit 0 when a validator crashes.** The traceback appears on stdout and
the process still reports success, so any CI step that trusts `$?` will happily pass rules
that were never validated. Read the output and confirm you saw a summary line, not a
traceback. Piping through `tail` makes this worse, since the pipeline's status is `tail`'s.

`sigma check`'s help text still says "not yet implemented". Ignore it, the command works.

**Some of these validators encode SigmaHQ's publication policy rather than the Sigma
specification, and TLP is where that bites.** Three TLP validators are registered at once and
their allowed sets are disjoint, so **no TLP marking passes a full `sigma check`**:

| Validator | Allows | Rejects |
|---|---|---|
| `sigmahq_tags_tlp` (SigmaHQ policy, HIGH) | `clear` | `white`, `green`, `amber`, `amber-strict`, `red` |
| `tlpv1_tag` | `white`, `green`, `amber`, `red` | `clear`, `amber-strict` |
| `tlpv2_tag` | `clear`, `green`, `amber`, `amber-strict`, `red` | `white` |

Tested:

```
sigma check -i -x d3_fendtag <tlp.amber rule>                       -> 1 issue
sigma check -i -x d3_fendtag -x sigmahq_tags_tlp <tlp.amber rule>   -> 0 issues
sigma check -i -x d3_fendtag <tlp.clear rule>                       -> 1 issue
sigma check -i -x d3_fendtag -x tlpv1_tag <tlp.clear rule>          -> 0 issues
```

For a ruleset not destined for upstream submission, keep the profile's marking and exclude the
one validator that objects to it, recording the exclusion in every validation note along with
the fact that the tag must come off before any upstream submission. A `tlp` failure is not
evidence that the profile is wrong.

Resolve every issue before moving on. Most are fast fixes: `wildcards_instead_of_modifiers`
means rewrite `'*\foo.exe'` as `|endswith: '\foo.exe'`, `number_as_string` means unquote the
event ID, `dangling_detection` usually means a filter was defined and left out of the
condition, which is a genuine logic bug and not a style nit.

**`dangling_detection` also closes off a construction the layered layout invites**, so it is
worth knowing the exact behaviour before you reach for it. A three-layer rule — anchor,
invariant, discriminator — makes it tempting to define the discriminator and leave the condition
requiring only the first two, as an "available but not required" flag selection. That is not a
Sigma construct. Tested on sigma-cli 3.1.0 / pySigma 1.5.0, with a `process_creation` rule whose
`selection_discriminator` held six command-line flag variants:

```
condition: selection_anchor and selection_invariant     # selection_discriminator defined, unreferenced
  → Found 0 errors, 0 condition errors and 1 issues.
    DanglingDetectionIssue  HIGH  Rule defines detection that is not referenced from condition
    Check failure

condition: all of selection_*                            # discriminator required
  → Found 0 errors, 0 condition errors and 0 issues.

selection_discriminator deleted, condition: selection_anchor and selection_invariant
  → Found 0 errors, 0 condition errors and 0 issues.
```

So when Stage 1's default-state analysis says the flags cannot be required — the tool's
no-argument invocation is in scope — the flag selection comes **out** of the YAML and into the
validation note, or becomes a sibling rule linked with `related:`. Both of those pass clean. What
fails is parking it in the file, and it fails at HIGH severity, which under `-i` takes the whole
run down.

One validator needs a judgement call rather than a fix. `escaped_wildcard` fires whenever a
value contains `\*` or `\?`, which is the only way to match a literal asterisk, and under
`-i` a LOW-severity issue still fails the run. This bites when the behavior you are detecting
genuinely contains wildcards, for example the file masks in
`rar a -df host.rar *.txt *.db *hist*`. Two honest options. Drop the mask and key on the
durable part of the command instead, which is usually the better rule anyway since the masks
change per campaign. Or keep the escape, accept the issue, and record in the note that
`escaped_wildcard` fires deliberately. What you must not do is quietly strip the backslash,
because that turns a literal into a wildcard and silently widens the rule.

## Layer 2: does it compile, and is the query sane?

```bash
sigma convert -t splunk -p sysmon rules/proc_creation_win_certutil_urlcache.yml
```

Stack multiple pipelines, ordered by priority, when the environment needs a local mapping on
top of a standard one:

```bash
sigma convert -t splunk -p sysmon -p ./corp-pipeline.yml rules/
```

`-f` selects the output format and matters more than most people realise. `-f default` gives
a bare query, while backend-specific formats give a deployable artifact carrying the
metadata, level, and description across:

```bash
sigma convert -t splunk -p sysmon -f savedsearches rules/
sigma convert -t lucene -p ecs_windows -f siem_rule_ndjson rules/
```

**Pick the pipeline per logsource, not once per profile.** The profile names the pipelines
the team uses, and which one applies depends on what the rule looks at. A Windows endpoint
rule wants `sysmon` or `windows-logsources`, a proxy or webserver rule against Splunk wants
`splunk_cim`, and an Elastic deployment wants the `ecs_*` pipeline matching the shipper. A
`category: proxy` rule converted with `-p sysmon` compiles cleanly and emits a query against
raw `c-uri` style field names that exist nowhere in the index, so it runs forever and matches
nothing. That is the failure mode this whole stage exists to catch, and it does not announce
itself.

**There is not a pipeline for every logsource-and-backend pair.** `sigma list pipelines lucene`
lists every installed pipeline compatible with the backend, not only the ones the
elasticsearch plugin ships, so read it carefully:

```
$ sigma list pipelines lucene
crowdstrike_fdr  crowdstrike_falcon  ecs_windows  ecs_windows_old  ecs_zeek_beats
ecs_zeek_corelight  ecs_kubernetes  ecs_macos_esf  sysmon  windows-logsources  windows-audit
```

Eleven entries, and the ones that map Sigma field names onto an Elastic index are the six
`ecs_*`: Windows, Zeek, Kubernetes, macOS ESF. `sysmon`, `windows-logsources` and
`windows-audit` come from the sysmon and windows plugins and map generic logsources onto
Windows event IDs, which is a different job and no help to a Linux rule; the `crowdstrike_*`
pair belongs to the crowdstrike plugin. So: no Linux process-creation pipeline, and no
webserver pipeline at all. For a Linux-primary shop on
Elastic — an extremely common shape — Stage 6's instruction to name a pipeline has no valid
answer, and the wrong answer fails silently in exactly the way this stage exists to prevent:

```
$ sigma convert -t lucene -p ecs_windows      proc_creation_lnx_curl_pipe_to_shell.yml
((CommandLine:(*curl\ * OR *wget\ *)) AND (...)) AND (NOT (ParentImage:(*\/apt\-get OR *\/dpkg)))
$ sigma convert -t lucene --without-pipeline  proc_creation_lnx_curl_pipe_to_shell.yml
((CommandLine:(*curl\ * OR *wget\ *)) AND (...)) AND (NOT (ParentImage:(*\/apt\-get OR *\/dpkg)))
```

**Byte-identical, both exit 0.** `CommandLine` and `ParentImage` are not ECS fields, so both
queries run forever and match nothing. The mismatched Windows pipeline mapped nothing at all;
the only thing it contributed was the appearance of having mapped something.

So: **if no pipeline exists for the rule's logsource on the target backend, convert with
`--without-pipeline`** and state in the validation note that the query carries generic Sigma
field names which will not match the index until the team supplies a mapping. Do not substitute
a pipeline for a different platform. Do not hand-write a pipeline from the profile alone
either, because a guessed field mapping is a fabricated field name wearing a different hat.
Note that omitting the pipeline is not an accidental path — Lucene refuses outright and tells
you to pass `--without-pipeline` — so the model has to choose it consciously, which is the
right shape for a decision that has to be recorded.

**Conversion success is necessary and not sufficient. Read the query, starting with the
brackets.** Known traps worth checking on every conversion:

> ### ⚠ OPEN QUESTION — UNDER TEST, do not act on this yet
>
> **The stated cause of the trap below is wrong, and the conclusion may be too. Nothing in
> this file, in `SKILL.md` Stage 4, or in `check_grouping.py` has been changed pending a live
> test, because this is a request to *remove* a warning and that deserves a higher bar than
> adding one. Keep following the guidance below until this is settled.**
>
> **What is confirmed on the installed tooling** (pySigma 1.5.0, backend-splunk 2.1.0,
> backend-elasticsearch 2.1.1):
>
> ```
> >>> SplunkBackend.precedence   -> (ConditionNOT, ConditionOR, ConditionAND)
> >>> SplunkBackend.parenthesize -> False
> >>> LuceneBackend.precedence   -> (ConditionNOT, ConditionOR, ConditionAND)
> >>> LuceneBackend.parenthesize -> True
> ```
>
> Both backends declare the **same** precedence, so precedence is not what differs between
> them. Lucene brackets because `parenthesize = True`; Splunk omits brackets because it is
> `False`. The passage below, and `check_grouping.py`'s docstring, both attribute the
> behaviour to a precedence mismatch. That attribution is not what the code does.
>
> **Why the conclusion is also in question.** Splunk Enterprise documents that "the search
> command evaluates OR before AND operators", which is exactly what `(NOT, OR, AND)` encodes.
> On that reading the emitted query parses as `EventID=1 AND (Image OR OriginalFileName) AND
> CommandLine` — which is what the rule means. The rule would then be neither widened nor
> narrowed, and the worked example below, which predicts a rule that "fires on all scheduled
> task execution on the estate", would be describing a query that does no such thing.
>
> **The minimal test, so someone with a Splunk instance can settle it in ten minutes.**
> Rule: `assets/splunk-grouping-test/proc_creation_win_schtasks_or_under_and.yml`, with the
> full protocol in that directory's `README.md`. Its `selection` is a list of two maps (an OR of two AND groups) and its
> condition ANDs that with a NOT filter. Command and emitted query, reproduced this
> session:
>
> ```
> $ sigma convert -t splunk -p sysmon proc_creation_win_schtasks_or_under_and.yml
> EventID=1 (ParentImage="*\\svchost.exe" ParentCommandLine="*-s Schedule*")
> OR (ParentImage="*\\taskeng.exe" Image="*\\AppData\\Local\\*") NOT Image="*\\OneDriveSetup.exe"
> ```
>
> The same rule on `splunk_spl2`, where the operators are explicit and the ambiguity is
> therefore readable directly:
>
> ```
> FROM main WHERE EventID=1 AND (svchost AND schedule) OR (taskeng AND appdata) AND NOT onedrive
> ```
>
> **The two candidate readings**, which is what needs settling against real data:
>
> | Reading | Parse | Consequence |
> |---|---|---|
> | A — the current guidance | `(EventID=1 AND svchost AND schedule) OR (taskeng AND appdata AND NOT onedrive)` | First branch has lost the path restriction and the filter. Rule is silently **widened** |
> | B — OR-before-AND per Splunk's docs | `EventID=1 AND ((svchost AND schedule) OR (taskeng AND appdata)) AND NOT onedrive` | Exactly what the rule means. Nothing is wrong |
>
> **How to settle it.** Two indexed events separate the readings, and the exact field values
> plus what each outcome means are in that directory's `README.md`. Run the emitted query
> verbatim, report which events come back, and record the Splunk version.
>
> **Settled, whichever way the live test goes:** of the three remedies offered below,
> restructuring the condition does nothing. An explicit parenthesised condition produces
> byte-identical Splunk output to the implicit form — confirmed this session on the two
> fixtures above, which differ only in their `condition` — so a model that tries that fix
> believes it succeeded when nothing changed. **The passage below and `check_grouping.py`'s
> Fix line both still offer restructuring as one of three options. That is frozen pending the
> test, so until then: skip that option. Only splitting the rule or hand-bracketing the
> deployed query do anything.**
>
> **Also frozen, also wrong:** the SPL quoted in the passage below, and in
> `check_grouping.py`'s docstring, brackets only the first of the two groups. The backend
> brackets **both** — see the real output above. The missing bracket is the outer one around
> the OR pair, which is the whole point, and the rendering below obscures it. Read the output
> above rather than the illustration below.

- **The Splunk backend does not parenthesize an OR group nested under an AND.** This is the
  worst trap here, because unlike the others it silently *widens* the rule. pySigma's Splunk
  backend declares precedence as NOT, OR, AND, and SPL binds the opposite way, NOT then AND
  then OR. A rule whose selection is a list of maps, meaning an OR of ANDs, converts like
  this:

  ```
  EventID=1 (ParentImage="*\\svchost.exe" ParentCommandLine="*-s Schedule*")
  OR ParentImage="*\\taskeng.exe" Image="*\\AppData\\Local\\*" NOT Image="*\\OneDriveSetup.exe"
  ```

  Splunk reads that as `(EventID=1 AND svchost AND schedule) OR (taskeng AND path AND NOT
  filter)`. The first branch has lost the path restriction and every filter, so the rule
  fires on all scheduled task execution on the estate. `sigma check` passes and
  `sigma convert` succeeds throughout. The same rule converts correctly on kusto, lucene, and
  ES|QL, which all bracket properly, so this is a Splunk-specific defect rather than a bad
  rule. Detect it by checking whether any `OR` in the output sits outside brackets. Fix it by
  restructuring the selection so the top level is pure AND, splitting the OR branch into its
  own rule, or hand-bracketing the deployed query and noting that you did.

  **The inner brackets are not reassurance.** In the example above the inner AND group *is*
  parenthesized, which is exactly what makes this easy to skim past. The bracket that is
  missing is the one around the whole OR group, separating it from the logsource condition
  and the `NOT`. Check the outermost level, not the innermost.

  **What is safe and what is not.** The backend collapses a multi-value list on one field
  with one modifier chain into `Field IN ("a", "b")`, which is unambiguous. Anything it
  cannot collapse becomes bare OR'd clauses at the top level. Two constructs reliably
  trigger it, and both are things this skill otherwise tells you to do:

  | Construct | Splunk output | Safe? |
  |---|---|---|
  | `CommandLine\|contains: [a, b]` | `CommandLine IN ("*a*", "*b*")` | Yes |
  | `Image\|endswith: [a, b]` | `Image IN ("*a", "*b")` | Yes |
  | `CommandLine\|windash\|contains: [a, b]` | ten bare `CommandLine="..." OR ...` clauses | **No** |
  | list of maps (`Image` OR `OriginalFileName`) | `Image="..." OR OriginalFileName="..."` | **No** |

  **Remedies, tested rather than assumed.** Bracketing the `condition` does nothing, because
  the backend ignores it. `splunk_spl2` has the identical defect. What actually works:

  - **For `windash`, write the dash variants out as an explicit `contains` list.** That
    collapses to a safe `IN (...)` and matches exactly the same events. It is more verbose in
    the YAML and correct in the query, which is the right trade:

    ```yaml
        selection_flags:
            CommandLine|contains:
                - '-urlcache'
                - '/urlcache'
                - '–urlcache'
                - '—urlcache'
    ```

  - **For `Image` OR `OriginalFileName` there is no in-rule fix.** Choose deliberately:
    split the OriginalFileName branch into a sibling rule, drop it and record the renaming
    evasion gap, or keep it and hand-bracket the deployed query, noting in the validation
    note that the converted query must be wrapped before deployment. Do not silently drop the
    field to make the conversion look tidy, because renaming is the cheapest evasion there is.

  Run `scripts/check_grouping.py` rather than eyeballing this. Where the rule's logsource has
  no pipeline on the target backend, pass the script `--without-pipeline` — Lucene refuses to
  convert without one, so on a Linux-on-Elastic package the check is otherwise unrunnable.

- **A Windows rule converted with `-p sysmon` alone emits a bare `EventID=1`** with no source
  or index constraint, which matches nothing in a real Splunk Windows ingest where the field
  is `EventCode` under a `source="WinEventLog:..."`. Stack the pipelines,
  `-p sysmon -p splunk_windows`, and check that the output carries a `source=` term.
- **`splunk_cim` with `-f default` emits bare data model field names** such as `Web.dest=`,
  which is unrunnable as a plain search. Use `-f data_model` to get the `tstats` form, or
  expect the engineer to wrap it themselves.

- **CIDR values go literal on some backends.** Splunk and Elasticsearch both emit
  `src_ip="10.10.50.0/24"`, which performs a real CIDR match only if the target field is
  typed as an IP, and silently matches nothing against a keyword field. The kusto backend
  instead emits an explicit `ipv4_is_in_range(...)`. Same rule, three different semantics.
- **`windash` multiplies clause count.** Two flag values become ten OR clauses. Fine for
  command lines, wasteful anywhere else.
- **`fieldref` is refused by several backends.** The Splunk backend returns
  `ORing FieldRef matching is not yet supported`.
- **`temporal_ordered` correlations are rejected** by both Splunk and ES|QL. Chained
  `temporal` over `event_count` converts to Splunk correctly and emits a malformed nested
  query on ES|QL, so it needs checking before it ships.
- **`cased` cannot be expressed by every backend.**
- **The Defender XDR mapping inverts what "Image" means on some categories.** For
  `image_load`, Sigma's `ImageLoaded` becomes `FolderPath` and Sigma's `Image`, meaning the
  loading process, becomes `InitiatingProcessFolderPath`. The converted KQL therefore reads
  as though the subject and object have swapped, which is correct and looks wrong. Flag it in
  the note so a reviewer hand-editing the KQL does not "fix" it into the opposite logic.

Put the converted query into the validation note. The engineer receiving the rule should be
able to see what will actually run without having to convert it themselves.

## Layer 3: does it fire on the behavior?

Neither of the layers above proves the rule matches anything. Where the environment allows
it, the cheapest options are:

- **[EVTX-ATTACK-SAMPLES](https://github.com/sbousseaden/EVTX-ATTACK-SAMPLES)**, a curated
  corpus of Windows event logs containing real attack behavior. Free, offline, no lab.
- **[Hayabusa](https://github.com/Yamato-Security/hayabusa)**, which runs Sigma natively over
  EVTX at speed and is the fastest loop for Windows rule testing.
- **[Chainsaw](https://github.com/WithSecureLabs/chainsaw)** and
  **[Zircolite](https://github.com/wagga40/Zircolite)** for Sigma over EVTX and other
  forensic artifacts.
- **[Atomic Red Team](https://github.com/redcanaryco/atomic-red-team)** to execute the
  technique in a lab and generate real telemetry, mapped to ATT&CK so it lines up with the
  rule's tags.

This skill does not usually have access to any of these, so the correct output is a test
plan naming which of them the analyst should use, not a claim that the rule fires.

## Layer 4: does it fire on their data?

Convert the rule and run it over 30 days of production data before deployment. Nothing in
layers 1 to 3 says anything about the false positive rate in a specific environment, and
only this does. This is the layer a report-derived rule cannot skip, and the reason every
rule ships as `experimental` with a retrohunt window in the test plan.

## When there is no shell

Some environments have no shell, no network, or both. In that case:

1. Work through the pre-flight checklist in `rule-quality.md` line by line.
2. Re-read each rule specifically for the things validators catch, which are raw wildcards
   where a modifier belongs, quoted numbers, filters missing from the condition, identifiers
   defined and never referenced, invented field names, and malformed tags.
3. Label the delivery unambiguously:

   > **Not machine-validated.** `sigma check` and `sigma convert` were not run in this
   > session. Run `sigma check -i <file>` and
   > `sigma convert -t <backend> -p <pipeline> <file>` before review.

Never describe a rule as validated when the commands did not run. The value of this skill is
that the word means something.

## Recording the evidence

Every validation note carries a block like this, with the real command output rather than a
paraphrase:

```
Validated with sigma-cli 3.1.0 / pySigma 1.5.0 against Sigma specification v2.1.0
  python3 scripts/attack_check.py --rules .
    → ATT&CK Enterprise v19.2, 2 valid, 0 invalid, cache primed
  sigma check -i -x d3_fendtag proc_creation_win_certutil_urlcache.yml
    → Found 0 errors, 0 condition errors and 0 issues
  sigma convert -t splunk -p sysmon proc_creation_win_certutil_urlcache.yml
    → converted, query below
```

Note what this example does *not* do. It does not exclude `attacktag` and then claim the tags
were checked by hand, because Layer 0 above primes the cache so the validator can run, and
"verified manually against attack.mitre.org" cannot catch a tactic rename — which is the
whole reason Layer 0 exists. If `attack_check.py` genuinely could not reach the ATT&CK data,
the honest line is that the tags are unverified, named as such.
