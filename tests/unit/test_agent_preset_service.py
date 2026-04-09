"""Tests for AgentPresetService."""

import asyncio
import os
import uuid
from typing import cast

import pytest
from dotenv import dotenv_values
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests.database import TEST_DB_CONFIG
from tracecat import config
from tracecat.agent.preset.schemas import (
    AgentPresetCreate,
    AgentPresetSkillBindingBase,
    AgentPresetUpdate,
)
from tracecat.agent.preset.service import AgentPresetService
from tracecat.agent.skill.schemas import (
    SkillCreate,
    SkillDraftPatch,
    SkillDraftUpsertTextFileOp,
)
from tracecat.agent.skill.service import SkillService
from tracecat.agent.types import AgentConfig
from tracecat.auth.types import Role
from tracecat.db.models import (
    AgentPreset,
    AgentPresetVersion,
    RegistryAction,
    RegistryIndex,
    RegistryRepository,
    RegistryVersion,
    Workspace,
)
from tracecat.exceptions import TracecatNotFoundError, TracecatValidationError
from tracecat.pagination import BaseCursorPaginator, CursorPaginationParams
from tracecat.registry.actions.schemas import RegistryActionType
from tracecat.registry.versions.schemas import (
    RegistryVersionManifest,
    RegistryVersionManifestAction,
)
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
async def agent_preset_service(
    session: AsyncSession, svc_role: Role
) -> AgentPresetService:
    """Create an agent preset service instance for testing."""
    return AgentPresetService(session=session, role=svc_role)


@pytest.fixture
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
    monkeypatch.setenv("TRACECAT__BLOB_STORAGE_BUCKET_SKILLS", minio_bucket)
    monkeypatch.setenv(
        "AWS_ACCESS_KEY_ID",
        os.environ.get("AWS_ACCESS_KEY_ID", "minio"),
    )
    monkeypatch.setenv(
        "AWS_SECRET_ACCESS_KEY",
        os.environ.get("AWS_SECRET_ACCESS_KEY", "password"),
    )

    await ensure_bucket_exists(minio_bucket)


@pytest.fixture
async def registry_repository(
    session: AsyncSession, svc_workspace: Workspace
) -> RegistryRepository:
    """Create a test registry repository."""
    repo = RegistryRepository(
        organization_id=svc_workspace.organization_id,
        origin="test",
    )
    session.add(repo)
    await session.commit()
    await session.refresh(repo)
    return repo


@pytest.fixture
async def registry_actions(
    session: AsyncSession, registry_repository: RegistryRepository
) -> list[RegistryAction]:
    """Create test registry actions with associated index entries.

    This fixture creates RegistryAction entries along with:
    - A RegistryVersion with a manifest containing all actions
    - RegistryIndex entries for each action

    This ensures actions are discoverable via both direct queries and index lookups.
    """
    # Define test actions
    test_actions_data = [
        {
            "name": "test_action",
            "namespace": "tools.test",
            "description": "Test action",
            "type": "udf",
        },
        {
            "name": "another_action",
            "namespace": "tools.test",
            "description": "Another test action",
            "type": "udf",
        },
        {
            "name": "http_request",
            "namespace": "core",
            "description": "HTTP request action",
            "type": "template",
        },
    ]

    # Create RegistryAction entries
    actions = []
    for action_data in test_actions_data:
        action = RegistryAction(
            organization_id=registry_repository.organization_id,
            repository_id=registry_repository.id,
            name=action_data["name"],
            namespace=action_data["namespace"],
            description=action_data["description"],
            origin="test",
            type=action_data["type"],
            interface={},
            implementation={},
            options={},
        )
        session.add(action)
        actions.append(action)
    await session.flush()

    # Build manifest for the registry version
    manifest_actions = {}
    for action_data in test_actions_data:
        action_name = f"{action_data['namespace']}.{action_data['name']}"
        manifest_actions[action_name] = RegistryVersionManifestAction(
            namespace=action_data["namespace"],
            name=action_data["name"],
            action_type=cast(RegistryActionType, action_data["type"]),
            description=action_data["description"],
            interface={"expects": {}, "returns": {}},
            implementation={"type": action_data["type"]},
        )
    manifest = RegistryVersionManifest(actions=manifest_actions)

    # Create RegistryVersion with manifest
    version = RegistryVersion(
        organization_id=registry_repository.organization_id,
        repository_id=registry_repository.id,
        version="test-version",
        manifest=manifest.model_dump(mode="json"),
        tarball_uri="s3://test/test.tar.gz",
    )
    session.add(version)
    await session.flush()

    # Set current_version_id on the repository
    registry_repository.current_version_id = version.id
    await session.flush()

    # Create RegistryIndex entries for each action
    for action_data in test_actions_data:
        index_entry = RegistryIndex(
            organization_id=registry_repository.organization_id,
            registry_version_id=version.id,
            namespace=action_data["namespace"],
            name=action_data["name"],
            action_type=action_data["type"],
            description=action_data["description"],
            options={"include_in_schema": True},
        )
        session.add(index_entry)
    await session.commit()

    for action in actions:
        await session.refresh(action)
    return actions


@pytest.fixture
def agent_preset_create_params() -> AgentPresetCreate:
    """Sample agent preset creation parameters."""
    return AgentPresetCreate(
        name="Test Agent Preset",
        slug=None,  # Will be auto-generated
        description="A test agent preset",
        instructions="You are a helpful assistant.",
        model_name="gpt-4o-mini",
        model_provider="openai",
        base_url=None,
        output_type=None,
        actions=None,
        namespaces=None,
        tool_approvals=None,
        mcp_integrations=None,
        retries=3,
        enable_thinking=True,
    )


@pytest.mark.anyio
class TestAgentPresetService:
    async def test_create_and_get_preset(
        self,
        agent_preset_service: AgentPresetService,
        agent_preset_create_params: AgentPresetCreate,
    ) -> None:
        """Test creating and retrieving an agent preset."""
        # Create preset
        created_preset = await agent_preset_service.create_preset(
            agent_preset_create_params
        )
        assert created_preset.name == agent_preset_create_params.name
        assert created_preset.slug == "test-agent-preset"  # Auto-slugified
        assert created_preset.description == agent_preset_create_params.description
        assert created_preset.model_name == agent_preset_create_params.model_name
        assert (
            created_preset.model_provider == agent_preset_create_params.model_provider
        )
        assert created_preset.enable_thinking is True
        assert created_preset.workspace_id == agent_preset_service.workspace_id

        # Retrieve by ID
        retrieved_preset = await agent_preset_service.get_preset(created_preset.id)
        assert retrieved_preset is not None
        assert retrieved_preset.id == created_preset.id
        assert retrieved_preset.name == agent_preset_create_params.name

    async def test_create_preset_with_custom_slug(
        self,
        agent_preset_service: AgentPresetService,
        agent_preset_create_params: AgentPresetCreate,
    ) -> None:
        """Test creating a preset with a custom slug."""
        agent_preset_create_params.slug = "my-custom-slug"

        created_preset = await agent_preset_service.create_preset(
            agent_preset_create_params
        )
        assert created_preset.slug == "my-custom-slug"

    async def test_create_preset_with_actions(
        self,
        agent_preset_service: AgentPresetService,
        agent_preset_create_params: AgentPresetCreate,
        registry_actions: list[RegistryAction],
    ) -> None:
        """Test creating a preset with validated actions."""
        # Use valid actions from registry
        agent_preset_create_params.actions = [
            "tools.test.test_action",
            "core.http_request",
        ]

        created_preset = await agent_preset_service.create_preset(
            agent_preset_create_params
        )
        assert created_preset.actions == agent_preset_create_params.actions

    async def test_create_preset_with_invalid_actions(
        self,
        agent_preset_service: AgentPresetService,
        agent_preset_create_params: AgentPresetCreate,
        registry_actions: list[RegistryAction],
    ) -> None:
        """Test that creating a preset with invalid actions raises an error."""
        # Use actions that don't exist in registry
        agent_preset_create_params.actions = [
            "tools.test.test_action",
            "tools.nonexistent.action",
            "another.invalid.action",
        ]

        with pytest.raises(
            TracecatValidationError, match="2 actions were not found in the registry"
        ):
            await agent_preset_service.create_preset(agent_preset_create_params)

    async def test_create_preset_allows_custom_provider_without_base_url(
        self,
        agent_preset_service: AgentPresetService,
        agent_preset_create_params: AgentPresetCreate,
    ) -> None:
        agent_preset_create_params.model_provider = "custom-model-provider"
        agent_preset_create_params.model_name = "customer-alias"
        agent_preset_create_params.base_url = None

        created_preset = await agent_preset_service.create_preset(
            agent_preset_create_params
        )

        assert created_preset.model_provider == "custom-model-provider"
        assert created_preset.model_name == "customer-alias"
        assert created_preset.base_url is None

    async def test_list_presets(
        self,
        agent_preset_service: AgentPresetService,
        agent_preset_create_params: AgentPresetCreate,
    ) -> None:
        """Test listing agent presets."""
        # Create multiple presets
        preset1 = await agent_preset_service.create_preset(agent_preset_create_params)

        params2 = agent_preset_create_params.model_copy(deep=True)
        params2.name = "Second Preset"
        preset2 = await agent_preset_service.create_preset(params2)

        # List all presets
        presets = await agent_preset_service.list_presets()
        assert len(presets) >= 2
        preset_ids = {preset.id for preset in presets}
        assert preset1.id in preset_ids
        assert preset2.id in preset_ids

        # Verify ordering by created_at descending (most recent first)
        assert presets[0].created_at >= presets[1].created_at

    async def test_update_preset_name(
        self,
        agent_preset_service: AgentPresetService,
        agent_preset_create_params: AgentPresetCreate,
    ) -> None:
        """Test updating a preset's name."""
        # Create preset
        created_preset = await agent_preset_service.create_preset(
            agent_preset_create_params
        )
        original_slug = created_preset.slug

        # Update name only
        update_params = AgentPresetUpdate(name="Updated Preset Name")
        updated_preset = await agent_preset_service.update_preset(
            created_preset, update_params
        )

        assert updated_preset.name == "Updated Preset Name"
        assert updated_preset.slug == original_slug  # Slug unchanged
        versions = await agent_preset_service.list_versions(
            created_preset.id,
            CursorPaginationParams(limit=10),
        )
        assert [version.version for version in versions.items] == [1]

    async def test_update_preset_enable_thinking_creates_new_version(
        self,
        agent_preset_service: AgentPresetService,
        agent_preset_create_params: AgentPresetCreate,
    ) -> None:
        created_preset = await agent_preset_service.create_preset(
            agent_preset_create_params
        )

        updated_preset = await agent_preset_service.update_preset(
            created_preset,
            AgentPresetUpdate(enable_thinking=False),
        )

        assert updated_preset.enable_thinking is False
        versions = await agent_preset_service.list_versions(
            created_preset.id,
            CursorPaginationParams(limit=10),
        )
        assert [version.version for version in versions.items] == [2, 1]
        assert versions.items[0].enable_thinking is False

    async def test_create_preset_creates_initial_version(
        self,
        agent_preset_service: AgentPresetService,
        agent_preset_create_params: AgentPresetCreate,
    ) -> None:
        """Creating a preset also creates and points to version 1."""
        created_preset = await agent_preset_service.create_preset(
            agent_preset_create_params
        )

        assert created_preset.current_version_id is not None
        current_version = await agent_preset_service.get_current_version_for_preset(
            created_preset
        )
        versions = await agent_preset_service.list_versions(
            created_preset.id,
            CursorPaginationParams(limit=10),
        )

        assert current_version.id == created_preset.current_version_id
        assert current_version.version == 1
        assert [version.version for version in versions.items] == [1]

    async def test_update_preset_execution_fields_create_new_version(
        self,
        agent_preset_service: AgentPresetService,
        agent_preset_create_params: AgentPresetCreate,
    ) -> None:
        """Changing executable fields creates a new immutable version."""
        created_preset = await agent_preset_service.create_preset(
            agent_preset_create_params
        )
        version_1 = await agent_preset_service.get_current_version_for_preset(
            created_preset
        )

        updated_preset = await agent_preset_service.update_preset(
            created_preset,
            AgentPresetUpdate(instructions="Updated instructions", retries=7),
        )
        versions = await agent_preset_service.list_versions(
            created_preset.id,
            CursorPaginationParams(limit=10),
        )

        assert updated_preset.current_version_id is not None
        assert updated_preset.current_version_id != version_1.id
        assert [version.version for version in versions.items] == [2, 1]
        assert versions.items[0].instructions == "Updated instructions"
        assert versions.items[0].retries == 7

    async def test_list_versions_returns_cursor_paginated_versions(
        self,
        agent_preset_service: AgentPresetService,
        agent_preset_create_params: AgentPresetCreate,
    ) -> None:
        created_preset = await agent_preset_service.create_preset(
            agent_preset_create_params
        )
        await agent_preset_service.update_preset(
            created_preset,
            AgentPresetUpdate(instructions="Version 2"),
        )
        await agent_preset_service.update_preset(
            created_preset,
            AgentPresetUpdate(instructions="Version 3"),
        )

        page_1 = await agent_preset_service.list_versions(
            created_preset.id,
            CursorPaginationParams(limit=2),
        )

        assert [version.version for version in page_1.items] == [3, 2]
        assert page_1.has_more is True
        assert page_1.next_cursor is not None

        page_2 = await agent_preset_service.list_versions(
            created_preset.id,
            CursorPaginationParams(limit=2, cursor=page_1.next_cursor),
        )

        assert [version.version for version in page_2.items] == [1]
        assert page_2.has_more is False

    async def test_list_versions_rejects_invalid_cursor(
        self,
        agent_preset_service: AgentPresetService,
        agent_preset_create_params: AgentPresetCreate,
    ) -> None:
        created_preset = await agent_preset_service.create_preset(
            agent_preset_create_params
        )

        with pytest.raises(
            TracecatValidationError,
            match="Invalid cursor for agent preset versions",
        ):
            await agent_preset_service.list_versions(
                created_preset.id,
                CursorPaginationParams(limit=2, cursor="invalid-base64!"),
            )

    async def test_list_versions_rejects_cursor_with_invalid_uuid(
        self,
        agent_preset_service: AgentPresetService,
        agent_preset_create_params: AgentPresetCreate,
    ) -> None:
        created_preset = await agent_preset_service.create_preset(
            agent_preset_create_params
        )
        invalid_uuid_cursor = BaseCursorPaginator.encode_cursor(
            "not-a-uuid",
            sort_column="version",
            sort_value=1,
        )

        with pytest.raises(
            TracecatValidationError,
            match="Invalid cursor for agent preset versions",
        ):
            await agent_preset_service.list_versions(
                created_preset.id,
                CursorPaginationParams(limit=2, cursor=invalid_uuid_cursor),
            )

    async def test_update_preset_concurrently_allocates_unique_versions(
        self,
        agent_preset_create_params: AgentPresetCreate,
        svc_role: Role,
    ) -> None:
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

                seed_service = AgentPresetService(
                    session=seed_session,
                    role=role.model_copy(deep=True),
                )
                created_preset = await seed_service.create_preset(
                    agent_preset_create_params
                )
                await seed_session.commit()

            async def update_preset(index: int) -> str:
                async with session_factory() as concurrent_session:
                    service = AgentPresetService(
                        session=concurrent_session,
                        role=role.model_copy(deep=True),
                    )
                    preset = await service.get_preset(created_preset.id)
                    assert preset is not None
                    updated = await service.update_preset(
                        preset,
                        AgentPresetUpdate(
                            instructions=f"Concurrent instructions {index}",
                        ),
                    )
                    return cast(str, updated.instructions)

            updated_instructions = await asyncio.gather(
                update_preset(1),
                update_preset(2),
            )

            assert sorted(updated_instructions) == [
                "Concurrent instructions 1",
                "Concurrent instructions 2",
            ]

            async with session_factory() as verification_session:
                versions = (
                    (
                        await verification_session.execute(
                            select(AgentPresetVersion.version)
                            .where(AgentPresetVersion.preset_id == created_preset.id)
                            .order_by(AgentPresetVersion.version.asc())
                        )
                    )
                    .scalars()
                    .all()
                )
                preset = await verification_session.scalar(
                    select(AgentPreset).where(AgentPreset.id == created_preset.id)
                )

            assert versions == [1, 2, 3]
            assert preset is not None
            assert preset.current_version_id is not None
        finally:
            await concurrent_engine.dispose()

    async def test_compare_versions_returns_structured_diff(
        self,
        agent_preset_service: AgentPresetService,
        agent_preset_create_params: AgentPresetCreate,
        registry_actions: list[RegistryAction],
    ) -> None:
        """Version compare exposes prompt, scalar, list, and approval changes."""
        agent_preset_create_params.actions = ["tools.test.test_action"]
        agent_preset_create_params.tool_approvals = {"tools.test.test_action": False}

        created_preset = await agent_preset_service.create_preset(
            agent_preset_create_params
        )
        version_1 = await agent_preset_service.get_current_version_for_preset(
            created_preset
        )

        await agent_preset_service.update_preset(
            created_preset,
            AgentPresetUpdate(
                instructions="Updated instructions",
                actions=["tools.test.another_action", "core.http_request"],
                tool_approvals={"tools.test.another_action": True},
                retries=9,
            ),
        )
        version_2 = await agent_preset_service.get_current_version_for_preset(
            created_preset
        )

        diff = await agent_preset_service.compare_versions(version_1, version_2)

        assert diff.instructions_changed is True
        assert diff.base_instructions == agent_preset_create_params.instructions
        assert diff.compare_instructions == "Updated instructions"
        assert any(
            change.field == "retries"
            and change.old_value == 3
            and change.new_value == 9
            for change in diff.scalar_changes
        )
        assert any(
            change.field == "actions"
            and change.added == ["core.http_request", "tools.test.another_action"]
            and change.removed == ["tools.test.test_action"]
            for change in diff.list_changes
        )
        assert any(
            change.tool == "tools.test.another_action"
            and change.old_value is None
            and change.new_value is True
            for change in diff.tool_approval_changes
        )

    async def test_preset_version_snapshots_exact_skill_versions(
        self,
        configure_minio_for_skills,
        session: AsyncSession,
        svc_role: Role,
        agent_preset_service: AgentPresetService,
    ) -> None:
        """Preset versions snapshot exact skill versions at creation time."""

        skill_service = SkillService(session=session, role=svc_role)
        created_skill = await skill_service.create_skill(
            SkillCreate(slug="triage-skill")
        )
        skill_version = await skill_service.publish_skill(created_skill.id)

        created_preset = await agent_preset_service.create_preset(
            AgentPresetCreate(
                name="Skill preset",
                description="Preset with a skill",
                instructions="Use the selected skill version",
                model_name="gpt-4o-mini",
                model_provider="openai",
                skills=[
                    AgentPresetSkillBindingBase(
                        skill_id=created_skill.id,
                        skill_version_id=skill_version.id,
                    )
                ],
            )
        )
        current_version = await agent_preset_service.get_current_version_for_preset(
            created_preset
        )
        version_read = await agent_preset_service.build_version_read(current_version)

        assert len(version_read.skills) == 1
        assert version_read.skills[0].skill_version_id == skill_version.id
        assert version_read.skills[0].skill_version == 1

    async def test_restore_version_restores_historical_skill_versions_on_head(
        self,
        configure_minio_for_skills,
        session: AsyncSession,
        svc_role: Role,
        agent_preset_service: AgentPresetService,
    ) -> None:
        """Restoring a preset version copies historical skill versions onto the head."""

        skill_service = SkillService(session=session, role=svc_role)
        created_skill = await skill_service.create_skill(
            SkillCreate(slug="restore-skill")
        )
        skill_version_one = await skill_service.publish_skill(created_skill.id)

        created_preset = await agent_preset_service.create_preset(
            AgentPresetCreate(
                name="Restore preset",
                description="Preset with restorable skill bindings",
                instructions="Use the selected skill version",
                model_name="gpt-4o-mini",
                model_provider="openai",
                skills=[
                    AgentPresetSkillBindingBase(
                        skill_id=created_skill.id,
                        skill_version_id=skill_version_one.id,
                    )
                ],
            )
        )
        preset_version_one = await agent_preset_service.get_current_version_for_preset(
            created_preset
        )

        draft = await skill_service.get_draft(created_skill.id)
        assert draft is not None
        await skill_service.patch_draft(
            skill_id=created_skill.id,
            params=SkillDraftPatch(
                base_revision=draft.draft_revision,
                operations=[
                    SkillDraftUpsertTextFileOp(
                        path="references/upgrade.md",
                        content="Second published version",
                    )
                ],
            ),
        )
        skill_version_two = await skill_service.publish_skill(created_skill.id)
        assert skill_version_two.id != skill_version_one.id

        await agent_preset_service.update_preset(
            created_preset,
            AgentPresetUpdate(
                instructions="Trigger a new preset version",
                skills=[
                    AgentPresetSkillBindingBase(
                        skill_id=created_skill.id,
                        skill_version_id=skill_version_two.id,
                    )
                ],
            ),
        )
        preset_version_two = await agent_preset_service.get_current_version_for_preset(
            created_preset
        )

        diff = await agent_preset_service.compare_versions(
            preset_version_one,
            preset_version_two,
        )
        restored = await agent_preset_service.restore_version(
            created_preset,
            preset_version_one,
        )
        restored_bindings = await agent_preset_service._list_head_skill_bindings(
            restored.id
        )

        assert len(diff.skill_changes) == 1
        assert diff.skill_changes[0].old_skill_version_id == skill_version_one.id
        assert diff.skill_changes[0].new_skill_version_id == skill_version_two.id
        assert len(restored_bindings) == 1
        assert restored_bindings[0].skill_version_id == skill_version_one.id

    async def test_update_preset_clears_all_skill_bindings_when_skills_is_null(
        self,
        configure_minio_for_skills,
        session: AsyncSession,
        svc_role: Role,
        agent_preset_service: AgentPresetService,
    ) -> None:
        """Explicit null skill updates clear mutable preset skill bindings."""

        skill_service = SkillService(session=session, role=svc_role)
        created_skill = await skill_service.create_skill(
            SkillCreate(slug="clear-skill-bindings")
        )
        skill_version = await skill_service.publish_skill(created_skill.id)

        created_preset = await agent_preset_service.create_preset(
            AgentPresetCreate(
                name="Clear skill preset",
                description="Preset with a removable skill binding",
                instructions="Use the selected skill version",
                model_name="gpt-4o-mini",
                model_provider="openai",
                skills=[
                    AgentPresetSkillBindingBase(
                        skill_id=created_skill.id,
                        skill_version_id=skill_version.id,
                    )
                ],
            )
        )

        await agent_preset_service.update_preset(
            created_preset,
            AgentPresetUpdate(skills=None),
        )
        current_version = await agent_preset_service.get_current_version_for_preset(
            created_preset
        )
        current_bindings = await agent_preset_service._list_head_skill_bindings(
            created_preset.id
        )
        version_read = await agent_preset_service.build_version_read(current_version)

        assert current_bindings == []
        assert version_read.skills == []

    async def test_restore_version_moves_current_pointer(
        self,
        agent_preset_service: AgentPresetService,
        agent_preset_create_params: AgentPresetCreate,
    ) -> None:
        """Restoring an old version repoints current without creating another row."""
        created_preset = await agent_preset_service.create_preset(
            agent_preset_create_params
        )
        version_1 = await agent_preset_service.get_current_version_for_preset(
            created_preset
        )

        await agent_preset_service.update_preset(
            created_preset,
            AgentPresetUpdate(instructions="Updated instructions"),
        )
        restored_preset = await agent_preset_service.restore_version(
            created_preset, version_1
        )
        versions = await agent_preset_service.list_versions(
            created_preset.id,
            CursorPaginationParams(limit=10),
        )

        assert restored_preset.current_version_id == version_1.id
        assert restored_preset.instructions == agent_preset_create_params.instructions
        assert [version.version for version in versions.items] == [2, 1]

    async def test_update_preset_slug(
        self,
        agent_preset_service: AgentPresetService,
        agent_preset_create_params: AgentPresetCreate,
    ) -> None:
        """Test updating a preset's slug."""
        # Create preset
        created_preset = await agent_preset_service.create_preset(
            agent_preset_create_params
        )

        # Update slug
        update_params = AgentPresetUpdate(slug="new-custom-slug")
        updated_preset = await agent_preset_service.update_preset(
            created_preset, update_params
        )

        assert updated_preset.slug == "new-custom-slug"

        # Verify we can fetch by new slug
        retrieved = await agent_preset_service.get_preset_by_slug("new-custom-slug")
        assert retrieved is not None
        assert retrieved.id == created_preset.id

    async def test_update_preset_actions_valid(
        self,
        agent_preset_service: AgentPresetService,
        agent_preset_create_params: AgentPresetCreate,
        registry_actions: list[RegistryAction],
    ) -> None:
        """Test updating a preset's actions with valid actions."""
        # Create preset with initial actions
        agent_preset_create_params.actions = ["tools.test.test_action"]
        created_preset = await agent_preset_service.create_preset(
            agent_preset_create_params
        )

        # Update with different valid actions
        update_params = AgentPresetUpdate(
            actions=["tools.test.another_action", "core.http_request"]
        )
        updated_preset = await agent_preset_service.update_preset(
            created_preset, update_params
        )

        assert updated_preset.actions == [
            "tools.test.another_action",
            "core.http_request",
        ]

    async def test_update_preset_actions_invalid(
        self,
        agent_preset_service: AgentPresetService,
        agent_preset_create_params: AgentPresetCreate,
    ) -> None:
        """Test that updating with invalid actions raises an error."""
        # Create preset
        created_preset = await agent_preset_service.create_preset(
            agent_preset_create_params
        )

        # Try to update with invalid actions
        update_params = AgentPresetUpdate(
            actions=["tools.invalid.action", "another.bad.action"]
        )

        with pytest.raises(
            TracecatValidationError, match="2 actions were not found in the registry"
        ):
            await agent_preset_service.update_preset(created_preset, update_params)

    async def test_update_preset_actions_to_empty_list(
        self,
        agent_preset_service: AgentPresetService,
        agent_preset_create_params: AgentPresetCreate,
        registry_actions: list[RegistryAction],
    ) -> None:
        """Test updating actions to an empty list."""
        # Create preset with actions
        agent_preset_create_params.actions = ["tools.test.test_action"]
        created_preset = await agent_preset_service.create_preset(
            agent_preset_create_params
        )

        # Update to empty list
        update_params = AgentPresetUpdate(actions=[])
        updated_preset = await agent_preset_service.update_preset(
            created_preset, update_params
        )

        assert updated_preset.actions == []

    async def test_update_preset_multiple_fields(
        self,
        agent_preset_service: AgentPresetService,
        agent_preset_create_params: AgentPresetCreate,
    ) -> None:
        """Test updating multiple fields at once."""
        # Create preset
        created_preset = await agent_preset_service.create_preset(
            agent_preset_create_params
        )

        # Update multiple fields
        update_params = AgentPresetUpdate(
            name="New Name",
            description="New description",
            instructions="New instructions",
            model_name="gpt-4",
            model_provider="openai",
            retries=5,
        )
        updated_preset = await agent_preset_service.update_preset(
            created_preset, update_params
        )

        assert updated_preset.name == "New Name"
        assert updated_preset.description == "New description"
        assert updated_preset.instructions == "New instructions"
        assert updated_preset.model_name == "gpt-4"
        assert updated_preset.retries == 5

    async def test_delete_preset(
        self,
        agent_preset_service: AgentPresetService,
        agent_preset_create_params: AgentPresetCreate,
    ) -> None:
        """Test deleting a preset."""
        # Create preset
        created_preset = await agent_preset_service.create_preset(
            agent_preset_create_params
        )
        preset_id = created_preset.id

        # Delete preset
        await agent_preset_service.delete_preset(created_preset)

        # Verify deletion
        deleted_preset = await agent_preset_service.get_preset(preset_id)
        assert deleted_preset is None

    async def test_get_preset_by_slug(
        self,
        agent_preset_service: AgentPresetService,
        agent_preset_create_params: AgentPresetCreate,
    ) -> None:
        """Test retrieving a preset by slug."""
        # Create preset
        created_preset = await agent_preset_service.create_preset(
            agent_preset_create_params
        )

        # Retrieve by slug
        retrieved = await agent_preset_service.get_preset_by_slug("test-agent-preset")
        assert retrieved is not None
        assert retrieved.id == created_preset.id
        assert retrieved.slug == "test-agent-preset"

    async def test_get_preset_by_slug_not_found(
        self, agent_preset_service: AgentPresetService
    ) -> None:
        """Test that getting a non-existent preset by slug returns None."""
        retrieved = await agent_preset_service.get_preset_by_slug("nonexistent-slug")
        assert retrieved is None

    async def test_unique_slug_per_workspace(
        self,
        agent_preset_service: AgentPresetService,
        agent_preset_create_params: AgentPresetCreate,
    ) -> None:
        """Test that slugs must be unique within a workspace."""
        # Create first preset
        await agent_preset_service.create_preset(agent_preset_create_params)

        # Try to create another with the same name (which generates same slug)
        with pytest.raises(
            TracecatValidationError,
            match="Agent preset slug 'test-agent-preset' is already in use",
        ):
            await agent_preset_service.create_preset(agent_preset_create_params)

    async def test_slug_normalization(
        self,
        agent_preset_service: AgentPresetService,
        agent_preset_create_params: AgentPresetCreate,
    ) -> None:
        """Test that slugs are properly normalized."""
        # Test various slug formats
        test_cases = [
            ("My Agent Preset", "my-agent-preset"),
            ("Agent_With_Underscores", "agent-with-underscores"),
            ("Agent123", "agent123"),
            ("UPPERCASE AGENT", "uppercase-agent"),
            ("Agent   Multiple   Spaces", "agent-multiple-spaces"),
        ]

        for name, expected_slug in test_cases:
            params = agent_preset_create_params.model_copy(deep=True)
            params.name = name
            preset = await agent_preset_service.create_preset(params)
            assert preset.slug == expected_slug

            # Clean up for next iteration
            await agent_preset_service.delete_preset(preset)

    async def test_empty_slug_raises_error(
        self,
        agent_preset_service: AgentPresetService,
        agent_preset_create_params: AgentPresetCreate,
    ) -> None:
        """Test that an empty slug raises a validation error."""
        agent_preset_create_params.name = ""
        agent_preset_create_params.slug = None

        with pytest.raises(
            TracecatValidationError, match="Agent preset slug cannot be empty"
        ):
            await agent_preset_service.create_preset(agent_preset_create_params)

    async def test_resolve_agent_preset_config_by_id(
        self,
        agent_preset_service: AgentPresetService,
        agent_preset_create_params: AgentPresetCreate,
    ) -> None:
        """Test resolving agent config by preset ID."""
        created_preset = await agent_preset_service.create_preset(
            agent_preset_create_params
        )

        config = await agent_preset_service.resolve_agent_preset_config(
            preset_id=created_preset.id
        )

        assert isinstance(config, AgentConfig)
        assert config.model_name == agent_preset_create_params.model_name

    async def test_resolve_agent_preset_config_by_slug(
        self,
        agent_preset_service: AgentPresetService,
        agent_preset_create_params: AgentPresetCreate,
    ) -> None:
        """Test resolving agent config by slug."""
        created_preset = await agent_preset_service.create_preset(
            agent_preset_create_params
        )

        config = await agent_preset_service.resolve_agent_preset_config(
            slug=created_preset.slug
        )

        assert isinstance(config, AgentConfig)
        assert config.model_name == agent_preset_create_params.model_name

    async def test_resolve_agent_preset_config_by_id_not_found(
        self, agent_preset_service: AgentPresetService
    ) -> None:
        """Test that resolving config for non-existent preset raises error."""
        with pytest.raises(TracecatNotFoundError, match="Agent preset '.*' not found"):
            await agent_preset_service.resolve_agent_preset_config(
                preset_id=uuid.uuid4()
            )

    async def test_resolve_agent_preset_config_by_slug_not_found(
        self, agent_preset_service: AgentPresetService
    ) -> None:
        """Test that resolving config by non-existent slug raises error."""
        with pytest.raises(
            TracecatNotFoundError,
            match="Agent preset 'nonexistent' not found",
        ):
            await agent_preset_service.resolve_agent_preset_config(slug="nonexistent")

    async def test_resolve_agent_preset_config_no_params_raises_error(
        self, agent_preset_service: AgentPresetService
    ) -> None:
        """Test that resolve without parameters raises ValueError."""
        with pytest.raises(
            ValueError,
            match="Either preset_id, slug, or preset_version_id must be provided",
        ):
            await agent_preset_service.resolve_agent_preset_config()

    async def test_update_preset_with_name_and_slug(
        self,
        agent_preset_service: AgentPresetService,
        agent_preset_create_params: AgentPresetCreate,
    ) -> None:
        """Test updating both name and slug together."""
        created_preset = await agent_preset_service.create_preset(
            agent_preset_create_params
        )

        # Update both name and slug
        update_params = AgentPresetUpdate(name="Brand New Name", slug="brand-new-slug")
        updated_preset = await agent_preset_service.update_preset(
            created_preset, update_params
        )

        assert updated_preset.name == "Brand New Name"
        assert updated_preset.slug == "brand-new-slug"

    async def test_update_slug_duplicate_raises_error(
        self,
        agent_preset_service: AgentPresetService,
        agent_preset_create_params: AgentPresetCreate,
    ) -> None:
        """Test that updating to a duplicate slug raises an error."""
        # Create first preset
        preset1 = await agent_preset_service.create_preset(agent_preset_create_params)

        # Create second preset
        params2 = agent_preset_create_params.model_copy(deep=True)
        params2.name = "Second Preset"
        preset2 = await agent_preset_service.create_preset(params2)

        # Try to update preset2's slug to match preset1
        update_params = AgentPresetUpdate(slug=preset1.slug)

        with pytest.raises(
            TracecatValidationError,
            match=f"Agent preset slug '{preset1.slug}' is already in use",
        ):
            await agent_preset_service.update_preset(preset2, update_params)

    async def test_preset_to_agent_config_conversion(
        self,
        agent_preset_service: AgentPresetService,
        agent_preset_create_params: AgentPresetCreate,
        registry_actions: list[RegistryAction],
    ) -> None:
        """Test conversion of a preset version into executable config."""
        # Create a preset with comprehensive configuration
        agent_preset_create_params.actions = ["tools.test.test_action"]
        agent_preset_create_params.namespaces = ["tools.test", "core"]
        agent_preset_create_params.output_type = "list[str]"
        agent_preset_create_params.tool_approvals = {"tools.test.test_action": False}

        preset = await agent_preset_service.create_preset(agent_preset_create_params)
        version = await agent_preset_service.get_current_version_for_preset(preset)

        # Test conversion
        agent_config = await agent_preset_service._version_to_agent_config(version)

        assert isinstance(agent_config, AgentConfig)
        assert agent_config.model_name == preset.model_name
        assert agent_config.model_provider == preset.model_provider
        assert agent_config.base_url == preset.base_url
        assert agent_config.instructions == preset.instructions
        assert agent_config.output_type == preset.output_type
        assert agent_config.actions == preset.actions
        assert agent_config.namespaces == preset.namespaces
        assert agent_config.tool_approvals == preset.tool_approvals
        assert agent_config.retries == preset.retries
        assert agent_config.model_settings == {"parallel_tool_calls": False}

    async def test_create_preset_with_tool_approvals(
        self,
        agent_preset_service: AgentPresetService,
        agent_preset_create_params: AgentPresetCreate,
        registry_actions: list[RegistryAction],
    ) -> None:
        """Test that creating a preset with tool_approvals is allowed."""
        agent_preset_create_params.actions = ["tools.test.test_action"]
        agent_preset_create_params.tool_approvals = {"tools.test.test_action": True}

        preset = await agent_preset_service.create_preset(agent_preset_create_params)
        assert preset.tool_approvals == {"tools.test.test_action": True}

    async def test_update_preset_tool_approvals(
        self,
        agent_preset_service: AgentPresetService,
        agent_preset_create_params: AgentPresetCreate,
        registry_actions: list[RegistryAction],
    ) -> None:
        """Test that updating tool_approvals on a preset is allowed."""
        agent_preset_create_params.actions = ["tools.test.test_action"]
        preset = await agent_preset_service.create_preset(agent_preset_create_params)

        update_params = AgentPresetUpdate(
            tool_approvals={"tools.test.test_action": True}
        )
        updated_preset = await agent_preset_service.update_preset(preset, update_params)
        assert updated_preset.tool_approvals == {"tools.test.test_action": True}

    async def test_update_preset_clear_tool_approvals(
        self,
        agent_preset_service: AgentPresetService,
        agent_preset_create_params: AgentPresetCreate,
        registry_actions: list[RegistryAction],
    ) -> None:
        """Test that clearing tool_approvals on a preset is allowed."""
        agent_preset_create_params.actions = ["tools.test.test_action"]
        agent_preset_create_params.tool_approvals = {"tools.test.test_action": True}
        preset = await agent_preset_service.create_preset(agent_preset_create_params)

        update_params = AgentPresetUpdate(tool_approvals=None)
        updated_preset = await agent_preset_service.update_preset(preset, update_params)
        assert updated_preset.tool_approvals is None
