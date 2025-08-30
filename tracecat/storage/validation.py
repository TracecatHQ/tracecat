"""File security validator.

For all files:
- Check size
- Sanitize file name
- Check extension against allowlist
- Check MIME (via magic number) against allowlist

For images:
- Randomized bits

Allowlist for extensions, content types, and MIME types via a config.
The defaults are based on OWASP recommendations:
https://cheatsheetseries.owasp.org/cheatsheets/File_Upload_Cheat_Sheet.html#list-allowed-extensions
"""

import os
import re

from polyfile.magic import MagicMatcher
from pydantic import BaseModel

from tracecat import config
from tracecat.logger import logger
from tracecat.storage.exceptions import (
    FileContentMismatchError,
    FileExtensionError,
    FileMimeTypeError,
    FileNameError,
    FileSizeError,
)


class FileValidationResult(BaseModel):
    """Result of file validation."""

    filename: str
    content_type: str
    extension: str


class FileSecurityValidator:
    """Comprehensive file security validator implementing OWASP recommendations with polyfile integration."""

    def __init__(
        self,
        max_file_size: int | None = None,
        max_filename_length: int | None = None,
        allowed_extensions: set[str] | None = None,
        allowed_mime_types: set[str] | None = None,
    ):
        self.max_file_size = max_file_size or config.TRACECAT__MAX_ATTACHMENT_SIZE_BYTES
        self.max_filename_length = (
            max_filename_length or config.TRACECAT__MAX_ATTACHMENT_FILENAME_LENGTH
        )
        self.allowed_extensions = list(
            allowed_extensions or config.TRACECAT__ALLOWED_ATTACHMENT_EXTENSIONS
        )
        self.allowed_mime_types = list(
            allowed_mime_types or config.TRACECAT__ALLOWED_ATTACHMENT_MIME_TYPES
        )

    def validate_file(
        self, content: bytes, filename: str, declared_mime_type: str
    ) -> FileValidationResult:
        """Validate a file."""
        # Harden: validate raw filename before sanitization
        self._validate_filename_raw(filename)

        # Sanitize filename
        filename = self._sanitize_filename(filename)

        # Validate file size
        self._validate_file_size(content)
        # Validate filename
        self._validate_filename(filename)
        # Validate extension
        self._validate_extension(filename)
        # Validate MIME type
        self._validate_mime_type(declared_mime_type)
        # Polyfile magic number check
        self._validate_magic_number(content, declared_mime_type)
        return FileValidationResult(
            filename=filename,
            content_type=declared_mime_type,
            extension=os.path.splitext(filename)[1].lower(),
        )

    def _validate_file_size(self, content: bytes) -> bool:
        """Validate file size."""
        if len(content) == 0:
            logger.error("File cannot be empty")
            raise FileSizeError("File cannot be empty")
        if len(content) > self.max_file_size:
            logger.error(
                f"File exceeds maximum allowed size of {self.max_file_size} bytes"
            )
            raise FileSizeError(
                f"File exceeds maximum allowed size of {self.max_file_size} bytes"
            )
        return True

    def _validate_filename(self, filename: str) -> bool:
        """Validate filename (post-sanitization)."""
        if not filename or not filename.strip():
            logger.error("File name cannot be empty")
            raise FileNameError("File name cannot be empty")
        if len(filename) > self.max_filename_length:
            logger.error(
                f"File name exceeds maximum allowed length of {self.max_filename_length} characters"
            )
            raise FileNameError(
                f"File name exceeds maximum allowed length of {self.max_filename_length} characters"
            )
        # Control characters should not be present
        if any(ord(c) < 32 for c in filename):
            logger.error("Invalid file name")
            raise FileNameError("Invalid file name")
        # Basic path separator defense-in-depth
        if "/" in filename or "\\" in filename:
            logger.error("Invalid file name")
            raise FileNameError("Invalid file name")
        return True

    def _validate_extension(self, filename: str) -> bool:
        """Validate extension."""
        extension = os.path.splitext(filename)[1].lower()
        if extension not in self.allowed_extensions:
            raise FileExtensionError(
                f"File extension {extension} is not allowed",
                extension,
                self.allowed_extensions,
            )
        return True

    def _validate_filename_raw(self, filename: str) -> bool:
        """Pre-sanitization filename validation to catch unsafe inputs early."""
        if filename is None:
            logger.error("File name cannot be empty")
            raise FileNameError("File name cannot be empty")

        if not str(filename).strip():
            logger.error("File name cannot be empty")
            raise FileNameError("File name cannot be empty")

        # Null byte and control characters
        if "\x00" in filename or any(ord(c) < 32 for c in filename):
            logger.error("Invalid file name")
            raise FileNameError("Invalid file name")

        # Path separators and traversal indicators
        if (
            "/" in filename
            or "\\" in filename
            or "../" in filename
            or "..\\" in filename
        ):
            logger.error("Invalid file name")
            raise FileNameError("Invalid file name")

        # Disallow reserved dot-only names
        if filename in {".", ".."}:
            logger.error("Invalid file name")
            raise FileNameError("Invalid file name")

        return True

    def _validate_mime_type(self, mime_type: str) -> bool:
        """Validate MIME type (normalized to base type)."""
        base_mime = self._normalize_mime_type(mime_type)
        allowed = {t.lower() for t in self.allowed_mime_types}
        if base_mime not in allowed:
            logger.error(f"File MIME type {mime_type} is not allowed")
            raise FileMimeTypeError(
                f"File MIME type {mime_type} is not allowed",
                base_mime,
                self.allowed_mime_types,
            )
        return True

    def _validate_magic_number(self, content: bytes, declared_mime_type: str) -> bool:
        """Validate file type via polyfile magic matching with generic errors.

        We trust polyfile's detection and intentionally avoid custom fallbacks
        or leaking detailed types in error messages.
        """
        if not content:
            # Generic user-facing message
            logger.error("Unknown or unsupported file type")
            raise FileContentMismatchError("Unknown or unsupported file type")

        detected_types: set[str] = set()

        try:
            matches = list(MagicMatcher.DEFAULT_INSTANCE.match(content))
            for match in matches:
                mimetypes = getattr(match, "mimetypes", None)
                if mimetypes:
                    for mt in mimetypes:
                        if mt:
                            detected_types.add(mt.split(";")[0].strip().lower())
        except Exception as e:
            # Keep messages generic for user-facing responses
            logger.error("Unknown or unsupported file type")
            raise FileContentMismatchError("Unknown or unsupported file type") from e

        if not detected_types:
            logger.error("Unknown or unsupported file type")
            raise FileContentMismatchError("Unknown or unsupported file type")

        declared_base = self._normalize_mime_type(declared_mime_type)
        if declared_base not in detected_types:
            # Keep message generic for users
            logger.error(
                "Detected MIME type does not match declared MIME type",
                declared_mime_type=declared_base,
                detected_types=list(detected_types),
            )
            raise FileMimeTypeError(
                "Unknown or unsupported file type",
                declared_base,
                self.allowed_mime_types,
            )

        allowed = {t.lower() for t in self.allowed_mime_types}
        if detected_types & allowed:
            return True

        representative = next(iter(detected_types))
        logger.error(
            "Detected MIME type is not allowed",
            declared_mime_type=declared_base,
            detected_type=representative,
            allowed_types=self.allowed_mime_types,
        )
        raise FileMimeTypeError(
            "Unknown or unsupported file type",
            representative,
            self.allowed_mime_types,
        )

    @staticmethod
    def _normalize_mime_type(mime_type: str) -> str:
        """Normalize a MIME type to its base form (lowercased, parameters stripped)."""
        if not mime_type:
            return ""
        return mime_type.split(";", 1)[0].strip().lower()

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

        # If filename is empty after sanitization, raise an error
        if not filename:
            logger.error("File name cannot be empty")
            raise FileNameError("File name cannot be empty")

        return filename
