"""Tests for the storage module."""

import asyncio
import base64
import hashlib
import os
import threading
from collections.abc import Iterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, call, patch
from urllib.parse import urlparse

import httpx
import pytest
from botocore.exceptions import ClientError, EndpointConnectionError

from tracecat.storage import blob as blob_module
from tracecat.storage.blob import (
    configure_bucket_lifecycle,
    copy_file,
    delete_file,
    download_file,
    download_file_to_path,
    ensure_bucket_exists,
    file_exists,
    generate_presigned_download_url,
    generate_presigned_upload_url,
    get_storage_client,
    open_download_stream,
    upload_file,
    upload_file_from_path,
)

SENSITIVE_KEY = "skills/tenant-id/private-object"
SENSITIVE_SOURCE_KEY = "skill-uploads/tenant-id/private-object"
SENSITIVE_BUCKET = "affected-customer-bucket"
PROVIDER_MESSAGE = (
    f"denied {SENSITIVE_BUCKET}/{SENSITIVE_SOURCE_KEY} to {SENSITIVE_KEY}"
)
REDACTED = "<redacted>"

# op -> (client method, redacted error name, failure log message)
_OPS: dict[str, tuple[str, str, str]] = {
    "upload": ("put_object", "StorageUploadError", "Failed to upload file"),
    "download": (
        "get_object",
        "StorageDownloadError",
        "Failed to open download stream",
    ),
    "copy": ("copy_object", "StorageCopyError", "Failed to copy file"),
    "delete": (
        "delete_object",
        "StorageDeleteError",
        "Failed to delete file",
    ),
    "file_exists": (
        "head_object",
        "StorageMetadataError",
        "Failed to check file existence",
    ),
    "presigned_download": (
        "generate_presigned_url",
        "StoragePresignError",
        "Failed to generate presigned download URL",
    ),
    "presigned_upload": (
        "generate_presigned_url",
        "StoragePresignError",
        "Failed to generate presigned upload URL",
    ),
}


@pytest.fixture
def mock_s3() -> Iterator[AsyncMock]:
    client = AsyncMock()
    with patch("tracecat.storage.blob.get_storage_client") as get_client:
        get_client.return_value.__aenter__.return_value = client
        yield client


@pytest.fixture
def storage_logger() -> Iterator[MagicMock]:
    with patch("tracecat.storage.blob.logger") as logger:
        yield logger


async def _call_op(op: str, *, redact: bool) -> object:
    match op:
        case "upload":
            return await upload_file(
                b"content",
                SENSITIVE_KEY,
                SENSITIVE_BUCKET,
                redact_log_identifiers=redact,
            )
        case "download":
            return await download_file(
                SENSITIVE_KEY, SENSITIVE_BUCKET, redact_log_identifiers=redact
            )
        case "copy":
            return await copy_file(
                source_key=SENSITIVE_SOURCE_KEY,
                destination_key=SENSITIVE_KEY,
                bucket=SENSITIVE_BUCKET,
                content_type="application/octet-stream",
                redact_log_identifiers=redact,
            )
        case "delete":
            return await delete_file(
                SENSITIVE_KEY, SENSITIVE_BUCKET, redact_log_identifiers=redact
            )
        case "file_exists":
            return await file_exists(
                SENSITIVE_KEY, SENSITIVE_BUCKET, redact_log_identifiers=redact
            )
        case "presigned_download":
            return await generate_presigned_download_url(
                key=SENSITIVE_KEY,
                bucket=SENSITIVE_BUCKET,
                redact_log_identifiers=redact,
            )
        case "presigned_upload":
            return await generate_presigned_upload_url(
                key=SENSITIVE_KEY,
                bucket=SENSITIVE_BUCKET,
                expiry=60,
                content_type="application/octet-stream",
                checksum_sha256="base64-checksum",
                redact_log_identifiers=redact,
            )
    raise AssertionError(op)


def _redacted_identifiers(op: str) -> dict[str, str]:
    if op == "copy":
        return {
            "source_key": REDACTED,
            "destination_key": REDACTED,
            "bucket": REDACTED,
        }
    return {"key": REDACTED, "bucket": REDACTED}


def _provider_error() -> ClientError:
    return ClientError(
        error_response={"Error": {"Code": "AccessDenied", "Message": PROVIDER_MESSAGE}},
        operation_name="Op",
    )


def _assert_no_identifiers(text: str) -> None:
    for secret in (
        SENSITIVE_KEY,
        SENSITIVE_SOURCE_KEY,
        SENSITIVE_BUCKET,
        PROVIDER_MESSAGE,
    ):
        assert secret not in text


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("op", "client_call", "log_level", "log_message", "log_extra"),
    [
        pytest.param(
            "upload",
            call.put_object(
                Bucket=SENSITIVE_BUCKET, Key=SENSITIVE_KEY, Body=b"content"
            ),
            "info",
            "File uploaded successfully",
            {"size": 7},
            id="upload",
        ),
        pytest.param(
            "download",
            call.get_object(Bucket=SENSITIVE_BUCKET, Key=SENSITIVE_KEY),
            "debug",
            "File downloaded successfully",
            {"size": 7},
            id="download",
        ),
        pytest.param(
            "copy",
            call.copy_object(
                Bucket=SENSITIVE_BUCKET,
                Key=SENSITIVE_KEY,
                CopySource={"Bucket": SENSITIVE_BUCKET, "Key": SENSITIVE_SOURCE_KEY},
                ContentType="application/octet-stream",
                MetadataDirective="REPLACE",
            ),
            "info",
            "File copied successfully",
            {},
            id="copy",
        ),
        pytest.param(
            "presigned_upload",
            call.generate_presigned_url(
                "put_object",
                Params={
                    "Bucket": SENSITIVE_BUCKET,
                    "Key": SENSITIVE_KEY,
                    "ContentType": "application/octet-stream",
                    "ChecksumSHA256": "base64-checksum",
                },
                ExpiresIn=60,
            ),
            "debug",
            "Generated presigned upload URL",
            {
                "expiry": 60,
                "content_type": "application/octet-stream",
                "checksum_sha256": "base64-checksum",
            },
            id="presigned_upload",
        ),
    ],
)
async def test_redacted_success_logs_hide_identifiers(
    mock_s3: AsyncMock,
    storage_logger: MagicMock,
    op: str,
    client_call: Any,
    log_level: str,
    log_message: str,
    log_extra: dict[str, object],
) -> None:
    body = AsyncMock()
    body.read.return_value = b"content"
    mock_s3.get_object.return_value = {"Body": body}
    mock_s3.generate_presigned_url.return_value = "https://example.com/upload"

    await _call_op(op, redact=True)

    assert mock_s3.mock_calls == [client_call]
    getattr(storage_logger, log_level).assert_called_once_with(
        log_message, **_redacted_identifiers(op), **log_extra
    )
    _assert_no_identifiers(str(storage_logger.mock_calls))


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("op", "failure"),
    [
        ("upload", "provider"),
        ("upload", "transport"),
        ("download", "provider"),
        ("download", "transport"),
        ("copy", "provider"),
        ("delete", "provider"),
        ("file_exists", "provider"),
        ("file_exists", "transport"),
        ("presigned_download", "provider"),
        ("presigned_upload", "provider"),
    ],
)
async def test_redacted_failures_hide_identifiers_and_provider_prose(
    mock_s3: AsyncMock, storage_logger: MagicMock, op: str, failure: str
) -> None:
    client_method, error_name, log_message = _OPS[op]
    error: Exception
    if failure == "provider":
        error, error_code = _provider_error(), "AccessDenied"
    else:
        error = EndpointConnectionError(
            endpoint_url=f"https://{SENSITIVE_BUCKET}/{SENSITIVE_KEY}"
        )
        error_code = None
    getattr(mock_s3, client_method).side_effect = error

    with pytest.raises(getattr(blob_module, error_name)) as raised:
        await _call_op(op, redact=True)

    assert raised.value.error_code == error_code
    _assert_no_identifiers(str(raised.value))
    storage_logger.error.assert_called_once_with(
        log_message,
        **_redacted_identifiers(op),
        error_code=error_code,
        error_type=type(error).__name__,
    )
    _assert_no_identifiers(str(storage_logger.mock_calls))


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("op", "logs_failure"),
    [
        ("upload", True),
        ("download", True),
        ("presigned_download", True),
        ("file_exists", False),
    ],
)
async def test_default_failures_reraise_client_error_unredacted(
    mock_s3: AsyncMock, storage_logger: MagicMock, op: str, logs_failure: bool
) -> None:
    client_method, _, log_message = _OPS[op]
    error = _provider_error()
    getattr(mock_s3, client_method).side_effect = error

    with pytest.raises(ClientError) as raised:
        await _call_op(op, redact=False)

    assert raised.value is error
    if logs_failure:
        storage_logger.error.assert_called_once_with(
            log_message,
            key=SENSITIVE_KEY,
            bucket=SENSITIVE_BUCKET,
            error=str(error),
        )
    else:
        assert not storage_logger.mock_calls


@pytest.mark.anyio
async def test_file_exists_redacted_not_found_remains_false(mock_s3: AsyncMock) -> None:
    mock_s3.head_object.side_effect = ClientError(
        error_response={"Error": {"Code": "404"}},
        operation_name="HeadObject",
    )

    assert not await file_exists(
        SENSITIVE_KEY, SENSITIVE_BUCKET, redact_log_identifiers=True
    )


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("key", "kwargs", "extra_params"),
    [
        pytest.param(
            "test/file.txt",
            {},
            {"ResponseContentDisposition": 'attachment; filename="file.txt"'},
            id="forces_attachment_by_default",
        ),
        pytest.param(
            "test/image.png", {"force_download": False}, {}, id="preview_inline"
        ),
        pytest.param(
            "test/file.bin",
            {"override_content_type": "application/octet-stream"},
            {
                "ResponseContentDisposition": 'attachment; filename="file.bin"',
                "ResponseContentType": "application/octet-stream",
            },
            id="content_type_override",
        ),
    ],
)
async def test_presigned_download_response_headers(
    mock_s3: AsyncMock,
    key: str,
    kwargs: dict[str, Any],
    extra_params: dict[str, str],
) -> None:
    mock_s3.generate_presigned_url.return_value = "https://example.com/presigned-url"

    result = await generate_presigned_download_url(key, "test-bucket", 3600, **kwargs)

    assert result == "https://example.com/presigned-url"
    mock_s3.generate_presigned_url.assert_called_once_with(
        "get_object",
        Params={"Bucket": "test-bucket", "Key": key, **extra_params},
        ExpiresIn=3600,
    )


class TestS3Operations:
    """Test S3/MinIO operations."""

    @pytest.fixture(autouse=True)
    def clear_session_cache(self):
        blob_module.clear_storage_session_cache()
        yield
        blob_module.clear_storage_session_cache()

    @pytest.mark.anyio
    async def test_ensure_bucket_exists_create_new(self, mock_s3: AsyncMock):
        """Test ensure_bucket_exists when bucket needs to be created."""

        # Simulate bucket not found
        mock_s3.head_bucket.side_effect = ClientError(
            error_response={"Error": {"Code": "404"}}, operation_name="head_bucket"
        )

        await ensure_bucket_exists("test-bucket")
        mock_s3.head_bucket.assert_called_once_with(Bucket="test-bucket")
        mock_s3.create_bucket.assert_called_once_with(Bucket="test-bucket")

    @pytest.mark.anyio
    async def test_ensure_bucket_exists_once_checks_each_bucket_once(
        self, mock_s3: AsyncMock, monkeypatch: pytest.MonkeyPatch
    ):
        """Externalized results stop paying a HEAD request per upload."""
        monkeypatch.setattr(blob_module, "_verified_buckets", set())

        for _ in range(3):
            await blob_module.ensure_bucket_exists_once("results")
        await blob_module.ensure_bucket_exists_once("other")

        assert mock_s3.head_bucket.call_args_list == [
            call(Bucket="results"),
            call(Bucket="other"),
        ]

    @pytest.mark.anyio
    async def test_ensure_bucket_exists_once_retries_after_failure(
        self, mock_s3: AsyncMock, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setattr(blob_module, "_verified_buckets", set())
        mock_s3.head_bucket.side_effect = [
            ClientError(
                error_response={"Error": {"Code": "403"}},
                operation_name="head_bucket",
            ),
            None,
        ]

        with pytest.raises(ClientError):
            await blob_module.ensure_bucket_exists_once("results")
        await blob_module.ensure_bucket_exists_once("results")
        await blob_module.ensure_bucket_exists_once("results")

        assert mock_s3.head_bucket.call_count == 2

    @pytest.mark.anyio
    async def test_get_storage_client_minio_uses_endpoint_and_env(self, monkeypatch):
        """get_storage_client for MinIO uses endpoint and AWS env vars"""

        monkeypatch.setattr(
            blob_module.config,
            "TRACECAT__BLOB_STORAGE_ENDPOINT",
            "http://localhost:9002",
            raising=False,
        )
        monkeypatch.setattr(
            blob_module.config,
            "TRACECAT__BLOB_STORAGE_SSL_VERIFY",
            True,
            raising=False,
        )
        monkeypatch.setenv("AWS_ACCESS_KEY_ID", "minioadmin")
        monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "minioadmin")

        with patch("tracecat.storage.blob.aioboto3.Session") as mock_session_cls:
            mock_session = mock_session_cls.return_value
            mock_client = AsyncMock()
            mock_session.client.return_value.__aenter__.return_value = mock_client

            async with get_storage_client() as client:
                assert client is mock_client
            mock_session.client.assert_called_once_with(
                "s3",
                endpoint_url="http://localhost:9002",
                config=blob_module._STORAGE_CLIENT_CONFIG,
                verify=None,
                aws_access_key_id="minioadmin",
                aws_secret_access_key="minioadmin",
            )

    @pytest.mark.anyio
    async def test_get_storage_client_s3_defaults(self, monkeypatch):
        """get_storage_client for S3 uses default session client without endpoint."""
        monkeypatch.setattr(
            blob_module.config,
            "TRACECAT__BLOB_STORAGE_ENDPOINT",
            None,
            raising=False,
        )
        monkeypatch.setattr(
            blob_module.config,
            "TRACECAT__BLOB_STORAGE_SSL_VERIFY",
            True,
            raising=False,
        )

        with patch("tracecat.storage.blob.aioboto3.Session") as mock_session_cls:
            mock_session = mock_session_cls.return_value
            mock_client = AsyncMock()
            mock_session.client.return_value.__aenter__.return_value = mock_client

            async with get_storage_client() as client:
                assert client is mock_client
            mock_session.client.assert_called_once_with(
                "s3", config=blob_module._AWS_STORAGE_CLIENT_CONFIG, verify=None
            )

    @pytest.mark.anyio
    async def test_get_storage_client_minio_ssl_verify_disabled(self, monkeypatch):
        """SSL verification can be disabled for self-signed MinIO/S3 endpoints."""
        monkeypatch.setattr(
            blob_module.config,
            "TRACECAT__BLOB_STORAGE_ENDPOINT",
            "https://minio.internal:9000",
            raising=False,
        )
        monkeypatch.setattr(
            blob_module.config,
            "TRACECAT__BLOB_STORAGE_SSL_VERIFY",
            False,
            raising=False,
        )
        monkeypatch.setenv("AWS_ACCESS_KEY_ID", "minioadmin")
        monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "minioadmin")

        with patch("tracecat.storage.blob.aioboto3.Session") as mock_session_cls:
            mock_session = mock_session_cls.return_value
            mock_client = AsyncMock()
            mock_session.client.return_value.__aenter__.return_value = mock_client

            async with get_storage_client() as client:
                assert client is mock_client
            assert mock_session.client.call_args.kwargs["verify"] is False

    @pytest.mark.anyio
    async def test_get_storage_client_s3_ssl_verify_disabled(self, monkeypatch):
        """SSL verification flag is honored on the default AWS S3 path too."""
        monkeypatch.setattr(
            blob_module.config,
            "TRACECAT__BLOB_STORAGE_ENDPOINT",
            None,
            raising=False,
        )
        monkeypatch.setattr(
            blob_module.config,
            "TRACECAT__BLOB_STORAGE_SSL_VERIFY",
            False,
            raising=False,
        )

        with patch("tracecat.storage.blob.aioboto3.Session") as mock_session_cls:
            mock_session = mock_session_cls.return_value
            mock_client = AsyncMock()
            mock_session.client.return_value.__aenter__.return_value = mock_client

            async with get_storage_client() as client:
                assert client is mock_client
            assert mock_session.client.call_args.kwargs["verify"] is False

    @pytest.mark.anyio
    async def test_get_storage_client_reuses_entered_client_per_event_loop(
        self, monkeypatch
    ):
        """get_storage_client reuses the entered aiobotocore client in a loop."""
        monkeypatch.setattr(
            blob_module.config,
            "TRACECAT__BLOB_STORAGE_ENDPOINT",
            None,
            raising=False,
        )

        with patch("tracecat.storage.blob.aioboto3.Session") as mock_session_cls:
            mock_session = mock_session_cls.return_value
            mock_client = AsyncMock()
            mock_session.client.return_value.__aenter__.return_value = mock_client

            async with get_storage_client() as client:
                assert client is mock_client
            async with get_storage_client() as client:
                assert client is mock_client

            mock_session_cls.assert_called_once()
            mock_session.client.assert_called_once_with(
                "s3", config=blob_module._AWS_STORAGE_CLIENT_CONFIG, verify=None
            )
            mock_session.client.return_value.__aenter__.assert_awaited_once()

    @pytest.mark.anyio
    async def test_get_storage_client_concurrent_first_use_enters_client_once(
        self, monkeypatch
    ):
        """Concurrent first use shares a single entered aiobotocore client."""
        monkeypatch.setattr(
            blob_module.config,
            "TRACECAT__BLOB_STORAGE_ENDPOINT",
            None,
            raising=False,
        )

        with patch("tracecat.storage.blob.aioboto3.Session") as mock_session_cls:
            mock_session = mock_session_cls.return_value
            mock_client = AsyncMock()

            async def enter_client() -> AsyncMock:
                await asyncio.sleep(0.01)
                return mock_client

            mock_session.client.return_value.__aenter__.side_effect = enter_client

            async def use_client() -> None:
                async with get_storage_client() as client:
                    assert client is mock_client

            await asyncio.gather(*(use_client() for _ in range(50)))

            mock_session_cls.assert_called_once()
            mock_session.client.assert_called_once_with(
                "s3", config=blob_module._AWS_STORAGE_CLIENT_CONFIG, verify=None
            )
            mock_session.client.return_value.__aenter__.assert_awaited_once()

    @pytest.mark.anyio
    async def test_close_storage_client_cache_closes_entered_client_context(
        self, monkeypatch
    ):
        """close_storage_client_cache exits cached aiobotocore client contexts."""
        monkeypatch.setattr(
            blob_module.config,
            "TRACECAT__BLOB_STORAGE_ENDPOINT",
            None,
            raising=False,
        )

        with patch("tracecat.storage.blob.aioboto3.Session") as mock_session_cls:
            mock_session = mock_session_cls.return_value
            mock_client = AsyncMock()
            mock_session.client.return_value.__aenter__.return_value = mock_client

            async with get_storage_client() as client:
                assert client is mock_client

            mock_session.client.return_value.__aexit__.assert_not_awaited()

            await blob_module.close_storage_client_cache()

            mock_session.client.return_value.__aexit__.assert_awaited_once_with(
                None, None, None
            )

    def test_get_storage_client_uses_new_session_for_new_event_loop(self, monkeypatch):
        """get_storage_client does not reuse aioboto3 sessions across event loops."""
        monkeypatch.setattr(
            blob_module.config,
            "TRACECAT__BLOB_STORAGE_ENDPOINT",
            None,
            raising=False,
        )

        async def use_client(expected_client: AsyncMock) -> None:
            async with get_storage_client() as client:
                assert client is expected_client

        with patch("tracecat.storage.blob.aioboto3.Session") as mock_session_cls:
            mock_session = mock_session_cls.return_value
            mock_client = AsyncMock()
            mock_session.client.return_value.__aenter__.return_value = mock_client

            asyncio.run(use_client(mock_client))
            asyncio.run(use_client(mock_client))

            assert mock_session_cls.call_count == 2
            assert mock_session.client.call_count == 2

    def test_get_storage_client_closes_cache_when_event_loop_closes(self, monkeypatch):
        """Temporary asyncio.run loops close their cached aiobotocore clients."""
        monkeypatch.setattr(
            blob_module.config,
            "TRACECAT__BLOB_STORAGE_ENDPOINT",
            None,
            raising=False,
        )

        async def use_client(expected_client: AsyncMock) -> None:
            async with get_storage_client() as client:
                assert client is expected_client

        with patch("tracecat.storage.blob.aioboto3.Session") as mock_session_cls:
            mock_session = mock_session_cls.return_value
            mock_client = AsyncMock()
            mock_session.client.return_value.__aenter__.return_value = mock_client

            asyncio.run(use_client(mock_client))

            mock_session.client.return_value.__aexit__.assert_awaited_once_with(
                None, None, None
            )
            assert len(blob_module._STORAGE_CLIENTS) == 0

    @pytest.mark.anyio
    async def test_upload_file_from_path_uses_bounded_transfer_config(
        self,
        mock_s3: AsyncMock,
        tmp_path: Path,
    ):
        """Path uploads bound queued multipart payloads."""

        path = tmp_path / "site-packages.tar.gz"
        path.write_bytes(b"content")

        await upload_file_from_path(
            path=path,
            key="registry/site-packages.tar.gz",
            bucket="tracecat-registry",
            content_type="application/gzip",
        )

        mock_s3.upload_file.assert_called_once()
        kwargs = mock_s3.upload_file.call_args.kwargs
        assert kwargs["Filename"] == str(path)
        assert kwargs["Bucket"] == "tracecat-registry"
        assert kwargs["Key"] == "registry/site-packages.tar.gz"
        assert kwargs["ExtraArgs"] == {"ContentType": "application/gzip"}
        transfer_config = kwargs["Config"]
        assert transfer_config.multipart_threshold == 8 * 1024 * 1024
        assert transfer_config.multipart_chunksize == 8 * 1024 * 1024
        assert transfer_config.max_request_concurrency == 4
        assert transfer_config.max_io_queue_size == 2

    @pytest.mark.anyio
    async def test_download_file_not_found(self, mock_s3: AsyncMock):
        """Test file download when file doesn't exist."""

        mock_s3.get_object.side_effect = ClientError(
            error_response={"Error": {"Code": "NoSuchKey"}}, operation_name="get_object"
        )

        with pytest.raises(FileNotFoundError):
            await download_file("nonexistent.txt", "test-bucket")

    @pytest.mark.anyio
    @pytest.mark.parametrize(
        ("filename", "expected"),
        [
            (
                "Q3 quote.pdf",
                "attachment; filename=\"Q3 quote.pdf\"; filename*=UTF-8''Q3%20quote.pdf",
            ),
            (
                'a"; filename="evil.exe\r\nX: y.pdf',
                'attachment; filename="a__ filename__evil.exe__X_ y.pdf"; '
                "filename*=UTF-8''a%22%3B%20filename%3D%22evil.exe%0D%0AX%3A%20y.pdf",
            ),
            (
                "résumé.pdf",
                "attachment; filename=\"r_sum_.pdf\"; filename*=UTF-8''r%C3%A9sum%C3%A9.pdf",
            ),
        ],
    )
    async def test_presigned_download_sanitizes_a_download_filename(
        self, mock_s3: AsyncMock, filename, expected
    ):
        """The filename goes into the header only, never into the key."""
        mock_s3.generate_presigned_url.return_value = "https://example.com/url"

        await generate_presigned_download_url(
            "forms/a/b/c", "test-bucket", 60, download_filename=filename
        )

        mock_s3.generate_presigned_url.assert_called_once_with(
            "get_object",
            Params={
                "Bucket": "test-bucket",
                "Key": "forms/a/b/c",
                "ResponseContentDisposition": expected,
            },
            ExpiresIn=60,
        )

    @pytest.mark.anyio
    async def test_presigned_aws_url_uses_regional_sigv4_host(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """AWS presigning must produce the regional host the deployment CSP allows."""
        monkeypatch.setattr(blob_module.config, "TRACECAT__BLOB_STORAGE_ENDPOINT", "")
        monkeypatch.setattr(
            blob_module.config, "TRACECAT__BLOB_STORAGE_PRESIGNED_URL_ENDPOINT", None
        )
        monkeypatch.setenv("AWS_DEFAULT_REGION", "us-west-2")
        monkeypatch.setenv("AWS_REGION", "us-west-2")
        monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
        monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
        monkeypatch.delenv("AWS_SESSION_TOKEN", raising=False)
        monkeypatch.delenv("AWS_PROFILE", raising=False)

        await blob_module.close_storage_client_cache()
        try:
            url = await generate_presigned_upload_url(
                key="attachments/test.txt",
                bucket="example-bucket",
                expiry=60,
            )
        finally:
            await blob_module.close_storage_client_cache()

        parsed = urlparse(url)
        assert parsed.hostname == "example-bucket.s3.us-west-2.amazonaws.com"
        assert "X-Amz-Algorithm=AWS4-HMAC-SHA256" in parsed.query

    @pytest.mark.anyio
    async def test_presigned_upload_checksum_rejects_changed_bytes(
        self,
        minio_bucket: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv(
            "AWS_ACCESS_KEY_ID",
            os.environ.get("AWS_ACCESS_KEY_ID")
            or os.environ.get("MINIO_ROOT_USER")
            or "minio",
        )
        monkeypatch.setenv(
            "AWS_SECRET_ACCESS_KEY",
            os.environ.get("AWS_SECRET_ACCESS_KEY")
            or os.environ.get("MINIO_ROOT_PASSWORD")
            or "password",
        )

        payload = b"bound"
        checksum_sha256 = base64.b64encode(hashlib.sha256(payload).digest()).decode(
            "ascii"
        )
        key = "skill-uploads/test/payload"
        try:
            upload_url = await generate_presigned_upload_url(
                key=key,
                bucket=minio_bucket,
                expiry=60,
                content_type="application/octet-stream",
                checksum_sha256=checksum_sha256,
            )
            headers = {
                "Content-Type": "application/octet-stream",
                "x-amz-checksum-sha256": checksum_sha256,
            }

            async with httpx.AsyncClient(timeout=10) as client:
                changed_response = await client.put(
                    upload_url,
                    content=b"other",
                    headers=headers,
                )
                exact_response = await client.put(
                    upload_url,
                    content=payload,
                    headers=headers,
                )

            assert changed_response.status_code == 400
            assert exact_response.status_code == 200
            assert await download_file(key=key, bucket=minio_bucket) == payload
        finally:
            await blob_module.close_storage_client_cache()

    @pytest.mark.anyio
    @patch("tracecat.storage.blob.config")
    async def test_presigned_url_path_replacement(
        self, mock_config, mock_s3: AsyncMock
    ):
        """Test that presigned URLs correctly include /s3 path prefix."""
        # Setup configuration
        mock_config.TRACECAT__PUBLIC_APP_URL = "http://localhost"
        mock_config.TRACECAT__BLOB_STORAGE_PRESIGNED_URL_EXPIRY = 10
        mock_config.TRACECAT__BLOB_STORAGE_PRESIGNED_URL_ENDPOINT = (
            "http://localhost/s3"
        )
        mock_config.TRACECAT__BLOB_STORAGE_ENDPOINT = "http://minio:9000"

        # Setup mock client

        # Mock the original presigned URL from MinIO
        original_url = "http://minio:9000/tracecat/attachments/test.txt?AWSAccessKeyId=minio&Signature=abc123&Expires=1234567890"
        mock_s3.generate_presigned_url.return_value = original_url

        # Generate presigned URL
        result = await generate_presigned_download_url(
            "attachments/test.txt", "tracecat", 30
        )

        # Verify URL transformation
        expected_url = "http://localhost/s3/tracecat/attachments/test.txt?AWSAccessKeyId=minio&Signature=abc123&Expires=1234567890"
        assert result == expected_url
        assert "/s3/" in result
        assert "localhost" in result

    @pytest.mark.anyio
    @patch("tracecat.storage.blob.config")
    async def test_presigned_url_no_replacement_when_not_minio(
        self, mock_config, mock_s3: AsyncMock
    ):
        """Test that URLs are not modified when not from internal MinIO."""
        # Mock config
        mock_config.TRACECAT__PUBLIC_APP_URL = "http://localhost"
        mock_config.TRACECAT__BLOB_STORAGE_PRESIGNED_URL_EXPIRY = 10
        mock_config.TRACECAT__BLOB_STORAGE_ENDPOINT = "http://minio:9000"
        mock_config.TRACECAT__BLOB_STORAGE_PRESIGNED_URL_ENDPOINT = None

        # Setup mock client

        # URL that doesn't start with http://minio:9000
        original_url = "https://s3.amazonaws.com/bucket/test.txt?AWSAccessKeyId=key&Signature=abc123&Expires=1234567890"
        mock_s3.generate_presigned_url.return_value = original_url

        # Generate presigned URL
        result = await generate_presigned_download_url(
            "attachments/test.txt", "tracecat", 30
        )

        # URL should remain unchanged since it's not from MinIO
        assert result == original_url
        parsed = urlparse(result)
        assert parsed.hostname == "s3.amazonaws.com"

    @pytest.mark.anyio
    @patch("tracecat.storage.blob.config")
    async def test_presigned_url_no_replacement_without_blob_endpoint(
        self, mock_config, mock_s3: AsyncMock
    ):
        """Skip presigned URL rewriting when blob endpoint is unset."""
        mock_config.TRACECAT__BLOB_STORAGE_PRESIGNED_URL_EXPIRY = 10
        mock_config.TRACECAT__BLOB_STORAGE_ENDPOINT = None
        mock_config.TRACECAT__BLOB_STORAGE_PRESIGNED_URL_ENDPOINT = "http://public/s3"

        original_url = "https://s3.amazonaws.com/bucket/test.txt?AWSAccessKeyId=key&Signature=abc123&Expires=1234567890"
        mock_s3.generate_presigned_url.return_value = original_url

        result = await generate_presigned_download_url(
            "attachments/test.txt", "tracecat", 30
        )

        assert result == original_url


class TestRewritePresignedEndpoint:
    """The internal-to-public endpoint swap is a prefix rewrite, not a substring one."""

    @pytest.mark.parametrize(
        ("internal", "public", "url", "expected"),
        [
            pytest.param(
                "http://minio:9000",
                "http://localhost/s3",
                "http://minio:9000/bucket/key.txt?Signature=abc",
                "http://localhost/s3/bucket/key.txt?Signature=abc",
                id="rewrites_matching_prefix",
            ),
            pytest.param(
                "http://minio:9000",
                "http://localhost/s3",
                "https://s3.amazonaws.com/bucket/key.txt?next=http://minio:9000/x",
                "https://s3.amazonaws.com/bucket/key.txt?next=http://minio:9000/x",
                id="ignores_endpoint_outside_prefix",
            ),
            pytest.param(
                None,
                "http://localhost/s3",
                "http://minio:9000/bucket/key.txt",
                "http://minio:9000/bucket/key.txt",
                id="internal_endpoint_unset",
            ),
            pytest.param(
                "http://minio:9000",
                None,
                "http://minio:9000/bucket/key.txt",
                "http://minio:9000/bucket/key.txt",
                id="public_endpoint_unset",
            ),
        ],
    )
    def test_rewrite_presigned_endpoint(
        self,
        monkeypatch: pytest.MonkeyPatch,
        internal: str | None,
        public: str | None,
        url: str,
        expected: str,
    ) -> None:
        monkeypatch.setattr(
            blob_module.config, "TRACECAT__BLOB_STORAGE_ENDPOINT", internal
        )
        monkeypatch.setattr(
            blob_module.config,
            "TRACECAT__BLOB_STORAGE_PRESIGNED_URL_ENDPOINT",
            public,
        )

        assert blob_module._rewrite_presigned_endpoint(url) == expected


class TestEdgeCases:
    """Test edge cases and error conditions."""

    @pytest.mark.anyio
    async def test_open_download_stream_yields_stream_and_length(
        self, mock_s3: AsyncMock
    ):
        """open_download_stream yields a usable body and ContentLength when present."""

        mock_stream = AsyncMock()
        mock_body = AsyncMock()
        mock_body.__aenter__.return_value = mock_stream
        mock_response = {"Body": mock_body, "ContentLength": 123}
        mock_s3.get_object.return_value = mock_response

        async with open_download_stream(key="k", bucket="b") as (stream, length):
            assert stream is mock_body
            assert length == 123

    @pytest.mark.anyio
    async def test_open_download_stream_can_redact_transport_failure(
        self, mock_s3: AsyncMock, storage_logger: MagicMock
    ) -> None:
        """Registry downloads suppress bucket, key, and provider messages."""
        sensitive_bucket = "affected-customer-bucket"
        sensitive_key = "tenant/private/site-packages.squashfs"
        mock_s3.get_object.side_effect = ClientError(
            error_response={
                "Error": {
                    "Code": "AccessDenied",
                    "Message": f"denied {sensitive_bucket}/{sensitive_key}",
                }
            },
            operation_name="get_object",
        )

        with pytest.raises(blob_module.StorageDownloadError) as raised:
            async with open_download_stream(
                key=sensitive_key,
                bucket=sensitive_bucket,
                redact_log_identifiers=True,
            ):
                pass

        assert sensitive_bucket not in str(raised.value)
        assert sensitive_key not in str(raised.value)
        storage_logger.error.assert_called_once_with(
            "Failed to open download stream",
            key="<redacted>",
            bucket="<redacted>",
            error_code="AccessDenied",
            error_type="ClientError",
        )

    @pytest.mark.anyio
    async def test_redacted_download_rejects_provider_prose_as_error_code(
        self, mock_s3: AsyncMock, storage_logger: MagicMock
    ) -> None:
        sensitive_message = "tenant/private/object is forbidden"
        mock_s3.get_object.side_effect = ClientError(
            error_response={"Error": {"Code": sensitive_message}},
            operation_name="get_object",
        )

        with pytest.raises(blob_module.StorageDownloadError) as raised:
            async with open_download_stream(
                key="tenant/private/object",
                bucket="affected-bucket",
                redact_log_identifiers=True,
            ):
                pass

        assert raised.value.error_code is None
        assert sensitive_message not in str(storage_logger.mock_calls)

    @pytest.mark.anyio
    async def test_download_file_to_path_writes_bytes(
        self, tmp_path: Path, monkeypatch
    ):
        """download_file_to_path streams to a file without loading all bytes into memory."""

        class DummyStream:
            def __init__(self, chunks: list[bytes]):
                self._chunks = chunks

            async def iter_chunks(self, *, chunk_size: int):  # noqa: ARG002
                for chunk in self._chunks:
                    yield chunk

        chunks = [b"hello ", b"world"]
        dummy_stream = DummyStream(chunks)

        @asynccontextmanager
        async def _fake_open_download_stream(
            *, key: str, bucket: str, redact_log_identifiers: bool = False
        ):  # noqa: ARG001
            yield dummy_stream, sum(len(c) for c in chunks)

        monkeypatch.setattr(
            "tracecat.storage.blob.open_download_stream",
            _fake_open_download_stream,
        )

        out = tmp_path / "out.bin"
        bytes_written = await download_file_to_path(
            key="k",
            bucket="b",
            output_path=out,
        )

        assert bytes_written == 11
        assert out.read_bytes() == b"hello world"

    @pytest.mark.anyio
    async def test_download_file_to_path_max_bytes_refuses(
        self, tmp_path: Path, monkeypatch
    ):
        """download_file_to_path refuses based on ContentLength max_bytes guardrail."""

        class DummyStream:
            async def iter_chunks(self, *, chunk_size: int):  # noqa: ARG002
                yield b"should-not-write"

        @asynccontextmanager
        async def _fake_open_download_stream(
            *, key: str, bucket: str, redact_log_identifiers: bool = False
        ):  # noqa: ARG001
            yield DummyStream(), 10

        monkeypatch.setattr(
            "tracecat.storage.blob.open_download_stream",
            _fake_open_download_stream,
        )

        out = tmp_path / "out.bin"
        with pytest.raises(ValueError, match="exceeds max_bytes"):
            await download_file_to_path(
                key="k",
                bucket="b",
                output_path=out,
                max_bytes=5,
            )

        assert not out.exists()
        assert not (tmp_path / "out.bin.part").exists()

    @pytest.mark.anyio
    async def test_download_file_to_path_reserves_content_length_before_writing(
        self, tmp_path: Path, monkeypatch
    ):
        """Capacity is reserved before a downloader creates its partial file."""

        class DummyStream:
            async def iter_chunks(self, *, chunk_size: int):  # noqa: ARG002
                yield b"payload"

        @asynccontextmanager
        async def _fake_open_download_stream(
            *, key: str, bucket: str, redact_log_identifiers: bool = False
        ):  # noqa: ARG001
            yield DummyStream(), 7

        monkeypatch.setattr(
            "tracecat.storage.blob.open_download_stream",
            _fake_open_download_stream,
        )

        out = tmp_path / "out.bin"
        reservations: list[int] = []

        async def ensure_capacity(size_bytes: int) -> None:
            assert not (tmp_path / "out.bin.part").exists()
            reservations.append(size_bytes)

        await download_file_to_path(
            key="k",
            bucket="b",
            output_path=out,
            max_bytes=10,
            ensure_capacity=ensure_capacity,
        )

        assert reservations == [7]
        assert out.read_bytes() == b"payload"

    @pytest.mark.anyio
    async def test_download_file_to_path_grows_unknown_length_reservation(
        self, tmp_path: Path, monkeypatch
    ):
        """Unknown-length downloads reserve each chunk above current usage."""

        class DummyStream:
            async def iter_chunks(self, *, chunk_size: int):  # noqa: ARG002
                yield b"abc"
                yield b"defg"

        @asynccontextmanager
        async def _fake_open_download_stream(
            *, key: str, bucket: str, redact_log_identifiers: bool = False
        ):  # noqa: ARG001
            yield DummyStream(), None

        monkeypatch.setattr(
            "tracecat.storage.blob.open_download_stream",
            _fake_open_download_stream,
        )

        out = tmp_path / "out.bin"
        temp_path = tmp_path / "out.bin.part"
        reservations: list[int] = []
        partial_sizes: list[int] = []

        async def ensure_capacity(size_bytes: int) -> None:
            partial_size = temp_path.stat().st_size
            assert 2 + partial_size + size_bytes <= 10
            reservations.append(size_bytes)
            partial_sizes.append(partial_size)

        await download_file_to_path(
            key="k",
            bucket="b",
            output_path=out,
            max_bytes=10,
            ensure_capacity=ensure_capacity,
        )

        assert reservations == [3, 4]
        assert partial_sizes == [0, 3]
        assert out.read_bytes() == b"abcdefg"

    @pytest.mark.anyio
    async def test_download_file_to_path_never_exceeds_reserved_length(
        self, tmp_path: Path, monkeypatch
    ):
        """A body larger than ContentLength is rejected before its excess write."""

        class DummyStream:
            async def iter_chunks(self, *, chunk_size: int):  # noqa: ARG002
                yield b"too-large"

        @asynccontextmanager
        async def _fake_open_download_stream(
            *, key: str, bucket: str, redact_log_identifiers: bool = False
        ):  # noqa: ARG001
            yield DummyStream(), 5

        monkeypatch.setattr(
            "tracecat.storage.blob.open_download_stream",
            _fake_open_download_stream,
        )

        out = tmp_path / "out.bin"
        reservations: list[int] = []

        async def ensure_capacity(size_bytes: int) -> None:
            reservations.append(size_bytes)

        with pytest.raises(ValueError, match="exceeds max_bytes=5"):
            await download_file_to_path(
                key="k",
                bucket="b",
                output_path=out,
                max_bytes=10,
                ensure_capacity=ensure_capacity,
            )

        assert reservations == [5]
        assert not out.exists()
        assert not (tmp_path / "out.bin.part").exists()

    @pytest.mark.anyio
    async def test_download_file_to_path_sha256_mismatch_cleans_partial(
        self, tmp_path: Path, monkeypatch
    ):
        """download_file_to_path removes partial file on SHA-256 mismatch."""

        class DummyStream:
            async def iter_chunks(self, *, chunk_size: int):  # noqa: ARG002
                yield b"hello"

        @asynccontextmanager
        async def _fake_open_download_stream(
            *, key: str, bucket: str, redact_log_identifiers: bool = False
        ):  # noqa: ARG001
            yield DummyStream(), 5

        monkeypatch.setattr(
            "tracecat.storage.blob.open_download_stream",
            _fake_open_download_stream,
        )

        out = tmp_path / "out.bin"
        expected_sha256 = hashlib.sha256(b"hello").hexdigest()
        with pytest.raises(ValueError, match="Integrity check failed"):
            await download_file_to_path(
                key="k",
                bucket="b",
                output_path=out,
                expected_sha256=expected_sha256 + "bad",
            )

        assert not out.exists()
        assert not (tmp_path / "out.bin.part").exists()

    @pytest.mark.anyio
    async def test_download_file_to_path_cancellation_cleans_partial(
        self, tmp_path: Path, monkeypatch
    ):
        """Cancellation removes the downloader-owned partial file."""
        download_blocked = asyncio.Event()

        class DummyStream:
            async def iter_chunks(self, *, chunk_size: int):  # noqa: ARG002
                yield b"partial"
                download_blocked.set()
                await asyncio.Event().wait()

        @asynccontextmanager
        async def _fake_open_download_stream(
            *, key: str, bucket: str, redact_log_identifiers: bool = False
        ):  # noqa: ARG001
            yield DummyStream(), None

        monkeypatch.setattr(
            "tracecat.storage.blob.open_download_stream",
            _fake_open_download_stream,
        )

        out = tmp_path / "out.bin"
        download = asyncio.create_task(
            download_file_to_path(
                key="k",
                bucket="b",
                output_path=out,
            )
        )
        await download_blocked.wait()
        assert (tmp_path / "out.bin.part").exists()

        download.cancel()
        with pytest.raises(asyncio.CancelledError):
            await download

        assert not out.exists()
        assert not (tmp_path / "out.bin.part").exists()

    @pytest.mark.anyio
    async def test_download_file_to_path_rejoins_cancelled_write_before_cleanup(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """Cancellation cannot unlink a partial file while its write is running."""
        write_started = threading.Event()
        finish_write = threading.Event()
        file_closed = threading.Event()

        class DummyStream:
            async def iter_chunks(self, *, chunk_size: int):  # noqa: ARG002
                yield b"partial"

        @asynccontextmanager
        async def _fake_open_download_stream(
            *, key: str, bucket: str, redact_log_identifiers: bool = False
        ):  # noqa: ARG001
            yield DummyStream(), 7

        out = tmp_path / "out.bin"
        partial_path = tmp_path / "out.bin.part"
        original_open = Path.open
        original_unlink = Path.unlink

        class BlockingFile:
            def __init__(self, path: Path, *args, **kwargs) -> None:
                self._file = original_open(path, *args, **kwargs)

            def __enter__(self):
                return self

            def __exit__(self, *args) -> None:
                self._file.close()
                file_closed.set()

            def write(self, chunk: bytes) -> int:
                write_started.set()
                assert finish_write.wait(timeout=5)
                return self._file.write(chunk)

        def blocking_open(path: Path, *args, **kwargs):
            if path == partial_path:
                return BlockingFile(path, *args, **kwargs)
            return original_open(path, *args, **kwargs)

        def checked_unlink(path: Path, *args, **kwargs):
            if path == partial_path:
                assert file_closed.is_set()
            return original_unlink(path, *args, **kwargs)

        monkeypatch.setattr(
            "tracecat.storage.blob.open_download_stream",
            _fake_open_download_stream,
        )
        monkeypatch.setattr(Path, "open", blocking_open)
        monkeypatch.setattr(Path, "unlink", checked_unlink)

        download = asyncio.create_task(
            download_file_to_path(key="k", bucket="b", output_path=out)
        )
        assert await asyncio.to_thread(write_started.wait, 2)
        download.cancel()
        await asyncio.sleep(0)
        download.cancel()
        await asyncio.sleep(0)
        assert not download.done()

        finish_write.set()
        with pytest.raises(asyncio.CancelledError):
            await download

        assert file_closed.is_set()
        assert not out.exists()
        assert not partial_path.exists()

    @pytest.mark.anyio
    async def test_download_file_to_path_defers_failed_partial_cleanup(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """A failed partial unlink remains discoverable for a later retry."""

        class DummyStream:
            async def iter_chunks(self, *, chunk_size: int):  # noqa: ARG002
                yield b"partial"
                raise RuntimeError("stream failed")

        @asynccontextmanager
        async def _fake_open_download_stream(
            *, key: str, bucket: str, redact_log_identifiers: bool
        ):  # noqa: ARG001
            assert redact_log_identifiers is True
            yield DummyStream(), None

        monkeypatch.setattr(
            "tracecat.storage.blob.open_download_stream",
            _fake_open_download_stream,
        )
        out = tmp_path / "out.bin"
        partial_path = tmp_path / "out.bin.part"
        deferred: list[Path] = []

        with patch.object(
            Path,
            "unlink",
            side_effect=PermissionError("cleanup denied"),
        ):
            with pytest.raises(RuntimeError, match="stream failed"):
                await download_file_to_path(
                    key="tenant/private/object",
                    bucket="affected-bucket",
                    output_path=out,
                    defer_cleanup=deferred.append,
                    redact_log_identifiers=True,
                )

        assert deferred == [partial_path]
        assert partial_path.is_file()


class TestBucketLifecycle:
    """Test bucket lifecycle configuration operations."""

    @pytest.mark.anyio
    async def test_configure_bucket_lifecycle_sets_expiration(self, mock_s3: AsyncMock):
        """Test configure_bucket_lifecycle sets lifecycle rules correctly."""

        await configure_bucket_lifecycle(
            bucket="test-bucket",
            expiration_days=30,
            rule_id="test-expiration",
        )

        mock_s3.put_bucket_lifecycle_configuration.assert_called_once()
        call_kwargs = mock_s3.put_bucket_lifecycle_configuration.call_args.kwargs
        assert call_kwargs["Bucket"] == "test-bucket"
        lifecycle_config = call_kwargs["LifecycleConfiguration"]
        assert len(lifecycle_config["Rules"]) == 1
        rule = lifecycle_config["Rules"][0]
        assert rule["ID"] == "test-expiration"
        assert rule["Status"] == "Enabled"
        assert rule["Expiration"]["Days"] == 30

    @pytest.mark.anyio
    async def test_configure_bucket_lifecycle_removes_rule_when_zero_days(
        self, mock_s3: AsyncMock
    ):
        """Test configure_bucket_lifecycle removes existing rule when expiration_days is 0."""

        await configure_bucket_lifecycle(bucket="test-bucket", expiration_days=0)

        mock_s3.delete_bucket_lifecycle.assert_called_once_with(Bucket="test-bucket")
        mock_s3.put_bucket_lifecycle_configuration.assert_not_called()

    @pytest.mark.anyio
    async def test_configure_bucket_lifecycle_handles_no_existing_rule(
        self, mock_s3: AsyncMock
    ):
        """Test configure_bucket_lifecycle handles case when no lifecycle rule exists to remove."""

        mock_s3.delete_bucket_lifecycle.side_effect = ClientError(
            error_response={"Error": {"Code": "NoSuchLifecycleConfiguration"}},
            operation_name="delete_bucket_lifecycle",
        )

        # Should not raise - gracefully handles missing lifecycle
        await configure_bucket_lifecycle(bucket="test-bucket", expiration_days=0)

        mock_s3.delete_bucket_lifecycle.assert_called_once()
