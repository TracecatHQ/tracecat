"""Tests for SkillService."""

import asyncio
import base64
import hashlib
import os
import uuid
from contextlib import asynccontextmanager

import pytest
from dotenv import dotenv_values
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests.database import TEST_DB_CONFIG
from tracecat import config
from tracecat.agent.preset.schemas import (
    AgentPresetCreate,
    AgentPresetSkillBindingBase,
)
from tracecat.agent.preset.service import AgentPresetService
from tracecat.agent.skill.schemas import (
    SkillCreate,
    SkillDraftAttachUploadedBlobOp,
    SkillDraftDeleteFileOp,
    SkillDraftPatch,
    SkillDraftRead,
    SkillDraftUpsertTextFileOp,
    SkillReadMinimal,
    SkillUpload,
    SkillUploadFile,
    SkillUploadSessionCreate,
    SkillVersionReadMinimal,
)
from tracecat.agent.skill.service import SkillService
from tracecat.auth.types import Role
from tracecat.db.models import SkillBlob, SkillVersion, Workspace
from tracecat.exceptions import TracecatValidationError
from tracecat.pagination import CursorPaginationParams
from tracecat.storage.blob import ensure_bucket_exists

pytestmark = pytest.mark.usefixtures("db")


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
        "http://localhost:9000",
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


@pytest.mark.anyio
class TestSkillService:
    async def test_insert_blob_row_reuses_existing_blob_identity(
        self,
        skill_service: SkillService,
    ) -> None:
        """Concurrent blob inserts should reuse the canonical row on conflict."""

        content = b"shared blob content"
        sha256 = hashlib.sha256(content).hexdigest()
        storage_key = skill_service._storage_key_for(sha256)

        original = await skill_service._insert_blob_row(
            sha256=sha256,
            bucket=config.TRACECAT__BLOB_STORAGE_BUCKET_SKILLS,
            key=storage_key,
            size_bytes=len(content),
        )
        reused = await skill_service._insert_blob_row(
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

        assert reused.id == original.id
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

    async def test_attach_uploaded_blob_promotes_from_staged_key(
        self,
        skill_service: SkillService,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Uppercase upload digests normalize before staged-key promotion."""

        created = await skill_service.create_skill(SkillCreate(name="staged-upload"))
        draft = await skill_service.get_draft(created.id)
        assert draft is not None

        content = b"uploaded content"
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
        assert "/uploads/" in upload.key
        assert upload.key.endswith(sha256)

        uploaded: dict[str, str] = {}

        async def fake_file_exists(*, key: str, bucket: str) -> bool:
            del key, bucket
            return True

        class FakeStream:
            async def read(self) -> bytes:
                return content

            async def iter_chunks(self, *, chunk_size: int):
                del chunk_size
                yield content

        @asynccontextmanager
        async def fake_open_download_stream(*, key: str, bucket: str):
            del key, bucket
            yield FakeStream(), len(content)

        async def fake_copy_file(
            *,
            source_key: str,
            destination_key: str,
            bucket: str,
            content_type: str | None = None,
        ) -> None:
            del source_key, bucket, content_type
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

        async def fake_file_exists(*, key: str, bucket: str) -> bool:
            del key, bucket
            return True

        class FakeStream:
            async def read(self) -> bytes:
                return content

            async def iter_chunks(self, *, chunk_size: int):
                del chunk_size
                yield content

        @asynccontextmanager
        async def fake_open_download_stream(*, key: str, bucket: str):
            del key, bucket
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

        async def fake_file_exists(*, key: str, bucket: str) -> bool:
            del key, bucket
            return True

        class FakeStream:
            async def read(self) -> bytes:
                return content

            async def iter_chunks(self, *, chunk_size: int):
                del chunk_size
                yield content

        @asynccontextmanager
        async def fake_open_download_stream(*, key: str, bucket: str):
            del key, bucket
            yield FakeStream(), len(content)

        async def fake_copy_file(
            *,
            source_key: str,
            destination_key: str,
            bucket: str,
            content_type: str | None = None,
        ) -> None:
            del source_key, destination_key, bucket, content_type

        async def fake_delete_file(*, key: str, bucket: str) -> None:
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

    async def test_restore_version_updates_current_version_without_replacing_draft(
        self,
        skill_service: SkillService,
    ) -> None:
        """Restoring a version should move the head pointer without rewriting draft files."""

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
        assert restored.current_version_id == version_one.id
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
        skill_version = await skill_service.publish_skill(created.id)

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
                        skill_version_id=skill_version.id,
                    )
                ],
            )
        )

        assert preset.current_version_id is not None
        with pytest.raises(TracecatValidationError, match="still attached to a preset"):
            await skill_service.archive_skill(created.id)

    async def test_archive_skill_locks_skill_row(
        self,
        skill_service: SkillService,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Archiving a skill locks the row before checking preset bindings."""

        created = await skill_service.create_skill(SkillCreate(name="locked-archive"))

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

        await skill_service.archive_skill(created.id)
        archived = await skill_service.get_skill(created.id)

        assert lock_calls == 1
        assert archived is not None
        assert archived.archived_at is not None
