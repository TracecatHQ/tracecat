"""S3 integration to download files and return contents as a string."""

import re
from typing import Annotated
import base64
import binascii
import asyncio

from tenacity import retry, stop_after_attempt, wait_exponential
from pydantic import Field
from types_aiobotocore_s3.type_defs import (
    ListObjectsV2OutputTypeDef,
    DeleteObjectOutputTypeDef,
)

from tracecat_registry import RegistrySecret, registry
from tracecat_registry.integrations.aws_boto3 import get_session
from tracecat.config import (
    TRACECAT__MAX_FILE_SIZE_BYTES,
    TRACECAT__S3_CONCURRENCY_LIMIT,
)

# Add this at the top with other constants
BUCKET_REGEX = re.compile(r"^[a-z0-9][a-z0-9.-]*[a-z0-9]$")

# Semaphore to limit concurrent S3 operations to prevent resource exhaustion
# This limits the number of concurrent S3 API calls
_s3_semaphore = asyncio.Semaphore(TRACECAT__S3_CONCURRENCY_LIMIT)

s3_secret = RegistrySecret(
    name="amazon_s3",
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

- name: `amazon_s3`
- optional_keys:
    Either:
        - `AWS_ACCESS_KEY_ID`
        - `AWS_SECRET_ACCESS_KEY`
        - `AWS_REGION`
    Or:
        - `AWS_PROFILE`
    Or:
        - `AWS_ROLE_ARN`
        - `AWS_ROLE_SESSION_NAME` (optional)
"""


@registry.register(
    default_title="Parse S3 URI",
    description="Parse an S3 URI into bucket name and object key.",
    display_group="Amazon S3",
    doc_url="https://boto3.amazonaws.com/v1/documentation/api/latest/guide/s3-example-download-file.html",
    namespace="tools.amazon_s3",
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
    display_group="Amazon S3",
    doc_url="https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/s3.html#S3.Client.get_object",
    namespace="tools.amazon_s3",
    secrets=[s3_secret],
)
async def get_object(
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


@registry.register(
    default_title="List S3 objects",
    description="List objects in an S3 bucket.",
    display_group="Amazon S3",
    doc_url="https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/s3.html#S3.Client.list_objects_v2",
    namespace="tools.amazon_s3",
    secrets=[s3_secret],
)
async def list_objects(
    bucket: Annotated[str, Field(..., description="S3 bucket name.")],
    prefix: Annotated[str, Field(..., description="S3 object key prefix.")],
    limit: Annotated[
        int, Field(..., description="Maximum number of objects to return.")
    ] = 1000,
) -> ListObjectsV2OutputTypeDef:
    session = await get_session()
    async with session.client("s3") as s3_client:
        response = await s3_client.list_objects_v2(
            Bucket=bucket, Prefix=prefix, MaxKeys=limit
        )
    return response


@registry.register(
    default_title="Get S3 objects",
    description="Download multiple S3 objects and return their bodies as strings.",
    display_group="Amazon S3",
    doc_url="https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/s3.html#S3.Client.get_object",
    namespace="tools.amazon_s3",
    secrets=[s3_secret],
)
async def get_objects(
    bucket: Annotated[str, Field(..., description="S3 bucket name.")],
    keys: Annotated[list[str], Field(..., description="S3 object keys.")],
) -> list[str]:
    # To prevent Amazon S3 rate limits and resource exhaustion
    @retry(
        stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10)
    )
    async def get_object_fn(key: str) -> str:
        # Use semaphore to limit concurrent S3 operations
        async with _s3_semaphore:
            return await get_object(bucket, key)

    return await asyncio.gather(*[get_object_fn(key) for key in keys])


@registry.register(
    default_title="Put S3 object",
    description="Put an object to S3.",
    display_group="Amazon S3",
    doc_url="https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/s3.html#S3.Client.put_object",
    namespace="tools.amazon_s3",
    secrets=[s3_secret],
)
async def upload_object(
    bucket: Annotated[str, Field(..., description="S3 bucket name.")],
    key: Annotated[str, Field(..., description="S3 object key.")],
    file_data: Annotated[
        str, Field(..., description="Base64 encoded content of the file to upload.")
    ],
) -> None:
    """Uploads an object to S3. The object key is validated and content decoded.

    Args:
        bucket: S3 bucket name.
        key: S3 object key. Will be validated for null bytes.
        file_data: Base64 encoded string of the file content.
    Raises:
        ValueError: If key is invalid, file_data is not valid base64, or file exceeds size limit.
    """
    if not key or "\x00" in key:
        raise ValueError(
            f"Invalid S3 object key '{key}': cannot be empty or contain null bytes."
        )

    try:
        content_bytes = base64.b64decode(file_data, validate=True)
    except binascii.Error as e:
        raise ValueError(f"Invalid base64 data for S3 object '{key}': {str(e)}") from e

    if len(content_bytes) > TRACECAT__MAX_FILE_SIZE_BYTES:
        raise ValueError(
            f"S3 object '{key}' exceeds maximum size limit of "
            f"{TRACECAT__MAX_FILE_SIZE_BYTES // 1024 // 1024}MB."
        )

    session = await get_session()
    async with session.client("s3") as s3_client:
        await s3_client.put_object(Bucket=bucket, Key=key, Body=content_bytes)


@registry.register(
    default_title="Delete S3 object",
    description="Delete an object from S3.",
    display_group="Amazon S3",
    doc_url="https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/s3.html#S3.Client.delete_object",
    namespace="tools.amazon_s3",
    secrets=[s3_secret],
)
async def delete_object(
    bucket: Annotated[str, Field(..., description="S3 bucket name.")],
    key: Annotated[str, Field(..., description="S3 object key.")],
) -> DeleteObjectOutputTypeDef:
    session = await get_session()
    async with session.client("s3") as s3_client:
        response = await s3_client.delete_object(Bucket=bucket, Key=key)
    return response
