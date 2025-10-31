"""MinIO integrations: https://docs.min.io/community/minio-object-store/developers/python/API.html"""

import base64
import io
from typing import Annotated, Any
from typing_extensions import Doc
from tracecat_registry import RegistrySecret, registry, secrets
from minio import Minio
from minio.commonconfig import CopySource
from tracecat.utils import to_jsonable_python
from tracecat.config import TRACECAT__MAX_FILE_SIZE_BYTES

minio_secret = RegistrySecret(
    name="minio",
    keys=["MINIO_ACCESS_KEY", "MINIO_SECRET_KEY"],
    optional_keys=["MINIO_ENDPOINT", "MINIO_REGION"],
)

"""MinIO credentials.

- name: `minio`
- keys:
    - `MINIO_ACCESS_KEY`: Required MinIO access key.
    - `MINIO_SECRET_KEY`: Required MinIO secret key.
- optional_keys:
    - `MINIO_ENDPOINT`: Optional MinIO endpoint URL.
    - `MINIO_REGION`: Optional MinIO region.
"""


def _get_client(
    endpoint: str | None = None, cert_check: bool = True, secure: bool = True
) -> Minio:
    """Helper function to create MinIO client."""
    return Minio(
        endpoint=endpoint or secrets.get("MINIO_ENDPOINT"),
        access_key=secrets.get("MINIO_ACCESS_KEY"),
        secret_key=secrets.get("MINIO_SECRET_KEY"),
        region=secrets.get("MINIO_REGION"),
        secure=secure,
        cert_check=cert_check,
    )


@registry.register(
    default_title="Call MinIO method",
    description="Instantiate a MinIO client and call a MinIO method.",
    display_group="MinIO",
    doc_url="https://docs.min.io/community/minio-object-store/developers/python/API.html",
    namespace="tools.minio",
    secrets=[minio_secret],
)
def call_method(
    method_name: Annotated[str, Doc("MinIO method name.")],
    params: Annotated[dict[str, Any], Doc("MinIO method parameters.")],
    endpoint: Annotated[str | None, Doc("MinIO endpoint URL.")] = None,
    secure: Annotated[bool, Doc("Whether to use HTTPS connection.")] = True,
    cert_check: Annotated[
        bool, Doc("Whether to check the server certificate for HTTPS connection.")
    ] = True,
) -> dict[str, Any]:
    client = _get_client(endpoint=endpoint, secure=secure, cert_check=cert_check)
    return to_jsonable_python(getattr(client, method_name)(**params))


@registry.register(
    default_title="Get MinIO object",
    description="Download an object from MinIO and return its body as a string.",
    display_group="MinIO",
    doc_url="https://docs.min.io/community/minio-object-store/developers/python/API.html#get_object",
    namespace="tools.minio",
    secrets=[minio_secret],
)
def get_object(
    bucket: Annotated[str, Doc("MinIO bucket name.")],
    key: Annotated[str, Doc("MinIO object key.")],
    endpoint: Annotated[str | None, Doc("MinIO endpoint URL.")] = None,
    cert_check: Annotated[
        bool, Doc("Whether to check the server certificate for HTTPS connection.")
    ] = True,
    secure: Annotated[bool, Doc("Whether to use HTTPS connection.")] = True,
) -> str:
    client = _get_client(endpoint, cert_check, secure)
    response = client.get_object(bucket, key)
    data = response.read()
    response.close()
    response.release_conn()
    return data.decode("utf-8")


@registry.register(
    default_title="List MinIO objects",
    description="List objects in a MinIO bucket.",
    display_group="MinIO",
    doc_url="https://docs.min.io/community/minio-object-store/developers/python/API.html#list_objects",
    namespace="tools.minio",
    secrets=[minio_secret],
)
def list_objects(
    bucket: Annotated[str, Doc("MinIO bucket name.")],
    prefix: Annotated[str | None, Doc("MinIO object key prefix.")] = None,
    recursive: Annotated[bool, Doc("List recursively.")] = True,
    endpoint: Annotated[str | None, Doc("MinIO endpoint URL.")] = None,
    cert_check: Annotated[
        bool, Doc("Whether to check the server certificate for HTTPS connection.")
    ] = True,
    secure: Annotated[bool, Doc("Whether to use HTTPS connection.")] = True,
) -> list[dict[str, Any]]:
    client = _get_client(endpoint, cert_check, secure)
    objects = client.list_objects(bucket, prefix=prefix, recursive=recursive)
    return to_jsonable_python(list(objects))


@registry.register(
    default_title="Copy MinIO objects",
    description="Copy MinIO objects from one bucket to another.",
    display_group="MinIO",
    doc_url="https://docs.min.io/community/minio-object-store/developers/python/API.html#copy_object",
    namespace="tools.minio",
    secrets=[minio_secret],
)
def copy_objects(
    src_bucket: Annotated[str, Doc("Source MinIO bucket name.")],
    dst_bucket: Annotated[str, Doc("Destination MinIO bucket name.")],
    prefix: Annotated[str, Doc("Prefix to filter objects.")],
    endpoint: Annotated[str | None, Doc("MinIO endpoint URL.")] = None,
    cert_check: Annotated[
        bool, Doc("Whether to check the server certificate for HTTPS connection.")
    ] = True,
    secure: Annotated[bool, Doc("Whether to use HTTPS connection.")] = True,
) -> list[dict[str, Any]]:
    client = _get_client(endpoint, cert_check, secure)
    objects = client.list_objects(src_bucket, prefix=prefix, recursive=True)
    results = []

    for obj in objects:
        key = obj.object_name
        if not key:
            continue
        copy_source = CopySource(src_bucket, key)
        result = client.copy_object(dst_bucket, key, copy_source)
        results.append(to_jsonable_python(result))

    return results


@registry.register(
    default_title="Get MinIO objects",
    description="Download multiple MinIO objects and return their bodies as strings.",
    display_group="MinIO",
    doc_url="https://docs.min.io/community/minio-object-store/developers/python/API.html#get_object",
    namespace="tools.minio",
    secrets=[minio_secret],
)
def get_objects(
    bucket: Annotated[str, Doc("MinIO bucket name.")],
    keys: Annotated[list[str], Doc("MinIO object keys.")],
    endpoint: Annotated[str | None, Doc("MinIO endpoint URL.")] = None,
    cert_check: Annotated[
        bool, Doc("Whether to check the server certificate for HTTPS connection.")
    ] = True,
    secure: Annotated[bool, Doc("Whether to use HTTPS connection.")] = True,
) -> list[str]:
    client = _get_client(endpoint, cert_check, secure)
    results = []

    for key in keys:
        response = client.get_object(bucket, key)
        data = response.read()
        response.close()
        response.release_conn()
        results.append(data.decode("utf-8"))

    return results


@registry.register(
    default_title="Put MinIO object",
    description="Put an object to MinIO.",
    display_group="MinIO",
    doc_url="https://docs.min.io/community/minio-object-store/developers/python/API.html#put_object",
    namespace="tools.minio",
    secrets=[minio_secret],
)
def put_object(
    bucket: Annotated[str, Doc("MinIO bucket name.")],
    key: Annotated[str, Doc("MinIO object key.")],
    file_data: Annotated[str, Doc("Base64 encoded content of the file to upload.")],
    endpoint: Annotated[str | None, Doc("MinIO endpoint URL.")] = None,
    cert_check: Annotated[
        bool, Doc("Whether to check the server certificate for HTTPS connection.")
    ] = True,
    secure: Annotated[bool, Doc("Whether to use HTTPS connection.")] = True,
) -> dict[str, Any]:
    if not key or "\x00" in key:
        raise ValueError(
            f"Invalid MinIO object key '{key}': cannot be empty or contain null bytes."
        )

    content_bytes = base64.b64decode(file_data, validate=True)

    if len(content_bytes) > TRACECAT__MAX_FILE_SIZE_BYTES:
        raise ValueError(
            f"MinIO object '{key}' exceeds maximum size limit of "
            f"{TRACECAT__MAX_FILE_SIZE_BYTES // 1024 // 1024}MB."
        )

    client = _get_client(endpoint, cert_check, secure)
    result = client.put_object(
        bucket, key, io.BytesIO(content_bytes), len(content_bytes)
    )
    return to_jsonable_python(result)


@registry.register(
    default_title="Delete MinIO object",
    description="Delete an object from MinIO.",
    display_group="MinIO",
    doc_url="https://docs.min.io/community/minio-object-store/developers/python/API.html#remove_object",
    namespace="tools.minio",
    secrets=[minio_secret],
)
def delete_object(
    bucket: Annotated[str, Doc("MinIO bucket name.")],
    key: Annotated[str, Doc("MinIO object key.")],
    endpoint: Annotated[str | None, Doc("MinIO endpoint URL.")] = None,
    cert_check: Annotated[
        bool, Doc("Whether to check the server certificate for HTTPS connection.")
    ] = True,
    secure: Annotated[bool, Doc("Whether to use HTTPS connection.")] = True,
) -> dict[str, Any]:
    client = _get_client(endpoint, cert_check, secure)
    response = client.remove_object(bucket, key)
    return to_jsonable_python(response)


@registry.register(
    default_title="List MinIO buckets",
    description="List MinIO buckets",
    display_group="MinIO",
    doc_url="https://docs.min.io/community/minio-object-store/developers/python/API.html#list_buckets",
    namespace="tools.minio",
    secrets=[minio_secret],
)
def list_buckets(
    endpoint: Annotated[str | None, Doc("MinIO endpoint URL.")] = None,
    cert_check: Annotated[
        bool, Doc("Whether to check the server certificate for HTTPS connection.")
    ] = True,
    secure: Annotated[bool, Doc("Whether to use HTTPS connection.")] = True,
) -> list[dict[str, Any]]:
    client = _get_client(endpoint, cert_check, secure)
    return to_jsonable_python(client.list_buckets())
