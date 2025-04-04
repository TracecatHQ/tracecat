from __future__ import annotations

from collections.abc import AsyncGenerator, Iterable
from contextlib import asynccontextmanager
from typing import Any, ClassVar

import aioboto3
import botocore.exceptions
import orjson
from aiobotocore.config import AioConfig
from types_aiobotocore_s3.client import S3Client
from types_aiobotocore_s3.type_defs import (
    CreateBucketOutputTypeDef,
    PutObjectOutputTypeDef,
)

from tracecat import config
from tracecat.concurrency import GatheringTaskGroup
from tracecat.config import TRACECAT__BUCKET_NAME
from tracecat.ee.store import hashing
from tracecat.ee.store.models import ObjectRef
from tracecat.logger import logger


class ObjectStore:
    """Async MinIO object store."""

    _instance: ClassVar[ObjectStore | None] = None

    def __init__(
        self,
        url: str | None = None,
        region: str = "us-east-1",
        access_key: str | None = None,
        secret_key: str | None = None,
        bucket_name: str = TRACECAT__BUCKET_NAME,
        namespace: str = "default",
    ) -> None:
        self.url = url or config.MINIO_ENDPOINT_URL
        self.region = region
        self._access_key = access_key or config.MINIO_ACCESS_KEY
        self._secret_key = secret_key or config.MINIO_SECRET_KEY
        self.session = aioboto3.Session(
            aws_access_key_id=self._access_key,
            aws_secret_access_key=self._secret_key,
        )
        self.config = AioConfig(
            s3={"addressing_style": "path"},  # Important for MinIO compatibility
            signature_version="s3v4",
            max_pool_connections=50,
            retries={
                "max_attempts": 6,
                "mode": "standard",
            },
        )
        self.bucket_name = bucket_name
        self.namespace = namespace  # Blob namespace
        self.logger = logger.bind(service="store")

    @asynccontextmanager
    async def _client(self) -> AsyncGenerator[S3Client, None]:
        async with self.session.client(
            "s3", endpoint_url=self.url, config=self.config
        ) as client:
            yield client

    @classmethod
    def get(cls) -> ObjectStore:
        """Get the singleton MinIO client instance.

        Returns:
            ObjectStore: A singleton instance of the ObjectStore class
        """
        if cls._instance is None:
            logger.warning(
                "NEW INSTANCE",
                url=config.MINIO_ENDPOINT_URL,
                region=config.MINIO_REGION,
                access_key=config.MINIO_ACCESS_KEY,
                secret_key=config.MINIO_SECRET_KEY,
                bucket_name=config.TRACECAT__BUCKET_NAME,
            )
            cls._instance = cls(
                url=config.MINIO_ENDPOINT_URL,
                region=config.MINIO_REGION,
                access_key=config.MINIO_ACCESS_KEY,
                secret_key=config.MINIO_SECRET_KEY,
                bucket_name=config.TRACECAT__BUCKET_NAME,
            )
        return cls._instance

    async def create_bucket(
        self, bucket_name: str | None = None
    ) -> CreateBucketOutputTypeDef:
        bucket_name = bucket_name or self.bucket_name
        async with self._client() as client:
            return await client.create_bucket(Bucket=bucket_name)

    def make_key(self, namespace: str, digest: str) -> str:
        return f"blobs/{namespace}/{digest}"

    """Put"""

    async def put_object(
        self, obj: Any, content_type: str = "application/json"
    ) -> ObjectRef:
        """Insert a JSON object into the object store and return the object ref."""
        data = orjson.dumps(obj)
        return await self.put_object_bytes(data, content_type=content_type)

    async def put_object_bytes(
        self, data: bytes, content_type: str = "application/json"
    ) -> ObjectRef:
        """Insert a bytes object into the object store and return the object ref."""
        digest = hashing.digest(data)
        key = self.make_key(namespace=self.namespace, digest=digest)
        async with self._client() as client:
            await put_object(
                client=client,
                bucket=self.bucket_name,
                key=key,
                data=data,
                content_type=content_type,
            )
        return ObjectRef(
            key=key,
            size=len(data),
            digest=digest,
            metadata={"encoding": "json/plain"},
        )

    """Get"""

    async def get_byte_objects_by_key(self, keys: Iterable[str]) -> list[bytes | None]:
        """Retrieve byte objects by keys, filtering out any that don't exist."""
        async with self._client() as client, GatheringTaskGroup() as tg:
            for key in keys:
                coro = get_object(client, bucket=self.bucket_name, key=key)
                tg.create_task(coro)
        return tg.results()

    async def get_object(self, ref: ObjectRef) -> Any:
        """Retrieve an object from MinIO by its object reference."""
        async with self._client() as client:
            data = await get_object(client, bucket=self.bucket_name, key=ref.key)

        if data is None:
            return None

        # At this point, data is guaranteed to be bytes
        data_bytes: bytes = data
        hashing.validate_digest(data=data_bytes, digest=ref.digest)
        return orjson.loads(data_bytes)

    async def generate_presigned_download_url(
        self, key: str, expires_in_seconds: int = 3600
    ) -> str:
        """Generate a presigned URL for downloading an object from MinIO.

        Args:
            ref: The ObjectRef reference to the object
            expires_in_seconds: How long the URL should be valid for (default: 1 hour)

        Returns:
            A presigned URL that can be used to download the object

        Raises:
            ValueError: If the object doesn't exist
        """
        # Validate that the object exists
        async with self._client() as client:
            try:
                await client.head_object(Bucket=self.bucket_name, Key=key)
            except botocore.exceptions.ClientError as e:
                if e.response.get("Error", {}).get("Code") == "404":
                    raise ValueError(f"Object does not exist: {key}") from None
                raise

            # Generate the presigned URL
            url = await client.generate_presigned_url(
                "get_object",
                Params={"Bucket": self.bucket_name, "Key": key},
                ExpiresIn=expires_in_seconds,
            )

            self.logger.info(
                "Generated presigned download URL",
                key=key,
                expires_in=expires_in_seconds,
            )
            return url


async def get_object(client: S3Client, *, bucket: str, key: str) -> bytes | None:
    """Get a bytes object from the object store. Returns None if the key doesn't exist."""
    try:
        response = await client.get_object(Bucket=bucket, Key=key)
        async with response["Body"] as stream:
            data = await stream.read()
        return data
    except botocore.exceptions.ClientError as e:
        if e.response.get("Error", {}).get("Code") == "NoSuchKey":
            return None
        raise


async def put_object(
    client: S3Client,
    *,
    bucket: str,
    key: str,
    data: bytes,
    content_type: str = "application/json",
) -> PutObjectOutputTypeDef | None:
    """Put a bytes object into the object store.

    If the object already exists, it will not be overwritten.
    """
    try:
        response = await client.put_object(
            Bucket=bucket,
            Key=key,
            Body=data,
            IfNoneMatch="*",  # Don't overwrite existing objects
            ContentType=content_type,
        )
        return response
    except botocore.exceptions.ClientError as e:
        if e.response.get("Error", {}).get("Code") == "PreconditionFailed":
            return None
        raise


async def setup_store():
    store = ObjectStore.get()
    try:
        await store.create_bucket()
        logger.info("Object store setup complete", bucket=store.bucket_name)
    except Exception as e:
        exc_type = e.__class__.__name__
        if exc_type == "BucketAlreadyOwnedByYou":
            logger.info("Object store already setup", bucket=store.bucket_name)
        else:
            logger.warning(
                "Couldn't set up object store", error=e, bucket=store.bucket_name
            )
