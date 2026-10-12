"""Files uploaded to a form's ``file`` fields.

A file is uploaded first and is pending until a submission claims it. Its
blob lives under a key built from IDs only; the filename is data.
"""

from __future__ import annotations

import base64
import hashlib
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import ColumnElement, delete, false, func, or_, select

from tracecat import config
from tracecat.authz.controls import has_scope
from tracecat.db.models import Form, FormFile, Workspace
from tracecat.exceptions import TracecatAuthorizationError, TracecatException
from tracecat.forms.constants import (
    EXPIRED_FILE_PURGE_BATCH,
    MAX_FILES_PER_FIELD,
    MAX_PENDING_FILES_PER_UPLOADER,
    PENDING_FILE_TTL,
)
from tracecat.forms.enums import FormErrorCode, FormFieldErrorCode, FormWidget
from tracecat.forms.exceptions import FormError
from tracecat.forms.schemas import (
    FormFieldBlock,
    FormFieldError,
    FormFileContent,
    FormFileDownload,
    FormFileRead,
)
from tracecat.forms.service import FormsService
from tracecat.forms.storage import delete_file_blobs, file_blob_key
from tracecat.service import BaseWorkspaceService
from tracecat.storage import blob
from tracecat.storage.exceptions import (
    FileExtensionError,
    FileMimeTypeError,
    FileSizeError,
    FileValidationError,
)
from tracecat.storage.validation import (
    FileSecurityValidator,
    FileValidationResult,
    mime_equivalence_key,
)


def matches_accept(
    accept: Sequence[str] | None, *, extension: str, content_type: str
) -> bool:
    """Whether a validated file is of a type a field accepts.

    Entries read like the HTML ``accept`` attribute: an extension (``.pdf``),
    a MIME type, or a ``type/*`` wildcard. A field with no entries takes
    whatever the workspace allows. An entry that is none of these matches
    nothing.
    """
    if not accept:
        return True
    for entry in accept:
        pattern = entry.strip().lower()
        if pattern.startswith("."):
            if extension == pattern:
                return True
        elif pattern.endswith("/*"):
            if content_type.startswith(pattern[:-1]):
                return True
        elif mime_equivalence_key(content_type) == mime_equivalence_key(pattern):
            return True
    return False


def _file_read(file: FormFile) -> FormFileRead:
    return FormFileRead(
        id=file.id,
        name=file.name,
        content_type=file.content_type,
        size=file.size,
        sha256=file.sha256,
    )


def _invalid_file(field: str) -> FormFieldError:
    # One message for every reason, so a caller cannot probe for file IDs.
    return FormFieldError(
        field=field,
        code=FormFieldErrorCode.INVALID_FILE,
        message="Upload the file again",
    )


class FormFilesService(BaseWorkspaceService):
    """Upload, claim, download and delete the files of forms."""

    service_name = "form_files"

    @property
    def _user_id(self) -> uuid.UUID | None:
        """The calling user. None for a service account."""
        return self.role.user_id if self.role.type == "user" else None

    @property
    def _service_account_id(self) -> uuid.UUID | None:
        return self.role.service_account_id if self._user_id is None else None

    def _uploaded_by_caller(self) -> ColumnElement[bool]:
        """Match the files the caller uploaded. No identity matches none."""
        if (user_id := self._user_id) is not None:
            return FormFile.uploaded_by_user_id == user_id
        if (service_account_id := self._service_account_id) is not None:
            return FormFile.uploaded_by_service_account_id == service_account_id
        return false()

    def _pending(self, form_id: uuid.UUID) -> tuple[ColumnElement[bool], ...]:
        return (
            FormFile.workspace_id == self.workspace_id,
            FormFile.form_id == form_id,
            FormFile.submission_id.is_(None),
        )

    # ----- Upload -----

    async def upload(
        self,
        form: Form,
        field_name: str,
        *,
        filename: str,
        content_type: str,
        content: bytes,
    ) -> FormFileRead:
        """Store a file for one of an online form's ``file`` fields.

        The file stays pending until the caller submits its ID.

        Args:
            form: The form.
            field_name: Name of a ``file`` field of the resolved form.
            filename: Name the client sent. Validated, stored as data.
            content_type: Type the client declared. Checked against the bytes.
            content: The file's bytes, already bounded by the size limit.

        Raises:
            FormError: ``form_offline``, ``form_unavailable``,
                ``file_field_invalid``, ``file_invalid``,
                ``file_type_not_allowed``, ``file_too_large``,
                ``file_limit_reached`` or ``file_upload_failed``.
            TracecatAuthorizationError: If the caller is neither a user nor a
                service account.
        """
        form_id = form.id
        if self._user_id is None and self._service_account_id is None:
            raise TracecatAuthorizationError(
                "Uploading a file needs a user or a service account"
            )
        _, _, resolved = await FormsService(self.session, self.role).resolve_published(
            form
        )
        field = next(
            (
                block
                for block in resolved.blocks
                if isinstance(block, FormFieldBlock)
                and block.widget is FormWidget.FILE
                and block.name == field_name
            ),
            None,
        )
        if field is None:
            raise FormError(
                FormErrorCode.FILE_FIELD_INVALID, "This form has no such file field"
            )

        await self.purge_expired(form_id)
        validated = await self._validate(
            field, filename=filename, content_type=content_type, content=content
        )

        # Serialize this form's uploads so two cannot both pass the count.
        # NO KEY UPDATE leaves inserts that reference the form unblocked.
        await self.session.execute(
            select(Form.id).where(Form.id == form_id).with_for_update(key_share=True)
        )
        pending_count = await self.session.scalar(
            select(func.count())
            .select_from(FormFile)
            .where(*self._pending(form_id), self._uploaded_by_caller())
        )
        if (pending_count or 0) >= MAX_PENDING_FILES_PER_UPLOADER:
            raise FormError(
                FormErrorCode.FILE_LIMIT_REACHED,
                "Too many files are waiting to be submitted. Submit the form or "
                "try again later.",
            )

        # The row goes first: a failed upload then leaves no blob behind.
        file = FormFile(
            id=uuid.uuid4(),
            workspace_id=self.workspace_id,
            form_id=form_id,
            field=field.name,
            uploaded_by_user_id=self._user_id,
            uploaded_by_service_account_id=self._service_account_id,
            name=validated.filename,
            content_type=validated.content_type,
            size=len(content),
            sha256=hashlib.sha256(content).hexdigest(),
        )
        file_read = _file_read(file)
        self.session.add(file)
        await self.session.commit()
        try:
            await blob.upload_file(
                content=content,
                key=file_blob_key(self.workspace_id, form_id, file_read.id),
                bucket=config.TRACECAT__BLOB_STORAGE_BUCKET_ATTACHMENTS,
                content_type=validated.content_type,
                redact_log_identifiers=True,
            )
        except Exception as e:
            await self.session.execute(
                delete(FormFile).where(FormFile.id == file_read.id)
            )
            await self.session.commit()
            # Never log the filename.
            self.logger.error(
                "Failed to store a form file",
                form_id=form_id,
                file_id=file_read.id,
                error_type=type(e).__name__,
            )
            raise FormError(
                FormErrorCode.FILE_UPLOAD_FAILED,
                "The file could not be stored. Try again.",
            ) from e
        return file_read

    async def _validate(
        self, field: FormFieldBlock, *, filename: str, content_type: str, content: bytes
    ) -> FileValidationResult:
        """Check a file against the workspace's attachment settings and the
        field's ``accept``. A file must pass both.

        The bytes are always checked against the declared type, whatever the
        workspace's magic-number setting for case attachments says.
        """
        if not content:
            raise FormError(FormErrorCode.FILE_INVALID, "The file is empty")
        settings = await self.session.scalar(
            select(Workspace.settings).where(Workspace.id == self.workspace_id)
        )
        settings = settings or {}
        # An empty allowlist falls back to the deployment default, as it does
        # for case attachments.
        validator = FileSecurityValidator(
            allowed_extensions=settings.get("allowed_attachment_extensions") or None,
            allowed_mime_types=settings.get("allowed_attachment_mime_types") or None,
            validate_magic_number=True,
        )
        try:
            validated = validator.validate_file(
                content=content, filename=filename, declared_mime_type=content_type
            )
        except FileSizeError as e:
            raise FormError(FormErrorCode.FILE_TOO_LARGE, str(e)) from e
        except (FileExtensionError, FileMimeTypeError) as e:
            raise FormError(FormErrorCode.FILE_TYPE_NOT_ALLOWED, str(e)) from e
        except FileValidationError as e:
            raise FormError(FormErrorCode.FILE_INVALID, str(e)) from e

        accept = field.config.accept if field.config is not None else None
        if not matches_accept(
            accept, extension=validated.extension, content_type=validated.content_type
        ):
            raise FormError(
                FormErrorCode.FILE_TYPE_NOT_ALLOWED,
                "This field does not take this type of file",
            )
        return validated

    async def purge_expired(self, form_id: uuid.UUID) -> None:
        """Delete a bounded batch of a form's files nobody claimed in time.

        Commits the row deletion, then deletes the blobs.
        """
        expired = (
            select(FormFile.id)
            .where(
                *self._pending(form_id),
                FormFile.created_at < datetime.now(UTC) - PENDING_FILE_TTL,
            )
            .limit(EXPIRED_FILE_PURGE_BATCH)
            .with_for_update(skip_locked=True)
        )
        statement = (
            delete(FormFile).where(FormFile.id.in_(expired)).returning(FormFile.id)
        )
        file_ids = (await self.session.execute(statement)).scalars().all()
        if not file_ids:
            return
        await self.session.commit()
        await delete_file_blobs(
            [file_blob_key(self.workspace_id, form_id, file_id) for file_id in file_ids]
        )

    # ----- Claim -----

    async def claim(
        self,
        form_id: uuid.UUID,
        fields: Sequence[FormFieldBlock],
        answers: dict[str, Any],
    ) -> tuple[list[FormFieldError], list[FormFile]]:
        """Replace the file IDs of ``file`` fields with file objects, in place.

        A file ID is accepted only if the file belongs to this form, was
        uploaded by this caller to the field it is submitted under, has not
        expired and is not claimed. The rows are locked, so a file is claimed
        once.

        Returns:
            The field errors, and the files to attach to the submission once
            its row exists. The caller sets their ``submission_id``.
        """
        file_ids_by_field: dict[str, list[uuid.UUID]] = {}
        errors: list[FormFieldError] = []
        for field in fields:
            if field.widget is not FormWidget.FILE or field.name not in answers:
                continue
            answer = answers[field.name]
            items = answer if field.multiple else [answer]
            max_files = MAX_FILES_PER_FIELD
            if field.config is not None and field.config.max_files is not None:
                max_files = field.config.max_files
            if len(items) > max_files:
                errors.append(
                    FormFieldError(
                        field=field.name,
                        code=FormFieldErrorCode.TOO_MANY_FILES,
                        message=f"Attach at most {max_files} files",
                    )
                )
                continue
            try:
                file_ids = [uuid.UUID(item) for item in items]
            except (AttributeError, TypeError, ValueError):
                errors.append(_invalid_file(field.name))
                continue
            if len(set(file_ids)) != len(file_ids):
                errors.append(_invalid_file(field.name))
                continue
            file_ids_by_field[field.name] = file_ids
        if not file_ids_by_field:
            return errors, []

        wanted = {
            file_id for file_ids in file_ids_by_field.values() for file_id in file_ids
        }
        statement = (
            select(FormFile)
            .where(
                *self._pending(form_id),
                FormFile.id.in_(wanted),
                FormFile.created_at >= datetime.now(UTC) - PENDING_FILE_TTL,
                self._uploaded_by_caller(),
            )
            .with_for_update()
        )
        files = {
            file.id: file for file in (await self.session.execute(statement)).scalars()
        }
        multiple = {field.name: field.multiple for field in fields}
        claimed: list[FormFile] = []
        for name, file_ids in file_ids_by_field.items():
            found = [
                files[file_id]
                for file_id in file_ids
                if file_id in files and files[file_id].field == name
            ]
            if len(found) != len(file_ids):
                errors.append(_invalid_file(name))
                continue
            claimed.extend(found)
            expanded = [_file_read(file).model_dump(mode="json") for file in found]
            answers[name] = expanded if multiple[name] else expanded[0]
        return errors, claimed

    # ----- Download -----

    async def _get_readable_file(
        self, file_id: uuid.UUID, *, form_id: uuid.UUID | None = None
    ) -> FormFile | None:
        """Get a file the caller may download.

        A caller downloads the files they uploaded, pending or claimed. With
        ``form:submission:read`` a caller also downloads any file a submission
        has claimed. Nobody else reads a pending file.
        """
        statement = select(FormFile).where(
            FormFile.workspace_id == self.workspace_id, FormFile.id == file_id
        )
        if form_id is not None:
            statement = statement.where(FormFile.form_id == form_id)
        readable = self._uploaded_by_caller()
        if has_scope(self.role.scopes or frozenset(), "form:submission:read"):
            readable = or_(readable, FormFile.submission_id.is_not(None))
        statement = statement.where(readable)
        return (await self.session.execute(statement)).scalar_one_or_none()

    async def get_download(
        self, form_id: uuid.UUID, file_id: uuid.UUID
    ) -> FormFileDownload | None:
        """Make a short-lived download URL for a file.

        The URL forces a download with a neutral content type, so a browser
        never renders an uploaded file.

        Returns:
            The download, or None if the file does not exist or is not the
            caller's to read. Callers must not tell these cases apart.
        """
        file = await self._get_readable_file(file_id, form_id=form_id)
        if file is None:
            return None
        download_url = await blob.generate_presigned_download_url(
            key=file_blob_key(self.workspace_id, file.form_id, file.id),
            bucket=config.TRACECAT__BLOB_STORAGE_BUCKET_ATTACHMENTS,
            force_download=True,
            override_content_type="application/octet-stream",
            redact_log_identifiers=True,
            # Header only. The key above holds IDs and nothing else.
            download_filename=file.name,
        )
        return FormFileDownload(
            download_url=download_url,
            file_name=file.name,
            content_type=file.content_type,
        )

    async def read_content(self, file_id: uuid.UUID) -> FormFileContent | None:
        """Read a file's bytes, for a workflow run.

        Returns:
            The file with its content, or None if the file does not exist, is
            not the caller's to read, or has no blob.

        Raises:
            TracecatException: If the stored bytes do not match the file's
                hash.
        """
        file = await self._get_readable_file(file_id)
        if file is None:
            return None
        try:
            content = await blob.download_file(
                key=file_blob_key(self.workspace_id, file.form_id, file.id),
                bucket=config.TRACECAT__BLOB_STORAGE_BUCKET_ATTACHMENTS,
                redact_log_identifiers=True,
            )
        except FileNotFoundError:
            return None
        if hashlib.sha256(content).hexdigest() != file.sha256:
            raise TracecatException("File integrity check failed")
        return FormFileContent(
            id=file.id,
            name=file.name,
            content_type=file.content_type,
            size=file.size,
            sha256=file.sha256,
            content_base64=base64.b64encode(content).decode("ascii"),
        )
