"""S3 integration to download files and return contents as a string."""

from typing import Annotated

from pydantic import Field

from tracecat_registry import RegistrySecret, registry
from tracecat_registry.integrations.boto3 import get_session

s3_secret = RegistrySecret(
    name="s3",
    optional_keys=[
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_REGION",
        "AWS_PROFILE_NAME",
        "AWS_ROLE_ARN",
        "AWS_ROLE_SESSION_NAME",
    ],
)
"""AWS secret.

Secret
------
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


@registry.register(
    default_title="Download S3 Object",
    description="Download an object from S3 and return its body as a string.",
    display_group="S3",
    namespace="integrations.s3",
    secrets=[s3_secret],
)
async def download_s3_object(
    bucket: Annotated[str, Field(..., description="S3 bucket name.")],
    key: Annotated[str, Field(..., description="S3 object key.")],
) -> str:
    session = await get_session()
    async with session.client("s3") as s3_client:
        obj = await s3_client.get_object(Bucket=bucket, Key=key)
        body = await obj["Body"].read()
        # Defensively handle different types of bodies
        if isinstance(body, bytes):
            return body.decode("utf-8")
        return body
