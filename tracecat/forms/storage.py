"""Blob keys of form files, and their deletion.

Kept free of service imports so the forms and workflow services can both use
it without an import cycle.
"""

import asyncio
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import ColumnElement, delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat import config
from tracecat.db.models import Form, FormFile
from tracecat.forms.constants import (
    BLOB_DELETE_BATCH,
    EXPIRED_FILE_PURGE_BATCH,
    PENDING_FILE_TTL,
)
from tracecat.logger import logger
from tracecat.storage import blob


def file_blob_key(
    workspace_id: uuid.UUID, form_id: uuid.UUID, file_id: uuid.UUID
) -> str:
    """Storage key of a form file. Built from IDs only, never from a filename."""
    return f"forms/{workspace_id}/{form_id}/{file_id}"


async def file_blob_keys(
    session: AsyncSession, workspace_id: uuid.UUID, condition: ColumnElement[bool]
) -> list[str]:
    """Keys of the files matching ``condition``, which may name ``Form`` columns.

    Read them before deleting the rows' owner.
    """
    statement = (
        select(FormFile.form_id, FormFile.id)
        .join(Form, Form.id == FormFile.form_id)
        .where(FormFile.workspace_id == workspace_id, condition)
    )
    rows = (await session.execute(statement)).tuples().all()
    return [file_blob_key(workspace_id, form_id, file_id) for form_id, file_id in rows]


async def purge_expired_files(session: AsyncSession, workspace_id: uuid.UUID) -> None:
    """Delete a bounded batch of a workspace's files nobody claimed in time.

    Covers every form of the workspace, online or not. Commits the row
    deletion, then deletes the blobs.
    """
    # Lock the batch first, in a statement of its own. As a subquery of the
    # DELETE, Postgres may run the limited select once per file row, and each
    # run skips the rows already deleted, so the DELETE passes the limit.
    expired = (
        select(FormFile.id)
        .where(
            FormFile.workspace_id == workspace_id,
            FormFile.submission_id.is_(None),
            FormFile.created_at < datetime.now(UTC) - PENDING_FILE_TTL,
        )
        .limit(EXPIRED_FILE_PURGE_BATCH)
        .with_for_update(skip_locked=True)
    )
    if not (file_ids := (await session.execute(expired)).scalars().all()):
        return
    statement = (
        delete(FormFile)
        .where(FormFile.id.in_(file_ids))
        .returning(FormFile.form_id, FormFile.id)
    )
    rows = (await session.execute(statement)).tuples().all()
    await session.commit()
    await delete_file_blobs(
        [file_blob_key(workspace_id, form_id, file_id) for form_id, file_id in rows]
    )


async def delete_file_blobs(keys: Sequence[str]) -> None:
    """Delete blobs whose rows are already gone. Best effort.

    Deletes every key, ``BLOB_DELETE_BATCH`` at a time. Nothing retries a
    delete that fails: the blob stays in storage with no row, and the warning
    names its key.
    """
    for start in range(0, len(keys), BLOB_DELETE_BATCH):
        batch = keys[start : start + BLOB_DELETE_BATCH]
        await asyncio.gather(*(_delete_file_blob(key) for key in batch))


async def _delete_file_blob(key: str) -> None:
    try:
        await blob.delete_file(
            key=key,
            bucket=config.TRACECAT__BLOB_STORAGE_BUCKET_ATTACHMENTS,
            redact_log_identifiers=True,
        )
    except Exception as e:
        # The key holds IDs only, so it is safe to log.
        logger.warning(
            "Failed to delete a form file from storage",
            key=key,
            error_type=type(e).__name__,
        )
