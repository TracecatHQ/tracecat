"""Universal APIs for AWS services using boto3.

Provides a interface to Boto3's Client and Paginator APIs.
Supports role-based authentication and session management.
"""

from typing import Annotated, Any

import aioboto3
from pydantic import Field

from tracecat_registry import RegistrySecret, logger, registry, secrets

aws_secret = RegistrySecret(
    name="aws",
    optional_keys=[
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_REGION",
        "AWS_PROFILE_NAME",
        "AWS_ROLE_ARN",
        "AWS_ROLE_SESSION_NAME",
    ],
    optional=False,
)
"""AWS credentials.

- name: `aws`
- optional_keys:
    Either:
        - `AWS_ACCESS_KEY_ID`
        - `AWS_SECRET_ACCESS_KEY`
        - `AWS_REGION`
    Or:
        - `AWS_PROFILE_NAME`
    Or:
        - `AWS_ROLE_ARN`
        - `AWS_ROLE_SESSION_NAME`
"""


async def get_temporary_credentials(
    role_arn: str,
    role_session_name: str | None = None,
) -> dict[str, Any]:
    async with aioboto3.Session().client("sts") as sts_client:
        # Assume the cross-account role
        response = await sts_client.assume_role(
            RoleArn=role_arn, RoleSessionName=role_session_name
        )
        creds = response["Credentials"]
    return creds


async def get_session():
    if secrets.get("AWS_ROLE_ARN"):
        role_arn = secrets.get("AWS_ROLE_ARN")
        role_session_name = secrets.get("AWS_ROLE_SESSION_NAME")
        creds = await get_temporary_credentials(role_arn, role_session_name)
        session = aioboto3.Session(
            aws_access_key_id=creds["AccessKeyId"],
            aws_secret_access_key=creds["SecretAccessKey"],
            aws_session_token=creds["SessionToken"],
            region_name=secrets.get("AWS_REGION"),
        )
    elif secrets.get("AWS_PROFILE_NAME"):
        profile_name = secrets.get("AWS_PROFILE_NAME")
        session = aioboto3.Session(profile_name=profile_name)
    elif secrets.get("AWS_ACCESS_KEY_ID") and secrets.get("AWS_SECRET_ACCESS_KEY"):
        logger.warning(
            "Role ARN and profile not found. Defaulting to IAM credentials (not recommended)."
        )
        session = aioboto3.Session(
            aws_access_key_id=secrets.get("AWS_ACCESS_KEY_ID"),
            aws_secret_access_key=secrets.get("AWS_SECRET_ACCESS_KEY"),
            region_name=secrets.get("AWS_REGION"),
        )
    else:
        raise ValueError("No AWS credentials found.")

    return session


@registry.register(
    default_title="Call AWS Boto3 API",
    description="Instantiate a Boto3 client and call an AWS Boto3 API method.",
    display_group="AWS Boto3",
    doc_url="https://boto3.amazonaws.com/v1/documentation/api/latest/guide/clients.html",
    namespace="tools.aws_boto3",
    secrets=[aws_secret],
)
async def call_api(
    service_name: Annotated[
        str,
        Field(
            ...,
            description="AWS service name e.g. 's3', 'ec2', 'guardduty'.",
        ),
    ],
    method_name: Annotated[
        str,
        Field(
            ...,
            description="Method name e.g. 'list_buckets', 'list_instances'",
        ),
    ],
    params: Annotated[
        dict[str, Any] | None,
        Field(..., description="Parameters for the API method."),
    ] = None,
) -> dict[str, Any]:
    params = params or {}
    session = await get_session()
    async with session.client(service_name) as client:  # type: ignore
        response = await getattr(client, method_name)(**params)
        return response


@registry.register(
    default_title="Call paginated AWS Boto3 API",
    description="Instantiate a Boto3 paginator and call a paginated AWS API method.",
    display_group="AWS Boto3",
    doc_url="https://boto3.amazonaws.com/v1/documentation/api/latest/guide/paginators.html",
    namespace="tools.aws_boto3",
    secrets=[aws_secret],
)
async def call_paginated_api(
    service_name: Annotated[
        str,
        Field(
            ...,
            description="AWS service name e.g. 's3', 'ec2', 'guardduty'.",
        ),
    ],
    paginator_name: Annotated[
        str,
        Field(
            ...,
            description="Paginator name e.g. 'list_objects_v2', 'describe_instances'.",
        ),
    ],
    params: Annotated[
        dict[str, Any] | None,
        Field(..., description="Parameters for the API paginator."),
    ] = None,
) -> list[dict[str, Any]]:
    params = params or {}
    session = await get_session()
    async with session.client(service_name) as client:  # type: ignore
        paginator = client.get_paginator(paginator_name)
        pages = paginator.paginate(**params)

    results = []
    async for page in pages:
        results.extend(page)

    return results
