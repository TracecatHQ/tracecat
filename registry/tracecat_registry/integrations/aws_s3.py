"""S3 integration to download files and return contents as a string."""

import re
from typing import Annotated

from pydantic import Field

from tracecat_registry import RegistrySecret, registry
from tracecat_registry.integrations.aws_boto3 import get_session

# Add this at the top with other constants
BUCKET_REGEX = re.compile(r"^[a-z0-9][a-z0-9.-]*[a-z0-9]$")

s3_secret = RegistrySecret(
    name="aws_s3",
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

- name: `aws_s3`
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
    default_title="Parse S3 URI",
    description="Parse an S3 URI into bucket name and object key.",
    display_group="AWS S3",
    doc_url="https://boto3.amazonaws.com/v1/documentation/api/latest/guide/s3-example-download-file.html",
    namespace="tools.aws_s3",
    secrets=[s3_secret],
)
async def parse_uri(uri: str) -> tuple[str, str]:
    uri = str(uri).strip()
    if not uri.startswith("s3://"):
        raise ValueError("S3 URI must start with 's3://'")

    uri_path = uri.replace("s3://", "")
    uri_paths = uri_path.split("/")
    bucket = uri_paths.pop(0)
    key = "/".join(uri_paths)

    return bucket, key


@registry.register(
    default_title="Get S3 object",
    description="Download an object from S3 and return its body as a string.",
    display_group="AWS S3",
    doc_url="https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/s3.html#S3.Client.get_object",
    namespace="tools.aws_s3",
    secrets=[s3_secret],
)
async def download_object(
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
        else:
            raise ValueError(f"Unexpected body type. Expected bytes, got {type(body)}")
