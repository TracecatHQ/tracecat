# Credential compromise

Use for an exposed long-term access key, unexpected IAM-user activity, or a
finding that identifies a compromised credential.

## Investigation decisions

1. Identify the key's owning principal and exposure window from finding metadata
   and authorized IAM inventory. Record key ID and state, not secret material.
   Establish whether the key belongs to a legitimate workload before disabling
   it; the impact depends on its consumers.
2. Correlate management events for the key and principal across the scoped
   regions. Look for new credentials, role assumptions, policy/trust changes,
   logging changes, and resource creation. Compare the activity with known
   workload behavior instead of declaring compromise from unfamiliar geography.
3. Follow any issued temporary credentials into the STS scenario. Source-key
   inactivity and downstream-session containment are separate questions.
4. Investigate access to sensitive resources using the relevant data events or
   service logs. Record denied attempts separately from successful access, and
   successful reads separately from evidence of extraction or disclosure.

CloudTrail event history covers the most recent 90 days of management events in
the queried region. It is not a complete data-access history. Older activity or
data events require an available trail, event data store, or equivalent logs.
Use the source's actual retention and configuration to bound the conclusion.

## Containment and recovery

Recommend disabling the identified compromised key when the evidence warrants
it, with a workload-impact assessment and an approved execution path. Preserve
the identity and event records needed for investigation before proposing deletion.
Replacing a key without removing the exposure path permits recurrence.

Do not report existing temporary sessions as invalidated merely because the
long-term key was disabled. Identify issued sessions and their applicable
permissions; propose session containment separately. Verify denied access under
the intended containment policy and check for continued suspicious activity
through other credentials or principals.

Inspect credentials and permission changes created during the incident. Remove
confirmed persistence through approved changes, repair the exposure mechanism,
and validate legitimate workload operation with the replacement identity.

## Evidence to hand off

Record the key-to-principal link, exposure estimate, suspicious and legitimate
activity comparison, downstream sessions, access evidence, containment results,
and any unqueried regions or unavailable data sources. A last-used timestamp is
an investigation lead, not a complete session or activity inventory.

## Authoritative references

- [CloudTrail event-history coverage](https://docs.aws.amazon.com/awscloudtrail/latest/userguide/view-cloudtrail-events.html).
- [Controlling access for existing temporary credentials](https://docs.aws.amazon.com/IAM/latest/UserGuide/id_credentials_temp_control-access_disable-perms.html).
