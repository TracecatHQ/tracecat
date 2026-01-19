"""Tests for the storage module."""

import hashlib
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock, patch
from urllib.parse import urlparse

import pytest
from botocore.exceptions import ClientError

from tracecat.storage import blob as blob_module
from tracecat.storage.blob import (
    configure_bucket_lifecycle,
    delete_file,
    download_file,
    download_file_to_path,
    ensure_bucket_exists,
    generate_presigned_download_url,
    generate_presigned_upload_url,
    get_bucket_lifecycle,
    get_storage_client,
    open_download_stream,
    upload_file,
)


class TestS3Operations:
    """Test S3/MinIO operations."""

    @pytest.fixture
    def mock_s3_client(self):
        """Create a mock S3 client."""
        mock_client = AsyncMock()
        return mock_client

    @pytest.mark.anyio
    @patch("tracecat.storage.blob.get_storage_client")
    async def test_ensure_bucket_exists_existing(self, mock_get_client):
        """Test ensure_bucket_exists when bucket already exists."""
        mock_client = AsyncMock()
        mock_get_client.return_value.__aenter__.return_value = mock_client

        await ensure_bucket_exists("test-bucket")
        mock_client.head_bucket.assert_called_once_with(Bucket="test-bucket")

    @pytest.mark.anyio
    @patch("tracecat.storage.blob.get_storage_client")
    async def test_ensure_bucket_exists_create_new(self, mock_get_client):
        """Test ensure_bucket_exists when bucket needs to be created."""
        mock_client = AsyncMock()
        mock_get_client.return_value.__aenter__.return_value = mock_client

        # Simulate bucket not found
        mock_client.head_bucket.side_effect = ClientError(
            error_response={"Error": {"Code": "404"}}, operation_name="head_bucket"
        )

        await ensure_bucket_exists("test-bucket")
        mock_client.head_bucket.assert_called_once_with(Bucket="test-bucket")
        mock_client.create_bucket.assert_called_once_with(Bucket="test-bucket")

    @pytest.mark.anyio
    async def test_get_storage_client_minio_uses_endpoint_and_env(self, monkeypatch):
        """get_storage_client for MinIO uses endpoint and AWS env vars"""

        monkeypatch.setattr(
            blob_module.config,
            "TRACECAT__BLOB_STORAGE_ENDPOINT",
            "http://localhost:9002",
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

        with patch("tracecat.storage.blob.aioboto3.Session") as mock_session_cls:
            mock_session = mock_session_cls.return_value
            mock_client = AsyncMock()
            mock_session.client.return_value.__aenter__.return_value = mock_client

            async with get_storage_client() as client:
                assert client is mock_client
            mock_session.client.assert_called_once_with("s3")

    @pytest.mark.anyio
    @patch("tracecat.storage.blob.get_storage_client")
    async def test_upload_file(self, mock_get_client):
        """Test file upload."""
        mock_client = AsyncMock()
        mock_get_client.return_value.__aenter__.return_value = mock_client

        content = b"test content"
        key = "test/file.txt"
        content_type = "text/plain"

        await upload_file(content, key, "test-bucket", content_type)

        mock_client.put_object.assert_called_once_with(
            Bucket="test-bucket", Key=key, Body=content, ContentType=content_type
        )

    @pytest.mark.anyio
    @patch("tracecat.storage.blob.get_storage_client")
    async def test_upload_file_without_content_type(self, mock_get_client):
        """When content_type is None, upload omits ContentType param."""
        mock_client = AsyncMock()
        mock_get_client.return_value.__aenter__.return_value = mock_client

        content = b"bytes"
        key = "no/ctype.bin"

        await upload_file(content, key, "bucket")
        # Ensure ContentType not passed
        mock_client.put_object.assert_called_once()
        kwargs = mock_client.put_object.call_args.kwargs
        assert kwargs == {"Bucket": "bucket", "Key": key, "Body": content}

    @pytest.mark.anyio
    @patch("tracecat.storage.blob.get_storage_client")
    async def test_download_file(self, mock_get_client):
        """Test file download."""
        mock_client = AsyncMock()
        mock_get_client.return_value.__aenter__.return_value = mock_client

        expected_content = b"test content"
        mock_stream = AsyncMock()
        mock_stream.read.return_value = expected_content
        mock_body = AsyncMock()
        mock_body.__aenter__.return_value = mock_stream
        mock_response = {"Body": mock_body}
        mock_client.get_object.return_value = mock_response

        result = await download_file("test/file.txt", "test-bucket")

        assert result == expected_content
        mock_client.get_object.assert_called_once_with(
            Bucket="test-bucket", Key="test/file.txt"
        )

    @pytest.mark.anyio
    @patch("tracecat.storage.blob.get_storage_client")
    async def test_download_file_not_found(self, mock_get_client):
        """Test file download when file doesn't exist."""
        mock_client = AsyncMock()
        mock_get_client.return_value.__aenter__.return_value = mock_client

        mock_client.get_object.side_effect = ClientError(
            error_response={"Error": {"Code": "NoSuchKey"}}, operation_name="get_object"
        )

        with pytest.raises(FileNotFoundError):
            await download_file("nonexistent.txt", "test-bucket")

    @pytest.mark.anyio
    @patch("tracecat.storage.blob.get_storage_client")
    async def test_delete_file(self, mock_get_client):
        """Test file deletion."""
        mock_client = AsyncMock()
        mock_get_client.return_value.__aenter__.return_value = mock_client

        await delete_file("test/file.txt", "test-bucket")

        mock_client.delete_object.assert_called_once_with(
            Bucket="test-bucket", Key="test/file.txt"
        )

    @pytest.mark.anyio
    @patch("tracecat.storage.blob.get_storage_client")
    async def test_delete_file_error_propagates(self, mock_get_client):
        """If delete_object fails, propagate ClientError."""
        mock_client = AsyncMock()
        mock_get_client.return_value.__aenter__.return_value = mock_client
        mock_client.delete_object.side_effect = ClientError(
            error_response={"Error": {"Code": "AccessDenied"}},
            operation_name="delete_object",
        )

        with pytest.raises(ClientError):
            await delete_file("k", "b")

    @pytest.mark.anyio
    @patch("tracecat.storage.blob.get_storage_client")
    @patch("tracecat.storage.blob.config")
    async def test_generate_presigned_download_url(self, mock_config, mock_get_client):
        """Test presigned download URL generation."""
        # Mock config
        mock_config.TRACECAT__BLOB_STORAGE_PRESIGNED_URL_EXPIRY = 10
        mock_config.TRACECAT__PUBLIC_APP_URL = "http://localhost"
        mock_config.TRACECAT__BLOB_STORAGE_ENDPOINT = "http://minio:9000"
        mock_config.TRACECAT__BLOB_STORAGE_PRESIGNED_URL_ENDPOINT = None

        mock_client = AsyncMock()
        mock_get_client.return_value.__aenter__.return_value = mock_client

        expected_url = "https://example.com/presigned-url"
        mock_client.generate_presigned_url.return_value = expected_url

        result = await generate_presigned_download_url(
            "test/file.txt", "test-bucket", 3600
        )

        assert result == expected_url
        mock_client.generate_presigned_url.assert_called_once_with(
            "get_object",
            Params={
                "Bucket": "test-bucket",
                "Key": "test/file.txt",
                "ResponseContentDisposition": 'attachment; filename="file.txt"',
            },
            ExpiresIn=3600,
        )

    @pytest.mark.anyio
    @patch("tracecat.storage.blob.get_storage_client")
    @patch("tracecat.storage.blob.config")
    async def test_generate_presigned_download_url_with_preview(
        self, mock_config, mock_get_client
    ):
        """Test presigned download URL generation with preview mode."""
        # Mock config
        mock_config.TRACECAT__BLOB_STORAGE_PRESIGNED_URL_EXPIRY = 10
        mock_config.TRACECAT__PUBLIC_APP_URL = "http://localhost"
        mock_config.TRACECAT__BLOB_STORAGE_ENDPOINT = "http://minio:9000"
        mock_config.TRACECAT__BLOB_STORAGE_PRESIGNED_URL_ENDPOINT = None

        mock_client = AsyncMock()
        mock_get_client.return_value.__aenter__.return_value = mock_client

        expected_url = "https://example.com/presigned-url"
        mock_client.generate_presigned_url.return_value = expected_url

        # Test with preview mode disabled (force_download=False)
        result = await generate_presigned_download_url(
            "test/image.png", "test-bucket", 3600, force_download=False
        )

        assert result == expected_url
        mock_client.generate_presigned_url.assert_called_once_with(
            "get_object",
            Params={
                "Bucket": "test-bucket",
                "Key": "test/image.png",
                # No ResponseContentDisposition when force_download=False
            },
            ExpiresIn=3600,
        )

    @pytest.mark.anyio
    @patch("tracecat.storage.blob.get_storage_client")
    @patch("tracecat.storage.blob.config")
    async def test_generate_presigned_download_url_with_content_type_override(
        self, mock_config, mock_get_client
    ):
        """Test presigned download URL generation with content type override."""
        # Mock config
        mock_config.TRACECAT__BLOB_STORAGE_PRESIGNED_URL_EXPIRY = 10
        mock_config.TRACECAT__PUBLIC_APP_URL = "http://localhost"
        mock_config.TRACECAT__BLOB_STORAGE_ENDPOINT = "http://minio:9000"
        mock_config.TRACECAT__BLOB_STORAGE_PRESIGNED_URL_ENDPOINT = None

        mock_client = AsyncMock()
        mock_get_client.return_value.__aenter__.return_value = mock_client

        expected_url = "https://example.com/presigned-url"
        mock_client.generate_presigned_url.return_value = expected_url

        # Test with content type override
        result = await generate_presigned_download_url(
            "test/file.bin",
            "test-bucket",
            3600,
            override_content_type="application/octet-stream",
        )

        assert result == expected_url
        mock_client.generate_presigned_url.assert_called_once_with(
            "get_object",
            Params={
                "Bucket": "test-bucket",
                "Key": "test/file.bin",
                "ResponseContentDisposition": 'attachment; filename="file.bin"',
                "ResponseContentType": "application/octet-stream",
            },
            ExpiresIn=3600,
        )

    @pytest.mark.anyio
    @patch("tracecat.storage.blob.get_storage_client")
    async def test_generate_presigned_upload_url(self, mock_get_client):
        """Test presigned upload URL generation."""
        mock_client = AsyncMock()
        mock_get_client.return_value.__aenter__.return_value = mock_client

        expected_url = "https://example.com/presigned-upload-url"
        mock_client.generate_presigned_url.return_value = expected_url

        result = await generate_presigned_upload_url(
            "test/file.txt", "test-bucket", 3600, "text/plain"
        )

        assert result == expected_url
        mock_client.generate_presigned_url.assert_called_once_with(
            "put_object",
            Params={
                "Bucket": "test-bucket",
                "Key": "test/file.txt",
                "ContentType": "text/plain",
            },
            ExpiresIn=3600,
        )

    @pytest.mark.anyio
    @patch("tracecat.storage.blob.get_storage_client")
    @patch("tracecat.storage.blob.config")
    async def test_presigned_url_path_replacement(self, mock_config, mock_get_client):
        """Test that presigned URLs correctly include /s3 path prefix."""
        # Setup configuration
        mock_config.TRACECAT__PUBLIC_APP_URL = "http://localhost"
        mock_config.TRACECAT__BLOB_STORAGE_PRESIGNED_URL_EXPIRY = 10
        mock_config.TRACECAT__BLOB_STORAGE_PRESIGNED_URL_ENDPOINT = (
            "http://localhost/s3"
        )
        mock_config.TRACECAT__BLOB_STORAGE_ENDPOINT = "http://minio:9000"

        # Setup mock client
        mock_client = AsyncMock()
        mock_get_client.return_value.__aenter__.return_value = mock_client

        # Mock the original presigned URL from MinIO
        original_url = "http://minio:9000/tracecat/attachments/test.txt?AWSAccessKeyId=minio&Signature=abc123&Expires=1234567890"
        mock_client.generate_presigned_url.return_value = original_url

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
    @patch("tracecat.storage.blob.get_storage_client")
    @patch("tracecat.storage.blob.config")
    async def test_presigned_url_no_replacement_when_not_minio(
        self, mock_config, mock_get_client
    ):
        """Test that URLs are not modified when not from internal MinIO."""
        # Mock config
        mock_config.TRACECAT__PUBLIC_APP_URL = "http://localhost"
        mock_config.TRACECAT__BLOB_STORAGE_PRESIGNED_URL_EXPIRY = 10
        mock_config.TRACECAT__BLOB_STORAGE_ENDPOINT = "http://minio:9000"
        mock_config.TRACECAT__BLOB_STORAGE_PRESIGNED_URL_ENDPOINT = None

        # Setup mock client
        mock_client = AsyncMock()
        mock_get_client.return_value.__aenter__.return_value = mock_client

        # URL that doesn't start with http://minio:9000
        original_url = "https://s3.amazonaws.com/bucket/test.txt?AWSAccessKeyId=key&Signature=abc123&Expires=1234567890"
        mock_client.generate_presigned_url.return_value = original_url

        # Generate presigned URL
        result = await generate_presigned_download_url(
            "attachments/test.txt", "tracecat", 30
        )

        # URL should remain unchanged since it's not from MinIO
        assert result == original_url
        parsed = urlparse(result)
        assert parsed.hostname == "s3.amazonaws.com"

    @pytest.mark.anyio
    @patch("tracecat.storage.blob.get_storage_client")
    @patch("tracecat.storage.blob.config")
    async def test_presigned_url_no_replacement_without_blob_endpoint(
        self, mock_config, mock_get_client
    ):
        """Skip presigned URL rewriting when blob endpoint is unset."""
        mock_config.TRACECAT__BLOB_STORAGE_PRESIGNED_URL_EXPIRY = 10
        mock_config.TRACECAT__BLOB_STORAGE_ENDPOINT = None
        mock_config.TRACECAT__BLOB_STORAGE_PRESIGNED_URL_ENDPOINT = "http://public/s3"

        mock_client = AsyncMock()
        mock_get_client.return_value.__aenter__.return_value = mock_client

        original_url = "https://s3.amazonaws.com/bucket/test.txt?AWSAccessKeyId=key&Signature=abc123&Expires=1234567890"
        mock_client.generate_presigned_url.return_value = original_url

        result = await generate_presigned_download_url(
            "attachments/test.txt", "tracecat", 30
        )

        assert result == original_url

    @pytest.mark.anyio
    @patch("tracecat.storage.blob.get_storage_client")
    async def test_presigned_url_expiry_validation(self, mock_get_client):
        """Test that presigned URLs have appropriate expiry times."""
        mock_client = AsyncMock()
        mock_get_client.return_value.__aenter__.return_value = mock_client

        mock_client.generate_presigned_url.return_value = (
            "http://test.com/file?Expires=123"
        )

        # Test with various expiry times
        short_expiry = 30  # 30 seconds - appropriate for immediate download
        await generate_presigned_download_url("test.txt", "bucket", short_expiry)

        # Verify the expiry was passed correctly with security headers
        mock_client.generate_presigned_url.assert_called_with(
            "get_object",
            Params={
                "Bucket": "bucket",
                "Key": "test.txt",
                "ResponseContentDisposition": 'attachment; filename="test.txt"',
            },
            ExpiresIn=short_expiry,
        )


class TestEdgeCases:
    """Test edge cases and error conditions."""

    @patch("tracecat.storage.blob.logger")
    @pytest.mark.anyio
    @patch("tracecat.storage.blob.get_storage_client")
    async def test_logging_on_errors(self, mock_get_client, mock_logger):
        """Test that errors are properly logged."""
        mock_client = AsyncMock()
        mock_get_client.return_value.__aenter__.return_value = mock_client

        # Simulate S3 error
        mock_client.put_object.side_effect = ClientError(
            error_response={"Error": {"Code": "AccessDenied"}},
            operation_name="put_object",
        )

        # This should raise and log
        with pytest.raises(ClientError):
            await upload_file(b"content", "key", "test-bucket")

        # Check that error was logged
        mock_logger.error.assert_called()

    @pytest.mark.anyio
    @patch("tracecat.storage.blob.get_storage_client")
    async def test_storage_error_handling_security(self, mock_get_client):
        """Test that storage errors don't leak sensitive information."""
        mock_client = AsyncMock()
        mock_get_client.return_value.__aenter__.return_value = mock_client

        # Simulate various S3 errors
        mock_client.get_object.side_effect = ClientError(
            error_response={
                "Error": {
                    "Code": "AccessDenied",
                    "Message": "Access denied to sensitive-bucket/secret-path/",
                }
            },
            operation_name="get_object",
        )

        # Error should be raised but internal paths should not be exposed
        with pytest.raises(ClientError):
            await download_file("test-file.txt", "test-bucket")

    @pytest.mark.anyio
    @patch("tracecat.storage.blob.get_storage_client")
    async def test_ensure_bucket_exists_head_error_propagates(self, mock_get_client):
        """Non-404 head_bucket errors bubble up."""
        mock_client = AsyncMock()
        mock_get_client.return_value.__aenter__.return_value = mock_client

        mock_client.head_bucket.side_effect = ClientError(
            error_response={"Error": {"Code": "500"}}, operation_name="head_bucket"
        )

        with pytest.raises(ClientError):
            await ensure_bucket_exists("bucket")

    @pytest.mark.anyio
    @patch("tracecat.storage.blob.get_storage_client")
    async def test_open_download_stream_yields_stream_and_length(self, mock_get_client):
        """open_download_stream yields a usable body and ContentLength when present."""
        mock_client = AsyncMock()
        mock_get_client.return_value.__aenter__.return_value = mock_client

        mock_stream = AsyncMock()
        mock_body = AsyncMock()
        mock_body.__aenter__.return_value = mock_stream
        mock_response = {"Body": mock_body, "ContentLength": 123}
        mock_client.get_object.return_value = mock_response

        async with open_download_stream(key="k", bucket="b") as (stream, length):
            assert stream is mock_stream
            assert length == 123

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
        async def _fake_open_download_stream(*, key: str, bucket: str):  # noqa: ARG001
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
        async def _fake_open_download_stream(*, key: str, bucket: str):  # noqa: ARG001
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
    async def test_download_file_to_path_sha256_mismatch_cleans_partial(
        self, tmp_path: Path, monkeypatch
    ):
        """download_file_to_path removes partial file on SHA-256 mismatch."""

        class DummyStream:
            async def iter_chunks(self, *, chunk_size: int):  # noqa: ARG002
                yield b"hello"

        @asynccontextmanager
        async def _fake_open_download_stream(*, key: str, bucket: str):  # noqa: ARG001
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
    @patch("tracecat.storage.blob.get_storage_client")
    async def test_ensure_bucket_exists_create_error_propagates(self, mock_get_client):
        """Create-bucket failure after 404 bubbles up."""
        mock_client = AsyncMock()
        mock_get_client.return_value.__aenter__.return_value = mock_client

        # Simulate 404 then create failure
        mock_client.head_bucket.side_effect = ClientError(
            error_response={"Error": {"Code": "404"}}, operation_name="head_bucket"
        )
        mock_client.create_bucket.side_effect = ClientError(
            error_response={"Error": {"Code": "BucketAlreadyExists"}},
            operation_name="create_bucket",
        )

        with pytest.raises(ClientError):
            await ensure_bucket_exists("bucket")


class TestBucketLifecycle:
    """Test bucket lifecycle configuration operations."""

    @pytest.mark.anyio
    @patch("tracecat.storage.blob.get_storage_client")
    async def test_get_bucket_lifecycle_returns_config(self, mock_get_client):
        """Test get_bucket_lifecycle returns config when it exists."""
        mock_client = AsyncMock()
        mock_get_client.return_value.__aenter__.return_value = mock_client

        expected_config = {
            "Rules": [
                {
                    "ID": "test-rule",
                    "Status": "Enabled",
                    "Filter": {"Prefix": ""},
                    "Expiration": {"Days": 30},
                }
            ]
        }
        mock_client.get_bucket_lifecycle_configuration.return_value = expected_config

        result = await get_bucket_lifecycle("test-bucket")

        assert result == expected_config
        mock_client.get_bucket_lifecycle_configuration.assert_called_once_with(
            Bucket="test-bucket"
        )

    @pytest.mark.anyio
    @patch("tracecat.storage.blob.get_storage_client")
    async def test_get_bucket_lifecycle_returns_none_when_not_configured(
        self, mock_get_client
    ):
        """Test get_bucket_lifecycle returns None when no lifecycle exists."""
        mock_client = AsyncMock()
        mock_get_client.return_value.__aenter__.return_value = mock_client

        mock_client.get_bucket_lifecycle_configuration.side_effect = ClientError(
            error_response={"Error": {"Code": "NoSuchLifecycleConfiguration"}},
            operation_name="get_bucket_lifecycle_configuration",
        )

        result = await get_bucket_lifecycle("test-bucket")

        assert result is None

    @pytest.mark.anyio
    @patch("tracecat.storage.blob.get_storage_client")
    async def test_get_bucket_lifecycle_propagates_other_errors(self, mock_get_client):
        """Test get_bucket_lifecycle propagates non-lifecycle errors."""
        mock_client = AsyncMock()
        mock_get_client.return_value.__aenter__.return_value = mock_client

        mock_client.get_bucket_lifecycle_configuration.side_effect = ClientError(
            error_response={"Error": {"Code": "AccessDenied"}},
            operation_name="get_bucket_lifecycle_configuration",
        )

        with pytest.raises(ClientError):
            await get_bucket_lifecycle("test-bucket")

    @pytest.mark.anyio
    @patch("tracecat.storage.blob.get_storage_client")
    async def test_configure_bucket_lifecycle_sets_expiration(self, mock_get_client):
        """Test configure_bucket_lifecycle sets lifecycle rules correctly."""
        mock_client = AsyncMock()
        mock_get_client.return_value.__aenter__.return_value = mock_client

        await configure_bucket_lifecycle(
            bucket="test-bucket",
            expiration_days=30,
            rule_id="test-expiration",
        )

        mock_client.put_bucket_lifecycle_configuration.assert_called_once()
        call_kwargs = mock_client.put_bucket_lifecycle_configuration.call_args.kwargs
        assert call_kwargs["Bucket"] == "test-bucket"
        lifecycle_config = call_kwargs["LifecycleConfiguration"]
        assert len(lifecycle_config["Rules"]) == 1
        rule = lifecycle_config["Rules"][0]
        assert rule["ID"] == "test-expiration"
        assert rule["Status"] == "Enabled"
        assert rule["Expiration"]["Days"] == 30

    @pytest.mark.anyio
    @patch("tracecat.storage.blob.get_storage_client")
    async def test_configure_bucket_lifecycle_uses_default_rule_id(
        self, mock_get_client
    ):
        """Test configure_bucket_lifecycle uses default rule ID."""
        mock_client = AsyncMock()
        mock_get_client.return_value.__aenter__.return_value = mock_client

        await configure_bucket_lifecycle(bucket="test-bucket", expiration_days=7)

        call_kwargs = mock_client.put_bucket_lifecycle_configuration.call_args.kwargs
        rule = call_kwargs["LifecycleConfiguration"]["Rules"][0]
        assert rule["ID"] == "workflow-artifact-expiration"

    @pytest.mark.anyio
    @patch("tracecat.storage.blob.get_storage_client")
    async def test_configure_bucket_lifecycle_removes_rule_when_zero_days(
        self, mock_get_client
    ):
        """Test configure_bucket_lifecycle removes existing rule when expiration_days is 0."""
        mock_client = AsyncMock()
        mock_get_client.return_value.__aenter__.return_value = mock_client

        await configure_bucket_lifecycle(bucket="test-bucket", expiration_days=0)

        mock_client.delete_bucket_lifecycle.assert_called_once_with(
            Bucket="test-bucket"
        )
        mock_client.put_bucket_lifecycle_configuration.assert_not_called()

    @pytest.mark.anyio
    @patch("tracecat.storage.blob.get_storage_client")
    async def test_configure_bucket_lifecycle_removes_rule_when_negative_days(
        self, mock_get_client
    ):
        """Test configure_bucket_lifecycle removes existing rule when expiration_days is negative."""
        mock_client = AsyncMock()
        mock_get_client.return_value.__aenter__.return_value = mock_client

        await configure_bucket_lifecycle(bucket="test-bucket", expiration_days=-1)

        mock_client.delete_bucket_lifecycle.assert_called_once_with(
            Bucket="test-bucket"
        )
        mock_client.put_bucket_lifecycle_configuration.assert_not_called()

    @pytest.mark.anyio
    @patch("tracecat.storage.blob.get_storage_client")
    async def test_configure_bucket_lifecycle_handles_no_existing_rule(
        self, mock_get_client
    ):
        """Test configure_bucket_lifecycle handles case when no lifecycle rule exists to remove."""
        mock_client = AsyncMock()
        mock_get_client.return_value.__aenter__.return_value = mock_client

        mock_client.delete_bucket_lifecycle.side_effect = ClientError(
            error_response={"Error": {"Code": "NoSuchLifecycleConfiguration"}},
            operation_name="delete_bucket_lifecycle",
        )

        # Should not raise - gracefully handles missing lifecycle
        await configure_bucket_lifecycle(bucket="test-bucket", expiration_days=0)

        mock_client.delete_bucket_lifecycle.assert_called_once()

    @pytest.mark.anyio
    @patch("tracecat.storage.blob.get_storage_client")
    async def test_configure_bucket_lifecycle_delete_error_propagates(
        self, mock_get_client
    ):
        """Test configure_bucket_lifecycle propagates errors during deletion."""
        mock_client = AsyncMock()
        mock_get_client.return_value.__aenter__.return_value = mock_client

        mock_client.delete_bucket_lifecycle.side_effect = ClientError(
            error_response={"Error": {"Code": "AccessDenied"}},
            operation_name="delete_bucket_lifecycle",
        )

        with pytest.raises(ClientError):
            await configure_bucket_lifecycle(bucket="test-bucket", expiration_days=0)

    @pytest.mark.anyio
    @patch("tracecat.storage.blob.get_storage_client")
    async def test_configure_bucket_lifecycle_error_propagates(self, mock_get_client):
        """Test configure_bucket_lifecycle propagates errors."""
        mock_client = AsyncMock()
        mock_get_client.return_value.__aenter__.return_value = mock_client

        mock_client.put_bucket_lifecycle_configuration.side_effect = ClientError(
            error_response={"Error": {"Code": "AccessDenied"}},
            operation_name="put_bucket_lifecycle_configuration",
        )

        with pytest.raises(ClientError):
            await configure_bucket_lifecycle(bucket="test-bucket", expiration_days=30)
