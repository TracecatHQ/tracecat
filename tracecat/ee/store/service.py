from __future__ import annotations

from collections.abc import AsyncGenerator, Iterable, Mapping, Sequence
from contextlib import asynccontextmanager
from typing import Any

import aioboto3
import orjson
from botocore.config import Config
from pydantic import BaseModel
from temporalio import activity
from types_aiobotocore_s3.client import S3Client
from types_aiobotocore_s3.type_defs import (
    CreateBucketOutputTypeDef,
    GetObjectOutputTypeDef,
    PutObjectOutputTypeDef,
)

from tracecat import config
from tracecat.concurrency import GatheringTaskGroup
from tracecat.dsl.models import ActionResult, ExecutionContext
from tracecat.ee.store.constants import WORKFLOW_RESULTS_BUCKET
from tracecat.ee.store.models import (
    ActionResultHandle,
    ExecutionResultHandle,
    StoreObjectPtr,
    WorkflowResultHandle,
)
from tracecat.expressions.common import ExprContext
from tracecat.expressions.core import extract_expressions
from tracecat.expressions.eval import eval_templated_object
from tracecat.identifiers import ActionRef, WorkflowExecutionID
from tracecat.logger import logger

_minio_client: MinioStore | None = None


def get_store() -> MinioStore:
    """Get the MinIO client."""
    global _minio_client
    if _minio_client is None:
        _minio_client = MinioStore(
            endpoint_url=config.MINIO_ENDPOINT_URL,
            access_key=config.MINIO_ACCESS_KEY,
            secret_key=config.MINIO_SECRET_KEY,
        )
    return _minio_client


class MinioStore:
    """Async MinIO object store."""

    def __init__(
        self,
        endpoint_url: str,
        region: str = "us-east-1",
        access_key: str | None = None,
        secret_key: str | None = None,
        bucket_name: str = WORKFLOW_RESULTS_BUCKET,
    ) -> None:
        self._endpoint_url = endpoint_url
        self._region = region
        self._session = aioboto3.Session(
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
        )
        self._config = Config(
            s3={"addressing_style": "path"},  # Important for MinIO compatibility
            signature_version="s3v4",
            max_pool_connections=50,
        )
        self._bucket_name = bucket_name
        self.logger = logger.bind(service="store")

    @asynccontextmanager
    async def _client(self) -> AsyncGenerator[S3Client, None]:
        async with self._session.client(
            "s3", endpoint_url=self._endpoint_url, config=self._config
        ) as client:
            yield client

    @property
    def bucket_name(self) -> str:
        return self._bucket_name

    async def create_bucket(
        self, bucket_name: str | None = None
    ) -> CreateBucketOutputTypeDef:
        bucket_name = bucket_name or self.bucket_name
        async with self._client() as client:
            return await client.create_bucket(Bucket=bucket_name)

    async def put(
        self, bucket_name: str, key: str, file_path: str
    ) -> PutObjectOutputTypeDef:
        async with self._client() as client:
            return await client.put_object(Bucket=bucket_name, Key=key, Body=file_path)

    async def get(self, bucket_name: str, key: str) -> GetObjectOutputTypeDef:
        async with self._client() as client:
            return await client.get_object(Bucket=bucket_name, Key=key)

    async def put_json(
        self, bucket_name: str, key: str, data: Mapping[str, Any]
    ) -> PutObjectOutputTypeDef:
        """Store a JSON object in MinIO.

        Args:
            bucket_name: Name of the target bucket
            object_name: Name/path of the object to store
            data: Dictionary to be stored as JSON
        """
        async with self._client() as client:
            return await client.put_object(
                Bucket=bucket_name,
                Key=key,
                Body=orjson.dumps(data),
                ContentType="application/json",
            )

    async def get_json(self, bucket_name: str, key: str) -> Any:
        """Retrieve and parse a JSON object from MinIO.

        Args:
            bucket_name: Name of the bucket
            object_name: Name/path of the object to retrieve

        Returns:
            The parsed JSON data as a dictionary
        """
        async with self._client() as client:
            response = await client.get_object(Bucket=bucket_name, Key=key)
            async with response["Body"] as stream:
                data = await stream.read()
        return orjson.loads(data)

    async def get_json_batched(
        self, bucket_name: str, keys: Sequence[str]
    ) -> list[Any]:
        async with self._client() as client:

            async def coro(key: str):
                response = await client.get_object(Bucket=bucket_name, Key=key)
                async with response["Body"] as stream:
                    data = await stream.read()
                return orjson.loads(data)

            async with GatheringTaskGroup() as tg:
                for key in keys:
                    tg.create_task(coro(key))
            return tg.results()

    async def store_action_result(
        self,
        execution_id: WorkflowExecutionID,
        action_ref: ActionRef,
        action_result: ActionResult,
    ) -> ActionResultHandle:
        """Store action result in structured storage.

        Args:
            workflow_id: ID of the workflow
            execution_id: ID of the execution
            action_ref: Reference of the action
            data: Data to store

        """
        handle = ActionResultHandle(wf_exec_id=execution_id, ref=action_ref)
        await self.put_json(self._bucket_name, handle.to_path(), action_result)
        return handle

    async def store_workflow_result(
        self,
        execution_id: WorkflowExecutionID,
        workflow_result: Any,
    ) -> WorkflowResultHandle:
        """Store workflow result.

        Args:
            workflow_id: ID of the workflow
            execution_id: ID of the execution
            result: Result to store

        """
        handle = WorkflowResultHandle(wf_exec_id=execution_id)
        await self.put_json(self._bucket_name, handle.to_path(), workflow_result)
        return handle

    async def load_execution_result(self, handle: ExecutionResultHandle) -> Any:
        """Retrieve execution result from object storage.

        Args:
            obj_ref: Store object reference

        Returns:
            The stored execution result
        """
        return await self.get_json(self._bucket_name, handle.to_path())

    async def load_action_result(self, handle: ActionResultHandle) -> ActionResult:
        """Retrieve action result from object storage.

        Args:
            obj_ref: Store object reference

        Returns:
            The stored action result
        """
        result = await self.load_execution_result(handle)
        return ActionResult(**result)

    async def load_action_result_batched(
        self,
        execution_id: WorkflowExecutionID,
        action_refs: Iterable[ActionRef],
    ) -> dict[ActionRef, ActionResult]:
        keys = [
            ActionResultHandle(wf_exec_id=execution_id, ref=ref).to_path()
            for ref in action_refs
        ]
        # NOTE: Order is preserved
        results = await self.get_json_batched(self._bucket_name, keys)
        return dict(zip(action_refs, results, strict=False))

    async def resolve_templated_object(
        self, execution_id: WorkflowExecutionID, args: Any, context: ExecutionContext
    ) -> Any:
        extracted_exprs = extract_expressions(args)
        extracted_action_refs = extracted_exprs[ExprContext.ACTIONS]
        if not extracted_action_refs:
            return args
        self.logger.trace(
            "Evaluating template remote", extracted_action_refs=extracted_action_refs
        )
        action_results = await self.load_action_result_batched(
            execution_id=execution_id, action_refs=extracted_action_refs
        )
        operand = context.copy()
        operand[ExprContext.ACTIONS] = action_results
        return eval_templated_object(args, operand=operand)


class StoreWorkflowResultActivityInput(BaseModel):
    execution_id: WorkflowExecutionID
    args: Any
    context: ExecutionContext


@activity.defn
async def store_workflow_result_activity(
    input: StoreWorkflowResultActivityInput,
) -> StoreObjectPtr:
    """Store the result of a workflow."""
    store = get_store()
    logger.info("Resolving templated object", execution_id=input.execution_id)
    obj = await store.resolve_templated_object(
        execution_id=input.execution_id,
        args=input.args,
        context=input.context,
    )
    # Store the result if it's a WorkflowResultHandle
    logger.info("Storing workflow result", execution_id=input.execution_id)
    handle = await store.store_workflow_result(
        execution_id=input.execution_id,
        workflow_result=obj,
    )
    logger.info("Workflow result stored", handle=handle)
    return handle.to_pointer()
