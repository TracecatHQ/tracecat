import json
import uuid
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest

from tracecat.auth.types import Role
from tracecat.db.models import Action, Workflow, WorkflowDefinition
from tracecat.dsl.common import DSLInput
from tracecat.expressions.expectations import ExpectedField
from tracecat.workflow.management import management
from tracecat.workflow.management.management import WorkflowsManagementService


class _ScalarResult:
    def __init__(self, value: Any):
        self.value = value

    def scalar_one_or_none(self) -> Any:
        return self.value


class _FakeSession:
    def __init__(self, *, graph_version: int, actions: list[Action]):
        self._graph_version = graph_version
        self._actions = actions
        self.execute_count = 0

    async def execute(self, _statement: Any) -> _ScalarResult:
        self.execute_count += 1
        if self.execute_count == 1:
            return _ScalarResult(self._graph_version)
        return _ScalarResult(None)

    async def flush(self) -> None:
        return None

    async def refresh(
        self, instance: Any, attribute_names: list[str] | None = None
    ) -> None:
        if (
            isinstance(instance, Workflow)
            and attribute_names
            and "actions" in attribute_names
        ):
            instance.actions = self._actions


class _FakeWorkflowGraphService:
    def __init__(self, _session: Any, *, role: Role):
        self.role = role

    async def apply_operations(self, **_kwargs: Any) -> None:
        return None


def test_workflow_fields_from_dsl_serializes_expected_fields() -> None:
    dsl = DSLInput(
        **{
            "title": "Workflow with expects",
            "description": "Ensures expects can be written to JSONB columns",
            "entrypoint": {
                "ref": "start",
                "expects": {
                    "report_url": ExpectedField(type="str", optional=True),
                    "report_text": ExpectedField(type="str", optional=True),
                },
            },
            "actions": [
                {
                    "ref": "start",
                    "action": "core.transform.reshape",
                    "args": {"value": "ok"},
                }
            ],
        }
    )

    fields = WorkflowsManagementService._workflow_fields_from_dsl(dsl)

    assert fields["expects"] == {
        "report_url": {
            "type": "str",
            "description": None,
            "enum": None,
            "optional": True,
        },
        "report_text": {
            "type": "str",
            "description": None,
            "enum": None,
            "optional": True,
        },
    }
    json.dumps(fields["expects"])


@pytest.mark.anyio
async def test_restore_workflow_definition_serializes_expected_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_id = uuid.uuid4()
    workflow_id = uuid.uuid4()
    role = Role(
        type="service",
        service_id="tracecat-api",
        organization_id=uuid.uuid4(),
        workspace_id=workspace_id,
        scopes=frozenset({"*"}),
    )
    workflow = Workflow(
        id=workflow_id,
        workspace_id=workspace_id,
        title="Current workflow",
        description="Current description",
        expects={},
        config={},
        graph_version=1,
        version=1,
    )
    definition = WorkflowDefinition(
        workspace_id=workspace_id,
        workflow_id=workflow_id,
        version=2,
        alias="restored-alias",
        registry_lock=None,
        content={
            "title": "Restored workflow",
            "description": "Restored description",
            "entrypoint": {
                "ref": "start",
                "expects": {
                    "report_url": {"type": "str", "optional": True},
                    "report_text": {"type": "str", "optional": True},
                },
            },
            "actions": [
                {
                    "ref": "start",
                    "action": "core.transform.reshape",
                    "args": {"value": "ok"},
                }
            ],
        },
    )
    actions = [
        Action(
            id=uuid.uuid4(),
            workspace_id=workspace_id,
            workflow_id=workflow_id,
            type="core.transform.reshape",
            title="start",
            description="",
            inputs="",
            control_flow={},
        )
    ]
    fake_session = _FakeSession(graph_version=workflow.graph_version, actions=actions)
    service = WorkflowsManagementService(cast(Any, fake_session), role=role)

    async def create_actions_from_dsl(
        self: WorkflowsManagementService,
        dsl: DSLInput,
        workflow_id: uuid.UUID,
    ) -> None:
        return None

    monkeypatch.setattr(
        WorkflowsManagementService,
        "create_actions_from_dsl",
        create_actions_from_dsl,
    )
    monkeypatch.setattr(management, "WorkflowGraphService", _FakeWorkflowGraphService)

    restored = await service.restore_workflow_definition(workflow, definition)

    assert restored.expects == {
        "report_url": {
            "type": "str",
            "description": None,
            "enum": None,
            "optional": True,
        },
        "report_text": {
            "type": "str",
            "description": None,
            "enum": None,
            "optional": True,
        },
    }
    json.dumps(restored.expects)


class _FakeCatalogService:
    """Stub catalog service returning a fixed (provider, name) -> id map."""

    def __init__(
        self,
        *,
        session: Any = None,
        mapping: dict[tuple[str, str], uuid.UUID] | None = None,
        enabled_ids: set[uuid.UUID] | None = None,
    ):
        self._mapping = mapping or {}
        # Incoming catalog_ids treated as already visible+enabled locally.
        self._enabled_ids = enabled_ids or set()
        self.calls: list[tuple[str, str]] = []
        self.enabled_calls: list[uuid.UUID] = []

    async def resolve_catalog_id_by_model(
        self,
        *,
        org_id: uuid.UUID,
        model_provider: str,
        model_name: str,
        workspace_id: uuid.UUID | None = None,
    ) -> uuid.UUID | None:
        self.calls.append((model_provider, model_name))
        return self._mapping.get((model_provider, model_name))

    async def is_catalog_id_enabled(
        self,
        *,
        org_id: uuid.UUID,
        catalog_id: uuid.UUID,
        workspace_id: uuid.UUID | None = None,
    ) -> bool:
        self.enabled_calls.append(catalog_id)
        return catalog_id in self._enabled_ids


def _agent_dsl(args: dict[str, Any], *, action: str = "ai.agent") -> DSLInput:
    return DSLInput(
        **{
            "title": "agent wf",
            "description": "",
            "entrypoint": {"ref": "agent", "expects": {}},
            "actions": [
                {
                    "ref": "agent",
                    "action": action,
                    "args": args,
                }
            ],
        }
    )


def _service(role: Role) -> WorkflowsManagementService:
    return WorkflowsManagementService(cast(Any, object()), role=role)


def _role() -> Role:
    return Role(
        type="service",
        service_id="tracecat-api",
        organization_id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        scopes=frozenset({"*"}),
    )


@pytest.mark.anyio
async def test_correlate_agent_catalog_ids_rewrites_nested_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    old_id = uuid.uuid4()
    local_id = uuid.uuid4()
    mapping = {("anthropic", "claude-opus-4-8"): local_id}
    monkeypatch.setattr(
        management,
        "AgentCatalogService",
        lambda session: _FakeCatalogService(session=session, mapping=mapping),
    )
    dsl = _agent_dsl(
        {
            "user_prompt": "hi",
            "model": {
                "model_name": "claude-opus-4-8",
                "model_provider": "anthropic",
                "catalog_id": str(old_id),
            },
        }
    )

    out = await _service(_role()).correlate_agent_catalog_ids(dsl)

    assert out.actions[0].args["model"]["catalog_id"] == str(local_id)


@pytest.mark.anyio
async def test_correlate_agent_catalog_ids_rewrites_flat_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    old_id = uuid.uuid4()
    local_id = uuid.uuid4()
    mapping = {("openai", "gpt-4.1"): local_id}
    monkeypatch.setattr(
        management,
        "AgentCatalogService",
        lambda session: _FakeCatalogService(session=session, mapping=mapping),
    )
    dsl = _agent_dsl(
        {
            "user_prompt": "hi",
            "model_name": "gpt-4.1",
            "model_provider": "openai",
            "catalog_id": str(old_id),
        }
    )

    out = await _service(_role()).correlate_agent_catalog_ids(dsl)

    assert out.actions[0].args["catalog_id"] == str(local_id)


@pytest.mark.anyio
async def test_external_import_publishes_before_online_case_trigger(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    role = _role()
    workflow = SimpleNamespace(id=uuid.uuid4(), version=None)
    session = SimpleNamespace(add=MagicMock(), flush=AsyncMock(), commit=AsyncMock())
    service = WorkflowsManagementService(cast(Any, session), role=role)
    dsl = DSLInput(
        **{
            "title": "online case trigger import",
            "description": "",
            "entrypoint": {"ref": "start", "expects": {}},
            "actions": [
                {
                    "ref": "start",
                    "action": "core.transform.reshape",
                    "args": {"value": "ok"},
                }
            ],
        }
    )
    definition_calls: list[dict[str, Any]] = []
    case_trigger_calls: list[dict[str, Any]] = []

    class FakeWorkflowDefinitionsService:
        def __init__(self, session: Any, role: Role) -> None:
            self.session = session
            self.role = role

        async def create_workflow_definition(
            self,
            workflow_id: uuid.UUID,
            dsl: DSLInput,
            *,
            commit: bool = True,
        ) -> SimpleNamespace:
            definition_calls.append(
                {"workflow_id": workflow_id, "dsl": dsl, "commit": commit}
            )
            return SimpleNamespace(version=1)

    class FakeCaseTriggersService:
        def __init__(self, session: Any, role: Role) -> None:
            self.session = session
            self.role = role

        async def upsert_case_trigger(
            self,
            workflow_id: uuid.UUID,
            params: Any,
            *,
            create_missing_tags: bool = False,
            commit: bool = True,
        ) -> None:
            case_trigger_calls.append(
                {
                    "workflow_id": workflow_id,
                    "params": params,
                    "create_missing_tags": create_missing_tags,
                    "commit": commit,
                }
            )

    monkeypatch.setattr(
        service,
        "create_db_workflow_from_dsl",
        AsyncMock(return_value=workflow),
    )
    monkeypatch.setattr(
        service,
        "correlate_agent_catalog_ids",
        AsyncMock(return_value=dsl),
    )
    monkeypatch.setattr(
        management,
        "WorkflowDefinitionsService",
        FakeWorkflowDefinitionsService,
    )
    monkeypatch.setattr(management, "CaseTriggersService", FakeCaseTriggersService)

    imported = await service.create_workflow_from_external_definition(
        {
            "definition": dsl.model_dump(mode="json"),
            "case_trigger": {
                "status": "online",
                "event_types": ["case_created"],
                "tag_filters": ["phishing"],
            },
        }
    )

    assert imported is workflow
    assert workflow.version == 1
    assert definition_calls == [
        {
            "workflow_id": workflow.id,
            "dsl": dsl,
            "commit": False,
        }
    ]
    session.add.assert_called_once_with(workflow)
    session.flush.assert_awaited_once()
    session.commit.assert_awaited_once()
    assert len(case_trigger_calls) == 1
    assert case_trigger_calls[0]["workflow_id"] == workflow.id
    assert case_trigger_calls[0]["params"].status == "online"
    assert case_trigger_calls[0]["params"].event_types == ["case_created"]
    assert case_trigger_calls[0]["params"].tag_filters == ["phishing"]
    assert case_trigger_calls[0]["create_missing_tags"] is True
    assert case_trigger_calls[0]["commit"] is False


@pytest.mark.anyio
async def test_correlate_agent_catalog_ids_keeps_id_on_no_match(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    old_id = uuid.uuid4()
    monkeypatch.setattr(
        management,
        "AgentCatalogService",
        lambda session: _FakeCatalogService(session=session, mapping={}),
    )
    dsl = _agent_dsl(
        {
            "user_prompt": "hi",
            "model": {
                "model_name": "claude-opus-4-8",
                "model_provider": "anthropic",
                "catalog_id": str(old_id),
            },
        }
    )

    out = await _service(_role()).correlate_agent_catalog_ids(dsl)

    assert out.actions[0].args["model"]["catalog_id"] == str(old_id)


@pytest.mark.anyio
async def test_correlate_agent_catalog_ids_ignores_non_agent_actions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        management,
        "AgentCatalogService",
        lambda session: _FakeCatalogService(
            session=session, mapping={("anthropic", "claude-opus-4-8"): uuid.uuid4()}
        ),
    )
    dsl = _agent_dsl({"value": "ok"}, action="core.transform.reshape")

    out = await _service(_role()).correlate_agent_catalog_ids(dsl)

    assert out is dsl


@pytest.mark.anyio
async def test_correlate_agent_catalog_ids_skips_when_no_catalog_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        management,
        "AgentCatalogService",
        lambda session: _FakeCatalogService(
            session=session, mapping={("anthropic", "claude-opus-4-8"): uuid.uuid4()}
        ),
    )
    dsl = _agent_dsl(
        {
            "user_prompt": "hi",
            "model": {
                "model_name": "claude-opus-4-8",
                "model_provider": "anthropic",
            },
        }
    )

    out = await _service(_role()).correlate_agent_catalog_ids(dsl)

    assert "catalog_id" not in out.actions[0].args["model"]


@pytest.mark.anyio
async def test_correlate_agent_catalog_ids_caches_duplicate_tuples(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Repeated (provider, name) across actions resolves with a single query."""
    local_id = uuid.uuid4()
    fake = _FakeCatalogService(mapping={("anthropic", "claude-opus-4-8"): local_id})
    monkeypatch.setattr(management, "AgentCatalogService", lambda session: fake)

    def _agent(ref: str, foreign: str) -> dict[str, Any]:
        return {
            "ref": ref,
            "action": "ai.agent",
            "args": {
                "user_prompt": "hi",
                "model": {
                    "model_name": "claude-opus-4-8",
                    "model_provider": "anthropic",
                    "catalog_id": foreign,
                },
            },
        }

    dsl = DSLInput(
        **{
            "title": "multi agent wf",
            "description": "",
            "entrypoint": {"ref": "a1", "expects": {}},
            "actions": [
                _agent("a1", str(uuid.uuid4())),
                _agent("a2", str(uuid.uuid4())),
                _agent("a3", str(uuid.uuid4())),
            ],
        }
    )

    out = await _service(_role()).correlate_agent_catalog_ids(dsl)

    # Three agent actions, same model -> resolver queried exactly once.
    assert fake.calls == [("anthropic", "claude-opus-4-8")]
    for action in out.actions:
        assert action.args["model"]["catalog_id"] == str(local_id)


@pytest.mark.anyio
async def test_correlate_agent_catalog_ids_mixed_nested_and_flat_catalog_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Nested provider/name + a top-level catalog_id: the flat id is remapped.

    AgentActionArgs merges nested over flat per-key, so a missing nested
    catalog_id leaves the top-level one in force. The correlation must read and
    rewrite it instead of skipping.
    """
    old_id = uuid.uuid4()
    local_id = uuid.uuid4()
    mapping = {("anthropic", "claude-opus-4-8"): local_id}
    monkeypatch.setattr(
        management,
        "AgentCatalogService",
        lambda session: _FakeCatalogService(session=session, mapping=mapping),
    )
    dsl = _agent_dsl(
        {
            "user_prompt": "hi",
            "model": {
                "model_name": "claude-opus-4-8",
                "model_provider": "anthropic",
            },
            "catalog_id": str(old_id),
        }
    )

    out = await _service(_role()).correlate_agent_catalog_ids(dsl)

    # The effective (top-level) catalog_id is remapped.
    assert out.actions[0].args["catalog_id"] == str(local_id)
    # Nested block had no catalog_id; it is left without one.
    assert "catalog_id" not in out.actions[0].args["model"]


@pytest.mark.anyio
async def test_correlate_agent_catalog_ids_rewrites_ai_action(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``ai.action`` uses the same model selection and must be correlated too."""
    old_id = uuid.uuid4()
    local_id = uuid.uuid4()
    mapping = {("anthropic", "claude-opus-4-8"): local_id}
    fake = _FakeCatalogService(mapping=mapping)
    monkeypatch.setattr(management, "AgentCatalogService", lambda session: fake)
    dsl = _agent_dsl(
        {
            "user_prompt": "hi",
            "model": {
                "model_name": "claude-opus-4-8",
                "model_provider": "anthropic",
                "catalog_id": str(old_id),
            },
        },
        action="ai.action",
    )

    out = await _service(_role()).correlate_agent_catalog_ids(dsl)

    assert fake.calls == [("anthropic", "claude-opus-4-8")]
    assert out.actions[0].args["model"]["catalog_id"] == str(local_id)


@pytest.mark.anyio
async def test_correlate_agent_catalog_ids_preserves_already_local_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An incoming catalog_id already enabled here is kept, not re-resolved.

    A same-environment pull must not rewrite the user's selected row to another
    enabled duplicate that merely wins the (provider, name) tuple resolver.
    """
    selected_id = uuid.uuid4()
    other_enabled_id = uuid.uuid4()
    # Tuple resolver would prefer a *different* enabled row.
    mapping = {("anthropic", "claude-opus-4-8"): other_enabled_id}
    fake = _FakeCatalogService(mapping=mapping, enabled_ids={selected_id})
    monkeypatch.setattr(management, "AgentCatalogService", lambda session: fake)
    dsl = _agent_dsl(
        {
            "user_prompt": "hi",
            "model": {
                "model_name": "claude-opus-4-8",
                "model_provider": "anthropic",
                "catalog_id": str(selected_id),
            },
        }
    )

    out = await _service(_role()).correlate_agent_catalog_ids(dsl)

    # The selected id is preserved and the tuple resolver was never queried.
    assert out.actions[0].args["model"]["catalog_id"] == str(selected_id)
    assert fake.enabled_calls == [selected_id]
    assert fake.calls == []
