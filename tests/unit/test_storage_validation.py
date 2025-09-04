import io
import zipfile

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
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("a.txt", "hello")
    return buf.getvalue()


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
        validator_basic.validate_file(
            content=pdf_bytes(), filename=filename, declared_mime_type="application/pdf"
        )


def test_empty_content_rejected(validator_basic: FileSecurityValidator) -> None:
    with pytest.raises(FileSizeError):
        validator_basic.validate_file(
            content=b"", filename="file.pdf", declared_mime_type="application/pdf"
        )


def test_extension_not_allowed_raises(validator_basic: FileSecurityValidator) -> None:
    # Only ".txt", ".pdf", ".zip" are allowed; using .exe should fail on extension
    with pytest.raises(FileExtensionError):
        validator_basic.validate_file(
            content=pdf_bytes(),
            filename="sample.exe",
            declared_mime_type="application/pdf",
        )


def test_declared_mime_normalization_allows_params(
    validator_basic: FileSecurityValidator,
) -> None:
    # Declared MIME with parameters should be normalized and accepted
    validation_result = validator_basic.validate_file(
        content=pdf_bytes(),
        filename="doc.pdf",
        declared_mime_type="application/pdf; charset=UTF-8",
    )
    assert validation_result.content_type == "application/pdf"


def test_mime_mismatch_raises_generic_message(
    validator_basic: FileSecurityValidator,
) -> None:
    # Content is PDF but declared is text/plain (both allowed in allowlist)
    with pytest.raises(FileMimeTypeError) as exc:
        validator_basic.validate_file(
            content=pdf_bytes(),
            filename="doc.pdf",
            declared_mime_type="text/plain",
        )
    # Generic, user-facing message
    assert str(exc.value) == "Unknown or unsupported file type"


def test_zip_upload_passes(validator_basic: FileSecurityValidator) -> None:
    validation_result = validator_basic.validate_file(
        content=zip_bytes(),
        filename="archive.zip",
        declared_mime_type="application/zip",
    )
    assert validation_result.content_type == "application/zip"


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
    validation_result = validator_basic.validate_file(
        content=pdf_bytes(),
        filename=filename,
        declared_mime_type="application/pdf",
    )
    assert validation_result.filename == filename


def office_bytes() -> bytes:
    # Office Open XML formats are ZIP containers; minimal header is fine for extension checks
    return b"PK\x03\x04" + b"0" * 128


@pytest.mark.parametrize(
    "filename,declared",
    [
        (
            "doc.docx",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ),
        (
            "sheet.xlsx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ),
        (
            "slides.pptx",
            "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        ),
    ],
)
def test_office_docs_rejected_by_extension(
    validator_basic: FileSecurityValidator, filename: str, declared: str
) -> None:
    # Only .pdf, .zip, .txt are allowed; Office docs should fail on extension.
    with pytest.raises(FileExtensionError):
        validator_basic.validate_file(
            content=office_bytes(), filename=filename, declared_mime_type=declared
        )


@pytest.mark.parametrize(
    "filename,declared",
    [
        (
            "macro.docm",
            "application/vnd.ms-word.document.macroEnabled.12",
        ),
        (
            "macro.xlsm",
            "application/vnd.ms-excel.sheet.macroEnabled.12",
        ),
        (
            "macro.pptm",
            "application/vnd.ms-powerpoint.presentation.macroEnabled.12",
        ),
    ],
)
def test_macro_enabled_office_docs_rejected_by_extension(
    validator_basic: FileSecurityValidator, filename: str, declared: str
) -> None:
    # Macro-enabled Office docs should be rejected by extension under the basic allowlist.
    with pytest.raises(FileExtensionError):
        validator_basic.validate_file(
            content=office_bytes(), filename=filename, declared_mime_type=declared
        )


def test_vbscript_rejected_by_extension(
    validator_basic: FileSecurityValidator,
) -> None:
    # VBScript should be rejected by extension under the basic allowlist.
    with pytest.raises(FileExtensionError):
        validator_basic.validate_file(
            content=b'WScript.Echo "hello"',
            filename="script.vbs",
            declared_mime_type="application/vbscript",
        )
