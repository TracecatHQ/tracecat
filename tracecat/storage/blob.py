"""File upload and download functions for S3/MinIO."""

from __future__ import annotations

import hashlib
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING

import aioboto3
import aiofiles
from botocore.exceptions import ClientError

from tracecat import config
from tracecat.logger import logger

if TYPE_CHECKING:
    from aiobotocore.response import StreamingBody
    from types_aiobotocore_s3.client import S3Client
    from types_aiobotocore_s3.type_defs import (
        BucketLifecycleConfigurationTypeDef,
        GetBucketLifecycleConfigurationOutputTypeDef,
    )


DEFAULT_DOWNLOAD_CHUNK_SIZE_BYTES = 8 * 1024 * 1024  # 8MB


@asynccontextmanager
async def get_storage_client() -> AsyncIterator[S3Client]:
    """Get a configured S3 client for either AWS S3.

    Yields:
        Configured aioboto3 S3 client
    """
    session = aioboto3.Session()
    # Configure client based on protocol
    if config.TRACECAT__BLOB_STORAGE_ENDPOINT:
        # MinIO configuration - use AWS_* or MINIO_ROOT_* credentials
        async with session.client(
            "s3",
            endpoint_url=config.TRACECAT__BLOB_STORAGE_ENDPOINT,
            # Defaults to minio default credentials. MUST REPLACE WITH PRODUCTION CREDENTIALS.
            aws_access_key_id=os.environ.get(
                "AWS_ACCESS_KEY_ID",
                os.environ.get("MINIO_ROOT_USER"),
            ),
            aws_secret_access_key=os.environ.get(
                "AWS_SECRET_ACCESS_KEY",
                os.environ.get("MINIO_ROOT_PASSWORD"),
            ),
        ) as client:
            yield client
    else:
        # AWS S3 configuration - use AWS credentials from environment or default credential chain
        async with session.client("s3") as client:
            yield client


async def ensure_bucket_exists(bucket: str) -> None:
    """Ensure the storage bucket exists, creating it if necessary.

    Args:
        bucket: Bucket name (required)
    """

    async with get_storage_client() as s3_client:
        try:
            await s3_client.head_bucket(Bucket=bucket)
            logger.debug("Bucket exists", bucket=bucket)
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code")
            if error_code == "404":
                # Bucket doesn't exist, create it
                try:
                    await s3_client.create_bucket(Bucket=bucket)
                    logger.info("Created bucket", bucket=bucket)
                except ClientError as create_error:
                    logger.error(
                        "Failed to create bucket",
                        bucket=bucket,
                        error=str(create_error),
                    )
                    raise
            else:
                logger.error(
                    "Failed to check bucket existence",
                    bucket=bucket,
                    error=str(e),
                )
                raise


async def get_bucket_lifecycle(
    bucket: str,
) -> GetBucketLifecycleConfigurationOutputTypeDef | None:
    """Get the current lifecycle configuration for a bucket.

    Args:
        bucket: Bucket name

    Returns:
        Lifecycle configuration response, or None if not configured.
    """
    async with get_storage_client() as s3_client:
        try:
            response = await s3_client.get_bucket_lifecycle_configuration(Bucket=bucket)
            logger.debug("Retrieved bucket lifecycle configuration", bucket=bucket)
            return response
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code")
            if error_code == "NoSuchLifecycleConfiguration":
                logger.debug("No lifecycle configuration exists", bucket=bucket)
                return None
            logger.error(
                "Failed to get bucket lifecycle configuration",
                bucket=bucket,
                error=str(e),
            )
            raise


async def configure_bucket_lifecycle(
    bucket: str,
    expiration_days: int,
    rule_id: str = "workflow-artifact-expiration",
) -> None:
    """Configure lifecycle rules for a bucket to expire objects after N days.

    Args:
        bucket: Bucket name
        expiration_days: Number of days after which objects expire.
            If <= 0, any existing lifecycle rule is removed.
        rule_id: Identifier for the lifecycle rule.
    """
    if expiration_days <= 0:
        # Remove existing lifecycle rule if present
        async with get_storage_client() as s3_client:
            try:
                await s3_client.delete_bucket_lifecycle(Bucket=bucket)
                logger.info(
                    "Removed bucket lifecycle configuration",
                    bucket=bucket,
                )
            except ClientError as e:
                error_code = e.response.get("Error", {}).get("Code")
                if error_code == "NoSuchLifecycleConfiguration":
                    logger.debug(
                        "No lifecycle configuration to remove",
                        bucket=bucket,
                    )
                else:
                    logger.error(
                        "Failed to remove bucket lifecycle configuration",
                        bucket=bucket,
                        error=str(e),
                    )
                    raise
        return

    async with get_storage_client() as s3_client:
        try:
            lifecycle_config: BucketLifecycleConfigurationTypeDef = {
                "Rules": [
                    {
                        "ID": rule_id,
                        "Status": "Enabled",
                        "Filter": {"Prefix": ""},
                        "Expiration": {"Days": expiration_days},
                    }
                ]
            }
            await s3_client.put_bucket_lifecycle_configuration(
                Bucket=bucket,
                LifecycleConfiguration=lifecycle_config,
            )
            logger.info(
                "Configured bucket lifecycle",
                bucket=bucket,
                rule_id=rule_id,
                expiration_days=expiration_days,
            )
        except ClientError as e:
            logger.error(
                "Failed to configure bucket lifecycle",
                bucket=bucket,
                error=str(e),
            )
            raise


async def generate_presigned_download_url(
    key: str,
    bucket: str,
    expiry: int | None = None,
    force_download: bool = True,
    override_content_type: str | None = None,
) -> str:
    """Generate a presigned URL for downloading a file with enhanced security.

    Args:
        key: The S3 object key
        bucket: Bucket name (required)
        expiry: URL expiry time in seconds (defaults to config)
        force_download: If True, forces Content-Disposition: attachment
        override_content_type: Override the Content-Type header (e.g., 'application/octet-stream')

    Returns:
        Presigned URL for downloading the file

    Raises:
        ClientError: If URL generation fails
    """
    expiry = expiry or config.TRACECAT__BLOB_STORAGE_PRESIGNED_URL_EXPIRY

    # Build request parameters with security headers
    params = {"Bucket": bucket, "Key": key}

    # Force download instead of inline display
    if force_download:
        # Extract filename from key for proper download naming
        filename = key.split("/")[-1] if "/" in key else key
        params["ResponseContentDisposition"] = f'attachment; filename="{filename}"'

    # Override content type to prevent browser execution
    if override_content_type:
        params["ResponseContentType"] = override_content_type

    async with get_storage_client() as s3_client:
        try:
            url = await s3_client.generate_presigned_url(
                "get_object",
                Params=params,
                ExpiresIn=expiry,
            )
            if (
                config.TRACECAT__BLOB_STORAGE_PRESIGNED_URL_ENDPOINT is not None
                and config.TRACECAT__BLOB_STORAGE_ENDPOINT
            ):
                url = url.replace(
                    config.TRACECAT__BLOB_STORAGE_ENDPOINT,
                    config.TRACECAT__BLOB_STORAGE_PRESIGNED_URL_ENDPOINT,
                )
            return url
        except ClientError as e:
            logger.error(
                "Failed to generate presigned download URL",
                key=key,
                bucket=bucket,
                error=str(e),
            )
            raise


async def generate_presigned_upload_url(
    key: str,
    bucket: str,
    expiry: int | None = None,
    content_type: str | None = None,
) -> str:
    """Generate a presigned URL for uploading a file.

    Args:
        key: The S3 object key
        bucket: Bucket name (required)
        expiry: URL expiry time in seconds (defaults to config)
        content_type: Optional content type constraint

    Returns:
        Presigned URL for uploading the file

    Raises:
        ClientError: If URL generation fails
    """
    expiry = expiry or config.TRACECAT__BLOB_STORAGE_PRESIGNED_URL_EXPIRY

    params = {"Bucket": bucket, "Key": key}
    if content_type:
        params["ContentType"] = content_type

    async with get_storage_client() as s3_client:
        try:
            url = await s3_client.generate_presigned_url(
                "put_object",
                Params=params,
                ExpiresIn=expiry,
            )
            logger.debug(
                "Generated presigned upload URL",
                key=key,
                bucket=bucket,
                expiry=expiry,
                content_type=content_type,
            )
            return url
        except ClientError as e:
            logger.error(
                "Failed to generate presigned upload URL",
                key=key,
                bucket=bucket,
                error=str(e),
            )
            raise


async def upload_file(
    content: bytes,
    key: str,
    bucket: str,
    content_type: str | None = None,
) -> None:
    """Upload a file to S3.

    Args:
        content: The file content as bytes
        key: The S3 object key
        bucket: Bucket name (required)
        content_type: Optional MIME type of the file

    Raises:
        ClientError: If the upload fails
    """

    try:
        async with get_storage_client() as s3_client:
            kwargs = {
                "Bucket": bucket,
                "Key": key,
                "Body": content,
            }
            if content_type:
                kwargs["ContentType"] = content_type

            await s3_client.put_object(**kwargs)
            logger.info(
                "File uploaded successfully",
                key=key,
                bucket=bucket,
                size=len(content),
            )
    except ClientError as e:
        logger.error(
            "Failed to upload file",
            key=key,
            bucket=bucket,
            error=str(e),
        )
        raise


async def download_file(key: str, bucket: str) -> bytes:
    """Download a file from S3.

    Args:
        key: The S3 object key
        bucket: Bucket name (required)

    Returns:
        File content as bytes

    Raises:
        ClientError: If the download fails
        FileNotFoundError: If the file doesn't exist
    """

    async with open_download_stream(key=key, bucket=bucket) as (stream, _):
        content = await stream.read()

    logger.debug(
        "File downloaded successfully",
        key=key,
        bucket=bucket,
        size=len(content),
    )
    return content


async def download_file_range(
    *,
    key: str,
    bucket: str,
    start: int,
    end: int | None = None,
) -> bytes:
    """Download a byte range from an S3 object.

    Args:
        key: The S3 object key
        bucket: Bucket name (required)
        start: Inclusive start byte offset
        end: Inclusive end byte offset. If omitted, reads until EOF.

    Returns:
        The requested byte range as bytes

    Raises:
        FileNotFoundError: If the object doesn't exist
        ValueError: If the provided range is invalid
        ClientError: If the download fails
    """
    if start < 0:
        raise ValueError("Range start must be >= 0")
    if end is not None and end < start:
        raise ValueError("Range end must be >= start")

    range_header = f"bytes={start}-" if end is None else f"bytes={start}-{end}"

    try:
        async with get_storage_client() as s3_client:
            response = await s3_client.get_object(
                Bucket=bucket,
                Key=key,
                Range=range_header,
            )
            body: StreamingBody = response["Body"]
            async with body as stream:
                content = await stream.read()
    except ClientError as e:
        error_code = e.response.get("Error", {}).get("Code")
        if error_code == "NoSuchKey":
            logger.warning(
                "File not found in storage",
                key=key,
                bucket=bucket,
            )
            raise FileNotFoundError from e
        if error_code == "InvalidRange":
            raise ValueError(
                f"Invalid range requested for {bucket}/{key}: {range_header}"
            ) from e
        logger.error(
            "Failed to download file range",
            key=key,
            bucket=bucket,
            range=range_header,
            error=str(e),
        )
        raise

    logger.debug(
        "File range downloaded successfully",
        key=key,
        bucket=bucket,
        range=range_header,
        size=len(content),
    )
    return content


@asynccontextmanager
async def open_download_stream(
    key: str,
    bucket: str,
) -> AsyncIterator[tuple[StreamingBody, int | None]]:
    """Open a streaming download for an S3/MinIO object.

    This is safer for very large objects because it allows callers to
    consume the response body incrementally (e.g., write to disk or stream
    to an HTTP response) instead of reading the entire object into memory.

    Usage:
        async with open_download_stream(key, bucket) as (stream, content_length):
            async for chunk in stream.iter_chunks(chunk_size=...):
                ...

    Args:
        key: The S3 object key.
        bucket: Bucket name (required).

    Yields:
        Tuple of (StreamingBody, content_length).

    Raises:
        ClientError: If the download fails.
        FileNotFoundError: If the file doesn't exist.
    """
    try:
        async with get_storage_client() as s3_client:
            response = await s3_client.get_object(Bucket=bucket, Key=key)
            body: StreamingBody = response["Body"]
            content_length: int | None = response.get("ContentLength")
            async with body as stream:
                yield stream, content_length
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") == "NoSuchKey":
            logger.warning(
                "File not found in storage",
                key=key,
                bucket=bucket,
            )
            raise FileNotFoundError from e
        logger.error(
            "Failed to open download stream",
            key=key,
            bucket=bucket,
            error=str(e),
        )
        raise


async def download_file_to_path(
    *,
    key: str,
    bucket: str,
    output_path: Path,
    chunk_size: int = DEFAULT_DOWNLOAD_CHUNK_SIZE_BYTES,
    max_bytes: int | None = None,
    expected_sha256: str | None = None,
) -> int:
    """Stream an S3/MinIO object to a local file.

    This avoids loading the full object into memory and is recommended for large
    blobs (e.g., tarballs).

    Args:
        key: The S3 object key.
        bucket: Bucket name (required).
        output_path: Local path to write to.
        chunk_size: Chunk size for streaming reads (default: 8MB).
        max_bytes: Optional guardrail; raise if the object exceeds this size.
        expected_sha256: Optional integrity check; raise if computed SHA-256 differs.

    Returns:
        Total bytes written.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = output_path.with_name(f"{output_path.name}.part")

    hasher = hashlib.sha256() if expected_sha256 is not None else None
    bytes_written = 0

    try:
        async with open_download_stream(key=key, bucket=bucket) as (
            stream,
            content_length,
        ):
            if (
                max_bytes is not None
                and content_length is not None
                and content_length > max_bytes
            ):
                raise ValueError(
                    f"Refusing to download {bucket}/{key} to disk: "
                    f"ContentLength={content_length} exceeds max_bytes={max_bytes}"
                )

            async with aiofiles.open(temp_path, "wb") as f:
                async for chunk in stream.iter_chunks(chunk_size=chunk_size):
                    if not chunk:
                        continue
                    bytes_written += len(chunk)
                    if max_bytes is not None and bytes_written > max_bytes:
                        raise ValueError(
                            f"Refusing to download {bucket}/{key} to disk: "
                            f"bytes_written={bytes_written} exceeds max_bytes={max_bytes}"
                        )
                    if hasher is not None:
                        hasher.update(chunk)
                    await f.write(chunk)

        if hasher is not None:
            actual_sha256 = hasher.hexdigest()
            if actual_sha256 != expected_sha256:
                raise ValueError(
                    f"Integrity check failed for {bucket}/{key}: "
                    f"expected {expected_sha256}, got {actual_sha256}"
                )

        os.replace(temp_path, output_path)
    except Exception:
        try:
            temp_path.unlink(missing_ok=True)
        except Exception:
            logger.warning(
                "Failed to cleanup partial download",
                temp_path=str(temp_path),
            )
        raise

    logger.debug(
        "File streamed to disk successfully",
        key=key,
        bucket=bucket,
        output_path=str(output_path),
        size=bytes_written,
    )
    return bytes_written


async def delete_file(key: str, bucket: str) -> None:
    """Delete a file from S3.

    Args:
        key: The S3 object key
        bucket: Bucket name (required)

    Raises:
        ClientError: If the deletion fails
    """

    try:
        async with get_storage_client() as s3_client:
            await s3_client.delete_object(Bucket=bucket, Key=key)
            logger.info(
                "File deleted successfully",
                key=key,
                bucket=bucket,
            )
    except ClientError as e:
        logger.error(
            "Failed to delete file",
            key=key,
            bucket=bucket,
            error=str(e),
        )
        raise


async def file_exists(key: str, bucket: str) -> bool:
    """Check if a file exists in S3.

    Args:
        key: The S3 object key
        bucket: Bucket name (required)

    Returns:
        True if the file exists, False otherwise
    """
    try:
        async with get_storage_client() as s3_client:
            await s3_client.head_object(Bucket=bucket, Key=key)
            return True
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") == "404":
            return False
        raise


async def select_object_content(
    key: str,
    bucket: str,
    expression: str,
) -> bytes:
    """Execute S3 Select SQL query on a JSON object.

    Uses S3 Select to query JSON documents without downloading the entire object.
    Useful for extracting specific fields or array elements.

    Args:
        key: The S3 object key
        bucket: Bucket name
        expression: SQL expression (e.g., "SELECT s.items[0] FROM s3object s")

    Returns:
        Query result as JSON bytes

    Raises:
        ClientError: If the query fails
    """
    async with get_storage_client() as s3_client:
        try:
            response = await s3_client.select_object_content(
                Bucket=bucket,
                Key=key,
                ExpressionType="SQL",
                Expression=expression,
                InputSerialization={"JSON": {"Type": "DOCUMENT"}},
                OutputSerialization={"JSON": {}},
            )

            # Collect streaming response payload
            result_bytes = b""
            async for event in response["Payload"]:
                if records := event.get("Records"):
                    if payload := records.get("Payload"):
                        result_bytes += payload

            logger.debug(
                "S3 Select query executed",
                key=key,
                bucket=bucket,
                expression=expression,
                result_size=len(result_bytes),
            )
            return result_bytes
        except ClientError as e:
            logger.error(
                "S3 Select query failed",
                key=key,
                bucket=bucket,
                expression=expression,
                error=str(e),
            )
            raise
