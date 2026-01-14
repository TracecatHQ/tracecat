"""Service for dispatching case event triggers to workflows."""

from __future__ import annotations

from collections.abc import Sequence
from enum import Enum
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.auth.types import AccessLevel, Role
from tracecat.cases.triggers.schemas import (
    CaseTriggerExecutionMode,
    CaseTriggerPayload,
    CaseWorkflowTriggerConfig,
)
from tracecat.db.models import Case, CaseEvent, Workflow
from tracecat.dsl.common import DSLInput
from tracecat.feature_flags import is_feature_enabled
from tracecat.feature_flags.enums import FeatureFlag
from tracecat.identifiers.workflow import WorkflowUUID, exec_id_to_parts
from tracecat.registry.lock.types import RegistryLock
from tracecat.service import BaseWorkspaceService
from tracecat.workflow.executions.enums import TriggerType
from tracecat.workflow.executions.service import WorkflowExecutionsService
from tracecat.workflow.management.definitions import WorkflowDefinitionsService
from tracecat.workflow.management.management import WorkflowsManagementService

if TYPE_CHECKING:
    pass


class CaseTriggerDispatchService(BaseWorkspaceService):
    """Service for dispatching workflows triggered by case events."""

    service_name = "case_trigger_dispatch"

    def __init__(self, session: AsyncSession, role: Role | None = None):
        super().__init__(session, role)

    async def dispatch_triggers_for_event(
        self,
        case: Case,
        event: CaseEvent,
        case_fields: dict[str, Any] | None = None,
    ) -> list[str]:
        """Dispatch workflows that match the given case event.

        This method:
        1. Checks if the case-triggers feature flag is enabled
        2. Loads all workflows in the workspace
        3. Parses trigger configs from workflow.object
        4. Matches triggers against the event
        5. Dispatches matching workflows with trigger_type=case_event

        Args:
            case: The case that the event belongs to
            event: The case event that was created
            case_fields: Optional custom field values for the case

        Returns:
            List of workflow execution IDs that were dispatched
        """
        # Check feature flag
        if not self._is_feature_enabled():
            self.logger.debug("Case triggers feature flag is disabled, skipping")
            return []

        self.logger.info(
            "Dispatching case event triggers",
            case_id=str(case.id),
            event_id=str(event.id),
            event_type=event.type,
        )

        # Load workflows
        workflows = await self._list_workflows()
        if not workflows:
            self.logger.debug("No workflows found in workspace")
            return []

        self.logger.debug(
            "Found workflows to scan",
            workflow_count=len(workflows),
        )

        # Build event dict for matching
        event_dict = self._build_event_dict(event)

        dispatched_exec_ids: list[str] = []
        for workflow in workflows:
            # Parse trigger configs from workflow.object
            trigger_configs = self._parse_trigger_configs(workflow)
            if not trigger_configs:
                continue

            # Check each trigger config
            for config in trigger_configs:
                if not config.enabled:
                    continue

                # Check if event matches this trigger
                if not self._matches_trigger(event, event_dict, config, workflow):
                    continue

                self.logger.info(
                    "Case event matches trigger, dispatching workflow",
                    workflow_id=str(workflow.id),
                    workflow_title=workflow.title,
                    trigger_id=config.id,
                    event_type=event.type,
                    execution_mode=config.execution_mode.value,
                )

                # Dispatch the workflow
                exec_ids = await self._dispatch_for_mode(
                    workflow=workflow,
                    case=case,
                    event=event,
                    case_fields=case_fields or {},
                    mode=config.execution_mode,
                )
                if exec_ids:
                    dispatched_exec_ids.extend(exec_ids)

        self.logger.info(
            "Case event trigger dispatch complete",
            case_id=str(case.id),
            event_id=str(event.id),
            dispatched_count=len(dispatched_exec_ids),
        )
        return dispatched_exec_ids

    def _is_feature_enabled(self) -> bool:
        """Check if the case-triggers feature flag is enabled."""
        return is_feature_enabled(FeatureFlag.CASE_TRIGGERS)

    async def _list_workflows(self) -> Sequence[Workflow]:
        """List all workflows in the workspace."""
        stmt = select(Workflow).where(Workflow.workspace_id == self.workspace_id)
        result = await self.session.execute(stmt)
        return result.scalars().all()

    def _normalize_execution_mode(self, raw_mode: Any) -> CaseTriggerExecutionMode:
        """Normalize execution mode values from stored trigger configs."""
        if isinstance(raw_mode, CaseTriggerExecutionMode):
            return raw_mode
        if raw_mode in (None, ""):
            return CaseTriggerExecutionMode.PUBLISHED_ONLY
        mode_str = str(raw_mode).lower()
        try:
            return CaseTriggerExecutionMode(mode_str)
        except ValueError:
            if mode_str == "draft":
                return CaseTriggerExecutionMode.DRAFT_ONLY
            if mode_str == "published":
                return CaseTriggerExecutionMode.PUBLISHED_ONLY
            self.logger.warning(
                "Unknown case trigger execution mode, defaulting to published-only",
                execution_mode=raw_mode,
            )
            return CaseTriggerExecutionMode.PUBLISHED_ONLY

    def _parse_trigger_configs(
        self, workflow: Workflow
    ) -> list[CaseWorkflowTriggerConfig]:
        """Parse case trigger configs from workflow.object.

        The trigger configs are stored in the trigger node's data.caseTriggers field.
        """
        if not workflow.object:
            return []

        nodes = workflow.object.get("nodes", [])
        configs: list[CaseWorkflowTriggerConfig] = []

        for node in nodes:
            if node.get("type") != "trigger":
                continue

            data = node.get("data", {})
            case_triggers = data.get("caseTriggers", [])
            if not isinstance(case_triggers, list):
                self.logger.warning(
                    "Invalid case trigger config list, skipping",
                    workflow_id=str(workflow.id),
                    case_triggers_type=type(case_triggers).__name__,
                )
                continue

            for trigger_dict in case_triggers:
                if not isinstance(trigger_dict, dict):
                    self.logger.warning(
                        "Invalid case trigger entry, skipping",
                        workflow_id=str(workflow.id),
                        trigger_entry_type=type(trigger_dict).__name__,
                    )
                    continue
                try:
                    # Convert camelCase to snake_case for Pydantic
                    config = CaseWorkflowTriggerConfig(
                        id=trigger_dict.get("id", ""),
                        enabled=trigger_dict.get("enabled", True),
                        event_type=trigger_dict.get("eventType", ""),
                        field_filters=trigger_dict.get("fieldFilters", {}),
                        allow_self_trigger=trigger_dict.get("allowSelfTrigger", False),
                        execution_mode=self._normalize_execution_mode(
                            trigger_dict.get("executionMode")
                        ),
                    )
                    configs.append(config)
                except (ValidationError, KeyError) as e:
                    self.logger.warning(
                        "Failed to parse case trigger config",
                        workflow_id=str(workflow.id),
                        trigger_id=trigger_dict.get("id"),
                        event_type=trigger_dict.get("eventType"),
                        error=str(e),
                    )
                    continue

        return configs

    def _build_event_dict(self, event: CaseEvent) -> dict[str, Any]:
        """Build a flat dict representation of the event for filtering."""
        return {
            "type": event.type,
            "data": event.data or {},
            "user_id": str(event.user_id) if event.user_id else None,
            "created_at": event.created_at.isoformat() if event.created_at else None,
        }

    def _matches_trigger(
        self,
        event: CaseEvent,
        event_dict: dict[str, Any],
        config: CaseWorkflowTriggerConfig,
        workflow: Workflow,
    ) -> bool:
        """Check if the event matches the trigger config.

        Uses the same filtering semantics as case durations:
        - Dot-delimited paths (e.g., data.field, data.new)
        - Scalar equality or list membership
        - Enum normalization to string values
        """
        # Check event type match
        if event.type != config.event_type:
            return False

        # Check field filters
        if not self._matches_filters(event_dict, config.field_filters):
            return False

        # Check self-trigger prevention
        if not config.allow_self_trigger:
            wf_exec_id = (event.data or {}).get("wf_exec_id")
            if wf_exec_id:
                try:
                    wf_id, _ = exec_id_to_parts(wf_exec_id)
                    if wf_id == WorkflowUUID.new(workflow.id):
                        self.logger.debug(
                            "Skipping self-trigger",
                            workflow_id=str(workflow.id),
                            wf_exec_id=wf_exec_id,
                        )
                        return False
                except ValueError:
                    # Invalid wf_exec_id format, allow trigger
                    pass

        return True

    def _matches_filters(
        self, event_dict: dict[str, Any], filters: dict[str, Any]
    ) -> bool:
        """Check if the event matches all field filters.

        Follows the same semantics as CaseDurationService._matches_filters.
        """
        for path, expected in filters.items():
            actual = self._resolve_field(event_dict, path)
            actual_normalized = self._normalize_filter_value(actual)
            expected_normalized = self._normalize_filter_value(expected)

            if isinstance(expected_normalized, list):
                if actual_normalized is None:
                    return False
                if isinstance(actual_normalized, list):
                    if not any(
                        item in expected_normalized for item in actual_normalized
                    ):
                        return False
                elif actual_normalized not in expected_normalized:
                    return False
            elif actual_normalized != expected_normalized:
                return False

        return True

    def _resolve_field(self, obj: dict[str, Any], path: str) -> Any:
        """Resolve a dot-delimited field path from a dict."""
        value: Any = obj
        for part in path.split("."):
            if isinstance(value, dict):
                value = value.get(part)
            else:
                return None
            if value is None:
                return None
        return value

    def _normalize_filter_value(self, value: Any) -> Any:
        """Normalize a filter value for comparison."""
        if isinstance(value, Enum):
            return value.value
        if isinstance(value, list | tuple | set):
            return [self._normalize_filter_value(item) for item in value]
        return value

    def _normalize_registry_lock(
        self,
        lock_data: RegistryLock | dict[str, Any] | None,
        *,
        workflow_id: str,
    ) -> RegistryLock | None:
        """Normalize registry lock data, falling back to None if invalid."""
        if lock_data is None:
            return None
        if isinstance(lock_data, RegistryLock):
            return lock_data
        try:
            return RegistryLock.model_validate(lock_data)
        except ValidationError as e:
            self.logger.warning(
                "Invalid registry lock data, falling back to auto-resolution",
                workflow_id=workflow_id,
                error=str(e),
            )
            return None

    def _build_trigger_payload(
        self, case: Case, event: CaseEvent, case_fields: dict[str, Any]
    ) -> CaseTriggerPayload:
        """Build payload for case-triggered workflow execution."""
        return CaseTriggerPayload(
            case_id=str(case.id),
            case_fields=case_fields,
            case_event={
                "id": str(event.id),
                "type": event.type,
                "created_at": event.created_at.isoformat()
                if event.created_at
                else None,
                "data": event.data or {},
                "user_id": str(event.user_id) if event.user_id else None,
            },
        )

    async def _dispatch_for_mode(
        self,
        *,
        workflow: Workflow,
        case: Case,
        event: CaseEvent,
        case_fields: dict[str, Any],
        mode: CaseTriggerExecutionMode,
    ) -> list[str]:
        """Dispatch workflows based on the configured execution mode."""
        if mode == CaseTriggerExecutionMode.DRAFT_ONLY:
            exec_id = await self._dispatch_draft_workflow(
                workflow=workflow,
                case=case,
                event=event,
                case_fields=case_fields,
            )
            return [exec_id] if exec_id else []
        if mode == CaseTriggerExecutionMode.ALWAYS:
            exec_ids: list[str] = []
            published_id = await self._dispatch_published_workflow(
                workflow=workflow,
                case=case,
                event=event,
                case_fields=case_fields,
            )
            if published_id:
                exec_ids.append(published_id)
            draft_id = await self._dispatch_draft_workflow(
                workflow=workflow,
                case=case,
                event=event,
                case_fields=case_fields,
            )
            if draft_id:
                exec_ids.append(draft_id)
            return exec_ids
        exec_id = await self._dispatch_published_workflow(
            workflow=workflow,
            case=case,
            event=event,
            case_fields=case_fields,
        )
        return [exec_id] if exec_id else []

    async def _dispatch_published_workflow(
        self,
        *,
        workflow: Workflow,
        case: Case,
        event: CaseEvent,
        case_fields: dict[str, Any],
    ) -> str | None:
        """Dispatch a published workflow execution with case event payload."""
        try:
            service_role = Role(
                type="service",
                service_id="tracecat-case-triggers",
                workspace_id=self.workspace_id,
                access_level=AccessLevel.ADMIN,
            )

            async with WorkflowDefinitionsService.with_session(
                role=service_role
            ) as defn_svc:
                definition = await defn_svc.get_definition_by_workflow_id(
                    WorkflowUUID.new(workflow.id)
                )
                if not definition:
                    self.logger.warning(
                        "No workflow definition found",
                        workflow_id=str(workflow.id),
                    )
                    return None

            dsl = DSLInput(**definition.content)
            payload = self._build_trigger_payload(case, event, case_fields)
            registry_lock = self._normalize_registry_lock(
                definition.registry_lock, workflow_id=str(workflow.id)
            )

            exec_svc = await WorkflowExecutionsService.connect(role=service_role)
            response = exec_svc.create_workflow_execution_nowait(
                dsl=dsl,
                wf_id=WorkflowUUID.new(workflow.id),
                payload=payload.model_dump(),
                trigger_type=TriggerType.CASE_EVENT,
                registry_lock=registry_lock,
            )

            self.logger.info(
                "Dispatched published workflow for case event",
                workflow_id=str(workflow.id),
                workflow_title=workflow.title,
                case_id=str(case.id),
                event_id=str(event.id),
            )

            return response["wf_exec_id"]
        except Exception:
            self.logger.exception(
                "Failed to dispatch published workflow for case event",
                workflow_id=str(workflow.id),
                case_id=str(case.id),
                event_id=str(event.id),
            )
            return None

    async def _dispatch_draft_workflow(
        self,
        *,
        workflow: Workflow,
        case: Case,
        event: CaseEvent,
        case_fields: dict[str, Any],
    ) -> str | None:
        """Dispatch a draft workflow execution with case event payload."""
        try:
            service_role = Role(
                type="service",
                service_id="tracecat-case-triggers",
                workspace_id=self.workspace_id,
                access_level=AccessLevel.ADMIN,
            )

            async with WorkflowsManagementService.with_session(
                role=service_role
            ) as mgmt_svc:
                draft_workflow = await mgmt_svc.get_workflow(
                    WorkflowUUID.new(workflow.id)
                )
                if not draft_workflow:
                    self.logger.warning(
                        "Workflow not found for draft execution",
                        workflow_id=str(workflow.id),
                    )
                    return None
                dsl = await mgmt_svc.build_dsl_from_workflow(draft_workflow)
                registry_lock = self._normalize_registry_lock(
                    draft_workflow.registry_lock, workflow_id=str(workflow.id)
                )

            payload = self._build_trigger_payload(case, event, case_fields)

            exec_svc = await WorkflowExecutionsService.connect(role=service_role)
            response = exec_svc.create_draft_workflow_execution_nowait(
                dsl=dsl,
                wf_id=WorkflowUUID.new(workflow.id),
                payload=payload.model_dump(),
                trigger_type=TriggerType.CASE_EVENT,
                registry_lock=registry_lock,
            )

            self.logger.info(
                "Dispatched draft workflow for case event",
                workflow_id=str(workflow.id),
                workflow_title=workflow.title,
                case_id=str(case.id),
                event_id=str(event.id),
            )

            return response["wf_exec_id"]
        except Exception:
            self.logger.exception(
                "Failed to dispatch draft workflow for case event",
                workflow_id=str(workflow.id),
                case_id=str(case.id),
                event_id=str(event.id),
            )
            return None
