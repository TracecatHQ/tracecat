"""Storage utilities for handling file uploads and downloads with S3."""

import hashlib
import os
import re

import aioboto3
from botocore.exceptions import ClientError
from polyfile.magic import MagicMatcher

from tracecat import config
from tracecat.logger import logger


# File validation exception classes
class FileValidationError(ValueError):
    """Base class for file validation errors."""

    pass


class FileSizeError(FileValidationError):
    """Raised when file size exceeds limits."""

    pass


class FileExtensionError(FileValidationError):
    """Raised when file extension is not allowed."""

    def __init__(self, message: str, extension: str, allowed_extensions: list[str]):
        super().__init__(message)
        self.extension = extension
        self.allowed_extensions = allowed_extensions


class FileContentTypeError(FileValidationError):
    """Raised when file content type is not allowed."""

    def __init__(self, message: str, content_type: str, allowed_types: list[str]):
        super().__init__(message)
        self.content_type = content_type
        self.allowed_types = allowed_types


class FileSecurityError(FileValidationError):
    """Raised when file contains security threats."""

    pass


class FileContentMismatchError(FileValidationError):
    """Raised when file content doesn't match extension/declared type."""

    pass


class FileNameError(FileValidationError):
    """Raised when filename is invalid or unsafe."""

    pass


class MaxAttachmentsExceededError(FileValidationError):
    """Raised when maximum number of attachments per case is exceeded."""

    def __init__(self, message: str, current_count: int, max_count: int):
        super().__init__(message)
        self.current_count = current_count
        self.max_count = max_count


class StorageLimitExceededError(FileValidationError):
    """Raised when adding a file would exceed the case storage limit."""

    def __init__(
        self, message: str, current_size: int, new_file_size: int, max_size: int
    ):
        super().__init__(message)
        self.current_size = current_size
        self.new_file_size = new_file_size
        self.max_size = max_size


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

# File size limits - using config values
MAX_FILE_SIZE = config.TRACECAT__MAX_ATTACHMENT_SIZE_BYTES
MAX_FILENAME_LENGTH = config.TRACECAT__MAX_ATTACHMENT_FILENAME_LENGTH

# Magic number signatures for file type validation
# Based on https://en.wikipedia.org/wiki/List_of_file_signatures
MAGIC_SIGNATURES = {
    # PDF
    b"\x25\x50\x44\x46": "application/pdf",
    # JPEG
    b"\xff\xd8\xff": "image/jpeg",
    # PNG
    b"\x89\x50\x4e\x47\x0d\x0a\x1a\x0a": "image/png",
    # GIF87a
    b"\x47\x49\x46\x38\x37\x61": "image/gif",
    # GIF89a
    b"\x47\x49\x46\x38\x39\x61": "image/gif",
    # WebP
    b"\x52\x49\x46\x46": "image/webp",  # Note: needs additional validation
    # ZIP (includes DOCX, XLSX)
    b"\x50\x4b\x03\x04": "application/zip",
    b"\x50\x4b\x05\x06": "application/zip",  # Empty ZIP
    b"\x50\x4b\x07\x08": "application/zip",  # Spanned ZIP
    # 7-Zip
    b"\x37\x7a\xbc\xaf\x27\x1c": "application/x-7z-compressed",
    # MS Office (legacy)
    b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1": "application/msword",  # Also XLS
}

# Allowed file extensions mapped to content types
ALLOWED_EXTENSIONS = {
    ".pdf": "application/pdf",
    ".doc": "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xls": "application/vnd.ms-excel",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".txt": "text/plain",
    ".csv": "text/csv",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".zip": "application/zip",
    ".7z": "application/x-7z-compressed",
    # Note: SVG is intentionally excluded due to XSS risks
}

# Dangerous extensions that should never be allowed
BLOCKED_EXTENSIONS = {
    ".exe",
    ".bat",
    ".cmd",
    ".com",
    ".pif",
    ".scr",
    ".vbs",
    ".js",
    ".jar",
    ".php",
    ".php3",
    ".php4",
    ".php5",
    ".pl",
    ".py",
    ".rb",
    ".sh",
    ".cgi",
    ".asp",
    ".aspx",
    ".jsp",
    ".war",
    ".ear",
    ".dll",
    ".so",
    ".dylib",
    ".app",
    ".deb",
    ".rpm",
    ".dmg",
    ".pkg",
    ".msi",
    ".apk",
    ".ipa",
    ".html",
    ".htm",
    ".svg",  # Blocked due to XSS risk via embedded scripts
    ".xml",
    ".xsl",
    ".xslt",
}


class FileSecurityValidator:
    """Comprehensive file security validator implementing OWASP recommendations with polyfile integration."""

    def __init__(self):
        self.max_file_size = config.TRACECAT__MAX_ATTACHMENT_SIZE_BYTES
        self.max_filename_length = config.TRACECAT__MAX_ATTACHMENT_FILENAME_LENGTH

    def validate_file(
        self,
        content: bytes,
        filename: str,
        declared_content_type: str,
    ) -> dict[str, str]:
        """Perform comprehensive file validation with polyfile analysis.

        Args:
            content: File content as bytes
            filename: Original filename
            declared_content_type: Content-Type header from upload

        Returns:
            Dict with validated filename and content_type

        Raises:
            ValueError: If any validation fails
        """
        # 1. Basic validations
        self._validate_file_size(len(content))
        self._validate_filename_safety(filename)

        # 2. Extension validation
        extension = self._extract_extension(filename)
        self._validate_extension(extension)

        # 3. Content-Type validation
        self._validate_declared_content_type(declared_content_type)

        # 4. Magic number validation
        detected_type = self._detect_file_type_by_magic(content)

        # 5. Cross-validation between extension, declared type, and magic number
        validated_type = self._cross_validate_file_type(
            extension, declared_content_type, detected_type
        )

        # 6. Enhanced polyfile analysis
        self._analyze_with_polyfile(content, validated_type, filename)

        # 7. Content analysis for additional security
        self._analyze_file_content(content, validated_type)

        # 8. Sanitize filename
        sanitized_filename = self._sanitize_filename(filename)

        return {
            "filename": sanitized_filename,
            "content_type": validated_type,
        }

    def _validate_file_size(self, size: int) -> None:
        """Validate file size constraints."""
        if size <= 0:
            raise FileSizeError("File cannot be empty")

        if size > self.max_file_size:
            raise FileSizeError(
                f"File size ({size / 1024 / 1024:.1f}MB) exceeds maximum allowed size "
                f"({self.max_file_size / 1024 / 1024}MB)"
            )

    def _validate_filename_safety(self, filename: str) -> None:
        """Validate filename for security issues."""
        if not filename or not filename.strip():
            raise FileNameError("Filename cannot be empty")

        if len(filename) > self.max_filename_length:
            raise FileNameError(
                f"Filename too long (max {self.max_filename_length} characters)"
            )

        # Check for directory traversal attempts (but allow multiple dots in filename)
        if (
            "../" in filename
            or "..\\" in filename
            or "/" in filename
            or "\\" in filename
        ):
            raise FileNameError("Filename contains invalid path characters")

        # Check for null bytes and control characters
        if any(ord(c) < 32 for c in filename if c != "\t"):
            raise FileNameError("Filename contains invalid control characters")

    def _extract_extension(self, filename: str) -> str:
        """Extract and normalize file extension."""
        _, ext = os.path.splitext(filename.lower())
        return ext

    def _validate_extension(self, extension: str) -> None:
        """Validate file extension against allow/block lists."""
        if extension in BLOCKED_EXTENSIONS:
            raise FileExtensionError(
                f"File extension '{extension}' is not allowed for security reasons",
                extension=extension,
                allowed_extensions=list(ALLOWED_EXTENSIONS.keys()),
            )

        if extension not in ALLOWED_EXTENSIONS:
            allowed_exts = sorted(ALLOWED_EXTENSIONS.keys())
            raise FileExtensionError(
                f"File extension '{extension}' is not supported. Allowed: {', '.join(allowed_exts)}",
                extension=extension,
                allowed_extensions=allowed_exts,
            )

    def _validate_declared_content_type(self, content_type: str) -> None:
        """Validate the declared Content-Type header."""
        if not content_type:
            raise FileContentTypeError(
                "Content-Type header is required",
                content_type="",
                allowed_types=list(ALLOWED_CONTENT_TYPES),
            )

        # Normalize content type (remove parameters like charset)
        base_content_type = content_type.split(";")[0].strip().lower()

        if base_content_type in BLOCKED_CONTENT_TYPES:
            raise FileContentTypeError(
                f"Content type '{base_content_type}' is not allowed for security reasons",
                content_type=base_content_type,
                allowed_types=list(ALLOWED_CONTENT_TYPES),
            )

        if base_content_type not in ALLOWED_CONTENT_TYPES:
            allowed_types = sorted(ALLOWED_CONTENT_TYPES)
            raise FileContentTypeError(
                f"Content type '{base_content_type}' is not supported. Allowed: {', '.join(allowed_types)}",
                content_type=base_content_type,
                allowed_types=allowed_types,
            )

    def _detect_file_type_by_magic(self, content: bytes) -> str | None:
        """Detect file type using magic number signatures."""
        if len(content) < 8:
            return None

        # Check magic signatures
        for signature, content_type in MAGIC_SIGNATURES.items():
            if content.startswith(signature):
                # Special case for WebP - needs additional validation
                if content_type == "image/webp":
                    if len(content) >= 12 and content[8:12] == b"WEBP":
                        return content_type
                    # If RIFF but not WEBP, it's not a valid WebP
                    elif signature == b"\x52\x49\x46\x46":
                        continue
                return content_type

        # Additional checks for Office documents (ZIP-based)
        if content.startswith(b"\x50\x4b"):
            return self._detect_office_format(content)

        return None

    def _detect_office_format(self, content: bytes) -> str:
        """Detect specific Office format from ZIP-based files."""
        # This is a simplified detection - in production, you might want
        # to use a proper ZIP parser to check internal structure
        if b"word/" in content[:1024]:
            return "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        elif b"xl/" in content[:1024]:
            return "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        else:
            return "application/zip"

    def _cross_validate_file_type(
        self, extension: str, declared_type: str, detected_type: str | None
    ) -> str:
        """Cross-validate file type from multiple sources."""
        # Get expected type from extension
        expected_from_ext = ALLOWED_EXTENSIONS.get(extension)
        if not expected_from_ext:
            raise FileExtensionError(
                f"Unsupported file extension: {extension}",
                extension=extension,
                allowed_extensions=list(ALLOWED_EXTENSIONS.keys()),
            )

        # Normalize declared type
        declared_base = declared_type.split(";")[0].strip().lower()

        # If we detected a type via magic numbers, validate consistency
        if detected_type:
            # Special handling for Office documents
            if extension in [".docx", ".xlsx"] and detected_type in [
                "application/zip",
                expected_from_ext,
            ]:
                return expected_from_ext

            # For other files, magic number should match expected type
            if detected_type != expected_from_ext:
                raise FileContentMismatchError(
                    f"File content (detected: {detected_type}) does not match "
                    f"extension {extension} (expected: {expected_from_ext})"
                )

        # Validate declared type matches extension
        if declared_base != expected_from_ext:
            # Allow some flexibility for common variations
            if not self._is_compatible_type(declared_base, expected_from_ext):
                raise FileContentMismatchError(
                    f"Declared content type '{declared_base}' does not match "
                    f"file extension {extension} (expected: {expected_from_ext})"
                )

        return expected_from_ext

    def _is_compatible_type(self, declared: str, expected: str) -> bool:
        """Check if declared type is compatible with expected type."""
        # Allow some common variations
        compatible_types = {
            "text/csv": ["text/plain"],
            "image/jpeg": ["image/jpg"],
        }

        return declared in compatible_types.get(expected, [])

    def _analyze_with_polyfile(
        self, content: bytes, content_type: str, filename: str
    ) -> None:
        """Perform advanced file analysis using polyfile.

        Args:
            content: File content as bytes
            content_type: Validated content type
            filename: Original filename

        Raises:
            ValueError: If polyfile detects security issues
        """
        try:
            # Analyze the file content directly with polyfile's MagicMatcher
            matches = list(MagicMatcher.DEFAULT_INSTANCE.match(content))

            # Check for polyglot files (files that are valid in multiple formats)
            self._check_polyglot_threats(matches, content_type)

            # Analyze file structure for anomalies
            self._analyze_file_structure(matches, content_type)

            # Check for embedded files or suspicious content
            self._check_embedded_content(matches, content)

        except Exception as e:
            # Log the error but don't fail validation unless it's a security issue
            logger.warning(
                "Polyfile analysis failed",
                filename=filename,
                content_type=content_type,
                error=str(e),
            )
            # If polyfile fails, we still have our other security checks

    def _check_polyglot_threats(self, matches: list, expected_type: str) -> None:
        """Check for polyglot file threats using polyfile analysis.

        Args:
            matches: List of polyfile match objects
            expected_type: Expected MIME type

        Raises:
            ValueError: If polyglot threats are detected
        """
        try:
            # Get all detected MIME types
            detected_types = set()

            # Iterate through polyfile's analysis results
            for match in matches:
                if hasattr(match, "mimetypes") and match.mimetypes:
                    for mimetype in match.mimetypes:
                        detected_types.add(mimetype)

            # Remove the expected type from detected types
            detected_types.discard(expected_type)

            # Check for dangerous polyglot combinations
            dangerous_types = {
                "text/html",
                "application/javascript",
                "application/x-httpd-php",
                "application/x-executable",
                "application/x-sharedlib",
                "application/x-shellscript",
                "text/x-python",
                "text/x-perl",
            }

            dangerous_detected = detected_types.intersection(dangerous_types)
            if dangerous_detected:
                raise FileSecurityError(
                    f"File appears to be a polyglot containing dangerous formats: {', '.join(dangerous_detected)}"
                )

            # If multiple non-dangerous types detected, log warning but allow
            if len(detected_types) > 0:
                logger.warning(
                    "Polyglot file detected",
                    expected_type=expected_type,
                    detected_types=list(detected_types),
                )

        except Exception as e:
            # Handle any unexpected errors in polyfile analysis
            logger.debug("Polyfile polyglot analysis failed", error=str(e))

    def _analyze_file_structure(self, matches: list, expected_type: str) -> None:
        """Analyze file structure for anomalies.

        Args:
            matches: List of polyfile match objects
            expected_type: Expected MIME type

        Raises:
            ValueError: If structural anomalies are detected
        """
        try:
            # Check for suspicious file structure patterns
            for match in matches:
                match_str = str(match).lower()

                # Check for executable signatures in non-executable files
                if expected_type != "application/x-executable":
                    dangerous_signatures = ["executable", "elf", "pe32", "mach-o"]
                    if any(sig in match_str for sig in dangerous_signatures):
                        raise FileSecurityError(
                            f"File contains executable signature '{match}' but is not an executable type"
                        )

                # Check for script signatures in non-script files
                if not expected_type.startswith("text/"):
                    script_signatures = [
                        "javascript",
                        "php",
                        "python",
                        "shell script",
                        "batch",
                    ]
                    if any(sig in match_str for sig in script_signatures):
                        raise FileSecurityError(
                            f"File contains script signature '{match}' in non-text file"
                        )

        except Exception as e:
            # Handle any unexpected errors in polyfile analysis
            logger.debug("Polyfile structure analysis failed", error=str(e))

    def _check_embedded_content(self, matches: list, content: bytes) -> None:
        """Check for suspicious embedded content.

        Args:
            matches: List of polyfile match objects
            content: File content as bytes

        Raises:
            ValueError: If suspicious embedded content is detected
        """
        try:
            # Look for multiple file format matches which might indicate embedded content
            if len(matches) > 1:
                match_strings = [str(match).lower() for match in matches]

                # Check for dangerous embedded content patterns
                dangerous_patterns = [
                    "executable",
                    "script",
                    "html",
                    "javascript",
                    "php",
                ]
                for pattern in dangerous_patterns:
                    matching_patterns = [m for m in match_strings if pattern in m]
                    if matching_patterns:
                        # Additional check: ensure this isn't just a false positive
                        # by checking if the content actually contains suspicious patterns
                        content_lower = content.lower()
                        if pattern.encode() in content_lower:
                            raise FileSecurityError(
                                f"File contains suspicious embedded content: {pattern} (detected: {matching_patterns[0]})"
                            )

        except Exception as e:
            # Handle any unexpected errors in polyfile analysis
            logger.debug("Polyfile embedded content analysis failed", error=str(e))

    def _analyze_file_content(self, content: bytes, content_type: str) -> None:
        """Perform additional content analysis for security."""
        # Only check for text-based embedded threats in non-binary file types
        if not content_type.startswith("image/"):
            self._check_for_embedded_threats(content, content_type)

        # Perform type-specific validation
        if content_type == "application/pdf":
            self._validate_pdf_content(content)
        elif content_type.startswith("image/"):
            self._validate_image_content(content)
        elif content_type in ["text/plain", "text/csv"]:
            self._validate_text_content(content)

    def _check_for_embedded_threats(self, content: bytes, content_type: str) -> None:
        """Check for common embedded threats based on content type."""
        # For binary formats, only check for executable signatures
        if content_type.startswith("image/") or content_type == "application/pdf":
            # Only check for executable signatures in binary files
            executable_signatures = [
                b"MZ",  # PE executable
                b"\x7fELF",  # ELF executable
                b"\xfe\xed\xfa",  # Mach-O executable
            ]

            for signature in executable_signatures:
                if content.startswith(signature):
                    raise FileSecurityError("File contains executable content")
            return

        # For text-based or document formats, check for script content
        dangerous_signatures = [
            b"MZ",  # PE executable
            b"\x7fELF",  # ELF executable
            b"\xfe\xed\xfa",  # Mach-O executable
            b"<script",  # JavaScript
            b"<?php",  # PHP
            b"#!/bin/sh",  # Shell script
            b"#!/bin/bash",  # Bash script
        ]

        content_lower = content.lower()
        for signature in dangerous_signatures:
            if signature.lower() in content_lower:
                raise FileSecurityError(
                    "File contains potentially dangerous embedded content"
                )

    def _validate_pdf_content(self, content: bytes) -> None:
        """Validate PDF-specific content with targeted JavaScript detection."""
        if not content.startswith(b"%PDF-"):
            raise FileContentMismatchError("Invalid PDF file structure")

        dangerous_patterns = [
            (b"/OpenAction", "auto-execute actions"),
            (b"/AA", "additional actions"),
            (b"/Launch", "external application launch"),
            (b"/SubmitForm", "form submission"),
            (b"/ImportData", "data import"),
            (b"/GoToR", "remote goto actions"),
        ]

        has_javascript = b"/JavaScript" in content or b"/JS" in content

        for pattern, description in dangerous_patterns:
            if pattern in content:
                if has_javascript:
                    # Only block if both JavaScript AND dangerous action present
                    raise FileSecurityError(
                        f"PDF contains JavaScript action {description} in blocklist"
                    )
                elif pattern in [b"/Launch", b"/SubmitForm", b"/GoToR"]:
                    # Block these even without explicit JavaScript (they're inherently risky)
                    raise FileSecurityError(
                        f"PDF contains JavaScript action {description} in blocklist"
                    )

        if has_javascript:
            logger.warning(
                "PDF contains JavaScript references but no actions in blocklist detected",
                content_hash=hashlib.sha256(content).hexdigest()[:16],
            )

    def _validate_image_content(self, content: bytes) -> None:
        """Validate image-specific content."""
        # Check for SVG content (which should already be blocked by extension)
        if content.startswith(b"<?xml") or b"<svg" in content[:1024]:
            # SVG detected - check for dangerous content
            content_lower = content.lower()
            svg_dangerous_patterns = [
                b"<script",
                b"javascript:",
                b"onclick",
                b"onload",
                b"onerror",
                b"onmouseover",
                b"<iframe",
                b"<embed",
                b"<object",
                b"<link",
                b"<meta",
                b"<foreignobject",
            ]
            for pattern in svg_dangerous_patterns:
                if pattern in content_lower:
                    raise FileSecurityError(
                        f"SVG file contains potentially dangerous content: {pattern.decode('utf-8', errors='ignore')}"
                    )

        # For other images, we rely on magic number validation and polyfile analysis
        # Avoid checking for text patterns in binary image data as it causes false positives

    def _validate_text_content(self, content: bytes) -> None:
        """Validate text file content."""
        try:
            # Ensure it's valid UTF-8 or ASCII
            text = content.decode("utf-8")

            # Check for script tags or other dangerous content
            dangerous_patterns = [
                "<script",
                "javascript:",
                "vbscript:",
                "data:text/html",
                "<?php",
                "<%",
                "<jsp:",
                "#!/",
            ]

            text_lower = text.lower()
            for pattern in dangerous_patterns:
                if pattern in text_lower:
                    raise FileSecurityError(
                        f"Text file contains potentially dangerous content: {pattern}"
                    )

        except UnicodeDecodeError as e:
            raise FileContentMismatchError(
                "Text file contains invalid UTF-8 encoding"
            ) from e

    def _sanitize_filename(self, filename: str) -> str:
        """Sanitize filename to prevent security issues."""
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
        if len(filename) > self.max_filename_length:
            name, ext = os.path.splitext(filename)
            max_name_length = self.max_filename_length - len(ext)
            filename = name[:max_name_length] + ext

        # If filename is empty after sanitization, generate a default
        if not filename:
            filename = "unnamed_file"

        return filename


# Core storage utility functions
def get_storage_client():
    """Get a configured S3 client for either AWS S3 or MinIO.

    Uses environment variables for credentials:
    - For MinIO: MINIO_ROOT_USER, MINIO_ROOT_PASSWORD
    - For S3: AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY

    Returns:
        Configured aioboto3 S3 client context manager
    """
    session = aioboto3.Session()

    # Configure client based on protocol
    if config.TRACECAT__BLOB_STORAGE_PROTOCOL == "minio":
        # MinIO configuration - use MINIO_ROOT_USER/MINIO_ROOT_PASSWORD
        return session.client(
            "s3",
            endpoint_url=config.TRACECAT__BLOB_STORAGE_ENDPOINT,
            aws_access_key_id=os.environ.get("MINIO_ROOT_USER"),
            aws_secret_access_key=os.environ.get("MINIO_ROOT_PASSWORD"),
        )
    else:
        # AWS S3 configuration - use AWS credentials from environment or default credential chain
        return session.client("s3")


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
                    # For MinIO and most regions, simple create_bucket works
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

            if url.startswith("http://minio:9000"):
                # If minio, assume presigned URL returned only works for the internal network Minio
                # Replace the internal endpoint with the public presigned URL endpoint (as defined in Caddyfile)
                url = url.replace(
                    "http://minio:9000", f"{config.TRACECAT__PUBLIC_APP_URL}/s3"
                )

            logger.debug(
                "Generated presigned download URL",
                key=key,
                bucket=bucket,
                expiry=expiry,
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
    max_size = config.TRACECAT__MAX_ATTACHMENT_SIZE_BYTES
    if size > max_size:
        raise ValueError(
            f"File size ({size / 1024 / 1024:.1f}MB) exceeds maximum allowed size "
            f"({max_size / 1024 / 1024}MB)"
        )


async def upload_file(
    content: bytes,
    key: str,
    bucket: str,
    content_type: str | None = None,
) -> None:
    """Upload a file to S3/MinIO.

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
    """Download a file from S3/MinIO.

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
            raise FileNotFoundError(f"File {key} not found in bucket {bucket}") from e
        logger.error(
            "Failed to download file",
            key=key,
            bucket=bucket,
            error=str(e),
        )
        raise


async def delete_file(key: str, bucket: str) -> None:
    """Delete a file from S3/MinIO.

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
    """Check if a file exists in S3/MinIO.

    Args:
        key: The S3 object key
        bucket: Bucket name (required)

    Returns:
        True if the file exists, False otherwise

    Raises:
        ClientError: If the check fails (other than 404)
    """

    try:
        async with get_storage_client() as s3_client:
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
