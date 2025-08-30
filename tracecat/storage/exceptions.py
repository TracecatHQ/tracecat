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


class FileMimeTypeError(FileValidationError):
    """Raised when file content type is not allowed."""

    def __init__(self, message: str, mime_type: str, allowed_types: list[str]):
        super().__init__(message)
        self.mime_type = mime_type
        self.allowed_types = allowed_types


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
