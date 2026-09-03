from __future__ import annotations

import uuid
from collections.abc import Iterator
from typing import get_args
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
from temporalio.client import WorkflowFailureError

from tracecat.agent import internal_router
from tracecat.agent.internal_router import router
from tracecat.agent.schemas import (
    AgentConfigSchema,
    AgentOutput,
    InternalRunAgentRequest,
    RunAgentArgs,
    RunUsage,
)
from tracecat.auth.dependencies import ExecutorWorkspaceRole
from tracecat.auth.types import Role
from tracecat.db.engine import get_async_session


@pytest.fixture
def executor_role() -> Role:
    return Role(
        type="service",
        service_id="tracecat-executor",
        workspace_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        scopes=frozenset({"agent:execute"}),
    )


@pytest.fixture
def client(executor_role: Role) -> Iterator[TestClient]:
    app = FastAPI()
    app.include_router(router)
    role_dependency = get_args(ExecutorWorkspaceRole)[1].dependency
    app.dependency_overrides[role_dependency] = lambda: executor_role

    async def override_session() -> AsyncMock:
        return AsyncMock()

    app.dependency_overrides[get_async_session] = override_session
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client


def _agent_output(output: object) -> AgentOutput:
    return AgentOutput(
        output=output,
        duration=1.25,
        session_id=uuid.uuid4(),
        usage=RunUsage(
            requests=2,
            tool_calls=1,
            input_tokens=10,
            output_tokens=5,
        ),
    )


def test_run_accepts_old_body_and_returns_old_response_shape(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    execute = AsyncMock(return_value=_agent_output({"answer": "done"}))
    monkeypatch.setattr(internal_router, "_execute_agent_workflow", execute)
    catalog_id = uuid.uuid4()

    response = client.post(
        "/internal/agent/run",
        json={
            "user_prompt": "Investigate the alert",
            "config": {
                "model_name": "test-model",
                "model_provider": "test-provider",
                "catalog_id": str(catalog_id),
                "base_url": "https://models.example.test",
                "instructions": "Use the connected tools",
                "output_type": {"type": "object"},
                "actions": ["core.http_request"],
                "namespaces": ["core"],
                "tool_approvals": {},
                "model_settings": {"temperature": 0},
                "mcp_servers": [
                    {
                        "name": "github",
                        "url": "https://mcp.example.test",
                        "headers": {"Authorization": "Bearer synthetic-token"},
                        "transport": "http",
                        "timeout": 30,
                    }
                ],
                "retries": 4,
                "enable_thinking": False,
            },
            "max_requests": 12,
            "max_tool_calls": 7,
        },
    )

    assert response.status_code == 200
    assert set(response.json()) == {
        "output",
        "message_history",
        "duration",
        "usage",
        "session_id",
    }
    assert response.json()["output"] == {"answer": "done"}
    assert execute.await_args is not None
    workflow_args = execute.await_args.args[0]
    assert workflow_args.agent_args.user_prompt == "Investigate the alert"
    assert workflow_args.agent_args.max_requests == 12
    assert workflow_args.agent_args.max_tool_calls == 7
    assert workflow_args.agent_args.config.catalog_id == catalog_id
    assert workflow_args.agent_args.config.mcp_servers[0]["name"] == "github"


def test_run_passes_preset_through_to_durable_workflow(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    execute = AsyncMock(return_value=_agent_output("done"))
    monkeypatch.setattr(internal_router, "_execute_agent_workflow", execute)

    response = client.post(
        "/internal/agent/run",
        json={
            "user_prompt": "Investigate the alert",
            "preset_slug": "triage",
            "preset_version": 2,
        },
    )

    assert response.status_code == 200
    assert execute.await_args is not None
    workflow_args = execute.await_args.args[0]
    assert workflow_args.agent_args.config is None
    assert workflow_args.agent_args.preset_slug == "triage"
    assert workflow_args.agent_args.preset_version == 2


def test_rank_filters_unknown_ids_appends_missing_and_applies_limits(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    execute = AsyncMock(
        return_value=_agent_output({"ranked_ids": ["unknown", "c", "c"]})
    )
    monkeypatch.setattr(internal_router, "_execute_agent_workflow", execute)

    response = client.post(
        "/internal/agent/rank",
        json={
            "items": [
                {"id": "a", "text": "Alpha"},
                {"id": "b", "text": "Beta"},
                {"id": "c", "text": "Gamma"},
            ],
            "criteria_prompt": "Most urgent first",
            "model_name": "test-model",
            "model_provider": "test-provider",
            "model_settings": {"temperature": 0},
            "max_requests": 5,
            "retries": 3,
            "base_url": "https://models.example.test",
            "min_items": 2,
            "max_items": 2,
        },
    )

    assert response.status_code == 200
    assert response.json() == ["c", "a"]
    assert execute.await_args is not None
    workflow_args = execute.await_args.args[0]
    assert workflow_args.agent_args.max_tool_calls == 0
    assert workflow_args.agent_args.config.actions is None
    assert workflow_args.agent_args.config.output_type == {
        "type": "object",
        "properties": {
            "ranked_ids": {
                "type": "array",
                "items": {"anyOf": [{"type": "string"}, {"type": "integer"}]},
            }
        },
        "required": ["ranked_ids"],
        "additionalProperties": False,
    }


def test_rank_pairwise_accepts_old_body_and_ignores_algorithm_fields(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    execute = AsyncMock(return_value=_agent_output({"ranked_ids": ["`2`", "unknown"]}))
    monkeypatch.setattr(internal_router, "_execute_agent_workflow", execute)

    response = client.post(
        "/internal/agent/rank-pairwise",
        json={
            "items": [
                {"id": 1, "text": "Low"},
                {"id": 2, "text": "High"},
                {"id": 3, "text": "Medium"},
            ],
            "criteria_prompt": "Highest priority first",
            "model_name": "test-model",
            "model_provider": "test-provider",
            "id_field": "legacy_id",
            "batch_size": 2,
            "num_passes": 7,
            "refinement_ratio": 0.25,
            "max_requests": 4,
            "retries": 2,
        },
    )

    assert response.status_code == 200
    assert response.json() == [2, 1, 3]
    assert execute.await_args is not None
    workflow_args = execute.await_args.args[0]
    assert workflow_args.agent_args.max_requests == 4
    assert "legacy_id" not in workflow_args.agent_args.user_prompt


def test_rank_rejects_output_without_ranked_ids_envelope(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    execute = AsyncMock(return_value=_agent_output(["a"]))
    monkeypatch.setattr(internal_router, "_execute_agent_workflow", execute)

    response = client.post(
        "/internal/agent/rank",
        json={
            "items": [{"id": "a", "text": "Alpha"}],
            "criteria_prompt": "Most urgent first",
            "model_name": "test-model",
            "model_provider": "test-provider",
        },
    )

    assert response.status_code == 400
    assert response.json() == {
        "detail": 'Ranking agent must return a JSON object with a "ranked_ids" list'
    }


def test_rank_maps_value_error_to_400(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    execute = AsyncMock(side_effect=ValueError("invalid ranking input"))
    monkeypatch.setattr(internal_router, "_execute_agent_workflow", execute)

    response = client.post(
        "/internal/agent/rank",
        json={
            "items": [{"id": "a", "text": "Alpha"}],
            "criteria_prompt": "Most urgent first",
            "model_name": "test-model",
            "model_provider": "test-provider",
        },
    )

    assert response.status_code == 400
    assert response.json() == {"detail": "invalid ranking input"}


def test_run_maps_workflow_failure_to_500(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    execute = AsyncMock(
        side_effect=WorkflowFailureError(cause=RuntimeError("durable run failed"))
    )
    monkeypatch.setattr(internal_router, "_execute_agent_workflow", execute)

    response = client.post(
        "/internal/agent/run",
        json={
            "user_prompt": "Investigate the alert",
            "config": {
                "model_name": "test-model",
                "model_provider": "test-provider",
            },
        },
    )

    assert response.status_code == 500
    assert response.json() == {
        "detail": {
            "error_type": "RuntimeError",
            "message": "durable run failed",
        }
    }


@pytest.mark.parametrize("max_requests", [-1, 0])
def test_internal_run_agent_request_rejects_non_positive_max_requests(
    max_requests: int,
) -> None:
    with pytest.raises(ValidationError):
        InternalRunAgentRequest(
            user_prompt="Investigate the alert",
            config=AgentConfigSchema(
                model_name="test-model",
                model_provider="test-provider",
            ),
            max_requests=max_requests,
        )


@pytest.mark.parametrize(
    ("max_requests", "max_tool_calls"),
    [(-1, -1), (0, 0)],
)
def test_run_agent_args_pins_persisted_shape_and_accepts_legacy_limits(
    max_requests: int,
    max_tool_calls: int,
) -> None:
    # Temporal replays deserialize from a dict; bounds must not run here.
    args = RunAgentArgs.model_validate(
        {
            "user_prompt": "Investigate the alert",
            "session_id": str(uuid.uuid4()),
            "preset_slug": "triage",
            "max_requests": max_requests,
            "max_tool_calls": max_tool_calls,
        }
    )

    assert args.max_requests == max_requests
    assert args.max_tool_calls == max_tool_calls
