"""Blob keys of form files, and their deletion.

Kept free of service imports so the forms and workflow services can both use
it without an import cycle.
"""

import os
import uuid
from collections.abc import Sequence

from sqlalchemy import ColumnElement, select
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat import config
from tracecat.db.models import Form, FormFile
from tracecat.logger import logger
from tracecat.storage import blob

MAX_BLOB_DELETES_PER_REQUEST = 500
"""Blobs one request deletes. The rest stay in storage and are logged."""


def file_blob_key(
    workspace_id: uuid.UUID, form_id: uuid.UUID, file_id: uuid.UUID
) -> str:
    """Storage key of a form file. Built from IDs only, never from a filename."""
    return f"forms/{workspace_id}/{form_id}/{file_id}"


async def _file_blob_keys(
    session: AsyncSession, workspace_id: uuid.UUID, condition: ColumnElement[bool]
) -> list[str]:
    statement = select(FormFile.form_id, FormFile.id).where(
        FormFile.workspace_id == workspace_id, condition
    )
    rows = (await session.execute(statement)).tuples().all()
    return [file_blob_key(workspace_id, form_id, file_id) for form_id, file_id in rows]


async def submission_file_blob_keys(
    session: AsyncSession, workspace_id: uuid.UUID, submission_id: uuid.UUID
) -> list[str]:
    """Keys of the files a submission claimed. Read them before deleting it."""
    return await _file_blob_keys(
        session, workspace_id, FormFile.submission_id == submission_id
    )


async def form_file_blob_keys(
    session: AsyncSession, workspace_id: uuid.UUID, form_id: uuid.UUID
) -> list[str]:
    """Keys of every file of a form, claimed or not. Read them before deleting it."""
    return await _file_blob_keys(session, workspace_id, FormFile.form_id == form_id)


async def workflow_file_blob_keys(
    session: AsyncSession, workspace_id: uuid.UUID, workflow_id: uuid.UUID
) -> list[str]:
    """Keys of every file of a workflow's form. Read them before deleting it."""
    form_ids = select(Form.id).where(
        Form.workspace_id == workspace_id, Form.workflow_id == workflow_id
    )
    return await _file_blob_keys(session, workspace_id, FormFile.form_id.in_(form_ids))


async def delete_file_blobs(keys: Sequence[str]) -> None:
    """Delete blobs whose rows are already gone. Best effort.

    Deletes one blob at a time, at most ``MAX_BLOB_DELETES_PER_REQUEST`` per
    call. Nothing retries a blob that is skipped or fails: it stays in storage
    with no row, and the warning names the key prefix to clean up.
    """
    for key in keys[:MAX_BLOB_DELETES_PER_REQUEST]:
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
    if leftover := keys[MAX_BLOB_DELETES_PER_REQUEST:]:
        logger.warning(
            "Left form files in storage after deleting their rows",
            count=len(leftover),
            key_prefix=os.path.commonprefix(list(leftover)),
        )
