from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Any, cast

import sqlalchemy as sa
import yaml
from pydantic import ValidationError
from sqlalchemy import and_, select
from sqlalchemy.orm import selectinload
from temporalio import activity
from temporalio.exceptions import ApplicationError

from tracecat.audit.logger import audit_log
from tracecat.authz.controls import require_scope
from tracecat.contexts import ctx_logical_time
from tracecat.db.models import (
    Action,
    CaseTrigger,
    Webhook,
    Workflow,
    WorkflowDefinition,
    WorkflowTag,
    WorkflowTagLink,
)
from tracecat.dsl.action import materialize_context
from tracecat.dsl.common import (
    DSLEntrypoint,
    DSLInput,
    build_action_statements_from_actions,
)
from tracecat.dsl.schemas import DSLConfig, ExecutionContext, RunContext
from tracecat.dsl.view import RFGraph
from tracecat.exceptions import TracecatValidationError
from tracecat.expressions.eval import eval_templated_object
from tracecat.identifiers import WorkflowID
from tracecat.identifiers.workflow import (
    LEGACY_WF_ID_PATTERN,
    WF_ID_SHORT_PATTERN,
    WorkflowUUID,
)
from tracecat.pagination import (
    BaseCursorPaginator,
    CursorPaginatedResponse,
    CursorPaginationParams,
)
from tracecat.registry.lock.service import RegistryLockService
from tracecat.service import BaseWorkspaceService
from tracecat.validation.schemas import (
    DSLValidationResult,
    ValidationDetail,
    ValidationResult,
)
from tracecat.validation.service import validate_dsl
from tracecat.workflow.actions.schemas import ActionControlFlow, ActionEdge
from tracecat.workflow.executions.enums import ExecutionType
from tracecat.workflow.management.definitions import WorkflowDefinitionsService
from tracecat.workflow.management.schemas import (
    ExternalWorkflowDefinition,
    GetErrorHandlerWorkflowIDActivityInputs,
    ResolveWorkflowAliasActivityInputs,
    WorkflowCreate,
    WorkflowDSLCreateResponse,
    WorkflowUpdate,
)
from tracecat.workflow.management.types import WorkflowDefinitionMinimal
from tracecat.workflow.schedules import bridge
from tracecat.workflow.schedules.service import WorkflowSchedulesService


class WorkflowsManagementService(BaseWorkspaceService):
    """Manages CRUD operations for Workflows."""

    service_name = "workflows"

    async def list_all_workflows(
        self, *, tags: list[str] | None = None, reverse: bool = False
    ) -> list[tuple[Workflow, WorkflowDefinitionMinimal | None]]:
        """List workflows with their latest definitions.

        Args:
            tags: Optional list of tag names to filter workflows by

        Returns:
            list[tuple[Workflow, WorkflowDefinition | None]]: List of tuples containing workflow
                and its latest definition (or None if no definition exists)
        """
        # Subquery to get the latest definition for each workflow
        latest_defn_subq = (
            select(
                WorkflowDefinition.workflow_id,
                sa.func.max(WorkflowDefinition.version).label("latest_version"),
            )
            .group_by(sa.cast(WorkflowDefinition.workflow_id, sa.UUID))
            .subquery()
        )

        # Main query selecting workflow with left outer join to definitions
        stmt = (
            select(
                Workflow,
                WorkflowDefinition.id,
                WorkflowDefinition.version,
                WorkflowDefinition.created_at,
            )
            .where(Workflow.workspace_id == self.workspace_id)
            .outerjoin(
                latest_defn_subq,
                sa.cast(Workflow.id, sa.UUID) == latest_defn_subq.c.workflow_id,
            )
            .outerjoin(
                WorkflowDefinition,
                and_(
                    WorkflowDefinition.workflow_id == Workflow.id,
                    WorkflowDefinition.version == latest_defn_subq.c.latest_version,
                ),
            )
        )

        if reverse:
            stmt = stmt.order_by(
                Workflow.created_at.asc(),
                Workflow.id.asc(),
            )
        else:
            stmt = stmt.order_by(
                Workflow.created_at.desc(),
                Workflow.id.desc(),
            )

        if tags:
            tag_set = set(tags)
            # Join through the WorkflowTagLink table to WorkflowTag table
            stmt = (
                stmt.join(
                    WorkflowTagLink,
                    sa.cast(Workflow.id, sa.UUID) == WorkflowTagLink.workflow_id,
                )
                .join(
                    WorkflowTag,
                    and_(
                        WorkflowTag.id == WorkflowTagLink.tag_id,
                        WorkflowTag.name.in_(tag_set),
                    ),
                )
                # Ensure we get distinct workflows when multiple tags match
                .distinct()
            )

        # Add eager loading for tags since they're accessed in the router
        stmt = stmt.options(selectinload(Workflow.tags))

        results = await self.session.execute(stmt)
        res = []
        for workflow, defn_id, defn_version, defn_created in results.all():
            if all((defn_id, defn_version, defn_created)):
                latest_defn = WorkflowDefinitionMinimal(
                    id=defn_id,
                    version=defn_version,
                    created_at=defn_created,
                )
            else:
                latest_defn = None
            res.append((workflow, latest_defn))
        return res

    async def list_workflows(
        self, params: CursorPaginationParams, *, tags: list[str] | None = None
    ) -> CursorPaginatedResponse[tuple[Workflow, WorkflowDefinitionMinimal | None]]:
        """List workflows with cursor-based pagination.

        Args:
            params: Cursor pagination parameters
            tags: Optional list of tag names to filter workflows by

        Returns:
            CursorPaginatedResponse containing workflows and their latest definitions
        """

        # Subquery to get the latest definition for each workflow
        latest_defn_subq = (
            select(
                WorkflowDefinition.workflow_id,
                sa.func.max(WorkflowDefinition.version).label("latest_version"),
            )
            .group_by(sa.cast(WorkflowDefinition.workflow_id, sa.UUID))
            .subquery()
        )

        # Main query selecting workflow with left outer join to definitions
        stmt = (
            select(
                Workflow,
                WorkflowDefinition.id,
                WorkflowDefinition.version,
                WorkflowDefinition.created_at.label("defn_created_at"),
            )
            .where(Workflow.workspace_id == self.workspace_id)
            .outerjoin(
                latest_defn_subq,
                sa.cast(Workflow.id, sa.UUID) == latest_defn_subq.c.workflow_id,
            )
            .outerjoin(
                WorkflowDefinition,
                and_(
                    WorkflowDefinition.workflow_id == Workflow.id,
                    WorkflowDefinition.version == latest_defn_subq.c.latest_version,
                ),
            )
        )

        # Apply tag filtering if specified
        if tags:
            tag_set = set(tags)
            stmt = (
                stmt.join(
                    WorkflowTagLink,
                    sa.cast(Workflow.id, sa.UUID) == WorkflowTagLink.workflow_id,
                )
                .join(
                    WorkflowTag,
                    and_(
                        WorkflowTag.id == WorkflowTagLink.tag_id,
                        WorkflowTag.name.in_(tag_set),
                    ),
                )
                .distinct()
            )

        # Use cursor paginator for workflows
        paginator = BaseCursorPaginator(self.session)

        # Since we're selecting multiple columns, we need to handle pagination differently
        # Apply cursor filter manually for complex queries
        if params.cursor:
            cursor_data = paginator.decode_cursor(params.cursor)
            cursor_id = uuid.UUID(cursor_data.id)

            # Extract the sort value (created_at timestamp) from cursor
            cursor_sort_value = cursor_data.sort_value
            cursor_has_sort_value = (
                cursor_data.sort_column == "created_at"
                and cursor_sort_value is not None
            )

            if cursor_has_sort_value:
                # Workflows are sorted by created_at DESC (see line 260)
                # Descending order logic:
                if params.reverse:
                    # Going backward: get records after cursor in sort order
                    stmt = stmt.where(
                        sa.or_(
                            Workflow.created_at > cursor_sort_value,
                            sa.and_(
                                Workflow.created_at == cursor_sort_value,
                                Workflow.id > cursor_id,
                            ),
                        )
                    )
                else:
                    # Going forward: get records before cursor in sort order
                    stmt = stmt.where(
                        sa.or_(
                            Workflow.created_at < cursor_sort_value,
                            sa.and_(
                                Workflow.created_at == cursor_sort_value,
                                Workflow.id < cursor_id,
                            ),
                        )
                    )
            else:
                # Fallback for old-format cursors or cursors without sort value
                # Use ID-only filtering to maintain backward compatibility
                if params.reverse:
                    stmt = stmt.where(Workflow.id > cursor_id)
                else:
                    stmt = stmt.where(Workflow.id < cursor_id)

        # Apply ordering
        if params.reverse:
            stmt = stmt.order_by(Workflow.created_at.asc(), Workflow.id.asc())
        else:
            stmt = stmt.order_by(Workflow.created_at.desc(), Workflow.id.desc())

        # Fetch limit + 1 to determine if there are more items
        stmt = stmt.limit(params.limit + 1)

        # Add eager loading for tags since they're accessed in the router
        stmt = stmt.options(selectinload(Workflow.tags))

        results = await self.session.execute(stmt)
        raw_items = list(results.all())

        # Check if there are more items
        has_more = len(raw_items) > params.limit
        if has_more:
            raw_items = raw_items[: params.limit]

        # Process results into the expected format
        items = []
        for workflow, defn_id, defn_version, defn_created in raw_items:
            if all((defn_id, defn_version, defn_created)):
                latest_defn = WorkflowDefinitionMinimal(
                    id=defn_id,
                    version=defn_version,
                    created_at=defn_created,
                )
            else:
                latest_defn = None
            items.append((workflow, latest_defn))

        # Generate cursors
        next_cursor = None
        prev_cursor = None

        if items:
            if has_more:
                last_workflow = items[-1][0]  # Get the workflow from the tuple
                next_cursor = paginator.encode_cursor(
                    last_workflow.id,
                    sort_column="created_at",
                    sort_value=last_workflow.created_at,
                )

            if params.cursor:
                first_workflow = items[0][0]  # Get the workflow from the tuple
                prev_cursor = paginator.encode_cursor(
                    first_workflow.id,
                    sort_column="created_at",
                    sort_value=first_workflow.created_at,
                )

        # If we were doing reverse pagination, swap the cursors and reverse items
        if params.reverse:
            items = list(reversed(items))
            next_cursor, prev_cursor = prev_cursor, next_cursor

        return CursorPaginatedResponse(
            items=items,
            next_cursor=next_cursor,
            prev_cursor=prev_cursor,
            has_more=has_more,
            has_previous=params.cursor is not None,
        )

    async def get_workflow(self, workflow_id: WorkflowID) -> Workflow | None:
        statement = (
            select(Workflow)
            .where(
                Workflow.workspace_id == self.workspace_id,
                Workflow.id == workflow_id,
            )
            .options(
                selectinload(Workflow.actions),
                selectinload(Workflow.webhook).options(selectinload(Webhook.api_key)),
                selectinload(Workflow.schedules),
            )
        )
        result = await self.session.execute(statement)
        workflow = result.scalar_one_or_none()
        if workflow:
            await self._reconcile_graph_object_with_actions(workflow)
        return workflow

    async def _reconcile_graph_object_with_actions(self, workflow: Workflow) -> bool:
        """Remove stale upstream edge references from actions.

        The graph layout is stored in action.upstream_edges and workflow trigger
        coordinates. When actions are deleted out-of-band (e.g. manual DB edits),
        their IDs may remain in upstream_edges of other actions. This method
        normalizes the edge lists by removing references to non-existent actions
        and invalid trigger IDs.

        Returns:
            bool: True if any changes were persisted, False otherwise.
        """

        await self.session.refresh(workflow, ["actions"])

        if not workflow.actions:
            return False

        valid_action_ids = {str(action.id) for action in workflow.actions}
        trigger_id = f"trigger-{workflow.id}"
        changed = False

        for action in workflow.actions:
            edges = action.upstream_edges or []
            filtered_edges: list[dict[str, Any]] = []

            for edge in edges:
                source_id = str(edge.get("source_id", ""))
                source_type = edge.get("source_type", "udf")

                if source_type == "trigger":
                    if source_id != trigger_id:
                        changed = True
                        continue
                elif source_type == "udf":
                    if source_id not in valid_action_ids:
                        changed = True
                        continue
                else:
                    changed = True
                    continue

                filtered_edges.append(edge)

            if filtered_edges != edges:
                action.upstream_edges = filtered_edges
                self.session.add(action)

        if changed:
            await self.session.commit()
            await self.session.refresh(workflow, ["actions"])

        return changed

    async def resolve_workflow_alias(
        self, alias: str, *, use_committed: bool = True
    ) -> WorkflowID | None:
        """Resolve a workflow alias to a workflow ID.

        Args:
            alias: The workflow alias to resolve.
            use_committed: If True, resolve from committed WorkflowDefinition aliases.
                           If False, resolve from draft Workflow aliases (for draft executions).
        """
        if use_committed:
            # For published executions: resolve from the latest committed definition with this alias
            statement = (
                select(WorkflowDefinition.workflow_id)
                .where(
                    WorkflowDefinition.workspace_id == self.workspace_id,
                    WorkflowDefinition.alias == alias,
                )
                .order_by(WorkflowDefinition.version.desc())
                .limit(1)
            )
        else:
            # For draft executions: resolve from draft Workflow table
            statement = select(Workflow.id).where(
                Workflow.workspace_id == self.workspace_id,
                Workflow.alias == alias,
            )
        result = await self.session.execute(statement)
        res = result.scalar_one_or_none()
        return WorkflowUUID.new(res) if res else None

    @require_scope("workflow:update")
    async def update_workflow(
        self, workflow_id: WorkflowID, params: WorkflowUpdate
    ) -> Workflow:
        statement = select(Workflow).where(
            Workflow.workspace_id == self.workspace_id,
            Workflow.id == workflow_id,
        )
        result = await self.session.execute(statement)
        workflow = result.scalar_one()
        set_fields = params.model_dump(exclude_unset=True)
        if "object" in set_fields:
            graph = RFGraph.model_validate(set_fields["object"])
            normalized_graph = graph.normalize_action_ids()
            set_fields["object"] = normalized_graph.model_dump(
                by_alias=True, mode="json"
            )

        for key, value in set_fields.items():
            # Safe because params has been validated
            setattr(workflow, key, value)
        self.session.add(workflow)
        await self.session.commit()
        await self.session.refresh(workflow)
        return workflow

    @require_scope("workflow:delete")
    @audit_log(resource_type="workflow", action="delete")
    async def delete_workflow(self, workflow_id: WorkflowID) -> None:
        """Delete a workflow and clean up associated resources.

        This method ensures that Temporal schedules are properly deleted
        before the database cascade deletion occurs.
        """
        statement = select(Workflow).where(
            Workflow.workspace_id == self.workspace_id,
            Workflow.id == workflow_id,
        )
        result = await self.session.execute(statement)
        workflow = result.scalar_one()

        # Clean up Temporal schedules before cascade deletion
        # This prevents orphaned schedules in Temporal
        schedule_service = WorkflowSchedulesService(self.session, role=self.role)
        schedules = await schedule_service.list_schedules(workflow_id)

        for schedule in schedules:
            try:
                await bridge.delete_schedule(schedule.id)
                self.logger.info(
                    "Deleted Temporal schedule during workflow cleanup",
                    schedule_id=schedule.id,
                    workflow_id=workflow_id,
                )
            except Exception as e:
                # Log but don't fail the entire workflow deletion
                self.logger.warning(
                    "Failed to delete Temporal schedule during workflow cleanup",
                    schedule_id=schedule.id,
                    workflow_id=workflow_id,
                    error=str(e),
                )

        # Now delete the workflow (cascade will handle database schedule cleanup)
        await self.session.delete(workflow)
        await self.session.commit()

    @require_scope("workflow:create")
    @audit_log(resource_type="workflow", action="create")
    async def create_workflow(self, params: WorkflowCreate) -> Workflow:
        """Create a new workflow."""
        now = datetime.now().strftime("%b %d, %Y, %H:%M:%S")
        title = params.title or now
        description = params.description or f"New workflow created {now}"

        # registry_lock will be resolved when workflow is committed with actions
        workflow = Workflow(
            title=title,
            description=description,
            workspace_id=self.workspace_id,
        )
        # When we create a workflow, we automatically create a webhook
        # Add the Workflow to the session first to generate an ID
        self.session.add(workflow)
        await self.session.flush()  # Flush to generate workflow.id
        await self.session.refresh(workflow)

        # Create and associate Webhook with the Workflow
        webhook = Webhook(
            workspace_id=self.workspace_id,
            # workflow_id=workflow.id,
        )
        webhook.workflow = workflow
        self.session.add(webhook)
        workflow.webhook = webhook

        case_trigger = CaseTrigger(
            workspace_id=self.workspace_id,
            workflow_id=workflow.id,
            status="offline",
            event_types=[],
            tag_filters=[],
        )
        case_trigger.workflow = workflow
        self.session.add(case_trigger)
        workflow.case_trigger = case_trigger

        await self.session.commit()
        await self.session.refresh(workflow)
        return workflow

    @require_scope("workflow:create")
    async def create_workflow_from_dsl(
        self, dsl_data: dict[str, Any], *, skip_secret_validation: bool = False
    ) -> WorkflowDSLCreateResponse:
        """Create a new workflow from a Tracecat DSL data object."""

        construction_errors: list[DSLValidationResult] = []
        dsl: DSLInput | None = None
        try:
            # Convert the workflow into a WorkflowDefinition
            # XXX: When we commit from the workflow, we have action IDs
            dsl = DSLInput.model_validate(dsl_data)
            self.logger.info("Creating workflow from database")
        except TracecatValidationError as e:
            self.logger.info("Custom validation error", error=e)
            construction_errors.append(
                DSLValidationResult(status="error", msg=str(e), detail=e.detail)
            )
        except ValidationError as e:
            self.logger.info("Pydantic validation error", error=e)
            construction_errors.append(
                DSLValidationResult(
                    status="error",
                    msg=str(e),
                    detail=ValidationDetail.list_from_pydantic(e),
                )
            )
        if construction_errors:
            return WorkflowDSLCreateResponse(
                errors=[ValidationResult.new(e) for e in construction_errors]
            )

        if dsl is None:
            raise ValueError("dsl should be defined if no construction errors")
        if not skip_secret_validation:
            if val_errors := await validate_dsl(
                session=self.session, dsl=dsl, role=self.role
            ):
                self.logger.info("Validation errors", errors=val_errors)
                return WorkflowDSLCreateResponse(errors=list(val_errors))

        self.logger.debug("Creating workflow from DSL", dsl=dsl)
        try:
            workflow = await self.create_db_workflow_from_dsl(dsl)
            return WorkflowDSLCreateResponse(workflow=workflow)
        except Exception as e:
            # Rollback the transaction on error
            self.logger.error(f"Error creating workflow: {e}")
            await self.session.rollback()
            raise e

    async def build_dsl_from_workflow(self, workflow: Workflow) -> DSLInput:
        """Build a DSLInput from a Workflow."""

        # XXX: Invoking workflow.actions instantiates the actions relationship
        actions = workflow.actions
        # If it still falsy, raise a user facing error
        if not actions:
            raise TracecatValidationError(
                "Workflow has no actions. Please add an action to the workflow before saving."
            )
        action_statements = build_action_statements_from_actions(actions)
        return DSLInput(
            title=workflow.title,
            description=workflow.description,
            entrypoint=DSLEntrypoint(expects=workflow.expects),
            actions=action_statements,
            config=DSLConfig(**workflow.config),
            returns=workflow.returns,
            error_handler=workflow.error_handler,
        )

    @require_scope("workflow:create")
    async def create_workflow_from_external_definition(
        self,
        import_data: dict[str, Any],
        *,
        use_workflow_id: bool = False,
        trigger_position: tuple[float, float] | None = None,
        viewport: tuple[float, float, float] | None = None,
        action_positions: dict[str, tuple[float, float]] | None = None,
    ) -> Workflow:
        """Import an external workflow definition into the current workspace.

        Optionally validate the workflow definition before importing. (Default: False)
        """

        external_defn = ExternalWorkflowDefinition.model_validate(import_data)
        # NOTE: We do not support adding invalid workflows

        dsl = external_defn.definition
        self.logger.trace("Constructed DSL from external definition", dsl=dsl)
        # We need to be able to control:
        # 1. The workspace the workflow is imported into
        # 2. The owner of the workflow
        # 3. The ID of the workflow

        workflow = await self.create_db_workflow_from_dsl(
            dsl,
            workflow_id=external_defn.workflow_id if use_workflow_id else None,
            created_at=external_defn.created_at,
            updated_at=external_defn.updated_at,
            trigger_position=trigger_position,
            viewport=viewport,
            action_positions=action_positions,
        )
        if external_defn.case_trigger is not None:
            from tracecat.workflow.case_triggers.service import CaseTriggersService

            case_trigger_service = CaseTriggersService(self.session, role=self.role)
            await case_trigger_service.upsert_case_trigger(
                WorkflowUUID.new(workflow.id),
                external_defn.case_trigger,
                create_missing_tags=True,
                commit=False,
            )
            await self.session.commit()
        return workflow

    @require_scope("workflow:create")
    async def create_db_workflow_from_dsl(
        self,
        dsl: DSLInput,
        *,
        workflow_id: WorkflowID | None = None,
        workflow_alias: str | None = None,
        created_at: datetime | None = None,
        updated_at: datetime | None = None,
        trigger_position: tuple[float, float] | None = None,
        viewport: tuple[float, float, float] | None = None,
        action_positions: dict[str, tuple[float, float]] | None = None,
        commit: bool = True,
    ) -> Workflow:
        """Create a new workflow and associated actions in the database from a DSLInput."""
        self.logger.info("Creating workflow from DSL", dsl=dsl)

        # Resolve registry_lock with action bindings from the DSL
        action_names = {action.action for action in dsl.actions}
        lock_service = RegistryLockService(self.session, self.role)
        resolved_lock = await lock_service.resolve_lock_with_bindings(action_names)
        registry_lock = resolved_lock.model_dump()

        entrypoint = dsl.entrypoint.model_dump()
        workflow_kwargs = {
            "title": dsl.title,
            "description": dsl.description,
            "workspace_id": self.workspace_id,
            "returns": dsl.returns,
            "config": dsl.config.model_dump(),
            "expects": entrypoint.get("expects"),
            "registry_lock": registry_lock,
        }
        if workflow_id:
            workflow_kwargs["id"] = workflow_id
        if created_at:
            workflow_kwargs["created_at"] = created_at
        if updated_at:
            workflow_kwargs["updated_at"] = updated_at
        if workflow_alias:
            workflow_kwargs["alias"] = workflow_alias
        if trigger_position is not None:
            workflow_kwargs["trigger_position_x"] = trigger_position[0]
            workflow_kwargs["trigger_position_y"] = trigger_position[1]
        if viewport is not None:
            workflow_kwargs["viewport_x"] = viewport[0]
            workflow_kwargs["viewport_y"] = viewport[1]
            workflow_kwargs["viewport_zoom"] = viewport[2]
        workflow = Workflow(**workflow_kwargs)

        # Add the Workflow to the session first and flush to ensure ID is persisted
        self.session.add(workflow)
        await self.session.flush()
        self.logger.debug("Workflow created", workflow_id=workflow.id)

        # Create and associate Webhook with the Workflow
        webhook = Webhook(
            workspace_id=self.workspace_id,
            workflow_id=workflow.id,
        )
        self.session.add(webhook)
        workflow.webhook = webhook

        case_trigger = CaseTrigger(
            workspace_id=self.workspace_id,
            workflow_id=workflow.id,
            status="offline",
            event_types=[],
            tag_filters=[],
        )
        self.session.add(case_trigger)
        workflow.case_trigger = case_trigger

        # Create actions from DSL (actions have workflow_id set, relationship managed by FK)
        await self.create_actions_from_dsl(dsl, workflow.id, action_positions)

        # Commit the transaction
        if commit:
            await self.session.commit()
            await self.session.refresh(workflow)
        return workflow

    async def create_actions_from_dsl(
        self,
        dsl: DSLInput,
        workflow_id: uuid.UUID,
        action_positions: dict[str, tuple[float, float]] | None = None,
    ) -> list[Action]:
        """Create Action entities from DSL and add to session.

        Builds upstream_edges from depends_on relationships.
        For root actions (no depends_on), creates trigger->action edge.
        """
        # Create all actions and build ref->action mapping
        actions: list[Action] = []
        ref_to_action: dict[str, Action] = {}
        for act_stmt in dsl.actions:
            control_flow = ActionControlFlow(
                run_if=act_stmt.run_if,
                for_each=act_stmt.for_each,
                retry_policy=act_stmt.retry_policy,
                start_delay=act_stmt.start_delay,
                wait_until=act_stmt.wait_until,
                join_strategy=act_stmt.join_strategy,
            )
            pos = (action_positions or {}).get(act_stmt.ref)
            new_action = Action(
                id=uuid.uuid4(),
                workspace_id=self.workspace_id,
                workflow_id=workflow_id,
                type=act_stmt.action,
                inputs=yaml.dump(act_stmt.args),
                title=act_stmt.title,
                description=act_stmt.description,
                control_flow=control_flow.model_dump(),
                position_x=pos[0] if pos else 0.0,
                position_y=pos[1] if pos else 0.0,
            )
            actions.append(new_action)
            ref_to_action[act_stmt.ref] = new_action

        # Build upstream_edges (separate loop handles forward references in depends_on)
        trigger_id = f"trigger-{workflow_id}"
        for act_stmt in dsl.actions:
            action = ref_to_action[act_stmt.ref]
            upstream_edges: list[ActionEdge] = []

            if act_stmt.depends_on:
                for dep_ref in act_stmt.depends_on:
                    if dep_action := ref_to_action.get(dep_ref):
                        upstream_edges.append(
                            ActionEdge(
                                source_id=str(dep_action.id),
                                source_type="udf",
                                source_handle="success",
                            )
                        )
            else:
                upstream_edges.append(
                    ActionEdge(
                        source_id=trigger_id,
                        source_type="trigger",
                    )
                )
            action.upstream_edges = cast(list[dict[str, Any]], upstream_edges)

        for action in actions:
            self.session.add(action)
        await self.session.flush()
        return actions

    @staticmethod
    @activity.defn
    async def resolve_workflow_alias_activity(
        ctx: RunContext,
        operand: ExecutionContext,
        input: ResolveWorkflowAliasActivityInputs,
    ) -> WorkflowID | None:
        # Resolve expr
        # Materialize any StoredObjects in operand before evaluation
        materialized = await materialize_context(operand)
        token = ctx_logical_time.set(ctx.logical_time)
        try:
            evaluated_alias = eval_templated_object(
                input.workflow_alias, operand=materialized
            )
        finally:
            ctx_logical_time.reset(token)
        if not isinstance(evaluated_alias, str):
            raise TypeError(
                f"Workflow alias expression must evaluate to a string. Got {type(evaluated_alias).__name__}"
            )
        async with WorkflowsManagementService.with_session(input.role) as service:
            return await service.resolve_workflow_alias(
                evaluated_alias, use_committed=input.use_committed
            )

    @staticmethod
    @activity.defn
    async def get_error_handler_workflow_id(
        input: GetErrorHandlerWorkflowIDActivityInputs,
    ) -> WorkflowID | None:
        args = input.args
        id_or_alias = None
        if args.dsl:
            # 1. If a DSL was provided, we must use its error handler
            if not args.dsl.error_handler:
                activity.logger.info("DSL has no error handler")
                return None
            id_or_alias = args.dsl.error_handler
        else:
            # 2. Otherwise, get error handler from the committed definition
            # This ensures schedules use the committed error handler
            async with WorkflowDefinitionsService.with_session(
                role=args.role
            ) as defn_service:
                defn = await defn_service.get_definition_by_workflow_id(args.wf_id)
            if not defn:
                activity.logger.info("No committed definition found")
                return None
            dsl = defn.content
            if not dsl or not dsl.get("error_handler"):
                activity.logger.info("Committed definition has no error handler")
                return None
            id_or_alias = dsl["error_handler"]

        # 3. Convert the error handler to an ID
        if re.match(LEGACY_WF_ID_PATTERN, id_or_alias):
            # TODO: Legacy workflow ID for backwards compatibility. Slowly deprecate.
            handler_wf_id = WorkflowUUID.from_legacy(id_or_alias)
        elif re.match(WF_ID_SHORT_PATTERN, id_or_alias):
            # Short workflow ID
            handler_wf_id = WorkflowUUID.new(id_or_alias)
        else:
            use_committed = args.execution_type == ExecutionType.PUBLISHED
            async with WorkflowsManagementService.with_session(
                role=args.role
            ) as service:
                handler_wf_id = await service.resolve_workflow_alias(
                    id_or_alias, use_committed=use_committed
                )
            if not handler_wf_id:
                raise ApplicationError(
                    f"Couldn't find matching workflow for alias {id_or_alias!r}",
                    non_retryable=True,
                    type="WorkflowAliasResolutionError",
                )
        return handler_wf_id
