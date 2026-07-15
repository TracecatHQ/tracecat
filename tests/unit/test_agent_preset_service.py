"""Tests for AgentPresetService."""

import asyncio
import os
import uuid
from datetime import UTC, datetime
from typing import cast

import pytest
import sqlalchemy as sa
from dotenv import dotenv_values
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests.database import TEST_DB_CONFIG
from tracecat import config
from tracecat.agent.channels.schemas import (
    AgentChannelTokenCreate,
    ChannelType,
    SlackChannelTokenConfig,
)
from tracecat.agent.channels.service import PENDING_SLACK_BOT_TOKEN, AgentChannelService
from tracecat.agent.preset.resolver import resolve_agents_config
from tracecat.agent.preset.schemas import (
    AgentPresetCreate,
    AgentPresetSkillBindingBase,
    AgentPresetSkillBindingRead,
    AgentPresetUpdate,
)
from tracecat.agent.preset.service import AgentPresetService
from tracecat.agent.skill.schemas import (
    SkillCreate,
    SkillDraftPatch,
    SkillDraftUpsertTextFileOp,
)
from tracecat.agent.skill.service import SkillService
from tracecat.agent.subagents import (
    AgentSubagentsConfig,
    AttachedSubagentRef,
    HeadAttachedSubagentRef,
    ResolvedAgentsConfig,
)
from tracecat.agent.types import AgentConfig
from tracecat.auth.types import Role
from tracecat.db.models import (
    AgentCatalog,
    AgentChannelToken,
    AgentModelAccess,
    AgentPreset,
    AgentPresetSkill,
    AgentPresetVersion,
    AgentPresetVersionSkill,
    AgentPresetVersionSubagent,
    MCPIntegration,
    Organization,
    RegistryAction,
    RegistryIndex,
    RegistryRepository,
    RegistryVersion,
    Skill,
    Workspace,
)
from tracecat.exceptions import TracecatNotFoundError, TracecatValidationError
from tracecat.integrations.enums import MCPAuthType
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


async def _create_stdio_mcp_integration(
    session: AsyncSession, workspace_id: uuid.UUID
) -> MCPIntegration:
    integration = MCPIntegration(
        workspace_id=workspace_id,
        name="Test stdio MCP",
        description="Synthetic stdio MCP integration",
        slug=f"test-stdio-mcp-{uuid.uuid4().hex}",
        server_type="stdio",
        auth_type=MCPAuthType.NONE,
        stdio_command="npx",
        stdio_args=["@tracecat/test-mcp-server"],
    )
    session.add(integration)
    await session.flush()
    await session.refresh(integration)
    return integration


class _AsyncContext:
    def __init__(self, value: object) -> None:
        self._value = value

    async def __aenter__(self) -> object:
        return self._value

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        return None


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
        created_preset = await agent_preset_service.create_preset(
            agent_preset_create_params
        )
        assert created_preset.name == agent_preset_create_params.name
        assert created_preset.slug == "test-agent-preset"  # Auto-slugified
        assert created_preset.description == agent_preset_create_params.description
        version = await agent_preset_service.get_current_version_for_preset(
            created_preset
        )
        assert version.model_name == agent_preset_create_params.model_name
        assert version.model_provider == agent_preset_create_params.model_provider
        assert version.enable_thinking is True
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
        # Use valid actions from registry
        agent_preset_create_params.actions = [
            "tools.test.test_action",
            "core.http_request",
        ]

        created_preset = await agent_preset_service.create_preset(
            agent_preset_create_params
        )
        version = await agent_preset_service.get_current_version_for_preset(
            created_preset
        )
        assert version.actions == agent_preset_create_params.actions

    async def test_create_preset_with_invalid_actions(
        self,
        agent_preset_service: AgentPresetService,
        agent_preset_create_params: AgentPresetCreate,
        registry_actions: list[RegistryAction],
    ) -> None:
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

    async def test_create_preset_with_stdio_mcp_forces_internet_access(
        self,
        session: AsyncSession,
        agent_preset_service: AgentPresetService,
        agent_preset_create_params: AgentPresetCreate,
    ) -> None:
        stdio_mcp = await _create_stdio_mcp_integration(
            session,
            agent_preset_service.workspace_id,
        )
        stdio_mcp_id = str(stdio_mcp.id)
        params = agent_preset_create_params.model_copy(
            update={
                "mcp_integrations": [stdio_mcp_id],
                "enable_internet_access": False,
            }
        )

        created_preset = await agent_preset_service.create_preset(params)
        current_version = await agent_preset_service.get_current_version_for_preset(
            created_preset
        )

        assert current_version.mcp_integrations == [stdio_mcp_id]
        assert current_version.enable_internet_access is True

    async def test_update_preset_attaching_stdio_mcp_forces_internet_access(
        self,
        session: AsyncSession,
        agent_preset_service: AgentPresetService,
        agent_preset_create_params: AgentPresetCreate,
    ) -> None:
        created_preset = await agent_preset_service.create_preset(
            agent_preset_create_params
        )
        stdio_mcp = await _create_stdio_mcp_integration(
            session,
            agent_preset_service.workspace_id,
        )
        stdio_mcp_id = str(stdio_mcp.id)

        updated_preset = await agent_preset_service.update_preset(
            created_preset,
            AgentPresetUpdate(
                mcp_integrations=[stdio_mcp_id],
                enable_internet_access=False,
            ),
        )
        current_version = await agent_preset_service.get_current_version_for_preset(
            updated_preset
        )

        assert current_version.mcp_integrations == [stdio_mcp_id]
        assert current_version.enable_internet_access is True
        assert current_version.version == 2

    async def test_update_preset_with_existing_stdio_mcp_cannot_disable_internet_access(
        self,
        session: AsyncSession,
        agent_preset_service: AgentPresetService,
        agent_preset_create_params: AgentPresetCreate,
    ) -> None:
        stdio_mcp = await _create_stdio_mcp_integration(
            session,
            agent_preset_service.workspace_id,
        )
        stdio_mcp_id = str(stdio_mcp.id)
        created_preset = await agent_preset_service.create_preset(
            agent_preset_create_params.model_copy(
                update={
                    "mcp_integrations": [stdio_mcp_id],
                    "enable_internet_access": False,
                }
            )
        )

        updated_preset = await agent_preset_service.update_preset(
            created_preset,
            AgentPresetUpdate(enable_internet_access=False),
        )
        current_version = await agent_preset_service.get_current_version_for_preset(
            updated_preset
        )
        versions = await agent_preset_service.list_versions(
            created_preset.id,
            CursorPaginationParams(limit=10),
        )

        assert current_version.enable_internet_access is True
        assert [version.version for version in versions.items] == [1]

    async def test_update_preset_unrelated_change_repairs_stdio_internet_access(
        self,
        session: AsyncSession,
        agent_preset_service: AgentPresetService,
        agent_preset_create_params: AgentPresetCreate,
    ) -> None:
        stdio_mcp = await _create_stdio_mcp_integration(
            session,
            agent_preset_service.workspace_id,
        )
        stdio_mcp_id = str(stdio_mcp.id)
        created_preset = await agent_preset_service.create_preset(
            agent_preset_create_params.model_copy(
                update={
                    "mcp_integrations": [stdio_mcp_id],
                    "enable_internet_access": False,
                }
            )
        )
        current_version = await agent_preset_service.get_current_version_for_preset(
            created_preset
        )

        current_version.enable_internet_access = False
        session.add(current_version)
        await session.commit()
        await session.refresh(created_preset)

        updated_preset = await agent_preset_service.update_preset(
            created_preset,
            AgentPresetUpdate(name="Renamed preset"),
        )
        new_current_version = await agent_preset_service.get_current_version_for_preset(
            updated_preset
        )

        assert updated_preset.name == "Renamed preset"
        assert new_current_version.mcp_integrations == [stdio_mcp_id]
        assert new_current_version.enable_internet_access is True
        assert new_current_version.version == 2

    async def test_update_preset_unrelated_change_tolerates_dangling_mcp_integration(
        self,
        session: AsyncSession,
        agent_preset_service: AgentPresetService,
        agent_preset_create_params: AgentPresetCreate,
    ) -> None:
        stdio_mcp = await _create_stdio_mcp_integration(
            session,
            agent_preset_service.workspace_id,
        )
        stdio_mcp_id = str(stdio_mcp.id)
        created_preset = await agent_preset_service.create_preset(
            agent_preset_create_params.model_copy(
                update={
                    "mcp_integrations": [stdio_mcp_id],
                    "enable_internet_access": False,
                }
            )
        )
        await session.delete(stdio_mcp)
        await session.flush()

        updated_preset = await agent_preset_service.update_preset(
            created_preset,
            AgentPresetUpdate(name="Renamed preset"),
        )
        current_version = await agent_preset_service.get_current_version_for_preset(
            updated_preset
        )

        assert updated_preset.name == "Renamed preset"
        assert current_version.mcp_integrations == [stdio_mcp_id]
        assert current_version.version == 1

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

        version = await agent_preset_service.get_current_version_for_preset(
            created_preset
        )
        assert version.model_provider == "custom-model-provider"
        assert version.model_name == "customer-alias"
        assert version.base_url is None

    async def test_create_preset_rejects_disabled_catalog_id(
        self,
        session: AsyncSession,
        svc_organization: Organization,
        agent_preset_service: AgentPresetService,
        agent_preset_create_params: AgentPresetCreate,
    ) -> None:
        catalog = AgentCatalog(
            organization_id=None,
            custom_provider_id=None,
            model_provider="openai",
            model_name="gpt-4.1",
            model_metadata={},
        )
        session.add(catalog)
        await session.commit()

        params = agent_preset_create_params.model_copy(
            update={"catalog_id": catalog.id}
        )
        with pytest.raises(
            TracecatValidationError,
            match=f"Catalog entry {catalog.id} is not enabled for this workspace",
        ):
            await agent_preset_service.create_preset(params)

    async def test_create_preset_allows_enabled_inherited_catalog_id(
        self,
        session: AsyncSession,
        svc_organization: Organization,
        agent_preset_service: AgentPresetService,
        agent_preset_create_params: AgentPresetCreate,
    ) -> None:
        catalog = AgentCatalog(
            organization_id=None,
            custom_provider_id=None,
            model_provider="openai",
            model_name="gpt-4.1",
            model_metadata={},
        )
        session.add(catalog)
        await session.flush()
        session.add(
            AgentModelAccess(
                organization_id=svc_organization.id,
                workspace_id=None,
                catalog_id=catalog.id,
            )
        )
        await session.commit()

        params = agent_preset_create_params.model_copy(
            update={
                "catalog_id": catalog.id,
                "model_name": catalog.model_name,
                "model_provider": catalog.model_provider,
            }
        )

        preset = await agent_preset_service.create_preset(params)

        version = await agent_preset_service.get_current_version_for_preset(preset)
        assert version.catalog_id == catalog.id
        assert version.model_name == catalog.model_name
        assert version.model_provider == catalog.model_provider

    async def test_create_preset_uses_catalog_model_fields_when_catalog_id_is_set(
        self,
        session: AsyncSession,
        svc_organization: Organization,
        agent_preset_service: AgentPresetService,
        agent_preset_create_params: AgentPresetCreate,
    ) -> None:
        catalog = AgentCatalog(
            organization_id=None,
            custom_provider_id=None,
            model_provider="openai",
            model_name="gpt-4.1",
            model_metadata={},
        )
        session.add(catalog)
        await session.flush()
        session.add(
            AgentModelAccess(
                organization_id=svc_organization.id,
                workspace_id=None,
                catalog_id=catalog.id,
            )
        )
        await session.commit()

        params = agent_preset_create_params.model_copy(
            update={
                "catalog_id": catalog.id,
                "model_name": "claude-sonnet-4-5",
                "model_provider": "anthropic",
            }
        )

        preset = await agent_preset_service.create_preset(params)

        version = await agent_preset_service.get_current_version_for_preset(preset)
        assert version.catalog_id == catalog.id
        assert version.model_name == catalog.model_name
        assert version.model_provider == catalog.model_provider

    async def test_build_version_read_includes_catalog_id(
        self,
        session: AsyncSession,
        svc_organization: Organization,
        agent_preset_service: AgentPresetService,
        agent_preset_create_params: AgentPresetCreate,
    ) -> None:
        catalog = AgentCatalog(
            organization_id=None,
            custom_provider_id=None,
            model_provider="openai",
            model_name="gpt-4.1",
            model_metadata={},
        )
        session.add(catalog)
        await session.flush()
        session.add(
            AgentModelAccess(
                organization_id=svc_organization.id,
                workspace_id=None,
                catalog_id=catalog.id,
            )
        )
        await session.commit()

        preset = await agent_preset_service.create_preset(
            agent_preset_create_params.model_copy(update={"catalog_id": catalog.id})
        )
        version = await agent_preset_service.get_current_version_for_preset(preset)

        assert version.catalog_id == catalog.id

        version_read = await agent_preset_service.build_version_read(version)

        assert version_read.catalog_id == catalog.id

    async def test_resolve_agent_preset_config_preserves_version_model_fields(
        self,
        session: AsyncSession,
        svc_organization: Organization,
        agent_preset_service: AgentPresetService,
        agent_preset_create_params: AgentPresetCreate,
    ) -> None:
        catalog = AgentCatalog(
            organization_id=None,
            custom_provider_id=None,
            model_provider="openai",
            model_name="gpt-4.1",
            model_metadata={},
        )
        session.add(catalog)
        await session.flush()
        session.add(
            AgentModelAccess(
                organization_id=svc_organization.id,
                workspace_id=None,
                catalog_id=catalog.id,
            )
        )
        await session.commit()

        params = agent_preset_create_params.model_copy(
            update={"catalog_id": catalog.id}
        )
        preset = await agent_preset_service.create_preset(params)
        version = (
            await session.execute(
                select(AgentPresetVersion).where(
                    AgentPresetVersion.id == preset.current_version_id
                )
            )
        ).scalar_one()
        version.model_name = "claude-sonnet-4-5"
        version.model_provider = "anthropic"
        session.add(version)
        await session.commit()

        config = await agent_preset_service.resolve_agent_preset_config(
            preset_id=preset.id
        )

        assert config.catalog_id == catalog.id
        assert config.model_name == "claude-sonnet-4-5"
        assert config.model_provider == "anthropic"

    async def test_update_preset_rejects_catalog_id_excluded_by_workspace_override(
        self,
        session: AsyncSession,
        svc_organization: Organization,
        svc_workspace: Workspace,
        agent_preset_service: AgentPresetService,
        agent_preset_create_params: AgentPresetCreate,
    ) -> None:
        inherited_catalog = AgentCatalog(
            organization_id=None,
            custom_provider_id=None,
            model_provider="openai",
            model_name="gpt-4.1",
            model_metadata={},
        )
        workspace_catalog = AgentCatalog(
            organization_id=None,
            custom_provider_id=None,
            model_provider="anthropic",
            model_name="claude-sonnet-4-5",
            model_metadata={},
        )
        session.add_all([inherited_catalog, workspace_catalog])
        await session.flush()
        session.add_all(
            [
                AgentModelAccess(
                    organization_id=svc_organization.id,
                    workspace_id=None,
                    catalog_id=inherited_catalog.id,
                ),
                AgentModelAccess(
                    organization_id=svc_organization.id,
                    workspace_id=svc_workspace.id,
                    catalog_id=workspace_catalog.id,
                ),
            ]
        )
        await session.commit()
        preset = await agent_preset_service.create_preset(agent_preset_create_params)

        with pytest.raises(
            TracecatValidationError,
            match=(
                f"Catalog entry {inherited_catalog.id} is not enabled "
                "for this workspace"
            ),
        ):
            await agent_preset_service.update_preset(
                preset,
                AgentPresetUpdate(catalog_id=inherited_catalog.id),
            )

        updated = await agent_preset_service.update_preset(
            preset,
            AgentPresetUpdate(catalog_id=workspace_catalog.id),
        )
        version = await agent_preset_service.get_current_version_for_preset(updated)
        assert version.catalog_id == workspace_catalog.id
        assert version.model_name == workspace_catalog.model_name
        assert version.model_provider == workspace_catalog.model_provider

        updated = await agent_preset_service.update_preset(
            updated,
            AgentPresetUpdate(
                model_name="gpt-4.1",
                model_provider="openai",
            ),
        )
        version = await agent_preset_service.get_current_version_for_preset(updated)
        assert version.catalog_id == workspace_catalog.id
        assert version.model_name == workspace_catalog.model_name
        assert version.model_provider == workspace_catalog.model_provider

    async def test_list_presets(
        self,
        agent_preset_service: AgentPresetService,
        agent_preset_create_params: AgentPresetCreate,
    ) -> None:
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
        created_preset = await agent_preset_service.create_preset(
            agent_preset_create_params
        )
        original_slug = created_preset.slug

        # The frontend sends the full execution payload on metadata edits.
        update_params = AgentPresetUpdate.model_validate(
            agent_preset_create_params.model_dump(exclude={"slug"})
            | {"name": "Updated Preset Name"}
        )
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

        versions = await agent_preset_service.list_versions(
            created_preset.id,
            CursorPaginationParams(limit=10),
        )
        assert [version.version for version in versions.items] == [2, 1]
        latest_version = await agent_preset_service.get_version(versions.items[0].id)
        assert latest_version is not None
        assert latest_version.enable_thinking is False
        preset_read = await agent_preset_service.build_preset_read(updated_preset)
        version_read = await agent_preset_service.build_version_read(latest_version)
        assert preset_read.enable_thinking is False
        assert version_read.enable_thinking is False

    async def test_create_preset_creates_initial_version(
        self,
        agent_preset_service: AgentPresetService,
        agent_preset_create_params: AgentPresetCreate,
    ) -> None:
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
        assert versions.items[0].id == updated_preset.current_version_id

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

    async def test_list_versions_exposes_subagent_eligibility(
        self,
        agent_preset_service: AgentPresetService,
        agent_preset_create_params: AgentPresetCreate,
    ) -> None:
        created_preset = await agent_preset_service.create_preset(
            agent_preset_create_params.model_copy(
                update={
                    "agents": AgentSubagentsConfig.model_validate(
                        {"enabled": True, "subagents": []}
                    )
                }
            )
        )

        versions = await agent_preset_service.list_versions(
            created_preset.id,
            CursorPaginationParams(limit=10),
        )

        assert len(versions.items) == 1
        version = versions.items[0]
        assert version.capabilities == ["subagents"]
        assert version.subagent_eligibility.eligible is False
        assert version.subagent_eligibility.reasons == ["agents_enabled"]
        assert version.subagent_eligibility.message is not None

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
                    version = await service.get_current_version_for_preset(updated)
                    return cast(str, version.instructions)

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

    async def test_compare_versions_keeps_legacy_slug_only_subagents(
        self,
        agent_preset_service: AgentPresetService,
        agent_preset_create_params: AgentPresetCreate,
    ) -> None:
        """Version diffs retain old-writer refs that predate ResourceHead IDs."""

        preset = await agent_preset_service.create_preset(agent_preset_create_params)
        legacy_version = await agent_preset_service.get_current_version_for_preset(
            preset
        )

        await agent_preset_service.update_preset(
            preset,
            AgentPresetUpdate(instructions="Create a comparison version"),
        )
        current_version = await agent_preset_service.get_current_version_for_preset(
            preset
        )

        legacy_version.agents = AgentSubagentsConfig(
            enabled=True,
            subagents=[
                AttachedSubagentRef(
                    preset="legacy-child",
                    name="legacy-alias",
                    description="Old-writer child",
                    max_turns=2,
                )
            ],
        ).model_dump(mode="json")

        diff = await agent_preset_service.compare_versions(
            legacy_version,
            current_version,
        )

        subagent_change = next(
            change for change in diff.scalar_changes if change.field == "subagents"
        )
        assert subagent_change.old_value == [
            {
                "preset_id": None,
                "preset": "legacy-child",
                "alias": "legacy-alias",
                "description": "Old-writer child",
                "max_turns": 2,
            }
        ]
        assert subagent_change.new_value == []

    async def test_compare_versions_reports_skill_head_attachment_changes(
        self,
        configure_minio_for_skills,
        session: AsyncSession,
        svc_role: Role,
        agent_preset_service: AgentPresetService,
    ) -> None:
        skill_service = SkillService(session=session, role=svc_role)
        skill = await skill_service.create_skill(SkillCreate(name="diff-skill"))
        published_skill = await skill_service.publish_skill(skill.id)
        preset = await agent_preset_service.create_preset(
            AgentPresetCreate(
                name="Skill diff preset",
                model_name="gpt-4o-mini",
                model_provider="openai",
            )
        )
        without_skill = await agent_preset_service.get_current_version_for_preset(
            preset
        )

        await agent_preset_service.update_preset(
            preset,
            AgentPresetUpdate(skills=[AgentPresetSkillBindingBase(skill_id=skill.id)]),
        )
        with_skill = await agent_preset_service.get_current_version_for_preset(preset)
        attached = await agent_preset_service.compare_versions(
            without_skill,
            with_skill,
        )
        version_edge = await session.scalar(
            select(AgentPresetVersionSkill).where(
                AgentPresetVersionSkill.preset_version_id == with_skill.id,
                AgentPresetVersionSkill.skill_id == skill.id,
            )
        )
        head_edge = await session.scalar(
            select(AgentPresetSkill).where(
                AgentPresetSkill.preset_id == preset.id,
                AgentPresetSkill.skill_id == skill.id,
            )
        )
        assert version_edge is not None
        assert head_edge is not None
        assert version_edge.skill_version_id == published_skill.id
        assert head_edge.skill_version_id == published_skill.id

        draft = await skill_service.get_draft(skill.id)
        assert draft is not None
        await skill_service.patch_draft(
            skill_id=skill.id,
            params=SkillDraftPatch(
                base_revision=draft.draft_revision,
                operations=[
                    SkillDraftUpsertTextFileOp(
                        path="SKILL.md",
                        content="---\nname: diff-skill-v2\n---\n\n# Diff skill v2\n",
                        content_type="text/markdown; charset=utf-8",
                    )
                ],
            ),
        )
        await skill_service.publish_skill(skill.id)

        await agent_preset_service.update_preset(
            preset,
            AgentPresetUpdate(instructions="Carry the existing skill edge"),
        )
        carried = await agent_preset_service.get_current_version_for_preset(preset)
        carried_bindings = await agent_preset_service._list_version_skill_bindings(
            carried.id
        )
        carried_read = await agent_preset_service.build_version_read(carried)
        assert carried_bindings[0].skill_id == skill.id
        assert carried_bindings[0].skill_name == "diff-skill"
        assert carried_read.skills[0].skill_name == "diff-skill"

        await agent_preset_service.update_preset(
            preset,
            AgentPresetUpdate(skills=None),
        )
        detached_version = await agent_preset_service.get_current_version_for_preset(
            preset
        )
        detached = await agent_preset_service.compare_versions(
            carried,
            detached_version,
        )

        assert [change.change_type for change in attached.skill_changes] == ["attached"]
        assert [change.change_type for change in detached.skill_changes] == ["detached"]

    async def test_create_preset_skill_binding_stores_version_edge(
        self,
        configure_minio_for_skills,
        session: AsyncSession,
        svc_role: Role,
        agent_preset_service: AgentPresetService,
    ) -> None:
        skill_service = SkillService(session=session, role=svc_role)
        created_skill = await skill_service.create_skill(
            SkillCreate(name="skill-only-current")
        )
        published_skill = await skill_service.publish_skill(created_skill.id)

        created_preset = await agent_preset_service.create_preset(
            AgentPresetCreate(
                name="Skill-only current preset",
                instructions="Use the selected skill",
                model_name="gpt-4o-mini",
                model_provider="openai",
                skills=[AgentPresetSkillBindingBase(skill_id=created_skill.id)],
            )
        )
        current_version = await agent_preset_service.get_current_version_for_preset(
            created_preset
        )
        version_bindings = await agent_preset_service._list_version_skill_bindings(
            current_version.id
        )
        version_read = await agent_preset_service.build_version_read(current_version)

        assert version_bindings[0].skill_id == created_skill.id
        assert version_read.skills[0].skill_id == created_skill.id
        assert version_read.skills[0].skill_name == "skill-only-current"
        version_edge = await session.scalar(
            select(AgentPresetVersionSkill).where(
                AgentPresetVersionSkill.preset_version_id == current_version.id,
                AgentPresetVersionSkill.skill_id == created_skill.id,
            )
        )
        head_edge = await session.scalar(
            select(AgentPresetSkill).where(
                AgentPresetSkill.preset_id == created_preset.id,
                AgentPresetSkill.skill_id == created_skill.id,
            )
        )
        assert version_edge is not None
        assert head_edge is not None
        assert version_edge.skill_version_id == published_skill.id
        assert head_edge.skill_version_id == published_skill.id
        assert created_preset.instructions == "Use the selected skill"
        assert created_preset.model_name == "gpt-4o-mini"
        assert created_preset.model_provider == "openai"

    async def test_version_agents_falls_back_to_legacy_json_without_edges(
        self,
        session: AsyncSession,
        agent_preset_service: AgentPresetService,
        agent_preset_create_params: AgentPresetCreate,
    ) -> None:
        child = await agent_preset_service.create_preset(
            agent_preset_create_params.model_copy(
                update={"name": "Epoch child", "slug": "epoch-child"}
            )
        )
        parent = await agent_preset_service.create_preset(
            agent_preset_create_params.model_copy(
                update={
                    "name": "Epoch parent",
                    "slug": "epoch-parent",
                    "agents": AgentSubagentsConfig.model_validate(
                        {
                            "enabled": True,
                            "subagents": [{"preset": child.slug}],
                        }
                    ),
                }
            )
        )
        version = await agent_preset_service.get_current_version_for_preset(parent)
        await session.execute(
            sa.delete(AgentPresetVersionSubagent).where(
                AgentPresetVersionSubagent.parent_preset_version_id == version.id
            )
        )
        session.add(version)
        await session.commit()

        legacy = await agent_preset_service._get_version_agents_config(version)
        assert legacy.enabled is True
        assert len(legacy.subagents) == 1
        assert legacy.subagents[0].preset == child.slug

        version.agents = AgentSubagentsConfig().model_dump(mode="json")
        session.add(version)
        await session.commit()
        authoritative = await agent_preset_service._get_version_agents_config(version)
        assert authoritative == AgentSubagentsConfig()

    async def test_client_supplied_stale_skill_version_is_ignored(
        self,
        configure_minio_for_skills,
        session: AsyncSession,
        svc_role: Role,
        agent_preset_service: AgentPresetService,
    ) -> None:
        skill_service = SkillService(session=session, role=svc_role)
        created_skill = await skill_service.create_skill(
            SkillCreate(name="ignore-stale-version")
        )
        stale_version = await skill_service.publish_skill(created_skill.id)
        draft = await skill_service.get_draft(created_skill.id)
        assert draft is not None
        await skill_service.patch_draft(
            skill_id=created_skill.id,
            params=SkillDraftPatch(
                base_revision=draft.draft_revision,
                operations=[
                    SkillDraftUpsertTextFileOp(
                        path="references/current.md",
                        content="Current published version",
                    )
                ],
            ),
        )
        current_published_version = await skill_service.publish_skill(created_skill.id)

        binding = AgentPresetSkillBindingBase.model_validate(
            {
                "skill_id": str(created_skill.id),
                "skill_version_id": str(stale_version.id),
            }
        )
        assert binding.model_dump(mode="json") == {"skill_id": str(created_skill.id)}

        created_preset = await agent_preset_service.create_preset(
            AgentPresetCreate(
                name="Ignore stale version preset",
                instructions="Use the current Skill version",
                model_name="gpt-4o-mini",
                model_provider="openai",
                skills=[binding],
            )
        )
        current_version = await agent_preset_service.get_current_version_for_preset(
            created_preset
        )
        version_bindings = await agent_preset_service._list_version_skill_bindings(
            current_version.id
        )
        version_read = await agent_preset_service.build_version_read(current_version)

        assert version_bindings[0].skill_id == created_skill.id
        assert version_read.skills[0].skill_id == created_skill.id
        assert current_published_version.id != stale_version.id

    async def test_create_preset_rejects_unpublished_skill_binding(
        self,
        session: AsyncSession,
        svc_role: Role,
        agent_preset_service: AgentPresetService,
    ) -> None:
        skill_service = SkillService(session=session, role=svc_role)
        created_skill = await skill_service.create_skill(
            SkillCreate(name="unpublished-binding")
        )

        with pytest.raises(TracecatValidationError) as exc_info:
            await agent_preset_service.create_preset(
                AgentPresetCreate(
                    name="Unpublished skill preset",
                    instructions="Use the selected skill",
                    model_name="gpt-4o-mini",
                    model_provider="openai",
                    skills=[AgentPresetSkillBindingBase(skill_id=created_skill.id)],
                )
            )

        detail = exc_info.value.detail
        assert detail is not None
        assert detail["code"] == "skill_not_published"
        assert detail["skill_id"] == str(created_skill.id)

    async def test_resolve_config_follows_skill_current_version(
        self,
        configure_minio_for_skills,
        session: AsyncSession,
        svc_role: Role,
        agent_preset_service: AgentPresetService,
    ) -> None:
        skill_service = SkillService(session=session, role=svc_role)
        created_skill = await skill_service.create_skill(
            SkillCreate(name="latest-skill-v1")
        )
        await skill_service.publish_skill(created_skill.id)

        created_preset = await agent_preset_service.create_preset(
            AgentPresetCreate(
                name="Latest skill preset",
                description="Preset that follows skill current versions",
                instructions="Use the selected skill",
                model_name="gpt-4o-mini",
                model_provider="openai",
                skills=[
                    AgentPresetSkillBindingBase(
                        skill_id=created_skill.id,
                    )
                ],
            )
        )

        draft = await skill_service.get_draft(created_skill.id)
        assert draft is not None
        await skill_service.patch_draft(
            skill_id=created_skill.id,
            params=SkillDraftPatch(
                base_revision=draft.draft_revision,
                operations=[
                    SkillDraftUpsertTextFileOp(
                        path="SKILL.md",
                        content=(
                            "---\nname: latest-skill-v2\n---\n\n# latest-skill-v2\n"
                        ),
                        content_type="text/markdown; charset=utf-8",
                    )
                ],
            ),
        )
        skill_version_two = await skill_service.publish_skill(created_skill.id)

        config = await agent_preset_service.resolve_agent_preset_config(
            preset_id=created_preset.id
        )

        assert config.resolved_skills is not None
        assert len(config.resolved_skills) == 1
        resolved_skill = config.resolved_skills[0]
        assert resolved_skill.skill_version_id == skill_version_two.id
        assert resolved_skill.skill_name == "latest-skill-v2"

    async def test_resolve_config_skips_archived_skill_head(
        self,
        configure_minio_for_skills,
        session: AsyncSession,
        svc_role: Role,
        agent_preset_service: AgentPresetService,
    ) -> None:
        """A historical preset edge skips a Skill head deleted after publication."""
        skill_service = SkillService(session=session, role=svc_role)
        created_skill = await skill_service.create_skill(
            SkillCreate(name="latest-archived-skill")
        )
        await skill_service.publish_skill(created_skill.id)
        created_preset = await agent_preset_service.create_preset(
            AgentPresetCreate(
                name="Latest archived skill preset",
                description="Preset with a historical skill binding",
                instructions="Use the selected skill",
                model_name="gpt-4o-mini",
                model_provider="openai",
                skills=[
                    AgentPresetSkillBindingBase(
                        skill_id=created_skill.id,
                    )
                ],
            )
        )
        historical_version = await agent_preset_service.get_current_version_for_preset(
            created_preset
        )
        await agent_preset_service.update_preset(
            created_preset,
            AgentPresetUpdate(skills=None),
        )
        await skill_service.archive_skill(created_skill.id)

        config = await agent_preset_service.resolve_agent_preset_config(
            preset_id=created_preset.id,
            preset_version_id=historical_version.id,
        )

        assert config.resolved_skills == []

    @pytest.mark.parametrize(
        "reclaim_slug", [False, True], ids=["unclaimed", "reclaimed"]
    )
    async def test_historical_deleted_skill_skips_without_relinking(
        self,
        configure_minio_for_skills,
        session: AsyncSession,
        svc_role: Role,
        agent_preset_service: AgentPresetService,
        reclaim_slug: bool,
    ) -> None:
        """Deleted UUID bindings skip safely without relinking by reused slug."""

        skill_service = SkillService(session=session, role=svc_role)
        deleted_skill = await skill_service.create_skill(
            SkillCreate(name="historical-deleted-skill")
        )
        await skill_service.publish_skill(deleted_skill.id)
        preset = await agent_preset_service.create_preset(
            AgentPresetCreate(
                name="Historical deleted skill preset",
                instructions="Use the selected skill",
                model_name="gpt-4o-mini",
                model_provider="openai",
                skills=[AgentPresetSkillBindingBase(skill_id=deleted_skill.id)],
            )
        )
        historical_version = await agent_preset_service.get_current_version_for_preset(
            preset
        )
        await agent_preset_service.update_preset(
            preset,
            AgentPresetUpdate(skills=None),
        )
        await skill_service.archive_skill(deleted_skill.id)
        if reclaim_slug:
            await skill_service.create_skill(
                SkillCreate(name="historical-deleted-skill")
            )

        config = await agent_preset_service.resolve_agent_preset_config(
            preset_id=preset.id,
            preset_version_id=historical_version.id,
        )

        assert config.resolved_skills == []

    async def test_list_versions_returns_metadata_without_skill_lookups(
        self,
        configure_minio_for_skills,
        session: AsyncSession,
        svc_role: Role,
        agent_preset_service: AgentPresetService,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        skill_service = SkillService(session=session, role=svc_role)
        created_skill = await skill_service.create_skill(
            SkillCreate(name="batched-skill")
        )
        await skill_service.publish_skill(created_skill.id)

        created_preset = await agent_preset_service.create_preset(
            AgentPresetCreate(
                name="Batched preset",
                description="Preset with batched version reads",
                instructions="Use the selected skill version",
                model_name="gpt-4o-mini",
                model_provider="openai",
                skills=[
                    AgentPresetSkillBindingBase(
                        skill_id=created_skill.id,
                    )
                ],
            )
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
        await skill_service.publish_skill(created_skill.id)

        await agent_preset_service.update_preset(
            created_preset,
            AgentPresetUpdate(
                instructions="Updated instructions",
                skills=[
                    AgentPresetSkillBindingBase(
                        skill_id=created_skill.id,
                    )
                ],
            ),
        )

        async def fail_single_version_lookup(
            version_id: uuid.UUID,
        ) -> list[AgentPresetSkillBindingRead]:
            del version_id
            raise AssertionError("list endpoint should batch skill binding lookups")

        monkeypatch.setattr(
            agent_preset_service,
            "_list_version_skill_bindings",
            fail_single_version_lookup,
        )

        version_reads = await agent_preset_service.list_versions(
            created_preset.id,
            CursorPaginationParams(limit=10),
        )

        assert [version.version for version in version_reads.items] == [2, 1]

    async def test_restore_version_rolls_forward_with_current_skill_heads(
        self,
        configure_minio_for_skills,
        session: AsyncSession,
        svc_role: Role,
        agent_preset_service: AgentPresetService,
    ) -> None:
        """Restoring mints N+1 while Skill edges follow their current heads."""

        skill_service = SkillService(session=session, role=svc_role)
        created_skill = await skill_service.create_skill(
            SkillCreate(name="restore-skill")
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
        restored_version = await agent_preset_service.get_current_version_for_preset(
            restored
        )
        restored_bindings = await agent_preset_service._list_version_skill_bindings(
            restored_version.id
        )

        assert diff.skill_changes == []
        assert len(restored_bindings) == 1
        assert restored_bindings[0].skill_id == created_skill.id
        assert restored_version.id not in {
            preset_version_one.id,
            preset_version_two.id,
        }
        assert restored_version.version == 3
        assert restored_version.instructions == preset_version_one.instructions

    async def test_build_version_read_uses_live_skill_head_name(
        self,
        configure_minio_for_skills,
        session: AsyncSession,
        svc_role: Role,
        agent_preset_service: AgentPresetService,
    ) -> None:
        skill_service = SkillService(session=session, role=svc_role)
        created_skill = await skill_service.create_skill(
            SkillCreate(name="version-one")
        )
        await skill_service.publish_skill(created_skill.id)

        created_preset = await agent_preset_service.create_preset(
            AgentPresetCreate(
                name="Snapshot skill name preset",
                instructions="Use the selected Skill",
                model_name="gpt-4o-mini",
                model_provider="openai",
                skills=[AgentPresetSkillBindingBase(skill_id=created_skill.id)],
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
                        path="SKILL.md",
                        content=("---\nname: version-two\n---\n\n# version-two\n"),
                        content_type="text/markdown; charset=utf-8",
                    )
                ],
            ),
        )
        await skill_service.publish_skill(created_skill.id)

        await agent_preset_service.update_preset(
            created_preset,
            AgentPresetUpdate(
                instructions="Snapshot the newer Skill version",
                skills=[AgentPresetSkillBindingBase(skill_id=created_skill.id)],
            ),
        )

        version_read = await agent_preset_service.build_version_read(preset_version_one)
        current_version = await agent_preset_service.get_current_version_for_preset(
            created_preset
        )
        current_bindings = await agent_preset_service._list_version_skill_bindings(
            current_version.id
        )

        assert version_read.skills[0].skill_id == created_skill.id
        assert version_read.skills[0].skill_name == "version-one"
        assert current_bindings[0].skill_id == created_skill.id
        assert current_bindings[0].skill_name == "version-one"

    async def test_create_preset_rejects_duplicate_bound_skill_names(
        self,
        configure_minio_for_skills,
        session: AsyncSession,
        svc_role: Role,
        agent_preset_service: AgentPresetService,
    ) -> None:
        skill_service = SkillService(session=session, role=svc_role)
        skill_a = await skill_service.create_skill(SkillCreate(name="shared-name"))
        await skill_service.publish_skill(skill_a.id)

        skill_b = await skill_service.create_skill(SkillCreate(name="skill-b-current"))
        skill_b_version = await skill_service.publish_skill(skill_b.id)
        skill_b_version_row = await skill_service.get_version(skill_b_version.id)
        assert skill_b_version_row is not None
        # Legacy data can have a unique head slug but a duplicate package name.
        skill_b_version_row.name = "shared-name"
        session.add(skill_b_version_row)
        await session.commit()

        with pytest.raises(
            TracecatValidationError,
            match="cannot include duplicate skill names",
        ) as exc_info:
            await agent_preset_service.create_preset(
                AgentPresetCreate(
                    name="Duplicate skill name preset",
                    instructions="Use both skills",
                    model_name="gpt-4o-mini",
                    model_provider="openai",
                    skills=[
                        AgentPresetSkillBindingBase(
                            skill_id=skill_a.id,
                        ),
                        AgentPresetSkillBindingBase(
                            skill_id=skill_b.id,
                        ),
                    ],
                )
            )

        detail = exc_info.value.detail
        assert detail is not None
        assert detail["code"] == "duplicate_skill_names"
        assert detail["skill_names"] == ["shared-name"]

    async def test_create_preset_allows_duplicate_display_names(
        self,
        configure_minio_for_skills,
        session: AsyncSession,
        svc_role: Role,
        agent_preset_service: AgentPresetService,
    ) -> None:
        skill_service = SkillService(session=session, role=svc_role)
        skill_a = await skill_service.create_skill(SkillCreate(name="shared-display"))
        await skill_service.publish_skill(skill_a.id)
        skill_b = await skill_service.create_skill(SkillCreate(name="shared-display"))
        draft_b = await skill_service.get_draft(skill_b.id)
        assert draft_b is not None
        await skill_service.patch_draft(
            skill_id=skill_b.id,
            params=SkillDraftPatch(
                base_revision=draft_b.draft_revision,
                operations=[
                    SkillDraftUpsertTextFileOp(
                        path="SKILL.md",
                        content="---\nname: shared-package-b\n---\n\n# package b\n",
                    )
                ],
            ),
        )
        await skill_service.publish_skill(skill_b.id)

        preset = await agent_preset_service.create_preset(
            AgentPresetCreate(
                name="Duplicate display preset",
                instructions="Use both skills",
                model_name="gpt-4o-mini",
                model_provider="openai",
                skills=[
                    AgentPresetSkillBindingBase(skill_id=skill_a.id),
                    AgentPresetSkillBindingBase(skill_id=skill_b.id),
                ],
            )
        )

        assert skill_a.name == skill_b.name == "shared-display"
        assert preset.current_version_id is not None

    async def test_resolve_agent_preset_config_rejects_duplicate_skill_names(
        self,
        configure_minio_for_skills,
        session: AsyncSession,
        svc_role: Role,
        agent_preset_service: AgentPresetService,
    ) -> None:
        skill_service = SkillService(session=session, role=svc_role)
        skill_a = await skill_service.create_skill(SkillCreate(name="shared-name"))
        await skill_service.publish_skill(skill_a.id)

        skill_b = await skill_service.create_skill(SkillCreate(name="skill-b-current"))
        skill_b_shared = await skill_service.publish_skill(skill_b.id)
        skill_b_shared_row = await skill_service.get_version(skill_b_shared.id)
        assert skill_b_shared_row is not None
        skill_b_shared_row.name = "shared-name"
        session.add(skill_b_shared_row)
        await session.commit()

        preset = await agent_preset_service.create_preset(
            AgentPresetCreate(
                name="Single skill preset",
                instructions="Use one skill",
                model_name="gpt-4o-mini",
                model_provider="openai",
                skills=[
                    AgentPresetSkillBindingBase(
                        skill_id=skill_a.id,
                    )
                ],
            )
        )
        preset_version = await agent_preset_service.get_current_version_for_preset(
            preset
        )

        session.add(
            AgentPresetVersionSkill(
                workspace_id=agent_preset_service.workspace_id,
                preset_version_id=preset_version.id,
                skill_id=skill_b.id,
            )
        )
        await session.commit()

        with pytest.raises(
            TracecatValidationError,
            match="Resolved preset version contains duplicate skill names",
        ) as exc_info:
            await agent_preset_service.resolve_agent_preset_config(
                preset_version_id=preset_version.id
            )

        assert exc_info.value.detail == {
            "code": "duplicate_skill_names",
            "skill_names": ["shared-name"],
            "preset_version_id": str(preset_version.id),
        }

    async def test_create_preset_locks_skill_bindings_during_validation(
        self,
        configure_minio_for_skills,
        session: AsyncSession,
        svc_role: Role,
        agent_preset_service: AgentPresetService,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        skill_service = SkillService(session=session, role=svc_role)
        created_skill = await skill_service.create_skill(
            SkillCreate(name="create-lock-skill")
        )
        await skill_service.publish_skill(created_skill.id)

        captured_for_update: list[bool] = []
        original_validated_skill_binding_ids = (
            agent_preset_service._validated_skill_binding_ids
        )

        async def instrumented_validated_skill_binding_ids(
            skill_ids: list[uuid.UUID],
            *,
            for_update: bool = False,
        ) -> list[uuid.UUID]:
            captured_for_update.append(for_update)
            return await original_validated_skill_binding_ids(
                skill_ids, for_update=for_update
            )

        monkeypatch.setattr(
            agent_preset_service,
            "_validated_skill_binding_ids",
            instrumented_validated_skill_binding_ids,
        )

        await agent_preset_service.create_preset(
            AgentPresetCreate(
                name="Create lock preset",
                description="Preset that validates skills under lock",
                instructions="Use the selected skill version",
                model_name="gpt-4o-mini",
                model_provider="openai",
                skills=[
                    AgentPresetSkillBindingBase(
                        skill_id=created_skill.id,
                    )
                ],
            )
        )

        assert captured_for_update == [True]

    async def test_update_preset_clears_all_skill_bindings_when_skills_is_null(
        self,
        configure_minio_for_skills,
        session: AsyncSession,
        svc_role: Role,
        agent_preset_service: AgentPresetService,
    ) -> None:
        skill_service = SkillService(session=session, role=svc_role)
        created_skill = await skill_service.create_skill(
            SkillCreate(name="clear-skill-bindings")
        )
        await skill_service.publish_skill(created_skill.id)

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
        current_bindings = await agent_preset_service._list_version_skill_bindings(
            current_version.id
        )
        version_read = await agent_preset_service.build_version_read(current_version)

        assert current_bindings == []
        assert version_read.skills == []

    async def test_update_preset_locks_skill_bindings_during_validation(
        self,
        configure_minio_for_skills,
        session: AsyncSession,
        svc_role: Role,
        agent_preset_service: AgentPresetService,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        skill_service = SkillService(session=session, role=svc_role)
        created_skill = await skill_service.create_skill(
            SkillCreate(name="update-lock-skill")
        )
        await skill_service.publish_skill(created_skill.id)

        created_preset = await agent_preset_service.create_preset(
            AgentPresetCreate(
                name="Update lock preset",
                description="Preset used to verify locking on update",
                instructions="Use the selected skill version",
                model_name="gpt-4o-mini",
                model_provider="openai",
            )
        )

        captured_for_update: list[bool] = []
        original_validated_skill_binding_ids = (
            agent_preset_service._validated_skill_binding_ids
        )

        async def instrumented_validated_skill_binding_ids(
            skill_ids: list[uuid.UUID],
            *,
            for_update: bool = False,
        ) -> list[uuid.UUID]:
            captured_for_update.append(for_update)
            return await original_validated_skill_binding_ids(
                skill_ids, for_update=for_update
            )

        monkeypatch.setattr(
            agent_preset_service,
            "_validated_skill_binding_ids",
            instrumented_validated_skill_binding_ids,
        )

        await agent_preset_service.update_preset(
            created_preset,
            AgentPresetUpdate(
                skills=[
                    AgentPresetSkillBindingBase(
                        skill_id=created_skill.id,
                    )
                ]
            ),
        )

        assert captured_for_update == [True]

    async def test_restore_version_rolls_forward_as_new_version(
        self,
        agent_preset_service: AgentPresetService,
        agent_preset_create_params: AgentPresetCreate,
    ) -> None:
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

        assert restored_preset.current_version_id != version_1.id
        restored_read = await agent_preset_service.build_preset_read(restored_preset)
        assert restored_read.instructions == agent_preset_create_params.instructions
        assert [version.version for version in versions.items] == [3, 2, 1]
        assert versions.items[0].id == restored_preset.current_version_id

    async def test_restore_version_rejects_deleted_mcp_integrations(
        self,
        session: AsyncSession,
        agent_preset_service: AgentPresetService,
        agent_preset_create_params: AgentPresetCreate,
    ) -> None:
        preset = await agent_preset_service.create_preset(agent_preset_create_params)
        version = await agent_preset_service.get_current_version_for_preset(preset)
        version.mcp_integrations = [str(uuid.uuid4())]
        session.add(version)
        await session.commit()

        with pytest.raises(TracecatValidationError, match="not found"):
            await agent_preset_service.restore_version(preset, version)

        await session.refresh(preset)
        assert preset.current_version_id == version.id

    async def test_restore_version_rejects_archived_skill_bindings(
        self,
        configure_minio_for_skills,
        session: AsyncSession,
        svc_role: Role,
        agent_preset_service: AgentPresetService,
    ) -> None:
        skill_service = SkillService(session=session, role=svc_role)
        created_skill = await skill_service.create_skill(
            SkillCreate(name="archived-restore-skill")
        )
        await skill_service.publish_skill(created_skill.id)

        created_preset = await agent_preset_service.create_preset(
            AgentPresetCreate(
                name="Archived restore preset",
                description="Preset that snapshots a skill before archiving",
                instructions="Use the selected skill version",
                model_name="gpt-4o-mini",
                model_provider="openai",
                skills=[
                    AgentPresetSkillBindingBase(
                        skill_id=created_skill.id,
                    )
                ],
            )
        )
        version_1 = await agent_preset_service.get_current_version_for_preset(
            created_preset
        )

        await agent_preset_service.update_preset(
            created_preset,
            AgentPresetUpdate(skills=None),
        )
        skill_row = (
            await session.execute(select(Skill).where(Skill.id == created_skill.id))
        ).scalar_one()
        archived_at = datetime.now(UTC)
        skill_row.deleted_at = archived_at
        await session.commit()

        with pytest.raises(TracecatValidationError, match="not found"):
            await agent_preset_service.restore_version(created_preset, version_1)

    async def test_restore_version_skips_soft_deleted_subagent_heads(
        self,
        agent_preset_service: AgentPresetService,
        agent_preset_create_params: AgentPresetCreate,
    ) -> None:
        child = await agent_preset_service.create_preset(
            agent_preset_create_params.model_copy(
                update={"name": "Restored Child", "slug": "restored-child"}
            )
        )
        parent = await agent_preset_service.create_preset(
            agent_preset_create_params.model_copy(
                update={
                    "name": "Restored Parent",
                    "slug": "restored-parent",
                    "agents": AgentSubagentsConfig.model_validate(
                        {
                            "enabled": True,
                            "subagents": [{"preset": child.slug}],
                        }
                    ),
                }
            )
        )
        version_with_child = await agent_preset_service.get_current_version_for_preset(
            parent
        )
        await agent_preset_service.update_preset(
            parent,
            AgentPresetUpdate(agents=AgentSubagentsConfig()),
        )
        version_without_child = (
            await agent_preset_service.get_current_version_for_preset(parent)
        )

        await agent_preset_service.delete_preset(child)

        restored = await agent_preset_service.restore_version(
            parent, version_with_child
        )

        await agent_preset_service.session.refresh(parent)
        assert parent.current_version_id == restored.current_version_id
        assert parent.current_version_id != version_without_child.id
        restored_read = await agent_preset_service.build_preset_read(parent)
        assert restored_read.agents == AgentSubagentsConfig(enabled=True)

    async def test_restore_version_binds_children_from_version_edges(
        self,
        session: AsyncSession,
        agent_preset_service: AgentPresetService,
        agent_preset_create_params: AgentPresetCreate,
    ) -> None:
        child = await agent_preset_service.create_preset(
            agent_preset_create_params.model_copy(
                update={"name": "Original Child", "slug": "stolen-slug"}
            )
        )
        parent = await agent_preset_service.create_preset(
            agent_preset_create_params.model_copy(
                update={
                    "name": "Edge Restore Parent",
                    "slug": "edge-restore-parent",
                    "agents": AgentSubagentsConfig.model_validate(
                        {
                            "enabled": True,
                            "subagents": [{"preset": child.slug}],
                        }
                    ),
                }
            )
        )
        version_with_child = await agent_preset_service.get_current_version_for_preset(
            parent
        )
        await agent_preset_service.update_preset(
            parent,
            AgentPresetUpdate(agents=AgentSubagentsConfig()),
        )
        # Move the child's slug and let another preset reclaim the old value;
        # the historical version edge still points at the original head ID.
        await session.execute(
            sa.update(AgentPreset)
            .where(AgentPreset.id == child.id)
            .values(slug="renamed-child")
            .execution_options(synchronize_session=False)
        )
        await session.commit()
        interloper = await agent_preset_service.create_preset(
            agent_preset_create_params.model_copy(
                update={"name": "Interloper", "slug": "stolen-slug"}
            )
        )

        restored = await agent_preset_service.restore_version(
            parent, version_with_child
        )
        restored_version = await agent_preset_service.get_current_version_for_preset(
            restored
        )
        current_children = (
            await session.scalars(
                select(AgentPresetVersionSubagent.child_preset_id).where(
                    AgentPresetVersionSubagent.parent_preset_version_id
                    == restored_version.id
                )
            )
        ).all()
        assert current_children == [child.id]
        assert interloper.id not in current_children

    async def test_restore_version_locks_skill_bindings_during_validation(
        self,
        configure_minio_for_skills,
        session: AsyncSession,
        svc_role: Role,
        agent_preset_service: AgentPresetService,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        skill_service = SkillService(session=session, role=svc_role)
        created_skill = await skill_service.create_skill(
            SkillCreate(name="restore-lock-skill")
        )
        await skill_service.publish_skill(created_skill.id)

        created_preset = await agent_preset_service.create_preset(
            AgentPresetCreate(
                name="Restore lock preset",
                description="Preset used to verify locking on restore",
                instructions="Use the selected skill version",
                model_name="gpt-4o-mini",
                model_provider="openai",
                skills=[
                    AgentPresetSkillBindingBase(
                        skill_id=created_skill.id,
                    )
                ],
            )
        )
        version_1 = await agent_preset_service.get_current_version_for_preset(
            created_preset
        )

        await agent_preset_service.update_preset(
            created_preset,
            AgentPresetUpdate(skills=None),
        )

        captured_for_update: list[bool] = []
        original_validated_skill_binding_ids = (
            agent_preset_service._validated_skill_binding_ids
        )

        async def instrumented_validated_skill_binding_ids(
            skill_ids: list[uuid.UUID],
            *,
            for_update: bool = False,
        ) -> list[uuid.UUID]:
            captured_for_update.append(for_update)
            return await original_validated_skill_binding_ids(
                skill_ids, for_update=for_update
            )

        monkeypatch.setattr(
            agent_preset_service,
            "_validated_skill_binding_ids",
            instrumented_validated_skill_binding_ids,
        )

        await agent_preset_service.restore_version(created_preset, version_1)

        assert captured_for_update == [True]

    async def test_update_preset_slug(
        self,
        agent_preset_service: AgentPresetService,
        agent_preset_create_params: AgentPresetCreate,
    ) -> None:
        created_preset = await agent_preset_service.create_preset(
            agent_preset_create_params
        )

        update_params = AgentPresetUpdate(slug="new-custom-slug")
        updated_preset = await agent_preset_service.update_preset(
            created_preset, update_params
        )

        assert updated_preset.slug == "new-custom-slug"

        retrieved = await agent_preset_service.get_preset_by_slug("new-custom-slug")
        assert retrieved is not None
        assert retrieved.id == created_preset.id

    async def test_update_preset_actions_valid(
        self,
        agent_preset_service: AgentPresetService,
        agent_preset_create_params: AgentPresetCreate,
        registry_actions: list[RegistryAction],
    ) -> None:
        agent_preset_create_params.actions = ["tools.test.test_action"]
        created_preset = await agent_preset_service.create_preset(
            agent_preset_create_params
        )

        update_params = AgentPresetUpdate(
            actions=["tools.test.another_action", "core.http_request"]
        )
        updated_preset = await agent_preset_service.update_preset(
            created_preset, update_params
        )
        updated_read = await agent_preset_service.build_preset_read(updated_preset)

        assert updated_read.actions == [
            "tools.test.another_action",
            "core.http_request",
        ]

    async def test_update_preset_actions_invalid(
        self,
        agent_preset_service: AgentPresetService,
        agent_preset_create_params: AgentPresetCreate,
    ) -> None:
        created_preset = await agent_preset_service.create_preset(
            agent_preset_create_params
        )

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
        agent_preset_create_params.actions = ["tools.test.test_action"]
        created_preset = await agent_preset_service.create_preset(
            agent_preset_create_params
        )

        update_params = AgentPresetUpdate(actions=[])
        updated_preset = await agent_preset_service.update_preset(
            created_preset, update_params
        )

        updated_read = await agent_preset_service.build_preset_read(updated_preset)
        assert updated_read.actions == []

    async def test_update_preset_multiple_fields(
        self,
        agent_preset_service: AgentPresetService,
        agent_preset_create_params: AgentPresetCreate,
    ) -> None:
        created_preset = await agent_preset_service.create_preset(
            agent_preset_create_params
        )

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
        updated_read = await agent_preset_service.build_preset_read(updated_preset)
        assert updated_read.instructions == "New instructions"
        assert updated_read.model_name == "gpt-4"
        assert updated_read.retries == 5

    async def test_delete_preset(
        self,
        agent_preset_service: AgentPresetService,
        agent_preset_create_params: AgentPresetCreate,
    ) -> None:
        """Deleting a preset soft-deletes it without deleting historical versions."""
        # Create preset
        created_preset = await agent_preset_service.create_preset(
            agent_preset_create_params
        )
        preset_id = created_preset.id
        version_id = created_preset.current_version_id
        assert version_id is not None

        # Delete preset
        await agent_preset_service.delete_preset(created_preset)

        deleted_preset = await agent_preset_service.get_preset(preset_id)
        soft_deleted_preset = await agent_preset_service.get_preset(
            preset_id,
            include_deleted=True,
        )
        version = await agent_preset_service.get_version(version_id)
        active_version = await agent_preset_service.get_active_version(
            preset_id=preset_id,
            version_id=version_id,
        )

        assert deleted_preset is None
        assert soft_deleted_preset is not None
        assert soft_deleted_preset.deleted_at is not None
        assert version is not None
        assert active_version is None

    async def test_resolve_exact_version_rejects_soft_deleted_preset(
        self,
        agent_preset_service: AgentPresetService,
        agent_preset_create_params: AgentPresetCreate,
    ) -> None:
        """Exact replay resolution should not bypass soft-deleted preset state."""
        created_preset = await agent_preset_service.create_preset(
            agent_preset_create_params
        )
        preset_id = created_preset.id
        version_id = created_preset.current_version_id
        assert version_id is not None

        await agent_preset_service.delete_preset(created_preset)

        with pytest.raises(
            TracecatNotFoundError,
            match=f"Agent preset '{preset_id}' not found",
        ):
            await agent_preset_service.resolve_agent_preset_config(
                preset_id=preset_id,
                preset_version_id=version_id,
            )
        with pytest.raises(
            TracecatNotFoundError,
            match=f"Agent preset version with ID '{version_id}' not found",
        ):
            await agent_preset_service.resolve_agent_preset_config(
                preset_version_id=version_id,
            )

    async def test_delete_preset_deactivates_channel_tokens(
        self,
        agent_preset_service: AgentPresetService,
        agent_preset_create_params: AgentPresetCreate,
    ) -> None:
        """Soft-deleting a preset should disable external channel ingress."""
        created_preset = await agent_preset_service.create_preset(
            agent_preset_create_params
        )
        token = AgentChannelToken(
            workspace_id=agent_preset_service.workspace_id,
            agent_preset_id=created_preset.id,
            channel_type="slack",
            config={},
            is_active=True,
        )
        agent_preset_service.session.add(token)
        await agent_preset_service.session.commit()

        await agent_preset_service.delete_preset(created_preset)

        await agent_preset_service.session.refresh(token)
        assert token.is_active is False

    async def test_delete_preset_removes_pending_slack_channel_tokens(
        self,
        agent_preset_service: AgentPresetService,
        agent_preset_create_params: AgentPresetCreate,
        svc_role: Role,
    ) -> None:
        """Soft-deleting a preset should cancel unfinished Slack OAuth installs."""
        created_preset = await agent_preset_service.create_preset(
            agent_preset_create_params
        )
        channel_service = AgentChannelService(
            agent_preset_service.session, role=svc_role
        )
        pending_token = await channel_service.create_token(
            AgentChannelTokenCreate(
                agent_preset_id=created_preset.id,
                channel_type=ChannelType.SLACK,
                config=SlackChannelTokenConfig(
                    slack_bot_token=PENDING_SLACK_BOT_TOKEN,
                    slack_client_id="client-id",
                    slack_client_secret="client-secret",
                    slack_signing_secret="signing-secret",
                ),
                is_active=False,
            )
        )
        inactive_token = await channel_service.create_token(
            AgentChannelTokenCreate(
                agent_preset_id=created_preset.id,
                channel_type=ChannelType.SLACK,
                config=SlackChannelTokenConfig(
                    slack_bot_token="xoxb-existing-token",
                    slack_client_id="client-id",
                    slack_client_secret="client-secret",
                    slack_signing_secret="signing-secret",
                ),
                is_active=False,
            )
        )
        assert pending_token.config["slack_bot_token"] != PENDING_SLACK_BOT_TOKEN

        await agent_preset_service.delete_preset(created_preset)

        deleted_pending = await agent_preset_service.session.scalar(
            select(AgentChannelToken).where(AgentChannelToken.id == pending_token.id)
        )
        remaining_inactive = await agent_preset_service.session.scalar(
            select(AgentChannelToken).where(AgentChannelToken.id == inactive_token.id)
        )
        assert deleted_pending is None
        assert remaining_inactive is not None
        assert remaining_inactive.is_active is False

    async def test_update_preset_rejects_soft_deleted_preset(
        self,
        agent_preset_service: AgentPresetService,
        agent_preset_create_params: AgentPresetCreate,
    ) -> None:
        """Soft-deleted presets cannot be mutated through a stale model instance."""
        created_preset = await agent_preset_service.create_preset(
            agent_preset_create_params
        )
        await agent_preset_service.delete_preset(created_preset)

        with pytest.raises(TracecatNotFoundError, match="not found"):
            await agent_preset_service.update_preset(
                created_preset,
                AgentPresetUpdate(name="Soft-deleted update"),
            )

    async def test_restore_version_rejects_soft_deleted_preset(
        self,
        agent_preset_service: AgentPresetService,
        agent_preset_create_params: AgentPresetCreate,
    ) -> None:
        """Soft-deleted presets cannot be restored through a stale model instance."""
        created_preset = await agent_preset_service.create_preset(
            agent_preset_create_params
        )
        current_version = await agent_preset_service.get_current_version_for_preset(
            created_preset
        )
        await agent_preset_service.delete_preset(created_preset)

        with pytest.raises(TracecatNotFoundError, match="not found"):
            await agent_preset_service.restore_version(created_preset, current_version)

    async def test_create_version_rejects_soft_deleted_preset(
        self,
        agent_preset_service: AgentPresetService,
        agent_preset_create_params: AgentPresetCreate,
    ) -> None:
        """Version creation refuses soft-deleted preset heads."""
        created_preset = await agent_preset_service.create_preset(
            agent_preset_create_params
        )
        await agent_preset_service.delete_preset(created_preset)

        with pytest.raises(TracecatNotFoundError, match="not found"):
            await agent_preset_service.create_version_from_current(created_preset)

    async def test_delete_preset_locks_target_before_subagent_reference_check(
        self,
        agent_preset_service: AgentPresetService,
        agent_preset_create_params: AgentPresetCreate,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Preset deletion serializes with restore before checking active refs."""
        created_preset = await agent_preset_service.create_preset(
            agent_preset_create_params
        )
        call_order: list[str] = []
        original_lock = agent_preset_service._lock_preset_row
        original_ensure = agent_preset_service._ensure_not_referenced_as_subagent

        async def instrumented_lock(preset_id: uuid.UUID) -> AgentPreset:
            call_order.append("lock")
            return await original_lock(preset_id)

        async def instrumented_ensure(preset: AgentPreset) -> None:
            call_order.append("reference_check")
            await original_ensure(preset)

        monkeypatch.setattr(
            agent_preset_service,
            "_lock_preset_row",
            instrumented_lock,
        )
        monkeypatch.setattr(
            agent_preset_service,
            "_ensure_not_referenced_as_subagent",
            instrumented_ensure,
        )

        await agent_preset_service.delete_preset(created_preset)

        assert call_order[:2] == ["lock", "reference_check"]
        assert call_order.count("lock") == 1

    async def test_delete_preset_blocks_when_referenced_as_subagent_in_head(
        self,
        agent_preset_service: AgentPresetService,
        agent_preset_create_params: AgentPresetCreate,
    ) -> None:
        """Deleting a preset is blocked while another preset head references it."""
        child = await agent_preset_service.create_preset(
            agent_preset_create_params.model_copy(
                update={"name": "Child Agent", "slug": "child-agent"}
            )
        )
        await agent_preset_service.create_preset(
            agent_preset_create_params.model_copy(
                update={
                    "name": "Parent Agent",
                    "slug": "parent-agent",
                    "agents": AgentSubagentsConfig.model_validate(
                        {
                            "enabled": True,
                            "subagents": [{"preset": child.slug}],
                        }
                    ),
                }
            )
        )

        with pytest.raises(
            TracecatValidationError,
            match="still referenced as a subagent",
        ) as exc_info:
            await agent_preset_service.delete_preset(child)

        assert exc_info.value.detail == {
            "code": "preset_in_use_as_subagent",
            "head_reference_count": 1,
        }
        assert await agent_preset_service.get_preset(child.id) is not None

    @pytest.mark.parametrize(
        ("legacy_subagents", "blocks_delete"),
        [
            pytest.param("child-ref", True, id="child-ref"),
            pytest.param(None, False, id="json-null"),
            pytest.param("invalid-scalar", False, id="non-array"),
        ],
    )
    async def test_delete_preset_handles_late_legacy_current_version(
        self,
        session: AsyncSession,
        agent_preset_service: AgentPresetService,
        agent_preset_create_params: AgentPresetCreate,
        legacy_subagents: str | None,
        blocks_delete: bool,
    ) -> None:
        """Legacy JSON protects real refs and tolerates non-array values."""

        child = await agent_preset_service.create_preset(
            agent_preset_create_params.model_copy(
                update={"name": "Legacy child", "slug": "legacy-child"}
            )
        )
        parent = await agent_preset_service.create_preset(
            agent_preset_create_params.model_copy(
                update={
                    "name": "Legacy parent",
                    "slug": "legacy-parent",
                    "agents": AgentSubagentsConfig.model_validate(
                        {
                            "enabled": True,
                            "subagents": [{"preset": child.slug}],
                        }
                    ),
                }
            )
        )
        version = await agent_preset_service.get_current_version_for_preset(parent)
        await session.execute(
            sa.delete(AgentPresetVersionSubagent).where(
                AgentPresetVersionSubagent.parent_preset_version_id == version.id
            )
        )
        version.agents = {
            "enabled": True,
            "subagents": (
                [{"preset": child.slug}]
                if legacy_subagents == "child-ref"
                else legacy_subagents
            ),
        }
        session.add(version)
        await session.commit()

        if blocks_delete:
            with pytest.raises(TracecatValidationError, match="still referenced"):
                await agent_preset_service.delete_preset(child)
            return

        await agent_preset_service.delete_preset(child)
        assert await agent_preset_service.get_preset(child.id) is None

    async def test_create_version_normalizes_late_legacy_subagent_refs(
        self,
        session: AsyncSession,
        agent_preset_service: AgentPresetService,
        agent_preset_create_params: AgentPresetCreate,
    ) -> None:
        """New writers turn a late old-writer JSON ref into a head edge."""

        child = await agent_preset_service.create_preset(
            agent_preset_create_params.model_copy(
                update={"name": "Legacy child", "slug": "legacy-child"}
            )
        )
        parent = await agent_preset_service.create_preset(
            agent_preset_create_params.model_copy(
                update={"name": "Legacy parent", "slug": "legacy-parent"}
            )
        )
        current = await agent_preset_service.get_current_version_for_preset(parent)
        await session.execute(
            sa.delete(AgentPresetVersionSubagent).where(
                AgentPresetVersionSubagent.parent_preset_version_id == current.id
            )
        )
        current.agents = {
            "enabled": True,
            "subagents": [{"preset": child.slug}],
        }
        session.add(current)
        await session.commit()

        created = await agent_preset_service.create_version_from_current(parent)

        edge = await session.scalar(
            select(AgentPresetVersionSubagent).where(
                AgentPresetVersionSubagent.parent_preset_version_id == created.id
            )
        )
        assert edge is not None
        assert edge.child_preset_id == child.id
        assert edge.alias == child.slug

        child.deleted_at = datetime.now(UTC)
        await session.flush()
        with pytest.raises(
            TracecatValidationError,
            match="Cannot create version.*unavailable subagent presets",
        ):
            await agent_preset_service.create_version_from_current(
                parent,
                current=current,
            )

    async def test_delete_preset_soft_deletes_when_only_referenced_as_subagent_in_history(
        self,
        agent_preset_service: AgentPresetService,
        agent_preset_create_params: AgentPresetCreate,
    ) -> None:
        """Historical head edges do not block delete and skip missing children."""
        child = await agent_preset_service.create_preset(
            agent_preset_create_params.model_copy(
                update={"name": "Historical Child", "slug": "historical-child"}
            )
        )
        parent = await agent_preset_service.create_preset(
            agent_preset_create_params.model_copy(
                update={
                    "name": "Historical Parent",
                    "slug": "historical-parent",
                    "agents": AgentSubagentsConfig.model_validate(
                        {
                            "enabled": True,
                            "subagents": [{"preset": child.slug}],
                        }
                    ),
                }
            )
        )
        parent_v1 = await agent_preset_service.get_current_version_for_preset(parent)
        await agent_preset_service.update_preset(
            parent, AgentPresetUpdate(agents=AgentSubagentsConfig())
        )

        await agent_preset_service.delete_preset(child)

        assert await agent_preset_service.get_preset(child.id) is None
        resolution = await resolve_agents_config(
            agent_preset_service,
            agents=await agent_preset_service._get_version_agents_config(parent_v1),
            parent_preset_id=parent.id,
            parent_slug=parent.slug,
            include_runtime_config=True,
        )
        assert resolution.enabled is True
        assert resolution.subagents == []
        assert len(resolution.skipped) == 1
        assert resolution.skipped[0].preset_id == child.id
        assert resolution.skipped[0].reason == "deleted"

        with pytest.raises(TracecatValidationError):
            await agent_preset_service.create_preset(
                agent_preset_create_params.model_copy(
                    update={
                        "name": "New Parent",
                        "slug": "new-parent",
                        "agents": AgentSubagentsConfig.model_validate(
                            {
                                "enabled": True,
                                "subagents": [{"preset": child.slug}],
                            }
                        ),
                    }
                )
            )

    async def test_resolve_agents_config_skips_only_failed_flat_tree_node(
        self,
        agent_preset_service: AgentPresetService,
        agent_preset_create_params: AgentPresetCreate,
    ) -> None:
        """Invariant: per-node failures skip only the failed node."""

        live_child = await agent_preset_service.create_preset(
            agent_preset_create_params.model_copy(
                update={"name": "Live Child", "slug": "live-child"}
            )
        )
        deleted_child = await agent_preset_service.create_preset(
            agent_preset_create_params.model_copy(
                update={"name": "Deleted Child", "slug": "deleted-child"}
            )
        )
        parent = await agent_preset_service.create_preset(
            agent_preset_create_params.model_copy(
                update={
                    "name": "Flat Tree Parent",
                    "slug": "flat-tree-parent",
                    "agents": AgentSubagentsConfig.model_validate(
                        {
                            "enabled": True,
                            "subagents": [
                                {"preset": live_child.slug},
                                {"preset": deleted_child.slug},
                            ],
                        }
                    ),
                }
            )
        )
        parent_v1 = await agent_preset_service.get_current_version_for_preset(parent)
        await agent_preset_service.update_preset(
            parent, AgentPresetUpdate(agents=AgentSubagentsConfig())
        )
        await agent_preset_service.delete_preset(deleted_child)

        resolved = await resolve_agents_config(
            agent_preset_service,
            agents=await agent_preset_service._get_version_agents_config(parent_v1),
            parent_preset_id=parent.id,
            parent_slug=parent.slug,
            include_runtime_config=True,
        )

        assert len(resolved.subagents) == 1
        assert resolved.subagents[0].binding.preset_id == live_child.id
        assert len(resolved.skipped) == 1
        assert resolved.skipped[0].preset_id == deleted_child.id

    async def test_delete_preset_ignores_resolved_reference_with_reused_slug(
        self,
        agent_preset_service: AgentPresetService,
        agent_preset_create_params: AgentPresetCreate,
    ) -> None:
        """Resolved refs use preset_id, so stale slugs do not block a new preset."""
        original_child = await agent_preset_service.create_preset(
            agent_preset_create_params.model_copy(
                update={"name": "Original Child", "slug": "reused-child"}
            )
        )
        await agent_preset_service.create_preset(
            agent_preset_create_params.model_copy(
                update={
                    "name": "Referencing Parent",
                    "slug": "referencing-parent",
                    "agents": AgentSubagentsConfig.model_validate(
                        {
                            "enabled": True,
                            "subagents": [{"preset": original_child.slug}],
                        }
                    ),
                }
            )
        )
        await agent_preset_service.update_preset(
            original_child,
            AgentPresetUpdate(slug="renamed-child"),
        )
        new_child = await agent_preset_service.create_preset(
            agent_preset_create_params.model_copy(
                update={"name": "New Child", "slug": "reused-child"}
            )
        )

        await agent_preset_service.delete_preset(new_child)

        assert await agent_preset_service.get_preset(new_child.id) is None
        assert (
            await agent_preset_service.get_preset(
                new_child.id,
                include_deleted=True,
            )
            is not None
        )
        with pytest.raises(
            TracecatValidationError,
            match="still referenced as a subagent",
        ):
            await agent_preset_service.delete_preset(original_child)

    async def test_get_preset_by_slug(
        self,
        agent_preset_service: AgentPresetService,
        agent_preset_create_params: AgentPresetCreate,
    ) -> None:
        created_preset = await agent_preset_service.create_preset(
            agent_preset_create_params
        )

        retrieved = await agent_preset_service.get_preset_by_slug("test-agent-preset")
        assert retrieved is not None
        assert retrieved.id == created_preset.id
        assert retrieved.slug == "test-agent-preset"

    async def test_get_preset_by_slug_not_found(
        self, agent_preset_service: AgentPresetService
    ) -> None:
        retrieved = await agent_preset_service.get_preset_by_slug("nonexistent-slug")
        assert retrieved is None

    async def test_unique_slug_per_workspace(
        self,
        agent_preset_service: AgentPresetService,
        agent_preset_create_params: AgentPresetCreate,
    ) -> None:
        await agent_preset_service.create_preset(agent_preset_create_params)

        with pytest.raises(
            TracecatValidationError,
            match="Agent preset slug 'test-agent-preset' is already in use",
        ):
            await agent_preset_service.create_preset(agent_preset_create_params)

    async def test_soft_deleted_preset_releases_slug(
        self,
        agent_preset_service: AgentPresetService,
        agent_preset_create_params: AgentPresetCreate,
    ) -> None:
        """Soft-deleted presets should not reserve slugs for new presets."""
        deleted = await agent_preset_service.create_preset(agent_preset_create_params)
        await agent_preset_service.delete_preset(deleted)

        recreated = await agent_preset_service.create_preset(agent_preset_create_params)

        assert recreated.id != deleted.id
        assert recreated.slug == deleted.slug
        assert await agent_preset_service.get_preset(deleted.id) is None

    async def test_slug_normalization(
        self,
        agent_preset_service: AgentPresetService,
        agent_preset_create_params: AgentPresetCreate,
    ) -> None:
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

            await agent_preset_service.delete_preset(preset)

    async def test_empty_slug_raises_error(
        self,
        agent_preset_service: AgentPresetService,
        agent_preset_create_params: AgentPresetCreate,
    ) -> None:
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
        with pytest.raises(TracecatNotFoundError, match="Agent preset '.*' not found"):
            await agent_preset_service.resolve_agent_preset_config(
                preset_id=uuid.uuid4()
            )

    async def test_resolve_agent_preset_config_by_slug_not_found(
        self, agent_preset_service: AgentPresetService
    ) -> None:
        with pytest.raises(
            TracecatNotFoundError,
            match="Agent preset 'nonexistent' not found",
        ):
            await agent_preset_service.resolve_agent_preset_config(slug="nonexistent")

    async def test_resolve_agent_preset_config_no_params_raises_error(
        self, agent_preset_service: AgentPresetService
    ) -> None:
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
        created_preset = await agent_preset_service.create_preset(
            agent_preset_create_params
        )

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
        preset1 = await agent_preset_service.create_preset(agent_preset_create_params)

        params2 = agent_preset_create_params.model_copy(deep=True)
        params2.name = "Second Preset"
        preset2 = await agent_preset_service.create_preset(params2)

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
        agent_preset_create_params.actions = ["tools.test.test_action"]
        agent_preset_create_params.namespaces = ["tools.test", "core"]
        agent_preset_create_params.output_type = "list[str]"
        agent_preset_create_params.tool_approvals = {"tools.test.test_action": False}

        preset = await agent_preset_service.create_preset(agent_preset_create_params)
        version = await agent_preset_service.get_current_version_for_preset(preset)

        agent_config = await agent_preset_service._version_to_agent_config(version)

        assert isinstance(agent_config, AgentConfig)
        assert agent_config.model_name == version.model_name
        assert agent_config.model_provider == version.model_provider
        assert agent_config.base_url == version.base_url
        assert agent_config.instructions == version.instructions
        assert agent_config.output_type == version.output_type
        assert agent_config.actions == version.actions
        assert agent_config.namespaces == version.namespaces
        assert agent_config.tool_approvals == version.tool_approvals
        assert agent_config.retries == version.retries
        assert agent_config.model_settings == {"parallel_tool_calls": False}

    async def test_create_preset_with_tool_approvals(
        self,
        agent_preset_service: AgentPresetService,
        agent_preset_create_params: AgentPresetCreate,
        registry_actions: list[RegistryAction],
    ) -> None:
        agent_preset_create_params.actions = ["tools.test.test_action"]
        agent_preset_create_params.tool_approvals = {"tools.test.test_action": True}

        preset = await agent_preset_service.create_preset(agent_preset_create_params)
        preset_read = await agent_preset_service.build_preset_read(preset)
        assert preset_read.tool_approvals == {"tools.test.test_action": True}

    async def test_create_parent_rejects_subagent_with_tool_approvals(
        self,
        agent_preset_service: AgentPresetService,
        agent_preset_create_params: AgentPresetCreate,
        registry_actions: list[RegistryAction],
    ) -> None:
        """Preset-backed subagents cannot require manual approval in v1."""
        child_params = agent_preset_create_params.model_copy(
            update={
                "name": "Approval Child",
                "slug": "approval-child",
                "actions": ["tools.test.test_action"],
                "tool_approvals": {"tools.test.test_action": True},
            }
        )
        child = await agent_preset_service.create_preset(child_params)

        parent_params = agent_preset_create_params.model_copy(
            update={
                "name": "Parent Agent",
                "slug": "parent-agent",
                "agents": AgentSubagentsConfig.model_validate(
                    {
                        "enabled": True,
                        "subagents": [{"preset": child.slug}],
                    }
                ),
            }
        )

        with pytest.raises(
            TracecatValidationError,
            match=(
                "Subagent preset 'approval-child' uses manual approvals, "
                "which are not supported for subagents yet."
            ),
        ):
            await agent_preset_service.create_preset(parent_params)

    async def test_create_parent_rechecks_subagent_before_saving_head(
        self,
        agent_preset_service: AgentPresetService,
        agent_preset_create_params: AgentPresetCreate,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Subagent children must still be active when the parent head is saved."""
        child = await agent_preset_service.create_preset(
            agent_preset_create_params.model_copy(
                update={"name": "Race Child", "slug": "race-child"}
            )
        )
        original_lock = agent_preset_service._lock_active_subagent_presets

        async def soft_delete_child_then_lock(agents: ResolvedAgentsConfig) -> None:
            child.deleted_at = datetime.now(UTC)
            agent_preset_service.session.add(child)
            await agent_preset_service.session.flush()
            await original_lock(agents)

        monkeypatch.setattr(
            agent_preset_service,
            "_lock_active_subagent_presets",
            soft_delete_child_then_lock,
        )

        with pytest.raises(
            TracecatValidationError,
            match="soft-deleted or missing subagent",
        ):
            await agent_preset_service.create_preset(
                agent_preset_create_params.model_copy(
                    update={
                        "name": "Race Parent",
                        "slug": "race-parent",
                        "agents": AgentSubagentsConfig.model_validate(
                            {
                                "enabled": True,
                                "subagents": [{"preset": child.slug}],
                            }
                        ),
                    }
                )
            )

    async def test_create_parent_rejects_child_with_own_subagent_edges(
        self,
        session: AsyncSession,
        agent_preset_service: AgentPresetService,
        agent_preset_create_params: AgentPresetCreate,
    ) -> None:
        """The v1 nested-subagent ban reads version-owned edges."""
        grandchild = await agent_preset_service.create_preset(
            agent_preset_create_params.model_copy(
                update={"name": "Drift Grandchild", "slug": "drift-grandchild"}
            )
        )
        child = await agent_preset_service.create_preset(
            agent_preset_create_params.model_copy(
                update={"name": "Drift Child", "slug": "drift-child"}
            )
        )
        child_version = await agent_preset_service.get_current_version_for_preset(child)
        session.add(child_version)
        await session.execute(
            sa.insert(AgentPresetVersionSubagent).values(
                workspace_id=agent_preset_service.workspace_id,
                parent_preset_version_id=child_version.id,
                child_preset_id=grandchild.id,
                alias="drifted",
            )
        )
        await session.flush()

        with pytest.raises(
            TracecatValidationError,
            match="cannot define its own agents in v1",
        ):
            await agent_preset_service.create_preset(
                agent_preset_create_params.model_copy(
                    update={
                        "name": "Drift Parent",
                        "slug": "drift-parent",
                        "agents": AgentSubagentsConfig.model_validate(
                            {
                                "enabled": True,
                                "subagents": [{"preset": child.slug}],
                            }
                        ),
                    }
                )
            )

    async def test_create_parent_uses_subagent_head_id_across_slug_rename(
        self,
        agent_preset_service: AgentPresetService,
        agent_preset_create_params: AgentPresetCreate,
    ) -> None:
        """Subagent refs compare stable head IDs instead of stale slugs."""
        child = await agent_preset_service.create_preset(
            agent_preset_create_params.model_copy(
                update={"name": "Original Child", "slug": "reused-slug"}
            )
        )
        await agent_preset_service.update_preset(
            child,
            AgentPresetUpdate(slug="renamed-child"),
        )

        parent = await agent_preset_service.create_preset(
            agent_preset_create_params.model_copy(
                update={
                    "name": "Parent Agent",
                    "slug": "reused-slug",
                    "agents": AgentSubagentsConfig.model_validate(
                        {
                            "enabled": True,
                            "subagents": [
                                {
                                    "preset": "reused-slug",
                                    "preset_id": child.id,
                                }
                            ],
                        }
                    ),
                }
            )
        )

        parent_version = await agent_preset_service.get_current_version_for_preset(
            parent
        )
        agents = await agent_preset_service._get_version_agents_config(parent_version)
        assert agents.enabled is True
        assert isinstance(agents.subagents[0], HeadAttachedSubagentRef)
        assert agents.subagents[0].preset_id == child.id

    async def test_resolve_config_keeps_head_ref_until_dispatch(
        self,
        agent_preset_service: AgentPresetService,
        agent_preset_create_params: AgentPresetCreate,
    ) -> None:
        """Root config stays head-based; dispatch resolves the child once."""
        child = await agent_preset_service.create_preset(
            agent_preset_create_params.model_copy(
                update={
                    "name": "Latest Child",
                    "slug": "latest-child",
                    "instructions": "Child v1",
                }
            )
        )
        child_version_one = await agent_preset_service.get_current_version_for_preset(
            child
        )
        parent = await agent_preset_service.create_preset(
            agent_preset_create_params.model_copy(
                update={
                    "name": "Latest Parent",
                    "slug": "latest-parent",
                    "agents": AgentSubagentsConfig.model_validate(
                        {
                            "enabled": True,
                            "subagents": [{"preset": child.slug}],
                        }
                    ),
                }
            )
        )

        await agent_preset_service.update_preset(
            child,
            AgentPresetUpdate(instructions="Child v2"),
        )
        child_version_two = await agent_preset_service.get_current_version_for_preset(
            child
        )

        config = await agent_preset_service.resolve_agent_preset_config(
            preset_id=parent.id
        )

        assert child_version_two.id != child_version_one.id
        assert config.agents.enabled is True
        assert len(config.agents.subagents) == 1
        head_subagent = config.agents.subagents[0]
        assert isinstance(head_subagent, HeadAttachedSubagentRef)
        assert head_subagent.preset_id == child.id

        resolved = await resolve_agents_config(
            agent_preset_service,
            agents=config.agents,
            parent_preset_id=parent.id,
            parent_slug=parent.slug,
        )
        resolved_subagent = resolved.subagents[0].binding
        assert resolved_subagent.preset_version_id == child_version_two.id
        assert resolved_subagent.preset_version == child_version_two.version

    async def test_update_parent_rejects_subagent_with_tool_approvals(
        self,
        agent_preset_service: AgentPresetService,
        agent_preset_create_params: AgentPresetCreate,
        registry_actions: list[RegistryAction],
    ) -> None:
        """Existing parent presets cannot attach approval-gated subagents."""
        child_params = agent_preset_create_params.model_copy(
            update={
                "name": "Approval Child",
                "slug": "approval-child",
                "actions": ["tools.test.test_action"],
                "tool_approvals": {"tools.test.test_action": True},
            }
        )
        child = await agent_preset_service.create_preset(child_params)
        parent = await agent_preset_service.create_preset(
            agent_preset_create_params.model_copy(
                update={"name": "Parent Agent", "slug": "parent-agent"}
            )
        )

        with pytest.raises(
            TracecatValidationError,
            match=(
                "Subagent preset 'approval-child' uses manual approvals, "
                "which are not supported for subagents yet."
            ),
        ):
            await agent_preset_service.update_preset(
                parent,
                AgentPresetUpdate(
                    agents=AgentSubagentsConfig.model_validate(
                        {
                            "enabled": True,
                            "subagents": [{"preset": child.slug}],
                        }
                    )
                ),
            )

    async def test_create_parent_rejects_unknown_subagent_slug(
        self,
        agent_preset_service: AgentPresetService,
        agent_preset_create_params: AgentPresetCreate,
    ) -> None:
        """A subagent ref that fails to resolve at save time is a validation
        error, never a silent drop from the persisted config."""
        parent_params = agent_preset_create_params.model_copy(
            update={
                "name": "Typo Parent",
                "slug": "typo-parent",
                "agents": AgentSubagentsConfig.model_validate(
                    {
                        "enabled": True,
                        "subagents": [{"preset": "no-such-child"}],
                    }
                ),
            }
        )

        with pytest.raises(
            TracecatValidationError,
            match="unavailable subagent presets",
        ) as excinfo:
            await agent_preset_service.create_preset(parent_params)
        detail = excinfo.value.detail
        assert detail is not None
        assert detail["code"] == "subagent_presets_unavailable"
        assert detail["skipped"] == [
            {
                "preset_slug": "no-such-child",
                "preset_id": None,
                "reason": "not_found",
            }
        ]

    async def test_update_parent_rejects_soft_deleted_subagent(
        self,
        agent_preset_service: AgentPresetService,
        agent_preset_create_params: AgentPresetCreate,
    ) -> None:
        """Attaching an already soft-deleted child at save time is a validation
        error, never a silent drop from the persisted config."""
        child = await agent_preset_service.create_preset(
            agent_preset_create_params.model_copy(
                update={"name": "Deleted Child", "slug": "deleted-child"}
            )
        )
        parent = await agent_preset_service.create_preset(
            agent_preset_create_params.model_copy(
                update={"name": "Deleted Child Parent", "slug": "deleted-child-parent"}
            )
        )
        child.deleted_at = datetime.now(UTC)
        agent_preset_service.session.add(child)
        await agent_preset_service.session.flush()

        with pytest.raises(
            TracecatValidationError,
            match="unavailable subagent presets",
        ) as excinfo:
            await agent_preset_service.update_preset(
                parent,
                AgentPresetUpdate(
                    agents=AgentSubagentsConfig.model_validate(
                        {
                            "enabled": True,
                            "subagents": [{"preset": child.slug}],
                        }
                    )
                ),
            )
        detail = excinfo.value.detail
        assert detail is not None
        assert detail["code"] == "subagent_presets_unavailable"
        assert detail["skipped"] == [
            {
                "preset_slug": "deleted-child",
                "preset_id": str(child.id),
                "reason": "deleted",
            }
        ]

    async def test_update_parent_rechecks_subagent_before_saving_head(
        self,
        agent_preset_service: AgentPresetService,
        agent_preset_create_params: AgentPresetCreate,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Updating a parent cannot attach a child soft-deleted after resolution."""
        child = await agent_preset_service.create_preset(
            agent_preset_create_params.model_copy(
                update={"name": "Update Race Child", "slug": "update-race-child"}
            )
        )
        parent = await agent_preset_service.create_preset(
            agent_preset_create_params.model_copy(
                update={"name": "Update Race Parent", "slug": "update-race-parent"}
            )
        )
        original_lock = agent_preset_service._lock_active_subagent_presets

        async def soft_delete_child_then_lock(agents: ResolvedAgentsConfig) -> None:
            child.deleted_at = datetime.now(UTC)
            agent_preset_service.session.add(child)
            await agent_preset_service.session.flush()
            await original_lock(agents)

        monkeypatch.setattr(
            agent_preset_service,
            "_lock_active_subagent_presets",
            soft_delete_child_then_lock,
        )

        with pytest.raises(
            TracecatValidationError,
            match="soft-deleted or missing subagent",
        ):
            await agent_preset_service.update_preset(
                parent,
                AgentPresetUpdate(
                    agents=AgentSubagentsConfig.model_validate(
                        {
                            "enabled": True,
                            "subagents": [{"preset": child.slug}],
                        }
                    )
                ),
            )

    async def test_update_preset_tool_approvals(
        self,
        agent_preset_service: AgentPresetService,
        agent_preset_create_params: AgentPresetCreate,
        registry_actions: list[RegistryAction],
    ) -> None:
        agent_preset_create_params.actions = ["tools.test.test_action"]
        preset = await agent_preset_service.create_preset(agent_preset_create_params)

        update_params = AgentPresetUpdate(
            tool_approvals={"tools.test.test_action": True}
        )
        updated_preset = await agent_preset_service.update_preset(preset, update_params)
        updated_read = await agent_preset_service.build_preset_read(updated_preset)
        assert updated_read.tool_approvals == {"tools.test.test_action": True}

    async def test_update_preset_clear_tool_approvals(
        self,
        agent_preset_service: AgentPresetService,
        agent_preset_create_params: AgentPresetCreate,
        registry_actions: list[RegistryAction],
    ) -> None:
        agent_preset_create_params.actions = ["tools.test.test_action"]
        agent_preset_create_params.tool_approvals = {"tools.test.test_action": True}
        preset = await agent_preset_service.create_preset(agent_preset_create_params)

        update_params = AgentPresetUpdate(tool_approvals=None)
        updated_preset = await agent_preset_service.update_preset(preset, update_params)
        updated_read = await agent_preset_service.build_preset_read(updated_preset)
        assert updated_read.tool_approvals is None
