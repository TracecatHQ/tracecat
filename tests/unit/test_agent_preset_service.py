"""Tests for AgentPresetService."""

import asyncio
import os
import uuid
from datetime import UTC, datetime
from typing import Any, cast

import pytest
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
from tracecat.agent.mcp.secret_resolution import TemplatedMappingResolutionError
from tracecat.agent.preset.resolver import resolve_agents_config
from tracecat.agent.preset.schemas import (
    AgentPresetCreate,
    AgentPresetSkillBindingBase,
    AgentPresetSkillBindingRead,
    AgentPresetUpdate,
)
from tracecat.agent.preset.service import AgentPresetService
from tracecat.agent.preset.types import SkillBindingSpec
from tracecat.agent.skill.schemas import (
    SkillCreate,
    SkillDraftPatch,
    SkillDraftUpsertTextFileOp,
)
from tracecat.agent.skill.service import SkillService
from tracecat.agent.subagents import (
    AgentSubagentsConfig,
    ResolvedAgentsConfig,
    ResolvedAttachedSubagentRef,
)
from tracecat.agent.types import AgentConfig
from tracecat.auth.types import Role
from tracecat.db.models import (
    AgentCatalog,
    AgentChannelToken,
    AgentModelAccess,
    AgentPreset,
    AgentPresetVersion,
    AgentPresetVersionSkill,
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
from tracecat.settings.schemas import (
    AppSettingsUpdate,
    VersionedResourceResolutionStrategy,
)
from tracecat.settings.service import SettingsService
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


@pytest.mark.anyio
async def test_resolve_stdio_env_resolves_secrets_variables_and_literals(
    agent_preset_service: AgentPresetService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stdio env resolves workspace secrets, variables, and literals."""
    suffix = uuid.uuid4().hex
    secret_name = f"stdio_secret_{suffix}"
    secret_value = f"stdio-secret-value-{suffix}"
    variable_name = f"stdio_variable_{suffix}"
    variable_value = f"stdio-variable-value-{suffix}"

    async def get_action_secrets(**_: object) -> dict[str, dict[str, str]]:
        return {secret_name: {"TOKEN": secret_value}}

    async def get_workspace_variables(
        *_: object, **__: object
    ) -> dict[str, dict[str, str]]:
        return {variable_name: {"host": variable_value}}

    monkeypatch.setattr(
        "tracecat.secrets.secrets_manager.get_action_secrets",
        get_action_secrets,
    )
    monkeypatch.setattr(
        "tracecat.executor.service.get_workspace_variables",
        get_workspace_variables,
    )
    stdio_env = {
        "TOKEN": f"${{{{ SECRETS.{secret_name}.TOKEN }}}}",
        "HOST": f"prefix-${{{{ VARS.{variable_name}.host }}}}",
        "LITERAL": "literal-value",
    }

    resolved = await agent_preset_service.resolve_stdio_env(
        stdio_env=stdio_env,
        mcp_integration_id=uuid.uuid4(),
        mcp_integration_slug=f"stdio-template-{suffix}",
    )

    assert resolved == {
        "TOKEN": secret_value,
        "HOST": f"prefix-{variable_value}",
        "LITERAL": "literal-value",
    }


@pytest.mark.anyio
async def test_resolve_stdio_env_missing_reference_fails_closed(
    agent_preset_service: AgentPresetService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unresolvable stdio env references raise instead of resolving to None."""
    missing_secret_name = f"missing_stdio_secret_{uuid.uuid4().hex}"

    async def get_action_secrets(**_: object) -> dict[str, dict[str, str]]:
        return {}

    async def get_workspace_variables(
        *_: object, **__: object
    ) -> dict[str, dict[str, str]]:
        return {}

    monkeypatch.setattr(
        "tracecat.secrets.secrets_manager.get_action_secrets",
        get_action_secrets,
    )
    monkeypatch.setattr(
        "tracecat.executor.service.get_workspace_variables",
        get_workspace_variables,
    )

    with pytest.raises(TemplatedMappingResolutionError) as exc_info:
        await agent_preset_service.resolve_stdio_env(
            stdio_env={"TOKEN": f"${{{{ SECRETS.{missing_secret_name}.TOKEN }}}}"},
            mcp_integration_id=uuid.uuid4(),
            mcp_integration_slug="stdio-missing-secret",
        )

    assert missing_secret_name in str(exc_info.value)


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

        assert created_preset.mcp_integrations == [stdio_mcp_id]
        assert created_preset.enable_internet_access is True
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

        assert updated_preset.mcp_integrations == [stdio_mcp_id]
        assert updated_preset.enable_internet_access is True
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
        versions = await agent_preset_service.list_versions(
            created_preset.id,
            CursorPaginationParams(limit=10),
        )

        assert updated_preset.enable_internet_access is True
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

        created_preset.enable_internet_access = False
        current_version.enable_internet_access = False
        session.add_all([created_preset, current_version])
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
        assert updated_preset.enable_internet_access is True
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

        assert updated_preset.name == "Renamed preset"
        assert updated_preset.mcp_integrations == [stdio_mcp_id]

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

        assert preset.catalog_id == catalog.id
        assert preset.model_name == catalog.model_name
        assert preset.model_provider == catalog.model_provider

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

        assert preset.catalog_id == catalog.id
        assert preset.model_name == catalog.model_name
        assert preset.model_provider == catalog.model_provider

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
        assert updated.catalog_id == workspace_catalog.id
        assert updated.model_name == workspace_catalog.model_name
        assert updated.model_provider == workspace_catalog.model_provider

        updated = await agent_preset_service.update_preset(
            updated,
            AgentPresetUpdate(
                model_name="gpt-4.1",
                model_provider="openai",
            ),
        )
        assert updated.catalog_id == workspace_catalog.id
        assert updated.model_name == workspace_catalog.model_name
        assert updated.model_provider == workspace_catalog.model_provider

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
        """Version list metadata includes version-specific subagent eligibility."""
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
            SkillCreate(name="triage-skill")
        )
        skill_version = await skill_service.publish_skill(created_skill.id)

        created_preset = await agent_preset_service.create_preset(
            AgentPresetCreate(
                name="Skill preset",
                description="Preset with a skill",
                instructions="Use the selected skill version",
                model_name="gpt-4o-mini",
                model_provider="openai",
                skills=[AgentPresetSkillBindingBase(skill_id=created_skill.id)],
            )
        )
        current_version = await agent_preset_service.get_current_version_for_preset(
            created_preset
        )
        version_read = await agent_preset_service.build_version_read(current_version)

        assert len(version_read.skills) == 1
        assert version_read.skills[0].skill_version_id == skill_version.id
        assert version_read.skills[0].skill_version == 1

    async def test_create_preset_skill_binding_without_version_stores_current_version(
        self,
        configure_minio_for_skills,
        session: AsyncSession,
        svc_role: Role,
        agent_preset_service: AgentPresetService,
    ) -> None:
        """Skill-only authoring stores the server-derived current skill version."""

        skill_service = SkillService(session=session, role=svc_role)
        created_skill = await skill_service.create_skill(
            SkillCreate(name="skill-only-current")
        )
        skill_version = await skill_service.publish_skill(created_skill.id)

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
        head_bindings = await agent_preset_service._list_head_skill_bindings(
            created_preset.id
        )
        version_read = await agent_preset_service.build_version_read(current_version)

        assert head_bindings[0].skill_version_id == skill_version.id
        assert version_read.skills[0].skill_version_id == skill_version.id

    async def test_update_preset_skill_binding_without_version_uses_current_version(
        self,
        configure_minio_for_skills,
        session: AsyncSession,
        svc_role: Role,
        agent_preset_service: AgentPresetService,
    ) -> None:
        """Skill-only updates store the current published Skill version."""

        skill_service = SkillService(session=session, role=svc_role)
        created_skill = await skill_service.create_skill(
            SkillCreate(name="skill-only-current-update")
        )
        previous_version = await skill_service.publish_skill(created_skill.id)
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
        current_version = await skill_service.publish_skill(created_skill.id)
        assert current_version.id != previous_version.id

        created_preset = await agent_preset_service.create_preset(
            AgentPresetCreate(
                name="Skill-only update preset",
                instructions="No skills yet",
                model_name="gpt-4o-mini",
                model_provider="openai",
            )
        )
        await agent_preset_service.update_preset(
            created_preset,
            AgentPresetUpdate(
                skills=[AgentPresetSkillBindingBase(skill_id=created_skill.id)]
            ),
        )
        updated_version = await agent_preset_service.get_current_version_for_preset(
            created_preset
        )
        head_bindings = await agent_preset_service._list_head_skill_bindings(
            created_preset.id
        )
        version_read = await agent_preset_service.build_version_read(updated_version)

        assert head_bindings[0].skill_version_id == current_version.id
        assert version_read.skills[0].skill_version_id == current_version.id

    async def test_client_supplied_stale_skill_version_is_ignored(
        self,
        configure_minio_for_skills,
        session: AsyncSession,
        svc_role: Role,
        agent_preset_service: AgentPresetService,
    ) -> None:
        """Legacy extra fields are ignored and cannot select a Skill version."""

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
        head_bindings = await agent_preset_service._list_head_skill_bindings(
            created_preset.id
        )
        version_read = await agent_preset_service.build_version_read(current_version)

        assert head_bindings[0].skill_version_id == current_published_version.id
        assert version_read.skills[0].skill_version_id == current_published_version.id
        assert current_published_version.id != stale_version.id

    async def test_create_preset_rejects_unpublished_skill_binding(
        self,
        session: AsyncSession,
        svc_role: Role,
        agent_preset_service: AgentPresetService,
    ) -> None:
        """Authoring rejects binding a Skill with no published version."""

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

    async def test_resolve_config_uses_latest_skill_versions_when_setting_enabled(
        self,
        configure_minio_for_skills,
        session: AsyncSession,
        svc_role: Role,
        svc_admin_role: Role,
        agent_preset_service: AgentPresetService,
    ) -> None:
        """Latest-resource mode resolves skills by current version at execution time."""

        settings_service = SettingsService(session=session, role=svc_admin_role)
        await settings_service.update_app_settings(
            AppSettingsUpdate(
                app_versioned_resource_resolution_strategy=(
                    VersionedResourceResolutionStrategy.LATEST
                )
            )
        )
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

    async def test_resolve_config_rejects_archived_skill_in_latest_mode(
        self,
        configure_minio_for_skills,
        session: AsyncSession,
        svc_role: Role,
        svc_admin_role: Role,
        agent_preset_service: AgentPresetService,
    ) -> None:
        """Latest-resource mode refuses archived skills from historical versions."""

        settings_service = SettingsService(session=session, role=svc_admin_role)
        await settings_service.update_app_settings(
            AppSettingsUpdate(
                app_versioned_resource_resolution_strategy=(
                    VersionedResourceResolutionStrategy.LATEST
                )
            )
        )
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

        with pytest.raises(TracecatValidationError) as exc_info:
            await agent_preset_service.resolve_agent_preset_config(
                preset_id=created_preset.id,
                preset_version_id=historical_version.id,
            )

        detail = exc_info.value.detail
        assert detail is not None
        assert detail["code"] == "skill_archived"
        assert str(created_skill.id) in str(detail["skills"])

    async def test_resolve_config_rejects_archived_skill_in_pinned_mode(
        self,
        configure_minio_for_skills,
        session: AsyncSession,
        svc_role: Role,
        svc_admin_role: Role,
        agent_preset_service: AgentPresetService,
    ) -> None:
        """Pinned-resource mode refuses archived skills from historical versions."""

        settings_service = SettingsService(session=session, role=svc_admin_role)
        await settings_service.update_app_settings(
            AppSettingsUpdate(
                app_versioned_resource_resolution_strategy=(
                    VersionedResourceResolutionStrategy.PINNED
                )
            )
        )
        skill_service = SkillService(session=session, role=svc_role)
        created_skill = await skill_service.create_skill(
            SkillCreate(name="pinned-archived-skill")
        )
        await skill_service.publish_skill(created_skill.id)
        created_preset = await agent_preset_service.create_preset(
            AgentPresetCreate(
                name="Pinned archived skill preset",
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

        with pytest.raises(TracecatValidationError) as exc_info:
            await agent_preset_service.resolve_agent_preset_config(
                preset_id=created_preset.id,
                preset_version_id=historical_version.id,
            )

        detail = exc_info.value.detail
        assert detail is not None
        assert detail["code"] == "skill_archived"
        assert str(created_skill.id) in str(detail["skills"])

    async def test_list_versions_returns_metadata_without_skill_lookups(
        self,
        configure_minio_for_skills,
        session: AsyncSession,
        svc_role: Role,
        agent_preset_service: AgentPresetService,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Version list metadata does not resolve skill bindings."""

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
        restored_bindings = await agent_preset_service._list_head_skill_bindings(
            restored.id
        )

        assert len(diff.skill_changes) == 1
        assert diff.skill_changes[0].old_skill_version_id == skill_version_one.id
        assert diff.skill_changes[0].new_skill_version_id == skill_version_two.id
        assert len(restored_bindings) == 1
        assert restored_bindings[0].skill_version_id == skill_version_one.id

    async def test_build_version_read_uses_snapshot_skill_version_name(
        self,
        configure_minio_for_skills,
        session: AsyncSession,
        svc_role: Role,
        agent_preset_service: AgentPresetService,
    ) -> None:
        """Preset version reads expose the name from their Skill snapshot."""

        skill_service = SkillService(session=session, role=svc_role)
        created_skill = await skill_service.create_skill(
            SkillCreate(name="version-one")
        )
        skill_version_one = await skill_service.publish_skill(created_skill.id)

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
        skill_version_two = await skill_service.publish_skill(created_skill.id)

        await agent_preset_service.update_preset(
            created_preset,
            AgentPresetUpdate(
                instructions="Snapshot the newer Skill version",
                skills=[AgentPresetSkillBindingBase(skill_id=created_skill.id)],
            ),
        )

        version_read = await agent_preset_service.build_version_read(preset_version_one)
        head_bindings = await agent_preset_service._list_head_skill_bindings(
            created_preset.id
        )

        assert version_read.skills[0].skill_version_id == skill_version_one.id
        assert version_read.skills[0].skill_name == "version-one"
        assert head_bindings[0].skill_version_id == skill_version_two.id
        assert head_bindings[0].skill_name == "version-two"

    async def test_non_skill_update_refreshes_head_and_version_skill_bindings(
        self,
        configure_minio_for_skills,
        session: AsyncSession,
        svc_role: Role,
        agent_preset_service: AgentPresetService,
    ) -> None:
        """A new preset snapshot keeps its derived head binding in sync."""

        skill_service = SkillService(session=session, role=svc_role)
        created_skill = await skill_service.create_skill(
            SkillCreate(name="head-binding-v1")
        )
        await skill_service.publish_skill(created_skill.id)

        created_preset = await agent_preset_service.create_preset(
            AgentPresetCreate(
                name="Head binding refresh preset",
                instructions="Use the current Skill",
                model_name="gpt-4o-mini",
                model_provider="openai",
                skills=[AgentPresetSkillBindingBase(skill_id=created_skill.id)],
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
                            "---\nname: head-binding-v2\n---\n\n# Head binding v2\n"
                        ),
                        content_type="text/markdown; charset=utf-8",
                    )
                ],
            ),
        )
        skill_version_two = await skill_service.publish_skill(created_skill.id)

        await agent_preset_service.update_preset(
            created_preset,
            AgentPresetUpdate(instructions="Use the refreshed current Skill"),
        )

        current_version = await agent_preset_service.get_current_version_for_preset(
            created_preset
        )
        head_bindings = await agent_preset_service._list_head_skill_bindings(
            created_preset.id
        )
        version_read = await agent_preset_service.build_version_read(current_version)

        assert head_bindings[0].skill_version_id == skill_version_two.id
        assert head_bindings[0].skill_name == "head-binding-v2"
        assert version_read.skills[0].skill_version_id == skill_version_two.id
        assert version_read.skills[0].skill_name == "head-binding-v2"

    async def test_create_preset_rejects_duplicate_bound_skill_names(
        self,
        configure_minio_for_skills,
        session: AsyncSession,
        svc_role: Role,
        agent_preset_service: AgentPresetService,
    ) -> None:
        """Preset version creation rejects duplicate current Skill names."""

        skill_service = SkillService(session=session, role=svc_role)
        skill_a = await skill_service.create_skill(SkillCreate(name="shared-name"))
        await skill_service.publish_skill(skill_a.id)

        skill_b = await skill_service.create_skill(SkillCreate(name="skill-b-current"))
        await skill_service.publish_skill(skill_b.id)

        draft_b = await skill_service.get_draft(skill_b.id)
        assert draft_b is not None
        await skill_service.patch_draft(
            skill_id=skill_b.id,
            params=SkillDraftPatch(
                base_revision=draft_b.draft_revision,
                operations=[
                    SkillDraftUpsertTextFileOp(
                        path="SKILL.md",
                        content="---\nname: shared-name\n---\n\n# shared-name\n",
                        content_type="text/markdown; charset=utf-8",
                    )
                ],
            ),
        )
        await skill_service.publish_skill(skill_b.id)

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
        assert uuid.UUID(detail["preset_id"])

    async def test_version_validation_and_snapshot_share_locked_skill_specs(
        self,
        configure_minio_for_skills,
        session: AsyncSession,
        svc_role: Role,
        agent_preset_service: AgentPresetService,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Validation and snapshotting consume one locked Skill resolution."""

        skill_service = SkillService(session=session, role=svc_role)
        created_skill = await skill_service.create_skill(
            SkillCreate(name="atomic-snapshot-skill")
        )
        await skill_service.publish_skill(created_skill.id)
        created_preset = await agent_preset_service.create_preset(
            AgentPresetCreate(
                name="Atomic snapshot preset",
                instructions="Use the selected Skill",
                model_name="gpt-4o-mini",
                model_provider="openai",
                skills=[AgentPresetSkillBindingBase(skill_id=created_skill.id)],
            )
        )

        original_resolve = agent_preset_service._resolve_head_skill_binding_specs
        original_validate = agent_preset_service._validate_unique_skill_binding_names
        original_snapshot = agent_preset_service._snapshot_version_skill_bindings
        calls: list[tuple[str, object]] = []

        async def instrumented_resolve(
            preset_id: uuid.UUID, *, for_update: bool = False
        ) -> list[SkillBindingSpec]:
            specs = await original_resolve(preset_id, for_update=for_update)
            calls.append(("resolve_locked" if for_update else "resolve", specs))
            return specs

        async def instrumented_validate(
            binding_specs: list[SkillBindingSpec], *, preset_id: uuid.UUID
        ) -> None:
            calls.append(("validate", binding_specs))
            await original_validate(binding_specs, preset_id=preset_id)

        async def instrumented_snapshot(
            preset_id: uuid.UUID,
            preset_version_id: uuid.UUID,
            *,
            binding_specs: list[SkillBindingSpec] | None = None,
        ) -> None:
            calls.append(("snapshot", binding_specs))
            await original_snapshot(
                preset_id,
                preset_version_id,
                binding_specs=binding_specs,
            )

        monkeypatch.setattr(
            agent_preset_service,
            "_resolve_head_skill_binding_specs",
            instrumented_resolve,
        )
        monkeypatch.setattr(
            agent_preset_service,
            "_validate_unique_skill_binding_names",
            instrumented_validate,
        )
        monkeypatch.setattr(
            agent_preset_service,
            "_snapshot_version_skill_bindings",
            instrumented_snapshot,
        )

        await agent_preset_service.update_preset(
            created_preset,
            AgentPresetUpdate(instructions="Create an atomic snapshot"),
        )

        assert [name for name, _value in calls] == [
            "resolve_locked",
            "validate",
            "snapshot",
        ]
        assert calls[0][1] is calls[1][1]
        assert calls[1][1] is calls[2][1]

    async def test_resolve_agent_preset_config_rejects_duplicate_skill_names(
        self,
        configure_minio_for_skills,
        session: AsyncSession,
        svc_role: Role,
        agent_preset_service: AgentPresetService,
    ) -> None:
        """Preset resolution fails before runtime if a snapshot contains duplicates."""

        skill_service = SkillService(session=session, role=svc_role)
        skill_a = await skill_service.create_skill(SkillCreate(name="shared-name"))
        await skill_service.publish_skill(skill_a.id)

        skill_b = await skill_service.create_skill(SkillCreate(name="skill-b-current"))
        await skill_service.publish_skill(skill_b.id)

        draft_b = await skill_service.get_draft(skill_b.id)
        assert draft_b is not None
        await skill_service.patch_draft(
            skill_id=skill_b.id,
            params=SkillDraftPatch(
                base_revision=draft_b.draft_revision,
                operations=[
                    SkillDraftUpsertTextFileOp(
                        path="SKILL.md",
                        content="---\nname: shared-name\n---\n\n# shared-name\n",
                        content_type="text/markdown; charset=utf-8",
                    )
                ],
            ),
        )
        skill_b_shared = await skill_service.publish_skill(skill_b.id)

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
                skill_version_id=skill_b_shared.id,
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
        """Preset creation validates bound skills under row locks."""

        skill_service = SkillService(session=session, role=svc_role)
        created_skill = await skill_service.create_skill(
            SkillCreate(name="create-lock-skill")
        )
        await skill_service.publish_skill(created_skill.id)

        captured_for_update: list[bool] = []
        original_validate_binding_inputs = (
            agent_preset_service.skills.validate_binding_inputs
        )

        async def instrumented_validate_binding_inputs(
            bindings: list[AgentPresetSkillBindingBase],
            *,
            for_update: bool = False,
        ) -> None:
            captured_for_update.append(for_update)
            await original_validate_binding_inputs(bindings, for_update=for_update)

        monkeypatch.setattr(
            agent_preset_service.skills,
            "validate_binding_inputs",
            instrumented_validate_binding_inputs,
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
        """Explicit null skill updates clear mutable preset skill bindings."""

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
        current_bindings = await agent_preset_service._list_head_skill_bindings(
            created_preset.id
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
        """Preset updates validate requested skill bindings under row locks."""

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
        original_validate_binding_inputs = (
            agent_preset_service.skills.validate_binding_inputs
        )

        async def instrumented_validate_binding_inputs(
            bindings: list[AgentPresetSkillBindingBase],
            *,
            for_update: bool = False,
        ) -> None:
            captured_for_update.append(for_update)
            await original_validate_binding_inputs(bindings, for_update=for_update)

        monkeypatch.setattr(
            agent_preset_service.skills,
            "validate_binding_inputs",
            instrumented_validate_binding_inputs,
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

    async def test_update_preset_locks_preset_before_replacing_skill_bindings(
        self,
        configure_minio_for_skills,
        session: AsyncSession,
        svc_role: Role,
        agent_preset_service: AgentPresetService,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Preset updates lock before reading or replacing mutable skill bindings."""

        skill_service = SkillService(session=session, role=svc_role)
        created_skill = await skill_service.create_skill(
            SkillCreate(name="ordered-lock-skill")
        )
        await skill_service.publish_skill(created_skill.id)

        created_preset = await agent_preset_service.create_preset(
            AgentPresetCreate(
                name="Ordered lock preset",
                description="Preset used to verify lock ordering on update",
                instructions="Use the selected skill version",
                model_name="gpt-4o-mini",
                model_provider="openai",
            )
        )

        original_lock = agent_preset_service._lock_preset_row
        original_get_specs = agent_preset_service._get_head_skill_binding_specs
        original_replace = agent_preset_service._replace_head_skill_bindings
        call_order: list[str] = []

        async def instrumented_lock(preset_id: uuid.UUID) -> None:
            call_order.append("lock")
            await original_lock(preset_id)

        async def instrumented_get_specs(
            preset_id: uuid.UUID,
        ) -> list[SkillBindingSpec]:
            call_order.append("read_specs")
            return await original_get_specs(preset_id)

        async def instrumented_replace(
            preset_id: uuid.UUID,
            bindings: list[AgentPresetSkillBindingBase],
            **kwargs: Any,
        ) -> None:
            call_order.append("replace")
            await original_replace(preset_id, bindings, **kwargs)

        monkeypatch.setattr(
            agent_preset_service,
            "_lock_preset_row",
            instrumented_lock,
        )
        monkeypatch.setattr(
            agent_preset_service,
            "_get_head_skill_binding_specs",
            instrumented_get_specs,
        )
        monkeypatch.setattr(
            agent_preset_service,
            "_replace_head_skill_bindings",
            instrumented_replace,
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

        assert call_order[:3] == ["lock", "read_specs", "replace"]
        assert call_order.count("lock") == 1

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

    async def test_restore_version_locks_preset_before_replacing_skill_bindings(
        self,
        configure_minio_for_skills,
        session: AsyncSession,
        svc_role: Role,
        agent_preset_service: AgentPresetService,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Preset restore locks before replacing mutable head skill bindings."""

        skill_service = SkillService(session=session, role=svc_role)
        created_skill = await skill_service.create_skill(
            SkillCreate(name="restore-ordered-lock-skill")
        )
        await skill_service.publish_skill(created_skill.id)

        created_preset = await agent_preset_service.create_preset(
            AgentPresetCreate(
                name="Restore ordered lock preset",
                description="Preset used to verify lock ordering on restore",
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
            AgentPresetUpdate(skills=[]),
        )

        original_lock = agent_preset_service._lock_preset_row
        original_restore = (
            agent_preset_service._restore_head_skill_bindings_from_version
        )
        call_order: list[str] = []

        async def instrumented_lock(preset_id: uuid.UUID) -> None:
            call_order.append("lock")
            await original_lock(preset_id)

        async def instrumented_restore(
            *, preset_id: uuid.UUID, version_id: uuid.UUID
        ) -> None:
            call_order.append("restore_bindings")
            await original_restore(preset_id=preset_id, version_id=version_id)

        monkeypatch.setattr(
            agent_preset_service,
            "_lock_preset_row",
            instrumented_lock,
        )
        monkeypatch.setattr(
            agent_preset_service,
            "_restore_head_skill_bindings_from_version",
            instrumented_restore,
        )

        await agent_preset_service.restore_version(created_preset, version_1)

        assert call_order[:2] == ["lock", "restore_bindings"]
        assert call_order.count("lock") == 1

    async def test_restore_version_rejects_archived_skill_bindings(
        self,
        configure_minio_for_skills,
        session: AsyncSession,
        svc_role: Role,
        agent_preset_service: AgentPresetService,
    ) -> None:
        """Restoring a version cannot reattach archived skills onto the mutable head."""

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
        skill_row.archived_at = archived_at
        skill_row.deleted_at = archived_at
        await session.commit()

        with pytest.raises(TracecatValidationError, match="not found"):
            await agent_preset_service.restore_version(created_preset, version_1)

    async def test_restore_version_rejects_soft_deleted_subagent_bindings(
        self,
        agent_preset_service: AgentPresetService,
        agent_preset_create_params: AgentPresetCreate,
    ) -> None:
        """Restoring a version cannot make soft-deleted subagents active again."""
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

        with pytest.raises(
            TracecatValidationError,
            match="soft-deleted or missing subagent",
        ):
            await agent_preset_service.restore_version(parent, version_with_child)

        await agent_preset_service.session.refresh(parent)
        assert parent.current_version_id == version_without_child.id
        assert parent.agents == {"enabled": False, "subagents": []}

    async def test_restore_version_locks_skill_bindings_during_validation(
        self,
        configure_minio_for_skills,
        session: AsyncSession,
        svc_role: Role,
        agent_preset_service: AgentPresetService,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Preset restore validates rebound skill bindings under row locks."""

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
        original_validate_binding_inputs = (
            agent_preset_service.skills.validate_binding_inputs
        )

        async def instrumented_validate_binding_inputs(
            bindings: list[AgentPresetSkillBindingBase],
            *,
            for_update: bool = False,
        ) -> None:
            captured_for_update.append(for_update)
            await original_validate_binding_inputs(bindings, for_update=for_update)

        monkeypatch.setattr(
            agent_preset_service.skills,
            "validate_binding_inputs",
            instrumented_validate_binding_inputs,
        )

        await agent_preset_service.restore_version(created_preset, version_1)

        assert captured_for_update == [True]

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

    async def test_resolve_pinned_version_rejects_soft_deleted_preset(
        self,
        agent_preset_service: AgentPresetService,
        agent_preset_create_params: AgentPresetCreate,
    ) -> None:
        """Pinned version resolution should not bypass soft-deleted preset state."""
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
            await agent_preset_service._create_version_from_preset(created_preset)

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

        async def instrumented_lock(preset_id: uuid.UUID) -> None:
            call_order.append("lock")
            await original_lock(preset_id)

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

    async def test_delete_preset_soft_deletes_when_only_referenced_as_subagent_in_history(
        self,
        agent_preset_service: AgentPresetService,
        agent_preset_create_params: AgentPresetCreate,
    ) -> None:
        """Historical subagent references do not block soft-delete or remain runnable."""
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
        with pytest.raises(TracecatNotFoundError):
            await resolve_agents_config(
                agent_preset_service,
                agents=AgentSubagentsConfig.model_validate(parent_v1.agents),
                parent_preset_id=parent.id,
                parent_slug=parent.slug,
                include_runtime_config=True,
            )

        with pytest.raises(TracecatNotFoundError):
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

    async def test_create_parent_allows_pinned_subagent_with_reused_parent_slug(
        self,
        agent_preset_service: AgentPresetService,
        agent_preset_create_params: AgentPresetCreate,
    ) -> None:
        """Pinned subagent refs compare immutable IDs instead of stale slugs."""
        child = await agent_preset_service.create_preset(
            agent_preset_create_params.model_copy(
                update={"name": "Original Child", "slug": "reused-slug"}
            )
        )
        child_version = await agent_preset_service.get_current_version_for_preset(child)
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
                                    "preset_version": child_version.version,
                                    "preset_id": child.id,
                                    "preset_version_id": child_version.id,
                                }
                            ],
                        }
                    ),
                }
            )
        )

        agents = AgentSubagentsConfig.model_validate(parent.agents)
        assert agents.enabled is True
        assert isinstance(agents.subagents[0], ResolvedAttachedSubagentRef)
        assert agents.subagents[0].preset_id == child.id

    async def test_resolve_config_uses_latest_subagent_version_when_setting_enabled(
        self,
        session: AsyncSession,
        svc_role: Role,
        svc_admin_role: Role,
        agent_preset_service: AgentPresetService,
        agent_preset_create_params: AgentPresetCreate,
    ) -> None:
        """Latest-resource mode resolves preset-backed subagents by current version."""

        settings_service = SettingsService(session=session, role=svc_admin_role)
        await settings_service.update_app_settings(
            AppSettingsUpdate(
                app_versioned_resource_resolution_strategy=(
                    VersionedResourceResolutionStrategy.LATEST
                )
            )
        )
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
        resolved_subagent = config.agents.subagents[0]
        assert isinstance(resolved_subagent, ResolvedAttachedSubagentRef)
        assert resolved_subagent.preset_id == child.id
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
