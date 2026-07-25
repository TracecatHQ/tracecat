import json
import uuid
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest

from tracecat.auth.types import Role
from tracecat.db.models import Action, Workflow, WorkflowDefinition
from tracecat.dsl.common import DSLInput
from tracecat.exceptions import TracecatNotFoundError, TracecatValidationError
from tracecat.expressions.expectations import ExpectedField
from tracecat.identifiers.workflow import WorkflowUUID
from tracecat.registry.lock.types import RegistryLock
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

    async def apply_operations_to_locked_workflow(
        self, *_args: Any, **_kwargs: Any
    ) -> None:
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
    registry_lock = {
        "origins": {"tracecat_registry": "test-version"},
        "actions": {"core.transform.reshape": "tracecat_registry"},
        "origin_fingerprints": {},
    }
    workflow = SimpleNamespace(
        id=uuid.uuid4(),
        version=None,
        alias="imported-case-trigger",
        registry_lock=registry_lock,
    )
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

        async def create_initial_workflow_definition(
            self,
            workflow_id: uuid.UUID,
            dsl: DSLInput,
            *,
            alias: str | None = None,
            registry_lock: RegistryLock | None = None,
            commit: bool = True,
        ) -> SimpleNamespace:
            definition_calls.append(
                {
                    "workflow_id": workflow_id,
                    "dsl": dsl,
                    "alias": alias,
                    "registry_lock": registry_lock,
                    "commit": commit,
                }
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
            "alias": "imported-case-trigger",
            "registry_lock": RegistryLock.model_validate(registry_lock),
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


def _edit_document_workflow_source() -> SimpleNamespace:
    """Minimal workflow source for ``build_workflow_edit_document``."""
    return SimpleNamespace(
        id=uuid.uuid4(),
        title="Edit document workflow",
        description="",
        status="offline",
        alias=None,
        error_handler=None,
        entrypoint=None,
        expects=None,
        config=None,
        returns=None,
        trigger_position_x=None,
        trigger_position_y=None,
        viewport_x=None,
        viewport_y=None,
        viewport_zoom=None,
        actions=[],
        schedules=[],
        case_trigger=None,
    )


@pytest.mark.anyio
async def test_persist_edit_document_wraps_case_trigger_validation_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An online case trigger on an unpublished workflow becomes WorkflowEditError.

    ``CaseTriggersService`` raises ``TracecatValidationError``
    for correctable authoring mistakes (e.g. enabling a case trigger before the
    workflow is published). ``persist_workflow_edit_document`` must convert that
    into a transport-neutral ``WorkflowEditError`` so the internal edit route
    (which only catches ``WorkflowEditError``/``ToolError``) returns a 400 the
    caller can fix instead of letting it escape as a 500.
    """
    from tracecat.cases.enums import CaseEventType
    from tracecat.exceptions import TracecatValidationError
    from tracecat.workflow.case_triggers.schemas import CaseTriggerConfig
    from tracecat.workflow.management import draft as draft_module
    from tracecat.workflow.management.draft import (
        WorkflowEditError,
        build_workflow_edit_document,
        persist_workflow_edit_document,
    )

    role = _role()
    source = _edit_document_workflow_source()
    original_document = build_workflow_edit_document(cast(Any, source))
    updated_document = original_document.model_copy(
        update={
            "case_trigger": CaseTriggerConfig(
                status="online",
                event_types=[CaseEventType.CASE_CREATED],
                tag_filters=[],
            )
        }
    )

    class FailingCaseTriggersService:
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
            raise TracecatValidationError(
                "Publish the workflow before enabling case triggers"
            )

    monkeypatch.setattr(draft_module, "CaseTriggersService", FailingCaseTriggersService)
    session = SimpleNamespace(
        add=MagicMock(), flush=AsyncMock(), commit=AsyncMock(), refresh=AsyncMock()
    )
    service = WorkflowsManagementService(cast(Any, session), role=role)

    with pytest.raises(WorkflowEditError) as exc:
        await persist_workflow_edit_document(
            role=role,
            service=service,
            workflow=cast(Any, source),
            original_document=original_document,
            updated_document=updated_document,
        )
    assert not isinstance(exc.value, TracecatValidationError)
    assert "case trigger" in str(exc.value).lower()
    session.commit.assert_not_awaited()


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


@pytest.mark.anyio
async def test_validate_edit_document_runs_case_trigger_check_on_dry_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """validate_only must run the case-trigger online-readiness check.

    Regression: previously the dry-run path returned ``valid: true`` without
    running ``CaseTriggersService`` validation, so enabling a case trigger on an
    unpublished workflow passed validation but failed on a real apply. The check
    now runs inside ``validate_workflow_edit_document`` (no commit), so a dry run
    reports the same WorkflowEditError a real apply would.
    """
    from tracecat.cases.enums import CaseEventType
    from tracecat.exceptions import TracecatValidationError
    from tracecat.workflow.case_triggers.schemas import CaseTriggerConfig
    from tracecat.workflow.management import draft as draft_module
    from tracecat.workflow.management.draft import (
        WorkflowEditError,
        build_workflow_edit_document,
        validate_workflow_edit_document,
        workflow_edit_document_changed_sections,
    )

    role = _role()
    source = _edit_document_workflow_source()
    original_document = build_workflow_edit_document(cast(Any, source))
    updated_document = original_document.model_copy(
        update={
            "case_trigger": CaseTriggerConfig(
                status="online",
                event_types=[CaseEventType.CASE_CREATED],
                tag_filters=[],
            )
        }
    )
    changed = workflow_edit_document_changed_sections(
        original_document, updated_document
    )
    assert "case_trigger" in changed

    class RejectingCaseTriggersService:
        def __init__(self, session: Any, role: Role) -> None:
            self.session = session
            self.role = role

        async def validate_case_trigger_config(
            self, workflow_id: Any, config: Any
        ) -> None:
            raise TracecatValidationError(
                "Publish the workflow before enabling case triggers"
            )

    monkeypatch.setattr(
        draft_module, "CaseTriggersService", RejectingCaseTriggersService
    )

    with pytest.raises(WorkflowEditError) as exc:
        await validate_workflow_edit_document(
            updated_document,
            workflow_id=WorkflowUUID.new(source.id),
            changed_sections=changed,
            session=cast(Any, SimpleNamespace()),
            role=role,
        )
    assert "case trigger" in str(exc.value).lower()


@pytest.mark.anyio
async def test_validate_edit_document_rejects_taken_alias(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Changing alias to one another workflow owns is a recoverable conflict.

    Regression: the edit path assigned the alias and committed with no
    IntegrityError handling, so a normal alias conflict surfaced as a 500 and a
    dry run could report it valid. ``validate_workflow_edit_document`` now
    pre-checks uniqueness and raises WorkflowEditError (-> 400/409).
    """
    from tracecat.workflow.management.draft import (
        WorkflowEditError,
        build_workflow_edit_document,
        validate_workflow_edit_document,
        workflow_edit_document_changed_sections,
    )

    role = _role()
    source = _edit_document_workflow_source()
    original_document = build_workflow_edit_document(cast(Any, source))
    updated_document = original_document.model_copy(
        update={
            "metadata": original_document.metadata.model_copy(
                update={"alias": "taken-alias"}
            )
        }
    )
    changed = workflow_edit_document_changed_sections(
        original_document, updated_document
    )
    assert "metadata" in changed

    # Another workflow already owns the alias -> scalar() returns an id.
    other_id = uuid.uuid4()
    session = SimpleNamespace(scalar=AsyncMock(return_value=other_id))

    with pytest.raises(WorkflowEditError) as exc:
        await validate_workflow_edit_document(
            updated_document,
            workflow_id=WorkflowUUID.new(source.id),
            changed_sections=changed,
            session=cast(Any, session),
            role=role,
        )
    assert "alias" in str(exc.value).lower()
    session.scalar.assert_awaited_once()


@pytest.mark.anyio
async def test_validate_edit_document_allows_free_alias() -> None:
    """A free alias passes validation (no other workflow owns it)."""
    from tracecat.workflow.management.draft import (
        build_workflow_edit_document,
        validate_workflow_edit_document,
        workflow_edit_document_changed_sections,
    )

    role = _role()
    source = _edit_document_workflow_source()
    original_document = build_workflow_edit_document(cast(Any, source))
    updated_document = original_document.model_copy(
        update={
            "metadata": original_document.metadata.model_copy(
                update={"alias": "free-alias"}
            )
        }
    )
    changed = workflow_edit_document_changed_sections(
        original_document, updated_document
    )

    # No other workflow owns the alias -> scalar() returns None.
    session = SimpleNamespace(scalar=AsyncMock(return_value=None))

    # Should not raise.
    await validate_workflow_edit_document(
        updated_document,
        workflow_id=WorkflowUUID.new(source.id),
        changed_sections=changed,
        session=cast(Any, session),
        role=role,
    )
    session.scalar.assert_awaited_once()


@pytest.mark.anyio
async def test_persist_edit_document_maps_alias_integrity_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A concurrent alias claim at commit becomes a recoverable WorkflowEditError.

    Pre-validation catches conflicts under normal conditions, but a concurrent
    edit can claim the alias between the check and the commit. The commit's
    IntegrityError -> WorkflowEditError mapping keeps that race recoverable
    instead of letting the unique violation escape as a 500.
    """
    from asyncpg import UniqueViolationError
    from sqlalchemy.exc import IntegrityError

    from tracecat.db.common import DBConstraints
    from tracecat.workflow.management.draft import (
        WorkflowEditError,
        build_workflow_edit_document,
        persist_workflow_edit_document,
    )

    role = _role()
    source = _edit_document_workflow_source()
    original_document = build_workflow_edit_document(cast(Any, source))
    updated_document = original_document.model_copy(
        update={
            "metadata": original_document.metadata.model_copy(
                update={"alias": "raced-alias"}
            )
        }
    )

    # Build an IntegrityError whose root cause is the alias unique violation.
    unique_violation = UniqueViolationError(
        f"duplicate key value violates unique constraint "
        f'"{DBConstraints.WORKFLOW_ALIAS_UNIQUE_IN_WORKSPACE}"'
    )
    integrity_error = IntegrityError("stmt", {}, unique_violation)
    # SQLAlchemy raises the IntegrityError ``from`` the DBAPI error, so the
    # asyncpg cause is reachable via ``__cause__``; the constructor alone does
    # not set it, so mirror real behavior for the cause-chain traversal.
    integrity_error.__cause__ = unique_violation

    commit_mock = AsyncMock(side_effect=integrity_error)
    session = SimpleNamespace(
        add=MagicMock(),
        flush=AsyncMock(),
        commit=commit_mock,
        rollback=AsyncMock(),
        refresh=AsyncMock(),
    )
    service = WorkflowsManagementService(cast(Any, session), role=role)

    with pytest.raises(WorkflowEditError) as exc:
        await persist_workflow_edit_document(
            role=role,
            service=service,
            workflow=cast(Any, source),
            original_document=original_document,
            updated_document=updated_document,
        )
    assert "alias" in str(exc.value).lower()
    session.rollback.assert_awaited_once()


@pytest.mark.anyio
async def test_publish_workflow_missing_raises_not_found() -> None:
    """Publishing a non-existent workflow raises TracecatNotFoundError.

    Callers (MCP tool -> ToolError, commit route -> 404, internal route -> 404)
    rely on this distinct exception type rather than a structured-error result.
    """
    from tracecat.exceptions import TracecatNotFoundError
    from tracecat.identifiers.workflow import WorkflowUUID

    role = _role()
    session = SimpleNamespace(commit=AsyncMock(), refresh=AsyncMock())
    service = WorkflowsManagementService(cast(Any, session), role=role)
    service.get_workflow = AsyncMock(return_value=None)  # type: ignore[method-assign]

    with pytest.raises(TracecatNotFoundError):
        await service.publish_workflow(WorkflowUUID.new(uuid.uuid4()))


@pytest.mark.anyio
async def test_publish_workflow_invalid_draft_returns_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An invalid/empty draft yields a WorkflowPublishResult with errors, not a raise.

    publish_workflow returns structured errors so the MCP tool, commit route, and
    internal route can each render them; only the internal route turns them into a
    400. version is None and the result is not ``ok``.
    """
    from tracecat.exceptions import TracecatValidationError
    from tracecat.identifiers.workflow import WorkflowUUID
    from tracecat.workflow.management.management import WorkflowPublishResult

    role = _role()
    session = SimpleNamespace(commit=AsyncMock(), refresh=AsyncMock())
    service = WorkflowsManagementService(cast(Any, session), role=role)
    service.get_workflow = AsyncMock(return_value=SimpleNamespace(id=uuid.uuid4()))  # type: ignore[method-assign]

    async def fail_build(_workflow: Any) -> Any:
        raise TracecatValidationError("Workflow has no actions.")

    monkeypatch.setattr(service, "build_dsl_from_workflow", fail_build)

    result = await service.publish_workflow(WorkflowUUID.new(uuid.uuid4()))

    assert isinstance(result, WorkflowPublishResult)
    assert result.version is None
    assert not result.ok
    assert len(result.errors) == 1
    session.commit.assert_not_awaited()


def test_normalize_persisted_revision_adds_missing_layout_entries() -> None:
    """A definition action without a layout entry must not desync dry-run revision.

    Regression: ``normalize_workflow_edit_document_for_persisted_revision`` only
    stripped orphan layout entries; it did not add entries for definition actions
    missing from the layout. The real persist path recreates the action and
    ``build_workflow_edit_document`` emits a layout entry at (0.0, 0.0) for it, so
    a ``validate_only`` patch that adds an action without a ``/layout/actions``
    entry produced a draft_revision that could not match the post-apply revision,
    causing the next edit to 409. The normalizer now adds the default entry so the
    dry-run and post-persist revisions agree.
    """
    from tracecat.mcp.schemas import WorkflowEditDocument
    from tracecat.workflow.management.draft import (
        build_workflow_edit_document,
        compute_workflow_edit_revision,
        normalize_workflow_edit_document_for_persisted_revision,
    )

    skeleton = build_workflow_edit_document(
        cast(Any, _edit_document_workflow_source())
    ).model_dump(mode="json")

    # Post-persist shape: action present in the definition AND the layout (the
    # default (0.0, 0.0) entry build_workflow_edit_document emits for it).
    persisted = json.loads(json.dumps(skeleton))
    persisted["definition"]["actions"] = [
        {
            "ref": "notify",
            "action": "core.transform.reshape",
            "args": {},
            "depends_on": [],
        }
    ]
    persisted["definition"]["entrypoint"] = {"ref": "notify", "expects": {}}
    persisted["layout"]["actions"] = [{"ref": "notify", "x": 0.0, "y": 0.0}]
    persisted_revision = compute_workflow_edit_revision(
        WorkflowEditDocument.model_validate(persisted)
    )

    # Dry-run shape: same definition, but the patch omitted the layout entry.
    dry = json.loads(json.dumps(persisted))
    dry["layout"]["actions"] = []
    dry_doc = WorkflowEditDocument.model_validate(dry)

    # Without normalization the revisions diverge (the bug)...
    assert compute_workflow_edit_revision(dry_doc) != persisted_revision
    # ...with normalization they agree (the fix).
    normalized = normalize_workflow_edit_document_for_persisted_revision(dry_doc)
    assert compute_workflow_edit_revision(normalized) == persisted_revision


def test_normalize_persisted_revision_drops_orphan_layout_entries() -> None:
    """Layout entries for refs no longer in the definition are still dropped."""
    from tracecat.mcp.schemas import WorkflowEditDocument
    from tracecat.workflow.management.draft import (
        build_workflow_edit_document,
        normalize_workflow_edit_document_for_persisted_revision,
    )

    skeleton = build_workflow_edit_document(
        cast(Any, _edit_document_workflow_source())
    ).model_dump(mode="json")
    payload = json.loads(json.dumps(skeleton))
    # Layout references an action that does not exist in the definition.
    payload["layout"]["actions"] = [{"ref": "ghost", "x": 1.0, "y": 2.0}]
    document = WorkflowEditDocument.model_validate(payload)

    normalized = normalize_workflow_edit_document_for_persisted_revision(document)

    assert [a.ref for a in normalized.layout.actions] == []


# ---------------------------------------------------------------------------
# run_workflow
# ---------------------------------------------------------------------------


def _run_dsl(expects: dict[str, Any] | None = None) -> DSLInput:
    """Minimal valid DSL for run_workflow tests, with an optional expects schema."""
    return DSLInput(
        **{
            "title": "run workflow test",
            "description": "",
            "entrypoint": {"ref": "start", "expects": expects or {}},
            "actions": [
                {
                    "ref": "start",
                    "action": "core.transform.reshape",
                    "args": {"value": "ok"},
                }
            ],
        }
    )


class _FakeExecService:
    """Records which dispatch method was called and with what kwargs."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def create_draft_workflow_execution_wait_for_start(self, **kwargs: Any):
        self.calls.append(("draft", kwargs))
        return {
            "message": "Draft workflow execution started",
            "wf_id": kwargs["wf_id"],
            "wf_exec_id": "wf-exec-draft",
        }

    async def create_workflow_execution_wait_for_start(self, **kwargs: Any):
        self.calls.append(("published", kwargs))
        return {
            "message": "Workflow execution started",
            "wf_id": kwargs["wf_id"],
            "wf_exec_id": "wf-exec-published",
        }


def _patch_exec_service(monkeypatch: pytest.MonkeyPatch) -> _FakeExecService:
    """Patch the locally-imported WorkflowExecutionsService.connect."""
    from tracecat.workflow.executions import service as exec_service_module

    fake = _FakeExecService()

    async def _connect(role: Any = None) -> _FakeExecService:
        return fake

    monkeypatch.setattr(
        exec_service_module.WorkflowExecutionsService, "connect", _connect
    )
    return fake


@pytest.mark.anyio
async def test_run_workflow_draft_builds_and_dispatches_draft(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wf_id = WorkflowUUID.new_uuid4()
    dsl = _run_dsl()
    service = _service(_role())

    monkeypatch.setattr(service, "get_workflow", AsyncMock(return_value=object()))
    monkeypatch.setattr(service, "build_dsl_from_workflow", AsyncMock(return_value=dsl))
    # No validation errors from the tiered validator.
    monkeypatch.setattr(management, "validate_dsl", AsyncMock(return_value=[]))
    fake_exec = _patch_exec_service(monkeypatch)

    resp = await service.run_workflow(wf_id, inputs={"a": 1}, use_draft=True)

    assert resp["wf_exec_id"] == "wf-exec-draft"
    assert [name for name, _ in fake_exec.calls] == ["draft"]
    _, kwargs = fake_exec.calls[0]
    assert kwargs["wf_id"] == wf_id
    assert kwargs["payload"] == {"a": 1}
    # Draft runs resolve the registry lock dynamically (not passed here).
    assert "registry_lock" not in kwargs


@pytest.mark.anyio
async def test_run_workflow_draft_missing_workflow_raises_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service(_role())
    monkeypatch.setattr(service, "get_workflow", AsyncMock(return_value=None))
    fake_exec = _patch_exec_service(monkeypatch)

    with pytest.raises(TracecatNotFoundError):
        await service.run_workflow(WorkflowUUID.new_uuid4(), use_draft=True)
    assert fake_exec.calls == []


@pytest.mark.anyio
async def test_run_workflow_draft_validation_error_does_not_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service(_role())
    dsl = _run_dsl()
    monkeypatch.setattr(service, "get_workflow", AsyncMock(return_value=object()))
    monkeypatch.setattr(service, "build_dsl_from_workflow", AsyncMock(return_value=dsl))

    # One validation error from the tiered validator.
    fake_error = SimpleNamespace(
        root=SimpleNamespace(model_dump=lambda mode="json": {"msg": "boom"})
    )
    monkeypatch.setattr(
        management, "validate_dsl", AsyncMock(return_value=[fake_error])
    )
    fake_exec = _patch_exec_service(monkeypatch)

    with pytest.raises(TracecatValidationError):
        await service.run_workflow(WorkflowUUID.new_uuid4(), use_draft=True)
    assert fake_exec.calls == []


@pytest.mark.anyio
async def test_run_workflow_published_latest_uses_definition_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wf_id = WorkflowUUID.new_uuid4()
    dsl = _run_dsl()
    lock = {
        "origins": {"tracecat_registry": "test-version"},
        "actions": {"core.transform.reshape": "tracecat_registry"},
        "origin_fingerprints": {},
    }
    defn = SimpleNamespace(content=dsl.model_dump(), registry_lock=lock)

    captured: dict[str, Any] = {}

    class _FakeDefnService:
        def __init__(self, session: Any, role: Any) -> None:
            pass

        async def get_definition_by_workflow_id(
            self, workflow_id: Any, *, version: int | None = None
        ):
            captured["version"] = version
            return defn

    monkeypatch.setattr(management, "WorkflowDefinitionsService", _FakeDefnService)
    fake_exec = _patch_exec_service(monkeypatch)

    resp = await _service(_role()).run_workflow(wf_id, use_draft=False)

    assert resp["wf_exec_id"] == "wf-exec-published"
    assert captured["version"] is None
    assert [name for name, _ in fake_exec.calls] == ["published"]
    _, kwargs = fake_exec.calls[0]
    assert isinstance(kwargs["registry_lock"], RegistryLock)


@pytest.mark.anyio
async def test_run_workflow_published_version_is_forwarded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dsl = _run_dsl()
    defn = SimpleNamespace(content=dsl.model_dump(), registry_lock=None)
    captured: dict[str, Any] = {}

    class _FakeDefnService:
        def __init__(self, session: Any, role: Any) -> None:
            pass

        async def get_definition_by_workflow_id(
            self, workflow_id: Any, *, version: int | None = None
        ):
            captured["version"] = version
            return defn

    monkeypatch.setattr(management, "WorkflowDefinitionsService", _FakeDefnService)
    fake_exec = _patch_exec_service(monkeypatch)

    await _service(_role()).run_workflow(
        WorkflowUUID.new_uuid4(), use_draft=False, version=3
    )

    assert captured["version"] == 3
    _, kwargs = fake_exec.calls[0]
    # No registry lock on the definition -> None passed through.
    assert kwargs["registry_lock"] is None


@pytest.mark.anyio
async def test_run_workflow_missing_version_raises_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FakeDefnService:
        def __init__(self, session: Any, role: Any) -> None:
            pass

        async def get_definition_by_workflow_id(
            self, workflow_id: Any, *, version: int | None = None
        ):
            return None

    monkeypatch.setattr(management, "WorkflowDefinitionsService", _FakeDefnService)
    fake_exec = _patch_exec_service(monkeypatch)

    with pytest.raises(TracecatNotFoundError):
        await _service(_role()).run_workflow(
            WorkflowUUID.new_uuid4(), use_draft=False, version=99
        )
    assert fake_exec.calls == []


@pytest.mark.anyio
async def test_run_workflow_invalid_inputs_raise_before_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # DSL requires an integer `count`; a string should fail validation.
    dsl = _run_dsl(expects={"count": {"type": "int"}})
    service = _service(_role())
    monkeypatch.setattr(service, "get_workflow", AsyncMock(return_value=object()))
    monkeypatch.setattr(service, "build_dsl_from_workflow", AsyncMock(return_value=dsl))
    monkeypatch.setattr(management, "validate_dsl", AsyncMock(return_value=[]))
    fake_exec = _patch_exec_service(monkeypatch)

    with pytest.raises(TracecatValidationError):
        await service.run_workflow(
            WorkflowUUID.new_uuid4(), inputs={"count": "not-an-int"}, use_draft=True
        )
    assert fake_exec.calls == []
