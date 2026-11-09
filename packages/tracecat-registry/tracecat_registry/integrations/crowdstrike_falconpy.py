"""Generic interface to FalconPy Uber class."""

import base64
import binascii
from collections.abc import Iterator
from typing import Annotated, Any, NotRequired, Protocol, TypedDict, cast

from falconpy import APIHarnessV2
from pydantic import Field

from tracecat_registry import RegistrySecret, ctx, registry, secrets
from tracecat_registry.config import TRACECAT__MAX_FILE_SIZE_BYTES

crowdstrike_secret = RegistrySecret(
    name="crowdstrike",
    keys=["CROWDSTRIKE_CLIENT_ID", "CROWDSTRIKE_CLIENT_SECRET"],
)
"""Crowdstrike OAuth2.0 credentials.

- name: `crowdstrike`
- keys:
    - `CROWDSTRIKE_CLIENT_ID`
    - `CROWDSTRIKE_CLIENT_SECRET`
"""


class FalconPyFile(TypedDict):
    """File to upload as a multipart form field."""

    field: str
    """Multipart form field name, for example `file`."""

    filename: str
    """File name sent with the upload."""

    content_base64: str
    """Base64-encoded file content."""

    content_type: NotRequired[str]
    """MIME type of the content. Defaults to `application/octet-stream`."""


def _size_error() -> ValueError:
    return ValueError(
        "CrowdStrike file content exceeds maximum size limit of "
        f"{TRACECAT__MAX_FILE_SIZE_BYTES // 1024 // 1024}MB"
    )


def _encode_bytes(content: bytes) -> dict[str, str]:
    if len(content) > TRACECAT__MAX_FILE_SIZE_BYTES:
        raise _size_error()
    return {"content_base64": base64.b64encode(content).decode("ascii")}


def _decode_file(file: FalconPyFile) -> tuple[str, tuple[str, bytes, str]]:
    encoded = "".join(file["content_base64"].split())
    if len(encoded) * 3 // 4 > TRACECAT__MAX_FILE_SIZE_BYTES + 2:
        raise _size_error()
    try:
        content = base64.b64decode(encoded, validate=True)
    except binascii.Error as e:
        raise ValueError(f"Invalid base64 content for file {file['filename']!r}") from e
    if len(content) > TRACECAT__MAX_FILE_SIZE_BYTES:
        raise _size_error()
    content_type = file.get("content_type") or "application/octet-stream"
    return file["field"], (file["filename"], content, content_type)


class _StreamingResponse(Protocol):
    """The `requests.Response` FalconPy returns when `stream=True`."""

    ok: bool
    status_code: int
    headers: Any
    text: str

    def json(self) -> Any: ...

    def iter_content(self, chunk_size: int) -> Iterator[bytes]: ...

    def close(self) -> None: ...


def _read_stream(response: _StreamingResponse) -> dict[str, Any]:
    try:
        if not response.ok:
            try:
                body: Any = response.json()
            except ValueError:
                body = response.text
            return {
                "status_code": response.status_code,
                "headers": dict(response.headers),
                "body": body,
            }
        content = bytearray()
        for chunk in response.iter_content(chunk_size=1024 * 1024):
            content.extend(chunk)
            if len(content) > TRACECAT__MAX_FILE_SIZE_BYTES:
                raise _size_error()
    finally:
        response.close()
    return _encode_bytes(bytes(content))


@registry.register(
    default_title="Call command",
    description="Instantiate a FalconPy Uber Class client and call a FalconPy API method.",
    display_group="FalconPy",
    doc_url="https://falconpy.io/Usage/Basic-Uber-Class-usage.html",
    namespace="tools.falconpy",
    secrets=[crowdstrike_secret],
)
async def call_command(
    operation_id: Annotated[
        str,
        Field(
            ...,
            description="Operation ID from https://www.falconpy.io/Operations/All-Operations.html",
        ),
    ],
    params: Annotated[
        dict[str, Any] | None,
        Field(..., description="Parameters to pass into the command"),
    ] = None,
    member_cid: Annotated[
        str | None,
        Field(..., description="Multi-tenant customer ID"),
    ] = None,
    base_url: Annotated[
        str | None,
        Field(..., description="Base URL for the Falcon API"),
    ] = None,
    extra_kwargs: Annotated[
        dict[str, Any] | None,
        Field(..., description="Extra keyword arguments to pass to the Falcon API"),
    ] = None,
    files: Annotated[
        list[FalconPyFile] | None,
        Field(
            ...,
            description=(
                "Files to upload as multipart form fields. Each file has `field`,"
                " `filename`, `content_base64` and optional `content_type`."
            ),
        ),
    ] = None,
) -> Any:
    """Binary responses are returned as `{"content_base64": ...}`."""
    params = params or {}
    extra_kwargs = extra_kwargs or {}

    # Resolve base_url: parameter takes precedence, then workspace variable
    resolved_base_url = base_url
    if not resolved_base_url:
        resolved_base_url = await ctx.variables.aio.get_or_default(
            "crowdstrike", "base_url", None
        )

    falcon = APIHarnessV2(
        base_url=resolved_base_url,
        client_id=secrets.get("CROWDSTRIKE_CLIENT_ID"),
        client_secret=secrets.get("CROWDSTRIKE_CLIENT_SECRET"),
        member_cid=member_cid,
        **extra_kwargs,
    )
    if files:
        params = {**params, "files": [_decode_file(file) for file in files]}
    result = falcon.command(operation_id, **params)
    if isinstance(result, bytes | bytearray):
        return _encode_bytes(bytes(result))
    if hasattr(result, "iter_content"):
        return _read_stream(cast(_StreamingResponse, result))
    return result
