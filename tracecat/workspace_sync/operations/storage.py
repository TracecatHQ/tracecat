"""Private object storage for immutable sync artifacts and individual diffs."""

import asyncio
import base64
import hashlib
import uuid
from dataclasses import replace

from pydantic import TypeAdapter

from tracecat import config
from tracecat.storage.blob import download_file, upload_file
from tracecat.sync import PullResourceDiff, PullResult
from tracecat.workspace_sync.operations.schemas import (
    PreparedSync,
    StoredSyncArtifact,
    SyncDiffPage,
)

_DIFF = TypeAdapter(PullResourceDiff)
_PAGE_SIZE = 50


def artifact_prefix(workspace_id: uuid.UUID, operation_id: uuid.UUID) -> str:
    """Derive tenant-isolated keys; callers never accept storage keys from users."""
    return f"workspace-sync/{workspace_id}/{operation_id}"


async def _upload(content: bytes, key: str) -> None:
    await upload_file(
        content,
        key,
        config.TRACECAT__BLOB_STORAGE_BUCKET_WORKFLOW,
        "application/json",
        redact_log_identifiers=True,
    )


async def _download(key: str) -> bytes:
    return await download_file(
        key, config.TRACECAT__BLOB_STORAGE_BUCKET_WORKFLOW, redact_log_identifiers=True
    )


async def store_prepared(prefix: str, prepared: PreparedSync) -> str:
    """Store a content-addressed snapshot and bounded pages of diff metadata."""
    diffs = prepared.preview.resource_diffs or []
    metadata = [replace(diff, diff="") for diff in diffs]
    preview = (
        replace(prepared.preview, resource_diffs=metadata)
        if isinstance(prepared.preview, PullResult)
        else prepared.preview.model_copy(update={"resource_diffs": metadata})
    )
    artifact = StoredSyncArtifact(
        prepared=prepared.model_copy(update={"preview": preview}),
        snapshot_skill_contents={
            key: skill.file_contents
            for key, skill in prepared.snapshot.spec.skills.items()
        }
        if prepared.snapshot
        else {},
        projection_skill_contents={
            key: skill.file_contents
            for key, skill in prepared.projection.spec.skills.items()
        }
        if prepared.projection
        else {},
    )
    content = artifact.model_dump_json().encode()
    key = f"{prefix}/{hashlib.sha256(content).hexdigest()}.json"
    semaphore = asyncio.Semaphore(8)

    async def store_diff(index: int, diff: PullResourceDiff) -> None:
        async with semaphore:
            await _upload(_DIFF.dump_json(diff), f"{key}.diff-{index}")

    # Bound both network concurrency and task allocation for very large previews.
    for offset in range(0, max(len(diffs), 1), _PAGE_SIZE):
        chunk = diffs[offset : offset + _PAGE_SIZE]
        await asyncio.gather(
            *(store_diff(offset + i, diff) for i, diff in enumerate(chunk))
        )
        next_offset = offset + _PAGE_SIZE
        cursor = (
            base64.urlsafe_b64encode(str(next_offset).encode()).decode()
            if next_offset < len(diffs)
            else None
        )
        page = SyncDiffPage(
            items=[replace(diff, diff="") for diff in chunk], next_cursor=cursor
        )
        await _upload(page.model_dump_json().encode(), f"{key}.page-{offset}")
    await _upload(content, key)
    return key


async def load_prepared(key: str) -> PreparedSync:
    """Load the immutable prepared artifact referenced by an authorized DB record."""
    artifact = StoredSyncArtifact.model_validate_json(await _download(key))
    prepared = artifact.prepared
    for source, contents in (
        (prepared.snapshot, artifact.snapshot_skill_contents),
        (prepared.projection, artifact.projection_skill_contents),
    ):
        if source:
            for source_id, skill in source.spec.skills.items():
                skill.file_contents = contents[source_id]
    return prepared


async def read_diff_page(key: str, cursor: str | None) -> SyncDiffPage:
    """Load one immutable page without reading the large prepared snapshot."""
    offset = 0
    if cursor:
        if len(cursor) > 32:
            raise ValueError("Invalid cursor")
        offset = int(base64.b64decode(cursor, altchars=b"-_", validate=True))
        if offset < 0 or offset % _PAGE_SIZE:
            raise ValueError("Invalid cursor")
    return SyncDiffPage.model_validate_json(await _download(f"{key}.page-{offset}"))


async def read_diff(key: str, index: int) -> PullResourceDiff:
    """Load the diff for exactly one selected resource."""
    return _DIFF.validate_json(await _download(f"{key}.diff-{index}"))
