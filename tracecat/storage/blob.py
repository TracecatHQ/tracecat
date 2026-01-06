"""File upload and download functions for S3/MinIO."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

import aioboto3
from botocore.exceptions import ClientError

from tracecat import config
from tracecat.logger import logger

if TYPE_CHECKING:
    from types_aiobotocore_s3.client import S3Client


@asynccontextmanager
async def get_storage_client() -> AsyncIterator[S3Client]:
    """Get a configured S3 client for either AWS S3.

    Yields:
        Configured aioboto3 S3 client
    """
    session = aioboto3.Session()
    # Configure client based on protocol
    if config.TRACECAT__BLOB_STORAGE_ENDPOINT is not None:
        # MinIO configuration - use MINIO_ROOT_USER/MINIO_ROOT_PASSWORD
        async with session.client(
            "s3",
            endpoint_url=config.TRACECAT__BLOB_STORAGE_ENDPOINT,
            # Defaults to minio default credentials. MUST REPLACE WITH PRODUCTION CREDENTIALS.
            aws_access_key_id=os.environ.get("AWS_ACCESS_KEY_ID", "minioadmin"),
            aws_secret_access_key=os.environ.get("AWS_SECRET_ACCESS_KEY", "minioadmin"),
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
            if config.TRACECAT__BLOB_STORAGE_PRESIGNED_URL_ENDPOINT is not None:
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

    try:
        async with get_storage_client() as s3_client:
            response = await s3_client.get_object(Bucket=bucket, Key=key)
            content = await response["Body"].read()
            logger.debug(
                "File downloaded successfully",
                key=key,
                bucket=bucket,
                size=len(content),
            )
            return content
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") == "NoSuchKey":
            logger.warning(
                "File not found in storage",
                key=key,
                bucket=bucket,
            )
            raise FileNotFoundError from e
        logger.error(
            "Failed to download file",
            key=key,
            bucket=bucket,
            error=str(e),
        )
        raise


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
