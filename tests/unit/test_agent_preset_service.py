"""Tests for AgentPresetService."""

import uuid
from typing import cast
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.agent.preset import service as preset_service_module
from tracecat.agent.preset.schemas import AgentPresetCreate, AgentPresetUpdate
from tracecat.agent.preset.service import AgentPresetService
from tracecat.agent.types import AgentConfig
from tracecat.auth.types import Role
from tracecat.db.models import (
    RegistryAction,
    RegistryIndex,
    RegistryRepository,
    RegistryVersion,
    Workspace,
)
from tracecat.exceptions import TracecatNotFoundError, TracecatValidationError
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

    async def test_discover_mcp_tools_is_read_only(
        self,
        agent_preset_service: AgentPresetService,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Discovering MCP tools should not write or commit DB state."""

        class _ToolDef:
            description = "List issues"

        async def _resolve_mcp_integrations(
            _mcp_integration_ids: list[str],
        ) -> list[dict[str, object]]:
            return [{"name": "Linear", "url": "https://mcp.example.com", "headers": {}}]

        async def _discover_user_mcp_tools(
            _mcp_servers: list[dict[str, object]],
        ) -> dict[str, _ToolDef]:
            return {"mcp__Linear__issues__list": _ToolDef()}

        commit_mock = AsyncMock()
        monkeypatch.setattr(
            agent_preset_service, "_resolve_mcp_integrations", _resolve_mcp_integrations
        )
        monkeypatch.setattr(
            preset_service_module,
            "discover_user_mcp_tools",
            _discover_user_mcp_tools,
        )
        monkeypatch.setattr(agent_preset_service.session, "commit", commit_mock)

        discovered = await agent_preset_service.discover_mcp_tools(
            ["40f31ce3-a0c7-4b0a-bf26-2d8dc6a0ea16"]
        )

        assert discovered == [
            preset_service_module.DiscoveredMCPTool(
                name="mcp.Linear.issues.list",
                description="List issues",
                server_name="Linear",
            )
        ]
        commit_mock.assert_not_awaited()
