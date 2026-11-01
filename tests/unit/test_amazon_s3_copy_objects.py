from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import pytest
import tracecat_registry.integrations.amazon_s3 as amazon_s3
import tracecat_registry.integrations.aws_boto3 as aws_boto3
from aiobotocore.session import get_session
from aiobotocore.stub import AioStubber


class _StubbedSession:
    """Session whose S3 client replays queued responses through AioStubber."""

    def __init__(self, responses: list[tuple[str, dict[str, Any], dict[str, Any]]]):
        self._responses = responses
        self.stubber: AioStubber | None = None

    @asynccontextmanager
    async def _client(self) -> AsyncIterator[Any]:
        session = get_session()
        async with session.create_client(
            "s3",
            region_name="us-east-1",
            aws_access_key_id="test",
            aws_secret_access_key="test",
        ) as client:
            with AioStubber(client) as stubber:
                for method, response, params in self._responses:
                    stubber.add_response(method, response, params)
                self.stubber = stubber
                yield client

    def client(self, service_name: str, endpoint_url: str | None = None) -> Any:
        assert service_name == "s3"
        return self._client()


@pytest.mark.anyio
async def test_copy_objects_copies_every_listed_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    list_params = {"Bucket": "src", "Prefix": "logs/"}
    responses: list[tuple[str, dict[str, Any], dict[str, Any]]] = [
        (
            "list_objects_v2",
            {
                "Contents": [{"Key": "logs/a"}],
                "IsTruncated": True,
                "NextContinuationToken": "page-2",
            },
            list_params,
        ),
        (
            "copy_object",
            {},
            {"CopySource": "src/logs/a", "Bucket": "dst", "Key": "logs/a"},
        ),
        (
            "list_objects_v2",
            {"Contents": [{"Key": "logs/b"}], "IsTruncated": False},
            {**list_params, "ContinuationToken": "page-2"},
        ),
        (
            "copy_object",
            {},
            {"CopySource": "src/logs/b", "Bucket": "dst", "Key": "logs/b"},
        ),
    ]
    session = _StubbedSession(responses)

    async def fake_get_session(**_: Any) -> _StubbedSession:
        return session

    monkeypatch.setattr(aws_boto3, "get_session", fake_get_session)

    results = await amazon_s3.copy_objects(
        src_bucket="src", dst_bucket="dst", prefix="logs/"
    )

    assert [result["Key"] for result in results] == ["logs/a", "logs/b"]
    assert all("Error" not in result for result in results)
    assert session.stubber is not None
    session.stubber.assert_no_pending_responses()


@pytest.mark.anyio
async def test_copy_objects_returns_empty_list_when_prefix_has_no_objects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _StubbedSession(
        [
            (
                "list_objects_v2",
                {"IsTruncated": False, "KeyCount": 0},
                {"Bucket": "src", "Prefix": "missing/"},
            )
        ]
    )

    async def fake_get_session(**_: Any) -> _StubbedSession:
        return session

    monkeypatch.setattr(aws_boto3, "get_session", fake_get_session)

    results = await amazon_s3.copy_objects(
        src_bucket="src", dst_bucket="dst", prefix="missing/"
    )

    assert results == []
