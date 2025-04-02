from __future__ import annotations

from collections.abc import AsyncGenerator, Iterable
from contextlib import asynccontextmanager
from typing import Any, ClassVar

import aioboto3
import botocore.exceptions
import orjson
from aiobotocore.config import AioConfig
from temporalio import activity
from types_aiobotocore_s3.client import S3Client
from types_aiobotocore_s3.type_defs import (
    CreateBucketOutputTypeDef,
    PutObjectOutputTypeDef,
)

from tracecat import config
from tracecat.concurrency import GatheringTaskGroup
from tracecat.config import TRACECAT__BUCKET_NAME
from tracecat.dsl.models import ExecutionContext, TaskResult
from tracecat.ee.store import hashing
from tracecat.ee.store.constants import OBJECT_REF_RESULT_TYPE
from tracecat.ee.store.models import (
    ObjectRef,
    ResolveObjectRefsActivityInput,
    StoreWorkflowResultActivityInput,
    as_object_ref,
)
from tracecat.expressions.common import ExprContext
from tracecat.expressions.core import extract_action_and_secret_expressions
from tracecat.expressions.eval import eval_templated_object
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

    async def resolve_object_refs(
        self, obj: Any, context: ExecutionContext
    ) -> ExecutionContext:
        """Resolve the minimal set of object refs in the execution context."""
        exprs = extract_action_and_secret_expressions(obj=obj)
        extracted_action_refs = exprs[ExprContext.ACTIONS]
        if not extracted_action_refs:
            logger.info("No action refs in result")
            return context

        # (2) Pull action results from the store
        # We only pull the action results that are actually used in the template
        # We need to populate the action context with the action results
        # Inside the ExecutionContext, each action ref is mapped to an object ref
        # Grab each object ref and resolve it
        action_refs = list(extracted_action_refs)
        # Read keys from the action context.
        # This should be a dict[str, TaskResult]
        # NOTE: We must only replace TaskResult.result with the result
        action_context: dict[str, TaskResult] = context.get(ExprContext.ACTIONS, {})
        logger.warning("Action context", action_context=action_context)

        ref2key: dict[str, ObjectRef] = {}
        for act_ref in action_refs:
            # For each action ref, we check if it's a blob
            act_res = action_context.get(act_ref)
            if act_res is None:
                # Shouldn't happen
                logger.warning("Action ref not found in action context", ref=act_ref)
                continue
            if act_res.get("result_typename") == OBJECT_REF_RESULT_TYPE:
                # This is a blob, parse it as object ref
                result = act_res.get("result")
                if obj_ref := as_object_ref(result):
                    ref2key[act_ref] = obj_ref
                else:
                    # Shouldn't happen
                    logger.warning(
                        "Couldn't parse action ref result as ObjectRef",
                        ref=act_ref,
                        result=result,
                    )
        # NOTE(perf): We could filter for unique keys here
        result_objs = await self.get_byte_objects_by_key(
            keys=[ref.key for ref in ref2key.values()]
        )
        logger.warning("Got result objs", n=len(result_objs))

        # We only update the actions that we fetched
        for (act_ref, obj_ref), fetched_bytes in zip(
            ref2key.items(), result_objs, strict=True
        ):
            if fetched_bytes is None:
                logger.warning("Object not found in store", key=obj_ref.key)
                continue

            # TODO: Handle checksum mismatch
            hashing.validate_digest(data=fetched_bytes, digest=obj_ref.digest)
            result = orjson.loads(fetched_bytes)
            action_context[act_ref]["result"] = result
            action_context[act_ref]["result_typename"] = type(result).__name__

        context.update(ACTIONS=action_context)
        logger.warning("Updated execution context", context=context)
        return context

    @staticmethod
    @activity.defn
    async def resolve_object_refs_activity(
        input: ResolveObjectRefsActivityInput,
    ) -> ExecutionContext:
        """Resolve the minimal set of object refs from the execution context."""
        return await ObjectStore.get().resolve_object_refs(input.obj, input.context)

    @staticmethod
    @activity.defn
    async def store_workflow_result_activity(
        input: StoreWorkflowResultActivityInput,
    ) -> ObjectRef:
        """Store the result of a workflow."""
        logger.info("Resolving templated object")
        store = ObjectStore.get()
        context = await store.resolve_object_refs(obj=input.args, context=input.context)
        result = eval_templated_object(input.args, operand=context)
        obj_ref = await store.put_object(obj=result)
        return obj_ref


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
