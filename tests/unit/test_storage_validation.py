"""Security tests for the storage module."""

from unittest.mock import MagicMock, patch

import pytest

from tracecat.storage.validation import (
    ALLOWED_CONTENT_TYPES,
    ALLOWED_EXTENSIONS,
    BLOCKED_CONTENT_TYPES,
    BLOCKED_EXTENSIONS,
    MAX_FILE_SIZE,
    FileContentMismatchError,
    FileSecurityError,
    FileSecurityValidator,
)


class TestFileSecurityValidator:
    """Test the FileSecurityValidator class."""

    def setup_method(self):
        """Set up test fixtures."""
        self.validator = FileSecurityValidator()

    def test_validate_file_size_valid(self):
        """Test valid file size validation."""
        # Should not raise for valid sizes
        self.validator._validate_file_size(1024)  # 1KB
        self.validator._validate_file_size(1024 * 1024)  # 1MB
        self.validator._validate_file_size(MAX_FILE_SIZE - 1)  # Just under limit

    def test_validate_file_size_invalid(self):
        """Test invalid file size validation."""
        # Empty file
        with pytest.raises(ValueError, match="File cannot be empty"):
            self.validator._validate_file_size(0)

        # Too large
        with pytest.raises(ValueError, match="exceeds maximum allowed size"):
            self.validator._validate_file_size(MAX_FILE_SIZE + 1)

    def test_validate_filename_safety_valid(self):
        """Test valid filename safety validation."""
        valid_names = [
            "document.pdf",
            "my-file_v2.docx",
            "data.2023.csv",
            "image.jpg",
            "archive.zip",
        ]
        for filename in valid_names:
            self.validator._validate_filename_safety(filename)

    def test_validate_filename_safety_invalid(self):
        """Test invalid filename safety validation."""
        invalid_cases = [
            ("", "Filename cannot be empty"),
            ("   ", "Filename cannot be empty"),
            ("../../../etc/passwd", "invalid path characters"),
            ("..\\windows\\system32", "invalid path characters"),
            ("file/with/slashes.txt", "invalid path characters"),
            ("file\\with\\backslashes.txt", "invalid path characters"),
            ("file\x00with\x01nulls.txt", "invalid control characters"),
            ("a" * 300 + ".txt", "Filename too long"),
        ]

        for filename, expected_error in invalid_cases:
            with pytest.raises(ValueError, match=expected_error):
                self.validator._validate_filename_safety(filename)

    def test_extract_extension(self):
        """Test file extension extraction."""
        test_cases = [
            ("document.pdf", ".pdf"),
            ("archive.tar.gz", ".gz"),
            ("FILE.TXT", ".txt"),  # Should normalize to lowercase
            ("no_extension", ""),
            ("multiple.dots.in.name.docx", ".docx"),
        ]

        for filename, expected in test_cases:
            assert self.validator._extract_extension(filename) == expected

    def test_validate_extension_valid(self):
        """Test valid extension validation."""
        valid_extensions = [".pdf", ".docx", ".jpg", ".png", ".csv", ".txt"]
        for ext in valid_extensions:
            self.validator._validate_extension(ext)

    def test_validate_extension_invalid(self):
        """Test invalid extension validation."""
        # Blocked extensions
        for ext in BLOCKED_EXTENSIONS:
            with pytest.raises(ValueError, match="not allowed for security reasons"):
                self.validator._validate_extension(ext)

        # Unsupported extension
        with pytest.raises(ValueError, match="not supported"):
            self.validator._validate_extension(".unsupported")

    def test_validate_declared_content_type_valid(self):
        """Test valid content type validation."""
        valid_types = [
            "application/pdf",
            "image/jpeg",
            "text/plain; charset=utf-8",  # With parameters
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ]

        for content_type in valid_types:
            self.validator._validate_declared_content_type(content_type)

    def test_validate_declared_content_type_invalid(self):
        """Test invalid content type validation."""
        # Empty content type
        with pytest.raises(ValueError, match="Content-Type header is required"):
            self.validator._validate_declared_content_type("")

        # Blocked content types
        for blocked_type in BLOCKED_CONTENT_TYPES:
            with pytest.raises(ValueError, match="not allowed for security reasons"):
                self.validator._validate_declared_content_type(blocked_type)

        # Unsupported content type
        with pytest.raises(ValueError, match="not supported"):
            self.validator._validate_declared_content_type("application/unsupported")

    def test_detect_file_type_by_magic(self):
        """Test magic number detection."""
        test_cases = [
            (b"%PDF-1.4\x0a\x00\x00\x00", "application/pdf"),  # Padded to 8+ bytes
            (b"\xff\xd8\xff\xe0\x00\x10JFIF", "image/jpeg"),  # Full JPEG header
            (b"\x89PNG\x0d\x0a\x1a\x0a", "image/png"),
            (b"GIF87a\x00\x00", "image/gif"),  # Padded to 8 bytes
            (b"GIF89a\x00\x00", "image/gif"),  # Padded to 8 bytes
            (b"PK\x03\x04\x00\x00\x00\x00", "application/zip"),  # Padded to 8 bytes
            (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", "application/msword"),
            (b"unknown_content", None),  # Changed to 8+ bytes
            (b"short", None),  # Less than 8 bytes should return None
        ]

        for content, expected in test_cases:
            result = self.validator._detect_file_type_by_magic(content)
            assert result == expected

    def test_detect_file_type_by_magic_webp(self):
        """Test WebP magic number detection with proper validation."""
        # Valid WebP
        webp_content = b"RIFF\x00\x00\x00\x00WEBP"
        result = self.validator._detect_file_type_by_magic(webp_content)
        assert result == "image/webp"

        # RIFF but not WebP
        riff_content = b"RIFF\x00\x00\x00\x00WAVE"
        result = self.validator._detect_file_type_by_magic(riff_content)
        assert result is None

    def test_cross_validate_file_type_valid(self):
        """Test valid cross-validation of file types."""
        test_cases = [
            (".pdf", "application/pdf", "application/pdf"),
            (".jpg", "image/jpeg", "image/jpeg"),
            (
                ".docx",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                "application/zip",
            ),
            (".txt", "text/plain", None),
        ]

        for extension, declared_type, detected_type in test_cases:
            result = self.validator._cross_validate_file_type(
                extension, declared_type, detected_type
            )
            expected_type = ALLOWED_EXTENSIONS[extension]
            assert result == expected_type

    def test_cross_validate_file_type_invalid(self):
        """Test invalid cross-validation scenarios."""
        # Extension doesn't match detected type
        with pytest.raises(ValueError, match="does not match extension"):
            self.validator._cross_validate_file_type(
                ".pdf", "application/pdf", "image/jpeg"
            )

        # Declared type doesn't match extension
        with pytest.raises(ValueError, match="does not match file extension"):
            self.validator._cross_validate_file_type(
                ".pdf", "image/jpeg", "application/pdf"
            )

        # Unsupported extension
        with pytest.raises(ValueError, match="Unsupported file extension"):
            self.validator._cross_validate_file_type(".unsupported", "text/plain", None)

    def test_check_for_embedded_threats(self):
        """Test detection of embedded threats."""
        # Safe content for text files
        safe_content = b"This is safe text content"
        self.validator._check_for_embedded_threats(safe_content, "text/plain")

        # Test text/document content with dangerous patterns
        dangerous_contents = [
            b"MZ\x90\x00",  # PE executable
            b"\x7fELF",  # ELF executable
            b"<script>alert('xss')</script>",  # JavaScript
            b"<?php echo 'hello'; ?>",  # PHP
            b"#!/bin/bash\necho 'script'",  # Shell script
        ]

        for content in dangerous_contents:
            with pytest.raises(
                ValueError, match="potentially dangerous embedded content"
            ):
                self.validator._check_for_embedded_threats(content, "text/plain")

        # Test that image files only check for executable signatures at the start
        image_content_safe = b"\xff\xd8\xff\xe0<script>alert('xss')</script>"
        # This should NOT raise an error for images since script checking is skipped
        self.validator._check_for_embedded_threats(image_content_safe, "image/jpeg")

        # But executable signatures at the start should still be caught for images
        image_with_executable = b"MZ\x90\x00\xff\xd8\xff\xe0"
        with pytest.raises(ValueError, match="File contains executable content"):
            self.validator._check_for_embedded_threats(
                image_with_executable, "image/jpeg"
            )

    def test_validate_pdf_content(self):
        """Test PDF content validation."""
        # Valid PDF
        valid_pdf = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3"
        self.validator._validate_pdf_content(valid_pdf)

        # Invalid PDF header
        with pytest.raises(
            FileContentMismatchError, match="Invalid PDF file structure"
        ):
            self.validator._validate_pdf_content(b"not a pdf")

        # PDF with JavaScript and dangerous action
        pdf_with_js_and_action = b"%PDF-1.4\n/JavaScript (alert('xss'))\n/OpenAction"
        with pytest.raises(FileSecurityError, match="PDF contains JavaScript action"):
            self.validator._validate_pdf_content(pdf_with_js_and_action)

        # PDF with only JavaScript (should not raise)
        pdf_with_js_only = b"%PDF-1.4\n/JavaScript (alert('xss'))"
        self.validator._validate_pdf_content(pdf_with_js_only)  # Should not raise

        # PDF with dangerous action without JavaScript
        pdf_with_launch = b"%PDF-1.4\n/Launch /F (malicious.exe)"
        with pytest.raises(FileSecurityError, match="PDF contains JavaScript action"):
            self.validator._validate_pdf_content(pdf_with_launch)

    def test_validate_image_content(self):
        """Test image content validation."""
        # Safe image content
        safe_image = b"\xff\xd8\xff\xe0\x00\x10JFIF"
        self.validator._validate_image_content(safe_image)

        # Image with embedded script - this should now pass since we don't check for scripts in images
        # The script checking is handled by the more selective _check_for_embedded_threats method
        image_with_script = b"\xff\xd8\xff\xe0<script>alert('xss')</script>"
        self.validator._validate_image_content(image_with_script)  # Should not raise

        # Test SVG detection and validation
        with pytest.raises(
            FileSecurityError, match="SVG file contains potentially dangerous content"
        ):
            # SVG with script tag
            svg_with_script = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert("XSS")</script></svg>'
            self.validator._validate_image_content(svg_with_script)

        # SVG with event handler
        svg_with_event = b'<svg xmlns="http://www.w3.org/2000/svg"><circle onclick="alert(1)"/></svg>'
        with pytest.raises(
            FileSecurityError, match="SVG file contains potentially dangerous content"
        ):
            self.validator._validate_image_content(svg_with_event)

        # SVG with JavaScript URL
        svg_with_js_url = b'<svg xmlns="http://www.w3.org/2000/svg"><a href="javascript:alert(1)"/></svg>'
        with pytest.raises(
            FileSecurityError, match="SVG file contains potentially dangerous content"
        ):
            self.validator._validate_image_content(svg_with_js_url)

    def test_validate_text_content(self):
        """Test text content validation."""
        # Safe text content
        safe_text = b"This is safe text content\nwith multiple lines"
        self.validator._validate_text_content(safe_text)

        # Text with dangerous patterns
        dangerous_texts = [
            b"<script>alert('xss')</script>",
            b"javascript:alert('xss')",
            b"<?php echo 'hello'; ?>",
            b"#!/bin/bash",
        ]

        for content in dangerous_texts:
            with pytest.raises(ValueError, match="potentially dangerous content"):
                self.validator._validate_text_content(content)

        # Invalid UTF-8
        with pytest.raises(ValueError, match="invalid UTF-8 encoding"):
            self.validator._validate_text_content(b"\xff\xfe")

    @patch("tracecat.storage.MagicMatcher")
    def test_validate_file_complete_flow(self, mock_magic_matcher):
        """Test complete file validation flow."""
        # Mock polyfile analysis
        mock_instance = MagicMock()
        mock_magic_matcher.DEFAULT_INSTANCE = mock_instance
        mock_instance.match.return_value = []

        # Valid PDF file
        pdf_content = b"%PDF-1.4\n" + b"A" * 1000
        result = self.validator.validate_file(
            content=pdf_content,
            filename="document.pdf",
            declared_content_type="application/pdf",
        )

        assert result["filename"] == "document.pdf"
        assert result["content_type"] == "application/pdf"

    def test_validate_file_security_failure(self):
        """Test file validation with security failures."""
        # File too large
        large_content = b"A" * (MAX_FILE_SIZE + 1)
        with pytest.raises(ValueError, match="exceeds maximum allowed size"):
            self.validator.validate_file(
                content=large_content,
                filename="large.txt",
                declared_content_type="text/plain",
            )

        # Dangerous extension
        with pytest.raises(ValueError, match="not allowed for security reasons"):
            self.validator.validate_file(
                content=b"content",
                filename="malware.exe",
                declared_content_type="application/x-executable",
            )

    def test_empty_file_content(self):
        """Test handling of empty file content."""
        with pytest.raises(ValueError, match="File cannot be empty"):
            self.validator.validate_file(
                content=b"", filename="empty.txt", declared_content_type="text/plain"
            )

    def test_unicode_filename(self):
        """Test handling of Unicode filenames."""
        # Fixed: \w includes Unicode word characters, so they're preserved
        result = self.validator._sanitize_filename("测试文件.txt")
        assert result == "测试文件.txt"  # Unicode chars are preserved by \w regex

    def test_very_long_filename(self):
        """Test handling of very long filenames."""
        long_name = "a" * 300 + ".txt"
        result = self.validator._sanitize_filename(long_name)
        assert len(result) <= 255
        assert result.endswith(".txt")

    def test_filename_sanitization_security(self):
        """Test that filename sanitization removes security risks."""
        # Test various security-problematic filenames
        test_cases = [
            # Control characters
            ("file\x00name.txt", "filename.txt"),
            ("file\x01\x02name.txt", "filename.txt"),
            # Unicode normalization attacks
            ("file\u202ename.txt", "filename.txt"),  # Right-to-left override
            # Long filename attack
            ("A" * 300 + ".txt", "A" * (255 - 4) + ".txt"),
            # Hidden file attempts
            ("...hidden.txt", "hidden.txt"),
        ]

        for malicious_name, expected_safe in test_cases:
            result = self.validator._sanitize_filename(malicious_name)
            assert result == expected_safe
            assert len(result) <= 255
            assert ".." not in result


class TestSecurityConfiguration:
    """Test security configuration constants."""

    def test_allowed_content_types_coverage(self):
        """Test that we have good coverage of allowed content types."""
        # Should include common document types
        assert "application/pdf" in ALLOWED_CONTENT_TYPES
        assert "text/plain" in ALLOWED_CONTENT_TYPES
        assert "text/csv" in ALLOWED_CONTENT_TYPES

        # Should include common image types
        assert "image/jpeg" in ALLOWED_CONTENT_TYPES
        assert "image/png" in ALLOWED_CONTENT_TYPES

        # Should include Office documents
        assert (
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            in ALLOWED_CONTENT_TYPES
        )
        assert (
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            in ALLOWED_CONTENT_TYPES
        )

    def test_blocked_content_types_security(self):
        """Test that dangerous content types are blocked."""
        dangerous_types = [
            "application/x-executable",
            "application/javascript",
            "text/html",
            "application/x-sh",
            "application/x-msdownload",
        ]

        for dangerous_type in dangerous_types:
            assert dangerous_type in BLOCKED_CONTENT_TYPES

    def test_blocked_extensions_security(self):
        """Test that dangerous extensions are blocked."""
        dangerous_extensions = [
            ".exe",
            ".bat",
            ".cmd",
            ".js",
            ".php",
            ".py",
            ".sh",
            ".dll",
            ".svg",  # SVG should be blocked due to XSS risk
            ".html",
            ".htm",
        ]

        for ext in dangerous_extensions:
            assert ext in BLOCKED_EXTENSIONS

    def test_no_overlap_allowed_blocked(self):
        """Test that there's no overlap between allowed and blocked types."""
        # Convert to base types (remove parameters)
        allowed_base = {ct.split(";")[0].strip() for ct in ALLOWED_CONTENT_TYPES}
        blocked_base = {ct.split(";")[0].strip() for ct in BLOCKED_CONTENT_TYPES}

        overlap = allowed_base.intersection(blocked_base)
        assert len(overlap) == 0, (
            f"Found overlap between allowed and blocked types: {overlap}"
        )


class TestSecurityHardening:
    """Test security hardening features for blob storage."""

    def test_file_validation_against_malicious_content(self):
        """Test comprehensive file validation against various attack vectors."""
        validator = FileSecurityValidator()

        # Test various malicious file types that should be blocked
        malicious_files = [
            # Executable files
            (b"MZ\x90\x00" + b"A" * 100, "malware.exe", "application/x-executable"),
            # Script files
            (b"#!/bin/bash\nrm -rf /", "script.sh", "application/x-sh"),
            # HTML with JavaScript
            (b"<script>alert('xss')</script>", "malicious.html", "text/html"),
            # PHP files
            (
                b"<?php system($_GET['cmd']); ?>",
                "webshell.php",
                "application/x-httpd-php",
            ),
        ]

        for content, filename, content_type in malicious_files:
            with pytest.raises(ValueError):
                validator.validate_file(
                    content=content,
                    filename=filename,
                    declared_content_type=content_type,
                )

    def test_content_type_spoofing_protection(self):
        """Test protection against content type spoofing attacks."""
        validator = FileSecurityValidator()

        # Test executable content with innocent content type
        executable_content = b"MZ\x90\x00" + b"A" * 100

        with pytest.raises(ValueError, match="File contains executable content"):
            validator.validate_file(
                content=executable_content,
                filename="document.pdf",  # Claiming to be PDF
                declared_content_type="application/pdf",
            )

    def test_filename_traversal_protection(self):
        """Test protection against directory traversal attacks."""
        validator = FileSecurityValidator()

        # Test various path traversal attempts
        malicious_filenames = [
            "../../../etc/passwd",
            "..\\..\\..\\windows\\system32\\config\\sam",
            "file/../../sensitive.txt",
            ".\\..\\sensitive.txt",
        ]

        for filename in malicious_filenames:
            with pytest.raises(ValueError, match="invalid path characters"):
                validator.validate_file(
                    content=b"content",
                    filename=filename,
                    declared_content_type="text/plain",
                )

    def test_file_size_limits_enforcement(self):
        """Test that file size limits are strictly enforced."""
        validator = FileSecurityValidator()

        # Test at boundary conditions
        max_size_content = b"A" * MAX_FILE_SIZE
        validator._validate_file_size(len(max_size_content))  # Should pass

        # Test over limit
        oversized_content = b"A" * (MAX_FILE_SIZE + 1)
        with pytest.raises(ValueError, match="exceeds maximum allowed size"):
            validator._validate_file_size(len(oversized_content))

    def test_content_validation_for_zip_bombs(self):
        """Test file size limits prevent zip bomb attacks."""
        validator = FileSecurityValidator()

        # Test that oversized files are rejected (this prevents zip bombs)
        # Zip bombs are primarily a decompression attack, so file size limits help
        oversized_zip = b"PK\x03\x04" + b"A" * (MAX_FILE_SIZE + 1)

        with pytest.raises(ValueError, match="exceeds maximum allowed size"):
            validator.validate_file(
                content=oversized_zip,
                filename="archive.zip",
                declared_content_type="application/zip",
            )
