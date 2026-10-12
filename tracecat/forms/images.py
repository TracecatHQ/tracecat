"""Validation for inline form images.

Form images are ``data:`` URIs stored in the form config. The server never
fetches a URL.
"""

import base64
import binascii
import re
from collections.abc import Callable

from tracecat.forms.constants import MAX_IMAGE_BYTES

_DATA_URI_RE = re.compile(r"data:image/(png|jpeg|webp);base64,(.*)", re.DOTALL)
# Base64 encodes 3 bytes as 4 characters. Checked before decoding to bound work.
_MAX_ENCODED_LENGTH = (MAX_IMAGE_BYTES + 2) // 3 * 4


def _is_png(data: bytes) -> bool:
    return data.startswith(b"\x89PNG\r\n\x1a\n")


def _is_jpeg(data: bytes) -> bool:
    return data.startswith(b"\xff\xd8\xff")


def _is_webp(data: bytes) -> bool:
    return data[:4] == b"RIFF" and data[8:12] == b"WEBP"


_MAGIC_CHECKS: dict[str, Callable[[bytes], bool]] = {
    "png": _is_png,
    "jpeg": _is_jpeg,
    "webp": _is_webp,
}


def image_data_uri_size(value: str) -> int:
    """Return the decoded size in bytes of a validated image data URI."""
    encoded = value.partition(",")[2]
    return len(encoded) * 3 // 4 - encoded.count("=", -2)


def decode_image_data_uri(value: str) -> bytes:
    """Decode an inline image and check its type and size.

    Args:
        value: A ``data:image/(png|jpeg|webp);base64,`` URI.

    Returns:
        The decoded image bytes.

    Raises:
        ValueError: If the prefix, the base64 payload, the magic bytes or the
            size is not acceptable.
    """
    if (match := _DATA_URI_RE.fullmatch(value)) is None:
        raise ValueError(
            "Image must be a data URI starting with "
            "data:image/png;base64, data:image/jpeg;base64, or data:image/webp;base64,"
        )
    subtype, encoded = match.groups()
    if len(encoded) > _MAX_ENCODED_LENGTH:
        raise ValueError(f"Image is larger than {MAX_IMAGE_BYTES // 1024} KB")
    try:
        data = base64.b64decode(encoded, validate=True)
    except binascii.Error as e:
        raise ValueError("Image is not valid base64") from e
    if len(data) > MAX_IMAGE_BYTES:
        raise ValueError(f"Image is larger than {MAX_IMAGE_BYTES // 1024} KB")
    if not _MAGIC_CHECKS[subtype](data):
        raise ValueError(f"Image content is not a valid {subtype} file")
    return data
