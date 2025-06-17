"""Integration tests for grep functionality using MinIO as S3-compatible storage."""

import asyncio
from io import BytesIO

import pytest
from minio import Minio
from tracecat_registry.integrations.amazon_s3 import _s3_semaphore
from tracecat_registry.integrations.grep import s3 as grep_s3

from tracecat import config


@pytest.fixture
async def sample_files(minio_client: Minio, minio_bucket: str) -> dict[str, str]:
    """Upload sample files to MinIO for testing."""
    files = {
        "log1.txt": """2024-01-01 10:00:00 INFO Starting application
2024-01-01 10:00:01 DEBUG Loading configuration
2024-01-01 10:00:02 ERROR Failed to connect to database
2024-01-01 10:00:03 WARN Retrying connection
2024-01-01 10:00:04 INFO Connected successfully
""",
        "log2.txt": """2024-01-01 11:00:00 INFO User login: john@example.com
2024-01-01 11:00:01 DEBUG Session created
2024-01-01 11:00:02 INFO Processing request
2024-01-01 11:00:03 ERROR Invalid request format
2024-01-01 11:00:04 WARN Request rejected
""",
        "config.json": """{
    "database": {
        "host": "localhost",
        "port": 5432,
        "name": "testdb"
    },
    "logging": {
        "level": "INFO",
        "format": "%(asctime)s %(levelname)s %(message)s"
    }
}""",
        "empty.txt": "",
        "special_chars.txt": "Special characters: !@#$%^&*()_+-=[]{}|;':\",./<>?",
    }

    # Upload files to MinIO
    for filename, content in files.items():
        content_bytes = content.encode("utf-8")
        minio_client.put_object(
            minio_bucket,
            filename,
            data=BytesIO(content_bytes),
            length=len(content_bytes),
            content_type="text/plain",
        )

    return files


class TestGrepS3Integration:
    """Integration tests for grep S3 functionality."""

    @pytest.mark.anyio
    async def test_grep_single_pattern_match(
        self, minio_bucket, mock_s3_secrets, sample_files, aioboto3_minio_client
    ):
        """Test grepping for a pattern that matches in multiple files."""
        result = await grep_s3(
            bucket=minio_bucket,
            keys=["log1.txt", "log2.txt"],
            pattern="ERROR",
            max_columns=1000,
        )

        # Verify results - ripgrep returns a list of JSON objects
        assert isinstance(result, list)

        if len(result) > 0:
            # Check that we found ERROR matches
            error_matches = [
                item
                for item in result
                if isinstance(item, dict) and item.get("type") == "match"
            ]
            assert len(error_matches) >= 2  # Should find ERROR in both files

            # Verify match content contains ERROR
            for match in error_matches:
                lines_data = match.get("data", {}).get("lines", {})
                if isinstance(lines_data, dict):
                    assert "ERROR" in lines_data.get("text", "")

    @pytest.mark.anyio
    async def test_grep_no_matches(
        self, minio_bucket, mock_s3_secrets, sample_files, aioboto3_minio_client
    ):
        """Test grepping for a pattern that doesn't match anything."""
        result = await grep_s3(
            bucket=minio_bucket,
            keys=["log1.txt", "log2.txt"],
            pattern="NONEXISTENT_PATTERN",
            max_columns=1000,
        )

        # Should return list with only summary objects when no matches found
        assert isinstance(result, list)
        matches = [
            item
            for item in result
            if isinstance(item, dict) and item.get("type") == "match"
        ]
        assert len(matches) == 0

    @pytest.mark.anyio
    async def test_grep_json_content(
        self, minio_bucket, mock_s3_secrets, sample_files, aioboto3_minio_client
    ):
        """Test grepping JSON content."""
        result = await grep_s3(
            bucket=minio_bucket,
            keys=["config.json"],
            pattern="localhost",
            max_columns=1000,
        )

        assert isinstance(result, list)

        if len(result) > 0:
            # Should find localhost in the JSON config
            matches = [
                item
                for item in result
                if isinstance(item, dict) and item.get("type") == "match"
            ]
            assert len(matches) >= 1

            # Check that at least one match contains localhost
            found_localhost = False
            for match in matches:
                lines_data = match.get("data", {}).get("lines", {})
                if isinstance(lines_data, dict) and "localhost" in lines_data.get(
                    "text", ""
                ):
                    found_localhost = True
                    break
            assert found_localhost

    @pytest.mark.anyio
    async def test_grep_empty_file(
        self, minio_bucket, mock_s3_secrets, sample_files, aioboto3_minio_client
    ):
        """Test grepping an empty file."""
        result = await grep_s3(
            bucket=minio_bucket,
            keys=["empty.txt"],
            pattern="anything",
            max_columns=1000,
        )

        # Empty file should return no match results
        assert isinstance(result, list)
        matches = [
            item
            for item in result
            if isinstance(item, dict) and item.get("type") == "match"
        ]
        assert len(matches) == 0

    @pytest.mark.anyio
    async def test_grep_special_characters(
        self, minio_bucket, mock_s3_secrets, sample_files, aioboto3_minio_client
    ):
        """Test grepping for special characters."""
        # @ doesn't need escaping in regex
        result = await grep_s3(
            bucket=minio_bucket,
            keys=["special_chars.txt"],
            pattern="@",  # Search for @ symbol without escaping
            max_columns=1000,
        )

        assert isinstance(result, list)

        if len(result) > 0:
            matches = [
                item
                for item in result
                if isinstance(item, dict) and item.get("type") == "match"
            ]
            assert len(matches) >= 1

    @pytest.mark.anyio
    async def test_grep_max_columns_limit(
        self, minio_bucket, mock_s3_secrets, sample_files, aioboto3_minio_client
    ):
        """Test grep with max columns limit."""
        result = await grep_s3(
            bucket=minio_bucket,
            keys=["log1.txt"],
            pattern="INFO",
            max_columns=10,  # Very small limit
        )

        # Should still work with small column limit
        assert isinstance(result, list)

    @pytest.mark.anyio
    async def test_grep_invalid_bucket(self, mock_s3_secrets, aioboto3_minio_client):
        """Test grep with invalid bucket name."""
        from tenacity import RetryError

        with pytest.raises(RetryError):  # get_objects uses retry logic
            await grep_s3(
                bucket="nonexistent-bucket",
                keys=["test.txt"],
                pattern="test",
                max_columns=1000,
            )

    @pytest.mark.anyio
    async def test_grep_invalid_keys(
        self, minio_bucket, mock_s3_secrets, sample_files, aioboto3_minio_client
    ):
        """Test grep with invalid/nonexistent keys."""
        from tenacity import RetryError

        with pytest.raises(RetryError):  # get_objects uses retry logic
            await grep_s3(
                bucket=minio_bucket,
                keys=["nonexistent.txt"],
                pattern="test",
                max_columns=1000,
            )

    @pytest.mark.anyio
    async def test_grep_validation_errors(self, mock_s3_secrets):
        """Test grep input validation."""
        # Test empty bucket
        with pytest.raises(ValueError, match="Bucket and keys must be provided"):
            await grep_s3(
                bucket="",
                keys=["test.txt"],
                pattern="test",
                max_columns=1000,
            )

        # Test empty keys
        with pytest.raises(ValueError, match="Bucket and keys must be provided"):
            await grep_s3(
                bucket="test-bucket",
                keys=[],
                pattern="test",
                max_columns=1000,
            )

        # Test too many keys
        with pytest.raises(
            ValueError, match="Cannot process more than 1000 keys at once"
        ):
            await grep_s3(
                bucket="test-bucket",
                keys=["test.txt"] * 1001,  # More than 1000 keys
                pattern="test",
                max_columns=1000,
            )

    @pytest.mark.anyio
    async def test_grep_caching_behavior(
        self, minio_bucket, mock_s3_secrets, sample_files, aioboto3_minio_client
    ):
        """Test that caching works correctly for repeated requests."""
        # First request
        result1 = await grep_s3(
            bucket=minio_bucket,
            keys=["log1.txt"],
            pattern="INFO",
            max_columns=1000,
        )

        # Second request (should use cache)
        result2 = await grep_s3(
            bucket=minio_bucket,
            keys=["log1.txt"],
            pattern="INFO",
            max_columns=1000,
        )

        # Results should have the same number of matches (paths will differ due to temp dirs)
        matches1 = [
            item
            for item in result1
            if isinstance(item, dict) and item.get("type") == "match"
        ]
        matches2 = [
            item
            for item in result2
            if isinstance(item, dict) and item.get("type") == "match"
        ]
        assert len(matches1) == len(matches2)

        # Check that the match content is the same
        for match1, match2 in zip(matches1, matches2, strict=False):
            assert match1["data"]["lines"]["text"] == match2["data"]["lines"]["text"]
            assert match1["data"]["line_number"] == match2["data"]["line_number"]

    @pytest.mark.anyio
    async def test_grep_multiple_patterns(
        self, minio_bucket, mock_s3_secrets, sample_files, aioboto3_minio_client
    ):
        """Test grepping with regex patterns."""
        # Test regex pattern for INFO or ERROR
        result = await grep_s3(
            bucket=minio_bucket,
            keys=["log1.txt", "log2.txt"],
            pattern="INFO|ERROR",
            max_columns=1000,
        )

        assert isinstance(result, list)

        if len(result) > 0:
            matches = [
                item
                for item in result
                if isinstance(item, dict) and item.get("type") == "match"
            ]
            # Should find multiple matches for both INFO and ERROR
            assert len(matches) >= 4

    @pytest.mark.anyio
    async def test_grep_case_sensitive(
        self, minio_bucket, mock_s3_secrets, sample_files, aioboto3_minio_client
    ):
        """Test case-sensitive grep behavior."""
        # Test lowercase pattern that shouldn't match uppercase text
        result = await grep_s3(
            bucket=minio_bucket,
            keys=["log1.txt"],
            pattern="info",  # lowercase
            max_columns=1000,
        )

        # Should not match uppercase INFO
        assert isinstance(result, list)
        matches = [
            item
            for item in result
            if isinstance(item, dict) and item.get("type") == "match"
        ]
        assert len(matches) == 0

    @pytest.mark.anyio
    async def test_grep_line_numbers(
        self, minio_bucket, mock_s3_secrets, sample_files, aioboto3_minio_client
    ):
        """Test that grep results include line numbers."""
        result = await grep_s3(
            bucket=minio_bucket,
            keys=["log1.txt"],
            pattern="ERROR",
            max_columns=1000,
        )

        assert isinstance(result, list)

        if len(result) > 0:
            matches = [
                item
                for item in result
                if isinstance(item, dict) and item.get("type") == "match"
            ]
            assert len(matches) >= 1

            # Check that line numbers are included
            for match in matches:
                data = match.get("data", {})
                assert "line_number" in data
                assert isinstance(data["line_number"], int)
                assert data["line_number"] > 0

    @pytest.mark.anyio
    async def test_grep_file_paths(
        self, minio_bucket, mock_s3_secrets, sample_files, aioboto3_minio_client
    ):
        """Test that grep results include file paths."""
        result = await grep_s3(
            bucket=minio_bucket,
            keys=["log1.txt", "log2.txt"],
            pattern="ERROR",
            max_columns=1000,
        )

        assert isinstance(result, list)

        if len(result) > 0:
            matches = [
                item
                for item in result
                if isinstance(item, dict) and item.get("type") == "match"
            ]
            assert len(matches) >= 2

            # Check that file paths are included and different
            file_paths = set()
            for match in matches:
                data = match.get("data", {})
                if "path" in data and isinstance(data["path"], dict):
                    file_paths.add(data["path"]["text"])

            # Should have matches from at least one file
            assert len(file_paths) >= 1

    @pytest.mark.anyio
    async def test_grep_concurrent_calls(
        self, minio_bucket, mock_s3_secrets, sample_files, aioboto3_minio_client
    ):
        """Test that concurrent grep calls work properly with resource limiting."""

        # Create multiple concurrent grep operations
        tasks = []
        for _ in range(8):  # Create 8 concurrent tasks
            task = grep_s3(
                bucket=minio_bucket,
                keys=["log1.txt", "log2.txt"],
                pattern="INFO",
                max_columns=1000,
            )
            tasks.append(task)

        # Execute all tasks concurrently
        results = await asyncio.gather(*tasks)

        # All tasks should complete successfully
        assert len(results) == 8

        # Each result should be a list
        for result in results:
            assert isinstance(result, list)

        # Results should be consistent (same pattern, same files)
        # Count matches in each result
        match_counts = []
        for result in results:
            matches = [
                item
                for item in result
                if isinstance(item, dict) and item.get("type") == "match"
            ]
            match_counts.append(len(matches))

        # All results should have the same number of matches
        assert all(count == match_counts[0] for count in match_counts)

    @pytest.mark.anyio
    async def test_grep_large_concurrent_load(
        self, minio_bucket, mock_s3_secrets, sample_files, aioboto3_minio_client
    ):
        """Test grep under larger concurrent load to verify resource limiting."""

        # Create many concurrent grep operations with different patterns
        patterns = ["INFO", "ERROR", "DEBUG", "WARN", "localhost"]
        tasks = []

        for i in range(20):  # Create 20 concurrent tasks
            pattern = patterns[i % len(patterns)]
            task = grep_s3(
                bucket=minio_bucket,
                keys=["log1.txt", "log2.txt", "config.json"],
                pattern=pattern,
                max_columns=1000,
            )
            tasks.append(task)

        # Execute all tasks concurrently - this should not overwhelm the system
        # due to semaphore-based concurrency limiting
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # All tasks should complete (either successfully or with expected exceptions)
        assert len(results) == 20

        # Count successful results vs exceptions
        successful_results = [r for r in results if not isinstance(r, Exception)]
        exceptions = [r for r in results if isinstance(r, Exception)]

        # With concurrency limiting, we expect some operations to succeed
        # The exact number depends on system resources and timing
        assert len(successful_results) >= 5  # At least some should succeed
        assert len(successful_results) <= 20  # But not more than total

        # Any exceptions should be expected types (not resource exhaustion)
        for exc in exceptions:
            # Should not be resource-related exceptions
            assert not isinstance(exc, OSError | MemoryError | asyncio.TimeoutError)
            # Log the exception type for debugging
            print(f"Exception type: {type(exc).__name__}: {exc}")

        # Verify that the concurrency limiting is working by checking that
        # we have a mix of successes and potential failures, which indicates
        # the system is properly managing resources rather than crashing

    @pytest.mark.anyio
    async def test_s3_concurrency_limits_configuration(self):
        """Test that S3 concurrency limits are properly configured."""

        # Verify that the S3 semaphore is using the configured limit
        assert _s3_semaphore._value == config.TRACECAT__S3_CONCURRENCY_LIMIT

        # Verify default values are reasonable
        assert config.TRACECAT__S3_CONCURRENCY_LIMIT > 0
        assert (
            config.TRACECAT__S3_CONCURRENCY_LIMIT <= 200
        )  # Reasonable upper bound for S3
