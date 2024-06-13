"""AWS GuardDuty integration. GuardDuty is a threat detection service that monitors for
malicious activity and unauthorized behavior across AWS accounts and workloads.

Authentication method:
- Cross-account AWS Role
- IAM credentials (not recommended)

Secret Values:
```json
{
    "aws_access_key_id": "<AWS_ACCESS_KEY_ID>",
    "aws_secret_access_key: "<AWS_SECRET_ACCESS_KEY>",
    "aws_region": "<AWS_REGION>",
}
```

IAM Policy:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "guardduty:ListDetectors",
        "guardduty:ListFindings",
        "guardduty:GetFindings"
      ],
      "Resource": "*"
    }
  ]
}
```


Trust Relationship:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "AWS": "arn:aws:iam::YOUR_SAAS_PROVIDER_ACCOUNT_ID:root"
      },
      "Action": "sts:AssumeRole"
    }
  ]
}
```

Supported APIs:

```python
list_alerts = {
    "endpoint": ["boto3.GuardDuty.list_findings", "boto3.GuardDuty.get_findings"],
    "user_agent": "boto3",
    "ocsf_schema": "array[detection_finding]",
    "reference": "https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/guardduty.html"
}
```
"""

from datetime import datetime
from typing import Annotated, Any

import aioboto3
from loguru import logger
from tqdm.asyncio import tqdm, trange
from types_aiobotocore_guardduty.client import GuardDutyClient

from tracecat.actions.io import retry
from tracecat.registry import Field, registry

GUARDDUTY_MAX_RESULTS = 50


@retry()
async def _list_guardduty_finding_ids(
    client: GuardDutyClient,
    detector_id: str,
    start_time: datetime,
    end_time: datetime,
    limit: int,
) -> list[str]:
    # Convert the datetime objects to Unix Epoch timestamps in millisecond format
    start_ts = int(start_time.timestamp() * 1000)
    end_ts = int(end_time.timestamp() * 1000)
    # List the findings
    all_finding_ids = []
    n_findings = 0

    pages = client.get_paginator("list_findings").paginate(
        DetectorId=detector_id,
        FindingCriteria={
            "Criterion": {
                "updatedAt": {
                    "Gte": start_ts,
                    "Lte": end_ts,
                }
            }
        },
        PaginationConfig={"MaxItems": limit, "PageSize": GUARDDUTY_MAX_RESULTS},
    )

    async for page in pages:
        finding_ids = page["FindingIds"]
        n_findings += len(finding_ids)
        all_finding_ids.extend(finding_ids)
        if n_findings >= limit:
            logger.warning("Findings limit reached: {limit}", limit=limit)
            break

    return all_finding_ids


@retry()
async def _get_findings(
    client: GuardDutyClient, detector_id: str, finding_ids: list[str]
) -> list[dict[str, Any]]:
    findings = await client.get_findings(DetectorId=detector_id, FindingIds=finding_ids)
    return findings


@registry.register(
    description="Fetch AWS GuardDuty alerts.",
    namespace="integrations.aws.guardduty",
    default_title="List AWS GuardDuty Alerts",
    display_group="Cloud D&R",
)
async def list_guardduty_alerts(
    start_time: Annotated[
        datetime, Field(..., description="The start time for the alerts")
    ],
    end_time: Annotated[
        datetime, Field(..., description="The end time for the alerts")
    ],
    limit: Annotated[
        int, Field(default=5000, description="The maximum number of alerts to return")
    ] = 5000,
    role_arn: Annotated[
        str | None, Field(default=None, description="The ARN of the role to assume")
    ] = None,
    role_session_name: Annotated[
        str | None,
        Field(default=None, description="The session name for the assumed role"),
    ] = None,
    profile_name: Annotated[
        str | None, Field(default=None, description="The AWS profile name to use")
    ] = None,
    aws_access_key_id: Annotated[
        str | None, Field(default=None, description="The AWS access key ID")
    ] = None,
    aws_secret_access_key: Annotated[
        str | None, Field(default=None, description="The AWS secret access key")
    ] = None,
    aws_region: Annotated[
        str | None, Field(default=None, description="The AWS region to use")
    ] = None,
) -> list[dict[str, Any]]:
    if role_arn:
        # Assume process is running in an environment with
        # permissions to assume the cross-account guardduty role
        sts_session = aioboto3.Session()
        async with sts_session.client("sts") as sts_client:
            # Assume the cross-account role
            response = await sts_client.assume_role(
                RoleArn=role_arn, RoleSessionName=role_session_name
            )
            credentials = response["Credentials"]

        # Create a GuardDuty client using the temporary credentials
        guardduty_session = aioboto3.Session(
            aws_access_key_id=credentials["AccessKeyId"],
            aws_secret_access_key=credentials["SecretAccessKey"],
            aws_session_token=credentials["SessionToken"],
            region_name=aws_region,
        )

    else:
        logger.warning(
            "Role ARN not found. Defaulting to IAM credentials (not recommended)."
        )
        guardduty_session = aioboto3.Session(
            profile_name=profile_name,
            aws_access_key_id=aws_access_key_id,
            aws_secret_access_key=aws_secret_access_key,
            region_name=aws_region,
        )

    # Get findings from GuardDuty
    async with guardduty_session.client("guardduty") as guardduty_client:
        # List all finding IDs
        all_finding_ids = []
        detectors = await guardduty_client.list_detectors()
        detector_ids = detectors["DetectorIds"]
        if len(detector_ids) == 0:
            raise ValueError("No GuardDuty detectors found.")

        logger.info("Found {n} GuardDuty detectors", n=len(detector_ids))
        with tqdm(
            detector_ids, desc="Fetch GuardDuty finding IDs", unit="detector"
        ) as pbar:
            for detector_id in pbar:
                pbar.set_postfix(detector_id=detector_id)
                finding_ids = await _list_guardduty_finding_ids(
                    guardduty_client,
                    detector_id=detector_id,
                    start_time=start_time,
                    end_time=end_time,
                    limit=limit,
                )
                all_finding_ids.extend(finding_ids)

        logger.info(
            "Found {n} GuardDuty findings between [{start_time}, {end_time}]",
            n=len(all_finding_ids),
            start_time=start_time.isoformat(),
            end_time=end_time.isoformat(),
        )

        # Get finding details at GUARDDUTY_MAX_RESULTS per request
        total_findings = len(all_finding_ids)
        findings = []
        with trange(
            0,
            total_findings,
            GUARDDUTY_MAX_RESULTS,
            desc="Fetch GuardDuty findings",
            unit="page",
        ) as pbar:
            for i in pbar:
                finding_ids_batch = all_finding_ids[i : i + GUARDDUTY_MAX_RESULTS]
                findings_batch = await _get_findings(
                    guardduty_client,
                    detector_id=detector_id,
                    finding_ids=finding_ids_batch,
                )
                findings.extend(findings_batch["Findings"])
                pbar.update()
                pbar.set_postfix(total_findings=len(findings))

    return findings
