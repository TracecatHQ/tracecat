from __future__ import annotations

import uuid
from datetime import date
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, patch

import pytest
import yaml

from tracecat.agent.catalog.service import AgentCatalogService
from tracecat.agent.catalog.types import ModelKey, ModelLifecycle
from tracecat.exceptions import RegistryValidationError
from tracecat.registry.repository import Repository
from tracecat.sync import CatalogMappingCandidate
from tracecat.workspace_sync.adapters import AGENT_PRESET_RESOURCE_ADAPTER
from tracecat.workspace_sync.adapters.base import SyncMappingService
from tracecat.workspace_sync.schemas import (
    AgentPresetResourceSpec,
    WorkflowResourceSpec,
)
from tracecat.workspace_sync.workflow import serialize_workflow_spec


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(scope="module")
def registry_repository() -> Repository:
    repository = Repository()
    repository.init(include_base=True, include_templates=False)
    return repository


@pytest.mark.anyio
@pytest.mark.parametrize("action_type", ["ai.agent", "ai.action"])
async def test_catalog_remap_keeps_nested_model_catalog_id_nested(
    action_type: str,
    registry_repository: Repository,
) -> None:
    source_catalog_id = uuid.uuid4()
    target_catalog_id = uuid.uuid4()
    model_key = ModelKey("openai", "test-model")
    workflow = WorkflowResourceSpec.model_validate(
        {
            "id": "nested-model-workflow",
            "definition": {
                "title": "Nested model workflow",
                "description": "",
                "entrypoint": {"ref": "run_agent", "expects": {}},
                "actions": [
                    {
                        "ref": "run_agent",
                        "action": action_type,
                        "args": {
                            "user_prompt": "Investigate the event.",
                            "model": {
                                "model_provider": model_key.model_provider,
                                "model_name": model_key.model_name,
                                "catalog_id": str(source_catalog_id),
                            },
                        },
                    }
                ],
            },
        }
    )
    workspace_service = cast(
        SyncMappingService,
        SimpleNamespace(
            session=object(),
            organization_id=uuid.uuid4(),
            workspace_id=uuid.uuid4(),
        ),
    )
    candidate = CatalogMappingCandidate(
        catalog_id=target_catalog_id,
        model_provider=model_key.model_provider,
        model_name=model_key.model_name,
        provider_name="OpenAI",
        model_display_name=None,
        endpoint_hostname=None,
        origin="platform",
    )

    with (
        patch.object(
            AgentCatalogService,
            "enabled_catalog_models",
            AsyncMock(return_value={}),
        ),
        patch.object(
            AgentCatalogService,
            "catalog_candidates_by_models",
            AsyncMock(return_value={model_key: [candidate]}),
        ),
    ):
        correlated = await AGENT_PRESET_RESOURCE_ADAPTER.correlate_catalog_ids(
            workspace_service,
            presets={},
            workflows={"nested-model-workflow": workflow},
        )

    correlated_workflow = correlated.workflows["nested-model-workflow"]
    action_args = correlated_workflow.definition.actions[0].args
    nested_model = action_args["model"]
    assert isinstance(nested_model, dict)
    assert "catalog_id" not in action_args
    assert nested_model["catalog_id"] == str(target_catalog_id)

    serialized = yaml.safe_load(serialize_workflow_spec(correlated_workflow))
    serialized_args = serialized["definition"]["actions"][0]["args"]
    assert "catalog_id" not in serialized_args
    assert serialized_args["model"]["catalog_id"] == str(target_catalog_id)

    bound_action = registry_repository.get(action_type)
    validated_args = bound_action.validate_args(serialized_args)
    assert validated_args["model"]["catalog_id"] == str(target_catalog_id)

    with pytest.raises(RegistryValidationError):
        bound_action.validate_args(
            {**serialized_args, "catalog_id": str(target_catalog_id)}
        )


@pytest.mark.anyio
async def test_catalog_correlation_clears_retired_model_catalog_ids(
    registry_repository: Repository,
) -> None:
    retired_key = ModelKey("openai", "retired-model")
    source_catalog_id = uuid.uuid4()
    workflow = WorkflowResourceSpec.model_validate(
        {
            "id": "retired-model-workflow",
            "definition": {
                "title": "Retired model workflow",
                "description": "",
                "entrypoint": {"ref": "run_agent", "expects": {}},
                "actions": [
                    {
                        "ref": "run_agent",
                        "action": "ai.agent",
                        "args": {
                            "user_prompt": "Investigate the event.",
                            "model": {
                                "model_provider": retired_key.model_provider,
                                "model_name": retired_key.model_name,
                                "catalog_id": str(source_catalog_id),
                            },
                        },
                    }
                ],
            },
        }
    )
    preset = AgentPresetResourceSpec.model_validate(
        {
            "id": "retired-model-preset",
            "slug": "retired-model-preset",
            "name": "Retired model preset",
            "model_provider": retired_key.model_provider,
            "model_name": retired_key.model_name,
            "catalog_id": str(source_catalog_id),
        }
    )
    workspace_service = cast(
        SyncMappingService,
        SimpleNamespace(
            session=object(),
            organization_id=uuid.uuid4(),
            workspace_id=uuid.uuid4(),
        ),
    )

    with (
        patch.object(
            AgentCatalogService, "enabled_catalog_models", AsyncMock(return_value={})
        ),
        patch.object(
            AgentCatalogService,
            "catalog_candidates_by_models",
            AsyncMock(return_value={}),
        ),
        patch(
            "tracecat.workspace_sync.adapters.agent_preset.get_retired_platform_models",
            return_value={
                retired_key: ModelLifecycle(
                    retires_on=date(2000, 1, 1), replacement="current-model"
                )
            },
        ),
    ):
        correlated = await AGENT_PRESET_RESOURCE_ADAPTER.correlate_catalog_ids(
            workspace_service,
            presets={"retired-model-preset": preset},
            workflows={"retired-model-workflow": workflow},
        )

    # The deleted row's ID is cleared; runs swap the retired model by name.
    assert correlated.diagnostics == []
    assert correlated.presets["retired-model-preset"].catalog_id is None
    action_args = (
        correlated.workflows["retired-model-workflow"].definition.actions[0].args
    )
    assert action_args["model"]["catalog_id"] is None
    registry_repository.get("ai.agent").validate_args(action_args)
