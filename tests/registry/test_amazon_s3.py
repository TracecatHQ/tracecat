"""Tests for S3 object body decoding."""

import gzip
from unittest.mock import AsyncMock, MagicMock

import pytest
from tracecat_registry.integrations import amazon_s3


@pytest.fixture
def s3_client(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    client = MagicMock()
    client.get_object = AsyncMock()
    session = MagicMock()
    session.client.return_value.__aenter__.return_value = client
    monkeypatch.setattr(
        amazon_s3.aws_boto3, "get_session", AsyncMock(return_value=session)
    )
    return client


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("key", "body", "expected"),
    [
        ("logs/audit.json", b'{"message":"plain"}', '{"message":"plain"}'),
        (
            "logs/audit.json.gz",
            gzip.compress(b'{"message":"compressed"}'),
            '{"message":"compressed"}',
        ),
        (
            "logs/extensionless",
            gzip.compress('{"message":"caf\u00e9"}'.encode()),
            '{"message":"caf\u00e9"}',
        ),
    ],
    ids=["plain", "cloudtrail-gzip", "extensionless-gzip"],
)
async def test_get_object_decodes_plain_and_gzip_bodies(
    s3_client: MagicMock, key: str, body: bytes, expected: str
) -> None:
    stream = MagicMock()
    stream.read = AsyncMock(return_value=body)
    s3_client.get_object.return_value = {"Body": stream}

    result = await amazon_s3.get_object("audit-bucket", key)

    assert result == expected
    s3_client.get_object.assert_awaited_once_with(Bucket="audit-bucket", Key=key)
    stream.read.assert_awaited_once_with()


@pytest.mark.anyio
async def test_get_objects_decodes_mixed_bodies_in_order(s3_client: MagicMock) -> None:
    objects = {
        "first.json.gz": gzip.compress(b'{"event":1}'),
        "second.json": b'{"event":2}',
        "third.json.gz": gzip.compress(b'{"event":3}'),
    }

    async def fetch(*, Bucket: str, Key: str) -> dict[str, MagicMock]:
        assert Bucket == "audit-bucket"
        stream = MagicMock()
        stream.read = AsyncMock(return_value=objects[Key])
        return {"Body": stream}

    s3_client.get_object.side_effect = fetch

    assert await amazon_s3.get_objects("audit-bucket", list(objects)) == [
        '{"event":1}',
        '{"event":2}',
        '{"event":3}',
    ]


@pytest.mark.anyio
async def test_get_object_rejects_truncated_gzip(s3_client: MagicMock) -> None:
    stream = MagicMock()
    stream.read = AsyncMock(return_value=gzip.compress(b"audit event")[:-2])
    s3_client.get_object.return_value = {"Body": stream}

    with pytest.raises(EOFError):
        await amazon_s3.get_object("audit-bucket", "logs/broken.json.gz")
