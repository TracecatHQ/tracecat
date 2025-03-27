from __future__ import annotations

from collections.abc import AsyncGenerator, Iterable, Mapping
from contextlib import asynccontextmanager
from typing import Any, Unpack

import aioboto3
import botocore.exceptions
import orjson
from botocore.config import Config
from types_aiobotocore_s3.client import S3Client
from types_aiobotocore_s3.type_defs import (
    CreateBucketOutputTypeDef,
    GetObjectOutputTypeDef,
    GetObjectRequestTypeDef,
    PutObjectOutputTypeDef,
    PutObjectRequestTypeDef,
)

from tracecat import config
from tracecat.concurrency import GatheringTaskGroup
from tracecat.ee.store.constants import DEFAULT_BUCKET_NAME
from tracecat.logger import logger

_minio_client: ObjectStore | None = None


def get_store() -> ObjectStore:
    """Get the MinIO client."""
    global _minio_client
    if _minio_client is None:
        _minio_client = ObjectStore(
            url=config.MINIO_ENDPOINT_URL,
            access_key=config.MINIO_ACCESS_KEY,
            secret_key=config.MINIO_SECRET_KEY,
        )
    return _minio_client


async def setup_store():
    store = get_store()
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


class ObjectStore:
    """Async MinIO object store."""

    def __init__(
        self,
        url: str | None = None,
        region: str = "us-east-1",
        access_key: str | None = None,
        secret_key: str | None = None,
        bucket_name: str = DEFAULT_BUCKET_NAME,
    ) -> None:
        self.url = url or config.MINIO_ENDPOINT_URL
        self.region = region
        self._access_key = access_key or config.MINIO_ACCESS_KEY
        self._secret_key = secret_key or config.MINIO_SECRET_KEY
        self.session = aioboto3.Session(
            aws_access_key_id=self._access_key,
            aws_secret_access_key=self._secret_key,
        )
        self.config = Config(
            s3={"addressing_style": "path"},  # Important for MinIO compatibility
            signature_version="s3v4",
            max_pool_connections=50,
            retries={
                "max_attempts": 6,
                "mode": "standard",
            },
        )
        self.bucket_name = bucket_name
        self.logger = logger.bind(service="store")

    @asynccontextmanager
    async def _client(self) -> AsyncGenerator[S3Client, None]:
        async with self.session.client(
            "s3", endpoint_url=self.url, config=self.config
        ) as client:
            yield client

    async def create_bucket(
        self, bucket_name: str | None = None
    ) -> CreateBucketOutputTypeDef:
        bucket_name = bucket_name or self.bucket_name
        async with self._client() as client:
            return await client.create_bucket(Bucket=bucket_name)

    async def _exists(self, client: S3Client, bucket_name: str, key: str) -> bool:
        try:
            await client.head_object(Bucket=bucket_name, Key=key)
            return True
        except botocore.exceptions.ClientError as e:
            if e.response.get("Error", {}).get("Code") == "404":
                return False
            raise

    async def _put(
        self, **kwargs: Unpack[PutObjectRequestTypeDef]
    ) -> PutObjectOutputTypeDef | None:
        async with self._client() as client:
            if not await self._exists(client, kwargs["Bucket"], kwargs["Key"]):
                return await client.put_object(**kwargs)
            return None

    async def _get(
        self, **kwargs: Unpack[GetObjectRequestTypeDef]
    ) -> GetObjectOutputTypeDef:
        async with self._client() as client:
            return await client.get_object(**kwargs)

    async def get_one(self, key: str) -> GetObjectOutputTypeDef:
        return await self._get(Bucket=self.bucket_name, Key=key)

    async def get_many(self, keys: list[str]) -> list[GetObjectOutputTypeDef]:
        async with self._client() as client, GatheringTaskGroup() as tg:
            for key in keys:
                coro = client.get_object(Bucket=self.bucket_name, Key=key)
                tg.create_task(coro)
        return tg.results()

    # async def put_json(
    #     self, bucket_name: str, key: str, data: Mapping[str, Any]
    # ) -> PutObjectOutputTypeDef:
    #     """Store a JSON object in MinIO.

    #     Args:
    #         bucket_name: Name of the target bucket
    #         object_name: Name/path of the object to store
    #         data: Dictionary to be stored as JSON

    #     Returns:
    #         The response from the put_object call

    #     Raises:
    #         StoreError: If the object cannot be stored
    #     """
    #     return await self.put(
    #         Bucket=bucket_name,
    #         Key=key,
    #         Body=orjson.dumps(data),
    #         ContentType="application/json",
    #     )

    async def get_json_many(self, keys: list[str]) -> list[Any]:
        """Retrieve and parse a JSON object from MinIO.

        Args:
            keys: List of object keys to retrieve

        Returns:
            The parsed JSON data as a dictionary

        Raises:
            ValueError: If the object cannot be retrieved
        """
        async with self._client() as client, GatheringTaskGroup() as tg:
            for key in keys:
                coro = get_json(client, bucket=self.bucket_name, key=key)
                tg.create_task(coro)
        return tg.results()


async def get_json(client: S3Client, *, bucket: str, key: str) -> Any:
    response = await client.get_object(Bucket=bucket, Key=key)
    async with response["Body"] as stream:
        data = await stream.read()
    return orjson.loads(data)
