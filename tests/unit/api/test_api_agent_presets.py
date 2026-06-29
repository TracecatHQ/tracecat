"""HTTP-level tests for agent preset API endpoints."""

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException, status

from tracecat.agent.preset import internal_router as agent_preset_internal_router
from tracecat.agent.preset import router as agent_preset_router
from tracecat.auth.types import Role
from tracecat.exceptions import TracecatNotFoundError, TracecatValidationError


@pytest.mark.anyio
async def test_create_preset_payload_backfills_legacy_model_fields(
    test_admin_role: Role,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    catalog_id = uuid.uuid4()

    class FakeAgentManagementService:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def get_default_model_selection(self) -> SimpleNamespace:
            return SimpleNamespace(
                model_name="claude-sonnet-4",
                model_provider="anthropic",
                catalog_id=catalog_id,
            )

    monkeypatch.setattr(
        agent_preset_internal_router,
        "AgentManagementService",
        FakeAgentManagementService,
    )

    payload = await agent_preset_internal_router._create_payload_with_default_model(
        role=test_admin_role,
        session=AsyncMock(),
        params=agent_preset_internal_router.PresetCreateRequest(name="Case Triage"),
    )

    assert payload == {
        "name": "Case Triage",
        # AgentPresetCreate still requires these deprecated legacy fields.
        "model_name": "claude-sonnet-4",
        "model_provider": "anthropic",
        "catalog_id": catalog_id,
    }


@pytest.mark.anyio
async def test_create_preset_payload_requires_default_model_selection(
    test_admin_role: Role,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeAgentManagementService:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def get_default_model_selection(self) -> None:
            return None

    monkeypatch.setattr(
        agent_preset_internal_router,
        "AgentManagementService",
        FakeAgentManagementService,
    )

    with pytest.raises(TracecatNotFoundError, match="No default model set"):
        await agent_preset_internal_router._create_payload_with_default_model(
            role=test_admin_role,
            session=AsyncMock(),
            params=agent_preset_internal_router.PresetCreateRequest(name="Case Triage"),
        )


@pytest.mark.anyio
async def test_create_preset_payload_resolves_catalog_id_without_default_model(
    test_admin_role: Role,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected_catalog_id = uuid.uuid4()
    session = AsyncMock()

    class FakeAgentPresetService:
        def __init__(self, service_session: object, *, role: Role) -> None:
            assert service_session == session
            assert role == test_admin_role

        async def _get_enabled_catalog_entry(
            self,
            catalog_id: uuid.UUID,
        ) -> SimpleNamespace:
            assert catalog_id == expected_catalog_id
            return SimpleNamespace(
                model_name="claude-sonnet-4",
                model_provider="anthropic",
            )

    monkeypatch.setattr(
        agent_preset_internal_router,
        "AgentPresetService",
        FakeAgentPresetService,
    )

    payload = await agent_preset_internal_router._create_payload_with_default_model(
        role=test_admin_role,
        session=session,
        params=agent_preset_internal_router.PresetCreateRequest(
            name="Case Triage",
            catalog_id=expected_catalog_id,
        ),
    )

    assert payload == {
        "name": "Case Triage",
        "catalog_id": expected_catalog_id,
        "model_name": "claude-sonnet-4",
        "model_provider": "anthropic",
    }


@pytest.mark.anyio
async def test_create_preset_payload_preserves_default_catalog_when_null_supplied(
    test_admin_role: Role,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    catalog_id = uuid.uuid4()

    class FakeAgentManagementService:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def get_default_model_selection(self) -> SimpleNamespace:
            return SimpleNamespace(
                model_name="claude-sonnet-4",
                model_provider="anthropic",
                catalog_id=catalog_id,
            )

    monkeypatch.setattr(
        agent_preset_internal_router,
        "AgentManagementService",
        FakeAgentManagementService,
    )

    payload = await agent_preset_internal_router._create_payload_with_default_model(
        role=test_admin_role,
        session=AsyncMock(),
        params=agent_preset_internal_router.PresetCreateRequest(
            name="Case Triage",
            catalog_id=None,
        ),
    )

    assert payload == {
        "name": "Case Triage",
        "model_name": "claude-sonnet-4",
        "model_provider": "anthropic",
        "catalog_id": catalog_id,
    }


@pytest.mark.anyio
async def test_create_preset_payload_drops_null_defaulted_fields(
    test_admin_role: Role,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    catalog_id = uuid.uuid4()

    class FakeAgentManagementService:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def get_default_model_selection(self) -> SimpleNamespace:
            return SimpleNamespace(
                model_name="claude-sonnet-4",
                model_provider="anthropic",
                catalog_id=catalog_id,
            )

    monkeypatch.setattr(
        agent_preset_internal_router,
        "AgentManagementService",
        FakeAgentManagementService,
    )

    payload = await agent_preset_internal_router._create_payload_with_default_model(
        role=test_admin_role,
        session=AsyncMock(),
        params=agent_preset_internal_router.PresetCreateRequest(
            name="Case Triage",
            agents=None,
            retries=None,
        ),
    )

    assert "agents" not in payload
    assert "retries" not in payload
    assert payload["model_name"] == "claude-sonnet-4"
    assert payload["model_provider"] == "anthropic"


@pytest.mark.anyio
async def test_create_preset_payload_rejects_partial_legacy_model_fields(
    test_admin_role: Role,
) -> None:
    with pytest.raises(
        TracecatValidationError,
        match="model_name and model_provider must be provided together",
    ):
        await agent_preset_internal_router._create_payload_with_default_model(
            role=test_admin_role,
            session=AsyncMock(),
            params=agent_preset_internal_router.PresetCreateRequest(
                name="Case Triage",
                model_name="gpt-4o-mini",
            ),
        )


@pytest.mark.anyio
async def test_restore_agent_preset_version_maps_validation_error(
    test_admin_role: Role,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Validation failures during restore should remain client errors."""

    preset_id = uuid.uuid4()
    version_id = uuid.uuid4()
    service = AsyncMock()
    service.get_preset.return_value = SimpleNamespace(id=preset_id)
    service.get_version.return_value = SimpleNamespace(
        id=version_id,
        preset_id=preset_id,
    )
    service.restore_version.side_effect = TracecatValidationError(
        "Skill binding could not be restored"
    )
    monkeypatch.setattr(
        agent_preset_router,
        "AgentPresetService",
        lambda *args, **kwargs: service,
    )

    with pytest.raises(HTTPException) as exc_info:
        await agent_preset_router.restore_agent_preset_version(
            preset_id=preset_id,
            version_id=version_id,
            role=test_admin_role,
            session=AsyncMock(),
        )

    assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST
    assert exc_info.value.detail == "Skill binding could not be restored"
    service.restore_version.assert_awaited_once()
