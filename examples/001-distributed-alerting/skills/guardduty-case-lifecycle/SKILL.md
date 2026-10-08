---
name: guardduty-case-lifecycle
description: Load when the prompt hands you a GuardDuty finding to triage, either a scheduled finding with its region and detector or a manual run carrying finding_id or since. Takes the finding from AWS to a triaged Tracecat case and a published Slack alert. You fetch it with boto3, map its family, dedupe it, attach it to a case or create one, and stop when a person has already decided. Otherwise you investigate, write the case, post or refresh the Work Object card, the brief, the evidence table and the owner ask, and keep the slack_work_objects row current. Load with business-context (when that skill exists), hypothesis-driven-triage, aws-cloud-incident-response-core and case-output.
metadata:
  tools:
    - tools.aws_boto3.call_api
    - core.cases.search_cases
    - core.cases.get_case
    - core.cases.create_case
    - core.cases.update_case
    - core.cases.add_case_tag
    - core.cases.create_comment
    - core.table.lookup
    - core.table.insert_row
    - tools.slack_sdk.call_method
    - tools.slack.post_message
    - tools.slack.update_message
    - tools.slack.list_replies
    - tools.slack.lookup_user_by_email
---

# GuardDuty case lifecycle

You own a GuardDuty finding from the moment it reaches you until the team can act on it in Slack. The only deterministic step before you is the scheduled pull, which lists recently updated finding ids in every enabled region and starts one run of you per finding. Every decision after that is yours, and so is every write: the case, the tags, the Slack card, the brief, the evidence table, the owner ask and the table row that answers the card's details panel.

Work through the stages below in order. Each stage says when to stop. When you stop, return one plain line for the run record saying what you did, for example `CASE-0001 created, Open, card, brief and owner ask posted.` or `Finding <id> unchanged on CASE-0001; nothing to do.`

## Fixed values

| Name | Value |
|---|---|
| Workspace case URL | `https://<your-tracecat-host>/workspaces/<workspace-id>/cases/<case_id>` |
| Triage channel | `#security-alerts`, `C0123456789` |
| Work Object table | `slack_work_objects` |
| Enabled regions | discover with `ec2` `describe_regions` in `us-east-1` when you need them |
| GuardDuty console link | `https://<region>.console.aws.amazon.com/guardduty/home?region=<region>#/findings?fId=<finding_id>` |
| Time format in anything a reader sees | `17 Sep 2026, 03:47 UTC` |

AWS calls use `tools.aws_boto3.call_api` with `service_name`, `method_name`, `params` and `region_name`. The configured credential already lands in your organisation's security audit account through `<your-read-only-audit-role>`, so leave `role_arn` empty. Call only read methods (`get_*`, `list_*`, `describe_*`).

## 1. Fetch the finding

- **Scheduled run.** The prompt gives `finding_id`, `region` and `detector_id`. Call `guardduty` `get_findings` in that region with `{"DetectorId": <detector_id>, "FindingIds": [<finding_id>]}`.
- **Manual run with a finding ARN.** The ARN reads `arn:aws:guardduty:<region>:<account>:detector/<detector_id>/finding/<finding_id>`. Take all three values from it and call `get_findings` as above.
- **Manual run with a bare finding id.** List the enabled regions. In each region, call `list_detectors` then `get_findings` until one returns the finding.
- **Manual run with `since`.** In each enabled region, call `list_findings` with `FindingCriteria` `updatedAt` `GreaterThanOrEqual` set to `since` in epoch milliseconds, `SortCriteria` `{"AttributeName": "updatedAt", "OrderBy": "DESC"}` and `MaxResults` 1. Take the newest across regions and fetch it.
- **Not found anywhere.** Return `Finding <id> not found in any enabled region.` and stop.

Read these from the finding and keep them for every later stage:

| Value | Where it comes from |
|---|---|
| id, account, region, type, severity | `id`, `accountId`, `region`, `type`, `severity` |
| count | `service.count` |
| first seen, last seen | `service.eventFirstSeen`, `service.eventLastSeen` |
| principal | `resource.accessKeyDetails.principalId`, else `resource.kubernetesDetails.kubernetesUserDetails.username` |
| resource | the first present of `resource.instanceDetails.instanceId`, `resource.kubernetesDetails.kubernetesWorkloadDetails.name`, `resource.eksClusterDetails.name`, `resource.s3BucketDetails[0].name`, `resource.accessKeyDetails.userName`, `resource.accessKeyDetails.accessKeyId` |
| sample | `service.additionalInfo.value` parsed as JSON, key `sample`. Only `true` marks a GuardDuty sample |
| indicator | `service.action.dnsRequestAction.domain`, else the `remoteIpDetails.ipAddressV4` under `networkConnectionAction`, `awsApiCallAction` or `kubernetesApiCallAction` |

## 2. Map the family

Map the finding type to one family with the ordered rules in `families.md`. The first matching rule wins. The family feeds the group tag and the hypothesis library.

## 3. Find the case

1. **Same finding.** Call `core.cases.search_cases` with tags `["finding-<id>"]`, status `["new", "in_progress", "on_hold"]`, `start_time` 90 days ago, `order_by` `created_at`, `sort` `desc`, `limit` 1. It is a hit when the returned case carries that tag.
2. **Nothing new (scheduled runs only).** On a hit, read the case's `last_seen` field. When it equals the finding's last seen time as an instant, the finding has not recurred. Return `Finding <id> unchanged on <short_id>; nothing to do.` and stop. A manual run always continues, because a person asked for a re-triage.
3. **Same group.** With no finding hit, search tags `["group-<family>-<account>"]` with the same statuses, `start_time` 24 hours ago, newest first, `limit` 1. It is a hit when the case carries that tag and carries neither `owner-confirmed` nor `owner-denied`.
4. **Attach** on either hit. Call `core.cases.update_case` on that case. Set these fields:
   - `finding_ids`: the existing list, with this id appended if absent.
   - `first_seen`: keep the existing value; set it only when empty.
   - `finding_count`, `finding_type`, `last_seen`, `principal`, `region`, `resource`: from this finding.
   - `payload.finding_summary`: `{account, count, finding_ids, first_seen, last_seen, region, resource_id, severity, type}`.
5. **Create** when neither search hit. Call `core.cases.create_case` with:
   - summary `Triage in progress: <type>`
   - description `Triage in progress.`
   - status `new`, severity `low`
   - `create_missing_tags` true
   - tags `guardduty`, `family-<family>`, `account-<account>`, `group-<family>-<account>`, `finding-<id>`
   - fields `aws_account`, `finding_count`, `finding_ids` (`[<id>]`), `finding_type`, `first_seen`, `last_seen`, `principal`, `region`, `resource`
   - the same `payload.finding_summary`

Keep the case id, its short id (`CASE-0001`), its URL, and whether you created it or attached to it.

## 4. Stop if a person has already decided

An attached case counts as decided when either is true:

- it carries `owner-confirmed` or `owner-denied`;
- its `disposition` field is `Inconclusive`.

On a decided case, add one comment with `core.cases.create_comment` and stop:

`Finding <id> (<type>) recurred with <count> events, last seen <last seen, reader format>, and was attached. A person has already decided this case (<the decided tags and disposition, comma separated>), so the description, disposition and severity were left unchanged and no new triage ran.`

## 5. Investigate

Load business-context if you have that skill, then hypothesis-driven-triage and aws-cloud-incident-response-core, and follow the method there. Build the investigation record that aws-cloud-incident-response-core defines. Gather these facts yourself:

- **Every finding on the case, in full.** Call `get_findings` for each id in `finding_ids`, in the case's region first. Look for any id it does not return in the other enabled regions, or in the SIEM's GuardDuty records. Triage the case, not only this finding.
- **Report times.**
  - Report time is now.
  - The due time is now plus 3 days; the next update is now plus 1 day.
  - The activity is still happening when the last event is within 24 hours of now.
- **The finding's own indicator.** Look it up with the threat enrichment tools you have (domain first, else the address), and with an IP reputation tool for an address.
  - A first-seen date within a few minutes of now means the enrichment service had never seen the indicator before this lookup. Zero detections then means no prior record, not a clean verdict.
  - A finding with no domain and no address has no external indicator. Say so rather than leaving the field blank.
- **Shared workload role sessions** for any finding on shared clusters. Some organisations run many tenants or jobs through one shared role that assumes other roles on their behalf. business-context names that role and its session naming pattern when your organisation has one. Query the SIEM for the role's cross-account `AssumeRole` sessions from 2 minutes before first seen to 2 minutes after last seen, grouped by tenant session prefix and role. The query below shows the logic in one SQL dialect; table, column and function names are `<adapt to your SIEM>`:

  ```sql
  SELECT substring(JSONExtractString(rawLog, 'requestParameters', 'roleSessionName'), 1, <tenant prefix length>) AS tenant_session,
         JSONExtractString(rawLog, 'requestParameters', 'roleArn') AS assumed_role,
         count() AS events, min(receivedAt) AS first_event, max(receivedAt) AS last_event,
         arrayStringConcat(arrayMap(x -> toString(x), arraySort(groupUniqArray(20)(toMinute(receivedAt)))), ',') AS minutes_of_hour
  FROM aws_cloudtrail_logs
  WHERE receivedAt >= parseDateTimeBestEffort('<first seen>') - INTERVAL 2 MINUTE
    AND receivedAt <= parseDateTimeBestEffort('<last seen>') + INTERVAL 2 MINUTE
    AND eventName = 'AssumeRole'
    AND JSONExtractString(rawLog, 'userIdentity', 'arn') LIKE '%<your-shared-workload-role>%'
  GROUP BY tenant_session, assumed_role ORDER BY events DESC LIMIT 10
  ```

  An empty result means no tenant workload assumed a role in the window. Say that. When business-context names no shared workload role, skip this check.

## 6. Decide the values the case carries

Apply these rules exactly. They come out the same way every time for the same investigation.

| Value | Rule |
|---|---|
| Status | Your disposition status, except that Benign with a linked email reads **Open** until the linked person answers |
| Severity | Escalate → `critical`; Benign with no linked email → `low`; everything else → `medium` |
| Reason | Your disposition reason with any trailing space or full stop removed |
| Due date field | now plus 3 days, ISO 8601 (the only place ISO is used) |

## 7. Write the case

Load case-output. Write the report and assemble the description exactly as case-output's "Case description" section lays it out, with the status line, the nine headings, the closure sentences and the Appendix Report line. Run case-output's self-check and fix anything it catches before you write.

Then call `core.cases.update_case` on the case with:

- `summary`: your case title.
- `description`: the assembled description.
- `severity`: from stage 6.
- `fields`:
  - `disposition`: the status from stage 6.
  - `due_date`: from stage 6.
  - `linked_person`: the linked person, or empty.
  - `principal`: the actor principal, else the finding's principal, else empty.

Add each tag with `core.cases.add_case_tag` and `create_if_missing` true: `guardduty`, `family-<family>`, `account-<account>`, `group-<family>-<account>` and `finding-<id>`. Add `escalation` too when the status is Escalate. Leave the case's other tags as they are, and never remove `escalation`.

When you attached rather than created, add one comment:

`Finding <id> (<type>, <first seen, reader format>) attached and the case re-triaged. Status: <disposition status> (<confidence> confidence). <reason> Case findings: <number of finding ids>.`

## 8. Publish to Slack

Follow `slack-delivery.md` in this skill. It covers the card, the brief, the evidence table, the owner ask and the `slack_work_objects` row, both for a new case and for re-triage of one already posted.

## Boundaries

- Read-only toward AWS, always.
- Never close a case, never set it to resolved, and never remove `escalation`.
- A case you create or update carries only what your evidence supports. Containment is a decision for a person and never an action of yours.
