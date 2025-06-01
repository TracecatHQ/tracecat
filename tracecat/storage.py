"""Storage utilities for handling file uploads and downloads with S3."""

import hashlib
import os
import re

import aioboto3
from botocore.exceptions import ClientError

from tracecat import config
from tracecat.logger import logger

# Security configuration based on OWASP recommendations
# Limited set of allowed MIME types for case management
ALLOWED_CONTENT_TYPES = {
    # Documents
    "application/pdf",
    "application/msword",  # .doc
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",  # .docx
    "application/vnd.ms-excel",  # .xls
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",  # .xlsx
    "text/plain",
    "text/csv",
    # Images
    "image/jpeg",
    "image/png",
    "image/gif",
    "image/webp",
    # Archives (with caution)
    "application/zip",
    "application/x-7z-compressed",
}

# Explicitly blocked dangerous types
BLOCKED_CONTENT_TYPES = {
    "application/x-executable",
    "application/x-sharedlib",
    "application/x-httpd-php",
    "application/javascript",
    "application/x-sh",
    "application/x-csh",
    "text/html",
    "text/x-python",
    "text/x-perl",
    "text/x-ruby",
    "application/x-msdownload",  # .exe, .dll
    "application/x-msdos-program",
}

# File size limits
MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB
MAX_FILENAME_LENGTH = 255


def get_storage_session() -> aioboto3.Session:
    """Get an aioboto3 session for S3 operations.

    Assumes AWS Fargate with IAM roles, so no explicit credentials needed.
    """
    return aioboto3.Session()


def compute_sha256(content: bytes) -> str:
    """Compute SHA256 hash of content.

    Args:
        content: The content to hash

    Returns:
        Hex-encoded SHA256 hash
    """
    return hashlib.sha256(content).hexdigest()


def validate_content_type(content_type: str) -> None:
    """Validate the content type against allow and deny lists.

    Args:
        content_type: The MIME type to validate

    Raises:
        ValueError: If the content type is not allowed or is blocked
    """
    # Normalize content type (remove parameters like charset)
    base_content_type = content_type.split(";")[0].strip().lower()

    if base_content_type in BLOCKED_CONTENT_TYPES:
        raise ValueError(
            f"File type '{base_content_type}' is not allowed for security reasons"
        )

    if base_content_type not in ALLOWED_CONTENT_TYPES:
        raise ValueError(
            f"File type '{base_content_type}' is not supported. "
            f"Allowed types: {', '.join(sorted(ALLOWED_CONTENT_TYPES))}"
        )


def validate_file_size(size: int) -> None:
    """Validate file size.

    Args:
        size: File size in bytes

    Raises:
        ValueError: If the file is too large
    """
    if size > MAX_FILE_SIZE:
        raise ValueError(
            f"File size ({size / 1024 / 1024:.1f}MB) exceeds maximum allowed size "
            f"({MAX_FILE_SIZE / 1024 / 1024}MB)"
        )


def sanitize_filename(filename: str) -> str:
    """Sanitize filename to prevent directory traversal and other attacks.

    Args:
        filename: The original filename

    Returns:
        Sanitized filename
    """
    # Get just the filename without any path components
    filename = os.path.basename(filename)

    # Remove any non-alphanumeric characters except dots, hyphens, and underscores
    filename = re.sub(r"[^\w\s.-]", "", filename)

    # Replace spaces with underscores
    filename = filename.replace(" ", "_")

    # Remove multiple consecutive dots (prevent directory traversal)
    filename = re.sub(r"\.{2,}", ".", filename)

    # Ensure filename doesn't start with a dot (hidden files)
    filename = filename.lstrip(".")

    # Truncate if too long (leave room for extension)
    if len(filename) > MAX_FILENAME_LENGTH:
        name, ext = os.path.splitext(filename)
        max_name_length = MAX_FILENAME_LENGTH - len(ext)
        filename = name[:max_name_length] + ext

    # If filename is empty after sanitization, generate a default
    if not filename:
        filename = "unnamed_file"

    return filename


async def upload_file(
    content: bytes,
    key: str,
    content_type: str | None = None,
    bucket: str | None = None,
) -> None:
    """Upload a file to S3.

    Args:
        content: The file content as bytes
        key: The S3 object key
        content_type: Optional MIME type of the file
        bucket: Optional bucket name (defaults to config)

    Raises:
        ClientError: If the upload fails
    """
    bucket = bucket or config.TRACECAT__BLOB_STORAGE_BUCKET
    session = get_storage_session()

    try:
        async with session.client("s3") as s3_client:
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


async def download_file(key: str, bucket: str | None = None) -> bytes:
    """Download a file from S3.

    Args:
        key: The S3 object key
        bucket: Optional bucket name (defaults to config)

    Returns:
        The file content as bytes

    Raises:
        FileNotFoundError: If the file doesn't exist
        ClientError: If the download fails
    """
    bucket = bucket or config.TRACECAT__BLOB_STORAGE_BUCKET
    session = get_storage_session()

    try:
        async with session.client("s3") as s3_client:
            response = await s3_client.get_object(Bucket=bucket, Key=key)
            content = await response["Body"].read()
            logger.info(
                "File downloaded successfully",
                key=key,
                bucket=bucket,
                size=len(content),
            )
            return content
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") == "NoSuchKey":
            logger.warning("File not found", key=key, bucket=bucket)
            raise FileNotFoundError(f"File not found: {key}") from e
        logger.error(
            "Failed to download file",
            key=key,
            bucket=bucket,
            error=str(e),
        )
        raise


async def delete_file(key: str, bucket: str | None = None) -> None:
    """Delete a file from S3.

    Args:
        key: The S3 object key
        bucket: Optional bucket name (defaults to config)

    Raises:
        ClientError: If the deletion fails
    """
    bucket = bucket or config.TRACECAT__BLOB_STORAGE_BUCKET
    session = get_storage_session()

    try:
        async with session.client("s3") as s3_client:
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


async def file_exists(key: str, bucket: str | None = None) -> bool:
    """Check if a file exists in S3.

    Args:
        key: The S3 object key
        bucket: Optional bucket name (defaults to config)

    Returns:
        True if the file exists, False otherwise

    Raises:
        ClientError: If the check fails (other than 404)
    """
    bucket = bucket or config.TRACECAT__BLOB_STORAGE_BUCKET
    session = get_storage_session()

    try:
        async with session.client("s3") as s3_client:
            await s3_client.head_object(Bucket=bucket, Key=key)
            return True
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") == "404":
            return False
        logger.error(
            "Failed to check file existence",
            key=key,
            bucket=bucket,
            error=str(e),
        )
        raise
