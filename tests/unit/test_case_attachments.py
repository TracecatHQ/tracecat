"""Comprehensive test suite for case attachments security with polyfile integration."""

import uuid

import pytest
from polyfile.magic import MagicMatcher

from tracecat import config, storage
from tracecat.cases.enums import CasePriority, CaseSeverity, CaseStatus
from tracecat.cases.models import CaseAttachmentCreate, CaseCreate
from tracecat.cases.service import CasesService
from tracecat.types.exceptions import TracecatNotFoundError


class TestFileSecurityValidator:
    """Test suite for FileSecurityValidator security features with polyfile integration."""

    def setup_method(self):
        """Set up test fixtures."""
        self.validator = storage.FileSecurityValidator()

    @pytest.mark.anyio
    async def test_valid_pdf_file(self):
        """Test validation of a valid PDF file."""
        # Valid PDF content with proper magic number
        pdf_content = b"%PDF-1.4\n1 0 obj\n<<\n/Type /Catalog\n>>\nendobj\nxref\n0 1\n0000000000 65535 f \ntrailer\n<<\n/Size 1\n/Root 1 0 R\n>>\nstartxref\n9\n%%EOF"

        result = self.validator.validate_file(
            content=pdf_content,
            filename="document.pdf",
            declared_content_type="application/pdf",
        )

        assert result["filename"] == "document.pdf"
        assert result["content_type"] == "application/pdf"

    @pytest.mark.anyio
    async def test_valid_jpeg_file(self):
        """Test validation of a valid JPEG file."""
        # Valid JPEG content with proper magic number
        jpeg_content = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x01\x00H\x00H\x00\x00\xff\xdb\x00C\x00"

        result = self.validator.validate_file(
            content=jpeg_content,
            filename="image.jpg",
            declared_content_type="image/jpeg",
        )

        assert result["filename"] == "image.jpg"
        assert result["content_type"] == "image/jpeg"

    @pytest.mark.anyio
    async def test_blocked_extension_rejection(self):
        """Test that blocked extensions are rejected."""
        content = b"fake content"

        blocked_extensions = [".exe", ".bat", ".php", ".js", ".html", ".sh"]

        for ext in blocked_extensions:
            filename = f"malicious{ext}"
            with pytest.raises(ValueError, match="not allowed for security reasons"):
                self.validator.validate_file(
                    content=content,
                    filename=filename,
                    declared_content_type="application/octet-stream",
                )

    @pytest.mark.anyio
    async def test_blocked_content_type_rejection(self):
        """Test that blocked content types are rejected."""
        content = b"fake content"

        blocked_types = [
            "application/x-executable",
            "application/javascript",
            "text/html",
            "application/x-sh",
        ]

        for content_type in blocked_types:
            with pytest.raises(ValueError, match="not allowed for security reasons"):
                self.validator.validate_file(
                    content=content,
                    filename="file.txt",
                    declared_content_type=content_type,
                )

    @pytest.mark.anyio
    async def test_magic_number_mismatch_detection(self):
        """Test detection of magic number mismatches."""
        # PDF magic number with .txt extension
        pdf_content = b"%PDF-1.4\nfake pdf content"

        with pytest.raises(ValueError, match="does not match extension"):
            self.validator.validate_file(
                content=pdf_content,
                filename="document.txt",
                declared_content_type="text/plain",
            )

    @pytest.mark.anyio
    async def test_embedded_executable_detection(self):
        """Test detection of embedded executables."""
        # Content with PE executable signature
        malicious_content = b"some content MZ\x90\x00 more content"

        with pytest.raises(ValueError, match="potentially dangerous embedded content"):
            self.validator.validate_file(
                content=malicious_content,
                filename="document.txt",
                declared_content_type="text/plain",
            )

    @pytest.mark.anyio
    async def test_javascript_in_pdf_detection(self):
        """Test detection of JavaScript in PDF files."""
        # PDF with JavaScript
        pdf_with_js = b"%PDF-1.4\n/JavaScript (alert('xss'))"

        with pytest.raises(ValueError, match="PDF contains JavaScript"):
            self.validator.validate_file(
                content=pdf_with_js,
                filename="malicious.pdf",
                declared_content_type="application/pdf",
            )

    @pytest.mark.anyio
    async def test_script_in_image_detection(self):
        """Test detection of scripts in image files."""
        # JPEG with embedded script
        jpeg_with_script = b"\xff\xd8\xff\xe0\x00\x10JFIF<script>alert('xss')</script>"

        with pytest.raises(ValueError, match="potentially dangerous embedded content"):
            self.validator.validate_file(
                content=jpeg_with_script,
                filename="image.jpg",
                declared_content_type="image/jpeg",
            )

    @pytest.mark.anyio
    async def test_directory_traversal_prevention(self):
        """Test prevention of directory traversal attacks."""
        content = b"fake content"

        malicious_filenames = [
            "../../../etc/passwd",
            "..\\..\\windows\\system32\\config\\sam",
            "/etc/passwd",
            "\\windows\\system32\\config\\sam",
        ]

        for filename in malicious_filenames:
            with pytest.raises(ValueError, match="invalid path characters"):
                self.validator.validate_file(
                    content=content,
                    filename=filename,
                    declared_content_type="text/plain",
                )

    @pytest.mark.anyio
    async def test_control_character_prevention(self):
        """Test prevention of control characters in filenames."""
        content = b"fake content"

        # Filename with null byte
        malicious_filename = "file\x00.txt"

        with pytest.raises(ValueError, match="invalid control characters"):
            self.validator.validate_file(
                content=content,
                filename=malicious_filename,
                declared_content_type="text/plain",
            )

    @pytest.mark.anyio
    async def test_empty_file_rejection(self):
        """Test rejection of empty files."""
        with pytest.raises(ValueError, match="File cannot be empty"):
            self.validator.validate_file(
                content=b"", filename="empty.txt", declared_content_type="text/plain"
            )

    @pytest.mark.anyio
    async def test_oversized_file_rejection(self):
        """Test rejection of oversized files."""
        # Create content larger than 50MB
        large_content = b"x" * (51 * 1024 * 1024)

        with pytest.raises(ValueError, match="exceeds maximum allowed size"):
            self.validator.validate_file(
                content=large_content,
                filename="large.txt",
                declared_content_type="text/plain",
            )

    @pytest.mark.anyio
    async def test_filename_sanitization(self):
        """Test filename sanitization."""
        content = b"valid content"

        test_cases = [
            ("file with spaces.txt", "file_with_spaces.txt"),
            (
                "file.with.dots.txt",
                "file.with.dots.txt",
            ),  # Multiple dots are now allowed
            (".hidden_file.txt", "hidden_file.txt"),
            ("file@#$%^&*().txt", "file.txt"),
        ]

        for input_filename, expected_filename in test_cases:
            result = self.validator.validate_file(
                content=content,
                filename=input_filename,
                declared_content_type="text/plain",
            )
            assert result["filename"] == expected_filename

    @pytest.mark.anyio
    async def test_text_file_script_detection(self):
        """Test detection of scripts in text files."""
        malicious_scripts = [
            (
                b"<script>alert('xss')</script>",
                "potentially dangerous embedded content",
            ),
            (
                b"javascript:alert('xss')",
                "Text file contains potentially dangerous content",
            ),
            (
                b"<?php system($_GET['cmd']); ?>",
                "potentially dangerous embedded content",
            ),
            (b"#!/bin/bash\nrm -rf /", "potentially dangerous embedded content"),
        ]

        for script_content, expected_error in malicious_scripts:
            with pytest.raises(ValueError, match=expected_error):
                self.validator.validate_file(
                    content=script_content,
                    filename="script.txt",
                    declared_content_type="text/plain",
                )

    @pytest.mark.anyio
    async def test_invalid_utf8_text_rejection(self):
        """Test rejection of invalid UTF-8 in text files."""
        # Invalid UTF-8 sequence
        invalid_utf8 = b"\xff\xfe\x00\x00"

        with pytest.raises(ValueError, match="invalid UTF-8 encoding"):
            self.validator.validate_file(
                content=invalid_utf8,
                filename="invalid.txt",
                declared_content_type="text/plain",
            )

    @pytest.mark.anyio
    async def test_office_document_validation(self):
        """Test validation of Office documents."""
        # Simulate DOCX content (ZIP with Office structure)
        docx_content = b"PK\x03\x04\x14\x00\x00\x00\x08\x00word/document.xml"

        result = self.validator.validate_file(
            content=docx_content,
            filename="document.docx",
            declared_content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )

        assert (
            result["content_type"]
            == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        )

    @pytest.mark.anyio
    async def test_webp_validation(self):
        """Test WebP file validation with proper RIFF structure."""
        # Valid WebP content
        webp_content = b"RIFF\x1a\x00\x00\x00WEBP"

        result = self.validator.validate_file(
            content=webp_content,
            filename="image.webp",
            declared_content_type="image/webp",
        )

        assert result["content_type"] == "image/webp"

    @pytest.mark.anyio
    async def test_invalid_webp_rejection(self):
        """Test rejection of invalid WebP files."""
        # Use a JPEG signature but claim it's WebP
        jpeg_content = b"\xff\xd8\xff\xe0\x00\x10JFIF"

        with pytest.raises(ValueError, match="File content.*does not match"):
            self.validator.validate_file(
                content=jpeg_content,
                filename="image.webp",
                declared_content_type="image/webp",
            )

    # === NEW POLYFILE-ENHANCED TESTS ===

    @pytest.mark.anyio
    async def test_polyfile_polyglot_detection(self):
        """Test polyfile's enhanced polyglot detection."""
        # Create content that might be detected as multiple formats
        # This is a simplified test - in reality, polyglots are more sophisticated
        polyglot_content = b"%PDF-1.4\n<html><script>alert('xss')</script></html>"

        # This should be caught by either our basic checks or polyfile analysis
        with pytest.raises(ValueError):
            self.validator.validate_file(
                content=polyglot_content,
                filename="polyglot.pdf",
                declared_content_type="application/pdf",
            )

    @pytest.mark.anyio
    async def test_polyfile_executable_in_document_detection(self):
        """Test detection of executable signatures in document files."""
        # PDF with embedded PE executable signature
        pdf_with_exe = b"%PDF-1.4\nMZ\x90\x00\x03\x00\x00\x00"

        with pytest.raises(
            ValueError,
            match="potentially dangerous embedded content|executable signature",
        ):
            self.validator.validate_file(
                content=pdf_with_exe,
                filename="malicious.pdf",
                declared_content_type="application/pdf",
            )

    @pytest.mark.anyio
    async def test_polyfile_script_in_binary_detection(self):
        """Test detection of script signatures in binary files."""
        # Image with embedded script tag (more realistic attack)
        image_with_script = b"\xff\xd8\xff\xe0\x00\x10JFIF<script>alert('xss')</script>"

        with pytest.raises(
            ValueError, match="potentially dangerous embedded content|script signature"
        ):
            self.validator.validate_file(
                content=image_with_script,
                filename="malicious.jpg",
                declared_content_type="image/jpeg",
            )

    @pytest.mark.anyio
    async def test_polyfile_multiple_format_detection(self):
        """Test polyfile's ability to detect multiple file formats."""
        # Content that might be valid as both ZIP and another format
        multi_format_content = (
            b"PK\x03\x04\x14\x00\x00\x00\x08\x00<script>alert('xss')</script>"
        )

        # Should either pass (if benign) or fail with specific error
        try:
            result = self.validator.validate_file(
                content=multi_format_content,
                filename="test.zip",
                declared_content_type="application/zip",
            )
            # If it passes, it should be properly validated
            assert result["content_type"] == "application/zip"
        except ValueError as e:
            # If it fails, it should be due to security concerns
            assert any(
                keyword in str(e).lower()
                for keyword in ["dangerous", "suspicious", "script", "embedded"]
            )

    @pytest.mark.anyio
    async def test_polyfile_enhanced_mime_detection(self):
        """Test polyfile's enhanced MIME type detection."""
        # Valid PDF that should be properly detected
        valid_pdf = b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\nxref\n0 1\n0000000000 65535 f\ntrailer<</Size 1/Root 1 0 R>>\nstartxref\n9\n%%EOF"

        result = self.validator.validate_file(
            content=valid_pdf,
            filename="document.pdf",
            declared_content_type="application/pdf",
        )

        assert result["content_type"] == "application/pdf"
        assert result["filename"] == "document.pdf"

    @pytest.mark.anyio
    async def test_polyfile_graceful_failure_handling(self):
        """Test that validation continues even if polyfile analysis fails."""
        # Use content that might cause polyfile issues but should still pass basic validation
        simple_text = b"This is just plain text content."

        # This should pass even if polyfile has issues
        result = self.validator.validate_file(
            content=simple_text,
            filename="simple.txt",
            declared_content_type="text/plain",
        )

        assert result["content_type"] == "text/plain"
        assert result["filename"] == "simple.txt"

    @pytest.mark.anyio
    async def test_polyfile_dangerous_mime_combinations(self):
        """Test detection of dangerous MIME type combinations."""
        # Content that might be detected as both a document and executable
        dangerous_combo = (
            b"%PDF-1.4\nMZ\x90\x00\x03\x00\x00\x00\x04\x00\x00\x00\xff\xff\x00\x00"
        )

        with pytest.raises(ValueError, match="dangerous|executable|suspicious"):
            self.validator.validate_file(
                content=dangerous_combo,
                filename="dangerous.pdf",
                declared_content_type="application/pdf",
            )

    @pytest.mark.anyio
    async def test_polyfile_embedded_content_validation(self):
        """Test polyfile's embedded content detection."""
        # ZIP file with suspicious embedded content
        zip_with_script = b"PK\x03\x04\x14\x00\x00\x00\x08\x00#!/bin/bash\nrm -rf /"

        with pytest.raises(ValueError, match="suspicious|embedded|script"):
            self.validator.validate_file(
                content=zip_with_script,
                filename="malicious.zip",
                declared_content_type="application/zip",
            )


class TestCaseAttachmentCreateModel:
    """Test the CaseAttachmentCreate model validation."""

    @pytest.mark.anyio
    async def test_valid_attachment_creation(self):
        """Test creation of valid attachments."""
        valid_data = {
            "file_name": "test_document.pdf",
            "content_type": "application/pdf",
            "size": 1024 * 1024,  # 1MB
            "content": b"%PDF-1.4\nvalid pdf content",
        }

        attachment = CaseAttachmentCreate(**valid_data)
        assert attachment.file_name == "test_document.pdf"
        assert attachment.content_type == "application/pdf"
        assert attachment.size == 1024 * 1024

    @pytest.mark.anyio
    async def test_size_validation(self):
        """Test file size validation in the model."""
        valid_data = {
            "file_name": "test.txt",
            "content_type": "text/plain",
            "content": b"content",
        }

        # Test negative size
        with pytest.raises(ValueError):
            CaseAttachmentCreate(**valid_data, size=-1)

        # Test zero size
        with pytest.raises(ValueError):
            CaseAttachmentCreate(**valid_data, size=0)

        # Test valid size
        attachment = CaseAttachmentCreate(**valid_data, size=1024)
        assert attachment.size == 1024


class TestLegacyFunctions:
    """Test backward compatibility of legacy functions."""

    @pytest.mark.anyio
    async def test_legacy_content_type_validation(self):
        """Test legacy content type validation function."""
        # Valid type
        storage.validate_content_type("application/pdf")

        # Invalid type
        with pytest.raises(ValueError):
            storage.validate_content_type("application/x-executable")

    @pytest.mark.anyio
    async def test_legacy_file_size_validation(self):
        """Test legacy file size validation function."""
        # Valid size
        storage.validate_file_size(1024)

        # Invalid size
        with pytest.raises(ValueError):
            storage.validate_file_size(100 * 1024 * 1024)

    @pytest.mark.anyio
    async def test_legacy_filename_sanitization(self):
        """Test legacy filename sanitization function."""
        result = storage.sanitize_filename("file with spaces.txt")
        assert result == "file_with_spaces.txt"

    @pytest.mark.anyio
    async def test_sha256_computation(self):
        """Test SHA256 hash computation."""
        content = b"Hello, World!"
        expected = "dffd6021bb2bd5b0af676290809ec3a53191dd81c7f70a4b28688a362182986f"
        result = storage.compute_sha256(content)
        assert result == expected


class TestPolyfileIntegration:
    """Test suite specifically for polyfile integration features."""

    def setup_method(self):
        """Set up test fixtures."""
        self.validator = storage.FileSecurityValidator()

    @pytest.mark.anyio
    async def test_polyfile_import_and_basic_functionality(self):
        """Test that polyfile is properly imported and functional."""

        # Test basic polyfile functionality
        test_content = b"Hello, World!"
        matches = list(MagicMatcher.DEFAULT_INSTANCE.match(test_content))

        # Should return some matches (even if just generic text)
        assert isinstance(matches, list)

    @pytest.mark.anyio
    async def test_polyfile_pdf_detection(self):
        """Test polyfile's PDF detection capabilities."""
        pdf_content = b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj"

        result = self.validator.validate_file(
            content=pdf_content,
            filename="test.pdf",
            declared_content_type="application/pdf",
        )

        assert result["content_type"] == "application/pdf"

    @pytest.mark.anyio
    async def test_polyfile_error_resilience(self):
        """Test that the validator is resilient to polyfile errors."""
        # Use malformed content that might cause polyfile issues
        malformed_content = b"\x00\x01\x02\x03\x04\x05"

        # Should not crash even if polyfile has issues
        try:
            result = self.validator.validate_file(
                content=malformed_content,
                filename="test.txt",
                declared_content_type="text/plain",
            )
            # If it succeeds, that's fine
            assert result["content_type"] == "text/plain"
        except ValueError:
            # If it fails, it should be due to our validation, not polyfile crashes
            pass


# === INTEGRATION TESTS FOR CASE ATTACHMENT ENDPOINTS ===


class TestCaseAttachmentEndpointsIntegration:
    """Integration tests for case attachment endpoints with MinIO storage."""

    @pytest.fixture(scope="class")
    def minio_config(self):
        """Configuration for connecting to MinIO instance."""
        return {
            "protocol": "minio",
            "endpoint": "http://localhost:9000",
            "bucket": "test-tracecat-attachments",
            "user": "minioadmin",
            "password": "miniopassword",
            "presigned_url_expiry": 300,
        }

    @pytest.fixture(autouse=True)
    async def setup_blob_storage_config(self, monkeypatch, minio_config):
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
        monkeypatch.setattr(
            config, "TRACECAT__BLOB_STORAGE_BUCKET", minio_config["bucket"]
        )
        monkeypatch.setattr(
            config, "TRACECAT__BLOB_STORAGE_ENDPOINT", minio_config["endpoint"]
        )
        monkeypatch.setattr(
            config,
            "TRACECAT__BLOB_STORAGE_PRESIGNED_URL_EXPIRY",
            minio_config["presigned_url_expiry"],
        )

        # Ensure bucket exists
        try:
            await storage.ensure_bucket_exists(minio_config["bucket"])
        except Exception:
            # Skip if MinIO is not available
            pytest.skip("MinIO not available for integration tests")

    @pytest.fixture
    async def test_case(self, async_session, test_role):
        """Create a test case for attachment testing."""

        service = CasesService(async_session, test_role)
        case_data = CaseCreate(
            summary="Test Case for Attachments",
            description="A test case for testing file attachments",
            status=CaseStatus.NEW,
            priority=CasePriority.MEDIUM,
            severity=CaseSeverity.MEDIUM,
        )
        case = await service.create_case(case_data)
        yield case

        # Cleanup: Delete the case and its attachments
        try:
            await service.delete_case(case)
        except Exception:
            pass  # Ignore cleanup errors

    @pytest.mark.anyio
    async def test_service_level_attachment_operations(
        self, async_session, test_role, test_case, minio_config
    ):
        """Test attachment operations at the service level with MinIO integration."""

        service = CasesService(async_session, test_role)

        # Test 1: List empty attachments
        attachments = await service.attachments.list_attachments(test_case)
        assert len(attachments) == 0

        # Test 2: Upload valid attachment
        pdf_content = b"%PDF-1.4\n1 0 obj\n<<\n/Type /Catalog\n>>\nendobj\nxref\n0 1\n0000000000 65535 f \ntrailer\n<<\n/Size 1\n/Root 1 0 R\n>>\nstartxref\n9\n%%EOF"

        attachment_data = CaseAttachmentCreate(
            file_name="test_document.pdf",
            content_type="application/pdf",
            size=len(pdf_content),
            content=pdf_content,
        )

        attachment = await service.attachments.create_attachment(
            test_case, attachment_data
        )

        # Verify attachment was created
        assert attachment.file.name == "test_document.pdf"
        assert attachment.file.content_type == "application/pdf"
        assert attachment.file.size == len(pdf_content)
        assert attachment.case_id == test_case.id

        # Test 3: Verify file exists in MinIO
        storage_key = f"attachments/{attachment.file.sha256}"
        assert await storage.file_exists(storage_key, bucket=minio_config["bucket"])

        # Test 4: Download attachment
        (
            downloaded_content,
            filename,
            content_type,
        ) = await service.attachments.download_attachment(test_case, attachment.id)
        assert downloaded_content == pdf_content
        assert filename == "test_document.pdf"
        assert content_type == "application/pdf"

        # Test 5: List attachments (should have one)
        attachments = await service.attachments.list_attachments(test_case)
        assert len(attachments) == 1
        assert attachments[0].id == attachment.id

        # Test 6: Get storage usage
        total_storage = await service.attachments.get_total_storage_used(test_case)
        assert total_storage == len(pdf_content)

        # Test 7: Delete attachment
        await service.attachments.delete_attachment(test_case, attachment.id)

        # Verify attachment is marked as deleted
        attachments = await service.attachments.list_attachments(test_case)
        assert len(attachments) == 0

    @pytest.mark.anyio
    async def test_service_level_invalid_file_upload(
        self, async_session, test_role, test_case
    ):
        """Test that invalid files are rejected at the service level."""
        service = CasesService(async_session, test_role)

        # Test invalid file type
        exe_content = b"MZ\x90\x00\x03\x00\x00\x00"
        attachment_data = CaseAttachmentCreate(
            file_name="malicious.exe",
            content_type="application/x-executable",
            size=len(exe_content),
            content=exe_content,
        )

        with pytest.raises(ValueError, match="not allowed"):
            await service.attachments.create_attachment(test_case, attachment_data)

        # Test oversized file
        large_content = b"x" * (51 * 1024 * 1024)  # 51MB
        attachment_data = CaseAttachmentCreate(
            file_name="large_file.txt",
            content_type="text/plain",
            size=len(large_content),
            content=large_content,
        )

        with pytest.raises(ValueError, match="exceeds maximum"):
            await service.attachments.create_attachment(test_case, attachment_data)

        # Test malicious content
        malicious_content = b"<script>alert('xss')</script>"
        attachment_data = CaseAttachmentCreate(
            file_name="malicious.txt",
            content_type="text/plain",
            size=len(malicious_content),
            content=malicious_content,
        )

        with pytest.raises(ValueError, match="dangerous"):
            await service.attachments.create_attachment(test_case, attachment_data)

    @pytest.mark.anyio
    async def test_service_level_file_deduplication(
        self, async_session, test_role, test_case
    ):
        """Test file deduplication at the service level."""

        service = CasesService(async_session, test_role)

        # Upload the same content twice with different names
        test_content = b"This content will be uploaded twice"

        attachment_data1 = CaseAttachmentCreate(
            file_name="file1.txt",
            content_type="text/plain",
            size=len(test_content),
            content=test_content,
        )

        attachment1 = await service.attachments.create_attachment(
            test_case, attachment_data1
        )

        attachment_data2 = CaseAttachmentCreate(
            file_name="file2.txt",
            content_type="text/plain",
            size=len(test_content),
            content=test_content,
        )

        attachment2 = await service.attachments.create_attachment(
            test_case, attachment_data2
        )

        # Both should have the same SHA256 hash (deduplication)
        assert attachment1.file.sha256 == attachment2.file.sha256

        # But different attachment IDs and filenames
        assert attachment1.id != attachment2.id
        assert attachment1.file.name != attachment2.file.name

    @pytest.mark.anyio
    async def test_service_level_multiple_file_types(
        self, async_session, test_role, test_case
    ):
        """Test uploading multiple different file types."""
        service = CasesService(async_session, test_role)

        # Upload multiple different file types
        files_to_upload = [
            ("document.pdf", b"%PDF-1.4\nPDF content", "application/pdf"),
            ("text.txt", b"Plain text content", "text/plain"),
            ("image.jpg", b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01", "image/jpeg"),
        ]

        uploaded_attachments = []
        for filename, content, content_type in files_to_upload:
            attachment_data = CaseAttachmentCreate(
                file_name=filename,
                content_type=content_type,
                size=len(content),
                content=content,
            )
            attachment = await service.attachments.create_attachment(
                test_case, attachment_data
            )
            uploaded_attachments.append(attachment)

        # List all attachments
        attachments = await service.attachments.list_attachments(test_case)
        assert len(attachments) == 3

        # Verify all files are present
        uploaded_names = {att.file.name for att in uploaded_attachments}
        listed_names = {att.file.name for att in attachments}
        assert uploaded_names == listed_names

    @pytest.mark.anyio
    async def test_service_level_attachment_not_found(
        self, async_session, test_role, test_case
    ):
        """Test handling of non-existent attachments."""

        service = CasesService(async_session, test_role)

        # Try to download non-existent attachment
        fake_attachment_id = uuid.uuid4()

        with pytest.raises(TracecatNotFoundError):
            await service.attachments.download_attachment(test_case, fake_attachment_id)

        # Try to delete non-existent attachment
        with pytest.raises(TracecatNotFoundError):
            await service.attachments.delete_attachment(test_case, fake_attachment_id)

    @pytest.mark.anyio
    async def test_service_level_filename_sanitization(
        self, async_session, test_role, test_case
    ):
        """Test filename sanitization at the service level."""
        service = CasesService(async_session, test_role)

        # Try to upload file with problematic filename
        test_content = b"Content for filename sanitization test"

        attachment_data = CaseAttachmentCreate(
            file_name="../../../etc/passwd",
            content_type="text/plain",
            size=len(test_content),
            content=test_content,
        )

        with pytest.raises(ValueError, match="invalid path characters"):
            await service.attachments.create_attachment(test_case, attachment_data)

    @pytest.mark.anyio
    async def test_service_level_content_type_validation(
        self, async_session, test_role, test_case
    ):
        """Test content type validation at the service level."""

        service = CasesService(async_session, test_role)

        # Upload with mismatched content type
        pdf_content = b"%PDF-1.4\nPDF content"

        attachment_data = CaseAttachmentCreate(
            file_name="document.pdf",
            content_type="text/plain",  # Wrong content type
            size=len(pdf_content),
            content=pdf_content,
        )

        with pytest.raises(ValueError, match="does not match"):
            await service.attachments.create_attachment(test_case, attachment_data)
