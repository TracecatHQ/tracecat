"""Universal APIs for AWS services using boto3.

Provides a interface to Boto3's Client and Paginator APIs.
Supports role-based authentication and session management.
"""

from typing import Annotated, Any
from typing_extensions import Doc

import boto3
import aioboto3
from types_aiobotocore_sts.type_defs import (
    CredentialsTypeDef as AsyncCredentialsTypeDef,
)
from types_boto3_sts.type_defs import CredentialsTypeDef

from tracecat_registry import RegistrySecret, logger, registry, secrets

aws_secret = RegistrySecret(
    name="aws",
    optional_keys=[
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_REGION",
        "AWS_PROFILE",
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
        - `AWS_PROFILE`
    Or:
        - `AWS_ROLE_ARN`
        - `AWS_ROLE_SESSION_NAME`
"""


def has_usable_aws_credentials(
    aws_access_key_id: str | None,
    aws_secret_access_key: str | None,
    aws_region: str | None,
    aws_profile: str | None,
    aws_role_arn: str | None,
    aws_role_session_name: str | None,
) -> bool:
    if not any(
        [
            aws_access_key_id,
            aws_secret_access_key,
            aws_region,
            aws_profile,
            aws_role_arn,
            aws_role_session_name,
        ]
    ):
        return False

    if aws_access_key_id and aws_secret_access_key and aws_region:
        return True

    if aws_profile:
        return True

    if aws_role_arn and aws_role_session_name:
        return True

    return False


async def get_temporary_credentials(
    role_arn: str,
    role_session_name: str | None = None,
) -> AsyncCredentialsTypeDef:
    async with aioboto3.Session().client("sts") as sts_client:
        # Assume the cross-account role
        kwargs = {}
        if role_session_name:
            kwargs["RoleSessionName"] = role_session_name
        response = await sts_client.assume_role(RoleArn=role_arn, **kwargs)
        creds = response["Credentials"]
    return creds


def get_sync_temporary_credentials(
    role_arn: str,
    role_session_name: str | None = None,
) -> CredentialsTypeDef:
    sts_client = boto3.Session().client("sts")
    kwargs = {}
    if role_session_name:
        kwargs["RoleSessionName"] = role_session_name
    response = sts_client.assume_role(RoleArn=role_arn, **kwargs)
    creds = response["Credentials"]
    return creds


async def get_session() -> aioboto3.Session:
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
    elif secrets.get("AWS_PROFILE"):
        profile_name = secrets.get("AWS_PROFILE")
        session = aioboto3.Session(profile_name=profile_name)
    elif (
        secrets.get("AWS_ACCESS_KEY_ID")
        and secrets.get("AWS_SECRET_ACCESS_KEY")
        and secrets.get("AWS_SESSION_TOKEN")
    ):
        session = aioboto3.Session(
            aws_access_key_id=secrets.get("AWS_ACCESS_KEY_ID"),
            aws_secret_access_key=secrets.get("AWS_SECRET_ACCESS_KEY"),
            aws_session_token=secrets.get("AWS_SESSION_TOKEN"),
            region_name=secrets.get("AWS_REGION"),
        )
    elif secrets.get("AWS_ACCESS_KEY_ID") and secrets.get("AWS_SECRET_ACCESS_KEY"):
        logger.warning(
            "Role ARN, profile, and session token not found. Defaulting to IAM credentials (not recommended)."
        )
        session = aioboto3.Session(
            aws_access_key_id=secrets.get("AWS_ACCESS_KEY_ID"),
            aws_secret_access_key=secrets.get("AWS_SECRET_ACCESS_KEY"),
            region_name=secrets.get("AWS_REGION"),
        )
    else:
        # NOTE: This is critical. We must not allow Boto3's default behavior of
        # using the AWS credentials from the environment.
        raise ValueError("No AWS credentials found.")

    return session


def get_sync_session() -> boto3.Session:
    if secrets.get("AWS_ROLE_ARN"):
        role_arn = secrets.get("AWS_ROLE_ARN")
        role_session_name = secrets.get("AWS_ROLE_SESSION_NAME")
        creds = get_sync_temporary_credentials(role_arn, role_session_name)
        session = boto3.Session(
            aws_access_key_id=creds["AccessKeyId"],
            aws_secret_access_key=creds["SecretAccessKey"],
            aws_session_token=creds["SessionToken"],
            region_name=secrets.get("AWS_REGION"),
        )
    elif secrets.get("AWS_PROFILE"):
        profile_name = secrets.get("AWS_PROFILE")
        session = boto3.Session(profile_name=profile_name)
    elif (
        secrets.get("AWS_ACCESS_KEY_ID")
        and secrets.get("AWS_SECRET_ACCESS_KEY")
        and secrets.get("AWS_SESSION_TOKEN")
    ):
        session = boto3.Session(
            aws_access_key_id=secrets.get("AWS_ACCESS_KEY_ID"),
            aws_secret_access_key=secrets.get("AWS_SECRET_ACCESS_KEY"),
            aws_session_token=secrets.get("AWS_SESSION_TOKEN"),
            region_name=secrets.get("AWS_REGION"),
        )
    elif secrets.get("AWS_ACCESS_KEY_ID") and secrets.get("AWS_SECRET_ACCESS_KEY"):
        logger.warning(
            "Role ARN, profile, and session token not found. Defaulting to IAM credentials (not recommended)."
        )
        session = boto3.Session(
            aws_access_key_id=secrets.get("AWS_ACCESS_KEY_ID"),
            aws_secret_access_key=secrets.get("AWS_SECRET_ACCESS_KEY"),
            region_name=secrets.get("AWS_REGION"),
        )
    else:
        # NOTE: This is critical. We must not allow Boto3's default behavior of
        # using the AWS credentials from the environment.
        raise ValueError("No AWS credentials found.")

    return session


@registry.register(
    default_title="Call method",
    description="Instantiate a Boto3 client and call an AWS Boto3 API method.",
    display_group="AWS Boto3",
    doc_url="https://boto3.amazonaws.com/v1/documentation/api/latest/guide/clients.html",
    namespace="tools.aws_boto3",
    secrets=[aws_secret],
)
async def call_api(
    service_name: Annotated[
        str,
        Doc("AWS service name e.g. 's3', 'ec2', 'guardduty'."),
    ],
    method_name: Annotated[
        str,
        Doc("Method name e.g. 'list_buckets', 'list_instances'"),
    ],
    endpoint_url: Annotated[
        str | None,
        Doc("Endpoint URL for the AWS service."),
    ] = None,
    params: Annotated[
        dict[str, Any] | None,
        Doc("Parameters for the API method."),
    ] = None,
) -> dict[str, Any]:
    params = params or {}
    session = await get_session()
    async with session.client(service_name, endpoint_url=endpoint_url) as client:  # type: ignore
        response = await getattr(client, method_name)(**params)
        return response


@registry.register(
    default_title="Call paginator",
    description="Instantiate a Boto3 paginator and call a paginated AWS API method.",
    display_group="AWS Boto3",
    doc_url="https://boto3.amazonaws.com/v1/documentation/api/latest/guide/paginators.html",
    namespace="tools.aws_boto3",
    secrets=[aws_secret],
)
async def call_paginated_api(
    service_name: Annotated[
        str,
        Doc("AWS service name e.g. 's3', 'ec2', 'guardduty'."),
    ],
    paginator_name: Annotated[
        str,
        Doc("Paginator name e.g. 'list_objects_v2', 'describe_instances'."),
    ],
    endpoint_url: Annotated[
        str | None,
        Doc("Endpoint URL for the AWS service."),
    ] = None,
    params: Annotated[
        dict[str, Any] | None,
        Doc("Parameters for the API paginator."),
    ] = None,
) -> list[dict[str, Any]]:
    params = params or {}
    session = await get_session()
    async with session.client(service_name, endpoint_url=endpoint_url) as client:  # type: ignore
        paginator = client.get_paginator(paginator_name)
        pages = paginator.paginate(**params)

    results = []
    async for page in pages:
        results.append(page)

    return results
