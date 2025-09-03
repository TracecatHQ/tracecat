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
from collections.abc import Sequence

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
        allowed_extensions: Sequence[str] | None = None,
        allowed_mime_types: Sequence[str] | None = None,
        validate_magic_number: bool = True,
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
        self.validate_magic_number = validate_magic_number

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
        normalized_mime = self._normalize_mime_type(declared_mime_type)
        self._validate_mime_type(normalized_mime)
        # Polyfile magic number check (if enabled)
        if self.validate_magic_number:
            self._validate_magic_number(content, normalized_mime)
        return FileValidationResult(
            filename=filename,
            content_type=normalized_mime,
            extension=os.path.splitext(filename.strip())[1].lower().strip(),
        )

    def _validate_file_size(self, content: bytes) -> bool:
        """Validate file size."""
        if len(content) == 0:
            logger.error("File cannot be empty")
            raise FileSizeError("File cannot be empty")
        if len(content) > self.max_file_size:
            logger.error(
                "File exceeds maximum allowed size",
                max_file_size=self.max_file_size,
            )
            raise FileSizeError(
                "File exceeds maximum allowed size",
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
        # Compute extension ignoring surrounding whitespace in the name
        safe_name = str(filename).strip()
        extension = os.path.splitext(safe_name)[1].lower().strip()
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
        # Use equivalence keys to handle common vendor/legacy prefixes consistently
        allowed_equiv = {self._mime_equivalence_key(t) for t in allowed}
        if self._mime_equivalence_key(base_mime) not in allowed_equiv:
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
        detected_equiv: set[str] = set()

        try:
            matches = list(MagicMatcher.DEFAULT_INSTANCE.match(content))
            for match in matches:
                mimetypes = getattr(match, "mimetypes", None)
                if mimetypes:
                    for mt in mimetypes:
                        if mt:
                            base = self._normalize_mime_type(mt)
                            detected_types.add(base)
                            detected_equiv.add(self._mime_equivalence_key(base))
        except Exception as e:
            # Keep messages generic for user-facing responses
            logger.error("Unknown or unsupported file type")
            raise FileContentMismatchError("Unknown or unsupported file type") from e

        if not detected_types:
            logger.error("Unknown or unsupported file type")
            raise FileContentMismatchError("Unknown or unsupported file type")

        declared_base = self._normalize_mime_type(declared_mime_type)
        declared_key = self._mime_equivalence_key(declared_base)
        if declared_key not in detected_equiv:
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
        allowed_equiv = {self._mime_equivalence_key(t) for t in allowed}
        if detected_equiv & allowed_equiv:
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

    @staticmethod
    def _mime_equivalence_key(mime_type: str) -> str:
        """Compute an equivalence key for MIME types to compare related variants consistently.

        Strategy:
        - Lowercase, strip parameters first (callers pass normalized base).
        - For subtype, remove a single leading "x-" (experimental prefix).
        - Remove a trailing "-compressed" qualifier.
        This avoids one-off mappings while handling common vendor variants consistently.
        """
        base = FileSecurityValidator._normalize_mime_type(mime_type)
        if "/" not in base:
            return base
        type_part, sub = base.split("/", 1)
        # remove leading experimental prefix
        if sub.startswith("x-"):
            sub = sub[2:]
        # strip trailing qualifier commonly seen in legacy types
        if sub.endswith("-compressed"):
            sub = sub[: -len("-compressed")]
        return f"{type_part}/{sub}"

    def _sanitize_filename(self, filename: str) -> str:
        """Sanitize filename to prevent security issues while preserving user-provided names.

        - Strip path components only (defense-in-depth against traversal). Other validations
          (control chars, separators, etc.) are handled in _validate_filename_raw/_validate_filename.
        - Preserve spaces and punctuation to match expected behavior in tests.
        """
        # Get just the filename without any path components
        sanitized = os.path.basename(str(filename))

        # If filename is empty after sanitization, raise an error
        if not sanitized:
            logger.error("File name cannot be empty")
            raise FileNameError("File name cannot be empty")

        return sanitized
