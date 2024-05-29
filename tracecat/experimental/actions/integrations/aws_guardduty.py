"""Native integration to query AWS GuardDuty findings.

Optional secrets: `aws-guardduty` secret with keys `AWS_ACCOUNT_ID` and `AWS_ORGANIZATION_ID`.

Note: this integration DOES NOT support IAM credential based authentication.
Secrets are only used to obscure potentially sensitive data (account ID, organization ID).
"""

import os
from typing import Any

import dateutil.parser

from tracecat.etl.aws_guardduty import load_guardduty_findings
from tracecat.etl.query_builder import pl_sql_query
from tracecat.experimental.registry import registry


@registry.register(
    namespace="aws_guardduty",
    description="Query AWS GuardDuty findings",
    secrets=["aws-guardduty"],
)
def query_guardduty_findings(
    start: str,
    end: str,
    query: str,
    account_id: str | None = None,
    organization_id: str | None = None,
) -> list[dict[str, Any]]:
    account_id = account_id or os.environ["AWS_ACCOUNT_ID"]
    organization_id = organization_id or os.environ["AWS_ORGANIZATION_ID"]
    start_dt = dateutil.parser.parse(start)
    end_dt = dateutil.parser.parse(end)
    # Hash the function call args
    # to use as a cache key
    # We need to use the session role to compute the cache key
    findings_lf = load_guardduty_findings(
        start=start_dt,
        end=end_dt,
        account_id=account_id,
        organization_id=organization_id,
    )
    queried_findings = pl_sql_query(lf=findings_lf, query=query, eager=True).to_dicts()
    return queried_findings
