"""S3 integration to download files and return contents as a string."""

import re
from typing import Annotated, Any
from typing_extensions import Doc
import base64
import binascii
import asyncio

from tenacity import retry, stop_after_attempt, wait_exponential
from types_aiobotocore_s3.type_defs import (
    ListObjectsV2OutputTypeDef,
    DeleteObjectOutputTypeDef,
)

from tracecat_registry import RegistrySecret, registry
import tracecat_registry.integrations.aws_boto3 as aws_boto3
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
    optional=True,  # Might be Minio etc.
)
"""AWS credentials.

- name: `amazon_s3`
- optional_keys:
    Either:
        - `AWS_ACCESS_KEY_ID`
        - `AWS_SECRET_ACCESS_KEY`
    Or:
        - `AWS_PROFILE`
    Or:
        - `AWS_ROLE_ARN`
        - `AWS_ROLE_SESSION_NAME` (optional)
    And:
        - `AWS_REGION`
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
    default_title="Call S3 method",
    description="Instantiate a S3 client and call a S3 method.",
    display_group="Amazon S3",
    doc_url="https://boto3.amazonaws.com/v1/documentation/api/latest/guide/s3-example-download-file.html",
    namespace="tools.amazon_s3",
    secrets=[s3_secret],
)
async def call_method(
    method_name: Annotated[str, Doc("S3 method name.")],
    params: Annotated[dict[str, Any], Doc("S3 method parameters.")],
) -> dict[str, Any]:
    session = await aws_boto3.get_session()
    async with session.client("s3") as s3_client:  # type: ignore
        return await getattr(s3_client, method_name)(**params)


@registry.register(
    default_title="Get S3 object",
    description="Download an object from S3 and return its body as a string.",
    display_group="Amazon S3",
    doc_url="https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/s3.html#S3.Client.get_object",
    namespace="tools.amazon_s3",
    secrets=[s3_secret],
)
async def get_object(
    bucket: Annotated[str, Doc("S3 bucket name.")],
    key: Annotated[str, Doc("S3 object key.")],
    endpoint_url: Annotated[
        str | None,
        Doc("Endpoint URL for the AWS S3 service."),
    ] = None,
) -> str:
    session = await aws_boto3.get_session()
    async with session.client("s3", endpoint_url=endpoint_url) as s3_client:  # type: ignore
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
    bucket: Annotated[str, Doc("S3 bucket name.")],
    prefix: Annotated[str | None, Doc("S3 object key prefix.")] = None,
    limit: Annotated[int, Doc("Maximum number of objects to return.")] = 1000,
    endpoint_url: Annotated[
        str | None,
        Doc("Endpoint URL for the AWS S3 service."),
    ] = None,
) -> ListObjectsV2OutputTypeDef:
    session = await aws_boto3.get_session()
    async with session.client("s3", endpoint_url=endpoint_url) as s3_client:  # type: ignore
        if prefix:
            response = await s3_client.list_objects_v2(
                Bucket=bucket, Prefix=prefix, MaxKeys=limit
            )
        else:
            response = await s3_client.list_objects_v2(Bucket=bucket, MaxKeys=limit)
    return response


@registry.register(
    default_title="Copy S3 objects",
    description="Copy S3 objects from one bucket to another.",
    display_group="Amazon S3",
    doc_url="https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/s3.html#S3.Client.copy_object",
    namespace="tools.amazon_s3",
    secrets=[s3_secret],
)
async def copy_objects(
    src_bucket: Annotated[str, Doc("Source S3 bucket name.")],
    dst_bucket: Annotated[str, Doc("Destination S3 bucket name.")],
    prefix: Annotated[
        str,
        Doc(
            "Prefix to filter objects (e.g., 'manuals_' for keys starting with 'manuals_')."
        ),
    ],
    endpoint_url: Annotated[
        str | None,
        Doc("Endpoint URL for the AWS S3 service."),
    ] = None,
) -> list[dict[str, Any]]:
    """Copy S3 objects from one bucket to another.

    Lists objects in source bucket with the given prefix and copies them to
    the destination bucket, keeping the same S3 keys. Returns a list of copy operation results from S3.

    Returns:
        A list of copy operation results from S3.
    """
    session = await aws_boto3.get_session()
    results = []

    async with session.client("s3", endpoint_url=endpoint_url) as s3_client:
        # List objects with prefix
        response = await s3_client.list_objects_v2(Bucket=src_bucket, Prefix=prefix)

        if "Contents" not in response:
            return []

        # Copy all objects and collect results
        for obj in response["Contents"]:
            key = obj.get("Key")
            if not key:
                continue

            try:
                copy_source = f"{src_bucket}/{key}"
                copy_response = await s3_client.copy_object(
                    CopySource=copy_source, Bucket=dst_bucket, Key=key
                )
                # Create a new dict with response data plus metadata
                result = {
                    **copy_response,
                    "Key": key,
                    "SourceBucket": src_bucket,
                    "DestinationBucket": dst_bucket,
                }
                results.append(result)
            except Exception as e:
                # Include failed operations in results with error info
                results.append(
                    {
                        "Key": key,
                        "SourceBucket": src_bucket,
                        "DestinationBucket": dst_bucket,
                        "Error": str(e),
                        "Success": False,
                    }
                )

    return results


@registry.register(
    default_title="Get S3 objects",
    description="Download multiple S3 objects and return their bodies as strings.",
    display_group="Amazon S3",
    doc_url="https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/s3.html#S3.Client.get_object",
    namespace="tools.amazon_s3",
    secrets=[s3_secret],
)
async def get_objects(
    bucket: Annotated[str, Doc("S3 bucket name.")],
    keys: Annotated[list[str], Doc("S3 object keys.")],
    endpoint_url: Annotated[
        str | None,
        Doc("Endpoint URL for the AWS S3 service."),
    ] = None,
) -> list[str]:
    # To prevent Amazon S3 rate limits and resource exhaustion
    @retry(
        stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10)
    )
    async def get_object_fn(key: str) -> str:
        # Use semaphore to limit concurrent S3 operations
        async with _s3_semaphore:
            return await get_object(bucket, key, endpoint_url)

    return await asyncio.gather(*[get_object_fn(key) for key in keys])


@registry.register(
    default_title="Put S3 object",
    description="Put an object to S3.",
    display_group="Amazon S3",
    doc_url="https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/s3.html#S3.Client.put_object",
    namespace="tools.amazon_s3",
    secrets=[s3_secret],
)
async def put_object(
    bucket: Annotated[str, Doc("S3 bucket name.")],
    key: Annotated[str, Doc("S3 object key.")],
    file_data: Annotated[str, Doc("Base64 encoded content of the file to upload.")],
    endpoint_url: Annotated[
        str | None,
        Doc("Endpoint URL for the AWS S3 service."),
    ] = None,
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

    session = await aws_boto3.get_session()
    async with session.client("s3", endpoint_url=endpoint_url) as s3_client:  # type: ignore
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
    bucket: Annotated[str, Doc("S3 bucket name.")],
    key: Annotated[str, Doc("S3 object key.")],
    endpoint_url: Annotated[
        str | None,
        Doc("Endpoint URL for the AWS S3 service."),
    ] = None,
) -> DeleteObjectOutputTypeDef:
    session = await aws_boto3.get_session()
    async with session.client("s3", endpoint_url=endpoint_url) as s3_client:  # type: ignore
        response = await s3_client.delete_object(Bucket=bucket, Key=key)
    return response
