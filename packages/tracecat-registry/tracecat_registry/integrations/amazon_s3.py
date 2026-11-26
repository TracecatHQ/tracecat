"""S3 integration to download files and return contents as a string."""

import asyncio
import base64
import binascii
import codecs
import re
import zlib
from typing import TYPE_CHECKING, Annotated, Any

from pydantic import Field
from tenacity import retry, stop_after_attempt, wait_exponential
from typing_extensions import Doc

if TYPE_CHECKING:
    from types_aiobotocore_s3.type_defs import (
        DeleteObjectOutputTypeDef,
        ListObjectsV2OutputTypeDef,
    )
else:
    ListObjectsV2OutputTypeDef = dict[str, Any]
    DeleteObjectOutputTypeDef = dict[str, Any]

from tracecat_registry import RegistrySecret, registry
from tracecat_registry.config import (
    TRACECAT__MAX_FILE_SIZE_BYTES,
    TRACECAT__S3_CONCURRENCY_LIMIT,
)
import tracecat_registry.integrations.aws_boto3 as aws_boto3

# Add this at the top with other constants
BUCKET_REGEX = re.compile(r"^[a-z0-9][a-z0-9.-]*[a-z0-9]$")

# Semaphore to limit concurrent S3 operations to prevent resource exhaustion
# This limits the number of concurrent S3 API calls
_s3_semaphore = asyncio.Semaphore(TRACECAT__S3_CONCURRENCY_LIMIT)

s3_secret = RegistrySecret(
    name="amazon_s3",
    optional_keys=[
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN",
        "AWS_REGION",
        "AWS_ROLE_ARN",
        "AWS_ROLE_SESSION_NAME",
    ],
    optional=True,  # Might be Minio etc.
)
"""AWS credentials.

- name: `amazon_s3`
- optional_keys:
    Either:
        - `AWS_ROLE_ARN` (recommended; Tracecat assumes the role on the host)
        - `AWS_ROLE_SESSION_NAME` (optional audit session label)
    Or:
        - `AWS_ACCESS_KEY_ID`
        - `AWS_SECRET_ACCESS_KEY`
        - `AWS_SESSION_TOKEN` (optional)
    And:
        - `AWS_REGION`

Tracecat automatically supplies the workspace-scoped AWS External ID used for
cross-account AssumeRole requests and uses a default STS session name when
`AWS_ROLE_SESSION_NAME` is unset.
"""

_AWS_SERVICE_REGION_DOC = (
    "AWS service region to use for this request. Overrides the AWS_REGION secret "
    "for the service client."
)

_READ_CHUNK_BYTES = 1024 * 1024
_GZIP_MAGIC = b"\x1f\x8b"
_GZIP_WBITS = 16 + zlib.MAX_WBITS
# A fixed constant, not TRACECAT__MAX_FILE_SIZE_BYTES: that env var is not passed into
# the action sandbox, so the indexed schema and the runtime check could disagree.
_MAX_BYTES_LIMIT = 20 * 1024 * 1024

MaxBytes = Annotated[
    int,
    Field(
        ...,
        ge=1,
        le=_MAX_BYTES_LIMIT,
        description=(
            "Maximum number of bytes to return, counted after gzip decompression. "
            "A larger object is cut at this size and the result ends with a "
            "truncation notice, so checks on the result can miss content past the "
            "cut. The default, 20 MiB, is also the maximum."
        ),
    ),
]


def _check_max_bytes(max_bytes: int) -> None:
    # The executor does not re-validate action inputs, so enforce the bounds here.
    if not 1 <= max_bytes <= _MAX_BYTES_LIMIT:
        raise ValueError(
            f"max_bytes must be between 1 and {_MAX_BYTES_LIMIT}, got {max_bytes}."
        )


async def _read_bounded(body: Any, max_bytes: int) -> tuple[bytes, bool, bool]:
    """Read at most ``max_bytes`` of an S3 body, gunzipping gzip data.

    Gzip is detected from the magic bytes, so it works whatever the key or
    ``ContentEncoding`` says. The cap applies to the decompressed output and
    each inflate call is bounded, so a small file cannot expand without limit.

    Returns the bytes, whether the body was gzip, and whether it was cut.
    """
    try:
        head = b""
        while len(head) < len(_GZIP_MAGIC) and (chunk := await _read_chunk(body)):
            head += chunk
        if head.startswith(_GZIP_MAGIC):
            data, truncated = await _gunzip_bounded(body, head, max_bytes)
            return data, True, truncated

        out = bytearray(head)
        while len(out) <= max_bytes and (chunk := await _read_chunk(body)):
            out += chunk
        truncated = len(out) > max_bytes
        del out[max_bytes:]
        return bytes(out), False, truncated
    finally:
        # Stop the download here rather than draining the rest of the object.
        body.close()


async def _gunzip_bounded(body: Any, data: bytes, max_bytes: int) -> tuple[bytes, bool]:
    out = bytearray()
    consumed = len(data)
    inflater = zlib.decompressobj(wbits=_GZIP_WBITS)
    try:
        while data:
            while data and len(out) <= max_bytes:
                if inflater.eof:
                    # Between members: skip zero padding, as gzip.decompress does,
                    # then start the next member (log shippers concatenate them).
                    if not (data := data.lstrip(b"\0")):
                        break
                    inflater = zlib.decompressobj(wbits=_GZIP_WBITS)
                out += inflater.decompress(data, max_bytes + 1 - len(out))
                data = (
                    inflater.unused_data if inflater.eof else inflater.unconsumed_tail
                )
            if len(out) > max_bytes:
                del out[max_bytes:]
                return bytes(out), True
            data = await _read_chunk(body)
            consumed += len(data)
            if data and consumed > max_bytes + _READ_CHUNK_BYTES:
                # Input that inflates to little (padding, tiny members) must not pull
                # the whole object down: download no more than the plain path does.
                # The output was not cut, so fail instead of reporting truncation.
                raise ValueError(
                    f"Invalid gzip data: more than {max_bytes + _READ_CHUNK_BYTES} "
                    f"compressed bytes decompressed to only {len(out)} bytes."
                )
    except zlib.error as e:
        raise ValueError(f"Invalid gzip data: {e}") from e
    if not inflater.eof:
        raise ValueError("Invalid gzip data: the stream ended early.")
    return bytes(out), False


async def _read_chunk(body: Any) -> bytes:
    chunk = await body.read(_READ_CHUNK_BYTES)
    if not isinstance(chunk, bytes):
        raise ValueError(f"Unexpected body type. Expected bytes, got {type(chunk)}")
    return chunk


def _decode_utf8(data: bytes, *, key: str, truncated: bool) -> str:
    # When the cut lands inside a multi-byte character, the incremental decoder
    # holds the partial character back instead of raising.
    decoder = codecs.getincrementaldecoder("utf-8")()
    try:
        return decoder.decode(data, final=not truncated)
    except UnicodeDecodeError as e:
        raise ValueError(
            f"S3 object {key!r} is not UTF-8 text (invalid byte at offset "
            f"{e.start}). get_object only returns text objects."
        ) from e


@registry.register(
    default_title="Parse S3 URI",
    description="Parse an S3 URI into bucket name and object key.",
    display_group="Amazon S3",
    doc_url="https://docs.aws.amazon.com/boto3/latest/guide/s3-example-download-file.html",
    namespace="tools.amazon_s3",
    secrets=[s3_secret],
)
async def parse_uri(uri: str) -> tuple[str, str]:
    uri = str(uri).strip()
    if not uri.startswith("s3://"):
        raise ValueError("S3 URI must start with 's3://'")

    uri_path = uri.replace("s3://", "")
    uri_paths = uri_path.split("/")
    bucket = uri_paths.pop(0)
    key = "/".join(uri_paths)

    return bucket, key


@registry.register(
    default_title="Call S3 method",
    description="Instantiate a S3 client and call a S3 method.",
    display_group="Amazon S3",
    doc_url="https://docs.aws.amazon.com/boto3/latest/guide/s3-example-download-file.html",
    namespace="tools.amazon_s3",
    secrets=[s3_secret],
)
async def call_method(
    method_name: Annotated[str, Doc("S3 method name.")],
    params: Annotated[dict[str, Any], Doc("S3 method parameters.")],
    endpoint_url: Annotated[
        str | None,
        Doc("Endpoint URL for the AWS S3 service."),
    ] = None,
    region_name: Annotated[
        str | None,
        Doc(_AWS_SERVICE_REGION_DOC),
    ] = None,
    role_arn: aws_boto3.RoleArn = None,
    role_session_name: aws_boto3.RoleSessionName = None,
    external_id: aws_boto3.ExternalId = None,
    duration_seconds: aws_boto3.DurationSeconds = None,
) -> dict[str, Any]:
    session = await aws_boto3.get_session(
        region_name=region_name,
        role_arn=role_arn,
        role_session_name=role_session_name,
        external_id=external_id,
        duration_seconds=duration_seconds,
    )
    async with session.client("s3", endpoint_url=endpoint_url) as s3_client:  # type: ignore
        return await getattr(s3_client, method_name)(**params)


@registry.register(
    default_title="Get S3 object",
    description=(
        "Download a text object from S3 and return its body as a string. "
        "Gzip objects are decompressed, and the result is capped at max_bytes."
    ),
    display_group="Amazon S3",
    doc_url="https://docs.aws.amazon.com/boto3/latest/reference/services/s3.html#S3.Client.get_object",
    namespace="tools.amazon_s3",
    secrets=[s3_secret],
)
async def get_object(
    bucket: Annotated[str, Doc("S3 bucket name.")],
    key: Annotated[str, Doc("S3 object key.")],
    endpoint_url: Annotated[
        str | None,
        Doc("Endpoint URL for the AWS S3 service."),
    ] = None,
    region_name: Annotated[
        str | None,
        Doc(_AWS_SERVICE_REGION_DOC),
    ] = None,
    role_arn: aws_boto3.RoleArn = None,
    role_session_name: aws_boto3.RoleSessionName = None,
    external_id: aws_boto3.ExternalId = None,
    duration_seconds: aws_boto3.DurationSeconds = None,
    version_id: Annotated[
        str | None,
        Doc("Version ID used to reference a specific version of the object."),
    ] = None,
    max_bytes: MaxBytes = _MAX_BYTES_LIMIT,
) -> str:
    _check_max_bytes(max_bytes)
    session = await aws_boto3.get_session(
        region_name=region_name,
        role_arn=role_arn,
        role_session_name=role_session_name,
        external_id=external_id,
        duration_seconds=duration_seconds,
    )
    params: dict[str, Any] = {"Bucket": bucket, "Key": key}
    if version_id:
        params["VersionId"] = version_id
    async with session.client("s3", endpoint_url=endpoint_url) as s3_client:  # type: ignore
        obj = await s3_client.get_object(**params)
        data, gzipped, truncated = await _read_bounded(obj["Body"], max_bytes)

    text = _decode_utf8(data, key=key, truncated=truncated)
    if not truncated:
        return text
    if gzipped:
        whole = "the decompressed object"
    else:
        whole = f"the {obj.get('ContentLength')}-byte object"
    return (
        f"{text}\n\n[Truncated by Tracecat: returned at most the first {max_bytes} "
        f"bytes of {whole}. Raise max_bytes (at most "
        f"{_MAX_BYTES_LIMIT}) to read more.]"
    )


@registry.register(
    default_title="List S3 objects",
    description="List objects in an S3 bucket.",
    display_group="Amazon S3",
    doc_url="https://docs.aws.amazon.com/boto3/latest/reference/services/s3.html#S3.Client.list_objects_v2",
    namespace="tools.amazon_s3",
    secrets=[s3_secret],
)
async def list_objects(
    bucket: Annotated[str, Doc("S3 bucket name.")],
    prefix: Annotated[str | None, Doc("S3 object key prefix.")] = None,
    limit: Annotated[int, Doc("Maximum number of objects to return.")] = 1000,
    endpoint_url: Annotated[
        str | None,
        Doc("Endpoint URL for the AWS S3 service."),
    ] = None,
    region_name: Annotated[
        str | None,
        Doc(_AWS_SERVICE_REGION_DOC),
    ] = None,
    role_arn: aws_boto3.RoleArn = None,
    role_session_name: aws_boto3.RoleSessionName = None,
    external_id: aws_boto3.ExternalId = None,
    duration_seconds: aws_boto3.DurationSeconds = None,
) -> ListObjectsV2OutputTypeDef:
    session = await aws_boto3.get_session(
        region_name=region_name,
        role_arn=role_arn,
        role_session_name=role_session_name,
        external_id=external_id,
        duration_seconds=duration_seconds,
    )
    async with session.client("s3", endpoint_url=endpoint_url) as s3_client:  # type: ignore
        if prefix:
            response = await s3_client.list_objects_v2(
                Bucket=bucket, Prefix=prefix, MaxKeys=limit
            )
        else:
            response = await s3_client.list_objects_v2(Bucket=bucket, MaxKeys=limit)
    return response


@registry.register(
    default_title="Copy S3 objects",
    description="Copy S3 objects from one bucket to another.",
    display_group="Amazon S3",
    doc_url="https://docs.aws.amazon.com/boto3/latest/reference/services/s3.html#S3.Client.copy_object",
    namespace="tools.amazon_s3",
    secrets=[s3_secret],
)
async def copy_objects(
    src_bucket: Annotated[str, Doc("Source S3 bucket name.")],
    dst_bucket: Annotated[str, Doc("Destination S3 bucket name.")],
    prefix: Annotated[
        str,
        Doc(
            "Prefix to filter objects (e.g., 'manuals_' for keys starting with 'manuals_')."
        ),
    ],
    endpoint_url: Annotated[
        str | None,
        Doc("Endpoint URL for the AWS S3 service."),
    ] = None,
    region_name: Annotated[
        str | None,
        Doc(_AWS_SERVICE_REGION_DOC),
    ] = None,
    role_arn: aws_boto3.RoleArn = None,
    role_session_name: aws_boto3.RoleSessionName = None,
    external_id: aws_boto3.ExternalId = None,
    duration_seconds: aws_boto3.DurationSeconds = None,
) -> list[dict[str, Any]]:
    """Copy S3 objects from one bucket to another.

    Lists objects in source bucket with the given prefix and copies them to
    the destination bucket, keeping the same S3 keys. Returns a list of copy operation results from S3.

    Returns:
        A list of copy operation results from S3.
    """
    session = await aws_boto3.get_session(
        region_name=region_name,
        role_arn=role_arn,
        role_session_name=role_session_name,
        external_id=external_id,
        duration_seconds=duration_seconds,
    )
    results = []

    async with session.client("s3", endpoint_url=endpoint_url) as s3_client:
        # List objects with prefix
        response = await s3_client.list_objects_v2(Bucket=src_bucket, Prefix=prefix)

        if "Contents" not in response:
            return []

        # Copy all objects and collect results
        for obj in response["Contents"]:
            key = obj.get("Key")
            if not key:
                continue

            try:
                copy_source = f"{src_bucket}/{key}"
                copy_response = await s3_client.copy_object(
                    CopySource=copy_source, Bucket=dst_bucket, Key=key
                )
                # Create a new dict with response data plus metadata
                result = {
                    **copy_response,
                    "Key": key,
                    "SourceBucket": src_bucket,
                    "DestinationBucket": dst_bucket,
                }
                results.append(result)
            except Exception as e:
                # Include failed operations in results with error info
                results.append(
                    {
                        "Key": key,
                        "SourceBucket": src_bucket,
                        "DestinationBucket": dst_bucket,
                        "Error": str(e),
                        "Success": False,
                    }
                )

    return results


@registry.register(
    default_title="Get S3 objects",
    description="Download multiple S3 objects and return their bodies as strings.",
    display_group="Amazon S3",
    doc_url="https://docs.aws.amazon.com/boto3/latest/reference/services/s3.html#S3.Client.get_object",
    namespace="tools.amazon_s3",
    secrets=[s3_secret],
)
async def get_objects(
    bucket: Annotated[str, Doc("S3 bucket name.")],
    keys: Annotated[list[str], Doc("S3 object keys.")],
    endpoint_url: Annotated[
        str | None,
        Doc("Endpoint URL for the AWS S3 service."),
    ] = None,
    region_name: Annotated[
        str | None,
        Doc(_AWS_SERVICE_REGION_DOC),
    ] = None,
    role_arn: aws_boto3.RoleArn = None,
    role_session_name: aws_boto3.RoleSessionName = None,
    external_id: aws_boto3.ExternalId = None,
    duration_seconds: aws_boto3.DurationSeconds = None,
    max_bytes: MaxBytes = _MAX_BYTES_LIMIT,
) -> list[str]:
    _check_max_bytes(max_bytes)

    # To prevent Amazon S3 rate limits and resource exhaustion
    @retry(
        stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10)
    )
    async def get_object_fn(key: str) -> str:
        # Use semaphore to limit concurrent S3 operations
        async with _s3_semaphore:
            return await get_object(
                bucket,
                key,
                max_bytes=max_bytes,
                endpoint_url=endpoint_url,
                region_name=region_name,
                role_arn=role_arn,
                role_session_name=role_session_name,
                external_id=external_id,
                duration_seconds=duration_seconds,
            )

    return await asyncio.gather(*[get_object_fn(key) for key in keys])


@registry.register(
    default_title="Put S3 object",
    description="Put an object to S3.",
    display_group="Amazon S3",
    doc_url="https://docs.aws.amazon.com/boto3/latest/reference/services/s3.html#S3.Client.put_object",
    namespace="tools.amazon_s3",
    secrets=[s3_secret],
)
async def put_object(
    bucket: Annotated[str, Doc("S3 bucket name.")],
    key: Annotated[str, Doc("S3 object key.")],
    file_data: Annotated[str, Doc("Base64 encoded content of the file to upload.")],
    endpoint_url: Annotated[
        str | None,
        Doc("Endpoint URL for the AWS S3 service."),
    ] = None,
    region_name: Annotated[
        str | None,
        Doc(_AWS_SERVICE_REGION_DOC),
    ] = None,
    role_arn: aws_boto3.RoleArn = None,
    role_session_name: aws_boto3.RoleSessionName = None,
    external_id: aws_boto3.ExternalId = None,
    duration_seconds: aws_boto3.DurationSeconds = None,
) -> None:
    """Uploads an object to S3. The object key is validated and content decoded.

    Args:
        bucket: S3 bucket name.
        key: S3 object key. Will be validated for null bytes.
        file_data: Base64 encoded string of the file content.
    Raises:
        ValueError: If key is invalid, file_data is not valid base64, or file exceeds size limit.
    """
    if not key or "\x00" in key:
        raise ValueError(
            f"Invalid S3 object key '{key}': cannot be empty or contain null bytes."
        )

    try:
        content_bytes = base64.b64decode(file_data, validate=True)
    except binascii.Error as e:
        raise ValueError(f"Invalid base64 data for S3 object '{key}': {str(e)}") from e

    if len(content_bytes) > TRACECAT__MAX_FILE_SIZE_BYTES:
        raise ValueError(
            f"S3 object '{key}' exceeds maximum size limit of "
            f"{TRACECAT__MAX_FILE_SIZE_BYTES // 1024 // 1024}MB."
        )

    session = await aws_boto3.get_session(
        region_name=region_name,
        role_arn=role_arn,
        role_session_name=role_session_name,
        external_id=external_id,
        duration_seconds=duration_seconds,
    )
    async with session.client("s3", endpoint_url=endpoint_url) as s3_client:  # type: ignore
        await s3_client.put_object(Bucket=bucket, Key=key, Body=content_bytes)


@registry.register(
    default_title="Delete S3 object",
    description="Delete an object from S3.",
    display_group="Amazon S3",
    doc_url="https://docs.aws.amazon.com/boto3/latest/reference/services/s3.html#S3.Client.delete_object",
    namespace="tools.amazon_s3",
    secrets=[s3_secret],
)
async def delete_object(
    bucket: Annotated[str, Doc("S3 bucket name.")],
    key: Annotated[str, Doc("S3 object key.")],
    endpoint_url: Annotated[
        str | None,
        Doc("Endpoint URL for the AWS S3 service."),
    ] = None,
    region_name: Annotated[
        str | None,
        Doc(_AWS_SERVICE_REGION_DOC),
    ] = None,
    role_arn: aws_boto3.RoleArn = None,
    role_session_name: aws_boto3.RoleSessionName = None,
    external_id: aws_boto3.ExternalId = None,
    duration_seconds: aws_boto3.DurationSeconds = None,
) -> DeleteObjectOutputTypeDef:
    session = await aws_boto3.get_session(
        region_name=region_name,
        role_arn=role_arn,
        role_session_name=role_session_name,
        external_id=external_id,
        duration_seconds=duration_seconds,
    )
    async with session.client("s3", endpoint_url=endpoint_url) as s3_client:  # type: ignore
        response = await s3_client.delete_object(Bucket=bucket, Key=key)
    return response
