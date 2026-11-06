import base64
import io
import struct
import zipfile
import zlib

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
        allowed_extensions={".pdf", ".zip", ".txt"},  # pyright: ignore[reportArgumentType]
        allowed_mime_types={"application/pdf", "application/zip", "text/plain"},  # pyright: ignore[reportArgumentType]
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


def test_mime_mismatch_reports_declared_and_detected_types(
    validator_basic: FileSecurityValidator,
) -> None:
    # Content is PDF but declared is text/plain (both allowed in allowlist)
    with pytest.raises(FileMimeTypeError) as exc:
        validator_basic.validate_file(
            content=pdf_bytes(),
            filename="doc.pdf",
            declared_mime_type="text/plain",
        )
    assert str(exc.value) == (
        "File content does not match declared MIME type text/plain "
        "(detected: application/pdf)"
    )
    assert exc.value.mime_type == "text/plain"
    assert exc.value.detected_types == ["application/pdf"]


def test_base64_encoded_zip_reports_detected_type(
    validator_basic: FileSecurityValidator,
) -> None:
    # A zip that was base64-encoded twice decodes to base64 text, not zip bytes
    with pytest.raises(FileMimeTypeError) as exc:
        validator_basic.validate_file(
            content=base64.b64encode(zip_bytes()),
            filename="archive.zip",
            declared_mime_type="application/zip",
        )
    assert str(exc.value) == (
        "File content does not match declared MIME type application/zip "
        "(detected: text/plain)"
    )
    assert exc.value.mime_type == "application/zip"
    assert exc.value.detected_types == ["text/plain"]


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


# --- Text types (no magic signature) ---

CSV_MAX_SIZE = 64 * 1024


@pytest.fixture()
def validator_text() -> FileSecurityValidator:
    return FileSecurityValidator(
        max_file_size=CSV_MAX_SIZE,
        allowed_extensions=[".csv", ".tsv", ".txt", ".json", ".md", ".png", ".7z"],
        allowed_mime_types=[
            "text/csv",
            "text/tab-separated-values",
            "text/plain",
            "application/json",
            "text/markdown",
            "image/png",
            "application/x-7z-compressed",
        ],
    )


def png_bytes() -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 0, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(b"\x00\x00"))
        + chunk(b"IEND", b"")
    )


def sevenzip_bytes() -> bytes:
    return b"7z\xbc\xaf\x27\x1c\x00\x04" + b"\x00" * 24


def _near_limit_csv() -> bytes:
    header = b"timestamp,actor,event,ip\n"
    row = b"2026-01-01T00:00:00Z,user@example.com,login_success,10.0.0.1\n"
    rows = (CSV_MAX_SIZE - len(header)) // len(row)
    content = header + row * rows
    assert CSV_MAX_SIZE - len(row) < len(content) <= CSV_MAX_SIZE
    return content


# Lines starting with + - [ > plus ragged rows: polyfile detects only
# application/x-brainfuck for this, which is the original failure.
CODE_LIKE_CSV = (
    b"phone,status,note\n"
    b"+15551234567,[ok],<none>\n"
    b"-1.5,[warn],>see above.\n"
    b"[a],b\n"
    b">quoted,[x],y,z\n"
    b"+1,-2,[3],<4>.\n"
)


@pytest.mark.parametrize(
    "content",
    [
        pytest.param(b"timestamp,actor,event\n", id="header-only"),
        pytest.param(b'name,note\n"alice","hello"\n', id="quoted-fields"),
        pytest.param(b'name,note\n"doe, jane","a, b, c"\n', id="commas-in-quotes"),
        pytest.param(b'a,b\n"multi\nline",2\n', id="newline-in-quotes"),
        pytest.param("\ufeffname,city\nJosé,São Paulo\n".encode(), id="utf8-bom"),
        pytest.param(b"a,b\r\n1,2\r\n", id="crlf"),
        pytest.param(b"ip\n10.0.0.1\n10.0.0.2\n", id="single-column"),
        pytest.param(b"a,b\n1,2,3\n4\n", id="ragged-rows"),
        pytest.param("名前,都市\n東京,🙂\n".encode(), id="non-ascii"),
        pytest.param(CODE_LIKE_CSV, id="code-like-brainfuck-detection"),
        pytest.param(_near_limit_csv(), id="near-size-limit"),
    ],
)
def test_valid_csv_passes(
    validator_text: FileSecurityValidator, content: bytes
) -> None:
    result = validator_text.validate_file(
        content=content, filename="evidence.csv", declared_mime_type="text/csv"
    )
    assert result.content_type == "text/csv"


@pytest.mark.parametrize(
    "filename,declared,content",
    [
        ("data.tsv", "text/tab-separated-values", b"a\tb\n1\t2\n"),
        ("notes.txt", "text/plain", b"+++[>++<-]>. looks like code\n"),
        ("notes.txt", "text/plain; charset=utf-8", b"hello\n"),
        ("data.json", "application/json", b'{"a": [1, 2]}'),
        ("README.md", "text/markdown", b"# Title\n\n- item\n"),
        ("legacy.csv", "text/x-csv", b"a,b\n1,2\n"),
    ],
)
def test_other_text_types_pass(
    validator_text: FileSecurityValidator,
    filename: str,
    declared: str,
    content: bytes,
) -> None:
    validator_text.validate_file(
        content=content, filename=filename, declared_mime_type=declared
    )


@pytest.mark.parametrize(
    "content,detected",
    [
        pytest.param(b"\x7fELF\x02\x01\x01" + b"\x00" * 57, None, id="elf"),
        pytest.param(b"MZ\x90\x00\x03" + b"\x00" * 59, None, id="pe"),
        pytest.param(png_bytes(), ["image/png"], id="png"),
        pytest.param(sevenzip_bytes(), None, id="7z"),
        pytest.param(b"a,b\n1,\x002\n", None, id="nul-byte"),
        pytest.param("a,b\nJosé,1\n".encode("latin-1"), None, id="not-utf8"),
        pytest.param(
            b"<html><script>alert(1)</script></html>,x\n1,2\n",
            ["text/csv", "text/html"],
            id="html",
        ),
        pytest.param(
            b'<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)"/>,a\n1,2\n',
            ["image/svg+xml", "text/csv"],
            id="svg",
        ),
        pytest.param(b"<?php system($_GET['c']); ?>", None, id="php"),
        pytest.param(b'a,"unterminated\n1,2\n', None, id="malformed-quotes"),
    ],
)
def test_disguised_csv_rejected(
    validator_text: FileSecurityValidator,
    content: bytes,
    detected: list[str] | None,
) -> None:
    with pytest.raises(FileMimeTypeError) as exc:
        validator_text.validate_file(
            content=content, filename="evidence.csv", declared_mime_type="text/csv"
        )
    assert exc.value.mime_type == "text/csv"
    assert str(exc.value).startswith(
        "File content does not match declared MIME type text/csv (detected: "
    )
    if detected is not None:
        assert exc.value.detected_types == detected


def test_png_and_7z_still_use_signature_check(
    validator_text: FileSecurityValidator,
) -> None:
    assert (
        validator_text.validate_file(
            content=png_bytes(), filename="shot.png", declared_mime_type="image/png"
        ).content_type
        == "image/png"
    )
    assert (
        validator_text.validate_file(
            content=sevenzip_bytes(),
            filename="evidence.7z",
            declared_mime_type="application/x-7z-compressed",
        ).content_type
        == "application/x-7z-compressed"
    )
    with pytest.raises(FileMimeTypeError):
        validator_text.validate_file(
            content=b"a,b\n1,2\n", filename="shot.png", declared_mime_type="image/png"
        )


def test_csv_still_requires_allowlisted_type(
    validator_basic: FileSecurityValidator,
) -> None:
    # validator_basic allows text/plain but not text/csv.
    with pytest.raises(FileMimeTypeError) as exc:
        validator_basic.validate_file(
            content=b"a,b\n1,2\n", filename="notes.txt", declared_mime_type="text/csv"
        )
    assert str(exc.value) == "File MIME type text/csv is not allowed"
