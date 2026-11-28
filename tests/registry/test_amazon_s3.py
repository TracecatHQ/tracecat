"""Live Amazon S3 action tests backed by the test MinIO server."""

from __future__ import annotations

import asyncio
from typing import Any

import aioboto3
import pytest
import tracecat_registry.integrations.aws_boto3 as aws_boto3
from minio import Minio
from tracecat_registry.integrations.amazon_s3 import copy_objects

from tests.conftest import MINIO_PORT, _minio_credentials

S3_LIST_PAGE_MAX = 1000


@pytest.fixture
def minio_s3_session(monkeypatch: pytest.MonkeyPatch) -> None:
    access_key, secret_key = _minio_credentials()

    async def get_session(**_: Any) -> aioboto3.Session:
        return aioboto3.Session(
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name="us-east-1",
        )

    monkeypatch.setattr(aws_boto3, "get_session", get_session)


@pytest.fixture
def minio_dst_bucket(minio_client: Minio, minio_bucket: str):
    bucket_name = f"{minio_bucket}-dst"
    minio_client.make_bucket(bucket_name)
    yield bucket_name
    for obj in minio_client.list_objects(bucket_name, recursive=True):
        if obj.object_name:
            minio_client.remove_object(bucket_name, obj.object_name)
    minio_client.remove_bucket(bucket_name)


@pytest.mark.anyio
async def test_copy_objects_copies_every_listing_page(
    minio_s3_session: None, minio_bucket: str, minio_dst_bucket: str
) -> None:
    endpoint_url = f"http://localhost:{MINIO_PORT}"
    keys = {f"logs/{i:04d}.json" for i in range(S3_LIST_PAGE_MAX + 5)}
    session = await aws_boto3.get_session()
    async with session.client("s3", endpoint_url=endpoint_url) as s3_client:
        semaphore = asyncio.Semaphore(50)

        async def put(key: str) -> None:
            async with semaphore:
                await s3_client.put_object(Bucket=minio_bucket, Key=key, Body=b"{}")

        await asyncio.gather(*(put(key) for key in keys))
        await s3_client.put_object(Bucket=minio_bucket, Key="other.json", Body=b"{}")

    results = await copy_objects(
        src_bucket=minio_bucket,
        dst_bucket=minio_dst_bucket,
        prefix="logs/",
        endpoint_url=endpoint_url,
    )

    assert {result["Key"] for result in results} == keys
    assert not [result for result in results if "Error" in result]
