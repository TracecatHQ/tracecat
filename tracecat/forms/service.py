"""Services for forms."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast

from pydantic import ValidationError
from sqlalchemy import and_, func, or_, select, true
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.sql.selectable import Select

from tracecat import config
from tracecat.audit.logger import audit_log
from tracecat.auth.credentials import compute_effective_scopes
from tracecat.authz.controls import has_scope
from tracecat.db.engine import get_async_session_bypass_rls_context_manager
from tracecat.db.models import (
    Form,
    FormEvent,
    FormSubmission,
    OrganizationMembership,
    ServiceAccount,
    User,
    Workflow,
    WorkflowDefinition,
    Workspace,
)
from tracecat.dsl.common import DSLInput, apply_expects_order
from tracecat.exceptions import TracecatConflictError, TracecatNotFoundError
from tracecat.expressions.schemas import ExpectedField
from tracecat.forms.constants import (
    FORM_VIEW_EVENT_DEDUP_WINDOW,
    SUBMISSION_REFERENCE_PREFIX,
)
from tracecat.forms.enums import (
    FormErrorCode,
    FormEventType,
    FormStatus,
    FormWidget,
)
from tracecat.forms.exceptions import FormError
from tracecat.forms.resolver import resolve_form
from tracecat.forms.schemas import (
    FormActorRead,
    FormConfig,
    FormCounts,
    FormCreate,
    FormEventRead,
    FormFieldBlock,
    FormIntakeLocation,
    FormIntakeRead,
    FormIntakeSubmissionRead,
    FormPreview,
    FormRead,
    FormReadMinimal,
    FormResolved,
    FormUpdate,
    FormUserRead,
    FormValidateResponse,
)
from tracecat.forms.types import ResolveResult
from tracecat.identifiers import OrganizationID, WorkspaceID
from tracecat.identifiers.workflow import WorkflowUUID
from tracecat.pagination import CursorPaginatedResponse, Page, PageParams, paginate
from tracecat.service import BaseOrgService, BaseWorkspaceService
from tracecat.workflow.management.definitions import WorkflowDefinitionsService
from tracecat.workspaces.service import WorkspaceService

# The fastapi-users base types these columns as plain values, so queries go
# through the table.
USERS = User.__table__


@dataclass(frozen=True, slots=True)
class FormInputs:
    """The workflow side of a form: its titles and its input schema."""

    workflow_title: str
    workflow_description: str
    expects: dict[str, ExpectedField]
    definition: WorkflowDefinition | None
    """The published definition the inputs come from. None while the workflow
    is unpublished; the inputs are then the draft's."""
    dsl: DSLInput | None

    @property
    def definition_version(self) -> int | None:
        return self.definition.version if self.definition is not None else None


def submission_reference(submission_id: uuid.UUID) -> str:
    """Short code a requester can quote for a submission."""
    return f"{SUBMISSION_REFERENCE_PREFIX}{submission_id.hex[:8].upper()}"


def stored_form_config(form: Form) -> FormConfig:
    """Build the config from a form row.

    Reads always go through ``FormConfig`` because JSONB reorders the keys
    inside each layout entry.
    """
    return FormConfig.model_validate(
        {
            "title": form.title,
            "description": form.description,
            "success_message": form.success_message,
            "layout": form.layout,
        }
    )


def _set_form_config(form: Form, form_config: FormConfig) -> None:
    data = form_config.model_dump(mode="json")
    form.title = data.get("title")
    form.description = data.get("description")
    form.success_message = data.get("success_message")
    form.layout = data.get("layout")


def _preview(
    form_config: FormConfig, inputs: FormInputs, result: ResolveResult
) -> FormPreview:
    return FormPreview(
        title=form_config.title or inputs.workflow_title,
        description=form_config.description or inputs.workflow_description or None,
        success_message=form_config.success_message,
        definition_version=inputs.definition_version,
        blocks=result.blocks,
    )


def page_response[T](page: Page[T]) -> CursorPaginatedResponse[T]:
    return CursorPaginatedResponse(
        items=page.items,
        next_cursor=page.next_cursor,
        prev_cursor=page.prev_cursor,
        has_more=page.has_more,
        has_previous=page.has_previous,
    )


def actor_read(
    user_id: uuid.UUID | None,
    email: str | None,
    first_name: str | None,
    last_name: str | None,
    service_account_id: uuid.UUID | None,
    service_account_name: str | None,
) -> FormActorRead | None:
    if user_id is not None:
        return FormActorRead(
            type="user",
            id=user_id,
            email=email,
            first_name=first_name,
            last_name=last_name,
        )
    if service_account_id is not None:
        return FormActorRead(
            type="service_account", id=service_account_id, name=service_account_name
        )
    return None


def org_users(organization_id: OrganizationID) -> Select[Any]:
    """Select the active members of an organization, as the ``user`` widget
    sees them: ID, email, first name and last name."""
    return (
        select(USERS.c.id, USERS.c.email, USERS.c.first_name, USERS.c.last_name)
        .join(
            OrganizationMembership,
            and_(
                OrganizationMembership.user_id == USERS.c.id,
                OrganizationMembership.organization_id == organization_id,
            ),
        )
        .where(USERS.c.is_active.is_(True))
    )


def user_read(values: tuple[object, ...]) -> FormUserRead:
    """Build a user from a row of ``org_users``."""
    return FormUserRead(
        user_id=cast(uuid.UUID, values[0]),
        email=cast(str, values[1]),
        first_name=cast(str | None, values[2]),
        last_name=cast(str | None, values[3]),
    )


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class FormsService(BaseWorkspaceService):
    """Manage the forms of a workspace."""

    service_name = "forms"

    def _has_scope(self, scope: str) -> bool:
        return has_scope(self.role.scopes or frozenset(), scope)

    # ----- Inputs -----

    async def load_inputs(self, workflow_id: uuid.UUID) -> FormInputs | None:
        """Load the input schema a form on this workflow resolves against.

        Inputs come from the published definition, read through ``DSLInput``
        so they are in the author's order. An unpublished workflow falls back
        to its draft inputs so a form can be prepared before the first publish.

        Returns:
            The inputs, or None if the workflow does not exist.

        Raises:
            FormError: ``form_unavailable`` if the published definition cannot
                be read.
        """
        statement = select(
            Workflow.title,
            Workflow.description,
            Workflow.expects,
            Workflow.expects_order,
        ).where(Workflow.workspace_id == self.workspace_id, Workflow.id == workflow_id)
        row = (await self.session.execute(statement)).tuples().one_or_none()
        if row is None:
            return None
        title, description, draft_expects, draft_order = row

        definitions = WorkflowDefinitionsService(self.session, role=self.role)
        definition = await definitions.get_definition_by_workflow_id(
            WorkflowUUID.new(workflow_id), load_relationships=False
        )
        try:
            if definition is None:
                dsl = None
                ordered = apply_expects_order(draft_expects, draft_order) or {}
                expects = {
                    name: ExpectedField.model_validate(expected)
                    for name, expected in ordered.items()
                }
            else:
                dsl = DSLInput(**definition.content)
                expects = dsl.entrypoint.expects or {}
        except ValidationError as e:
            raise FormError(
                FormErrorCode.FORM_UNAVAILABLE,
                "The workflow's input schema cannot be read",
            ) from e
        return FormInputs(
            workflow_title=title,
            workflow_description=description,
            expects=expects,
            definition=definition,
            dsl=dsl,
        )

    async def _require_inputs(self, workflow_id: uuid.UUID) -> FormInputs:
        if (inputs := await self.load_inputs(workflow_id)) is None:
            raise TracecatNotFoundError("Workflow not found")
        return inputs

    def _check_saveable(
        self, form_config: FormConfig, inputs: FormInputs, status: FormStatus
    ) -> None:
        result = resolve_form(form_config, inputs.expects)
        if result.has_errors:
            raise FormError(
                FormErrorCode.FORM_CONFIG_INVALID,
                "The form config has errors",
                issues=result.issues,
            )
        if status is FormStatus.ONLINE and inputs.definition is None:
            raise FormError(
                FormErrorCode.FORM_WORKFLOW_UNPUBLISHED,
                "Publish the workflow before putting its form online",
            )

    async def resolve_published(
        self, form: Form
    ) -> tuple[FormInputs, FormConfig, ResolveResult]:
        """Resolve an online form against its published inputs.

        Runs on every render and submit, because the published schema can
        change after the form is saved.

        Raises:
            FormError: ``form_offline``, or ``form_unavailable`` if the
                workflow is unpublished or the config no longer resolves.
        """
        if form.status != FormStatus.ONLINE:
            raise FormError(FormErrorCode.FORM_OFFLINE, "This form is offline")
        unavailable = FormError(
            FormErrorCode.FORM_UNAVAILABLE, "This form is not available right now"
        )
        inputs = await self.load_inputs(form.workflow_id)
        if inputs is None or inputs.definition is None:
            raise unavailable
        current_config = stored_form_config(form)
        result = resolve_form(current_config, inputs.expects)
        if result.has_errors:
            raise unavailable
        return inputs, current_config, result

    # ----- Config -----

    async def get_form(self, form_id: uuid.UUID) -> Form | None:
        statement = select(Form).where(
            Form.workspace_id == self.workspace_id, Form.id == form_id
        )
        return (await self.session.execute(statement)).scalar_one_or_none()

    async def get_form_by_workflow_id(self, workflow_id: uuid.UUID) -> Form | None:
        statement = select(Form).where(
            Form.workspace_id == self.workspace_id, Form.workflow_id == workflow_id
        )
        return (await self.session.execute(statement)).scalar_one_or_none()

    async def list_forms(
        self, page: PageParams
    ) -> CursorPaginatedResponse[FormReadMinimal]:
        """List forms newest first.

        Callers without ``form:update`` only see online forms. Submission
        counts need ``form:submission:read``.
        """
        submissions = (
            select(
                func.count().label("submission_count"),
                func.max(FormSubmission.created_at).label("last_submitted_at"),
            )
            .where(
                FormSubmission.workspace_id == Form.workspace_id,
                FormSubmission.form_id == Form.id,
            )
            .correlate(Form)
            .lateral()
        )
        statement = (
            select(
                Form.id,
                Form.workflow_id,
                Workflow.title,
                Form.status,
                func.coalesce(Form.title, Workflow.title),
                func.coalesce(Form.description, func.nullif(Workflow.description, "")),
                submissions.c.submission_count,
                submissions.c.last_submitted_at,
                Form.created_at,
                Form.updated_at,
            )
            .join(Workflow, Workflow.id == Form.workflow_id)
            .join(submissions, true())
            .where(Form.workspace_id == self.workspace_id)
        )
        if not self._has_scope("form:update"):
            statement = statement.where(Form.status == FormStatus.ONLINE)
        show_counts = self._has_scope("form:submission:read")

        def to_read(values: tuple[object, ...]) -> FormReadMinimal:
            return FormReadMinimal(
                id=cast(uuid.UUID, values[0]),
                workflow_id=WorkflowUUID.new(values[1]),
                workflow_title=cast(str, values[2]),
                status=FormStatus(cast(str, values[3])),
                title=cast(str, values[4]),
                description=cast(str | None, values[5]),
                submission_count=cast(int, values[6]) if show_counts else None,
                last_submitted_at=(
                    cast(datetime | None, values[7]) if show_counts else None
                ),
                created_at=cast(datetime, values[8]),
                updated_at=cast(datetime, values[9]),
            )

        result = await paginate(
            self.session,
            statement,
            page=page,
            order_by=(Form.created_at.desc(), Form.surrogate_id.desc()),
            row_factory=to_read,
        )
        return page_response(result)

    async def read_form(self, form: Form) -> FormRead:
        """Build the builder view of a form: config, issues, URL and counts."""
        inputs = await self._require_inputs(form.workflow_id)
        current_config = stored_form_config(form)
        result = resolve_form(current_config, inputs.expects)
        counts = None
        if self._has_scope("form:submission:read"):
            counts = await self._count_activity(form)
        return FormRead(
            id=form.id,
            workspace_id=form.workspace_id,
            workflow_id=WorkflowUUID.new(form.workflow_id),
            workflow_title=inputs.workflow_title,
            workflow_version=inputs.definition_version,
            status=FormStatus(form.status),
            config=current_config,
            issues=result.issues,
            url=f"{config.TRACECAT__PUBLIC_APP_URL}/forms/{form.id}",
            counts=counts,
            created_at=form.created_at,
            updated_at=form.updated_at,
        )

    async def _count_activity(self, form: Form) -> FormCounts:
        views = (
            select(func.count())
            .where(
                FormEvent.workspace_id == self.workspace_id,
                FormEvent.form_id == form.id,
                FormEvent.type == FormEventType.FORM_VIEWED,
            )
            .scalar_subquery()
        )
        submissions = (
            select(func.count())
            .where(
                FormSubmission.workspace_id == self.workspace_id,
                FormSubmission.form_id == form.id,
            )
            .scalar_subquery()
        )
        row = (await self.session.execute(select(views, submissions))).tuples().one()
        return FormCounts(views=row[0], submissions=row[1])

    @audit_log(resource_type="form", action="create")
    async def create_form(self, params: FormCreate) -> Form:
        """Create the form of a workflow.

        Raises:
            TracecatNotFoundError: If the workflow does not exist.
            TracecatConflictError: If the workflow already has a form.
            FormError: If the config has errors, or the form is created online
                on an unpublished workflow.
        """
        workflow_id = WorkflowUUID.new(params.workflow_id)
        inputs = await self._require_inputs(workflow_id)
        if await self.get_form_by_workflow_id(workflow_id) is not None:
            raise TracecatConflictError("This workflow already has a form")
        new_config = params.config if params.config is not None else FormConfig()
        self._check_saveable(new_config, inputs, params.status)

        form = Form(
            workspace_id=self.workspace_id,
            workflow_id=workflow_id,
            status=params.status.value,
        )
        _set_form_config(form, new_config)
        self.session.add(form)
        try:
            await self.session.commit()
        except IntegrityError as e:
            await self.session.rollback()
            raise TracecatConflictError("This workflow already has a form") from e
        await self.session.refresh(form)
        return form

    @audit_log(resource_type="form", action="update")
    async def update_form(self, form: Form, params: FormUpdate) -> Form:
        """Update a form's status, config or both. ``config`` replaces whole.

        Raises:
            FormError: If the new config has errors, or the form goes online
                with errors or on an unpublished workflow.
        """
        status = params.status or FormStatus(form.status)
        new_config = (
            params.config if params.config is not None else stored_form_config(form)
        )
        if params.config is not None or params.status is FormStatus.ONLINE:
            inputs = await self._require_inputs(form.workflow_id)
            self._check_saveable(new_config, inputs, status)

        form.status = status.value
        if params.config is not None:
            _set_form_config(form, params.config)
        await self.session.commit()
        await self.session.refresh(form)
        return form

    @audit_log(resource_type="form", action="delete")
    async def delete_form(self, form: Form) -> None:
        """Delete a form. Its submissions and events go with it."""
        await self.session.delete(form)
        await self.session.commit()

    async def validate_config(
        self, workflow_id: uuid.UUID, form_config: FormConfig
    ) -> FormValidateResponse:
        """Resolve an unsaved config against a workflow's inputs.

        Raises:
            TracecatNotFoundError: If the workflow does not exist.
        """
        inputs = await self._require_inputs(workflow_id)
        result = resolve_form(form_config, inputs.expects)
        return FormValidateResponse(
            valid=not result.has_errors,
            issues=result.issues,
            preview=_preview(form_config, inputs, result),
        )

    # ----- Render -----

    async def render_form(self, form: Form) -> FormResolved:
        """Resolve an online form for a requester.

        Raises:
            FormError: ``form_offline`` or ``form_unavailable``.
        """
        inputs, current_config, result = await self.resolve_published(form)
        preview = _preview(current_config, inputs, result)
        return FormResolved(
            id=form.id,
            workspace_id=form.workspace_id,
            workflow_id=WorkflowUUID.new(form.workflow_id),
            status=FormStatus(form.status),
            title=preview.title,
            description=preview.description,
            success_message=preview.success_message,
            definition_version=preview.definition_version,
            blocks=preview.blocks,
        )

    async def record_view(self, form: Form) -> None:
        """Record that the calling user viewed a form. Best effort.

        One view per user per form within the dedupe window. Service accounts
        are not recorded.
        """
        if self.role.type != "user" or self.role.user_id is None:
            return
        # Read before the try: a rollback expires the row.
        form_id = form.id
        recent_view = (
            select(FormEvent.id)
            .where(
                FormEvent.workspace_id == self.workspace_id,
                FormEvent.form_id == form_id,
                FormEvent.type == FormEventType.FORM_VIEWED,
                FormEvent.user_id == self.role.user_id,
                FormEvent.created_at
                >= datetime.now(UTC) - FORM_VIEW_EVENT_DEDUP_WINDOW,
            )
            .limit(1)
        )
        try:
            if (await self.session.execute(recent_view)).first() is not None:
                return
            self.session.add(
                FormEvent(
                    workspace_id=self.workspace_id,
                    form_id=form_id,
                    type=FormEventType.FORM_VIEWED.value,
                    user_id=self.role.user_id,
                )
            )
            await self.session.commit()
        except SQLAlchemyError as e:
            await self.session.rollback()
            self.logger.warning(
                "Failed to record form view",
                form_id=form_id,
                error_type=type(e).__name__,
            )

    # ----- Activity -----

    async def list_events(
        self, form: Form, page: PageParams
    ) -> CursorPaginatedResponse[FormEventRead]:
        """List a form's views and submissions newest first."""
        statement = (
            select(
                FormEvent.id,
                FormEvent.type,
                FormEvent.submission_id,
                FormEvent.created_at,
                FormEvent.user_id,
                USERS.c.email,
                USERS.c.first_name,
                USERS.c.last_name,
                FormEvent.service_account_id,
                ServiceAccount.name,
            )
            .outerjoin(USERS, USERS.c.id == FormEvent.user_id)
            .outerjoin(
                ServiceAccount, ServiceAccount.id == FormEvent.service_account_id
            )
            .where(
                FormEvent.workspace_id == self.workspace_id,
                FormEvent.form_id == form.id,
            )
        )

        def to_read(values: tuple[object, ...]) -> FormEventRead:
            submission_id = cast(uuid.UUID | None, values[2])
            return FormEventRead(
                id=cast(uuid.UUID, values[0]),
                type=FormEventType(cast(str, values[1])),
                submission_id=submission_id,
                reference=(
                    submission_reference(submission_id)
                    if submission_id is not None
                    else None
                ),
                created_at=cast(datetime, values[3]),
                actor=actor_read(
                    cast(uuid.UUID | None, values[4]),
                    cast(str | None, values[5]),
                    cast(str | None, values[6]),
                    cast(str | None, values[7]),
                    cast(uuid.UUID | None, values[8]),
                    cast(str | None, values[9]),
                ),
            )

        result = await paginate(
            self.session,
            statement,
            page=page,
            order_by=(FormEvent.created_at.desc(), FormEvent.surrogate_id.desc()),
            row_factory=to_read,
        )
        return page_response(result)

    # ----- User widget -----

    async def search_users(
        self, form: Form, page: PageParams, *, search: str | None = None
    ) -> CursorPaginatedResponse[FormUserRead]:
        """Search organization members for a form's ``user`` widget.

        Raises:
            TracecatNotFoundError: If the form has no ``user`` widget.
        """
        inputs = await self._require_inputs(form.workflow_id)
        result = resolve_form(stored_form_config(form), inputs.expects)
        if not any(
            isinstance(block, FormFieldBlock) and block.widget is FormWidget.USER
            for block in result.blocks
        ):
            raise TracecatNotFoundError("This form has no user field")

        statement = org_users(self.organization_id)
        if search:
            pattern = f"%{_escape_like(search)}%"
            statement = statement.where(
                or_(
                    USERS.c.email.ilike(pattern, escape="\\"),
                    USERS.c.first_name.ilike(pattern, escape="\\"),
                    USERS.c.last_name.ilike(pattern, escape="\\"),
                )
            )

        # Email is unique, so it is a total ordering on its own.
        users = await paginate(
            self.session,
            statement,
            page=page,
            order_by=(USERS.c.email.asc(),),
            row_factory=user_read,
        )
        return page_response(users)


class FormsIntakeService(BaseOrgService):
    """Requester-side lookups that span the caller's workspaces.

    These routes carry no workspace, so the request's RLS context cannot see
    form rows. Each method works out which workspaces the caller may read
    forms in, then reads with RLS bypassed and an explicit organization and
    workspace filter.
    """

    service_name = "forms_intake"

    async def _can_read_forms(self, workspace_id: WorkspaceID) -> bool:
        """Whether the caller holds ``form:read`` in a workspace.

        Scopes are matched in Python: custom roles can hold wildcards.
        """
        if self.role.type != "user":
            return False
        workspace_role = self.role.model_copy(update={"workspace_id": workspace_id})
        scopes = await compute_effective_scopes(workspace_role)
        return has_scope(scopes, "form:read")

    async def _readable_workspace_ids(self) -> list[WorkspaceID]:
        workspaces = WorkspaceService(self.session, role=self.role)
        return [
            workspace.id
            for workspace in await workspaces.list_accessible_workspaces()
            if await self._can_read_forms(workspace.id)
        ]

    async def locate_form(self, form_id: uuid.UUID) -> FormIntakeLocation | None:
        """Find the workspace of a form the caller may read.

        Returns:
            The location, or None for an unknown form, a form in another
            organization, or a workspace where the caller lacks ``form:read``.
            Callers must not tell these cases apart.
        """
        statement = (
            select(Form.workspace_id, Workspace.name, Workspace.organization_id)
            .join(Workspace, Workspace.id == Form.workspace_id)
            .where(Form.id == form_id)
        )
        async with get_async_session_bypass_rls_context_manager() as session:
            row = (await session.execute(statement)).tuples().one_or_none()
        if row is None:
            return None
        workspace_id, workspace_name, organization_id = row
        if organization_id != self.organization_id:
            return None
        if not await self._can_read_forms(workspace_id):
            return None
        return FormIntakeLocation(
            form_id=form_id, workspace_id=workspace_id, workspace_name=workspace_name
        )

    async def list_forms(
        self, page: PageParams
    ) -> CursorPaginatedResponse[FormIntakeRead]:
        """List online forms across the workspaces the caller may read forms in."""
        workspace_ids = await self._readable_workspace_ids()
        if not workspace_ids:
            return CursorPaginatedResponse(items=[])
        statement = (
            select(
                Form.id,
                Form.workspace_id,
                Workspace.name,
                func.coalesce(Form.title, Workflow.title),
                func.coalesce(Form.description, func.nullif(Workflow.description, "")),
            )
            .join(Workspace, Workspace.id == Form.workspace_id)
            .join(Workflow, Workflow.id == Form.workflow_id)
            .where(
                Workspace.organization_id == self.organization_id,
                Form.workspace_id.in_(workspace_ids),
                Form.status == FormStatus.ONLINE,
            )
        )

        def to_read(values: tuple[object, ...]) -> FormIntakeRead:
            return FormIntakeRead(
                id=cast(uuid.UUID, values[0]),
                workspace_id=cast(WorkspaceID, values[1]),
                workspace_name=cast(str, values[2]),
                title=cast(str, values[3]),
                description=cast(str | None, values[4]),
            )

        async with get_async_session_bypass_rls_context_manager() as session:
            forms = await paginate(
                session,
                statement,
                page=page,
                order_by=(Form.created_at.desc(), Form.surrogate_id.desc()),
                row_factory=to_read,
            )
        return page_response(forms)

    async def list_submissions(
        self, page: PageParams
    ) -> CursorPaginatedResponse[FormIntakeSubmissionRead]:
        """List the caller's own submissions newest first, without run fields."""
        workspace_ids = await self._readable_workspace_ids()
        if not workspace_ids or self.role.user_id is None:
            return CursorPaginatedResponse(items=[])
        statement = (
            select(
                FormSubmission.id,
                FormSubmission.form_id,
                func.coalesce(Form.title, Workflow.title),
                FormSubmission.workspace_id,
                Workspace.name,
                FormSubmission.created_at,
            )
            .join(Form, Form.id == FormSubmission.form_id)
            .join(Workflow, Workflow.id == Form.workflow_id)
            .join(Workspace, Workspace.id == FormSubmission.workspace_id)
            .where(
                Workspace.organization_id == self.organization_id,
                FormSubmission.workspace_id.in_(workspace_ids),
                FormSubmission.submitted_by_user_id == self.role.user_id,
            )
        )

        def to_read(values: tuple[object, ...]) -> FormIntakeSubmissionRead:
            submission_id = cast(uuid.UUID, values[0])
            return FormIntakeSubmissionRead(
                id=submission_id,
                reference=submission_reference(submission_id),
                form_id=cast(uuid.UUID, values[1]),
                form_title=cast(str, values[2]),
                workspace_id=cast(WorkspaceID, values[3]),
                workspace_name=cast(str, values[4]),
                submitted_at=cast(datetime, values[5]),
            )

        async with get_async_session_bypass_rls_context_manager() as session:
            submissions = await paginate(
                session,
                statement,
                page=page,
                order_by=(
                    FormSubmission.created_at.desc(),
                    FormSubmission.surrogate_id.desc(),
                ),
                row_factory=to_read,
            )
        return page_response(submissions)
