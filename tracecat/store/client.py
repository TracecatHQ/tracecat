import asyncio
import os
from collections.abc import AsyncGenerator, Mapping
from contextlib import asynccontextmanager
from typing import Any

import aioboto3
import orjson
from botocore.config import Config
from types_aiobotocore_s3.client import S3Client
from types_aiobotocore_s3.type_defs import (
    CreateBucketOutputTypeDef,
    GetObjectOutputTypeDef,
    PutObjectOutputTypeDef,
)

from tracecat.dsl.models import ActionResult
from tracecat.identifiers import ActionRef, WorkflowExecutionID, WorkflowID
from tracecat.store.models import ActionResultObject


class MinioClient:
    """Async MinIO client"""

    def __init__(
        self,
        endpoint_url: str,
        region: str = "us-east-1",
    ) -> None:
        self._session = aioboto3.Session(
            aws_access_key_id=os.getenv("MINIO_ACCESS_KEY"),
            aws_secret_access_key=os.getenv("MINIO_SECRET_KEY"),
        )
        self._config = Config(
            s3={"addressing_style": "path"},  # Important for MinIO compatibility
            signature_version="s3v4",
            region_name=region,
        )
        self._endpoint_url = endpoint_url

    @asynccontextmanager
    async def client(self) -> AsyncGenerator[S3Client, None]:
        async with self._session.client(
            "s3", endpoint_url=self._endpoint_url, config=self._config
        ) as client:
            yield client

    async def create_bucket(self, bucket_name: str) -> CreateBucketOutputTypeDef:
        async with self.client() as client:
            return await client.create_bucket(Bucket=bucket_name)

    async def put(
        self, bucket_name: str, object_name: str, file_path: str
    ) -> PutObjectOutputTypeDef:
        async with self.client() as client:
            return await client.put_object(
                Bucket=bucket_name, Key=object_name, Body=file_path
            )

    async def get(self, bucket_name: str, object_name: str) -> GetObjectOutputTypeDef:
        async with self.client() as client:
            return await client.get_object(Bucket=bucket_name, Key=object_name)

    async def put_json(
        self, bucket_name: str, object_name: str, data: Mapping[str, Any]
    ) -> PutObjectOutputTypeDef:
        """Store a JSON object in MinIO.

        Args:
            bucket_name: Name of the target bucket
            object_name: Name/path of the object to store
            data: Dictionary to be stored as JSON
        """
        async with self.client() as client:
            return await client.put_object(
                Bucket=bucket_name,
                Key=object_name,
                Body=orjson.dumps(data),
                ContentType="application/json",
            )

    async def get_json(self, bucket_name: str, object_name: str) -> Mapping[str, Any]:
        """Retrieve and parse a JSON object from MinIO.

        Args:
            bucket_name: Name of the bucket
            object_name: Name/path of the object to retrieve

        Returns:
            The parsed JSON data as a dictionary
        """
        async with self.client() as client:
            response = await client.get_object(Bucket=bucket_name, Key=object_name)
            async with response["Body"] as stream:
                data = await stream.read()
                return orjson.loads(data)

    async def store_action_result(
        self,
        workflow_id: WorkflowID,
        execution_id: WorkflowExecutionID,
        action_ref: ActionRef,
        action_result: ActionResult,
    ) -> None:
        """Store action result in structured storage.

        Args:
            workflow_id: ID of the workflow
            execution_id: ID of the execution
            action_ref: Reference of the action
            data: Data to store

        """
        obj = ActionResultObject(
            workflow_id=workflow_id, execution_id=execution_id, object_name=action_ref
        )
        object_name = obj.to_path()
        await self.put_json("tracecat", object_name, action_result)

    async def load_action_result(
        self,
        workflow_id: WorkflowID,
        execution_id: WorkflowExecutionID,
        action_ref: ActionRef,
    ) -> ActionResult:
        """Retrieve action result from structured storage.

        Args:
            workflow_id: ID of the workflow
            execution_id: ID of the execution
            action_ref: Reference of the action

        Returns:
            The stored data
        """
        obj = ActionResultObject(
            workflow_id=workflow_id, execution_id=execution_id, object_name=action_ref
        )
        object_name = obj.to_path()
        result = await self.get_json("tracecat", object_name)
        return ActionResult(**result)


if __name__ == "__main__":

    async def main() -> None:
        from dotenv import load_dotenv

        load_dotenv()
        client = MinioClient(endpoint_url="http://localhost:9000")

        with open("test2.txt", "w") as f:
            f.write("Hello, world!")

        # await client.create_bucket("test")
        await client.put("test", "test2.txt", "test2.txt")
        result = await client.get("test", "test2.txt")
        print(result)

        # Example JSON data
        test_data = {
            "message": "Hello, JSON!",
            "numbers": [1, 2, 3],
            "nested": {"key": "value"},
        }

        await client.put_json("test", "data.json", test_data)
        result = await client.get_json("test", "data.json")
        print(result)

        # If the bucket doesn't exist, create it
        try:
            await client.create_bucket("tracecat")
        except Exception:
            pass

        workflow_id = "wf-1234567890abcdef1234567890abcdef"
        execution_id = "exec-1234567890abcdef1234567890abcdef"
        action_ref = "my_action"
        data = ActionResult(result="Hello, world!")
        await client.store_action_result(workflow_id, execution_id, action_ref, data)
        result = await client.load_action_result(workflow_id, execution_id, action_ref)
        print(result)
        assert result == data

    asyncio.run(main())
