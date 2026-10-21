# Sigma Organizational Profile

Save this as `sigma-org-profile.md` in the Claude project attached to your detection work,
or keep it as a file and attach it when you run the skill. The skill reads it at Stage 0 so
it stops asking the same questions every time.

Fill in what you know and leave the rest. A half-complete profile is considerably better
than none, and the skill will ask about the gaps once and offer to write the answers back.

## The defaults

Every question below has a default, so you can say "use the defaults" and get a draft
immediately. These are the assumptions the skill falls back on, and they are also what it
uses when nobody is available to answer at all. None of them are guesses about your
environment, they are the safest position to take when the environment is unknown.

| Question | Default | Why this default |
|---|---|---|
| Backend | `splunk` with the `sysmon` pipeline | The most widely deployed combination, and the best-supported pySigma backend. Conversion for a second backend is one command. |
| Telemetry collected | Sysmon process creation, Windows Security channel, DNS, and proxy assumed present. Everything else assumed absent. | Rules are written against the telemetry most teams have, and anything needing more is declared in `logsource.definition` rather than assumed. |
| Environment specifics | None known | No `filter_optional_*` blocks get written, since a filter with an invented value is worse than none. False positives become prose and tuning actions instead. |
| `author` | `CTI Team` | A placeholder that is obviously a placeholder. |
| Naming convention | SigmaHQ | The convention every public rule already follows. |
| TLP marking | none applied | Better to omit a marking than to assert the wrong one. |
| False positive tolerance | low | Assume the SOC disables noisy rules, which pushes drafts toward tighter discriminators. |
| Hunting rules wanted | yes | Deliberately broad rules ship tagged `detection.threat-hunting` at `level: informational` rather than being tightened into uselessness or dropped. |
| Abstraction level | durable and broader | Higher on the Summiting the Pyramid ladder, because a rule that dies on the actor's next build wastes the review it took to deploy it. |
| Atomic indicators in Sigma | no | Hashes, IPs, and domains go to indicator matching, which has a different lifecycle. |
| Global filters maintained | assumed not | Environment tuning gets written into the validation note as an action rather than assumed to have a home. |
| Retrohunt window | 30 days | Long enough to be meaningful and short enough that most teams still hold the data. |
| Platform priority | Windows endpoints first | Where the majority of published tradecraft and Sigma coverage sits. |
| Conversion pipelines used | whatever the logsource needs, chosen per rule at Stage 6 | The pipeline follows the rule's logsource, not the profile. A proxy rule converted with `-p sysmon` compiles happily against field names nothing in the index uses. Where no pipeline exists for the pair, `--without-pipeline` with the gap recorded. |
| Output format wanted | `-f default`, a bare query per rule | Universally supported, readable in a review, and it does not pretend to be deployable. A deployable artifact needs a named format, so it is opt-in. |
| Log retention available for retrohunting | unknown, so the test plan asks rather than assumes | Retention varies by index and by licence in ways nobody can default. The retrohunt window above is the *requested* window; whether the data exists is a question for the analyst. |

Anything taken from this table gets recorded as an assumption in the validation note, so
testing corrects it.

---

## 1. Detection platform

**Primary SIEM or backend:**
`splunk` / `esql` / `lucene` / `eql` / `kusto` (Defender XDR or Sentinel) / `secops` /
`crowdstrike` / `sentinelone` / `cortexxdr` / `qradar-aql` / `loki` / other

**Conversion pipelines used:**
For example `sysmon`, `windows-logsources`, `splunk_windows`, `splunk_cim`, `ecs_windows`,
`microsoft_xdr`, `sentinel_asim`, plus any in-house pipeline file and its path.

**Secondary backend, if rules must convert for two platforms:**

**Output format wanted:**
Bare query (`default`), or a deployable artifact such as `savedsearches` for Splunk or
`siem_rule_ndjson` for Kibana.

## 2. Telemetry actually collected

Mark each as collected, partial, or not collected. "Partial" is the interesting answer, so
add the caveat where there is one, for example "Sysmon on servers only" or "script block
logging on the DC estate".

| Source | Status | Notes |
|---|---|---|
| Sysmon (and config, for example sysmon-modular) | | |
| Native Windows process creation, 4688 with command line auditing | | |
| EDR process telemetry (which product) | | |
| PowerShell script block logging, 4104 | | |
| PowerShell module logging, 4103 | | |
| Windows Security channel (which subcategories are audited) | | |
| Registry auditing | | |
| DNS query logs (client, server, or both) | | |
| Web proxy logs (which product) | | |
| Firewall logs | | |
| Linux auditd or Sysmon for Linux | | |
| macOS endpoint telemetry | | |
| AWS CloudTrail | | |
| Azure or Entra ID sign-in and audit logs | | |
| Microsoft 365 unified audit log | | |
| Google Workspace | | |
| Okta or other IdP | | |
| Kubernetes audit logs | | |

**Known telemetry gaps we already accept:**

**Log retention available for retrohunting:**
For example 30 days hot, 90 days searchable, 1 year in cold storage.

## 3. Environment specifics that cause false positives

The single most valuable section, because it turns generic rules into ones your SOC will
keep.

**Administrative and deployment tooling in use:**
SCCM, Intune, Ansible, PDQ, Tanium, BigFix, Chef, Puppet, login scripts, other.

**Vulnerability scanners and their source ranges:**

**Backup and security agents that behave like attackers:**
Raw disk access, high-privilege process access, driver loads.

**Admin account naming pattern:**
For example `CORP\adm_*`, or a named service account prefix.

**Internal ranges that should be excluded from external-connection logic:**

**Internal PKI, update, or artifact endpoints that LOLBIN rules will hit:**

**Developer or build estate, and whether it is in scope:**

**Gold image and VDI paths, or software installed everywhere that looks unusual elsewhere:**

**Business applications with genuinely odd behavior:**
The line-of-business app that spawns cmd.exe from Excel, and anything else the SOC has
already learned to ignore.

## 4. House conventions

**`author` value to use on rules:**

**Rule naming convention, if it differs from SigmaHQ:**
The default is `proc_creation_win_<description>.yml` with SigmaHQ title conventions.

**Mandatory internal metadata fields:**
For example an owner field, a ticket reference, an internal severity mapping.

**Licence to apply, if any:**
For example `DRL-1.1`.

**TLP marking to apply by default:**
For example `tlp.amber`.

**Where rules are stored and how they are deployed:**
Repository, branch and review process, and whether conversion happens at deploy time.

## 5. Detection posture

**Tolerance for false positives on alerting rules:**
Low (a rule firing more than a few times a week gets disabled) / medium / high.

**Do you want hunting rules alongside alerting rules?**
If yes, the skill will tag deliberately broad rules `detection.threat-hunting` with
`level: informational` rather than tightening them into uselessness.

**Preferred abstraction level:**
Durable and broader (higher on the Summiting the Pyramid ladder, noisier, survives
retooling), or specific and quieter (lower, tighter, dies faster). "Durable" is the better
default for most teams.

**Do you want atomic indicators in Sigma rules at all?**
The default is no, with hashes, IPs, and domains routed to indicator matching instead. Say
so if you want short-lived incident rules tagged `detection.emerging-threats` as an
exception.

**Do you maintain global filter documents for environment tuning?**
If yes, the skill will keep environment specifics out of the rules and note what belongs in
your filter repo instead.

**Who owns tuning after handover, and what is the promotion criterion for moving a rule from
`experimental` to `test`?**

## 6. Scope and priorities

**Platforms that matter most, in order:**
For example Windows endpoints, then Entra ID, then AWS.

**Crown jewel systems worth a `scope` field:**
Domain controllers, certificate authorities, build systems, payment estate.

**Threats or actors currently in your intelligence requirements:**
This helps the skill prioritize which behaviors in a report are worth drafting first.

---

*Profile last updated: YYYY-MM-DD*
