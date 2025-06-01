"""Test case attachments functionality."""

import pytest

from tracecat import storage
from tracecat.cases.models import CaseAttachmentCreate


@pytest.mark.anyio
async def test_storage_functions():
    """Test the storage utility functions."""
    print("Testing storage functions...")

    # Test filename sanitization
    test_filenames = [
        ("normal_file.pdf", "normal_file.pdf"),
        ("../../../etc/passwd", "passwd"),
        ("file with spaces.doc", "file_with_spaces.doc"),
        (".hidden_file", "hidden_file"),
        ("file...with...dots", "file.with.dots"),
        ("a" * 300 + ".txt", "a" * 251 + ".txt"),  # Truncation test
        ("", "unnamed_file"),
    ]

    for input_name, expected in test_filenames:
        result = storage.sanitize_filename(input_name)
        print(f"  {input_name!r} -> {result!r} (expected: {expected!r})")
        assert result == expected, f"Expected {expected}, got {result}"

    # Test content type validation
    valid_types = [
        "application/pdf",
        "image/jpeg",
        "text/plain",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ]

    invalid_types = [
        "application/x-executable",
        "text/html",
        "application/javascript",
        "image/svg+xml",
    ]

    print("\nTesting valid content types...")
    for content_type in valid_types:
        try:
            storage.validate_content_type(content_type)
            print(f"  ✓ {content_type}")
        except ValueError as e:
            pytest.fail(f"Valid content type {content_type} was rejected: {e}")

    print("\nTesting invalid content types...")
    for content_type in invalid_types:
        try:
            storage.validate_content_type(content_type)
            pytest.fail(f"Invalid content type {content_type} was accepted")
        except ValueError:
            print(f"  ✓ {content_type} correctly rejected")

    # Test file size validation
    print("\nTesting file size validation...")

    # Valid sizes (storage only validates max size, not min)
    valid_sizes = [1024, 1024 * 1024, 10 * 1024 * 1024]  # 1KB, 1MB, 10MB
    for size in valid_sizes:
        try:
            storage.validate_file_size(size)
            print(f"  ✓ {size} bytes")
        except ValueError as e:
            pytest.fail(f"Valid file size {size} was rejected: {e}")

    # Invalid sizes (only too large files are rejected by storage)
    invalid_sizes = [101 * 1024 * 1024]  # 101MB (exceeds 50MB limit)
    for size in invalid_sizes:
        try:
            storage.validate_file_size(size)
            pytest.fail(f"Invalid file size {size} was accepted")
        except ValueError:
            print(f"  ✓ {size} bytes correctly rejected")

    # Test SHA256 computation
    print("\nTesting SHA256 computation...")
    test_content = b"Hello, World!"
    expected_hash = "dffd6021bb2bd5b0af676290809ec3a53191dd81c7f70a4b28688a362182986f"
    computed_hash = storage.compute_sha256(test_content)
    print(f"  Content: {test_content}")
    print(f"  Expected: {expected_hash}")
    print(f"  Computed: {computed_hash}")
    assert computed_hash == expected_hash, (
        f"Hash mismatch: expected {expected_hash}, got {computed_hash}"
    )


@pytest.mark.anyio
async def test_case_attachment_create_model():
    """Test the CaseAttachmentCreate model validation."""
    print("\nTesting CaseAttachmentCreate model...")

    # Valid attachment
    valid_data = {
        "file_name": "test_document.pdf",
        "content_type": "application/pdf",
        "size": 1024 * 1024,  # 1MB
        "content": b"fake pdf content",
    }

    try:
        attachment = CaseAttachmentCreate(**valid_data)
        print(f"  ✓ Valid attachment created: {attachment.file_name}")
        assert attachment.file_name == "test_document.pdf"
        assert attachment.content_type == "application/pdf"
        assert attachment.size == 1024 * 1024
        assert attachment.content == b"fake pdf content"
    except Exception as e:
        pytest.fail(f"Failed to create valid attachment: {e}")

    # Test that empty filename is allowed (will be sanitized later)
    try:
        attachment = CaseAttachmentCreate(
            file_name="",  # Empty filename is allowed
            content_type="application/pdf",
            size=1024,
            content=b"content",
        )
        print(f"  ✓ Empty filename allowed: '{attachment.file_name}'")
    except Exception as e:
        pytest.fail(f"Empty filename should be allowed: {e}")

    # Test validation errors that should actually fail
    invalid_cases = [
        # Negative size (should fail due to gt=0 constraint)
        {**valid_data, "size": -1},
        # Zero size (should fail due to gt=0 constraint)
        {**valid_data, "size": 0},
    ]

    for i, invalid_data in enumerate(invalid_cases):
        try:
            CaseAttachmentCreate(**invalid_data)
            pytest.fail(f"Invalid case {i} should have failed validation")
        except Exception:
            print(f"  ✓ Invalid case {i} correctly rejected")


if __name__ == "__main__":
    import asyncio

    async def main():
        await test_storage_functions()
        await test_case_attachment_create_model()
        print("\n✅ All tests passed!")

    asyncio.run(main())
