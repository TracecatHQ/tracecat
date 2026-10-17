# Sentry activity paging reconciliation

`reconcile_sentry_activity_paging.py` prepares a narrowly scoped update to one
modern Sentry workflow alert. It adds this action-filter condition to the one
active `sentry_app` action whose display target is `incident.io`:

```json
{
  "type": "tagged_event",
  "comparison": {
    "key": "tracecat.capture.boundary",
    "match": "ne",
    "value": "activity"
  },
  "conditionResult": true
}
```

The condition is evaluated in an ALL group. An activity capture has the
`tracecat.capture.boundary=activity` tag and therefore does not satisfy the
condition. A terminal capture with `tracecat.capture.boundary=workflow` does
satisfy it, as does a terminal capture that has no boundary tag. This follows
the current Sentry `tagged_event` handler, which evaluates `GroupEvent` tags,
and its `NOT_EQUAL` implementation, where a missing value passes:

- [tagged event handler at the reviewed Sentry commit](https://github.com/getsentry/sentry/blob/e917c50c2413cd5082524c3f98791eb035fd38de/src/sentry/workflow_engine/handlers/condition/tagged_event_handler.py)
- [NOT_EQUAL matching at the reviewed Sentry commit](https://github.com/getsentry/sentry/blob/e917c50c2413cd5082524c3f98791eb035fd38de/src/sentry/rules/match.py)

The script uses only the current
[`/api/0/organizations/{organization}/workflows/{workflow_id}/` endpoint](https://docs.sentry.io/api/monitors/update-an-alert-by-id/).
It refuses legacy alert-rule responses and unknown workflow shapes.

The observed configuration owner is Sentry itself: no paging-rule definition
was found in the application or infrastructure repository. Configure each
regional alert explicitly; this tool does not discover, create, or enable
alerts. It changes paging eligibility only and leaves application source
captures available for diagnostics.

## Review and dry run

Set `SENTRY_AUTH_TOKEN` in the process environment. Do not put the token in a
command argument or plan file. The operator must provide the exact workflow
name and expected environment; an organization ID and detector IDs can add
further identity checks.

```bash
uv run python scripts/monitoring/reconcile_sentry_activity_paging.py \
  --base-url https://sentry-region.example \
  --organization example-org \
  --workflow-id 123456 \
  --expected-name "Tracecat workflow alert" \
  --expected-environment production \
  --expected-organization-id 654321 \
  --expected-detector-id 987654 \
  --plan-file /tmp/activity-paging-plan.json
```

Dry run is the default and performs one GET. The JSON output is a sanitized
structural plan containing the complete snapshot SHA-256 digest, changed
filter index, and exact condition to append. It omits workflow action config,
target identifiers, response bodies, and credentials. Review that output and
record `snapshot_sha256` before considering a write.

## Guarded write

After reviewing the dry-run plan, pass its digest explicitly with `--apply`:

```bash
uv run python scripts/monitoring/reconcile_sentry_activity_paging.py \
  --base-url https://sentry-region.example \
  --organization example-org \
  --workflow-id 123456 \
  --expected-name "Tracecat workflow alert" \
  --expected-environment production \
  --expected-organization-id 654321 \
  --expected-detector-id 987654 \
  --expected-digest "REPLACE_WITH_REVIEWED_SHA256" \
  --apply
```

The script refetches the workflow immediately before the PUT and refuses the
write if the complete response digest changed. The PUT intentionally contains
only `name`, `enabled`, and the complete `actionFilters` list. Including every
filter lets Sentry's update validator preserve unrelated routes while omitting
configuration, triggers, environment, owner, and detector fields from the
mutation. The script then fetches the workflow again and verifies the protected
metadata, action settings, filter order, unrelated filters, and the server
assigned ID for the new condition.

The selected route must be one active `sentry_app` action with
`config.targetType="sentry_app"`, a numeric-string `targetIdentifier`, and
`config.targetDisplay="incident.io"`, in an ALL action-filter group containing
no other action. Because the API serializers do not promise list ordering, a
post-write reordering is treated as a verification failure and is never
silently normalized.

Sentry's endpoint does not provide a server compare-and-swap token in this
flow. Coordinate with other operators to avoid concurrent edits between the
last GET and the PUT; the digest check cannot close that race. The complete
response digest includes `lastTriggered`, so new alert activity can require a
fresh dry run even when the configuration is unchanged.

A successful status followed by a lost response is treated as
an ambiguous mutation; the script never retries it. Inspect the workflow and
reconcile manually before any subsequent command. A second run after a
successful update recognizes the exact condition and returns `no-op` without a
PUT, even if the old reviewed digest predates that update.

The script refuses a disabled workflow or target action, an environment or
identity mismatch, multiple target groups/actions, a target group that is not
ALL, mixed actions in that group, conflicting or duplicate boundary filters,
legacy fields, unknown fields, non-HTTPS URLs, URL credentials, query strings,
and redirects. Existing unrelated ALL filters are copied into the request and
verified unchanged. Unexpected non-target groups are refused when their shape
cannot be established safely.

Configuration verification does not establish incident creation, escalation,
or human page delivery. Those require a separately authorized end-to-end test.
The implementation was validated with synthetic HTTP responses and public
Sentry source; live CLI dry-run, apply, and paging tests were not performed.

The API serializer and validator sources that define the wire shape and partial
PUT behavior are pinned here:

- [workflow serializer](https://github.com/getsentry/sentry/blob/e917c50c2413cd5082524c3f98791eb035fd38de/src/sentry/workflow_engine/endpoints/serializers/workflow_serializer.py)
- [workflow details endpoint](https://github.com/getsentry/sentry/blob/e917c50c2413cd5082524c3f98791eb035fd38de/src/sentry/workflow_engine/endpoints/organization_workflow_details.py)
- [workflow validator](https://github.com/getsentry/sentry/blob/e917c50c2413cd5082524c3f98791eb035fd38de/src/sentry/workflow_engine/endpoints/validators/base/workflow.py)
- [condition-group validator](https://github.com/getsentry/sentry/blob/e917c50c2413cd5082524c3f98791eb035fd38de/src/sentry/workflow_engine/endpoints/validators/base/data_condition_group.py)
