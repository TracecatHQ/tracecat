"""Tests for SkillService."""

import asyncio
import base64
import hashlib
import os
import uuid
from collections.abc import Sequence
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock
from urllib.parse import urlparse

import pytest
from asyncpg import UniqueViolationError
from botocore.exceptions import ClientError
from dotenv import dotenv_values
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests.database import TEST_DB_CONFIG
from tracecat import config
from tracecat.agent.dependencies.service import AgentDependencyService
from tracecat.agent.preset.schemas import (
    AgentPresetCreate,
    AgentPresetSkillBindingBase,
    AgentPresetUpdate,
)
from tracecat.agent.preset.service import AgentPresetService
from tracecat.agent.skill import service as skill_service_module
from tracecat.agent.skill.schemas import (
    SkillCreate,
    SkillDraftAttachUploadedBlobOp,
    SkillDraftDeleteFileOp,
    SkillDraftMoveFileOp,
    SkillDraftPatch,
    SkillDraftRead,
    SkillDraftUpsertTextFileOp,
    SkillReadMinimal,
    SkillUpload,
    SkillUploadFile,
    SkillUploadSessionCreate,
    SkillVersionPublish,
    SkillVersionReadMinimal,
)
from tracecat.agent.skill.service import (
    SKILL_SLUG_UNIQUE_CONSTRAINT,
    PreparedDraftAttachUploadedBlobOp,
    PublishedBlobObject,
    SkillBlobPublicationClaim,
    SkillService,
)
from tracecat.auth.types import Role
from tracecat.db.models import (
    MCPIntegration,
    RegistryIndex,
    RegistryRepository,
    RegistryVersion,
    Skill,
    SkillBlob,
    SkillVersion,
    SkillVersionMcpTool,
    SkillVersionTool,
    Workspace,
)
from tracecat.db.models import (
    SkillUpload as SkillUploadModel,
)
from tracecat.exceptions import TracecatNotFoundError, TracecatValidationError
from tracecat.integrations.enums import MCPAuthType
from tracecat.integrations.service import IntegrationService
from tracecat.pagination import CursorPaginationParams
from tracecat.registry.actions.schemas import RegistryActionType
from tracecat.registry.versions.schemas import (
    RegistryVersionManifest,
    RegistryVersionManifestAction,
)
from tracecat.storage.blob import ensure_bucket_exists, file_exists, upload_file

pytestmark = pytest.mark.usefixtures("db")


def _skill_slug_unique_violation() -> IntegrityError:
    # The service helper duck-types constraint_name off the exception chain,
    # so an Any-typed fake keeps pyright happy without touching psycopg types.
    unique_violation: Any = UniqueViolationError("duplicate skill slug")
    unique_violation.constraint_name = SKILL_SLUG_UNIQUE_CONSTRAINT
    integrity_error = IntegrityError("INSERT INTO skill", {}, unique_violation)
    integrity_error.__cause__ = unique_violation
    return integrity_error


@pytest.fixture(scope="session", autouse=True)
def sync_minio_credentials(monkeysession: pytest.MonkeyPatch) -> None:
    """Ensure MinIO-backed skill tests use the active local credentials."""

    try:
        env = dotenv_values()
    except Exception:
        env = {}

    access_key = (
        env.get("AWS_ACCESS_KEY_ID")
        or env.get("MINIO_ROOT_USER")
        or os.environ.get("AWS_ACCESS_KEY_ID")
        or os.environ.get("MINIO_ROOT_USER")
        or "minio"
    )
    secret_key = (
        env.get("AWS_SECRET_ACCESS_KEY")
        or env.get("MINIO_ROOT_PASSWORD")
        or os.environ.get("AWS_SECRET_ACCESS_KEY")
        or os.environ.get("MINIO_ROOT_PASSWORD")
        or "password"
    )

    monkeysession.setenv("AWS_ACCESS_KEY_ID", access_key)
    monkeysession.setenv("AWS_SECRET_ACCESS_KEY", secret_key)


@pytest.fixture
async def skill_service(session: AsyncSession, svc_role: Role) -> SkillService:
    """Create a skill service bound to the test workspace."""

    return SkillService(session=session, role=svc_role)


@pytest.fixture(autouse=True)
async def configure_minio_for_skills(
    minio_bucket: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Point skill storage at the test MinIO bucket."""

    monkeypatch.setattr(
        config,
        "TRACECAT__BLOB_STORAGE_ENDPOINT",
        f"http://localhost:{os.environ.get('MINIO_PORT', '9000')}",
        raising=False,
    )
    monkeypatch.setattr(
        config,
        "TRACECAT__BLOB_STORAGE_BUCKET_SKILLS",
        minio_bucket,
        raising=False,
    )
    monkeypatch.setenv(
        "TRACECAT__BLOB_STORAGE_BUCKET_SKILLS",
        minio_bucket,
    )
    monkeypatch.setenv(
        "AWS_ACCESS_KEY_ID",
        os.environ.get("AWS_ACCESS_KEY_ID", "minio"),
    )
    monkeypatch.setenv(
        "AWS_SECRET_ACCESS_KEY",
        os.environ.get("AWS_SECRET_ACCESS_KEY", "password"),
    )

    await ensure_bucket_exists(minio_bucket)


async def _legacy_archive_skill(session: AsyncSession, skill_id: uuid.UUID) -> datetime:
    """Simulate an old app instance that writes only Skill.archived_at."""

    skill = (
        await session.execute(select(Skill).where(Skill.id == skill_id))
    ).scalar_one()
    archived_at = datetime.now(UTC)
    skill.archived_at = archived_at
    skill.deleted_at = None
    await session.commit()
    return archived_at


@pytest.mark.anyio
class TestSkillService:
    async def test_claim_blob_publication_reuses_existing_identity(
        self,
        skill_service: SkillService,
    ) -> None:
        """Only the first claim should own publication for a blob identity."""

        content = b"shared blob content"
        sha256 = hashlib.sha256(content).hexdigest()
        storage_key = skill_service._storage_key_for(sha256)

        original = await skill_service._claim_blob_publication(
            sha256=sha256,
            bucket=config.TRACECAT__BLOB_STORAGE_BUCKET_SKILLS,
            key=storage_key,
            size_bytes=len(content),
        )
        reused = await skill_service._claim_blob_publication(
            sha256=sha256,
            bucket=config.TRACECAT__BLOB_STORAGE_BUCKET_SKILLS,
            key=storage_key,
            size_bytes=len(content),
        )
        blob_rows = (
            (
                await skill_service.session.execute(
                    select(SkillBlob).where(
                        SkillBlob.workspace_id == skill_service.workspace_id,
                        SkillBlob.sha256 == sha256,
                    )
                )
            )
            .scalars()
            .all()
        )

        assert original.is_owner is True
        assert reused.is_owner is False
        assert reused.blob.id == original.blob.id
        assert len(blob_rows) == 1

    async def test_create_skill_seeds_default_draft(
        self,
        skill_service: SkillService,
    ) -> None:
        """Creating a skill seeds a publishable draft with root SKILL.md."""

        created = await skill_service.create_skill(
            SkillCreate(
                name="triage-skill",
                description="Handle security triage",
            )
        )

        assert created.name == "triage-skill"
        assert created.draft_revision == 1
        assert created.is_draft_publishable is True
        assert created.draft_file_count == 1

        draft = await skill_service.get_draft(created.id)
        assert draft is not None
        assert draft.name == "triage-skill"
        assert draft.description == "Handle security triage"
        assert [file.path for file in draft.files] == ["SKILL.md"]

    async def test_prepare_draft_download_returns_presigned_plan_for_all_files(
        self,
        skill_service: SkillService,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Every draft file should receive a rewritten presigned download URL."""

        contents = {
            "SKILL.md": b"---\nname: download-skill\n---\n\n# Download\n",
            "scripts/helper.py": b"def main():\n    return 'ok'\n",
        }
        created = await skill_service.upload_skill(
            SkillUpload(
                name="download-skill",
                files=[
                    SkillUploadFile(
                        path="SKILL.md",
                        content_base64=base64.b64encode(contents["SKILL.md"]).decode(),
                        content_type="text/markdown; charset=utf-8",
                    ),
                    SkillUploadFile(
                        path="scripts/helper.py",
                        content_base64=base64.b64encode(
                            contents["scripts/helper.py"]
                        ).decode(),
                        content_type="text/x-python; charset=utf-8",
                    ),
                ],
            )
        )
        monkeypatch.setattr(
            config,
            "TRACECAT__BLOB_STORAGE_PRESIGNED_URL_ENDPOINT",
            "http://downloads.example",
        )

        prepared = await skill_service.prepare_draft_download(skill_id=created.id)

        assert prepared is not None
        assert prepared.workspace_id == skill_service.workspace_id
        assert prepared.skill_id == created.id
        assert prepared.skill_name == "download-skill"
        assert prepared.draft_revision == created.draft_revision
        files_by_path = {file.path: file for file in prepared.files}
        assert set(files_by_path) == {"SKILL.md", "scripts/helper.py"}
        assert {path: file.sha256 for path, file in files_by_path.items()} == {
            path: hashlib.sha256(content).hexdigest()
            for path, content in contents.items()
        }
        assert files_by_path["SKILL.md"].size_bytes == len(contents["SKILL.md"])
        assert (
            files_by_path["scripts/helper.py"].content_type
            == "text/x-python; charset=utf-8"
        )
        assert all(
            urlparse(file.download_url).hostname == "downloads.example"
            for file in prepared.files
        )

    async def test_prepare_draft_download_returns_none_for_missing_skill(
        self,
        skill_service: SkillService,
    ) -> None:
        """Missing skills should not produce a draft download plan."""

        assert await skill_service.prepare_draft_download(skill_id=uuid.uuid4()) is None

    async def test_oversized_existing_manifest_is_visible_but_not_downloadable(
        self,
        skill_service: SkillService,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        created = await skill_service.create_skill(SkillCreate(name="bounded-read"))
        monkeypatch.setattr(config, "TRACECAT__MAX_SKILL_MANIFEST_SIZE_BYTES", 1)

        draft = await skill_service.get_draft(created.id)

        assert draft is not None
        assert draft.is_publishable is False
        assert [error.code for error in draft.validation_errors] == [
            "skill_manifest_size_limit_exceeded"
        ]
        with pytest.raises(TracecatValidationError) as exc_info:
            await skill_service.prepare_draft_download(skill_id=created.id)
        assert exc_info.value.detail is not None
        assert exc_info.value.detail["code"] == "skill_manifest_size_limit_exceeded"

    async def test_create_skill_suffixes_live_duplicate_slug(
        self,
        skill_service: SkillService,
        session: AsyncSession,
        svc_role: Role,
    ) -> None:
        """Duplicate names stay allowed; the slug gets a deterministic suffix."""

        created = await skill_service.create_skill(SkillCreate(name="unique-skill"))

        second = await skill_service.create_skill(SkillCreate(name="unique-skill"))
        third = await skill_service.create_skill(SkillCreate(name="unique-skill"))

        assert second.name == "unique-skill"
        assert second.slug == "unique-skill-2"
        assert third.slug == "unique-skill-3"

        other_workspace_id = uuid.uuid4()
        session.add(
            Workspace(
                id=other_workspace_id,
                name="other-skill-workspace",
                organization_id=svc_role.organization_id,
            )
        )
        await session.commit()
        other_service = SkillService(
            session=session,
            role=svc_role.model_copy(
                update={"workspace_id": other_workspace_id},
                deep=True,
            ),
        )

        other_created = await other_service.create_skill(
            SkillCreate(name="unique-skill")
        )

        assert created.slug == "unique-skill"
        assert other_created.slug == "unique-skill"
        assert other_created.workspace_id == other_workspace_id

    async def test_create_skill_suffixes_max_length_duplicate_slug(
        self,
        skill_service: SkillService,
    ) -> None:
        """Max-length names use truncated suffix candidates for each probe."""

        max_length_name = "a" * 64

        created = await skill_service.create_skill(SkillCreate(name=max_length_name))
        second = await skill_service.create_skill(SkillCreate(name=max_length_name))
        third = await skill_service.create_skill(SkillCreate(name=max_length_name))

        assert created.slug == max_length_name
        assert second.slug == f"{max_length_name[:62]}-2"
        assert third.slug == f"{max_length_name[:62]}-3"

    async def test_create_skill_retries_slug_unique_violation(
        self,
        skill_service: SkillService,
        svc_workspace: Workspace,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Slug unique races roll back, reallocate, and retry the insert."""

        allocated_slugs = ["race-skill", "race-skill-2"]
        original_flush = skill_service.session.flush
        original_rollback = skill_service.session.rollback
        flush_calls = 0
        rollback_calls = 0

        async def allocate_slug(_desired: str) -> str:
            return allocated_slugs.pop(0)

        async def flush_once_then_succeed(*args: Any, **kwargs: Any) -> None:
            nonlocal flush_calls
            flush_calls += 1
            if flush_calls == 1:
                raise _skill_slug_unique_violation()
            await original_flush(*args, **kwargs)

        async def rollback_and_count() -> None:
            nonlocal rollback_calls
            rollback_calls += 1
            await original_rollback()

        monkeypatch.setattr(skill_service, "_allocate_skill_slug", allocate_slug)
        monkeypatch.setattr(skill_service.session, "flush", flush_once_then_succeed)
        monkeypatch.setattr(skill_service.session, "rollback", rollback_and_count)

        created = await skill_service.create_skill(SkillCreate(name="race-skill"))

        assert created.slug == "race-skill-2"
        assert flush_calls >= 3
        assert rollback_calls == 1
        assert allocated_slugs == []
        await skill_service.session.refresh(svc_workspace)

    async def test_create_skill_reuses_slug_after_soft_delete(
        self,
        skill_service: SkillService,
    ) -> None:
        """Soft-deleted skill slugs can be reused by new live skills."""

        deleted = await skill_service.create_skill(SkillCreate(name="reused-skill"))
        await skill_service.archive_skill(deleted.id)

        recreated = await skill_service.create_skill(SkillCreate(name="reused-skill"))

        assert recreated.id != deleted.id
        assert recreated.slug == deleted.slug

    async def test_create_skill_reuses_slug_of_legacy_archived_row(
        self,
        skill_service: SkillService,
        session: AsyncSession,
    ) -> None:
        """Rows archived by legacy pods (``archived_at`` set, ``deleted_at``
        NULL) are effectively dead and must not reserve their slug — the
        service check matches the ``uq_skill_workspace_slug_active`` partial
        index predicate, which frees the slug for live rows.
        """

        legacy = await skill_service.create_skill(SkillCreate(name="legacy-archived"))
        legacy_row = (
            await session.execute(select(Skill).where(Skill.id == legacy.id))
        ).scalar_one()
        legacy_row.archived_at = datetime.now(UTC)
        assert legacy_row.deleted_at is None
        await session.commit()

        recreated = await skill_service.create_skill(
            SkillCreate(name="legacy-archived")
        )

        assert recreated.id != legacy.id
        assert recreated.slug == legacy.slug

    async def test_create_skill_reserves_slugless_legacy_row_name(
        self,
        skill_service: SkillService,
        session: AsyncSession,
    ) -> None:
        """Live legacy rows without a slug reserve their name as a slug.

        Old pods insert skills with ``slug IS NULL`` during the expand
        window and reads project their name as the slug, so allocation must
        treat that name as taken to keep apparent slugs live-unique.
        """

        legacy = await skill_service.create_skill(SkillCreate(name="legacy-slugless"))
        legacy_row = (
            await session.execute(select(Skill).where(Skill.id == legacy.id))
        ).scalar_one()
        legacy_row.slug = None
        await session.commit()

        created = await skill_service.create_skill(SkillCreate(name="legacy-slugless"))

        assert created.slug == "legacy-slugless-2"

    async def test_create_skill_preserves_multiline_description(
        self,
        skill_service: SkillService,
    ) -> None:
        """Seeded SKILL.md should remain valid for YAML-sensitive values."""

        created = await skill_service.create_skill(
            SkillCreate(
                name="yaml-skill",
                description="Line one\nLine two",
            )
        )

        draft = await skill_service.get_draft(created.id)

        assert draft is not None
        assert draft.is_publishable is True
        assert draft.name == "yaml-skill"
        assert draft.description == "Line one\nLine two"

    async def test_skill_md_frontmatter_accepts_crlf_delimiters(
        self,
        skill_service: SkillService,
    ) -> None:
        """CRLF frontmatter delimiters should validate like LF delimiters."""

        created = await skill_service.create_skill(SkillCreate(name="crlf-skill"))
        draft = await skill_service.get_draft(created.id)
        assert draft is not None

        await skill_service.patch_draft(
            skill_id=created.id,
            params=SkillDraftPatch(
                base_revision=draft.draft_revision,
                operations=[
                    SkillDraftUpsertTextFileOp(
                        path="SKILL.md",
                        content=(
                            "---\r\n"
                            "name: crlf-skill\r\n"
                            "description: Created on Windows\r\n"
                            "---\r\n"
                            "# CRLF skill\r\n"
                        ),
                        content_type="text/markdown; charset=utf-8",
                    )
                ],
            ),
        )

        updated_draft = await skill_service.get_draft(created.id)

        assert updated_draft is not None
        assert updated_draft.is_publishable is True
        assert updated_draft.validation_errors == []
        assert updated_draft.name == "crlf-skill"
        assert updated_draft.description == "Created on Windows"

    async def test_skill_md_frontmatter_accepts_leading_utf8_bom(
        self,
        skill_service: SkillService,
    ) -> None:
        """UTF-8 BOM-prefixed frontmatter should validate like plain frontmatter."""

        created = await skill_service.create_skill(SkillCreate(name="bom-skill"))
        draft = await skill_service.get_draft(created.id)
        assert draft is not None

        await skill_service.patch_draft(
            skill_id=created.id,
            params=SkillDraftPatch(
                base_revision=draft.draft_revision,
                operations=[
                    SkillDraftUpsertTextFileOp(
                        path="SKILL.md",
                        content=(
                            "\ufeff---\n"
                            "name: bom-skill\n"
                            "description: Saved with BOM\n"
                            "---\n"
                            "# BOM skill\n"
                        ),
                        content_type="text/markdown; charset=utf-8",
                    )
                ],
            ),
        )

        updated_draft = await skill_service.get_draft(created.id)

        assert updated_draft is not None
        assert updated_draft.is_publishable is True
        assert updated_draft.validation_errors == []
        assert updated_draft.name == "bom-skill"
        assert updated_draft.description == "Saved with BOM"

    async def test_skill_md_frontmatter_accepts_closing_delimiter_at_eof(
        self,
        skill_service: SkillService,
    ) -> None:
        """EOF closing frontmatter delimiters should validate like newline delimiters."""

        created = await skill_service.create_skill(SkillCreate(name="eof-skill"))
        draft = await skill_service.get_draft(created.id)
        assert draft is not None

        await skill_service.patch_draft(
            skill_id=created.id,
            params=SkillDraftPatch(
                base_revision=draft.draft_revision,
                operations=[
                    SkillDraftUpsertTextFileOp(
                        path="SKILL.md",
                        content=(
                            "---\n"
                            "name: eof-skill\n"
                            "description: No trailing newline\n"
                            "---"
                        ),
                        content_type="text/markdown; charset=utf-8",
                    )
                ],
            ),
        )

        updated_draft = await skill_service.get_draft(created.id)

        assert updated_draft is not None
        assert updated_draft.is_publishable is True
        assert updated_draft.validation_errors == []
        assert updated_draft.name == "eof-skill"
        assert updated_draft.description == "No trailing newline"

    def test_create_skill_rejects_non_spec_name(self) -> None:
        """Skill names must already satisfy the spec format."""

        with pytest.raises(ValidationError, match="string_pattern_mismatch"):
            SkillCreate(name="  Triage Skill 2026  ")

    async def test_upload_skill_allows_duplicate_active_name(
        self,
        skill_service: SkillService,
    ) -> None:
        """Uploading a second logical skill with the same current name is allowed."""

        await skill_service.create_skill(SkillCreate(name="duplicate-skill"))

        created = await skill_service.upload_skill(
            SkillUpload(
                name="duplicate-skill",
                files=[
                    SkillUploadFile(
                        path="SKILL.md",
                        content_base64=base64.b64encode(
                            b"---\nname: duplicate-skill\n---\n\n# Duplicate\n"
                        ).decode(),
                        content_type="text/markdown; charset=utf-8",
                    )
                ],
            )
        )

        assert created.name == "duplicate-skill"
        assert created.id is not None

    async def test_replace_skill_draft_updates_existing_skill(
        self,
        skill_service: SkillService,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Replacing a draft should update one skill instead of creating a duplicate."""

        async def _has_entitlement(_entitlement):
            return True

        monkeypatch.setattr(skill_service, "has_entitlement", _has_entitlement)

        created = await skill_service.upload_skill(
            SkillUpload(
                name="replace-skill",
                files=[
                    SkillUploadFile(
                        path="SKILL.md",
                        content_base64=base64.b64encode(
                            b"---\nname: replace-skill\n---\n\n# Original\n"
                        ).decode(),
                        content_type="text/markdown; charset=utf-8",
                    ),
                    SkillUploadFile(
                        path="old.txt",
                        content_base64=base64.b64encode(b"old").decode(),
                        content_type="text/plain; charset=utf-8",
                    ),
                ],
            )
        )

        updated = await skill_service.replace_skill_draft(
            skill_id=created.id,
            params=SkillUpload(
                name="replace-skill",
                files=[
                    SkillUploadFile(
                        path="SKILL.md",
                        content_base64=base64.b64encode(
                            b"---\n"
                            b"name: replace-skill\n"
                            b"description: Updated description\n"
                            b"---\n\n"
                            b"# Updated\n"
                        ).decode(),
                        content_type="text/markdown; charset=utf-8",
                    ),
                    SkillUploadFile(
                        path="payload.bin",
                        content_base64=base64.b64encode(b"\x00\x01").decode(),
                        content_type="application/octet-stream",
                    ),
                ],
            ),
        )

        draft = await skill_service.get_draft(created.id)
        old_file = await skill_service.get_draft_file(
            skill_id=created.id,
            path="old.txt",
        )

        assert updated.id == created.id
        assert updated.description == "Updated description"
        assert updated.draft_revision == created.draft_revision + 1
        assert updated.draft_file_count == 2
        assert draft is not None
        assert draft.is_publishable is True
        assert {file.path for file in draft.files} == {"SKILL.md", "payload.bin"}
        assert old_file is None

    async def test_replace_skill_draft_rejects_invalid_manifest_before_blob_upload(
        self,
        skill_service: SkillService,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Invalid replacements should fail before writing blob objects."""

        async def _has_entitlement(_entitlement):
            return True

        monkeypatch.setattr(skill_service, "has_entitlement", _has_entitlement)

        created = await skill_service.upload_skill(
            SkillUpload(
                name="invalid-replace-skill",
                files=[
                    SkillUploadFile(
                        path="SKILL.md",
                        content_base64=base64.b64encode(
                            b"---\nname: invalid-replace-skill\n---\n\n# Original\n"
                        ).decode(),
                        content_type="text/markdown; charset=utf-8",
                    )
                ],
            )
        )
        upload_called = False

        async def fake_upload_file(
            *,
            content: bytes,
            key: str,
            bucket: str,
            content_type: str,
            redact_log_identifiers: bool = False,
        ) -> None:
            del content, key, bucket, content_type, redact_log_identifiers
            nonlocal upload_called
            upload_called = True

        monkeypatch.setattr(
            "tracecat.agent.skill.service.blob.upload_file",
            fake_upload_file,
        )

        with pytest.raises(TracecatValidationError, match="failed validation"):
            await skill_service.replace_skill_draft(
                skill_id=created.id,
                params=SkillUpload(
                    name="invalid-replace-skill",
                    files=[
                        SkillUploadFile(
                            path="notes.txt",
                            content_base64=base64.b64encode(
                                b"not a valid skill manifest"
                            ).decode(),
                            content_type="text/plain; charset=utf-8",
                        )
                    ],
                ),
            )

        assert upload_called is False

    async def test_upload_skill_reuses_blob_across_distinct_file_content_types(
        self,
        skill_service: SkillService,
    ) -> None:
        """Same bytes should deduplicate even when file MIME types differ."""

        shared_bytes = b"shared payload"
        encoded = base64.b64encode(shared_bytes).decode()

        created = await skill_service.upload_skill(
            SkillUpload(
                name="content-type-skill",
                files=[
                    SkillUploadFile(
                        path="SKILL.md",
                        content_base64=base64.b64encode(
                            b"---\nname: content-type-skill\n---\n\n# Skill\n"
                        ).decode(),
                        content_type="text/markdown; charset=utf-8",
                    ),
                    SkillUploadFile(
                        path="notes.txt",
                        content_base64=encoded,
                        content_type="text/plain; charset=utf-8",
                    ),
                    SkillUploadFile(
                        path="payload.bin",
                        content_base64=encoded,
                        content_type="application/octet-stream",
                    ),
                ],
            )
        )

        notes_file = await skill_service.get_draft_file(
            skill_id=created.id,
            path="notes.txt",
        )
        payload_file = await skill_service.get_draft_file(
            skill_id=created.id,
            path="payload.bin",
        )
        sha256 = hashlib.sha256(shared_bytes).hexdigest()
        blob_rows = (
            (
                await skill_service.session.execute(
                    select(SkillBlob).where(
                        SkillBlob.workspace_id == skill_service.workspace_id,
                        SkillBlob.sha256 == sha256,
                    )
                )
            )
            .scalars()
            .all()
        )

        assert notes_file is not None
        assert notes_file.kind == "inline"
        assert notes_file.content_type == "text/plain; charset=utf-8"
        assert payload_file is not None
        assert payload_file.kind == "download"
        assert payload_file.content_type == "application/octet-stream"
        assert len(blob_rows) == 1
        assert blob_rows[0].key == skill_service._storage_key_for(sha256)

    async def test_upload_skill_rejects_invalid_manifest_before_blob_upload(
        self,
        skill_service: SkillService,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Invalid one-shot uploads should fail before writing blob objects."""

        upload_called = False

        async def fake_upload_file(
            *,
            content: bytes,
            key: str,
            bucket: str,
            content_type: str,
            redact_log_identifiers: bool = False,
        ) -> None:
            del content, key, bucket, content_type, redact_log_identifiers
            nonlocal upload_called
            upload_called = True

        monkeypatch.setattr(
            "tracecat.agent.skill.service.blob.upload_file",
            fake_upload_file,
        )

        with pytest.raises(TracecatValidationError, match="failed validation"):
            await skill_service.upload_skill(
                SkillUpload(
                    name="invalid-upload",
                    files=[
                        SkillUploadFile(
                            path="notes.txt",
                            content_base64=base64.b64encode(
                                b"not a valid skill manifest"
                            ).decode(),
                            content_type="text/plain; charset=utf-8",
                        )
                    ],
                )
            )

        assert upload_called is False

    async def test_invalid_skill_md_frontmatter_stays_in_validation_channel(
        self,
        skill_service: SkillService,
    ) -> None:
        """Malformed frontmatter should not break draft, read, or list responses."""

        created = await skill_service.create_skill(
            SkillCreate(name="broken-frontmatter")
        )
        draft = await skill_service.get_draft(created.id)
        assert draft is not None

        await skill_service.patch_draft(
            skill_id=created.id,
            params=SkillDraftPatch(
                base_revision=draft.draft_revision,
                operations=[
                    SkillDraftUpsertTextFileOp(
                        path="SKILL.md",
                        content="---\nname: [broken\n---\n# Broken skill\n",
                        content_type="text/markdown; charset=utf-8",
                    )
                ],
            ),
        )

        updated_draft = await skill_service.get_draft(created.id)
        skill_read = await skill_service.get_skill_read(created.id)
        listing = await skill_service.list_skills(CursorPaginationParams(limit=10))

        assert updated_draft is not None
        assert updated_draft.is_publishable is False
        assert [error.code for error in updated_draft.validation_errors] == [
            "invalid_skill_md_frontmatter"
        ]
        assert skill_read is not None
        assert [error.code for error in skill_read.draft_validation_errors] == [
            "invalid_skill_md_frontmatter"
        ]
        assert len(listing.items) == 1
        assert isinstance(listing.items[0], SkillReadMinimal)
        assert listing.items[0].id == created.id
        assert listing.items[0].name == created.name
        # List responses expose the late-binding handle callers bind with.
        assert listing.items[0].slug == created.slug

    async def test_list_skills_excludes_archived_skills(
        self,
        skill_service: SkillService,
    ) -> None:
        """Archived skills are hidden from the normal skills list."""

        archived = await skill_service.create_skill(SkillCreate(name="archived-skill"))
        active = await skill_service.create_skill(SkillCreate(name="active-skill"))

        await skill_service.archive_skill(archived.id)
        listing = await skill_service.list_skills(CursorPaginationParams(limit=10))

        assert [skill.id for skill in listing.items] == [active.id]
        assert await skill_service.get_skill(archived.id) is None
        assert (
            await skill_service.get_skill(archived.id, include_archived=True)
            is not None
        )
        assert isinstance(listing.items[0], SkillReadMinimal)
        assert listing.items[0].id == active.id
        assert listing.items[0].name == active.name

    async def test_get_skill_by_identifier_accepts_uuid_or_slug(
        self,
        skill_service: SkillService,
    ) -> None:
        """Identifiers resolve by UUID or live slug."""

        created = await skill_service.create_skill(SkillCreate(name="lookup-skill"))

        by_uuid = await skill_service.get_skill_by_identifier(created.id)
        by_uuid_string = await skill_service.get_skill_by_identifier(str(created.id))
        by_slug = await skill_service.get_skill_by_identifier(created.slug)

        assert by_uuid is not None
        assert by_uuid.id == created.id
        assert by_uuid_string is not None
        assert by_uuid_string.id == created.id
        assert by_slug is not None
        assert by_slug.id == created.id

    async def test_get_skill_by_identifier_falls_back_to_uuid_like_slug(
        self,
        skill_service: SkillService,
    ) -> None:
        """UUID-shaped slugs remain reachable when no skill ID matches."""

        uuid_like_slug = "00000000-0000-4000-8000-000000000001"
        created = await skill_service.create_skill(SkillCreate(name=uuid_like_slug))

        by_uuid_like_slug = await skill_service.get_skill_by_identifier(uuid_like_slug)

        assert by_uuid_like_slug is not None
        assert by_uuid_like_slug.id == created.id

    async def test_get_skill_by_identifier_resolves_slugless_legacy_row(
        self,
        skill_service: SkillService,
        session: AsyncSession,
    ) -> None:
        """Live legacy rows without a slug stay reachable by their name.

        Old pods insert skills with ``slug IS NULL`` during the expand
        window; reads advertise their name as the slug, so identifier
        lookup must honor it. An exact slug match wins over the fallback.
        """

        legacy = await skill_service.create_skill(SkillCreate(name="legacy-lookup"))
        legacy_row = (
            await session.execute(select(Skill).where(Skill.id == legacy.id))
        ).scalar_one()
        legacy_row.slug = None
        await session.commit()

        resolved = await skill_service.get_skill_by_identifier("legacy-lookup")

        assert resolved is not None
        assert resolved.id == legacy.id

        exact = await skill_service.create_skill(SkillCreate(name="legacy-lookup"))
        assert exact.slug == "legacy-lookup-2"
        exact_row = (
            await session.execute(select(Skill).where(Skill.id == exact.id))
        ).scalar_one()
        exact_row.slug = "legacy-lookup"
        await session.commit()

        resolved_exact = await skill_service.get_skill_by_identifier("legacy-lookup")

        assert resolved_exact is not None
        assert resolved_exact.id == exact.id

    async def test_get_skill_by_identifier_ambiguous_legacy_rows_raise(
        self,
        skill_service: SkillService,
        session: AsyncSession,
    ) -> None:
        """Two live legacy rows projecting the same fallback slug fail loud.

        ``uq_skill_workspace_slug_active`` does not constrain NULL slugs and
        old pods enforced no name uniqueness, so the expand window can leave
        two live same-name slugless rows. Resolving one silently would be a
        silent substitution; the lookup must raise instead. The edge
        disappears at contract when slugs are backfilled NOT NULL.
        """

        first = await skill_service.create_skill(SkillCreate(name="legacy-dup"))
        second = await skill_service.create_skill(SkillCreate(name="legacy-dup"))
        rows = (
            (
                await session.execute(
                    select(Skill).where(Skill.id.in_([first.id, second.id]))
                )
            )
            .scalars()
            .all()
        )
        for row in rows:
            row.name = "legacy-dup"
            row.slug = None
        await session.commit()

        with pytest.raises(TracecatValidationError, match="ambiguous"):
            await skill_service.get_skill_by_identifier("legacy-dup")

    async def test_get_skill_by_identifier_prefers_id_over_matching_slug(
        self,
        skill_service: SkillService,
    ) -> None:
        """A UUID string matching one ID and another slug resolves to the ID."""

        by_id = await skill_service.create_skill(SkillCreate(name="collision-id"))
        by_slug = await skill_service.create_skill(SkillCreate(name=str(by_id.id)))

        resolved = await skill_service.get_skill_by_identifier(str(by_id.id))

        assert resolved is not None
        assert resolved.id == by_id.id
        assert resolved.id != by_slug.id

    async def test_get_skill_by_identifier_excludes_soft_deleted_slugs(
        self,
        skill_service: SkillService,
    ) -> None:
        """Slug lookup ignores soft-deleted rows."""

        deleted = await skill_service.create_skill(SkillCreate(name="deleted-lookup"))

        await skill_service.archive_skill(deleted.id)

        assert await skill_service.get_skill_by_identifier(deleted.slug) is None

    async def test_get_skill_by_identifier_recreated_slug_resolves_to_new_skill(
        self,
        skill_service: SkillService,
    ) -> None:
        """Reused slugs resolve to the new live skill, not the tombstone."""

        deleted = await skill_service.create_skill(SkillCreate(name="recreated-lookup"))
        await skill_service.archive_skill(deleted.id)
        recreated = await skill_service.create_skill(SkillCreate(name=deleted.slug))

        resolved = await skill_service.get_skill_by_identifier(deleted.slug)

        assert resolved is not None
        assert resolved.id == recreated.id
        assert resolved.id != deleted.id

    async def test_get_skill_by_identifier_unknown_returns_none(
        self,
        skill_service: SkillService,
    ) -> None:
        """Unknown identifiers remain not found."""

        assert await skill_service.get_skill_by_identifier("missing-skill") is None

    async def test_patch_draft_enforces_revision(
        self,
        skill_service: SkillService,
    ) -> None:
        """Draft mutations require the current draft revision."""

        created = await skill_service.create_skill(SkillCreate(name="revision-skill"))

        with pytest.raises(TracecatValidationError, match="Draft revision conflict"):
            await skill_service.patch_draft(
                skill_id=created.id,
                params=SkillDraftPatch(
                    base_revision=0,
                    operations=[
                        SkillDraftUpsertTextFileOp(
                            path="references/context.md",
                            content="Reference content",
                        )
                    ],
                ),
            )

    async def test_patch_draft_rejects_terminal_parent_path_segments(
        self,
        skill_service: SkillService,
    ) -> None:
        """Draft mutations reject any path segment equal to '..'."""

        created = await skill_service.create_skill(
            SkillCreate(name="invalid-path-skill")
        )
        draft = await skill_service.get_draft(created.id)
        assert draft is not None

        with pytest.raises(
            TracecatValidationError, match="cannot escape the skill root"
        ):
            await skill_service.patch_draft(
                skill_id=created.id,
                params=SkillDraftPatch(
                    base_revision=draft.draft_revision,
                    operations=[
                        SkillDraftUpsertTextFileOp(
                            path="references/..",
                            content="blocked",
                        )
                    ],
                ),
            )

    async def test_patch_draft_concurrent_requests_conflict(
        self,
        svc_role: Role,
    ) -> None:
        """Concurrent draft patches with the same revision do not both commit."""

        role = svc_role.model_copy(update={"workspace_id": uuid.uuid4()}, deep=True)
        concurrent_engine = create_async_engine(TEST_DB_CONFIG.test_url)
        session_factory = async_sessionmaker(
            bind=concurrent_engine,
            expire_on_commit=False,
        )

        try:
            async with session_factory() as seed_session:
                workspace = await seed_session.scalar(
                    select(Workspace).where(Workspace.id == role.workspace_id)
                )
                if workspace is None:
                    seed_session.add(
                        Workspace(
                            id=role.workspace_id,
                            name="test-workspace",
                            organization_id=role.organization_id,
                        )
                    )
                    await seed_session.commit()

                seed_service = SkillService(
                    session=seed_session,
                    role=role.model_copy(deep=True),
                )
                created = await seed_service.create_skill(
                    SkillCreate(name="concurrent-draft-skill")
                )
                draft = await seed_service.get_draft(created.id)
                assert draft is not None

            async def patch_draft(
                index: int,
            ) -> SkillDraftRead | TracecatValidationError:
                async with session_factory() as concurrent_session:
                    service = SkillService(
                        session=concurrent_session,
                        role=role.model_copy(deep=True),
                    )
                    try:
                        return await service.patch_draft(
                            skill_id=created.id,
                            params=SkillDraftPatch(
                                base_revision=draft.draft_revision,
                                operations=[
                                    SkillDraftUpsertTextFileOp(
                                        path=f"references/{index}.md",
                                        content=f"content {index}",
                                    )
                                ],
                            ),
                        )
                    except TracecatValidationError as exc:
                        return exc

            results = await asyncio.gather(
                patch_draft(1),
                patch_draft(2),
            )

            successes = [
                result for result in results if isinstance(result, SkillDraftRead)
            ]
            conflicts = [
                result
                for result in results
                if isinstance(result, TracecatValidationError)
            ]

            assert len(successes) == 1
            assert len(conflicts) == 1
            assert "Draft revision conflict" in str(conflicts[0])

            async with session_factory() as verification_session:
                service = SkillService(
                    session=verification_session,
                    role=role.model_copy(deep=True),
                )
                final_draft = await service.get_draft(created.id)

            assert final_draft is not None
            assert final_draft.draft_revision == draft.draft_revision + 1
            reference_paths = [
                file.path
                for file in final_draft.files
                if file.path.startswith("references/")
            ]
            assert len(reference_paths) == 1
        finally:
            await concurrent_engine.dispose()

    async def test_patch_draft_refreshes_preloaded_revision_before_lock(
        self,
        svc_role: Role,
    ) -> None:
        """The locking read must refresh a skill already in the identity map."""

        role = svc_role.model_copy(update={"workspace_id": uuid.uuid4()}, deep=True)
        concurrent_engine = create_async_engine(TEST_DB_CONFIG.test_url)
        session_factory = async_sessionmaker(
            bind=concurrent_engine,
            expire_on_commit=False,
        )

        try:
            async with session_factory() as stale_session:
                workspace = await stale_session.scalar(
                    select(Workspace).where(Workspace.id == role.workspace_id)
                )
                if workspace is None:
                    stale_session.add(
                        Workspace(
                            id=role.workspace_id,
                            name="test-workspace",
                            organization_id=role.organization_id,
                        )
                    )
                    await stale_session.commit()

                stale_service = SkillService(
                    session=stale_session,
                    role=role.model_copy(deep=True),
                )
                created = await stale_service.create_skill(
                    SkillCreate(name="preloaded-revision-skill")
                )
                stale_draft = await stale_service.get_draft(created.id)
                assert stale_draft is not None

                async with session_factory() as competing_session:
                    competing_service = SkillService(
                        session=competing_session,
                        role=role.model_copy(deep=True),
                    )
                    committed = await competing_service.patch_draft(
                        skill_id=created.id,
                        params=SkillDraftPatch(
                            base_revision=stale_draft.draft_revision,
                            operations=[
                                SkillDraftUpsertTextFileOp(
                                    path="references/competing.md",
                                    content="competing content",
                                )
                            ],
                        ),
                    )

                with pytest.raises(TracecatValidationError) as exc_info:
                    await stale_service.patch_draft(
                        skill_id=created.id,
                        params=SkillDraftPatch(
                            base_revision=stale_draft.draft_revision,
                            operations=[
                                SkillDraftUpsertTextFileOp(
                                    path="references/stale.md",
                                    content="stale content",
                                )
                            ],
                        ),
                    )

                assert exc_info.value.detail is not None
                assert exc_info.value.detail["code"] == "draft_revision_conflict"
                assert (
                    exc_info.value.detail["current_revision"]
                    == committed.draft_revision
                )
        finally:
            await concurrent_engine.dispose()

    @pytest.mark.parametrize(
        "content",
        [
            pytest.param(b"uploaded content", id="nonempty"),
            pytest.param(b"", id="empty"),
        ],
    )
    async def test_attach_uploaded_blob_promotes_from_staged_key(
        self,
        skill_service: SkillService,
        monkeypatch: pytest.MonkeyPatch,
        content: bytes,
    ) -> None:
        """Staged uploads support empty files and normalized uppercase digests."""

        created = await skill_service.create_skill(SkillCreate(name="staged-upload"))
        draft = await skill_service.get_draft(created.id)
        assert draft is not None

        sha256 = hashlib.sha256(content).hexdigest()
        upload_sha256 = sha256.upper()
        upload = await skill_service.create_draft_upload(
            skill_id=created.id,
            params=SkillUploadSessionCreate(
                sha256=upload_sha256,
                size_bytes=len(content),
                content_type="text/plain; charset=utf-8",
            ),
        )

        canonical_key = skill_service._storage_key_for(sha256)
        assert upload.key != canonical_key
        assert upload.key.startswith(f"skill-uploads/{skill_service.workspace_id}/")
        assert upload.key.endswith(sha256)

        uploaded: dict[str, str] = {}

        async def fake_file_exists(
            *, key: str, bucket: str, redact_log_identifiers: bool = False
        ) -> bool:
            del key, bucket
            assert redact_log_identifiers is True
            return True

        class FakeStream:
            async def read(self) -> bytes:
                return content

            async def iter_chunks(self, *, chunk_size: int):
                del chunk_size
                yield content

        @asynccontextmanager
        async def fake_open_download_stream(
            *, key: str, bucket: str, redact_log_identifiers: bool = False
        ):
            del bucket
            assert redact_log_identifiers is True
            yield FakeStream(), len(content)

        async def fake_copy_file(
            *,
            source_key: str,
            destination_key: str,
            bucket: str,
            content_type: str | None = None,
            redact_log_identifiers: bool = False,
        ) -> None:
            del source_key, bucket, content_type
            assert redact_log_identifiers is True
            uploaded["key"] = destination_key

        monkeypatch.setattr(
            "tracecat.agent.skill.service.blob.file_exists", fake_file_exists
        )
        monkeypatch.setattr(
            "tracecat.agent.skill.service.blob.open_download_stream",
            fake_open_download_stream,
        )
        monkeypatch.setattr(
            "tracecat.agent.skill.service.blob.copy_file",
            fake_copy_file,
        )

        await skill_service.patch_draft(
            skill_id=created.id,
            params=SkillDraftPatch(
                base_revision=draft.draft_revision,
                operations=[
                    SkillDraftAttachUploadedBlobOp(
                        path="references/uploaded.txt",
                        upload_id=upload.upload_id,
                    )
                ],
            ),
        )

        blob_row = (
            await skill_service.session.execute(
                select(SkillBlob).where(
                    SkillBlob.workspace_id == skill_service.workspace_id,
                    SkillBlob.sha256 == sha256,
                )
            )
        ).scalar_one()

        assert uploaded["key"] == canonical_key
        assert blob_row.key == canonical_key
        assert blob_row.sha256 == sha256

    async def test_delete_published_blob_objects_redacts_failure_logs(
        self,
        skill_service: SkillService,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Rollback cleanup must not log tenant-bearing storage identifiers."""

        sensitive_bucket = "affected-customer-bucket"
        sensitive_key = "skills/tenant-id/private-object"
        delete_file = AsyncMock(
            side_effect=RuntimeError(f"failed {sensitive_bucket}/{sensitive_key}")
        )
        mock_logger = MagicMock()
        monkeypatch.setattr(
            "tracecat.agent.skill.service.blob.delete_file", delete_file
        )
        monkeypatch.setattr(skill_service, "logger", mock_logger)

        await skill_service._delete_blob_objects(
            [PublishedBlobObject(bucket=sensitive_bucket, key=sensitive_key)]
        )

        delete_file.assert_awaited_once_with(
            key=sensitive_key,
            bucket=sensitive_bucket,
            redact_log_identifiers=True,
        )
        mock_logger.warning.assert_called_once_with(
            "Failed to delete rolled-back skill blob object",
            error_type="RuntimeError",
        )
        assert sensitive_bucket not in str(mock_logger.mock_calls)
        assert sensitive_key not in str(mock_logger.mock_calls)

    async def test_patch_draft_rollback_deletes_canonical_objects_it_published(
        self,
        skill_service: SkillService,
        svc_workspace: Workspace,
    ) -> None:
        """A failed batch must remove canonical objects copied for earlier files.

        Uses real MinIO: the first upload is copied to its canonical key before
        the second fails verification. The rollback must delete that copy but
        keep the object of a committed blob the same batch merely reused.
        """

        created = await skill_service.create_skill(SkillCreate(name="batch-rollback"))
        draft = await skill_service.get_draft(created.id)
        assert draft is not None
        bucket = config.TRACECAT__BLOB_STORAGE_BUCKET_SKILLS

        seed_markdown = SkillService._build_default_skill_markdown(
            name="batch-rollback", description=None
        )
        seed_key = skill_service._storage_key_for(
            hashlib.sha256(seed_markdown.encode("utf-8")).hexdigest()
        )
        assert await file_exists(key=seed_key, bucket=bucket)

        # Materialization runs in digest order, so the lower digest is the one
        # copied to its canonical key before the higher one fails.
        good_content, bad_content = sorted(
            (b"first staged file", b"second staged file"),
            key=lambda content: hashlib.sha256(content).hexdigest(),
        )
        good_sha256 = hashlib.sha256(good_content).hexdigest()
        bad_sha256 = hashlib.sha256(bad_content).hexdigest()
        good_canonical_key = skill_service._storage_key_for(good_sha256)

        uploads: list[Any] = []
        for content, sha256 in ((good_content, good_sha256), (bad_content, bad_sha256)):
            upload = await skill_service.create_draft_upload(
                skill_id=created.id,
                params=SkillUploadSessionCreate(
                    sha256=sha256,
                    size_bytes=len(content),
                    content_type="text/plain; charset=utf-8",
                ),
            )
            uploads.append(upload)
        good_upload, bad_upload = uploads
        await upload_file(
            content=good_content,
            key=good_upload.key,
            bucket=good_upload.bucket,
            content_type="application/octet-stream",
        )
        # Corrupt the second staged object so its verification fails.
        await upload_file(
            content=bad_content + b"!",
            key=bad_upload.key,
            bucket=bad_upload.bucket,
            content_type="application/octet-stream",
        )

        with pytest.raises(TracecatValidationError) as exc_info:
            await skill_service.patch_draft(
                skill_id=created.id,
                params=SkillDraftPatch(
                    base_revision=draft.draft_revision,
                    operations=[
                        SkillDraftAttachUploadedBlobOp(
                            path="references/good.txt",
                            upload_id=good_upload.upload_id,
                        ),
                        SkillDraftAttachUploadedBlobOp(
                            path="references/bad.txt",
                            upload_id=bad_upload.upload_id,
                        ),
                        # Reuses the committed seed blob; must survive rollback.
                        SkillDraftUpsertTextFileOp(
                            path="references/seed-copy.md",
                            content=seed_markdown,
                            content_type="text/markdown; charset=utf-8",
                        ),
                    ],
                ),
            )
        assert exc_info.value.detail is not None
        assert exc_info.value.detail["code"] == "upload_integrity_error"

        assert not await file_exists(key=good_canonical_key, bucket=bucket)
        assert await file_exists(key=seed_key, bucket=bucket)
        good_blob_row = await skill_service.session.scalar(
            select(SkillBlob).where(
                SkillBlob.workspace_id == skill_service.workspace_id,
                SkillBlob.sha256 == good_sha256,
            )
        )
        assert good_blob_row is None
        refreshed = await skill_service.get_draft(created.id)
        assert refreshed is not None
        assert refreshed.draft_revision == draft.draft_revision
        await skill_service.session.refresh(svc_workspace)

    async def test_attach_rejects_canonical_copy_poisoned_after_verification(
        self,
        skill_service: SkillService,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A staged re-PUT racing the canonical copy must not become a blob.

        The staged PUT URL can still be valid while completion runs, so the
        canonical copy is re-verified; bytes that differ from the verified
        staged read are rejected and the canonical object is deleted.
        """

        content = b"verified staged bytes\n"
        poisoned = b"poisoned canonical bytes\n"
        sha256 = hashlib.sha256(content).hexdigest()

        created = await skill_service.create_skill(SkillCreate(name="poison-race"))
        draft = await skill_service.get_draft(created.id)
        assert draft is not None

        upload = await skill_service.create_draft_upload(
            skill_id=created.id,
            params=SkillUploadSessionCreate(
                sha256=sha256,
                size_bytes=len(content),
                content_type="text/plain; charset=utf-8",
            ),
        )
        canonical_key = skill_service._storage_key_for(sha256)
        deleted: list[str] = []

        async def fake_file_exists(
            *, key: str, bucket: str, redact_log_identifiers: bool = False
        ) -> bool:
            del key, bucket
            assert redact_log_identifiers is True
            return True

        class FakeStream:
            def __init__(self, payload: bytes) -> None:
                self._payload = payload

            async def read(self) -> bytes:
                return self._payload

            async def iter_chunks(self, *, chunk_size: int):
                del chunk_size
                yield self._payload

        @asynccontextmanager
        async def fake_open_download_stream(
            *, key: str, bucket: str, redact_log_identifiers: bool = False
        ):
            del bucket
            assert redact_log_identifiers is True
            payload = poisoned if key == canonical_key else content
            yield FakeStream(payload), len(payload)

        async def fake_copy_file(
            *,
            source_key: str,
            destination_key: str,
            bucket: str,
            content_type: str | None = None,
            redact_log_identifiers: bool = False,
        ) -> None:
            del source_key, destination_key, bucket, content_type
            assert redact_log_identifiers is True

        async def fake_delete_file(
            *, key: str, bucket: str, redact_log_identifiers: bool = False
        ) -> None:
            assert redact_log_identifiers is True
            del bucket
            deleted.append(key)

        monkeypatch.setattr(
            "tracecat.agent.skill.service.blob.file_exists", fake_file_exists
        )
        monkeypatch.setattr(
            "tracecat.agent.skill.service.blob.open_download_stream",
            fake_open_download_stream,
        )
        monkeypatch.setattr(
            "tracecat.agent.skill.service.blob.copy_file", fake_copy_file
        )
        monkeypatch.setattr(
            "tracecat.agent.skill.service.blob.delete_file", fake_delete_file
        )

        with pytest.raises(TracecatValidationError) as excinfo:
            await skill_service.patch_draft(
                skill_id=created.id,
                params=SkillDraftPatch(
                    base_revision=draft.draft_revision,
                    operations=[
                        SkillDraftAttachUploadedBlobOp(
                            path="references/uploaded.txt",
                            upload_id=upload.upload_id,
                        )
                    ],
                ),
            )

        assert excinfo.value.detail is not None
        assert excinfo.value.detail["code"] == "upload_integrity_error"
        # Both the immediate integrity-error cleanup and the outer rollback
        # cleanup may delete the canonical object; S3 deletes are idempotent.
        assert deleted
        assert set(deleted) == {canonical_key}
        blob_row = (
            await skill_service.session.execute(
                select(SkillBlob).where(
                    SkillBlob.workspace_id == skill_service.workspace_id,
                    SkillBlob.sha256 == sha256,
                )
            )
        ).scalar_one_or_none()
        assert blob_row is None

    async def test_upload_cancellation_cleans_registered_canonical_key(
        self,
        skill_service: SkillService,
        svc_workspace: Workspace,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Cancellation after an accepted upload must delete the canonical object."""

        content = "upload accepted before cancellation\n"
        sha256 = hashlib.sha256(content.encode()).hexdigest()
        created = await skill_service.create_skill(SkillCreate(name="cancel-upload"))
        draft = await skill_service.get_draft(created.id)
        assert draft is not None
        canonical_key = skill_service._storage_key_for(sha256)
        stored_keys: set[str] = set()
        deleted: list[str] = []

        async def cancel_after_accepted_upload(
            *,
            content: bytes,
            key: str,
            bucket: str,
            content_type: str | None = None,
            redact_log_identifiers: bool = False,
        ) -> None:
            del content, bucket, content_type
            assert redact_log_identifiers is True
            stored_keys.add(key)
            raise asyncio.CancelledError()

        async def fake_delete_file(
            *, key: str, bucket: str, redact_log_identifiers: bool = False
        ) -> None:
            del bucket
            assert redact_log_identifiers is True
            deleted.append(key)
            stored_keys.discard(key)

        monkeypatch.setattr(
            "tracecat.agent.skill.service.blob.upload_file",
            cancel_after_accepted_upload,
        )
        monkeypatch.setattr(
            "tracecat.agent.skill.service.blob.delete_file", fake_delete_file
        )

        with pytest.raises(asyncio.CancelledError):
            await skill_service.patch_draft(
                skill_id=created.id,
                params=SkillDraftPatch(
                    base_revision=draft.draft_revision,
                    operations=[
                        SkillDraftUpsertTextFileOp(
                            path="references/uploaded.txt",
                            content=content,
                        )
                    ],
                ),
            )

        assert deleted == [canonical_key]
        assert stored_keys == set()
        blob_row = await skill_service.session.scalar(
            select(SkillBlob).where(
                SkillBlob.workspace_id == skill_service.workspace_id,
                SkillBlob.sha256 == sha256,
            )
        )
        assert blob_row is None
        await skill_service.session.refresh(svc_workspace)

    async def test_attach_redacts_canonical_verification_failure(
        self,
        skill_service: SkillService,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Canonical verification hides tenant identifiers on storage failure."""

        content = b"verified staged bytes\n"
        sha256 = hashlib.sha256(content).hexdigest()
        created = await skill_service.create_skill(SkillCreate(name="verify-failure"))
        draft = await skill_service.get_draft(created.id)
        assert draft is not None
        upload = await skill_service.create_draft_upload(
            skill_id=created.id,
            params=SkillUploadSessionCreate(
                sha256=sha256,
                size_bytes=len(content),
                content_type="text/plain; charset=utf-8",
            ),
        )
        canonical_key = skill_service._storage_key_for(sha256)
        provider_message = f"denied access to {upload.bucket}/{canonical_key}"
        get_object_calls = 0
        deleted: list[str] = []

        async def fake_file_exists(
            *, key: str, bucket: str, redact_log_identifiers: bool = False
        ) -> bool:
            del key, bucket
            assert redact_log_identifiers is True
            return True

        class FakeStream:
            async def __aenter__(self) -> "FakeStream":
                return self

            async def __aexit__(self, *_args: object) -> None:
                return None

            async def iter_chunks(self, *, chunk_size: int):
                del chunk_size
                yield content

        @asynccontextmanager
        async def fake_get_storage_client():
            client = AsyncMock()

            async def get_object(*, Bucket: str, Key: str):
                nonlocal get_object_calls
                get_object_calls += 1
                if get_object_calls == 1:
                    return {"Body": FakeStream(), "ContentLength": len(content)}
                raise ClientError(
                    error_response={
                        "Error": {
                            "Code": "AccessDenied",
                            "Message": provider_message,
                        }
                    },
                    operation_name="GetObject",
                )

            client.get_object.side_effect = get_object
            yield client

        async def fake_copy_file(
            *,
            source_key: str,
            destination_key: str,
            bucket: str,
            content_type: str | None = None,
            redact_log_identifiers: bool = False,
        ) -> None:
            del source_key, destination_key, bucket, content_type
            assert redact_log_identifiers is True

        async def fake_delete_file(
            *, key: str, bucket: str, redact_log_identifiers: bool = False
        ) -> None:
            del bucket
            assert redact_log_identifiers is True
            deleted.append(key)

        mock_logger = MagicMock()
        monkeypatch.setattr(
            "tracecat.agent.skill.service.blob.file_exists", fake_file_exists
        )
        monkeypatch.setattr(
            "tracecat.agent.skill.service.blob.get_storage_client",
            fake_get_storage_client,
        )
        monkeypatch.setattr(
            "tracecat.agent.skill.service.blob.copy_file", fake_copy_file
        )
        monkeypatch.setattr(
            "tracecat.agent.skill.service.blob.delete_file", fake_delete_file
        )
        monkeypatch.setattr("tracecat.storage.blob.logger", mock_logger)

        with pytest.raises(skill_service_module.blob.StorageDownloadError) as raised:
            await skill_service.patch_draft(
                skill_id=created.id,
                params=SkillDraftPatch(
                    base_revision=draft.draft_revision,
                    operations=[
                        SkillDraftAttachUploadedBlobOp(
                            path="references/uploaded.txt",
                            upload_id=upload.upload_id,
                        )
                    ],
                ),
            )

        assert raised.value.error_code == "AccessDenied"
        assert canonical_key not in str(raised.value)
        assert provider_message not in str(raised.value)
        mock_logger.error.assert_called_once_with(
            "Failed to open download stream",
            key="<redacted>",
            bucket="<redacted>",
            error_code="AccessDenied",
            error_type="ClientError",
        )
        assert canonical_key not in str(mock_logger.mock_calls)
        assert str(skill_service.workspace_id) not in str(mock_logger.mock_calls)
        assert provider_message not in str(mock_logger.mock_calls)
        assert get_object_calls == 2
        assert deleted
        assert set(deleted) == {canonical_key}

    async def test_copy_cancellation_cleans_registered_canonical_key(
        self,
        skill_service: SkillService,
        svc_workspace: Workspace,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Cancellation after an accepted copy must delete the canonical object."""

        content = b"copy accepted before cancellation\n"
        sha256 = hashlib.sha256(content).hexdigest()
        created = await skill_service.create_skill(SkillCreate(name="cancel-copy"))
        draft = await skill_service.get_draft(created.id)
        assert draft is not None
        upload = await skill_service.create_draft_upload(
            skill_id=created.id,
            params=SkillUploadSessionCreate(
                sha256=sha256,
                size_bytes=len(content),
                content_type="text/plain; charset=utf-8",
            ),
        )
        canonical_key = skill_service._storage_key_for(sha256)
        stored_keys: set[str] = set()
        deleted: list[str] = []

        async def fake_file_exists(
            *, key: str, bucket: str, redact_log_identifiers: bool = False
        ) -> bool:
            del key, bucket
            assert redact_log_identifiers is True
            return True

        async def fake_stream_verify_object(**_kwargs: Any) -> None:
            return None

        async def cancel_after_accepted_copy(
            *,
            source_key: str,
            destination_key: str,
            bucket: str,
            content_type: str | None = None,
            redact_log_identifiers: bool = False,
        ) -> None:
            del source_key, bucket, content_type
            assert redact_log_identifiers is True
            stored_keys.add(destination_key)
            raise asyncio.CancelledError()

        async def fake_delete_file(
            *, key: str, bucket: str, redact_log_identifiers: bool = False
        ) -> None:
            del bucket
            assert redact_log_identifiers is True
            deleted.append(key)
            stored_keys.discard(key)

        monkeypatch.setattr(
            "tracecat.agent.skill.service.blob.file_exists", fake_file_exists
        )
        monkeypatch.setattr(
            skill_service,
            "_stream_verify_object",
            fake_stream_verify_object,
        )
        monkeypatch.setattr(
            "tracecat.agent.skill.service.blob.copy_file",
            cancel_after_accepted_copy,
        )
        monkeypatch.setattr(
            "tracecat.agent.skill.service.blob.delete_file", fake_delete_file
        )

        with pytest.raises(asyncio.CancelledError):
            await skill_service.patch_draft(
                skill_id=created.id,
                params=SkillDraftPatch(
                    base_revision=draft.draft_revision,
                    operations=[
                        SkillDraftAttachUploadedBlobOp(
                            path="references/uploaded.txt",
                            upload_id=upload.upload_id,
                        )
                    ],
                ),
            )

        assert deleted == [canonical_key]
        assert stored_keys == set()
        blob_row = await skill_service.session.scalar(
            select(SkillBlob).where(
                SkillBlob.workspace_id == skill_service.workspace_id,
                SkillBlob.sha256 == sha256,
            )
        )
        assert blob_row is None
        await skill_service.session.refresh(svc_workspace)

    async def test_patch_draft_returns_before_staged_object_cleanup(
        self,
        skill_service: SkillService,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Slow post-commit cleanup must not delay the committed draft response."""

        content = b"background staged cleanup\n"
        created = await skill_service.create_skill(
            SkillCreate(name="background-cleanup")
        )
        draft = await skill_service.get_draft(created.id)
        assert draft is not None
        upload = await skill_service.create_draft_upload(
            skill_id=created.id,
            params=SkillUploadSessionCreate(
                sha256=hashlib.sha256(content).hexdigest(),
                size_bytes=len(content),
                content_type="text/plain; charset=utf-8",
            ),
        )
        await upload_file(
            content=content,
            key=upload.key,
            bucket=upload.bucket,
            content_type="application/octet-stream",
        )

        deletion_started = asyncio.Event()
        allow_deletion = asyncio.Event()
        deleted: list[tuple[str, str]] = []

        async def blocked_delete_file(
            *, key: str, bucket: str, redact_log_identifiers: bool = False
        ) -> None:
            assert redact_log_identifiers is True
            deletion_started.set()
            await allow_deletion.wait()
            deleted.append((key, bucket))

        monkeypatch.setattr(
            "tracecat.agent.skill.service.blob.delete_file", blocked_delete_file
        )

        patched = await asyncio.wait_for(
            skill_service.patch_draft(
                skill_id=created.id,
                params=SkillDraftPatch(
                    base_revision=draft.draft_revision,
                    operations=[
                        SkillDraftAttachUploadedBlobOp(
                            path="references/uploaded.txt",
                            upload_id=upload.upload_id,
                        )
                    ],
                ),
            ),
            timeout=5,
        )

        assert patched.draft_revision == draft.draft_revision + 1
        try:
            await asyncio.wait_for(deletion_started.wait(), timeout=1)
            assert deleted == []
        finally:
            allow_deletion.set()
            await asyncio.gather(*skill_service_module._staged_upload_cleanup_tasks)
        assert deleted == [(upload.key, upload.bucket)]

    async def test_concurrent_blob_claims_reuse_the_committed_owner(
        self,
        svc_role: Role,
    ) -> None:
        """Same-digest contenders should reuse the row claimed by the winner."""

        role = svc_role.model_copy(update={"workspace_id": uuid.uuid4()}, deep=True)
        concurrent_engine = create_async_engine(TEST_DB_CONFIG.test_url)
        session_factory = async_sessionmaker(
            bind=concurrent_engine,
            expire_on_commit=False,
        )

        try:
            async with session_factory() as seed_session:
                seed_session.add(
                    Workspace(
                        id=role.workspace_id,
                        name="test-workspace",
                        organization_id=role.organization_id,
                    )
                )
                await seed_session.commit()

            content = b"shared staged upload\n"
            sha256 = hashlib.sha256(content).hexdigest()
            storage_key = f"skills/{role.workspace_id}/{sha256}"
            async with (
                session_factory() as owner_session,
                session_factory() as contender_session,
            ):
                owner_service = SkillService(
                    session=owner_session,
                    role=role.model_copy(deep=True),
                )
                contender_service = SkillService(
                    session=contender_session,
                    role=role.model_copy(deep=True),
                )
                owner_claim = await owner_service._claim_blob_publication(
                    sha256=sha256,
                    bucket=config.TRACECAT__BLOB_STORAGE_BUCKET_SKILLS,
                    key=storage_key,
                    size_bytes=len(content),
                )

                contender_started = asyncio.Event()

                async def claim_as_contender() -> SkillBlobPublicationClaim:
                    contender_started.set()
                    claim = await contender_service._claim_blob_publication(
                        sha256=sha256,
                        bucket=config.TRACECAT__BLOB_STORAGE_BUCKET_SKILLS,
                        key=storage_key,
                        size_bytes=len(content),
                    )
                    await contender_session.commit()
                    return claim

                contender_task = asyncio.create_task(claim_as_contender())
                await contender_started.wait()
                await asyncio.sleep(0)
                assert contender_task.done() is False

                await owner_session.commit()
                contender_claim = await asyncio.wait_for(contender_task, timeout=5)

            assert owner_claim.is_owner is True
            assert contender_claim.is_owner is False
            assert contender_claim.blob.id == owner_claim.blob.id
        finally:
            await concurrent_engine.dispose()

    async def test_patch_materializes_blob_claims_in_digest_order(
        self,
        skill_service: SkillService,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Caller path order must not become PostgreSQL claim-lock order."""

        skill_id = uuid.uuid4()
        now = datetime.now(UTC)

        def upload_for(digest: str) -> SkillUploadModel:
            upload = SkillUploadModel(
                workspace_id=skill_service.workspace_id,
                skill_id=skill_id,
                sha256=digest,
                size_bytes=1,
                content_type="application/octet-stream",
                bucket="skills",
                key=f"skill-uploads/{skill_service.workspace_id}/{digest}",
                expires_at=now + timedelta(minutes=5),
                created_by=None,
            )
            upload.id = uuid.uuid4()
            return upload

        upload_a = upload_for("a" * 64)
        upload_b = upload_for("b" * 64)
        blob_a = SkillBlob(
            id=uuid.uuid4(),
            workspace_id=skill_service.workspace_id,
            sha256=upload_a.sha256,
            bucket="skills",
            key=f"skills/{skill_service.workspace_id}/{upload_a.sha256}",
            size_bytes=1,
        )
        blob_b = SkillBlob(
            id=uuid.uuid4(),
            workspace_id=skill_service.workspace_id,
            sha256=upload_b.sha256,
            bucket="skills",
            key=f"skills/{skill_service.workspace_id}/{upload_b.sha256}",
            size_bytes=1,
        )
        calls: list[str] = []

        async def materialize(
            upload: SkillUploadModel,
            *,
            published: list[PublishedBlobObject] | None = None,
        ) -> SkillBlob:
            del published
            calls.append(upload.sha256)
            return blob_a if upload.sha256 == upload_a.sha256 else blob_b

        monkeypatch.setattr(skill_service, "_materialize_uploaded_blob", materialize)

        materialized = await skill_service._materialize_patch_operation_blobs(
            [
                PreparedDraftAttachUploadedBlobOp(path="b.txt", upload=upload_b),
                PreparedDraftAttachUploadedBlobOp(path="a.txt", upload=upload_a),
            ]
        )

        assert calls == [upload_a.sha256, upload_b.sha256]
        assert materialized[0] is blob_b
        assert materialized[1] is blob_a

    async def test_prepare_draft_uploads_commits_one_checksum_bound_batch(
        self,
        skill_service: SkillService,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        created = await skill_service.create_skill(SkillCreate(name="prepare-batch"))
        monkeypatch.setattr(config, "TRACECAT__MAX_SKILL_TRANSFER_FILES_COUNT", 2)
        captured: list[tuple[str, str, str, str, int]] = []

        async def generate_presigned_upload_url(
            *,
            key: str,
            bucket: str,
            content_type: str,
            checksum_sha256: str,
            expiry: int,
            redact_log_identifiers: bool = False,
        ) -> str:
            assert redact_log_identifiers is True
            captured.append(
                (
                    key,
                    bucket,
                    content_type,
                    checksum_sha256,
                    expiry,
                )
            )
            return f"https://uploads.example/{len(captured)}"

        monkeypatch.setattr(
            "tracecat.agent.skill.service.blob.generate_presigned_upload_url",
            generate_presigned_upload_url,
        )
        params = [
            SkillUploadSessionCreate(
                sha256=hashlib.sha256(content).hexdigest(),
                size_bytes=len(content),
                content_type="application/octet-stream",
            )
            for content in (b"first", b"")
        ]

        prepared = await skill_service.prepare_draft_uploads(
            skill_id=created.id,
            params=params,
            url_expiry_seconds=60,
        )

        assert prepared.created is False
        assert prepared.skill_id == created.id
        assert [item[4] for item in captured] == [60, 60]
        assert [item[3] for item in captured] == [
            base64.b64encode(hashlib.sha256(content).digest()).decode("ascii")
            for content in (b"first", b"")
        ]
        assert [upload.headers["Content-Length"] for upload in prepared.uploads] == [
            "5",
            "0",
        ]
        assert [
            upload.headers["x-amz-checksum-sha256"] for upload in prepared.uploads
        ] == [item[3] for item in captured]
        upload_rows = (
            await skill_service.session.execute(
                select(SkillUploadModel).where(
                    SkillUploadModel.id.in_(
                        [upload.upload_id for upload in prepared.uploads]
                    )
                )
            )
        ).scalars()
        assert len(upload_rows.all()) == 2

    async def test_prepare_new_skill_uploads_rolls_back_presigning_failure(
        self,
        skill_service: SkillService,
        svc_workspace: Workspace,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        call_count = 0

        async def generate_presigned_upload_url(**_kwargs: Any) -> str:
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise RuntimeError("signing failed")
            return "https://uploads.example/first"

        monkeypatch.setattr(
            "tracecat.agent.skill.service.blob.generate_presigned_upload_url",
            generate_presigned_upload_url,
        )
        params = [
            SkillUploadSessionCreate(
                sha256=hashlib.sha256(content).hexdigest(),
                size_bytes=len(content),
                content_type="application/octet-stream",
            )
            for content in (b"first", b"second")
        ]

        with pytest.raises(RuntimeError, match="signing failed"):
            await skill_service.prepare_new_skill_draft_uploads(
                skill_params=SkillCreate(name="atomic-prepare"),
                params=params,
                url_expiry_seconds=60,
            )

        skill = await skill_service.session.scalar(
            select(Skill).where(
                Skill.workspace_id == skill_service.workspace_id,
                Skill.name == "atomic-prepare",
            )
        )
        uploads = (
            await skill_service.session.execute(
                select(SkillUploadModel).where(
                    SkillUploadModel.workspace_id == skill_service.workspace_id
                )
            )
        ).scalars()
        assert skill is None
        assert uploads.all() == []
        # The seed SKILL.md object was written outside the SQL transaction and
        # must not be left behind as an orphan.
        seed_markdown = SkillService._build_default_skill_markdown(
            name="atomic-prepare", description=None
        )
        seed_key = skill_service._storage_key_for(
            hashlib.sha256(seed_markdown.encode("utf-8")).hexdigest()
        )
        assert not await file_exists(
            key=seed_key, bucket=config.TRACECAT__BLOB_STORAGE_BUCKET_SKILLS
        )
        await skill_service.session.refresh(svc_workspace)

    async def test_prepare_new_skill_uploads_rollback_keeps_reused_blob_object(
        self,
        skill_service: SkillService,
        svc_workspace: Workspace,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A rollback must only delete objects it published, never reused ones."""

        existing = await skill_service.create_skill(SkillCreate(name="shared-seed"))
        seed_markdown = SkillService._build_default_skill_markdown(
            name="shared-seed", description=None
        )
        seed_sha256 = hashlib.sha256(seed_markdown.encode("utf-8")).hexdigest()
        seed_key = skill_service._storage_key_for(seed_sha256)
        assert await file_exists(
            key=seed_key, bucket=config.TRACECAT__BLOB_STORAGE_BUCKET_SKILLS
        )

        async def generate_presigned_upload_url(**_kwargs: Any) -> str:
            raise RuntimeError("signing failed")

        monkeypatch.setattr(
            "tracecat.agent.skill.service.blob.generate_presigned_upload_url",
            generate_presigned_upload_url,
        )
        content = b"payload"
        with pytest.raises(RuntimeError, match="signing failed"):
            await skill_service.prepare_new_skill_draft_uploads(
                skill_params=SkillCreate(name="shared-seed"),
                params=[
                    SkillUploadSessionCreate(
                        sha256=hashlib.sha256(content).hexdigest(),
                        size_bytes=len(content),
                        content_type="application/octet-stream",
                    )
                ],
                url_expiry_seconds=60,
            )

        # The second skill reused the committed seed blob, so the rollback must
        # leave both the row and its object for the first skill.
        assert await file_exists(
            key=seed_key, bucket=config.TRACECAT__BLOB_STORAGE_BUCKET_SKILLS
        )
        blob_row = await skill_service.session.scalar(
            select(SkillBlob).where(
                SkillBlob.workspace_id == skill_service.workspace_id,
                SkillBlob.sha256 == seed_sha256,
            )
        )
        assert blob_row is not None
        assert await skill_service.get_skill(existing.id) is not None
        await skill_service.session.refresh(svc_workspace)

    async def test_prepare_draft_uploads_rejects_aggregate_limit_before_writes(
        self,
        skill_service: SkillService,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        created = await skill_service.create_skill(SkillCreate(name="bounded-prepare"))
        monkeypatch.setattr(config, "TRACECAT__MAX_SKILL_TOTAL_SIZE_BYTES", 1)
        params = [
            SkillUploadSessionCreate(
                sha256=hashlib.sha256(content).hexdigest(),
                size_bytes=len(content),
                content_type="application/octet-stream",
            )
            for content in (b"a", b"b")
        ]

        with pytest.raises(TracecatValidationError) as exc_info:
            await skill_service.prepare_draft_uploads(
                skill_id=created.id,
                params=params,
            )

        assert exc_info.value.detail is not None
        assert exc_info.value.detail["code"] == "skill_total_size_limit_exceeded"
        upload_rows = (
            await skill_service.session.execute(
                select(SkillUploadModel).where(
                    SkillUploadModel.workspace_id == skill_service.workspace_id
                )
            )
        ).scalars()
        assert upload_rows.all() == []

    async def test_prepare_draft_uploads_rejects_transfer_file_limit_before_writes(
        self,
        skill_service: SkillService,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        created = await skill_service.create_skill(SkillCreate(name="bounded-transfer"))
        monkeypatch.setattr(config, "TRACECAT__MAX_SKILL_TRANSFER_FILES_COUNT", 1)

        async def unexpected_generate_presigned_upload_url(**_kwargs: Any) -> str:
            raise AssertionError("Presigning must not start")

        monkeypatch.setattr(
            "tracecat.agent.skill.service.blob.generate_presigned_upload_url",
            unexpected_generate_presigned_upload_url,
        )
        params = [
            SkillUploadSessionCreate(
                sha256=hashlib.sha256(content).hexdigest(),
                size_bytes=len(content),
                content_type="application/octet-stream",
            )
            for content in (b"a", b"b")
        ]

        with pytest.raises(TracecatValidationError) as exc_info:
            await skill_service.prepare_draft_uploads(
                skill_id=created.id,
                params=params,
            )

        assert exc_info.value.detail == {
            "code": "skill_transfer_file_count_limit_exceeded",
            "file_count": 2,
            "max_file_count": 1,
        }
        upload_rows = (
            await skill_service.session.execute(
                select(SkillUploadModel).where(
                    SkillUploadModel.skill_id == created.id,
                )
            )
        ).scalars()
        assert upload_rows.all() == []

    @pytest.mark.parametrize(
        ("content_type", "reason"),
        [
            (" ; ", "empty"),
            (";".join(["x"] * 128), "too_long"),
        ],
    )
    async def test_create_draft_upload_rejects_invalid_normalized_content_type(
        self,
        skill_service: SkillService,
        content_type: str,
        reason: str,
    ) -> None:
        """Upload sessions should validate the normalized content type."""

        created = await skill_service.create_skill(
            SkillCreate(name=f"invalid-content-type-{reason.replace('_', '-')}")
        )
        content = b"upload payload"

        with pytest.raises(TracecatValidationError) as exc_info:
            await skill_service.create_draft_upload(
                skill_id=created.id,
                params=SkillUploadSessionCreate(
                    sha256=hashlib.sha256(content).hexdigest(),
                    size_bytes=len(content),
                    content_type=content_type,
                ),
            )

        expected_detail: dict[str, object] = {
            "code": "invalid_content_type",
            "reason": reason,
        }
        if reason == "too_long":
            expected_detail["max_length"] = 255
        assert exc_info.value.detail == expected_detail
        upload_rows = (
            await skill_service.session.execute(
                select(SkillUploadModel).where(
                    SkillUploadModel.workspace_id == skill_service.workspace_id,
                    SkillUploadModel.skill_id == created.id,
                )
            )
        ).scalars()
        assert upload_rows.all() == []

    async def test_create_draft_upload_reaps_expired_incomplete_uploads(
        self,
        skill_service: SkillService,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Creating a new upload session should clean up older expired sessions."""

        created = await skill_service.create_skill(SkillCreate(name="reap-uploads"))
        stale_upload = await skill_service.create_draft_upload(
            skill_id=created.id,
            params=SkillUploadSessionCreate(
                sha256=hashlib.sha256(b"stale upload").hexdigest(),
                size_bytes=len(b"stale upload"),
                content_type="text/plain; charset=utf-8",
            ),
        )
        stale_upload_row = await skill_service.session.scalar(
            select(SkillUploadModel).where(
                SkillUploadModel.id == stale_upload.upload_id
            )
        )
        assert stale_upload_row is not None
        stale_upload_row.expires_at = datetime.now(UTC) - timedelta(minutes=1)
        skill_service.session.add(stale_upload_row)
        await skill_service.session.commit()

        deletion_started = asyncio.Event()
        allow_deletion = asyncio.Event()
        deleted: dict[str, str] = {}

        async def fake_delete_file(
            *, key: str, bucket: str, redact_log_identifiers: bool = False
        ) -> None:
            assert redact_log_identifiers is True
            deletion_started.set()
            await allow_deletion.wait()
            deleted["key"] = key
            deleted["bucket"] = bucket

        monkeypatch.setattr(
            "tracecat.agent.skill.service.blob.delete_file",
            fake_delete_file,
        )

        fresh_upload = await skill_service.create_draft_upload(
            skill_id=created.id,
            params=SkillUploadSessionCreate(
                sha256=hashlib.sha256(b"fresh upload").hexdigest(),
                size_bytes=len(b"fresh upload"),
                content_type="text/plain; charset=utf-8",
            ),
        )

        assert fresh_upload.upload_id != stale_upload.upload_id
        try:
            await asyncio.wait_for(deletion_started.wait(), timeout=1)
            assert deleted == {}
        finally:
            allow_deletion.set()
            await asyncio.gather(*skill_service_module._staged_upload_cleanup_tasks)
        assert deleted == {
            "key": stale_upload.key,
            "bucket": config.TRACECAT__BLOB_STORAGE_BUCKET_SKILLS,
        }
        assert (
            await skill_service.session.scalar(
                select(SkillUploadModel).where(
                    SkillUploadModel.id == stale_upload.upload_id
                )
            )
            is None
        )

    async def test_prepare_new_skill_upload_returns_before_reaped_object_cleanup(
        self,
        skill_service: SkillService,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Slow expired-object maintenance must not delay a committed plan."""

        existing = await skill_service.create_skill(
            SkillCreate(name="stale-upload-owner")
        )
        stale_upload = await skill_service.create_draft_upload(
            skill_id=existing.id,
            params=SkillUploadSessionCreate(
                sha256=hashlib.sha256(b"stale upload").hexdigest(),
                size_bytes=len(b"stale upload"),
                content_type="text/plain; charset=utf-8",
            ),
        )
        stale_upload_row = await skill_service.session.scalar(
            select(SkillUploadModel).where(
                SkillUploadModel.id == stale_upload.upload_id
            )
        )
        assert stale_upload_row is not None
        stale_upload_row.expires_at = datetime.now(UTC) - timedelta(minutes=1)
        skill_service.session.add(stale_upload_row)
        await skill_service.session.commit()

        deletion_started = asyncio.Event()
        allow_deletion = asyncio.Event()

        async def blocked_delete_file(
            *, key: str, bucket: str, redact_log_identifiers: bool = False
        ) -> None:
            del key, bucket
            assert redact_log_identifiers is True
            deletion_started.set()
            await allow_deletion.wait()

        monkeypatch.setattr(
            "tracecat.agent.skill.service.blob.delete_file",
            blocked_delete_file,
        )

        content = b"new upload"
        prepared = await asyncio.wait_for(
            skill_service.prepare_new_skill_draft_uploads(
                skill_params=SkillCreate(name="nonblocking-cleanup"),
                params=[
                    SkillUploadSessionCreate(
                        sha256=hashlib.sha256(content).hexdigest(),
                        size_bytes=len(content),
                        content_type="application/octet-stream",
                    )
                ],
                url_expiry_seconds=60,
            ),
            timeout=5,
        )

        assert prepared.created is True
        assert prepared.skill_id != existing.id
        try:
            await asyncio.wait_for(deletion_started.wait(), timeout=1)
        finally:
            allow_deletion.set()
            await asyncio.gather(*skill_service_module._staged_upload_cleanup_tasks)

    async def test_attach_uploaded_blob_rejects_size_mismatch(
        self,
        skill_service: SkillService,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Uploaded blob finalization validates the actual object length."""

        created = await skill_service.create_skill(SkillCreate(name="size-mismatch"))
        draft = await skill_service.get_draft(created.id)
        assert draft is not None

        content = b"size mismatch payload"
        sha256 = hashlib.sha256(content).hexdigest()
        upload = await skill_service.create_draft_upload(
            skill_id=created.id,
            params=SkillUploadSessionCreate(
                sha256=sha256,
                size_bytes=len(content) - 1,
                content_type="text/plain; charset=utf-8",
            ),
        )
        iterated = False

        async def fake_file_exists(
            *, key: str, bucket: str, redact_log_identifiers: bool = False
        ) -> bool:
            del key, bucket
            assert redact_log_identifiers is True
            return True

        class FakeStream:
            async def read(self) -> bytes:
                return content

            async def iter_chunks(self, *, chunk_size: int):
                nonlocal iterated
                del chunk_size
                iterated = True
                yield content

        @asynccontextmanager
        async def fake_open_download_stream(
            *, key: str, bucket: str, redact_log_identifiers: bool = False
        ):
            del bucket
            assert key == upload.key
            assert redact_log_identifiers is True
            yield FakeStream(), len(content)

        monkeypatch.setattr(
            "tracecat.agent.skill.service.blob.file_exists", fake_file_exists
        )
        monkeypatch.setattr(
            "tracecat.agent.skill.service.blob.open_download_stream",
            fake_open_download_stream,
        )

        with pytest.raises(TracecatValidationError, match="size mismatch"):
            await skill_service.patch_draft(
                skill_id=created.id,
                params=SkillDraftPatch(
                    base_revision=draft.draft_revision,
                    operations=[
                        SkillDraftAttachUploadedBlobOp(
                            path="references/uploaded.txt",
                            upload_id=upload.upload_id,
                        )
                    ],
                ),
            )
        assert iterated is False

    async def test_attach_uploaded_blob_deletes_expired_staged_key(
        self,
        skill_service: SkillService,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Expired staged uploads should be deleted before the validation error returns."""

        created = await skill_service.create_skill(SkillCreate(name="expired-upload"))
        draft = await skill_service.get_draft(created.id)
        assert draft is not None

        upload = await skill_service.create_draft_upload(
            skill_id=created.id,
            params=SkillUploadSessionCreate(
                sha256=hashlib.sha256(b"expired upload").hexdigest(),
                size_bytes=len(b"expired upload"),
                content_type="text/plain; charset=utf-8",
            ),
        )
        upload_row = await skill_service.session.scalar(
            select(SkillUploadModel).where(SkillUploadModel.id == upload.upload_id)
        )
        assert upload_row is not None
        upload_row.expires_at = datetime.now(UTC) - timedelta(minutes=1)
        skill_service.session.add(upload_row)
        await skill_service.session.commit()

        deleted: dict[str, str] = {}

        async def fake_delete_file(
            *, key: str, bucket: str, redact_log_identifiers: bool = False
        ) -> None:
            assert redact_log_identifiers is True
            deleted["key"] = key
            deleted["bucket"] = bucket

        monkeypatch.setattr(
            "tracecat.agent.skill.service.blob.delete_file",
            fake_delete_file,
        )

        with pytest.raises(TracecatValidationError) as exc_info:
            await skill_service.patch_draft(
                skill_id=created.id,
                params=SkillDraftPatch(
                    base_revision=draft.draft_revision,
                    operations=[
                        SkillDraftAttachUploadedBlobOp(
                            path="references/uploaded.txt",
                            upload_id=upload.upload_id,
                        )
                    ],
                ),
            )

        assert exc_info.value.detail == {
            "code": "upload_expired",
            "upload_id": str(upload.upload_id),
        }
        assert deleted == {
            "key": upload.key,
            "bucket": config.TRACECAT__BLOB_STORAGE_BUCKET_SKILLS,
        }

    async def test_attach_uploaded_blob_stops_streaming_after_size_exceeded(
        self,
        skill_service: SkillService,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Uploaded blob finalization stops streaming once size exceeds declared size."""

        created = await skill_service.create_skill(SkillCreate(name="oversized-stream"))
        draft = await skill_service.get_draft(created.id)
        assert draft is not None

        chunks = [b"abc", b"def", b"ghi"]
        content = b"".join(chunks)
        upload = await skill_service.create_draft_upload(
            skill_id=created.id,
            params=SkillUploadSessionCreate(
                sha256=hashlib.sha256(content).hexdigest(),
                size_bytes=5,
                content_type="text/plain; charset=utf-8",
            ),
        )
        chunks_yielded = 0

        async def fake_file_exists(
            *, key: str, bucket: str, redact_log_identifiers: bool = False
        ) -> bool:
            del key, bucket
            assert redact_log_identifiers is True
            return True

        class FakeStream:
            async def iter_chunks(self, *, chunk_size: int):
                nonlocal chunks_yielded
                del chunk_size
                for chunk in chunks:
                    chunks_yielded += 1
                    yield chunk

        @asynccontextmanager
        async def fake_open_download_stream(
            *, key: str, bucket: str, redact_log_identifiers: bool = False
        ):
            del bucket
            assert key == upload.key
            assert redact_log_identifiers is True
            yield FakeStream(), None

        monkeypatch.setattr(
            "tracecat.agent.skill.service.blob.file_exists", fake_file_exists
        )
        monkeypatch.setattr(
            "tracecat.agent.skill.service.blob.open_download_stream",
            fake_open_download_stream,
        )

        with pytest.raises(TracecatValidationError, match="size mismatch"):
            await skill_service.patch_draft(
                skill_id=created.id,
                params=SkillDraftPatch(
                    base_revision=draft.draft_revision,
                    operations=[
                        SkillDraftAttachUploadedBlobOp(
                            path="references/uploaded.txt",
                            upload_id=upload.upload_id,
                        )
                    ],
                ),
            )
        assert chunks_yielded == 2

    async def test_attach_uploaded_blob_deletes_staged_key_after_commit(
        self,
        skill_service: SkillService,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Successful materialization should clean up the staged upload object."""

        created = await skill_service.create_skill(SkillCreate(name="cleanup-upload"))
        draft = await skill_service.get_draft(created.id)
        assert draft is not None

        content = b"cleanup payload"
        sha256 = hashlib.sha256(content).hexdigest()
        upload = await skill_service.create_draft_upload(
            skill_id=created.id,
            params=SkillUploadSessionCreate(
                sha256=sha256,
                size_bytes=len(content),
                content_type="text/plain; charset=utf-8",
            ),
        )
        deleted: dict[str, str] = {}

        async def fake_file_exists(
            *, key: str, bucket: str, redact_log_identifiers: bool = False
        ) -> bool:
            del key, bucket
            assert redact_log_identifiers is True
            return True

        class FakeStream:
            async def read(self) -> bytes:
                return content

            async def iter_chunks(self, *, chunk_size: int):
                del chunk_size
                yield content

        @asynccontextmanager
        async def fake_open_download_stream(
            *, key: str, bucket: str, redact_log_identifiers: bool = False
        ):
            del bucket
            assert redact_log_identifiers is True
            yield FakeStream(), len(content)

        async def fake_copy_file(
            *,
            source_key: str,
            destination_key: str,
            bucket: str,
            content_type: str | None = None,
            redact_log_identifiers: bool = False,
        ) -> None:
            del source_key, destination_key, bucket, content_type
            assert redact_log_identifiers is True

        async def fake_delete_file(
            *, key: str, bucket: str, redact_log_identifiers: bool = False
        ) -> None:
            assert redact_log_identifiers is True
            deleted["key"] = key
            deleted["bucket"] = bucket

        monkeypatch.setattr(
            "tracecat.agent.skill.service.blob.file_exists", fake_file_exists
        )
        monkeypatch.setattr(
            "tracecat.agent.skill.service.blob.open_download_stream",
            fake_open_download_stream,
        )
        monkeypatch.setattr(
            "tracecat.agent.skill.service.blob.copy_file",
            fake_copy_file,
        )
        monkeypatch.setattr(
            "tracecat.agent.skill.service.blob.delete_file",
            fake_delete_file,
        )

        await skill_service.patch_draft(
            skill_id=created.id,
            params=SkillDraftPatch(
                base_revision=draft.draft_revision,
                operations=[
                    SkillDraftAttachUploadedBlobOp(
                        path="references/uploaded.txt",
                        upload_id=upload.upload_id,
                    )
                ],
            ),
        )

        assert deleted == {
            "key": upload.key,
            "bucket": config.TRACECAT__BLOB_STORAGE_BUCKET_SKILLS,
        }

    async def test_attach_uploaded_blob_waits_for_later_op_validation(
        self,
        skill_service: SkillService,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Later invalid ops should fail before upload materialization begins."""

        created = await skill_service.create_skill(SkillCreate(name="defer-attach"))
        draft = await skill_service.get_draft(created.id)
        assert draft is not None

        content = b"deferred payload"
        sha256 = hashlib.sha256(content).hexdigest()
        upload = await skill_service.create_draft_upload(
            skill_id=created.id,
            params=SkillUploadSessionCreate(
                sha256=sha256,
                size_bytes=len(content),
                content_type="text/plain; charset=utf-8",
            ),
        )

        async def unexpected_file_exists(*, key: str, bucket: str) -> bool:
            del key, bucket
            raise AssertionError("Upload materialization should not start yet")

        monkeypatch.setattr(
            "tracecat.agent.skill.service.blob.file_exists",
            unexpected_file_exists,
        )

        with pytest.raises(TracecatValidationError, match="Cannot move missing"):
            await skill_service.patch_draft(
                skill_id=created.id,
                params=SkillDraftPatch(
                    base_revision=draft.draft_revision,
                    operations=[
                        SkillDraftAttachUploadedBlobOp(
                            path="references/uploaded.txt",
                            upload_id=upload.upload_id,
                        ),
                        SkillDraftMoveFileOp(
                            from_path="references/missing.txt",
                            to_path="references/moved.txt",
                        ),
                    ],
                ),
            )

        upload_row = await skill_service.session.scalar(
            select(SkillUploadModel).where(SkillUploadModel.id == upload.upload_id)
        )
        assert upload_row is not None
        assert upload_row.completed_at is None
        assert upload_row.blob_id is None
        assert (
            await skill_service.session.scalar(
                select(SkillBlob).where(
                    SkillBlob.workspace_id == skill_service.workspace_id,
                    SkillBlob.sha256 == sha256,
                )
            )
            is None
        )

    async def test_patch_rejects_final_size_limit_before_upload_materialization(
        self,
        skill_service: SkillService,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        created = await skill_service.create_skill(SkillCreate(name="bounded-patch"))
        draft = await skill_service.get_draft(created.id)
        assert draft is not None

        content = b"bounded payload"
        upload = await skill_service.create_draft_upload(
            skill_id=created.id,
            params=SkillUploadSessionCreate(
                sha256=hashlib.sha256(content).hexdigest(),
                size_bytes=len(content),
                content_type="application/octet-stream",
            ),
        )
        monkeypatch.setattr(
            config,
            "TRACECAT__MAX_SKILL_TOTAL_SIZE_BYTES",
            sum(file.size_bytes for file in draft.files) + len(content) - 1,
        )

        async def unexpected_file_exists(*, key: str, bucket: str) -> bool:
            del key, bucket
            raise AssertionError("Upload materialization must not start")

        monkeypatch.setattr(
            "tracecat.agent.skill.service.blob.file_exists",
            unexpected_file_exists,
        )

        with pytest.raises(TracecatValidationError) as exc_info:
            await skill_service.patch_draft(
                skill_id=created.id,
                params=SkillDraftPatch(
                    base_revision=draft.draft_revision,
                    operations=[
                        SkillDraftAttachUploadedBlobOp(
                            path="references/payload.bin",
                            upload_id=upload.upload_id,
                        )
                    ],
                ),
            )

        assert exc_info.value.detail is not None
        assert exc_info.value.detail["code"] == "skill_total_size_limit_exceeded"

    async def test_patch_rejects_transfer_file_limit_before_upload_lookup(
        self,
        skill_service: SkillService,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        created = await skill_service.create_skill(SkillCreate(name="bounded-complete"))
        draft = await skill_service.get_draft(created.id)
        assert draft is not None
        monkeypatch.setattr(config, "TRACECAT__MAX_SKILL_TRANSFER_FILES_COUNT", 1)

        with pytest.raises(TracecatValidationError) as exc_info:
            await skill_service.patch_draft(
                skill_id=created.id,
                params=SkillDraftPatch(
                    base_revision=draft.draft_revision,
                    operations=[
                        SkillDraftAttachUploadedBlobOp(
                            path="references/first.bin",
                            upload_id=uuid.uuid4(),
                        ),
                        SkillDraftAttachUploadedBlobOp(
                            path="references/second.bin",
                            upload_id=uuid.uuid4(),
                        ),
                    ],
                ),
            )

        assert exc_info.value.detail == {
            "code": "skill_transfer_file_count_limit_exceeded",
            "file_count": 2,
            "max_file_count": 1,
        }

    async def test_prepare_draft_download_enforces_transfer_file_limit(
        self,
        skill_service: SkillService,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A bulk download is capped like uploads: over the limit is rejected."""

        created = await skill_service.upload_skill(
            SkillUpload(
                name="bounded-download",
                files=[
                    SkillUploadFile(
                        path="SKILL.md",
                        content_base64=base64.b64encode(
                            b"---\nname: bounded-download\n---\n\n# Bounded\n"
                        ).decode(),
                        content_type="text/markdown; charset=utf-8",
                    ),
                    SkillUploadFile(
                        path="scripts/helper.py",
                        content_base64=base64.b64encode(b"print('ok')\n").decode(),
                        content_type="text/x-python; charset=utf-8",
                    ),
                ],
            )
        )

        monkeypatch.setattr(config, "TRACECAT__MAX_SKILL_TRANSFER_FILES_COUNT", 1)
        with pytest.raises(TracecatValidationError) as exc_info:
            await skill_service.prepare_draft_download(skill_id=created.id)
        assert exc_info.value.detail == {
            "code": "skill_transfer_file_count_limit_exceeded",
            "file_count": 2,
            "max_file_count": 1,
        }

        monkeypatch.setattr(config, "TRACECAT__MAX_SKILL_TRANSFER_FILES_COUNT", 2)
        prepared = await skill_service.prepare_draft_download(skill_id=created.id)
        assert prepared is not None
        assert len(prepared.files) == 2

    async def test_patch_draft_cancellation_deletes_published_objects(
        self,
        skill_service: SkillService,
        svc_workspace: Workspace,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Cancellation mid-materialization still removes published objects."""

        created = await skill_service.create_skill(SkillCreate(name="cancelled-patch"))
        draft = await skill_service.get_draft(created.id)
        assert draft is not None
        cancelled_object = PublishedBlobObject(
            bucket="skills-bucket", key="skills/cancelled/object"
        )

        async def cancel_after_publishing(
            operations: Sequence[Any],
            *,
            published: list[PublishedBlobObject] | None = None,
        ) -> dict[int, SkillBlob]:
            del operations
            assert published is not None
            published.append(cancelled_object)
            raise asyncio.CancelledError()

        deleted: list[tuple[str, str]] = []

        async def fake_delete_file(
            *, key: str, bucket: str, redact_log_identifiers: bool = False
        ) -> None:
            assert redact_log_identifiers is True
            deleted.append((key, bucket))

        monkeypatch.setattr(
            skill_service,
            "_materialize_patch_operation_blobs",
            cancel_after_publishing,
        )
        monkeypatch.setattr(
            "tracecat.agent.skill.service.blob.delete_file", fake_delete_file
        )

        with pytest.raises(asyncio.CancelledError):
            await skill_service.patch_draft(
                skill_id=created.id,
                params=SkillDraftPatch(
                    base_revision=draft.draft_revision,
                    operations=[
                        SkillDraftUpsertTextFileOp(
                            path="references/note.txt",
                            content="cancelled",
                        )
                    ],
                ),
            )

        assert deleted == [(cancelled_object.key, cancelled_object.bucket)]
        await skill_service.session.refresh(svc_workspace)

    async def test_publish_requires_root_skill_md(
        self,
        skill_service: SkillService,
    ) -> None:
        """Publishing fails when the draft no longer contains root SKILL.md."""

        created = await skill_service.create_skill(SkillCreate(name="invalid-skill"))
        draft = await skill_service.get_draft(created.id)
        assert draft is not None

        await skill_service.patch_draft(
            skill_id=created.id,
            params=SkillDraftPatch(
                base_revision=draft.draft_revision,
                operations=[SkillDraftDeleteFileOp(path="SKILL.md")],
            ),
        )

        with pytest.raises(TracecatValidationError, match="failed validation"):
            await skill_service.publish_skill(created.id)

    async def test_publish_rejects_file_directory_path_collisions(
        self,
        skill_service: SkillService,
    ) -> None:
        """Publishing fails when one manifest path is a parent of another."""

        created = await skill_service.create_skill(SkillCreate(name="path-collision"))
        draft = await skill_service.get_draft(created.id)
        assert draft is not None

        await skill_service.patch_draft(
            skill_id=created.id,
            params=SkillDraftPatch(
                base_revision=draft.draft_revision,
                operations=[
                    SkillDraftUpsertTextFileOp(
                        path="docs",
                        content="plain file",
                        content_type="text/plain; charset=utf-8",
                    ),
                    SkillDraftUpsertTextFileOp(
                        path="docs/readme.md",
                        content="# Readme",
                        content_type="text/markdown; charset=utf-8",
                    ),
                ],
            ),
        )

        updated_draft = await skill_service.get_draft(created.id)

        assert updated_draft is not None
        assert updated_draft.is_publishable is False
        assert [error.code for error in updated_draft.validation_errors] == [
            "path_prefix_collision"
        ]
        assert updated_draft.validation_errors[0].path == "docs/readme.md"

        with pytest.raises(TracecatValidationError, match="failed validation"):
            await skill_service.publish_skill(created.id)

    async def test_publish_skill_version_uses_files_without_rewriting_draft(
        self,
        skill_service: SkillService,
    ) -> None:
        """Workflow publishes should create versions without mutating the draft."""

        created = await skill_service.create_skill(SkillCreate(name="reflect-skill"))
        draft_before = await skill_service.get_draft(created.id)
        assert draft_before is not None

        published = await skill_service.publish_skill_version(
            skill_id=created.id,
            params=SkillVersionPublish(
                files=[
                    SkillUploadFile(
                        path="SKILL.md",
                        content_base64=base64.b64encode(
                            b"---\n"
                            b"name: reflected-skill\n"
                            b"description: Learned from signals\n"
                            b"---\n\n"
                            b"# reflected-skill\n"
                        ).decode(),
                        content_type="text/markdown; charset=utf-8",
                    ),
                    SkillUploadFile(
                        path="signals.md",
                        content_base64=base64.b64encode(
                            b"Escalate correlated endpoint alerts."
                        ).decode(),
                        content_type="text/markdown; charset=utf-8",
                    ),
                ],
            ),
        )
        draft_after = await skill_service.get_draft(created.id)
        version_file = await skill_service.get_version_file(
            skill_id=created.id,
            version_id=published.id,
            path="signals.md",
        )
        skill_read = await skill_service.get_skill_read(created.id)

        assert published.version == 1
        assert published.name == "reflected-skill"
        assert published.description == "Learned from signals"
        assert draft_after is not None
        assert draft_after.draft_revision == draft_before.draft_revision
        assert draft_after.name == draft_before.name
        assert version_file is not None
        assert version_file.kind == "inline"
        assert version_file.text_content == "Escalate correlated endpoint alerts."
        assert skill_read is not None
        assert skill_read.current_version_id == published.id
        assert skill_read.name == "reflected-skill"

    async def test_publish_skill_version_rejects_stale_base_version(
        self,
        skill_service: SkillService,
    ) -> None:
        """Workflow publishes require the caller's observed current version."""

        created = await skill_service.create_skill(SkillCreate(name="conflict-skill"))
        first = await skill_service.publish_skill_version(
            skill_id=created.id,
            params=SkillVersionPublish(
                base_version_id=None,
                files=[
                    SkillUploadFile(
                        path="SKILL.md",
                        content_base64=base64.b64encode(
                            b"---\nname: conflict-one\n---\n\n# conflict-one\n"
                        ).decode(),
                        content_type="text/markdown; charset=utf-8",
                    )
                ],
            ),
        )

        with pytest.raises(TracecatValidationError) as exc_info:
            await skill_service.publish_skill_version(
                skill_id=created.id,
                params=SkillVersionPublish(
                    base_version_id=None,
                    files=[
                        SkillUploadFile(
                            path="SKILL.md",
                            content_base64=base64.b64encode(
                                b"---\nname: conflict-two\n---\n\n# conflict-two\n"
                            ).decode(),
                            content_type="text/markdown; charset=utf-8",
                        )
                    ],
                ),
            )

        assert exc_info.value.detail == {
            "code": "skill_version_conflict",
            "current_version_id": str(first.id),
        }

    async def test_publish_skill_concurrently_allocates_unique_versions(
        self,
        svc_role: Role,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Concurrent publishes serialize on the skill row and keep version numbers unique."""

        role = svc_role.model_copy(update={"workspace_id": uuid.uuid4()}, deep=True)
        concurrent_engine = create_async_engine(TEST_DB_CONFIG.test_url)
        session_factory = async_sessionmaker(
            bind=concurrent_engine,
            expire_on_commit=False,
        )
        original_get_skill_for_update = SkillService._get_skill_for_update
        lock_calls = 0

        async def instrumented_get_skill_for_update(
            self: SkillService, skill_id: uuid.UUID
        ):
            nonlocal lock_calls
            lock_calls += 1
            return await original_get_skill_for_update(self, skill_id)

        monkeypatch.setattr(
            SkillService,
            "_get_skill_for_update",
            instrumented_get_skill_for_update,
        )

        try:
            async with session_factory() as seed_session:
                workspace = await seed_session.scalar(
                    select(Workspace).where(Workspace.id == role.workspace_id)
                )
                if workspace is None:
                    seed_session.add(
                        Workspace(
                            id=role.workspace_id,
                            name="test-workspace",
                            organization_id=role.organization_id,
                        )
                    )
                    await seed_session.commit()

                seed_service = SkillService(
                    session=seed_session,
                    role=role.model_copy(deep=True),
                )
                created = await seed_service.create_skill(
                    SkillCreate(name="concurrent-publish-skill")
                )

            async def publish_once(index: int):
                del index
                async with session_factory() as concurrent_session:
                    service = SkillService(
                        session=concurrent_session,
                        role=role.model_copy(deep=True),
                    )
                    return await service.publish_skill(created.id)

            published_versions = await asyncio.gather(
                publish_once(1),
                publish_once(2),
            )

            assert lock_calls == 2
            assert sorted(version.version for version in published_versions) == [1, 2]

            async with session_factory() as verification_session:
                versions = (
                    (
                        await verification_session.execute(
                            select(SkillVersion.version)
                            .where(SkillVersion.skill_id == created.id)
                            .order_by(SkillVersion.version.asc())
                        )
                    )
                    .scalars()
                    .all()
                )

            assert versions == [1, 2]
        finally:
            await concurrent_engine.dispose()

    async def test_restore_version_publishes_copy_without_replacing_draft(
        self,
        skill_service: SkillService,
    ) -> None:
        """Restoring publishes a copy while leaving the working draft untouched."""

        created = await skill_service.create_skill(SkillCreate(name="snapshot-skill"))
        draft = await skill_service.get_draft(created.id)
        assert draft is not None

        await skill_service.patch_draft(
            skill_id=created.id,
            params=SkillDraftPatch(
                base_revision=draft.draft_revision,
                operations=[
                    SkillDraftUpsertTextFileOp(
                        path="references/guide.md",
                        content="Version one",
                    ),
                    SkillDraftUpsertTextFileOp(
                        path="SKILL.md",
                        content=(
                            "---\n"
                            "name: version-one\n"
                            "description: First published version\n"
                            "---\n\n"
                            "# version-one\n"
                        ),
                        content_type="text/markdown; charset=utf-8",
                    ),
                ],
            ),
        )
        version_one = await skill_service.publish_skill(created.id)

        updated_draft = await skill_service.get_draft(created.id)
        assert updated_draft is not None
        await skill_service.patch_draft(
            skill_id=created.id,
            params=SkillDraftPatch(
                base_revision=updated_draft.draft_revision,
                operations=[
                    SkillDraftUpsertTextFileOp(
                        path="references/guide.md",
                        content="Version two",
                    ),
                    SkillDraftUpsertTextFileOp(
                        path="SKILL.md",
                        content=(
                            "---\n"
                            "name: version-two\n"
                            "description: Second published version\n"
                            "---\n\n"
                            "# version-two\n"
                        ),
                        content_type="text/markdown; charset=utf-8",
                    ),
                ],
            ),
        )
        version_two = await skill_service.publish_skill(created.id)
        current_draft = await skill_service.get_draft(created.id)
        assert current_draft is not None

        restored = await skill_service.restore_version(
            skill_id=created.id,
            version_id=version_one.id,
        )
        restored_draft = await skill_service.get_draft(created.id)
        restored_file = await skill_service.get_draft_file(
            skill_id=created.id,
            path="references/guide.md",
        )

        assert isinstance(restored, SkillReadMinimal)
        assert restored.current_version_id not in {version_one.id, version_two.id}
        assert restored.name == version_one.name
        assert restored.description == version_one.description
        assert restored_draft is not None
        assert restored_draft.draft_revision == current_draft.draft_revision
        assert restored_draft.name == "version-two"
        assert restored_draft.description == "Second published version"
        assert restored_file is not None
        assert restored_file.kind == "inline"
        assert restored_file.text_content == "Version two"
        assert version_two.name == "version-two"

    async def test_skill_rename_and_restore_leave_slug_stable(
        self,
        skill_service: SkillService,
    ) -> None:
        """Changing a skill name through publish or restore does not move slug."""

        created = await skill_service.create_skill(SkillCreate(name="stable-skill"))
        original_slug = created.slug
        draft = await skill_service.get_draft(created.id)
        assert draft is not None

        await skill_service.patch_draft(
            skill_id=created.id,
            params=SkillDraftPatch(
                base_revision=draft.draft_revision,
                operations=[
                    SkillDraftUpsertTextFileOp(
                        path="SKILL.md",
                        content=(
                            "---\n"
                            "name: first-renamed-skill\n"
                            "description: First name\n"
                            "---\n\n"
                            "# First renamed skill\n"
                        ),
                        content_type="text/markdown; charset=utf-8",
                    )
                ],
            ),
        )
        first_version = await skill_service.publish_skill(created.id)

        first_read = await skill_service.get_skill_read(created.id)
        assert first_read is not None
        assert first_read.name == "first-renamed-skill"
        assert first_read.slug == original_slug

        current_draft = await skill_service.get_draft(created.id)
        assert current_draft is not None
        await skill_service.patch_draft(
            skill_id=created.id,
            params=SkillDraftPatch(
                base_revision=current_draft.draft_revision,
                operations=[
                    SkillDraftUpsertTextFileOp(
                        path="SKILL.md",
                        content=(
                            "---\n"
                            "name: second-renamed-skill\n"
                            "description: Second name\n"
                            "---\n\n"
                            "# Second renamed skill\n"
                        ),
                        content_type="text/markdown; charset=utf-8",
                    )
                ],
            ),
        )
        await skill_service.publish_skill(created.id)

        restored = await skill_service.restore_version(
            skill_id=created.id,
            version_id=first_version.id,
        )
        restored_read = await skill_service.get_skill_read(created.id)

        assert restored.name == "first-renamed-skill"
        assert restored_read is not None
        assert restored_read.name == "first-renamed-skill"
        assert restored_read.slug == original_slug

    async def test_skill_read_metadata_tracks_current_version_not_draft(
        self,
        skill_service: SkillService,
    ) -> None:
        """Top-level skill metadata should mirror the current version, not draft edits."""

        created = await skill_service.create_skill(SkillCreate(name="metadata-skill"))
        draft = await skill_service.get_draft(created.id)
        assert draft is not None

        await skill_service.patch_draft(
            skill_id=created.id,
            params=SkillDraftPatch(
                base_revision=draft.draft_revision,
                operations=[
                    SkillDraftUpsertTextFileOp(
                        path="SKILL.md",
                        content=(
                            "---\n"
                            "name: published-title\n"
                            "description: Published description\n"
                            "---\n\n"
                            "# published-title\n"
                        ),
                        content_type="text/markdown; charset=utf-8",
                    )
                ],
            ),
        )
        published = await skill_service.publish_skill(created.id)
        skill_read = await skill_service.get_skill_read(created.id)

        assert skill_read is not None
        assert skill_read.current_version_id == published.id
        assert skill_read.name == "published-title"
        assert skill_read.description == "Published description"
        assert skill_read.current_version is not None
        assert skill_read.current_version.id == published.id
        assert skill_read.current_version.name == "published-title"
        assert skill_read.current_version.description == "Published description"

        published_draft = await skill_service.get_draft(created.id)
        assert published_draft is not None
        await skill_service.patch_draft(
            skill_id=created.id,
            params=SkillDraftPatch(
                base_revision=published_draft.draft_revision,
                operations=[
                    SkillDraftUpsertTextFileOp(
                        path="SKILL.md",
                        content=(
                            "---\n"
                            "name: draft-title\n"
                            "description: Draft-only description\n"
                            "---\n\n"
                            "# draft-title\n"
                        ),
                        content_type="text/markdown; charset=utf-8",
                    )
                ],
            ),
        )

        updated_draft = await skill_service.get_draft(created.id)
        skill_read_after_draft_edit = await skill_service.get_skill_read(created.id)

        assert updated_draft is not None
        assert updated_draft.name == "draft-title"
        assert updated_draft.description == "Draft-only description"
        assert skill_read_after_draft_edit is not None
        assert skill_read_after_draft_edit.current_version_id == published.id
        assert skill_read_after_draft_edit.name == "published-title"
        assert skill_read_after_draft_edit.description == "Published description"
        assert skill_read_after_draft_edit.current_version is not None
        assert skill_read_after_draft_edit.current_version.id == published.id
        assert skill_read_after_draft_edit.current_version.name == "published-title"
        assert (
            skill_read_after_draft_edit.current_version.description
            == "Published description"
        )

    async def test_list_versions_returns_minimal_read_model(
        self,
        skill_service: SkillService,
    ) -> None:
        """Version listings should exclude per-file manifests."""

        created = await skill_service.create_skill(SkillCreate(name="minimal-versions"))
        draft = await skill_service.get_draft(created.id)
        assert draft is not None

        await skill_service.patch_draft(
            skill_id=created.id,
            params=SkillDraftPatch(
                base_revision=draft.draft_revision,
                operations=[
                    SkillDraftUpsertTextFileOp(
                        path="references/guide.md",
                        content="Version one",
                    )
                ],
            ),
        )
        published = await skill_service.publish_skill(created.id)

        versions = await skill_service.list_versions(
            skill_id=created.id,
            params=CursorPaginationParams(limit=10),
        )

        assert len(versions.items) == 1
        listed_version = versions.items[0]
        assert isinstance(listed_version, SkillVersionReadMinimal)
        assert listed_version.id == published.id
        assert listed_version.version == published.version
        assert not hasattr(listed_version, "files")

        detailed_version = await skill_service.get_version_read(
            skill_id=created.id,
            version_id=published.id,
        )
        assert sorted(file.path for file in detailed_version.files) == [
            "SKILL.md",
            "references/guide.md",
        ]
        assert not hasattr(detailed_version.files[0], "content_base64")

        snapshot = await skill_service.get_version_snapshot_read(
            skill_id=created.id,
            version_id=published.id,
        )
        snapshot_files = {file.path: file for file in snapshot.files}
        assert sorted(snapshot_files) == ["SKILL.md", "references/guide.md"]
        assert (
            base64.b64decode(snapshot_files["references/guide.md"].content_base64)
            == b"Version one"
        )
        assert snapshot_files["references/guide.md"].content_type.startswith(
            "text/plain"
        )

    async def test_archived_skill_hides_published_version_reads(
        self,
        skill_service: SkillService,
    ) -> None:
        """Archived skills should not expose published version contents."""

        created = await skill_service.create_skill(SkillCreate(name="archived-version"))
        draft = await skill_service.get_draft(created.id)
        assert draft is not None

        await skill_service.patch_draft(
            skill_id=created.id,
            params=SkillDraftPatch(
                base_revision=draft.draft_revision,
                operations=[
                    SkillDraftUpsertTextFileOp(
                        path="references/guide.md",
                        content="Version one",
                    )
                ],
            ),
        )
        published = await skill_service.publish_skill(created.id)

        version_file = await skill_service.get_version_file(
            skill_id=created.id,
            version_id=published.id,
            path="references/guide.md",
        )
        assert version_file is not None

        detailed_version = await skill_service.get_version_read(
            skill_id=created.id,
            version_id=published.id,
        )
        assert detailed_version.id == published.id

        await skill_service.archive_skill(created.id)

        assert (
            await skill_service.get_version_file(
                skill_id=created.id,
                version_id=published.id,
                path="references/guide.md",
            )
            is None
        )
        with pytest.raises(TracecatNotFoundError, match="Skill version"):
            await skill_service.get_version_read(
                skill_id=created.id,
                version_id=published.id,
            )

    async def test_archived_skill_blocks_mutation_paths(
        self,
        skill_service: SkillService,
    ) -> None:
        """Archived skills should reject draft and version mutations."""

        created = await skill_service.create_skill(SkillCreate(name="archived-mutate"))
        published = await skill_service.publish_skill(created.id)
        draft = await skill_service.get_draft(created.id)
        assert draft is not None

        await skill_service.archive_skill(created.id)

        with pytest.raises(TracecatNotFoundError, match=f"Skill '{created.id}'"):
            await skill_service.patch_draft(
                skill_id=created.id,
                params=SkillDraftPatch(
                    base_revision=draft.draft_revision,
                    operations=[
                        SkillDraftUpsertTextFileOp(
                            path="references/guide.md",
                            content="Updated",
                        )
                    ],
                ),
            )

        with pytest.raises(TracecatNotFoundError, match=f"Skill '{created.id}'"):
            await skill_service.publish_skill(created.id)

        with pytest.raises(TracecatNotFoundError, match=f"Skill '{created.id}'"):
            await skill_service.restore_version(
                skill_id=created.id,
                version_id=published.id,
            )

    async def test_restore_version_locks_skill_row(
        self,
        skill_service: SkillService,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Restoring the current version pointer locks the mutable skill row first."""

        created = await skill_service.create_skill(SkillCreate(name="locked-restore"))
        version = await skill_service.publish_skill(created.id)

        original_get_skill_for_update = SkillService._get_skill_for_update
        lock_calls = 0

        async def instrumented_get_skill_for_update(
            self: SkillService, skill_id: uuid.UUID
        ):
            nonlocal lock_calls
            lock_calls += 1
            return await original_get_skill_for_update(self, skill_id)

        monkeypatch.setattr(
            SkillService,
            "_get_skill_for_update",
            instrumented_get_skill_for_update,
        )

        await skill_service.restore_version(skill_id=created.id, version_id=version.id)

        assert lock_calls == 1

    async def test_archive_blocks_when_preset_head_references_skill(
        self,
        session: AsyncSession,
        svc_role: Role,
        skill_service: SkillService,
    ) -> None:
        """Archiving is blocked while a preset head still binds the skill."""

        created = await skill_service.create_skill(SkillCreate(name="bound-skill"))
        await skill_service.publish_skill(created.id)

        preset_service = AgentPresetService(session=session, role=svc_role)
        preset = await preset_service.create_preset(
            AgentPresetCreate(
                name="Bound preset",
                description="Preset with skill",
                instructions="Use the skill",
                model_name="gpt-4o-mini",
                model_provider="openai",
                skills=[
                    AgentPresetSkillBindingBase(
                        skill_id=created.id,
                    )
                ],
            )
        )

        assert preset.current_version_id is not None
        with pytest.raises(
            TracecatValidationError, match="still referenced by agent presets"
        ):
            await skill_service.archive_skill(created.id)

    async def test_archive_with_unlink_option_publishes_parent(
        self,
        session: AsyncSession,
        svc_role: Role,
        skill_service: SkillService,
    ) -> None:
        """Explicit unlinking publishes parent membership removal."""

        created = await skill_service.create_skill(SkillCreate(name="unlink-skill"))
        await skill_service.publish_skill(created.id)
        preset_service = AgentPresetService(session=session, role=svc_role)
        preset = await preset_service.create_preset(
            AgentPresetCreate(
                name="Unlink preset",
                description="Preset with a removable skill",
                instructions="Use the skill",
                model_name="gpt-4o-mini",
                model_provider="openai",
                skills=[AgentPresetSkillBindingBase(skill_id=created.id)],
            )
        )
        original_version_id = preset.current_version_id

        await skill_service.archive_skill(created.id, unlink_from_presets=True)

        refreshed = await preset_service.get_preset(preset.id)
        assert refreshed is not None
        assert refreshed.current_version_id != original_version_id
        assert await preset_service._list_head_skill_bindings(preset.id) == []
        assert await skill_service.get_skill(created.id) is None

    async def test_archive_allows_when_only_preset_history_references_skill(
        self,
        session: AsyncSession,
        svc_role: Role,
        skill_service: SkillService,
    ) -> None:
        """Archiving only cares about active preset-head skill bindings."""

        created = await skill_service.create_skill(SkillCreate(name="history-skill"))
        await skill_service.publish_skill(created.id)

        preset_service = AgentPresetService(session=session, role=svc_role)
        preset = await preset_service.create_preset(
            AgentPresetCreate(
                name="Historical preset",
                description="Preset with historical skill use",
                instructions="Use the skill",
                model_name="gpt-4o-mini",
                model_provider="openai",
                skills=[
                    AgentPresetSkillBindingBase(
                        skill_id=created.id,
                    )
                ],
            )
        )
        await preset_service.update_preset(preset, AgentPresetUpdate(skills=None))

        await skill_service.archive_skill(created.id)

        archived = await skill_service.get_skill(created.id, include_archived=True)
        assert archived is not None
        assert archived.archived_at is not None
        assert archived.deleted_at == archived.archived_at

    async def test_archive_allows_when_only_soft_deleted_preset_references_skill(
        self,
        session: AsyncSession,
        svc_role: Role,
        skill_service: SkillService,
    ) -> None:
        """Soft-deleted preset heads should not count as active skill usage."""

        created = await skill_service.create_skill(
            SkillCreate(name="soft-deleted-preset")
        )
        await skill_service.publish_skill(created.id)

        preset_service = AgentPresetService(session=session, role=svc_role)
        preset = await preset_service.create_preset(
            AgentPresetCreate(
                name="Soft-deleted preset",
                description="Preset soft-deleted with skill use",
                instructions="Use the skill",
                model_name="gpt-4o-mini",
                model_provider="openai",
                skills=[
                    AgentPresetSkillBindingBase(
                        skill_id=created.id,
                    )
                ],
            )
        )

        await preset_service.delete_preset(preset)
        await skill_service.archive_skill(created.id)

        archived = await skill_service.get_skill(created.id, include_archived=True)
        assert archived is not None
        assert archived.archived_at is not None
        assert archived.deleted_at == archived.archived_at

    async def test_archive_skill_dual_writes_archive_and_delete_timestamps(
        self,
        skill_service: SkillService,
    ) -> None:
        """Archiving a skill writes both legacy and canonical tombstone columns."""

        created = await skill_service.create_skill(SkillCreate(name="dual-write"))

        await skill_service.archive_skill(created.id)

        archived = await skill_service.get_skill(created.id, include_archived=True)
        assert archived is not None
        assert archived.archived_at is not None
        assert archived.deleted_at == archived.archived_at

    async def test_legacy_archived_skill_is_hidden_from_active_lookups(
        self,
        session: AsyncSession,
        skill_service: SkillService,
    ) -> None:
        """Legacy archived-only skills are excluded from every active read path."""

        created = await skill_service.create_skill(SkillCreate(name="legacy-archive"))
        active = await skill_service.create_skill(SkillCreate(name="still-active"))

        await _legacy_archive_skill(session, created.id)

        listing = await skill_service.list_skills(CursorPaginationParams(limit=10))

        assert [skill.id for skill in listing.items] == [active.id]
        assert await skill_service.get_skill(created.id) is None
        assert await skill_service.get_skill_by_identifier(created.id) is None
        assert await skill_service.get_skill_by_identifier(created.slug) is None
        assert await skill_service.get_skill_read(created.id) is None

    async def test_legacy_archived_skill_binding_and_resolution_treat_as_archived(
        self,
        session: AsyncSession,
        svc_role: Role,
        skill_service: SkillService,
    ) -> None:
        """Legacy archived-only skills remain unbindable and resolve as archived."""

        created = await skill_service.create_skill(
            SkillCreate(name="legacy-resolution")
        )
        await skill_service.publish_skill(created.id)
        preset_service = AgentPresetService(session=session, role=svc_role)
        preset = await preset_service.create_preset(
            AgentPresetCreate(
                name="Legacy archived preset",
                description="Preset with a legacy archived skill",
                instructions="Use the selected skill",
                model_name="gpt-4o-mini",
                model_provider="openai",
                skills=[
                    AgentPresetSkillBindingBase(
                        skill_id=created.id,
                    )
                ],
            )
        )
        assert preset.current_version_id is not None

        await _legacy_archive_skill(session, created.id)

        with pytest.raises(TracecatValidationError) as bind_exc_info:
            await skill_service.validate_binding_inputs(
                [
                    AgentPresetSkillBindingBase(
                        skill_id=created.id,
                    )
                ]
            )
        bind_detail = bind_exc_info.value.detail
        assert bind_detail is not None
        assert bind_detail["code"] == "skill_not_found"

        for use_latest_versions in (False, True):
            with pytest.raises(TracecatValidationError) as resolve_exc_info:
                await skill_service.get_resolved_skill_refs_for_preset_version(
                    preset.current_version_id,
                    use_latest_versions=use_latest_versions,
                )
            resolve_detail = resolve_exc_info.value.detail
            assert resolve_detail is not None
            assert resolve_detail["code"] == "skill_archived"
            assert str(created.id) in str(resolve_detail["skills"])

    async def test_legacy_archived_skill_api_projection_reports_deleted_at(
        self,
        session: AsyncSession,
        skill_service: SkillService,
    ) -> None:
        """Legacy archived-only API projections expose a deleted_at tombstone."""

        created = await skill_service.create_skill(SkillCreate(name="legacy-api-read"))
        legacy_archived_at = await _legacy_archive_skill(session, created.id)

        archived = await skill_service.get_skill(created.id, include_archived=True)
        assert archived is not None
        assert archived.archived_at == legacy_archived_at
        assert archived.deleted_at is None

        full_read = await skill_service._build_skill_read(archived)
        minimal_read = skill_service._build_skill_read_minimal(archived)

        assert full_read.deleted_at == legacy_archived_at
        assert minimal_read.deleted_at == legacy_archived_at

    async def test_archive_skill_delegates_dependency_coordination(
        self,
        skill_service: SkillService,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Archiving delegates cross-resource locking and unlink publication."""

        created = await skill_service.create_skill(SkillCreate(name="locked-archive"))

        original_unlink = AgentDependencyService.unlink_skill_from_active_presets
        coordination_calls = 0

        async def instrumented_unlink(
            self: AgentDependencyService,
            skill_id: uuid.UUID,
            *,
            unlink_from_presets: bool,
        ) -> Skill:
            nonlocal coordination_calls
            coordination_calls += 1
            return await original_unlink(
                self,
                skill_id,
                unlink_from_presets=unlink_from_presets,
            )

        monkeypatch.setattr(
            AgentDependencyService,
            "unlink_skill_from_active_presets",
            instrumented_unlink,
        )

        await skill_service.archive_skill(created.id)
        archived = await skill_service.get_skill(created.id, include_archived=True)

        assert coordination_calls == 1
        assert archived is not None
        assert archived.archived_at is not None
        assert archived.deleted_at == archived.archived_at

    async def test_unknown_frontmatter_tool_fails_draft_save(
        self,
        skill_service: SkillService,
    ) -> None:
        """Raw SKILL.md writes reject unknown tool IDs with structured detail."""

        created = await skill_service.create_skill(
            SkillCreate(name="unknown-tool-skill")
        )
        draft = await skill_service.get_draft(created.id)
        assert draft is not None

        with pytest.raises(TracecatValidationError) as exc_info:
            await skill_service.patch_draft(
                skill_id=created.id,
                params=SkillDraftPatch(
                    base_revision=draft.draft_revision,
                    operations=[
                        SkillDraftUpsertTextFileOp(
                            path="SKILL.md",
                            content="""---
name: unknown-tool-skill
metadata:
  tools:
    - core.missing.action
---
""",
                            content_type="text/markdown; charset=utf-8",
                        )
                    ],
                ),
            )

        assert exc_info.value.detail is not None
        assert exc_info.value.detail["code"] == "skill_draft_tool_validation_failed"
        assert exc_info.value.detail["errors"][0]["code"] == "unknown_skill_tools"

    async def test_dispatch_treats_absent_projection_rows_as_empty(
        self,
        session: AsyncSession,
        svc_role: Role,
        skill_service: SkillService,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Versions without projection rows grant no tools without reparsing."""

        created = await skill_service.create_skill(
            SkillCreate(name="legacy-projection-skill")
        )
        await skill_service.publish_skill(created.id)

        preset_service = AgentPresetService(session=session, role=svc_role)
        preset = await preset_service.create_preset(
            AgentPresetCreate(
                name="Legacy projection preset",
                instructions="Use the legacy skill",
                model_name="gpt-4o-mini",
                model_provider="openai",
                skills=[AgentPresetSkillBindingBase(skill_id=created.id)],
            )
        )
        preset_version = await preset_service.get_current_version_for_preset(preset)

        download_file = AsyncMock(side_effect=AssertionError("unexpected legacy read"))
        monkeypatch.setattr(skill_service_module.blob, "download_file", download_file)
        resolved_skills = (
            await preset_service.skills.get_resolved_skill_refs_for_preset_version(
                preset_version.id
            )
        )
        grants = await preset_service.skill_tools.compile_tool_grants(
            preset_version_id=preset_version.id,
            resolved_skills=resolved_skills,
        )

        assert grants.registry_tool_ids == ()
        assert grants.mcp_grants == ()
        download_file.assert_not_awaited()

    async def test_registry_tool_projection_follows_selected_resolution_mode(
        self,
        session: AsyncSession,
        svc_role: Role,
        skill_service: SkillService,
    ) -> None:
        """Fresh runs follow heads while immutable snapshots retain pinned grants."""

        repository = RegistryRepository(
            organization_id=svc_role.organization_id,
            origin="skill-tool-test",
        )
        session.add(repository)
        await session.flush()
        manifest = RegistryVersionManifest(
            actions={
                "core.http_request": RegistryVersionManifestAction(
                    namespace="core",
                    name="http_request",
                    action_type=cast(RegistryActionType, "template"),
                    description="Synthetic HTTP action",
                    interface={"expects": {}, "returns": {}},
                    implementation={"type": "template"},
                )
            }
        )
        registry_version = RegistryVersion(
            organization_id=svc_role.organization_id,
            repository_id=repository.id,
            version="skill-tool-test-v1",
            manifest=manifest.model_dump(mode="json"),
            tarball_uri="s3://synthetic/registry.tar.gz",
        )
        session.add(registry_version)
        await session.flush()
        repository.current_version_id = registry_version.id
        session.add(
            RegistryIndex(
                organization_id=svc_role.organization_id,
                registry_version_id=registry_version.id,
                namespace="core",
                name="http_request",
                action_type="template",
                description="Synthetic HTTP action",
                options={"include_in_schema": True},
            )
        )
        await session.commit()

        created = await skill_service.create_skill(
            SkillCreate(name="registry-tool-skill")
        )
        draft = await skill_service.get_draft(created.id)
        assert draft is not None
        await skill_service.patch_draft(
            skill_id=created.id,
            params=SkillDraftPatch(
                base_revision=draft.draft_revision,
                operations=[
                    SkillDraftUpsertTextFileOp(
                        path="SKILL.md",
                        content="""---
name: registry-tool-skill
metadata:
  tools:
    - core.http_request
---
""",
                        content_type="text/markdown; charset=utf-8",
                    )
                ],
            ),
        )
        first_version = await skill_service.publish_skill(created.id)

        projection = (
            await session.execute(
                select(SkillVersionTool).where(
                    SkillVersionTool.skill_version_id == first_version.id
                )
            )
        ).scalar_one()
        assert projection.tool_id == "core.http_request"

        preset_service = AgentPresetService(session=session, role=svc_role)
        preset = await preset_service.create_preset(
            AgentPresetCreate(
                name="Projected tool preset",
                instructions="Use the skill",
                model_name="gpt-4o-mini",
                model_provider="openai",
                skills=[AgentPresetSkillBindingBase(skill_id=created.id)],
            )
        )
        pinned_preset_version = await preset_service.get_current_version_for_preset(
            preset
        )

        next_draft = await skill_service.get_draft(created.id)
        assert next_draft is not None
        await skill_service.patch_draft(
            skill_id=created.id,
            params=SkillDraftPatch(
                base_revision=next_draft.draft_revision,
                operations=[
                    SkillDraftUpsertTextFileOp(
                        path="SKILL.md",
                        content="""---
name: registry-tool-skill
metadata:
  tools: []
---
""",
                        content_type="text/markdown; charset=utf-8",
                    )
                ],
            ),
        )
        await skill_service.publish_skill(created.id)

        latest_config = await preset_service._version_to_agent_config(
            pinned_preset_version
        )
        assert latest_config.actions is None

        pinned_config = await preset_service._version_to_agent_config(
            pinned_preset_version,
            resolve_dependencies_from_heads=False,
        )
        assert pinned_config.actions == ["core.http_request"]

        await preset_service.update_preset(
            preset,
            AgentPresetUpdate(skills=[]),
        )
        detached_version = await preset_service.get_current_version_for_preset(preset)
        detached_config = await preset_service._version_to_agent_config(
            detached_version
        )
        assert detached_config.actions is None

    async def test_mcp_deletion_ignores_live_skill_without_projection_rows(
        self,
        session: AsyncSession,
        svc_role: Role,
        skill_service: SkillService,
    ) -> None:
        """A current skill without declared tools does not block MCP deletion."""

        integration = MCPIntegration(
            workspace_id=skill_service.workspace_id,
            name="Unreferenced MCP",
            slug=f"unreferenced-mcp-{uuid.uuid4().hex}",
            server_type="http",
            server_uri="https://mcp.example.test",
            auth_type=MCPAuthType.NONE,
            tools=[],
        )
        session.add(integration)
        await session.commit()

        skill = await skill_service.create_skill(SkillCreate(name="no-tool-skill"))
        await skill_service.publish_skill(skill.id)

        integration_service = IntegrationService(session=session, role=svc_role)
        deleted = await integration_service.delete_mcp_integration(
            mcp_integration_id=integration.id
        )

        assert deleted is True

    async def test_mcp_projection_filters_tools_and_blocks_live_deletion(
        self,
        session: AsyncSession,
        svc_role: Role,
        skill_service: SkillService,
    ) -> None:
        """Specific MCP grants are projected, filtered, and deletion-protected."""

        integration = MCPIntegration(
            workspace_id=skill_service.workspace_id,
            name="Synthetic MCP",
            slug=f"synthetic-mcp-{uuid.uuid4().hex}",
            server_type="http",
            server_uri="https://mcp.example.test",
            auth_type=MCPAuthType.NONE,
            tools=[
                {
                    "name": "allowed_tool",
                    "description": "Allowed by the skill",
                    "enabled": True,
                    "status": "available",
                },
                {
                    "name": "other_tool",
                    "description": "Not allowed by the skill",
                    "enabled": True,
                    "status": "available",
                },
            ],
        )
        session.add(integration)
        await session.commit()

        created = await skill_service.create_skill(SkillCreate(name="mcp-tool-skill"))
        draft = await skill_service.get_draft(created.id)
        assert draft is not None
        await skill_service.patch_draft(
            skill_id=created.id,
            params=SkillDraftPatch(
                base_revision=draft.draft_revision,
                operations=[
                    SkillDraftUpsertTextFileOp(
                        path="SKILL.md",
                        content=f"""---
name: mcp-tool-skill
metadata:
  tools:
    - mcp.{integration.slug}.allowed_tool
---
""",
                        content_type="text/markdown; charset=utf-8",
                    )
                ],
            ),
        )
        skill_version = await skill_service.publish_skill(created.id)
        projection = (
            await session.execute(
                select(SkillVersionMcpTool).where(
                    SkillVersionMcpTool.skill_version_id == skill_version.id
                )
            )
        ).scalar_one()
        assert projection.mcp_integration_id == integration.id
        assert projection.tool_name == "allowed_tool"

        preset_service = AgentPresetService(session=session, role=svc_role)
        preset = await preset_service.create_preset(
            AgentPresetCreate(
                name="MCP skill preset",
                instructions="Use the MCP skill",
                model_name="gpt-4o-mini",
                model_provider="openai",
                skills=[AgentPresetSkillBindingBase(skill_id=created.id)],
            )
        )
        preset_version = await preset_service.get_current_version_for_preset(preset)
        config = await preset_service._version_to_agent_config(preset_version)
        assert config.mcp_servers is not None
        assert len(config.mcp_servers) == 1
        assert config.mcp_servers[0].get("tools") == [
            {
                "name": "allowed_tool",
                "description": "Allowed by the skill",
                "enabled": True,
                "requires_approval": False,
                "status": "available",
            }
        ]

        integration_service = IntegrationService(session=session, role=svc_role)
        with pytest.raises(TracecatValidationError) as exc_info:
            await integration_service.delete_mcp_integration(
                mcp_integration_id=integration.id
            )
        assert exc_info.value.detail is not None
        assert exc_info.value.detail["code"] == "mcp_integration_referenced_by_skill"
