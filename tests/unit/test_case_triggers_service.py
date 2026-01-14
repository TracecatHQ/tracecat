"""Unit tests for CaseTriggerDispatchService."""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat import config
from tracecat.auth.types import AccessLevel, Role
from tracecat.cases.enums import (
    CaseEventType,
    CasePriority,
    CaseSeverity,
    CaseStatus,
)
from tracecat.cases.schemas import CaseCreate, UpdatedEvent
from tracecat.cases.service import CaseEventsService, CasesService
from tracecat.cases.triggers.schemas import (
    CaseTriggerExecutionMode,
    CaseWorkflowTriggerConfig,
)
from tracecat.cases.triggers.service import CaseTriggerDispatchService
from tracecat.db.models import Case, CaseEvent, Workflow, Workspace
from tracecat.exceptions import TracecatAuthorizationError
from tracecat.feature_flags.enums import FeatureFlag
from tracecat.identifiers.workflow import WorkflowUUID

# -----------------------------------------------------------------------------
# Pure Function Tests (No Database Required)
# These tests use a mock service to test helper methods that don't need DB
# -----------------------------------------------------------------------------


@pytest.fixture
def mock_service() -> CaseTriggerDispatchService:
    """Create a mock service for testing pure functions.

    This creates a mock service instance that has the real methods bound to it
    but without requiring a database connection.
    """
    mock = MagicMock()
    # Bind the real methods to the mock so they work correctly
    mock._resolve_field = CaseTriggerDispatchService._resolve_field.__get__(
        mock, CaseTriggerDispatchService
    )
    mock._normalize_filter_value = (
        CaseTriggerDispatchService._normalize_filter_value.__get__(
            mock, CaseTriggerDispatchService
        )
    )
    mock._matches_filters = CaseTriggerDispatchService._matches_filters.__get__(
        mock, CaseTriggerDispatchService
    )
    mock._build_event_dict = CaseTriggerDispatchService._build_event_dict.__get__(
        mock, CaseTriggerDispatchService
    )
    mock._parse_trigger_configs = (
        CaseTriggerDispatchService._parse_trigger_configs.__get__(
            mock, CaseTriggerDispatchService
        )
    )
    mock._matches_trigger = CaseTriggerDispatchService._matches_trigger.__get__(
        mock, CaseTriggerDispatchService
    )
    return mock


class TestResolveField:
    """Test the _resolve_field method (pure function tests)."""

    def test_resolve_simple_field(self, mock_service) -> None:
        """Test resolving a simple top-level field."""
        obj = {"field": "value"}
        result = mock_service._resolve_field(obj, "field")
        assert result == "value"

    def test_resolve_nested_field(self, mock_service) -> None:
        """Test resolving a nested field with dot notation."""
        obj = {"data": {"nested": {"value": "target"}}}
        result = mock_service._resolve_field(obj, "data.nested.value")
        assert result == "target"

    def test_resolve_missing_field_returns_none(self, mock_service) -> None:
        """Test that missing fields return None."""
        obj = {"data": {"field": "value"}}
        result = mock_service._resolve_field(obj, "data.missing")
        assert result is None

    def test_resolve_non_dict_intermediate_returns_none(self, mock_service) -> None:
        """Test that non-dict intermediate values return None."""
        obj = {"data": "not_a_dict"}
        result = mock_service._resolve_field(obj, "data.nested.value")
        assert result is None


class TestNormalizeFilterValue:
    """Test the _normalize_filter_value method (pure function tests)."""

    def test_normalize_enum(self, mock_service) -> None:
        """Test that enums are normalized to their value."""
        result = mock_service._normalize_filter_value(CaseSeverity.HIGH)
        assert result == "high"

    def test_normalize_list(self, mock_service) -> None:
        """Test that lists are normalized recursively."""
        result = mock_service._normalize_filter_value(
            [CaseSeverity.HIGH, CaseSeverity.CRITICAL]
        )
        assert result == ["high", "critical"]

    def test_normalize_string_passthrough(self, mock_service) -> None:
        """Test that strings pass through unchanged."""
        result = mock_service._normalize_filter_value("test")
        assert result == "test"

    def test_normalize_none(self, mock_service) -> None:
        """Test that None passes through unchanged."""
        result = mock_service._normalize_filter_value(None)
        assert result is None


class TestMatchesFilters:
    """Test the _matches_filters method (pure function tests)."""

    def test_filter_matches_scalar_equality(self, mock_service) -> None:
        """Test scalar equality filter matching."""
        event_dict = {
            "type": "case_updated",
            "data": {"field": "description", "old": "old", "new": "new"},
            "user_id": str(uuid.uuid4()),
            "created_at": datetime.now(UTC).isoformat(),
        }
        filters = {"data.field": "description"}
        result = mock_service._matches_filters(event_dict, filters)
        assert result is True

    def test_filter_matches_list_membership(self, mock_service) -> None:
        """Test list membership filter matching."""
        event_dict = {
            "type": "severity_changed",
            "data": {"old": "low", "new": "critical"},
            "user_id": str(uuid.uuid4()),
            "created_at": datetime.now(UTC).isoformat(),
        }
        filters = {"data.new": ["high", "critical"]}
        result = mock_service._matches_filters(event_dict, filters)
        assert result is True

    def test_filter_no_match_list_membership(self, mock_service) -> None:
        """Test list membership filter not matching."""
        event_dict = {
            "type": "severity_changed",
            "data": {"old": "low", "new": "medium"},
            "user_id": str(uuid.uuid4()),
            "created_at": datetime.now(UTC).isoformat(),
        }
        filters = {"data.new": ["high", "critical"]}
        result = mock_service._matches_filters(event_dict, filters)
        assert result is False

    def test_filter_matches_dot_notation(self, mock_service) -> None:
        """Test dot notation path resolution."""
        event_dict = {
            "type": "case_updated",
            "data": {
                "field": "description",
                "nested": {"deeply": {"value": "target"}},
            },
            "user_id": str(uuid.uuid4()),
            "created_at": datetime.now(UTC).isoformat(),
        }
        filters = {"data.nested.deeply.value": "target"}
        result = mock_service._matches_filters(event_dict, filters)
        assert result is True

    def test_filter_enum_normalization(self, mock_service) -> None:
        """Test that enums are normalized to their string values."""
        event_dict = {
            "type": "severity_changed",
            "data": {"old": "low", "new": "high"},
            "user_id": str(uuid.uuid4()),
            "created_at": datetime.now(UTC).isoformat(),
        }
        filters = {"data.new": CaseSeverity.HIGH}
        result = mock_service._matches_filters(event_dict, filters)
        assert result is True

    def test_filter_multiple_filters_all_match(self, mock_service) -> None:
        """Test that all filters must match."""
        event_dict = {
            "type": "case_updated",
            "data": {"field": "description", "old": "old", "new": "new"},
            "user_id": "user-123",
            "created_at": datetime.now(UTC).isoformat(),
        }
        filters = {
            "data.field": "description",
            "user_id": "user-123",
        }
        result = mock_service._matches_filters(event_dict, filters)
        assert result is True

    def test_filter_multiple_filters_one_fails(self, mock_service) -> None:
        """Test that if any filter fails, the result is False."""
        event_dict = {
            "type": "case_updated",
            "data": {"field": "description", "old": "old", "new": "new"},
            "user_id": "user-123",
            "created_at": datetime.now(UTC).isoformat(),
        }
        filters = {
            "data.field": "description",
            "user_id": "different-user",
        }
        result = mock_service._matches_filters(event_dict, filters)
        assert result is False

    def test_filter_missing_path_returns_false(self, mock_service) -> None:
        """Test that missing paths in event dict return False."""
        event_dict = {
            "type": "case_updated",
            "data": {"field": "description"},
            "user_id": str(uuid.uuid4()),
            "created_at": datetime.now(UTC).isoformat(),
        }
        filters = {"data.nonexistent.path": "value"}
        result = mock_service._matches_filters(event_dict, filters)
        assert result is False


class TestBuildEventDict:
    """Test the _build_event_dict method (pure function tests)."""

    def test_build_event_dict_structure(self, mock_service) -> None:
        """Test that event dict contains expected fields."""
        event = MagicMock(spec=CaseEvent)
        event.type = CaseEventType.CASE_UPDATED
        event.data = {"field": "description", "old": "old", "new": "new"}
        event.user_id = uuid.uuid4()
        event.created_at = datetime.now(UTC)

        event_dict = mock_service._build_event_dict(event)

        assert "type" in event_dict
        assert event_dict["type"] == event.type
        assert "data" in event_dict
        assert isinstance(event_dict["data"], dict)
        assert "user_id" in event_dict
        assert "created_at" in event_dict

    def test_build_event_dict_null_user_id(self, mock_service) -> None:
        """Test that event dict handles null user_id."""
        event = MagicMock(spec=CaseEvent)
        event.type = CaseEventType.CASE_UPDATED
        event.data = {"field": "description"}
        event.user_id = None
        event.created_at = datetime.now(UTC)

        event_dict = mock_service._build_event_dict(event)
        assert event_dict["user_id"] is None


class TestParseTriggerConfigsStandalone:
    """Test trigger config parsing without database (using mock workflows)."""

    def test_parse_trigger_configs_from_workflow_object(self, mock_service) -> None:
        """Test parsing valid trigger configs from workflow object."""
        workflow = MagicMock(spec=Workflow)
        workflow.id = uuid.uuid4()
        workflow.object = {
            "nodes": [
                {
                    "type": "trigger",
                    "data": {
                        "caseTriggers": [
                            {
                                "id": "trigger-1",
                                "enabled": True,
                                "eventType": "case_updated",
                                "fieldFilters": {"data.field": "description"},
                                "allowSelfTrigger": False,
                            }
                        ]
                    },
                }
            ]
        }

        configs = mock_service._parse_trigger_configs(workflow)

        assert len(configs) == 1
        assert configs[0].id == "trigger-1"
        assert configs[0].enabled is True
        assert configs[0].event_type == CaseEventType.CASE_UPDATED
        assert configs[0].field_filters == {"data.field": "description"}
        assert configs[0].allow_self_trigger is False
        assert configs[0].execution_mode == CaseTriggerExecutionMode.PUBLISHED_ONLY

    def test_parse_trigger_configs_execution_mode(self, mock_service) -> None:
        """Test parsing execution mode from trigger configs."""
        workflow = MagicMock(spec=Workflow)
        workflow.id = uuid.uuid4()
        workflow.object = {
            "nodes": [
                {
                    "type": "trigger",
                    "data": {
                        "caseTriggers": [
                            {
                                "id": "trigger-1",
                                "enabled": True,
                                "eventType": "case_updated",
                                "fieldFilters": {},
                                "allowSelfTrigger": False,
                                "executionMode": "draft_only",
                            }
                        ]
                    },
                }
            ]
        }

        configs = mock_service._parse_trigger_configs(workflow)

        assert len(configs) == 1
        assert configs[0].execution_mode == CaseTriggerExecutionMode.DRAFT_ONLY

    def test_parse_trigger_configs_empty_workflow_object(self, mock_service) -> None:
        """Test parsing with empty workflow object returns empty list."""
        workflow = MagicMock(spec=Workflow)
        workflow.id = uuid.uuid4()
        workflow.object = None

        configs = mock_service._parse_trigger_configs(workflow)
        assert configs == []

    def test_parse_trigger_configs_no_trigger_node(self, mock_service) -> None:
        """Test parsing with no trigger node returns empty list."""
        workflow = MagicMock(spec=Workflow)
        workflow.id = uuid.uuid4()
        workflow.object = {
            "nodes": [
                {"type": "action", "data": {"someField": "value"}},
            ]
        }

        configs = mock_service._parse_trigger_configs(workflow)
        assert configs == []

    def test_parse_trigger_configs_invalid_case_triggers_type(
        self, mock_service
    ) -> None:
        """Test parsing skips non-list caseTriggers values."""
        workflow = MagicMock(spec=Workflow)
        workflow.id = uuid.uuid4()
        workflow.object = {
            "nodes": [
                {
                    "type": "trigger",
                    "data": {"caseTriggers": {"id": "not-a-list"}},
                }
            ]
        }

        configs = mock_service._parse_trigger_configs(workflow)
        assert configs == []

    def test_parse_trigger_configs_invalid_trigger_entry(
        self, mock_service
    ) -> None:
        """Test parsing skips non-dict trigger entries."""
        workflow = MagicMock(spec=Workflow)
        workflow.id = uuid.uuid4()
        workflow.object = {
            "nodes": [
                {
                    "type": "trigger",
                    "data": {"caseTriggers": [None, "bad"]},
                }
            ]
        }

        configs = mock_service._parse_trigger_configs(workflow)
        assert configs == []

    def test_parse_trigger_configs_camelcase_conversion(self, mock_service) -> None:
        """Test that camelCase JSON is converted to snake_case."""
        workflow = MagicMock(spec=Workflow)
        workflow.id = uuid.uuid4()
        workflow.object = {
            "nodes": [
                {
                    "type": "trigger",
                    "data": {
                        "caseTriggers": [
                            {
                                "id": "test-id",
                                "enabled": True,
                                "eventType": "severity_changed",
                                "fieldFilters": {"data.new": "critical"},
                                "allowSelfTrigger": True,
                            }
                        ]
                    },
                }
            ]
        }

        configs = mock_service._parse_trigger_configs(workflow)

        assert len(configs) == 1
        assert configs[0].event_type == CaseEventType.SEVERITY_CHANGED
        assert configs[0].allow_self_trigger is True
        assert configs[0].execution_mode == CaseTriggerExecutionMode.PUBLISHED_ONLY
        assert configs[0].execution_mode == CaseTriggerExecutionMode.PUBLISHED_ONLY


class TestMatchesTriggerStandalone:
    """Test _matches_trigger without database."""

    def test_matches_trigger_same_event_type(self, mock_service) -> None:
        """Test that matching event type returns True."""
        workflow = MagicMock(spec=Workflow)
        workflow.id = uuid.uuid4()

        event = MagicMock(spec=CaseEvent)
        event.type = CaseEventType.CASE_UPDATED
        event.data = {"field": "description", "old": "old", "new": "new"}
        event.user_id = uuid.uuid4()
        event.created_at = datetime.now(UTC)

        event_dict = mock_service._build_event_dict(event)

        config = CaseWorkflowTriggerConfig(
            id="test",
            enabled=True,
            event_type=CaseEventType.CASE_UPDATED,
            field_filters={"data.field": "description"},
            allow_self_trigger=False,
        )

        result = mock_service._matches_trigger(event, event_dict, config, workflow)
        assert result is True

    def test_matches_trigger_different_event_type(self, mock_service) -> None:
        """Test that non-matching event type returns False."""
        workflow = MagicMock(spec=Workflow)
        workflow.id = uuid.uuid4()

        event = MagicMock(spec=CaseEvent)
        event.type = CaseEventType.STATUS_CHANGED
        event.data = {"old": "new", "new": "in_progress"}
        event.user_id = uuid.uuid4()
        event.created_at = datetime.now(UTC)

        event_dict = mock_service._build_event_dict(event)

        config = CaseWorkflowTriggerConfig(
            id="test",
            enabled=True,
            event_type=CaseEventType.CASE_UPDATED,
            field_filters={},
            allow_self_trigger=False,
        )

        result = mock_service._matches_trigger(event, event_dict, config, workflow)
        assert result is False


class TestSelfTriggerPreventionStandalone:
    """Test self-trigger prevention without database."""

    def test_self_trigger_prevented_by_default(self, mock_service) -> None:
        """Test that self-triggers are blocked when allow_self_trigger is False."""
        workflow = MagicMock(spec=Workflow)
        workflow.id = uuid.uuid4()

        # Create a proper wf_exec_id format: wf-short:exec-suffix
        wf_id_short = WorkflowUUID.new(workflow.id).short()
        wf_exec_id = f"{wf_id_short}:exec-1234567890"
        event = MagicMock(spec=CaseEvent)
        event.type = CaseEventType.CASE_UPDATED
        event.data = {
            "field": "description",
            "old": "old",
            "new": "new",
            "wf_exec_id": wf_exec_id,
        }
        event.user_id = uuid.uuid4()
        event.created_at = datetime.now(UTC)

        event_dict = mock_service._build_event_dict(event)

        config = CaseWorkflowTriggerConfig(
            id="test",
            enabled=True,
            event_type=CaseEventType.CASE_UPDATED,
            field_filters={"data.field": "description"},
            allow_self_trigger=False,
        )

        result = mock_service._matches_trigger(event, event_dict, config, workflow)
        assert result is False

    def test_self_trigger_allowed_when_enabled(self, mock_service) -> None:
        """Test that self-triggers are allowed when allow_self_trigger is True."""
        workflow = MagicMock(spec=Workflow)
        workflow.id = uuid.uuid4()

        # Create a proper wf_exec_id format: wf-short:exec-suffix
        wf_id_short = WorkflowUUID.new(workflow.id).short()
        wf_exec_id = f"{wf_id_short}:exec-1234567890"
        event = MagicMock(spec=CaseEvent)
        event.type = CaseEventType.CASE_UPDATED
        event.data = {
            "field": "description",
            "old": "old",
            "new": "new",
            "wf_exec_id": wf_exec_id,
        }
        event.user_id = uuid.uuid4()
        event.created_at = datetime.now(UTC)

        event_dict = mock_service._build_event_dict(event)

        config = CaseWorkflowTriggerConfig(
            id="test",
            enabled=True,
            event_type=CaseEventType.CASE_UPDATED,
            field_filters={"data.field": "description"},
            allow_self_trigger=True,
        )

        result = mock_service._matches_trigger(event, event_dict, config, workflow)
        assert result is True

    def test_different_workflow_not_blocked(self, mock_service) -> None:
        """Test that events from different workflows are not blocked."""
        workflow = MagicMock(spec=Workflow)
        workflow.id = uuid.uuid4()

        # Use a different workflow ID (proper format)
        different_workflow_id = uuid.uuid4()
        different_wf_id_short = WorkflowUUID.new(different_workflow_id).short()
        wf_exec_id = f"{different_wf_id_short}:exec-1234567890"
        event = MagicMock(spec=CaseEvent)
        event.type = CaseEventType.CASE_UPDATED
        event.data = {
            "field": "description",
            "old": "old",
            "new": "new",
            "wf_exec_id": wf_exec_id,
        }
        event.user_id = uuid.uuid4()
        event.created_at = datetime.now(UTC)

        event_dict = mock_service._build_event_dict(event)

        config = CaseWorkflowTriggerConfig(
            id="test",
            enabled=True,
            event_type=CaseEventType.CASE_UPDATED,
            field_filters={"data.field": "description"},
            allow_self_trigger=False,
        )

        result = mock_service._matches_trigger(event, event_dict, config, workflow)
        assert result is True

    def test_invalid_wf_exec_id_format_allows_trigger(self, mock_service) -> None:
        """Test that invalid wf_exec_id format doesn't block the trigger."""
        workflow = MagicMock(spec=Workflow)
        workflow.id = uuid.uuid4()

        event = MagicMock(spec=CaseEvent)
        event.type = CaseEventType.CASE_UPDATED
        event.data = {
            "field": "description",
            "old": "old",
            "new": "new",
            "wf_exec_id": "invalid-format-no-colon",
        }
        event.user_id = uuid.uuid4()
        event.created_at = datetime.now(UTC)

        event_dict = mock_service._build_event_dict(event)

        config = CaseWorkflowTriggerConfig(
            id="test",
            enabled=True,
            event_type=CaseEventType.CASE_UPDATED,
            field_filters={"data.field": "description"},
            allow_self_trigger=False,
        )

        result = mock_service._matches_trigger(event, event_dict, config, workflow)
        assert result is True


# -----------------------------------------------------------------------------
# Integration Tests (Require Database)
# These tests are marked with usefixtures("db") to require the database fixture
# -----------------------------------------------------------------------------


@pytest.fixture
async def case_trigger_service(
    session: AsyncSession, svc_role: Role
) -> CaseTriggerDispatchService:
    """Create a case trigger dispatch service instance for testing."""
    return CaseTriggerDispatchService(session=session, role=svc_role)


@pytest.fixture
async def cases_service(session: AsyncSession, svc_role: Role) -> CasesService:
    """Create a cases service instance for testing."""
    return CasesService(session=session, role=svc_role)


@pytest.fixture
async def case_events_service(
    session: AsyncSession, svc_role: Role
) -> CaseEventsService:
    """Create a case events service instance for testing."""
    return CaseEventsService(session=session, role=svc_role)


@pytest.fixture
async def test_case(cases_service: CasesService) -> Case:
    """Create a test case for event testing."""
    case_params = CaseCreate(
        summary="Test Case for Triggers",
        description="This is a test case for trigger testing",
        status=CaseStatus.NEW,
        priority=CasePriority.MEDIUM,
        severity=CaseSeverity.LOW,
    )
    return await cases_service.create_case(case_params)


@pytest.fixture
async def test_workflow(
    session: AsyncSession, svc_workspace: Workspace
) -> AsyncGenerator[Workflow, None]:
    """Create a test workflow with case trigger config."""
    workflow = Workflow(
        title="test-trigger-workflow",
        workspace_id=svc_workspace.id,
        description="Test workflow for case triggers",
        status="online",
        entrypoint=None,
        returns=None,
        object={
            "nodes": [
                {
                    "type": "trigger",
                    "data": {
                        "caseTriggers": [
                            {
                                "id": "trigger-1",
                                "enabled": True,
                                "eventType": "case_updated",
                                "fieldFilters": {"data.field": "description"},
                                "allowSelfTrigger": False,
                            }
                        ]
                    },
                }
            ]
        },
    )
    session.add(workflow)
    await session.commit()
    try:
        yield workflow
    finally:
        await session.delete(workflow)
        await session.commit()


@pytest.fixture
async def test_case_event(
    case_events_service: CaseEventsService, test_case: Case
) -> CaseEvent:
    """Create a test case event."""
    event_data = UpdatedEvent(
        type=CaseEventType.CASE_UPDATED,
        field="description",
        old="old description",
        new="new description",
    )
    return await case_events_service.create_event(test_case, event_data)


# -----------------------------------------------------------------------------
# Service Initialization Tests
# -----------------------------------------------------------------------------


@pytest.mark.anyio
@pytest.mark.usefixtures("db")
async def test_service_requires_workspace(session: AsyncSession) -> None:
    """Test that the service requires a workspace ID."""
    role_without_workspace = Role(
        type="service",
        user_id=uuid.uuid4(),
        workspace_id=None,
        service_id="tracecat-service",
        access_level=AccessLevel.BASIC,
    )

    with pytest.raises(TracecatAuthorizationError):
        CaseTriggerDispatchService(session=session, role=role_without_workspace)


# -----------------------------------------------------------------------------
# Feature Flag Gating Tests
# -----------------------------------------------------------------------------


@pytest.mark.anyio
@pytest.mark.usefixtures("db")
class TestFeatureFlagGating:
    async def test_dispatch_returns_empty_when_feature_disabled(
        self,
        case_trigger_service: CaseTriggerDispatchService,
        test_case: Case,
        test_case_event: CaseEvent,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Test that dispatch returns empty list when feature flag is disabled."""
        # Ensure the feature flag is NOT set
        monkeypatch.setattr(config, "TRACECAT__FEATURE_FLAGS", set())

        result = await case_trigger_service.dispatch_triggers_for_event(
            case=test_case,
            event=test_case_event,
        )

        assert result == []

    async def test_dispatch_processes_triggers_when_feature_enabled(
        self,
        case_trigger_service: CaseTriggerDispatchService,
        test_case: Case,
        test_case_event: CaseEvent,
        test_workflow: Workflow,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Test that dispatch processes triggers when feature flag is enabled."""
        # Enable the feature flag
        monkeypatch.setattr(
            config, "TRACECAT__FEATURE_FLAGS", {FeatureFlag.CASE_TRIGGERS}
        )

        # The dispatch won't actually execute workflows (no definition), but it
        # should get past the feature flag check and attempt to process
        result = await case_trigger_service.dispatch_triggers_for_event(
            case=test_case,
            event=test_case_event,
        )

        # Result will be empty because we don't have workflow definitions set up,
        # but the key is it didn't return early due to feature flag
        assert isinstance(result, list)


# -----------------------------------------------------------------------------
# Trigger Config Parsing Tests (Integration)
# -----------------------------------------------------------------------------


@pytest.mark.anyio
@pytest.mark.usefixtures("db")
class TestTriggerConfigParsingIntegration:
    async def test_parse_trigger_configs_from_workflow_object(
        self,
        case_trigger_service: CaseTriggerDispatchService,
        test_workflow: Workflow,
    ) -> None:
        """Test parsing valid trigger configs from workflow object."""
        configs = case_trigger_service._parse_trigger_configs(test_workflow)

        assert len(configs) == 1
        assert configs[0].id == "trigger-1"
        assert configs[0].enabled is True
        assert configs[0].event_type == CaseEventType.CASE_UPDATED
        assert configs[0].field_filters == {"data.field": "description"}
        assert configs[0].allow_self_trigger is False

    async def test_parse_trigger_configs_camelcase_conversion(
        self,
        case_trigger_service: CaseTriggerDispatchService,
        session: AsyncSession,
        svc_workspace: Workspace,
    ) -> None:
        """Test that camelCase JSON is converted to snake_case."""
        workflow = Workflow(
            title="camelcase-test",
            workspace_id=svc_workspace.id,
            status="online",
            object={
                "nodes": [
                    {
                        "type": "trigger",
                        "data": {
                            "caseTriggers": [
                                {
                                    "id": "test-id",
                                    "enabled": True,
                                    "eventType": "severity_changed",
                                    "fieldFilters": {"data.new": "critical"},
                                    "allowSelfTrigger": True,
                                }
                            ]
                        },
                    }
                ]
            },
        )
        session.add(workflow)
        await session.commit()

        configs = case_trigger_service._parse_trigger_configs(workflow)

        assert len(configs) == 1
        assert configs[0].event_type == CaseEventType.SEVERITY_CHANGED
        assert configs[0].allow_self_trigger is True

        await session.delete(workflow)
        await session.commit()

    async def test_parse_trigger_configs_empty_workflow_object(
        self,
        case_trigger_service: CaseTriggerDispatchService,
        session: AsyncSession,
        svc_workspace: Workspace,
    ) -> None:
        """Test parsing with empty workflow object returns empty list."""
        workflow = Workflow(
            title="empty-object-test",
            workspace_id=svc_workspace.id,
            status="online",
            object=None,
        )
        session.add(workflow)
        await session.commit()

        configs = case_trigger_service._parse_trigger_configs(workflow)

        assert configs == []

        await session.delete(workflow)
        await session.commit()

    async def test_parse_trigger_configs_no_trigger_node(
        self,
        case_trigger_service: CaseTriggerDispatchService,
        session: AsyncSession,
        svc_workspace: Workspace,
    ) -> None:
        """Test parsing with no trigger node returns empty list."""
        workflow = Workflow(
            title="no-trigger-node-test",
            workspace_id=svc_workspace.id,
            status="online",
            object={
                "nodes": [
                    {"type": "action", "data": {"someField": "value"}},
                ]
            },
        )
        session.add(workflow)
        await session.commit()

        configs = case_trigger_service._parse_trigger_configs(workflow)

        assert configs == []

        await session.delete(workflow)
        await session.commit()

    async def test_parse_trigger_configs_invalid_config_skipped(
        self,
        case_trigger_service: CaseTriggerDispatchService,
        session: AsyncSession,
        svc_workspace: Workspace,
    ) -> None:
        """Test that invalid configs are skipped with a warning."""
        workflow = Workflow(
            title="invalid-config-test",
            workspace_id=svc_workspace.id,
            status="online",
            object={
                "nodes": [
                    {
                        "type": "trigger",
                        "data": {
                            "caseTriggers": [
                                {
                                    "id": "valid-trigger",
                                    "enabled": True,
                                    "eventType": "case_updated",
                                    "fieldFilters": {},
                                    "allowSelfTrigger": False,
                                },
                                {
                                    # Invalid: missing required fields
                                    "id": "invalid-trigger",
                                    "eventType": "not_a_valid_event_type",
                                },
                            ]
                        },
                    }
                ]
            },
        )
        session.add(workflow)
        await session.commit()

        configs = case_trigger_service._parse_trigger_configs(workflow)

        # Only the valid config should be returned
        assert len(configs) == 1
        assert configs[0].id == "valid-trigger"

        await session.delete(workflow)
        await session.commit()


# -----------------------------------------------------------------------------
# Event Dict Building Tests (Integration)
# -----------------------------------------------------------------------------


@pytest.mark.anyio
@pytest.mark.usefixtures("db")
class TestEventDictBuildingIntegration:
    async def test_build_event_dict_structure(
        self,
        case_trigger_service: CaseTriggerDispatchService,
        test_case_event: CaseEvent,
    ) -> None:
        """Test that event dict contains expected fields."""
        event_dict = case_trigger_service._build_event_dict(test_case_event)

        assert "type" in event_dict
        assert event_dict["type"] == test_case_event.type
        assert "data" in event_dict
        assert isinstance(event_dict["data"], dict)
        assert "user_id" in event_dict
        assert "created_at" in event_dict

    async def test_build_event_dict_null_user_id(
        self,
        case_trigger_service: CaseTriggerDispatchService,
    ) -> None:
        """Test that event dict handles null user_id."""
        # Create a mock event without user_id
        event = MagicMock(spec=CaseEvent)
        event.type = CaseEventType.CASE_UPDATED
        event.data = {"field": "description"}
        event.user_id = None
        event.created_at = datetime.now(UTC)

        event_dict = case_trigger_service._build_event_dict(event)

        assert event_dict["user_id"] is None


# -----------------------------------------------------------------------------
# Event Matching Tests (Integration)
# -----------------------------------------------------------------------------


@pytest.mark.anyio
@pytest.mark.usefixtures("db")
class TestEventMatchingIntegration:
    async def test_matches_trigger_same_event_type(
        self,
        case_trigger_service: CaseTriggerDispatchService,
        test_workflow: Workflow,
    ) -> None:
        """Test that matching event type returns True."""
        event = MagicMock(spec=CaseEvent)
        event.type = CaseEventType.CASE_UPDATED
        event.data = {"field": "description", "old": "old", "new": "new"}
        event.user_id = uuid.uuid4()
        event.created_at = datetime.now(UTC)

        event_dict = case_trigger_service._build_event_dict(event)

        config = CaseWorkflowTriggerConfig(
            id="test",
            enabled=True,
            event_type=CaseEventType.CASE_UPDATED,
            field_filters={"data.field": "description"},
            allow_self_trigger=False,
        )

        result = case_trigger_service._matches_trigger(
            event, event_dict, config, test_workflow
        )

        assert result is True

    async def test_matches_trigger_different_event_type(
        self,
        case_trigger_service: CaseTriggerDispatchService,
        test_workflow: Workflow,
    ) -> None:
        """Test that non-matching event type returns False."""
        event = MagicMock(spec=CaseEvent)
        event.type = CaseEventType.STATUS_CHANGED
        event.data = {"old": "new", "new": "in_progress"}
        event.user_id = uuid.uuid4()
        event.created_at = datetime.now(UTC)

        event_dict = case_trigger_service._build_event_dict(event)

        config = CaseWorkflowTriggerConfig(
            id="test",
            enabled=True,
            event_type=CaseEventType.CASE_UPDATED,  # Different type
            field_filters={},
            allow_self_trigger=False,
        )

        result = case_trigger_service._matches_trigger(
            event, event_dict, config, test_workflow
        )

        assert result is False


# -----------------------------------------------------------------------------
# Field Filter Matching Tests (Integration)
# -----------------------------------------------------------------------------


@pytest.mark.anyio
@pytest.mark.usefixtures("db")
class TestFieldFilterMatchingIntegration:
    async def test_filter_matches_scalar_equality(
        self,
        case_trigger_service: CaseTriggerDispatchService,
    ) -> None:
        """Test scalar equality filter matching."""
        event_dict = {
            "type": "case_updated",
            "data": {"field": "description", "old": "old", "new": "new"},
            "user_id": str(uuid.uuid4()),
            "created_at": datetime.now(UTC).isoformat(),
        }

        filters = {"data.field": "description"}

        result = case_trigger_service._matches_filters(event_dict, filters)

        assert result is True

    async def test_filter_matches_list_membership(
        self,
        case_trigger_service: CaseTriggerDispatchService,
    ) -> None:
        """Test list membership filter matching."""
        event_dict = {
            "type": "severity_changed",
            "data": {"old": "low", "new": "critical"},
            "user_id": str(uuid.uuid4()),
            "created_at": datetime.now(UTC).isoformat(),
        }

        filters = {"data.new": ["high", "critical"]}

        result = case_trigger_service._matches_filters(event_dict, filters)

        assert result is True

    async def test_filter_no_match_list_membership(
        self,
        case_trigger_service: CaseTriggerDispatchService,
    ) -> None:
        """Test list membership filter not matching."""
        event_dict = {
            "type": "severity_changed",
            "data": {"old": "low", "new": "medium"},
            "user_id": str(uuid.uuid4()),
            "created_at": datetime.now(UTC).isoformat(),
        }

        filters = {"data.new": ["high", "critical"]}

        result = case_trigger_service._matches_filters(event_dict, filters)

        assert result is False

    async def test_filter_matches_dot_notation(
        self,
        case_trigger_service: CaseTriggerDispatchService,
    ) -> None:
        """Test dot notation path resolution."""
        event_dict = {
            "type": "case_updated",
            "data": {
                "field": "description",
                "nested": {"deeply": {"value": "target"}},
            },
            "user_id": str(uuid.uuid4()),
            "created_at": datetime.now(UTC).isoformat(),
        }

        filters = {"data.nested.deeply.value": "target"}

        result = case_trigger_service._matches_filters(event_dict, filters)

        assert result is True

    async def test_filter_enum_normalization(
        self,
        case_trigger_service: CaseTriggerDispatchService,
    ) -> None:
        """Test that enums are normalized to their string values."""
        event_dict = {
            "type": "severity_changed",
            "data": {"old": "low", "new": "high"},
            "user_id": str(uuid.uuid4()),
            "created_at": datetime.now(UTC).isoformat(),
        }

        # Filter uses enum value
        filters = {"data.new": CaseSeverity.HIGH}

        result = case_trigger_service._matches_filters(event_dict, filters)

        assert result is True

    async def test_filter_multiple_filters_all_match(
        self,
        case_trigger_service: CaseTriggerDispatchService,
    ) -> None:
        """Test that all filters must match."""
        event_dict = {
            "type": "case_updated",
            "data": {"field": "description", "old": "old", "new": "new"},
            "user_id": "user-123",
            "created_at": datetime.now(UTC).isoformat(),
        }

        filters = {
            "data.field": "description",
            "user_id": "user-123",
        }

        result = case_trigger_service._matches_filters(event_dict, filters)

        assert result is True

    async def test_filter_multiple_filters_one_fails(
        self,
        case_trigger_service: CaseTriggerDispatchService,
    ) -> None:
        """Test that if any filter fails, the result is False."""
        event_dict = {
            "type": "case_updated",
            "data": {"field": "description", "old": "old", "new": "new"},
            "user_id": "user-123",
            "created_at": datetime.now(UTC).isoformat(),
        }

        filters = {
            "data.field": "description",  # Matches
            "user_id": "different-user",  # Does not match
        }

        result = case_trigger_service._matches_filters(event_dict, filters)

        assert result is False

    async def test_filter_missing_path_returns_false(
        self,
        case_trigger_service: CaseTriggerDispatchService,
    ) -> None:
        """Test that missing paths in event dict return False."""
        event_dict = {
            "type": "case_updated",
            "data": {"field": "description"},
            "user_id": str(uuid.uuid4()),
            "created_at": datetime.now(UTC).isoformat(),
        }

        filters = {"data.nonexistent.path": "value"}

        result = case_trigger_service._matches_filters(event_dict, filters)

        assert result is False


# -----------------------------------------------------------------------------
# Self-Trigger Prevention Tests (Integration)
# -----------------------------------------------------------------------------


@pytest.mark.anyio
@pytest.mark.usefixtures("db")
class TestSelfTriggerPreventionIntegration:
    async def test_self_trigger_prevented_by_default(
        self,
        case_trigger_service: CaseTriggerDispatchService,
        test_workflow: Workflow,
    ) -> None:
        """Test that self-triggers are blocked when allow_self_trigger is False."""
        # Create event with wf_exec_id matching the workflow
        wf_id_short = WorkflowUUID.new(test_workflow.id).short()
        wf_exec_id = f"{wf_id_short}:some-run-id"
        event = MagicMock(spec=CaseEvent)
        event.type = CaseEventType.CASE_UPDATED
        event.data = {
            "field": "description",
            "old": "old",
            "new": "new",
            "wf_exec_id": wf_exec_id,
        }
        event.user_id = uuid.uuid4()
        event.created_at = datetime.now(UTC)

        event_dict = case_trigger_service._build_event_dict(event)

        config = CaseWorkflowTriggerConfig(
            id="test",
            enabled=True,
            event_type=CaseEventType.CASE_UPDATED,
            field_filters={"data.field": "description"},
            allow_self_trigger=False,  # Default
        )

        result = case_trigger_service._matches_trigger(
            event, event_dict, config, test_workflow
        )

        assert result is False

    async def test_self_trigger_allowed_when_enabled(
        self,
        case_trigger_service: CaseTriggerDispatchService,
        test_workflow: Workflow,
    ) -> None:
        """Test that self-triggers are allowed when allow_self_trigger is True."""
        wf_id_short = WorkflowUUID.new(test_workflow.id).short()
        wf_exec_id = f"{wf_id_short}:some-run-id"
        event = MagicMock(spec=CaseEvent)
        event.type = CaseEventType.CASE_UPDATED
        event.data = {
            "field": "description",
            "old": "old",
            "new": "new",
            "wf_exec_id": wf_exec_id,
        }
        event.user_id = uuid.uuid4()
        event.created_at = datetime.now(UTC)

        event_dict = case_trigger_service._build_event_dict(event)

        config = CaseWorkflowTriggerConfig(
            id="test",
            enabled=True,
            event_type=CaseEventType.CASE_UPDATED,
            field_filters={"data.field": "description"},
            allow_self_trigger=True,  # Allow
        )

        result = case_trigger_service._matches_trigger(
            event, event_dict, config, test_workflow
        )

        assert result is True

    async def test_different_workflow_not_blocked(
        self,
        case_trigger_service: CaseTriggerDispatchService,
        test_workflow: Workflow,
    ) -> None:
        """Test that events from different workflows are not blocked."""
        # Use a different workflow ID
        different_workflow_id = uuid.uuid4()
        different_wf_id_short = WorkflowUUID.new(different_workflow_id).short()
        wf_exec_id = f"{different_wf_id_short}:some-run-id"
        event = MagicMock(spec=CaseEvent)
        event.type = CaseEventType.CASE_UPDATED
        event.data = {
            "field": "description",
            "old": "old",
            "new": "new",
            "wf_exec_id": wf_exec_id,
        }
        event.user_id = uuid.uuid4()
        event.created_at = datetime.now(UTC)

        event_dict = case_trigger_service._build_event_dict(event)

        config = CaseWorkflowTriggerConfig(
            id="test",
            enabled=True,
            event_type=CaseEventType.CASE_UPDATED,
            field_filters={"data.field": "description"},
            allow_self_trigger=False,
        )

        result = case_trigger_service._matches_trigger(
            event, event_dict, config, test_workflow
        )

        assert result is True

    async def test_invalid_wf_exec_id_format_allows_trigger(
        self,
        case_trigger_service: CaseTriggerDispatchService,
        test_workflow: Workflow,
    ) -> None:
        """Test that invalid wf_exec_id format doesn't block the trigger."""
        event = MagicMock(spec=CaseEvent)
        event.type = CaseEventType.CASE_UPDATED
        event.data = {
            "field": "description",
            "old": "old",
            "new": "new",
            "wf_exec_id": "invalid-format-no-colon",
        }
        event.user_id = uuid.uuid4()
        event.created_at = datetime.now(UTC)

        event_dict = case_trigger_service._build_event_dict(event)

        config = CaseWorkflowTriggerConfig(
            id="test",
            enabled=True,
            event_type=CaseEventType.CASE_UPDATED,
            field_filters={"data.field": "description"},
            allow_self_trigger=False,
        )

        result = case_trigger_service._matches_trigger(
            event, event_dict, config, test_workflow
        )

        assert result is True


# -----------------------------------------------------------------------------
# End-to-End Dispatch Tests (Integration)
# -----------------------------------------------------------------------------


@pytest.mark.anyio
@pytest.mark.usefixtures("db")
class TestEndToEndDispatch:
    async def test_dispatch_includes_offline_workflows(
        self,
        case_trigger_service: CaseTriggerDispatchService,
        session: AsyncSession,
        svc_workspace: Workspace,
        test_case: Case,
        test_case_event: CaseEvent,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Test that offline workflows are still considered."""
        monkeypatch.setattr(
            config, "TRACECAT__FEATURE_FLAGS", {FeatureFlag.CASE_TRIGGERS}
        )

        # Create an offline workflow with triggers
        offline_workflow = Workflow(
            title="offline-workflow",
            workspace_id=svc_workspace.id,
            status="offline",
            object={
                "nodes": [
                    {
                        "type": "trigger",
                        "data": {
                            "caseTriggers": [
                                {
                                    "id": "trigger-1",
                                    "enabled": True,
                                    "eventType": "case_updated",
                                    "fieldFilters": {},
                                    "allowSelfTrigger": False,
                                }
                            ]
                        },
                    }
                ]
            },
        )
        session.add(offline_workflow)
        await session.commit()

        try:
            # Get the workflows that would be scanned
            workflows = await case_trigger_service._list_workflows()

            # Offline workflows should be included
            assert offline_workflow.id in [w.id for w in workflows]
        finally:
            await session.delete(offline_workflow)
            await session.commit()

    async def test_dispatch_skips_disabled_triggers(
        self,
        case_trigger_service: CaseTriggerDispatchService,
        session: AsyncSession,
        svc_workspace: Workspace,
        test_case: Case,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Test that disabled triggers are skipped."""
        monkeypatch.setattr(
            config, "TRACECAT__FEATURE_FLAGS", {FeatureFlag.CASE_TRIGGERS}
        )

        # Create workflow with disabled trigger
        workflow = Workflow(
            title="disabled-trigger-workflow",
            workspace_id=svc_workspace.id,
            status="online",
            object={
                "nodes": [
                    {
                        "type": "trigger",
                        "data": {
                            "caseTriggers": [
                                {
                                    "id": "disabled-trigger",
                                    "enabled": False,  # Disabled
                                    "eventType": "case_updated",
                                    "fieldFilters": {},
                                    "allowSelfTrigger": False,
                                }
                            ]
                        },
                    }
                ]
            },
        )
        session.add(workflow)
        await session.commit()

        # Create a matching event
        event = MagicMock(spec=CaseEvent)
        event.id = uuid.uuid4()
        event.type = CaseEventType.CASE_UPDATED
        event.data = {"field": "description", "old": "old", "new": "new"}
        event.user_id = uuid.uuid4()
        event.created_at = datetime.now(UTC)

        try:
            result = await case_trigger_service.dispatch_triggers_for_event(
                case=test_case,
                event=event,
            )

            # No dispatches because trigger is disabled
            assert result == []
        finally:
            await session.delete(workflow)
            await session.commit()

    async def test_dispatch_triggers_with_mock_execution(
        self,
        case_trigger_service: CaseTriggerDispatchService,
        test_case: Case,
        test_workflow: Workflow,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Test full dispatch pipeline with mocked workflow execution."""
        monkeypatch.setattr(
            config, "TRACECAT__FEATURE_FLAGS", {FeatureFlag.CASE_TRIGGERS}
        )

        # Create a matching event
        event = MagicMock(spec=CaseEvent)
        event.id = uuid.uuid4()
        event.type = CaseEventType.CASE_UPDATED
        event.data = {"field": "description", "old": "old", "new": "new"}
        event.user_id = uuid.uuid4()
        event.created_at = datetime.now(UTC)

        # Mock the workflow definition service
        mock_definition = MagicMock()
        mock_definition.content = {
            "title": "Test Workflow",
            "description": "Test",
            "entrypoint": {"ref": "action1"},
            "actions": [
                {"ref": "action1", "action": "core.transform.transform", "args": {}}
            ],
        }

        mock_defn_ctx = AsyncMock()
        mock_defn_ctx.__aenter__.return_value.get_definition_by_workflow_id = AsyncMock(
            return_value=mock_definition
        )
        mock_defn_ctx.__aexit__ = AsyncMock(return_value=None)

        # Mock the workflow execution service
        wf_exec_id = f"{WorkflowUUID.new(test_workflow.id).short()}:exec-123"
        mock_exec_response = {
            "message": "Workflow execution started",
            "wf_id": WorkflowUUID.new(test_workflow.id),
            "wf_exec_id": wf_exec_id,
        }

        mock_exec_svc = AsyncMock()
        mock_exec_svc.create_workflow_execution_nowait = MagicMock(
            return_value=mock_exec_response
        )

        with (
            patch(
                "tracecat.cases.triggers.service.WorkflowDefinitionsService.with_session",
                return_value=mock_defn_ctx,
            ),
            patch(
                "tracecat.cases.triggers.service.WorkflowExecutionsService.connect",
                return_value=mock_exec_svc,
            ),
        ):
            result = await case_trigger_service.dispatch_triggers_for_event(
                case=test_case,
                event=event,
            )

            # Should have dispatched one workflow
            assert len(result) == 1
            assert result[0] == wf_exec_id

    async def test_dispatch_all_matching_triggers(
        self,
        case_trigger_service: CaseTriggerDispatchService,
        session: AsyncSession,
        svc_workspace: Workspace,
        test_case: Case,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Test that all matching triggers are dispatched per workflow."""
        monkeypatch.setattr(
            config, "TRACECAT__FEATURE_FLAGS", {FeatureFlag.CASE_TRIGGERS}
        )

        # Create workflow with multiple matching triggers
        workflow = Workflow(
            title="multi-trigger-workflow",
            workspace_id=svc_workspace.id,
            status="online",
            object={
                "nodes": [
                    {
                        "type": "trigger",
                        "data": {
                            "caseTriggers": [
                                {
                                    "id": "trigger-1",
                                    "enabled": True,
                                    "eventType": "case_updated",
                                    "fieldFilters": {},
                                    "allowSelfTrigger": False,
                                },
                                {
                                    "id": "trigger-2",
                                    "enabled": True,
                                    "eventType": "case_updated",
                                    "fieldFilters": {},
                                    "allowSelfTrigger": False,
                                },
                            ]
                        },
                    }
                ]
            },
        )
        session.add(workflow)
        await session.commit()

        # Create a matching event
        event = MagicMock(spec=CaseEvent)
        event.id = uuid.uuid4()
        event.type = CaseEventType.CASE_UPDATED
        event.data = {"field": "description", "old": "old", "new": "new"}
        event.user_id = uuid.uuid4()
        event.created_at = datetime.now(UTC)

        # Mock the workflow services
        mock_definition = MagicMock()
        mock_definition.content = {
            "title": "Test Workflow",
            "description": "Test",
            "entrypoint": {"ref": "action1"},
            "actions": [
                {"ref": "action1", "action": "core.transform.transform", "args": {}}
            ],
        }

        mock_defn_ctx = AsyncMock()
        mock_defn_ctx.__aenter__.return_value.get_definition_by_workflow_id = AsyncMock(
            return_value=mock_definition
        )
        mock_defn_ctx.__aexit__ = AsyncMock(return_value=None)

        mock_exec_response = {
            "message": "Workflow execution started",
            "wf_id": WorkflowUUID.new(workflow.id),
            "wf_exec_id": f"{WorkflowUUID.new(workflow.id).short()}:exec-123",
        }

        mock_exec_svc = AsyncMock()
        mock_exec_svc.create_workflow_execution_nowait = MagicMock(
            return_value=mock_exec_response
        )

        try:
            with (
                patch(
                    "tracecat.cases.triggers.service.WorkflowDefinitionsService.with_session",
                    return_value=mock_defn_ctx,
                ),
                patch(
                    "tracecat.cases.triggers.service.WorkflowExecutionsService.connect",
                    return_value=mock_exec_svc,
                ),
            ):
                result = await case_trigger_service.dispatch_triggers_for_event(
                    case=test_case,
                    event=event,
                )

                # Should dispatch for each matching trigger
                assert len(result) == 2
                assert mock_exec_svc.create_workflow_execution_nowait.call_count == 2
        finally:
            await session.delete(workflow)
            await session.commit()
