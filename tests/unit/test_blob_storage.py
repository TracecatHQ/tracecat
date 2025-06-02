"""Test suite for blob storage functionality with MinIO from docker-compose.dev.yml."""

import uuid
from unittest.mock import patch

import pytest
from botocore.exceptions import NoCredentialsError

from tracecat import config, storage


@pytest.fixture(scope="session")
def minio_config():
    """Configuration for connecting to the MinIO instance from docker-compose.dev.yml."""
    return {
        "protocol": "minio",
        "endpoint": "http://localhost:9000",
        "bucket": "test-tracecat",
        "user": "minioadmin",  # Default MinIO credentials
        "password": "miniopassword",  # Updated to match user's environment
        "presigned_url_expiry": 300,
    }


@pytest.fixture(autouse=True)
def setup_blob_storage_config(monkeypatch, minio_config):
    """Configure blob storage environment variables for testing."""
    # Set environment variables
    monkeypatch.setenv("TRACECAT__BLOB_STORAGE_PROTOCOL", minio_config["protocol"])
    monkeypatch.setenv("TRACECAT__BLOB_STORAGE_BUCKET", minio_config["bucket"])
    monkeypatch.setenv("TRACECAT__BLOB_STORAGE_ENDPOINT", minio_config["endpoint"])
    monkeypatch.setenv(
        "TRACECAT__BLOB_STORAGE_PRESIGNED_URL_EXPIRY",
        str(minio_config["presigned_url_expiry"]),
    )
    monkeypatch.setenv("MINIO_ROOT_USER", minio_config["user"])
    monkeypatch.setenv("MINIO_ROOT_PASSWORD", minio_config["password"])

    # Update config module attributes
    monkeypatch.setattr(
        config, "TRACECAT__BLOB_STORAGE_PROTOCOL", minio_config["protocol"]
    )
    monkeypatch.setattr(config, "TRACECAT__BLOB_STORAGE_BUCKET", minio_config["bucket"])
    monkeypatch.setattr(
        config, "TRACECAT__BLOB_STORAGE_ENDPOINT", minio_config["endpoint"]
    )
    monkeypatch.setattr(
        config,
        "TRACECAT__BLOB_STORAGE_PRESIGNED_URL_EXPIRY",
        minio_config["presigned_url_expiry"],
    )


@pytest.fixture
async def test_bucket(minio_config):
    """Ensure test bucket exists and clean it up after tests."""
    bucket_name = minio_config["bucket"]

    try:
        # Ensure bucket exists
        await storage.ensure_bucket_exists(bucket_name)
        yield bucket_name
    finally:
        # Clean up test files
        try:
            async with storage.get_storage_client() as s3_client:
                # List all objects with test prefix
                response = await s3_client.list_objects_v2(
                    Bucket=bucket_name, Prefix="test/"
                )

                if "Contents" in response:
                    # Delete all test objects
                    for obj in response["Contents"]:
                        if "Key" in obj:
                            await s3_client.delete_object(
                                Bucket=bucket_name, Key=obj["Key"]
                            )
        except Exception:
            # Ignore cleanup errors - MinIO might not be running
            pass


class TestBlobStorageOperations:
    """Test suite for basic blob storage operations."""

    @pytest.mark.anyio
    async def test_ensure_bucket_exists(self, minio_config):
        """Test bucket creation and existence check."""
        bucket_name = minio_config["bucket"]

        # Should not raise an exception
        await storage.ensure_bucket_exists(bucket_name)

        # Should be idempotent
        await storage.ensure_bucket_exists(bucket_name)

    @pytest.mark.anyio
    async def test_upload_and_download_file(self, test_bucket):
        """Test file upload and download operations."""
        test_content = b"Hello, World! This is a test file."
        test_key = f"test/{uuid.uuid4()}/hello.txt"

        # Upload file
        await storage.upload_file(
            content=test_content,
            key=test_key,
            content_type="text/plain",
            bucket=test_bucket,
        )

        # Download file
        downloaded_content = await storage.download_file(test_key, bucket=test_bucket)

        # Verify content matches
        assert downloaded_content == test_content

    @pytest.mark.anyio
    async def test_file_exists(self, test_bucket):
        """Test file existence check."""
        test_content = b"Test content for existence check"
        test_key = f"test/{uuid.uuid4()}/exists.txt"
        non_existent_key = f"test/{uuid.uuid4()}/does-not-exist.txt"

        # File should not exist initially
        assert not await storage.file_exists(test_key, bucket=test_bucket)
        assert not await storage.file_exists(non_existent_key, bucket=test_bucket)

        # Upload file
        await storage.upload_file(
            content=test_content, key=test_key, bucket=test_bucket
        )

        # File should exist now
        assert await storage.file_exists(test_key, bucket=test_bucket)
        assert not await storage.file_exists(non_existent_key, bucket=test_bucket)

    @pytest.mark.anyio
    async def test_delete_file(self, test_bucket):
        """Test file deletion."""
        test_content = b"Test content for deletion"
        test_key = f"test/{uuid.uuid4()}/delete-me.txt"

        # Upload file
        await storage.upload_file(
            content=test_content, key=test_key, bucket=test_bucket
        )

        # Verify file exists
        assert await storage.file_exists(test_key, bucket=test_bucket)

        # Delete file
        await storage.delete_file(test_key, bucket=test_bucket)

        # Verify file no longer exists
        assert not await storage.file_exists(test_key, bucket=test_bucket)

    @pytest.mark.anyio
    async def test_download_nonexistent_file(self, test_bucket):
        """Test downloading a file that doesn't exist."""
        non_existent_key = f"test/{uuid.uuid4()}/does-not-exist.txt"

        with pytest.raises(FileNotFoundError):
            await storage.download_file(non_existent_key, bucket=test_bucket)

    @pytest.mark.anyio
    async def test_upload_with_content_type(self, test_bucket):
        """Test uploading files with different content types."""
        test_cases = [
            (b"Plain text content", "text/plain"),
            (b'{"key": "value"}', "application/json"),
            (b"%PDF-1.4\nPDF content", "application/pdf"),
        ]

        for content, content_type in test_cases:
            test_key = f"test/{uuid.uuid4()}/content-type-test"

            await storage.upload_file(
                content=content,
                key=test_key,
                content_type=content_type,
                bucket=test_bucket,
            )

            # Verify file was uploaded
            assert await storage.file_exists(test_key, bucket=test_bucket)

            # Verify content
            downloaded = await storage.download_file(test_key, bucket=test_bucket)
            assert downloaded == content


class TestPresignedUrls:
    """Test suite for presigned URL functionality."""

    @pytest.mark.anyio
    async def test_generate_presigned_download_url(self, test_bucket):
        """Test generating presigned download URLs."""
        test_content = b"Content for presigned download test"
        test_key = f"test/{uuid.uuid4()}/presigned-download.txt"

        # Upload file first
        await storage.upload_file(
            content=test_content, key=test_key, bucket=test_bucket
        )

        # Generate presigned URL
        url = await storage.generate_presigned_download_url(
            test_key, bucket=test_bucket, expiry=300
        )

        # URL should be a string and contain the key
        assert isinstance(url, str)
        assert len(url) > 0
        # Should contain MinIO endpoint
        assert "localhost:9000" in url

    @pytest.mark.anyio
    async def test_generate_presigned_upload_url(self, test_bucket):
        """Test generating presigned upload URLs."""
        test_key = f"test/{uuid.uuid4()}/presigned-upload.txt"

        # Generate presigned URL
        url = await storage.generate_presigned_upload_url(
            test_key, bucket=test_bucket, expiry=300, content_type="text/plain"
        )

        # URL should be a string and contain the key
        assert isinstance(url, str)
        assert len(url) > 0
        # Should contain MinIO endpoint
        assert "localhost:9000" in url

    @pytest.mark.anyio
    async def test_presigned_url_expiry(self, test_bucket):
        """Test presigned URLs with different expiry times."""
        test_key = f"test/{uuid.uuid4()}/expiry-test.txt"

        # Test different expiry times
        for expiry in [60, 300, 3600]:
            url = await storage.generate_presigned_download_url(
                test_key, bucket=test_bucket, expiry=expiry
            )
            assert isinstance(url, str)
            assert len(url) > 0


class TestStorageClientConfiguration:
    """Test suite for storage client configuration."""

    @pytest.mark.anyio
    async def test_minio_client_configuration(self):
        """Test MinIO client configuration."""
        async with storage.get_storage_client() as client:
            # Should be able to create client without errors
            assert client is not None

            # Test basic operation
            response = await client.list_buckets()
            assert "Buckets" in response

    @pytest.mark.anyio
    async def test_s3_client_configuration(self, monkeypatch):
        """Test S3 client configuration (mocked)."""
        # Mock S3 configuration
        monkeypatch.setattr(config, "TRACECAT__BLOB_STORAGE_PROTOCOL", "s3")

        # Mock AWS credentials for testing
        with patch.dict(
            "os.environ",
            {"AWS_ACCESS_KEY_ID": "test-key", "AWS_SECRET_ACCESS_KEY": "test-secret"},
        ):
            async with storage.get_storage_client() as client:
                # Should be able to create client without errors
                assert client is not None


class TestLegacyFunctions:
    """Test suite for legacy storage functions."""

    @pytest.mark.anyio
    async def test_compute_sha256(self):
        """Test SHA256 hash computation."""
        test_content = b"Hello, World!"
        expected_hash = (
            "dffd6021bb2bd5b0af676290809ec3a53191dd81c7f70a4b28688a362182986f"
        )

        result = storage.compute_sha256(test_content)
        assert result == expected_hash

    @pytest.mark.anyio
    async def test_validate_content_type(self):
        """Test content type validation."""
        # Valid content types
        valid_types = [
            "application/pdf",
            "text/plain",
            "image/jpeg",
        ]

        for content_type in valid_types:
            # Should not raise an exception
            storage.validate_content_type(content_type)

        # Invalid content types
        invalid_types = [
            "application/x-executable",
            "application/javascript",
            "text/html",
        ]

        for content_type in invalid_types:
            with pytest.raises(ValueError):
                storage.validate_content_type(content_type)

    @pytest.mark.anyio
    async def test_validate_file_size(self):
        """Test file size validation."""
        # Valid sizes
        valid_sizes = [1024, 1024 * 1024, 10 * 1024 * 1024]

        for size in valid_sizes:
            # Should not raise an exception
            storage.validate_file_size(size)

        # Invalid size (too large)
        with pytest.raises(ValueError):
            storage.validate_file_size(100 * 1024 * 1024)  # 100MB

    @pytest.mark.anyio
    async def test_sanitize_filename(self):
        """Test filename sanitization."""
        test_cases = [
            ("file with spaces.txt", "file_with_spaces.txt"),
            ("file@#$%^&*().txt", "file.txt"),
            (".hidden_file.txt", "hidden_file.txt"),
            ("file.with.dots.txt", "file.with.dots.txt"),
        ]

        for input_filename, expected_output in test_cases:
            result = storage.sanitize_filename(input_filename)
            assert result == expected_output


class TestErrorHandling:
    """Test suite for error handling scenarios."""

    @pytest.mark.anyio
    async def test_invalid_bucket_operations(self):
        """Test operations with invalid bucket names."""
        invalid_bucket = "non-existent-bucket-12345"
        test_key = "test/file.txt"

        # file_exists should return False for non-existent buckets (graceful handling)
        result = await storage.file_exists(test_key, bucket=invalid_bucket)
        assert result is False

    @pytest.mark.anyio
    async def test_missing_credentials(self, monkeypatch):
        """Test behavior with missing credentials."""
        # Remove credentials
        monkeypatch.delenv("MINIO_ROOT_USER", raising=False)
        monkeypatch.delenv("MINIO_ROOT_PASSWORD", raising=False)

        # Should handle missing credentials gracefully
        # This will raise a NoCredentialsError when credentials are missing
        with pytest.raises(NoCredentialsError):
            async with storage.get_storage_client() as client:
                await client.list_buckets()


class TestFileSecurityIntegration:
    """Test integration between blob storage and file security validation."""

    def setup_method(self):
        """Set up test fixtures."""
        self.validator = storage.FileSecurityValidator()

    @pytest.mark.anyio
    async def test_upload_validated_file(self, test_bucket):
        """Test uploading a file that has been validated."""
        # Valid PDF content
        pdf_content = b"%PDF-1.4\n1 0 obj\n<<\n/Type /Catalog\n>>\nendobj\nxref\n0 1\n0000000000 65535 f \ntrailer\n<<\n/Size 1\n/Root 1 0 R\n>>\nstartxref\n9\n%%EOF"

        # Validate file first
        validated = self.validator.validate_file(
            content=pdf_content,
            filename="document.pdf",
            declared_content_type="application/pdf",
        )

        # Upload validated file
        test_key = f"test/{uuid.uuid4()}/{validated['filename']}"
        await storage.upload_file(
            content=pdf_content,
            key=test_key,
            content_type=validated["content_type"],
            bucket=test_bucket,
        )

        # Verify file was uploaded
        assert await storage.file_exists(test_key, bucket=test_bucket)

        # Verify content
        downloaded = await storage.download_file(test_key, bucket=test_bucket)
        assert downloaded == pdf_content

    @pytest.mark.anyio
    async def test_upload_rejected_file_content(self, test_bucket):
        """Test that we can still upload content that would be rejected by validation."""
        # This tests that storage functions work independently of validation
        malicious_content = b"<script>alert('xss')</script>"
        test_key = f"test/{uuid.uuid4()}/malicious.txt"

        # Storage should allow upload (validation is separate)
        await storage.upload_file(
            content=malicious_content,
            key=test_key,
            content_type="text/plain",
            bucket=test_bucket,
        )

        # Verify file was uploaded
        assert await storage.file_exists(test_key, bucket=test_bucket)

        # But validation should reject it
        with pytest.raises(ValueError):
            self.validator.validate_file(
                content=malicious_content,
                filename="malicious.txt",
                declared_content_type="text/plain",
            )
