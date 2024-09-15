"""Universal APIs for AWS services using boto3.

Provides a interface to Boto3's Client and Paginator APIs.
Supports role-based authentication and session management.
"""

import os
from typing import Annotated, Any

import aioboto3

from tracecat.logging import logger
from tracecat.registry import Field, RegistrySecret, registry

# TODO: Support possible sets of secrets
# e.g. AWS_PROFILE_NAME or AWS_ROLE_ARN
aws_secret = RegistrySecret(
    name="aws_guardduty",
    keys=["AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_REGION"],
)
"""AWS GuardDuty secret.

Secret
------
- name: `aws`
- keys:
    - `AWS_ACCESS_KEY_ID`
    - `AWS_SECRET_ACCESS_KEY`
    - `AWS_REGION`

Example Usage
-------------
Environment variables:
>>> os.environ["AWS_ACCESS_KEY_ID"]

Expression:
>>> ${{ SECRETS.aws_guardduty.AWS_ACCESS_KEY_ID }}
"""


async def get_temporary_credentials(
    role_arn: str,
    role_session_name: str | None = None,
):
    async with aioboto3.Session().client("sts") as sts_client:
        # Assume the cross-account role
        response = await sts_client.assume_role(
            RoleArn=role_arn, RoleSessionName=role_session_name
        )
        creds = response["Credentials"]
    return creds


async def get_session():
    if os.getenv("AWS_ROLE_ARN"):
        role_arn = os.getenv("AWS_ROLE_ARN")
        role_session_name = os.getenv("AWS_ROLE_SESSION_NAME")
        creds = await get_temporary_credentials(role_arn, role_session_name)
        session = aioboto3.Session(
            aws_access_key_id=creds["AccessKeyId"],
            aws_secret_access_key=creds["SecretAccessKey"],
            aws_session_token=creds["SessionToken"],
            region_name=os.environ["AWS_REGION"],
        )
    elif os.getenv("AWS_PROFILE_NAME"):
        profile_name = os.getenv("AWS_PROFILE_NAME")
        session = aioboto3.Session(profile_name=profile_name)
    else:
        logger.warning(
            "Role ARN and profile not found. Defaulting to IAM credentials (not recommended)."
        )
        session = aioboto3.Session(
            aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
            aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
            region_name=os.getenv("AWS_REGION"),
        )

    return session


@registry.register(
    default_title="Call Boto3 Client",
    description="Call a Boto3 Client method with parameters.",
    display_group="AWS",
    namespace="integrations.aws",
    secrets=[aws_secret],
)
async def call_boto3_client(
    service: Annotated[
        str,
        Field(
            ...,
            description="AWS service name to create Boto3 Client, e.g. 's3', 'ec2', 'guardduty'.",
        ),
    ],
    method: Annotated[
        str,
        Field(
            ...,
            description="Client method name in Boto3, e.g. 'list_buckets', 'list_instances'",
        ),
    ],
    params: Annotated[
        dict[str, Any],
        Field(..., description="Parameters for the client method."),
    ] = None,
) -> dict[str, Any]:
    params = params or {}
    session = await get_session()
    async with session.client(service) as client:
        response = await getattr(client, method)(**params)
        return response


@registry.register(
    default_title="Call Boto3 Paginator",
    description="Call a Boto3 Paginator method with parameters.",
    display_group="AWS",
    namespace="integrations.aws",
    secrets=[aws_secret],
)
async def call_boto3_paginator(
    service: Annotated[
        str,
        Field(
            ...,
            description="AWS service name to create Boto3 Paginator, e.g. 's3', 'ec2', 'guardduty'.",
        ),
    ],
    paginator: Annotated[
        str,
        Field(
            ...,
            description="Paginator method name in Boto3, e.g. 'list_objects_v2', 'describe_instances'.",
        ),
    ],
    params: Annotated[
        dict[str, Any],
        Field(..., description="Parameters for the paginator."),
    ] = None,
) -> list[dict[str, Any]]:
    params = params or {}
    session = await get_session()
    async with session.client(service) as client:
        paginator = client.get_paginator(paginator)
        pages = paginator.paginate(**params)

    results = []
    async for page in pages:
        results.extend(page)

    return results
