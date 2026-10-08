# Hypothesis library

Decision questions by finding family, and where each answer usually lives. Make every question specific to the case before asking it, with real names, IPs, pods, and UTC times. A question the SIEM cannot answer becomes an open question in the output, addressed to the role in "Who can answer". You do not post it; the workflow does.

## Where answers live

The source names in this table are examples from one SIEM schema: `<adapt to your SIEM>`. Rows that point at business-context apply only when you have that skill; without it, the answer is "no baseline recorded".

| Data | SIEM source |
|---|---|
| GuardDuty findings and history | `aws_guardduty_logs` |
| AWS API activity and SSO logins | `aws_cloudtrail_logs` |
| Network flows, including the pre SNAT pod address | `aws_vpc_flow_logs` (`pktSrcAddr`, `pktDstAddr`) |
| Source control, product audit and other webhooks | the catch-all log table, filtered by source type |
| Kubernetes audit | `kubernetes_audit_logs` if it has rows; otherwise not in the SIEM |
| Tenant and workload run attribution (traces, job systems) | Pod to tenant: not in the SIEM, ask platform. Tenant that was calling AWS at a given time: `aws_cloudtrail_logs`, the shared workload role's AssumeRole events, where `requestParameters.roleSessionName` follows your organisation's session naming pattern (business-context) |
| Earlier cases for the same principal, resource, or type | Tracecat case search, last 30 days |
| Other GuardDuty findings for the same identity, 90 days | `aws_guardduty_logs`, matching the session name, principal, or access key in the raw record |
| SSO username to a named person | First the employee list in business-context (name and email), matched by email local part or first initial plus surname; state the pattern used. Then IAM Identity Center or IdP sign in logs, if ingested: run one count query for the username first. When no IdP sign in source with these usernames is ingested, say "not ingested" once |
| What normal looks like for a person, role, or schedule | business-context, baselines section; if it is not there, the answer is "no baseline recorded", not "abnormal" |
| Whether activity follows a customer or platform schedule | business-context, baselines section: known recurring customer workloads (their schedules and report times) and platform owned schedules. Compare the minute of hour of each occurrence with those schedules and with the shared workload role's own AssumeRole minute distribution (recipe below) |

Confirm a source has rows for the account and window before relying on an empty result.

## Baseline (every case)

- Is this a GuardDuty sample (`sample: true`)?
- Does a known benign pattern in business-context (Known benign patterns) match this finding completely, not just by name?
- Was there a deploy, release, infrastructure-as-code run, or helm operation in the window?
- Were any write or change actions made by the actor or on the resource?
- What kind of actor is this (human, automation, service), and what decides it? See "Classify the actor before you triage" in aws-cloud-incident-response-core.
- Has the same identity triggered other GuardDuty findings in the last 90 days, and were any of them cased?
- Has the same principal, resource, or finding type appeared in an earlier case, and how was it closed?
- If the case holds more than one finding: do they share an actor, a resource, and a window, or are they separate events that happen to be the same type?

## Human operator in Kubernetes

Types: `Execution:Kubernetes/*`, `PrivilegeEscalation:Kubernetes/*`, `Persistence:Kubernetes/*`, `Discovery:Kubernetes/*`, `Impact:Kubernetes/*`

- Which person do the SSO and directory logs link to this session? Name the log that makes the link. If none does, the person is unresolved.
- Did that person complete an SSO login with an MFA challenge from the same IP shortly before?
- Is that IP and time of day within their recorded baseline (business-context baselines section, or their last 30 days of sign ins)?
- Is this a helm release (`fieldManager=helm`) or part of a deploy in the window?
- What did the command touch: config, secrets, credentials, or workload definitions?

## DNS based findings from compute

Types: `Trojan:EC2/DGADomainRequest.*`, `Trojan:EC2/DNSDataExfiltration`, `Backdoor:EC2/C&CActivity.B!DNS`, `CryptoCurrency:EC2/*!DNS`, `Impact:EC2/*!DNS`, `Trojan:EC2/DriveBySourceTraffic!DNS`, `Trojan:EC2/PhishingDomainRequest!DNS`, `UnauthorizedAccess:EC2/MetadataDNSRebind`

On a Kubernetes cluster the flagged node is almost never the source: the cluster DNS pods (CoreDNS) forward lookups for every other pod, so GuardDuty flags the node that hosts them. Ask, in this order:

- Do the lookup times follow a schedule that business context already names? Take every occurrence time (the GuardDuty finding's revisions in `aws_guardduty_logs`, one row per `updatedAt`, plus `eventFirstSeen` and `eventLastSeen`), reduce each to minute of hour and day of week, and compare with the known recurring customer workloads and the platform owned schedules in the baselines section of business-context. A steady match with a customer workload is a lead for the Customer explanation and fills `deployment.customer` with that tenant label and the match rate, as an inference; a match with a platform schedule is a lead for Platform. A match shows that the times overlap. It does not show who made the lookup or that it was expected, so keep asking the questions below until workload evidence other than timing supports the explanation. Ask this before anything else: it tells you which attribution to test first.
- Did any connection follow a lookup? Flow logs from **every** node in the cluster to any non AWS, non known SaaS destination in the five minutes after each query time. No connection is evidence for resolution only, and it is one point, not the verdict: command and control can run inside DNS itself or over a connection opened before the lookup. Weigh it with the periodicity, domain set and query size questions below.
- Are the queries periodic? Pull query timestamps from GuardDuty history for the finding. A fixed interval or fixed time of day says schedule. Jitter and bursts say beacon.
- Were several nodes flagged for the same domains at the same minute? If yes, they are the CoreDNS hosts, and the client is elsewhere.
- Which pod sent the upstream query (`pktSrcAddr` to the VPC resolver on port 53)? If it is CoreDNS, the client is an open question for platform: CoreDNS logs for these domains, then the tenant from traces.
- When business-context names a shared workload role: did that role (`<your-shared-workload-role>`) call `sts:AssumeRole` into a customer account within a minute of each lookup, consistently across occurrences? The recipe below shows the logic in one SQL dialect; table, column and function names are `<adapt to your SIEM>`: `SELECT receivedAt, JSONExtractString(rawLog, 'requestParameters', 'roleArn') AS role, JSONExtractString(rawLog, 'recipientAccountId') AS account FROM aws_cloudtrail_logs WHERE receivedAt BETWEEN <lookup minus 90 s> AND <lookup plus 90 s> AND eventName = 'AssumeRole' AND JSONExtractString(rawLog, 'userIdentity', 'arn') LIKE '%<your-shared-workload-role>%' ORDER BY receivedAt LIMIT 50`, once per occurrence, and `SELECT toMinute(receivedAt) AS m, count() FROM aws_cloudtrail_logs WHERE receivedAt >= now() - INTERVAL 7 DAY AND eventName = 'AssumeRole' AND JSONExtractString(rawLog, 'userIdentity', 'arn') LIKE '%<your-shared-workload-role>%' GROUP BY m ORDER BY count() DESC LIMIT 20` for the schedule shape. Group by the assumed role, the account, and the tenant. When the role's `requestParameters.roleSessionName` follows a pattern that names the tenant (business-context gives the pattern, for example `<prefix>-<tenant id>-run-<run id>`), every AssumeRole event names the tenant that was running; add `substring(JSONExtractString(rawLog, 'requestParameters', 'roleSessionName'), 1, <tenant prefix length>) AS tenant` to both queries. In the schedule query, also select the assumed role and the account and group by all of them (`GROUP BY tenant, role, account, m`), so the minute distribution is per tenant. A distribution that merges every tenant cannot give one tenant's match rate. A tenant that is running within a minute of every occurrence, and whose minute of hour distribution over seven days covers the occurrence minutes, is the probable tenant; fill `deployment.customer` with the tenant label and the match rate. Report it as an inference from timing, with the account, the role and the match rate. Report the match as an inference from timing, never as documented behaviour; when business-context lists that tenant's schedules, quote those and nothing more. No match means no correlated `AssumeRole` in the window. It does not show that the workload never touched AWS: a session issued earlier can be reused. Say so, and attribution stays with platform. A query error here is not an answer: read the table schema and rerun; this question is decisive for the family.
- Does the domain set look like a feed being resolved (many distinct domains, each queried once or twice) or a beacon (one or two domains, queried steadily)?
- For `DNSDataExfiltration`: how large are the query names and how many distinct subdomains per parent? Feed resolution uses short names; exfiltration uses long, unique ones.
- For `MetadataDNSRebind`: this contradicts any control that keeps workloads away from the instance metadata service. Which pod, and is it a platform service or a tenant workload?

## Flow based findings from compute

Types: `Backdoor:EC2/*` (non DNS), `CryptoCurrency:EC2/*` (non DNS), `Trojan:EC2/BlackholeTraffic`, `Trojan:EC2/DropPoint`, `UnauthorizedAccess:EC2/TorClient`, `UnauthorizedAccess:EC2/TorRelay`, `UnauthorizedAccess:EC2/MaliciousIPCaller.Custom`, `Impact:EC2/PortSweep`, `Impact:EC2/WinRMBruteForce`, `Impact:EC2/AbusedDomainRequest.Reputation`, `Behavior:EC2/*`, `DefenseEvasion:EC2/*`

- Which pod made it? Pre SNAT flow logs (`pktSrcAddr`) for the node's ENIs in the window. Here the flagged node is the right node.
- Which service or tenant owns that pod? A platform service is answerable from business context; a tenant needs platform.
- How much data moved and for how long? One short connection to a listed IP is consistent with a lookup or a scanner reply, and also with a short check-in, so size and duration alone do not clear it. Weigh it with the destination, the direction and the finding type. Sustained or repeated transfer is stronger evidence of real use.
- Is the destination covered by a known benign pattern in business-context (for example a DNS over HTTPS resolver, inbound scanners hitting a load balancer, Tor exit matches on inbound only traffic)?
- Is the destination a security relevant endpoint a workload would deliberately touch (Tor exit list, malware sandbox, threat feed)? Say so as an inference, not a conclusion.
- Is the destination or the domain one of the customer domains listed in business context, or infrastructure that resolves to one? A customer's workload reaching its own estate is expected.
- For `PortSweep` and `Behavior:EC2/*`: does the pattern match autoscaling, a health check, or a scanner workload, and did it start at a deploy or schedule boundary?

## Credential use

Types: `UnauthorizedAccess:IAMUser/*`, `CredentialAccess:*`, `*InstanceCredentialExfiltration*`, `Recon:IAMUser/*`, `Discovery:IAMUser/*`, `Persistence:IAMUser/*`, `PenTest:IAMUser/*`, `InitialAccess:IAMUser/*`, `Exfiltration:IAMUser/*`, `Impact:IAMUser/*`

- Which principal, and is it a human, CI, a platform role, or a customer assumed role? Decide from the session name, user agent family, and sign in events, and say which. A browser user agent under an SSO role is a person at the console even when the name cannot be resolved.
- What has the same identity done across all accounts in the last 30 days, by user agent family, and does the flagged activity fit?
- Where was it used from, and is that within the principal's recorded baseline?
- Was the same credential used from two places at once?
- Were any write actions made, and which?
- For `PenTest:*`: the user agent names a pentest distribution. Is there a documented pentest window? When business context documents none, the answer is no, and the question goes to security.
- For `InstanceCredentialExfiltration`: which node's instance profile, and from which IP outside AWS? A tenant workload assuming a cross account role from the shared workload role is not this; this is the node's own credentials used elsewhere, and it contradicts a control.

## Configuration and posture change

Types: `Policy:IAMUser/*`, `Policy:S3/*`, `Stealth:IAMUser/*`, `Stealth:S3/*`, `DefenseEvasion:IAMUser/*`, `Impact:S3/*`

- Who made the change: a human session, the infrastructure-as-code pipeline's identity, or a controller?
- Is there a matching infrastructure-as-code run, PR, or release?
- What is now exposed or disabled, and is it still in that state?
- For `Stealth:*` (logging disabled, trail stopped, GuardDuty detector changed): which principal, and is it an infrastructure-as-code run? A human doing this outside a run is Neither regardless of who.
- For `Policy:S3/BucketPublic*` and `Policy:S3/BucketAnonymousAccessGranted`: which bucket, is it one the platform serves publicly by design, and did the change come from infrastructure as code?
- For failed `Impact:S3/*` deletes in a log archive account: the service control policy working. Still name the principal.

## Inbound probing

Types: `Recon:EC2/*`, `UnauthorizedAccess:EC2/*BruteForce*`, `*PortProbe*`

- Did the traffic reach a service, or stop at a NAT gateway or an ALB returning 404?
- Is the source a known internet scanner?
- Any successful authentication from that source?

## Generic (any other type)

- Who or what acted, on which resource, in which deployment?
- Does a known benign pattern explain it?
- Were any writes made, and is it still happening?

## Who can answer

The workload classes below are a starting set. Use your organisation's own classes and owners when business-context defines them.

| Workload class | Ask |
|---|---|
| Customer workload | Platform, for tenant and workload run attribution. Never the customer |
| Internal platform service | The owning engineering team, or platform if ownership is unknown |
| Build and deployment | The release owner |
| Engineering access | The person the logs link to the session |
| Security tooling | Security |
| Data and storage | The data owner |
| Unknown | Platform, to establish what the resource is and who owns it |

Ask one precise question per gap, naming the resource, the action, and the UTC window. Shape: "Did you <action> on <resource> in <cluster or account> at <UTC time>, and what were you doing?"

A question to a person is a request for their account of events, not an accusation. Word it that way.
