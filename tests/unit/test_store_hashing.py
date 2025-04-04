"""Tests for the store hashing utilities."""

import hashlib

import pytest

from tracecat.ee.store.hashing import digest, validate_digest


class TestHashing:
    """Tests for the hashing utilities."""

    def test_digest_with_string_data(self):
        """Test computing the digest for string data."""
        # Arrange
        test_data = b"test data"
        expected_hash = hashlib.sha256(test_data).hexdigest()

        # Act
        result = digest(test_data)

        # Assert
        assert result == f"sha256:{expected_hash}"
        assert result.startswith("sha256:")

    def test_digest_with_empty_data(self):
        """Test computing the digest for empty data."""
        # Arrange
        test_data = b""
        expected_hash = hashlib.sha256(test_data).hexdigest()

        # Act
        result = digest(test_data)

        # Assert
        assert result == f"sha256:{expected_hash}"

    def test_digest_with_binary_data(self):
        """Test computing the digest for binary data."""
        # Arrange
        test_data = b"\x00\x01\x02\x03\x04"
        expected_hash = hashlib.sha256(test_data).hexdigest()

        # Act
        result = digest(test_data)

        # Assert
        assert result == f"sha256:{expected_hash}"

    def test_validate_digest_valid(self):
        """Test validating a valid digest."""
        # Arrange
        test_data = b"test data"
        test_hash = hashlib.sha256(test_data).hexdigest()
        test_digest = f"sha256:{test_hash}"

        # Act & Assert - should not raise an exception
        validate_digest(test_digest, test_data)

    def test_validate_digest_invalid_format(self):
        """Test validating a digest with invalid format."""
        # Arrange
        test_data = b"test data"
        invalid_digest = "invalid_format"

        # Act & Assert
        with pytest.raises(ValueError, match="Invalid digest format"):
            validate_digest(invalid_digest, test_data)

    def test_validate_digest_unsupported_algorithm(self):
        """Test validating a digest with unsupported algorithm."""
        # Arrange
        test_data = b"test data"
        test_hash = hashlib.sha256(test_data).hexdigest()
        invalid_algorithm_digest = f"md5:{test_hash}"

        # Act & Assert
        with pytest.raises(ValueError, match="Invalid digest format"):
            validate_digest(invalid_algorithm_digest, test_data)

    def test_validate_digest_checksum_mismatch(self):
        """Test validating a digest with incorrect checksum."""
        # Arrange
        test_data = b"test data"
        wrong_data = b"wrong data"
        test_hash = hashlib.sha256(test_data).hexdigest()
        test_digest = f"sha256:{test_hash}"

        # Act & Assert
        with pytest.raises(ValueError, match="Checksum mismatch"):
            validate_digest(test_digest, wrong_data)

    def test_digest_and_validate_roundtrip(self):
        """Test computing a digest and then validating it."""
        # Arrange
        test_data = b"test data for roundtrip"

        # Act
        test_digest = digest(test_data)

        # Assert - should not raise an exception
        validate_digest(test_digest, test_data)

    def test_validate_digest_with_large_data(self):
        """Test validating a digest with a large amount of data."""
        # Arrange
        test_data = b"x" * 1024 * 1024  # 1MB of data
        test_hash = hashlib.sha256(test_data).hexdigest()
        test_digest = f"sha256:{test_hash}"

        # Act & Assert - should not raise an exception
        validate_digest(test_digest, test_data)
