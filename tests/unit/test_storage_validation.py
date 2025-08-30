import pytest

from tracecat.storage.exceptions import (
    FileExtensionError,
    FileMimeTypeError,
    FileNameError,
    FileSizeError,
)
from tracecat.storage.validation import FileSecurityValidator


@pytest.fixture()
def validator_basic() -> FileSecurityValidator:
    return FileSecurityValidator(
        max_file_size=1024 * 1024,  # 1 MB
        max_filename_length=128,
        allowed_extensions={".pdf", ".zip", ".txt"},
        allowed_mime_types={"application/pdf", "application/zip", "text/plain"},
    )


def pdf_bytes() -> bytes:
    # Minimal PDF header and trailer for magic detection
    return b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\n%%EOF\n"


def zip_bytes() -> bytes:
    # Minimal ZIP local header signature + padding
    return b"PK\x03\x04" + b"0" * 64


@pytest.mark.parametrize(
    "filename",
    [
        "../evil.txt",
        "..\\evil.txt",
        "a/b.txt",
        "a\\b.txt",
        "",
        " ",
        "bad\x00name.txt",
        ".",
        "..",
    ],
)
def test_invalid_raw_filenames_rejected(
    validator_basic: FileSecurityValidator, filename: str
) -> None:
    with pytest.raises(FileNameError):
        validator_basic.validate(
            content=pdf_bytes(), filename=filename, declared_mime_type="application/pdf"
        )


def test_empty_content_rejected(validator_basic: FileSecurityValidator) -> None:
    with pytest.raises(FileSizeError):
        validator_basic.validate(
            content=b"", filename="file.pdf", declared_mime_type="application/pdf"
        )


def test_extension_not_allowed_raises(validator_basic: FileSecurityValidator) -> None:
    # Only ".txt", ".pdf", ".zip" are allowed; using .exe should fail on extension
    with pytest.raises(FileExtensionError):
        validator_basic.validate(
            content=pdf_bytes(),
            filename="sample.exe",
            declared_mime_type="application/pdf",
        )


def test_declared_mime_normalization_allows_params(
    validator_basic: FileSecurityValidator,
) -> None:
    # Declared MIME with parameters should be normalized and accepted
    assert validator_basic.validate(
        content=pdf_bytes(),
        filename="doc.pdf",
        declared_mime_type="application/pdf; charset=UTF-8",
    )


def test_mime_mismatch_raises_generic_message(
    validator_basic: FileSecurityValidator,
) -> None:
    # Content is PDF but declared is text/plain (both allowed in allowlist)
    with pytest.raises(FileMimeTypeError) as exc:
        validator_basic.validate(
            content=pdf_bytes(),
            filename="doc.pdf",
            declared_mime_type="text/plain",
        )
    # Generic, user-facing message
    assert str(exc.value) == "Unknown or unsupported file type"


def test_zip_upload_passes(validator_basic: FileSecurityValidator) -> None:
    assert validator_basic.validate(
        content=zip_bytes(),
        filename="archive.zip",
        declared_mime_type="application/zip",
    )


@pytest.mark.parametrize(
    "filename",
    [
        "My Report (final) ..v1 .pdf",
        ".hidden.name..pdf",
        "weird_chars_#@!$%^&().pdf",
        " spaced name .pdf ",
    ],
)
def test_sanitized_filenames_validate(
    validator_basic: FileSecurityValidator, filename: str
) -> None:
    assert validator_basic.validate(
        content=pdf_bytes(),
        filename=filename,
        declared_mime_type="application/pdf",
    )
