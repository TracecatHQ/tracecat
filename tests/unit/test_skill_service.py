"""Tests for SkillService."""

import asyncio
import base64
import hashlib
import os
import uuid

import pytest
from dotenv import dotenv_values
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
    SkillUpload,
    SkillUploadFile,
    SkillUploadSessionCreate,
)
from tracecat.agent.skill.service import SkillService
from tracecat.auth.types import Role
from tracecat.db.models import SkillBlob, SkillVersion, Workspace
from tracecat.exceptions import TracecatValidationError
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
    async def test_create_skill_seeds_default_draft(
        self,
        skill_service: SkillService,
    ) -> None:
        """Creating a skill seeds a publishable draft with root SKILL.md."""

        created = await skill_service.create_skill(
            SkillCreate(
                slug="triage-skill",
                title="Triage skill",
                description="Handle security triage",
            )
        )

        assert created.slug == "triage-skill"
        assert created.draft_revision == 1
        assert created.is_draft_publishable is True
        assert created.draft_file_count == 1

        draft = await skill_service.get_draft(created.id)
        assert draft is not None
        assert draft.title == "Triage skill"
        assert draft.description == "Handle security triage"
        assert [file.path for file in draft.files] == ["SKILL.md"]

    async def test_create_skill_slugifies_to_kebab_case(
        self,
        skill_service: SkillService,
    ) -> None:
        """Creating a skill stores the slug in canonical kebab-case."""

        created = await skill_service.create_skill(
            SkillCreate(slug="  Triage Skill 2026  ")
        )

        assert created.slug == "triage-skill-2026"

    async def test_upload_skill_conflict_raises_validation_error(
        self,
        skill_service: SkillService,
    ) -> None:
        """Uploading a duplicate slug fails with a conflict-style validation error."""

        await skill_service.create_skill(SkillCreate(slug="duplicate-skill"))

        with pytest.raises(TracecatValidationError, match="already in use"):
            await skill_service.upload_skill(
                SkillUpload(
                    slug="duplicate-skill",
                    files=[
                        SkillUploadFile(
                            path="SKILL.md",
                            content_base64=base64.b64encode(b"# Duplicate").decode(),
                            content_type="text/markdown; charset=utf-8",
                        )
                    ],
                )
            )

    async def test_upload_skill_preserves_distinct_blob_content_types(
        self,
        skill_service: SkillService,
    ) -> None:
        """Same bytes can back separate skill blobs when MIME types differ."""

        shared_bytes = b"shared payload"
        encoded = base64.b64encode(shared_bytes).decode()

        created = await skill_service.upload_skill(
            SkillUpload(
                slug="content-type-skill",
                files=[
                    SkillUploadFile(
                        path="SKILL.md",
                        content_base64=base64.b64encode(b"# Skill").decode(),
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
        assert sorted(blob_row.content_type for blob_row in blob_rows) == [
            "application/octet-stream",
            "text/plain; charset=utf-8",
        ]

    async def test_patch_draft_enforces_revision(
        self,
        skill_service: SkillService,
    ) -> None:
        """Draft mutations require the current draft revision."""

        created = await skill_service.create_skill(SkillCreate(slug="revision-skill"))

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
            SkillCreate(slug="invalid-path-skill")
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
                    SkillCreate(slug="concurrent-draft-skill")
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
        """Staged uploads use a temporary key until validated and promoted."""

        created = await skill_service.create_skill(SkillCreate(slug="staged-upload"))
        draft = await skill_service.get_draft(created.id)
        assert draft is not None

        content = b"uploaded content"
        sha256 = hashlib.sha256(content).hexdigest()
        upload = await skill_service.create_draft_upload(
            skill_id=created.id,
            params=SkillUploadSessionCreate(
                sha256=sha256,
                size_bytes=len(content),
                content_type="text/plain; charset=utf-8",
            ),
        )

        canonical_key = skill_service._storage_key_for(sha256)
        assert upload.key != canonical_key
        assert "/uploads/" in upload.key

        uploaded: dict[str, str] = {}

        async def fake_file_exists(*, key: str, bucket: str) -> bool:
            del key, bucket
            return True

        async def fake_download_file(*, key: str, bucket: str) -> bytes:
            del key, bucket
            return content

        async def fake_upload_file(
            *, content: bytes, key: str, bucket: str, content_type: str | None = None
        ) -> None:
            del content, bucket, content_type
            uploaded["key"] = key

        monkeypatch.setattr(
            "tracecat.agent.skill.service.blob.file_exists", fake_file_exists
        )
        monkeypatch.setattr(
            "tracecat.agent.skill.service.blob.download_file", fake_download_file
        )
        monkeypatch.setattr(
            "tracecat.agent.skill.service.blob.upload_file", fake_upload_file
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

    async def test_attach_uploaded_blob_rejects_size_mismatch(
        self,
        skill_service: SkillService,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Uploaded blob finalization validates the actual object length."""

        created = await skill_service.create_skill(SkillCreate(slug="size-mismatch"))
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

        async def fake_download_file(*, key: str, bucket: str) -> bytes:
            del key, bucket
            return content

        monkeypatch.setattr(
            "tracecat.agent.skill.service.blob.file_exists", fake_file_exists
        )
        monkeypatch.setattr(
            "tracecat.agent.skill.service.blob.download_file", fake_download_file
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

    async def test_publish_requires_root_skill_md(
        self,
        skill_service: SkillService,
    ) -> None:
        """Publishing fails when the draft no longer contains root SKILL.md."""

        created = await skill_service.create_skill(SkillCreate(slug="invalid-skill"))
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
                    SkillCreate(slug="concurrent-publish-skill")
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

    async def test_publish_snapshots_and_restore_replaces_draft(
        self,
        skill_service: SkillService,
    ) -> None:
        """Published versions remain immutable and restore replaces the draft."""

        created = await skill_service.create_skill(SkillCreate(slug="snapshot-skill"))
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
                    )
                ],
            ),
        )
        await skill_service.publish_skill(created.id)

        restored = await skill_service.restore_version(
            skill_id=created.id,
            version_id=version_one.id,
        )
        restored_file = await skill_service.get_draft_file(
            skill_id=created.id,
            path="references/guide.md",
        )

        assert restored.draft_revision > updated_draft.draft_revision
        assert restored_file is not None
        assert restored_file.kind == "inline"
        assert restored_file.text_content == "Version one"

    async def test_restore_version_locks_skill_row(
        self,
        skill_service: SkillService,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Restoring a draft snapshot locks the mutable skill row first."""

        created = await skill_service.create_skill(SkillCreate(slug="locked-restore"))
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

        created = await skill_service.create_skill(SkillCreate(slug="bound-skill"))
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

        created = await skill_service.create_skill(SkillCreate(slug="locked-archive"))

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
