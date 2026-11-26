"""Bounded reads in tools.amazon_s3.get_object, against the test MinIO server."""

import gzip
import tracemalloc
import uuid
import zlib
from collections.abc import AsyncIterator
from dataclasses import dataclass

import aioboto3
import pytest
from tracecat_registry import secrets
from tracecat_registry.integrations import amazon_s3

from tests.conftest import MINIO_PORT, _minio_credentials

ENDPOINT_URL = f"http://localhost:{MINIO_PORT}"
CEILING = 20 * 1024 * 1024


@dataclass(frozen=True)
class Bucket:
    name: str
    session: aioboto3.Session

    async def put(self, key: str, body: bytes) -> str:
        async with self.session.client("s3", endpoint_url=ENDPOINT_URL) as s3:  # type: ignore
            response = await s3.put_object(Bucket=self.name, Key=key, Body=body)
        return response["VersionId"]

    async def get(
        self,
        key: str,
        *,
        version_id: str | None = None,
        max_bytes: int = CEILING,
    ) -> str:
        return await amazon_s3.get_object(
            self.name,
            key,
            version_id=version_id,
            max_bytes=max_bytes,
            endpoint_url=ENDPOINT_URL,
        )


@pytest.fixture
async def bucket() -> AsyncIterator[Bucket]:
    access_key, secret_key = _minio_credentials()
    # Secrets live in a context variable, so set them in this async fixture,
    # which shares the test's context.
    token = secrets.set_context(
        {
            "AWS_ACCESS_KEY_ID": access_key,
            "AWS_SECRET_ACCESS_KEY": secret_key,
            "AWS_REGION": "us-east-1",
        }
    )
    session = aioboto3.Session(
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name="us-east-1",
    )
    name = f"test-s3-get-object-{uuid.uuid4().hex[:12]}"
    async with session.client("s3", endpoint_url=ENDPOINT_URL) as s3:  # type: ignore
        await s3.create_bucket(Bucket=name)
        await s3.put_bucket_versioning(
            Bucket=name, VersioningConfiguration={"Status": "Enabled"}
        )
    yield Bucket(name=name, session=session)
    async with session.client("s3", endpoint_url=ENDPOINT_URL) as s3:  # type: ignore
        listing = await s3.list_object_versions(Bucket=name)
        for version in listing.get("Versions", []):
            await s3.delete_object(
                Bucket=name,
                Key=version.get("Key", ""),
                VersionId=version.get("VersionId", ""),
            )
        await s3.delete_bucket(Bucket=name)
    secrets.reset_context(token)


def _gzip_of_zeros(size: int) -> bytes:
    compressor = zlib.compressobj(9, zlib.DEFLATED, 16 + zlib.MAX_WBITS)
    chunk = b"\0" * (1024 * 1024)
    parts = [compressor.compress(chunk) for _ in range(size // len(chunk))]
    return b"".join(parts) + compressor.flush()


@pytest.mark.anyio
async def test_small_plain_file_is_returned_whole(bucket: Bucket) -> None:
    await bucket.put("notes.txt", "hello, wörld\n".encode())

    assert await bucket.get("notes.txt") == "hello, wörld\n"


@pytest.mark.anyio
async def test_object_over_the_cap_is_cut_with_a_notice(bucket: Bucket) -> None:
    await bucket.put("big.log", b"a" * 5000)

    result = await bucket.get("big.log", max_bytes=1000)

    text, notice = result.split("\n\n", 1)
    assert text == "a" * 1000
    assert notice == (
        "[Truncated by Tracecat: returned at most the first 1000 bytes of the "
        f"5000-byte object. Raise max_bytes (at most {CEILING}) to read more.]"
    )


@pytest.mark.anyio
async def test_cut_never_splits_a_multibyte_character(bucket: Bucket) -> None:
    # "é" is two bytes, so a 5-byte cap lands inside the third character.
    await bucket.put("accents.txt", ("é" * 10).encode())

    result = await bucket.get("accents.txt", max_bytes=5)

    assert result.startswith("éé\n\n[Truncated by Tracecat:")


@pytest.mark.anyio
async def test_gzip_is_detected_by_magic_bytes(bucket: Bucket) -> None:
    payload = b'{"eventName": "GetObject"}\n' * 100
    # No .gz suffix and no Content-Encoding: detection must not depend on either.
    await bucket.put("events/no-suffix", gzip.compress(payload))

    assert await bucket.get("events/no-suffix") == payload.decode()


@pytest.mark.anyio
async def test_concatenated_gzip_members_are_all_read(bucket: Bucket) -> None:
    await bucket.put("multi.gz", gzip.compress(b"first\n") + gzip.compress(b"second\n"))

    assert await bucket.get("multi.gz") == "first\nsecond\n"


@pytest.mark.anyio
async def test_zero_padding_between_gzip_members_is_skipped(bucket: Bucket) -> None:
    # 1.5 MiB of padding spans a read-chunk boundary.
    padding = b"\0" * (3 * 1024 * 1024 // 2)
    body = gzip.compress(b"first\n") + padding + gzip.compress(b"second\n") + b"\0"
    await bucket.put("padded.gz", body)

    assert await bucket.get("padded.gz") == "first\nsecond\n"


@pytest.mark.anyio
async def test_gzip_cap_applies_to_decompressed_bytes(bucket: Bucket) -> None:
    payload = b"0123456789" * 1000
    await bucket.put("logs.json.gz", gzip.compress(payload))

    result = await bucket.get("logs.json.gz", max_bytes=25)

    assert result == (
        "0123456789012345678901234\n\n[Truncated by Tracecat: returned at most the "
        "first 25 bytes of the decompressed object. Raise max_bytes (at most "
        f"{CEILING}) to read more.]"
    )


@pytest.mark.anyio
async def test_gzip_that_expands_past_the_cap_stays_bounded(bucket: Bucket) -> None:
    # 256 MiB of zeros compresses to about 250 KB.
    bomb = _gzip_of_zeros(256 * 1024 * 1024)
    await bucket.put("bomb.gz", bomb)
    max_bytes = 1024 * 1024

    tracemalloc.start()
    try:
        result = await bucket.get("bomb.gz", max_bytes=max_bytes)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert result.startswith("\0" * max_bytes + "\n\n[Truncated by Tracecat:")
    assert peak < 16 * 1024 * 1024


@pytest.mark.anyio
async def test_gzip_that_inflates_to_little_stops_downloading(
    bucket: Bucket, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 8 MiB of padding after a 1-byte member: the output never reaches the cap, so
    # only the input bound stops the read.
    await bucket.put("padding.gz", gzip.compress(b"x") + b"\0" * (8 * 1024 * 1024))
    downloaded = 0
    read_chunk = amazon_s3._read_chunk  # pyright: ignore[reportPrivateUsage]

    async def counting_read_chunk(body: object) -> bytes:
        nonlocal downloaded
        chunk = await read_chunk(body)
        downloaded += len(chunk)
        return chunk

    monkeypatch.setattr(amazon_s3, "_read_chunk", counting_read_chunk)

    with pytest.raises(ValueError, match="decompressed to only 1 bytes"):
        await bucket.get("padding.gz", max_bytes=1024)
    # At most max_bytes plus two 1 MiB chunks, not the 8 MiB object.
    assert downloaded <= 1024 + 2 * 1024 * 1024


@pytest.mark.anyio
async def test_many_tiny_gzip_members_are_read(bucket: Bucket) -> None:
    # 200,000 empty members (about 4 MB) span many read chunks and inflater slices.
    body = gzip.compress(b"", mtime=0) * 200_000 + gzip.compress(b"done\n")
    await bucket.put("members.gz", body)

    assert await bucket.get("members.gz") == "done\n"


@pytest.mark.anyio
async def test_corrupt_gzip_raises(bucket: Bucket) -> None:
    await bucket.put("cut.gz", gzip.compress(b"x" * 10_000)[:-20])

    with pytest.raises(ValueError, match="Invalid gzip data"):
        await bucket.get("cut.gz")


@pytest.mark.anyio
async def test_version_id_pins_the_object_version(bucket: Bucket) -> None:
    first = await bucket.put("state.json", b'{"v": 1}')
    await bucket.put("state.json", b'{"v": 2}')

    assert await bucket.get("state.json", version_id=first) == '{"v": 1}'
    assert await bucket.get("state.json") == '{"v": 2}'


@pytest.mark.anyio
async def test_non_utf8_bytes_raise(bucket: Bucket) -> None:
    await bucket.put("image.bin", b"ok\xff\xfe")

    with pytest.raises(ValueError, match="not UTF-8 text .*offset 2"):
        await bucket.get("image.bin")


@pytest.mark.anyio
async def test_get_objects_does_not_retry_unreadable_objects(bucket: Bucket) -> None:
    # A retry would sleep 4 s or more and then raise tenacity.RetryError.
    await bucket.put("image.bin", b"ok\xff\xfe")

    with pytest.raises(ValueError, match="not UTF-8 text"):
        await amazon_s3.get_objects(
            bucket.name, ["image.bin"], endpoint_url=ENDPOINT_URL
        )


@pytest.mark.anyio
async def test_get_objects_passes_the_cap_through(bucket: Bucket) -> None:
    await bucket.put("a.txt", b"a" * 100)
    await bucket.put("b.txt", b"b")

    results = await amazon_s3.get_objects(
        bucket.name, ["a.txt", "b.txt"], max_bytes=10, endpoint_url=ENDPOINT_URL
    )

    assert results[0].startswith("a" * 10 + "\n\n[Truncated by Tracecat:")
    assert results[1] == "b"


@pytest.mark.anyio
@pytest.mark.parametrize("max_bytes", [0, CEILING + 1, 1.5, True])
async def test_max_bytes_outside_its_bounds_is_rejected(
    bucket: Bucket, max_bytes: int
) -> None:
    await bucket.put("notes.txt", b"hello")

    with pytest.raises(ValueError, match="max_bytes must be an integer between 1"):
        await bucket.get("notes.txt", max_bytes=max_bytes)
