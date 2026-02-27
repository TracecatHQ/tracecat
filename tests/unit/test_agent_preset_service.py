"""Tests for AgentPresetService."""

import uuid
from typing import cast

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.agent.preset.schemas import AgentPresetCreate, AgentPresetUpdate
from tracecat.agent.preset.service import (
    SYSTEM_PRESET_DEFINITIONS,
    SYSTEM_PRESET_SLUG_CASE_COPILOT,
    SYSTEM_PRESET_SLUG_WORKSPACE_COPILOT,
    AgentPresetService,
    seed_system_presets_for_workspace,
)
from tracecat.agent.types import AgentConfig
from tracecat.auth.types import Role
from tracecat.authz.enums import ScopeSource
from tracecat.authz.scopes import VIEWER_SCOPES
from tracecat.db.models import (
    AgentPreset,
    RegistryAction,
    RegistryIndex,
    RegistryRepository,
    RegistryVersion,
    RoleScope,
    Scope,
    Workspace,
)
from tracecat.db.models import (
    Role as DBRole,
)
from tracecat.exceptions import (
    ScopeDeniedError,
    TracecatAuthorizationError,
    TracecatNotFoundError,
    TracecatValidationError,
)
from tracecat.registry.actions.schemas import RegistryActionType
from tracecat.registry.versions.schemas import (
    RegistryVersionManifest,
    RegistryVersionManifestAction,
)

pytestmark = pytest.mark.usefixtures("db")


@pytest.fixture
async def agent_preset_service(
    session: AsyncSession, svc_role: Role
) -> AgentPresetService:
    """Create an agent preset service instance for testing."""
    return AgentPresetService(session=session, role=svc_role)


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

    async def test_get_agent_config(
        self,
        agent_preset_service: AgentPresetService,
        agent_preset_create_params: AgentPresetCreate,
        registry_actions: list[RegistryAction],
    ) -> None:
        """Test getting agent config from a preset."""
        # Create preset with full configuration
        agent_preset_create_params.actions = ["tools.test.test_action"]
        agent_preset_create_params.namespaces = ["tools.test"]
        agent_preset_create_params.tool_approvals = {"tools.test.test_action": True}

        created_preset = await agent_preset_service.create_preset(
            agent_preset_create_params
        )

        # Get agent config by ID
        agent_config = await agent_preset_service.get_agent_config(created_preset.id)

        assert isinstance(agent_config, AgentConfig)
        assert agent_config.model_name == agent_preset_create_params.model_name
        assert agent_config.model_provider == agent_preset_create_params.model_provider
        assert agent_config.instructions == agent_preset_create_params.instructions
        assert agent_config.actions == agent_preset_create_params.actions
        assert agent_config.namespaces == agent_preset_create_params.namespaces
        assert agent_config.tool_approvals == agent_preset_create_params.tool_approvals
        assert agent_config.retries == agent_preset_create_params.retries

    async def test_get_agent_config_by_slug(
        self,
        agent_preset_service: AgentPresetService,
        agent_preset_create_params: AgentPresetCreate,
    ) -> None:
        """Test getting agent config by slug."""
        created_preset = await agent_preset_service.create_preset(
            agent_preset_create_params
        )

        config = await agent_preset_service.get_agent_config_by_slug(
            created_preset.slug
        )

        assert isinstance(config, AgentConfig)
        assert config.model_name == agent_preset_create_params.model_name

    async def test_get_agent_config_not_found(
        self, agent_preset_service: AgentPresetService
    ) -> None:
        """Test that getting config for non-existent preset raises error."""
        with pytest.raises(
            TracecatNotFoundError, match="Agent preset with ID .* not found"
        ):
            await agent_preset_service.get_agent_config(uuid.uuid4())

    async def test_get_agent_config_by_slug_not_found(
        self, agent_preset_service: AgentPresetService
    ) -> None:
        """Test that getting config by non-existent slug raises error."""
        with pytest.raises(
            TracecatNotFoundError,
            match="Agent preset with slug 'nonexistent' not found",
        ):
            await agent_preset_service.get_agent_config_by_slug("nonexistent")

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

    async def test_resolve_agent_preset_config_no_params_raises_error(
        self, agent_preset_service: AgentPresetService
    ) -> None:
        """Test that resolve without parameters raises ValueError."""
        with pytest.raises(
            ValueError, match="Either preset_id or slug must be provided"
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
        """Test the _preset_to_agent_config conversion method."""
        # Create a preset with comprehensive configuration
        agent_preset_create_params.actions = ["tools.test.test_action"]
        agent_preset_create_params.namespaces = ["tools.test", "core"]
        agent_preset_create_params.output_type = "list[str]"
        agent_preset_create_params.tool_approvals = {"tools.test.test_action": False}

        preset = await agent_preset_service.create_preset(agent_preset_create_params)

        # Test conversion
        agent_config = await agent_preset_service._preset_to_agent_config(preset)

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

    async def test_delete_system_preset_forbidden(
        self,
        agent_preset_service: AgentPresetService,
        agent_preset_create_params: AgentPresetCreate,
    ) -> None:
        preset = await agent_preset_service.create_preset(agent_preset_create_params)
        preset.is_system = True
        agent_preset_service.session.add(preset)
        await agent_preset_service.session.commit()

        with pytest.raises(
            TracecatAuthorizationError, match="Cannot delete system presets"
        ):
            await agent_preset_service.delete_preset(preset)

    async def test_update_system_preset_allows_noop_slug(
        self,
        agent_preset_service: AgentPresetService,
        agent_preset_create_params: AgentPresetCreate,
    ) -> None:
        preset = await agent_preset_service.create_preset(agent_preset_create_params)
        preset.is_system = True
        agent_preset_service.session.add(preset)
        await agent_preset_service.session.commit()

        updated = await agent_preset_service.update_preset(
            preset,
            AgentPresetUpdate(
                slug=preset.slug,
                instructions="Updated instructions",
            ),
        )

        assert updated.slug == preset.slug
        assert updated.instructions == "Updated instructions"

    async def test_seed_system_presets_skips_reserved_slug_collisions(
        self,
        session: AsyncSession,
        agent_preset_service: AgentPresetService,
        agent_preset_create_params: AgentPresetCreate,
    ) -> None:
        reserved_slug = SYSTEM_PRESET_DEFINITIONS[0].slug
        agent_preset_create_params.slug = reserved_slug
        colliding_user_preset = await agent_preset_service.create_preset(
            agent_preset_create_params
        )
        assert colliding_user_preset.is_system is False

        await seed_system_presets_for_workspace(
            session=session,
            workspace_id=agent_preset_service.workspace_id,
        )
        await session.commit()

        stmt = select(AgentPreset).where(
            AgentPreset.workspace_id == agent_preset_service.workspace_id,
            AgentPreset.slug.in_(
                [definition.slug for definition in SYSTEM_PRESET_DEFINITIONS]
            ),
        )
        system_slug_presets = list((await session.execute(stmt)).scalars().all())

        assert len(system_slug_presets) == len(SYSTEM_PRESET_DEFINITIONS)
        reserved_rows = [
            preset for preset in system_slug_presets if preset.slug == reserved_slug
        ]
        assert len(reserved_rows) == 1
        assert reserved_rows[0].is_system is False

    async def test_seed_system_presets_include_assistant_defaults(
        self,
        session: AsyncSession,
        agent_preset_service: AgentPresetService,
    ) -> None:
        await seed_system_presets_for_workspace(
            session=session,
            workspace_id=agent_preset_service.workspace_id,
        )
        await session.commit()

        stmt = select(AgentPreset).where(
            AgentPreset.workspace_id == agent_preset_service.workspace_id
        )
        presets = list((await session.execute(stmt)).scalars().all())
        by_slug = {preset.slug: preset for preset in presets}

        workspace_copilot = by_slug[SYSTEM_PRESET_SLUG_WORKSPACE_COPILOT]
        case_copilot = by_slug[SYSTEM_PRESET_SLUG_CASE_COPILOT]

        assert workspace_copilot.is_system is True
        assert workspace_copilot.actions is not None
        assert "core.table.list_tables" in workspace_copilot.actions

        assert case_copilot.is_system is True
        assert case_copilot.actions is not None
        assert "core.cases.get_case" in case_copilot.actions

    async def test_list_presets_filters_by_preset_scope(
        self,
        session: AsyncSession,
        svc_role: Role,
        agent_preset_service: AgentPresetService,
        agent_preset_create_params: AgentPresetCreate,
    ) -> None:
        preset1 = await agent_preset_service.create_preset(agent_preset_create_params)
        params2 = agent_preset_create_params.model_copy(deep=True)
        params2.name = "Second Preset"
        preset2 = await agent_preset_service.create_preset(params2)

        limited_role = Role(
            type="user",
            user_id=svc_role.user_id,
            workspace_id=svc_role.workspace_id,
            organization_id=svc_role.organization_id,
            service_id=svc_role.service_id,
            scopes=frozenset(
                {
                    "agent:read",
                    f"agent:preset:{preset1.slug}:read",
                }
            ),
        )
        limited_service = AgentPresetService(session=session, role=limited_role)

        presets = await limited_service.list_presets()
        preset_ids = {preset.id for preset in presets}

        assert preset1.id in preset_ids
        assert preset2.id not in preset_ids

    async def test_get_agent_config_blocks_other_preset_execute_scope(
        self,
        session: AsyncSession,
        svc_role: Role,
        agent_preset_service: AgentPresetService,
        agent_preset_create_params: AgentPresetCreate,
    ) -> None:
        preset1 = await agent_preset_service.create_preset(agent_preset_create_params)
        params2 = agent_preset_create_params.model_copy(deep=True)
        params2.name = "Second Preset"
        preset2 = await agent_preset_service.create_preset(params2)

        scoped_role = Role(
            type="user",
            user_id=svc_role.user_id,
            workspace_id=svc_role.workspace_id,
            organization_id=svc_role.organization_id,
            service_id=svc_role.service_id,
            scopes=frozenset({f"agent:preset:{preset1.slug}:execute"}),
        )
        scoped_service = AgentPresetService(session=session, role=scoped_role)

        with pytest.raises(ScopeDeniedError) as exc_info:
            await scoped_service.get_agent_config(preset2.id)
        assert exc_info.value.missing_scopes == [f"agent:preset:{preset2.slug}:execute"]

    async def test_update_preset_blocks_other_preset_update_scope(
        self,
        session: AsyncSession,
        svc_role: Role,
        agent_preset_service: AgentPresetService,
        agent_preset_create_params: AgentPresetCreate,
    ) -> None:
        preset1 = await agent_preset_service.create_preset(agent_preset_create_params)
        params2 = agent_preset_create_params.model_copy(deep=True)
        params2.name = "Second Preset"
        preset2 = await agent_preset_service.create_preset(params2)

        scoped_role = Role(
            type="user",
            user_id=svc_role.user_id,
            workspace_id=svc_role.workspace_id,
            organization_id=svc_role.organization_id,
            service_id=svc_role.service_id,
            scopes=frozenset({f"agent:preset:{preset1.slug}:update"}),
        )
        scoped_service = AgentPresetService(session=session, role=scoped_role)

        with pytest.raises(ScopeDeniedError) as exc_info:
            await scoped_service.update_preset(
                preset2,
                AgentPresetUpdate(description="Attempted cross-preset update"),
            )
        assert exc_info.value.missing_scopes == [f"agent:preset:{preset2.slug}:update"]

    async def test_get_agent_config_requires_execute_scope(
        self,
        session: AsyncSession,
        svc_role: Role,
        agent_preset_service: AgentPresetService,
        agent_preset_create_params: AgentPresetCreate,
    ) -> None:
        preset = await agent_preset_service.create_preset(agent_preset_create_params)
        viewer_role = Role(
            type="user",
            user_id=svc_role.user_id,
            workspace_id=svc_role.workspace_id,
            organization_id=svc_role.organization_id,
            service_id=svc_role.service_id,
            scopes=VIEWER_SCOPES,
        )
        viewer_service = AgentPresetService(session=session, role=viewer_role)

        with pytest.raises(ScopeDeniedError):
            await viewer_service.get_agent_config(preset.id)

    async def test_update_preset_accepts_specific_preset_update_scope(
        self,
        session: AsyncSession,
        svc_role: Role,
        agent_preset_service: AgentPresetService,
        agent_preset_create_params: AgentPresetCreate,
    ) -> None:
        preset = await agent_preset_service.create_preset(agent_preset_create_params)
        scoped_role = Role(
            type="user",
            user_id=svc_role.user_id,
            workspace_id=svc_role.workspace_id,
            organization_id=svc_role.organization_id,
            service_id=svc_role.service_id,
            scopes=frozenset({f"agent:preset:{preset.slug}:update"}),
        )
        scoped_service = AgentPresetService(session=session, role=scoped_role)

        updated = await scoped_service.update_preset(
            preset,
            AgentPresetUpdate(description="Scoped update"),
        )
        assert updated.description == "Scoped update"

    async def test_preset_execution_role_scopes_are_resolved(
        self,
        session: AsyncSession,
        svc_role: Role,
        agent_preset_service: AgentPresetService,
        agent_preset_create_params: AgentPresetCreate,
    ) -> None:
        preset = await agent_preset_service.create_preset(agent_preset_create_params)
        org_id = svc_role.organization_id
        assert org_id is not None

        custom_role = DBRole(
            id=uuid.uuid4(),
            name="Preset executor",
            slug=None,
            description="Custom execution role",
            organization_id=org_id,
        )
        custom_scope = Scope(
            id=uuid.uuid4(),
            name="action:tools.test.test_action:execute",
            resource="action",
            action="execute",
            description="Execute test action",
            source=ScopeSource.CUSTOM,
            source_ref="tools.test.test_action",
            organization_id=org_id,
        )
        session.add(custom_role)
        session.add(custom_scope)
        await session.flush()

        session.add(
            RoleScope(
                role_id=custom_role.id,
                scope_id=custom_scope.id,
            )
        )
        preset.assigned_role_id = custom_role.id
        session.add(preset)
        await session.commit()

        config = await agent_preset_service.get_agent_config(preset.id)
        assert config.tool_execution_scopes == ["action:tools.test.test_action:execute"]

    async def test_preset_execution_role_with_no_scopes_resolves_to_empty_list(
        self,
        session: AsyncSession,
        svc_role: Role,
        agent_preset_service: AgentPresetService,
        agent_preset_create_params: AgentPresetCreate,
    ) -> None:
        preset = await agent_preset_service.create_preset(agent_preset_create_params)
        org_id = svc_role.organization_id
        assert org_id is not None

        custom_role = DBRole(
            id=uuid.uuid4(),
            name="Preset executor (empty)",
            slug=None,
            description="Preset role without scopes",
            organization_id=org_id,
        )
        session.add(custom_role)
        await session.flush()

        preset.assigned_role_id = custom_role.id
        session.add(preset)
        await session.commit()

        config = await agent_preset_service.get_agent_config(preset.id)
        assert config.tool_execution_scopes == []
