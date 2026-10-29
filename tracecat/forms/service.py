"""Services for forms."""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError
from sqlalchemy import ColumnElement, and_, func, null, or_, select, true
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.selectable import Select

from tracecat import config
from tracecat.audit.logger import audit_log
from tracecat.authz.controls import has_scope
from tracecat.authz.service import query_effective_scopes_by_workspace
from tracecat.db.engine import (
    get_async_session_auth_context_manager,
    get_async_session_bypass_rls_context_manager,
)
from tracecat.db.errors import is_unique_violation
from tracecat.db.models import (
    Form,
    FormSubmission,
    FormView,
    OrganizationMembership,
    User,
    Workflow,
    WorkflowDefinition,
    Workspace,
)
from tracecat.dsl.common import DSLInput, apply_expects_order
from tracecat.exceptions import ScopeDeniedError
from tracecat.expressions.schemas import ExpectedField
from tracecat.forms.access import (
    can_edit_forms,
    can_read_form_config,
    can_read_submissions,
)
from tracecat.forms.constants import FORM_VIEW_DEDUP_WINDOW
from tracecat.forms.enums import FormErrorCode, FormStatus, FormWidget
from tracecat.forms.exceptions import FormError
from tracecat.forms.resolver import resolve_form
from tracecat.forms.schemas import (
    FormConfig,
    FormCounts,
    FormCreate,
    FormIntakeLocation,
    FormIntakeRead,
    FormIntakeSubmissionRead,
    FormPreview,
    FormRead,
    FormReadMinimal,
    FormUpdate,
    FormUserRead,
    FormValidateResponse,
)
from tracecat.forms.storage import delete_file_blobs, lock_forms_for_deletion
from tracecat.forms.types import ResolveResult
from tracecat.identifiers import OrganizationID, WorkspaceID
from tracecat.identifiers.workflow import WorkflowUUID
from tracecat.managed_resources.guard import ensure_unmanaged
from tracecat.pagination import CursorPaginatedResponse, PageParams, paginate
from tracecat.service import BaseOrgService, BaseWorkspaceService
from tracecat.workflow.management.definitions import WorkflowDefinitionsService
from tracecat.workspace_sync.enums import SyncResourceType
from tracecat.workspaces.service import WorkspaceService

# The fastapi-users base types these columns as plain values, so queries go
# through the table.
USERS = User.__table__

# An empty title or description falls back to the workflow's, as in render.
_FORM_TITLE = func.coalesce(func.nullif(Form.title, ""), Workflow.title)
_FORM_DESCRIPTION = func.coalesce(
    func.nullif(Form.description, ""), func.nullif(Workflow.description, "")
)


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


def is_configured(form: Form) -> bool:
    """Whether a form is online or has any config. Git sync writes only these."""
    return form.status == FormStatus.ONLINE or bool(
        stored_form_config(form).model_dump()
    )


def _set_form_config(form: Form, form_config: FormConfig) -> None:
    data = form_config.model_dump(mode="json")
    form.title = data.get("title")
    form.description = data.get("description")
    form.success_message = data.get("success_message")
    form.layout = data.get("layout")


def config_version(form_config: FormConfig) -> str:
    """A short hash of a config, so a submission can tell the config changed."""
    return hashlib.sha256(form_config.model_dump_json().encode()).hexdigest()[:16]


def _preview(
    inputs: FormInputs, form_config: FormConfig, result: ResolveResult
) -> FormPreview:
    return FormPreview(
        title=form_config.title or inputs.workflow_title,
        description=form_config.description or inputs.workflow_description or None,
        success_message=form_config.success_message,
        definition_version=inputs.definition_version,
        config_version=config_version(form_config),
        blocks=result.blocks,
    )


async def paginate_rows[T](
    session: AsyncSession,
    statement: Select[Any],
    build: Callable[[dict[str, Any]], T],
    page: PageParams,
    *order_by: ColumnElement[Any],
) -> CursorPaginatedResponse[T]:
    """Page through ``statement``, building each item from its labeled columns."""
    names = [str(column.key) for column in statement.selected_columns]
    result = await paginate(
        session,
        statement,
        page=page,
        order_by=order_by,
        row_factory=lambda values: build(dict(zip(names, values, strict=True))),
    )
    return CursorPaginatedResponse(
        items=result.items,
        next_cursor=result.next_cursor,
        prev_cursor=result.prev_cursor,
        has_more=result.has_more,
        has_previous=result.has_previous,
    )


def org_users(organization_id: OrganizationID) -> Select[Any]:
    """Select the active members of an organization as ``FormUserRead`` columns."""
    return (
        select(
            USERS.c.id.label("user_id"),
            USERS.c.email,
            USERS.c.first_name,
            USERS.c.last_name,
        )
        .join(
            OrganizationMembership,
            and_(
                OrganizationMembership.user_id == USERS.c.id,
                OrganizationMembership.organization_id == organization_id,
            ),
        )
        .where(USERS.c.is_active.is_(True))
    )


class FormsService(BaseWorkspaceService):
    """Manage the forms of a workspace."""

    service_name = "forms"

    # ----- Inputs -----

    async def load_inputs(self, workflow_id: uuid.UUID) -> FormInputs | None:
        """Load the input schema a form on this workflow resolves against.

        Inputs come from the published definition, read through ``DSLInput``
        so they are in the author's order. An unpublished workflow falls back
        to its draft inputs so a form can be prepared before the first publish.
        Returns None if the workflow does not exist.

        Raises:
            FormError: ``form_unavailable`` if the definition cannot be read.
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
            raise FormError(FormErrorCode.NOT_FOUND, "Workflow not found")
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

        Runs on every render, upload and submit, because the published schema
        can change after the form is saved. A requester is never told what is
        wrong with the config.

        Raises:
            FormError: ``form_offline``, or ``form_unavailable`` if the
                workflow is unpublished or the config no longer resolves.
        """
        if form.status != FormStatus.ONLINE:
            raise FormError(FormErrorCode.FORM_OFFLINE, "This form is offline")
        inputs = await self.load_inputs(form.workflow_id)
        if inputs is not None and inputs.definition is not None:
            current_config = stored_form_config(form)
            result = resolve_form(current_config, inputs.expects)
            if not result.has_errors:
                return inputs, current_config, result
        raise FormError(
            FormErrorCode.FORM_UNAVAILABLE, "This form is not available right now"
        )

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
        counts need ``form:submission:read``. The workflow fields need
        ``form:update`` or ``form:submission:read``.
        """
        submissions = (
            select(
                func.count().label("count"),
                func.max(FormSubmission.created_at).label("last"),
            )
            .where(
                FormSubmission.workspace_id == Form.workspace_id,
                FormSubmission.form_id == Form.id,
            )
            .correlate(Form)
            .lateral()
        )
        # A requester never handles the workflow behind a form.
        show_workflow = can_read_form_config(self.role)
        show_counts = can_read_submissions(self.role)
        statement = (
            select(
                Form.id,
                (Form.workflow_id if show_workflow else null()).label("workflow_id"),
                (Workflow.title if show_workflow else null()).label("workflow_title"),
                Form.status,
                _FORM_TITLE.label("title"),
                _FORM_DESCRIPTION.label("description"),
                (submissions.c.count if show_counts else null()).label(
                    "submission_count"
                ),
                (submissions.c.last if show_counts else null()).label(
                    "last_submitted_at"
                ),
                Form.created_at,
                Form.updated_at,
            )
            .join(Workflow, Workflow.id == Form.workflow_id)
            .join(submissions, true())
            .where(Form.workspace_id == self.workspace_id)
        )
        if not can_edit_forms(self.role):
            statement = statement.where(Form.status == FormStatus.ONLINE)
        return await paginate_rows(
            self.session,
            statement,
            FormReadMinimal.model_validate,
            page,
            Form.created_at.desc(),
            Form.surrogate_id.desc(),
        )

    async def read_form(self, form: Form) -> FormRead:
        """Build the builder view of a form: config, issues, URL and counts."""
        inputs = await self._require_inputs(form.workflow_id)
        current_config = stored_form_config(form)
        counts = None
        if can_read_submissions(self.role):
            views = select(func.count()).where(
                FormView.workspace_id == self.workspace_id, FormView.form_id == form.id
            )
            submissions = select(func.count()).where(
                FormSubmission.workspace_id == self.workspace_id,
                FormSubmission.form_id == form.id,
            )
            row = await self.session.execute(
                select(views.scalar_subquery(), submissions.scalar_subquery())
            )
            view_count, submission_count = row.tuples().one()
            counts = FormCounts(views=view_count, submissions=submission_count)
        return FormRead(
            id=form.id,
            workspace_id=form.workspace_id,
            workflow_id=WorkflowUUID.new(form.workflow_id).short(),
            workflow_title=inputs.workflow_title,
            workflow_version=inputs.definition_version,
            status=FormStatus(form.status),
            config=current_config,
            config_version=config_version(current_config),
            issues=resolve_form(current_config, inputs.expects).issues,
            url=f"{config.TRACECAT__PUBLIC_APP_URL}/forms/{form.id}",
            counts=counts,
            created_at=form.created_at,
            updated_at=form.updated_at,
        )

    @audit_log(resource_type="form", action="create")
    async def create_form(self, params: FormCreate) -> Form:
        """Create the form of a workflow. A workflow has at most one form.

        An offline form with the default config is created even if that config
        has errors, such as a required input no widget can render, so the
        editor can show them. It cannot go online until they are fixed.
        """
        workflow_id = WorkflowUUID.new(params.workflow_id)
        inputs = await self._require_inputs(workflow_id)
        already_exists = FormError(
            FormErrorCode.ALREADY_EXISTS, "This workflow already has a form"
        )
        if await self.get_form_by_workflow_id(workflow_id) is not None:
            raise already_exists
        new_config = params.config or FormConfig()
        if params.config is not None or params.status is FormStatus.ONLINE:
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
            if not is_unique_violation(e, "uq_form_workflow_id"):
                raise
            raise already_exists from e
        await self.session.refresh(form)
        return form

    @audit_log(resource_type="form", action="update")
    async def update_form(self, form: Form, params: FormUpdate) -> Form:
        """Update a form's status, config or both. ``config`` replaces whole.

        A config with errors is not saved, and an unpublished workflow's form
        cannot go online. Going offline is always allowed. With
        ``base_config_version``, an update to a config that changed since is
        rejected.

        Raises:
            FormError: ``form_changed`` for a stale ``base_config_version``, or
                the reason the config or status cannot be saved.
        """
        if params.base_config_version is not None:
            # Locked, so two saves from the same config cannot both pass.
            await self.session.refresh(form, with_for_update=True)
            if params.base_config_version != config_version(stored_form_config(form)):
                raise FormError(
                    FormErrorCode.FORM_CHANGED,
                    "This form changed since you opened it",
                )
        status = params.status or FormStatus(form.status)
        if params.config is not None or params.status is FormStatus.ONLINE:
            inputs = await self._require_inputs(form.workflow_id)
            new_config = params.config or stored_form_config(form)
            self._check_saveable(new_config, inputs, status)
        form.status = status.value
        if params.config is not None:
            _set_form_config(form, params.config)
        await self.session.commit()
        await self.session.refresh(form)
        return form

    async def upsert_form(self, workflow_id: uuid.UUID, params: FormUpdate) -> Form:
        """Update a workflow's form, creating it first if there is none.

        Audited as the update or the create it turns into.
        """
        # Customers may switch a managed workflow's form on and off; its
        # config comes from the package.
        if params.model_fields_set - {"status"}:
            await ensure_unmanaged(
                self.session,
                workspace_id=self.workspace_id,
                resource_type=SyncResourceType.WORKFLOW,
                local_id=workflow_id,
            )
        if (form := await self.get_form_by_workflow_id(workflow_id)) is not None:
            return await self.update_form(form, params)
        return await self.create_form(
            FormCreate(
                workflow_id=WorkflowUUID.new(workflow_id),
                status=params.status or FormStatus.OFFLINE,
                config=params.config,
            )
        )

    @audit_log(resource_type="form", action="delete")
    async def delete_form(self, form: Form) -> None:
        """Delete a form with its submissions, views and files.

        The file blobs are deleted after the commit, best effort.
        """
        keys = await lock_forms_for_deletion(
            self.session, self.workspace_id, Form.id == form.id
        )
        await self.session.delete(form)
        await self.session.commit()
        await delete_file_blobs(keys)

    async def sync_form(
        self, workflow_id: uuid.UUID, form_config: FormConfig | None, status: FormStatus
    ) -> None:
        """Write a workflow's form from a git pull, in the caller's transaction.

        Flushes without committing, checks no config and writes no audit event:
        a pulled config that no longer resolves is stored and shows its issues.
        The caller checks the scopes a pulled config needs. With no config, a
        configured form goes offline with an empty config and keeps its
        submissions, which needs ``form:update``. A workflow without a
        configured form is left alone.
        """
        form = await self.get_form_by_workflow_id(workflow_id)
        if form_config is None:
            if form is None or not is_configured(form):
                return
            if not has_scope(self.role.scopes or frozenset(), "form:update"):
                raise ScopeDeniedError(
                    required_scopes=["form:update"],
                    missing_scopes=["form:update"],
                    message="Taking a form offline and clearing it needs form:update",
                )
            form_config, status = FormConfig(), FormStatus.OFFLINE
        if form is None:
            form = Form(workspace_id=self.workspace_id, workflow_id=workflow_id)
            self.session.add(form)
        form.status = status.value
        _set_form_config(form, form_config)
        await self.session.flush()

    async def validate_config(
        self, form: Form, form_config: FormConfig
    ) -> FormValidateResponse:
        """Resolve an unsaved config against the inputs of a form's workflow."""
        inputs = await self._require_inputs(form.workflow_id)
        result = resolve_form(form_config, inputs.expects)
        return FormValidateResponse(
            valid=not result.has_errors,
            issues=result.issues,
            preview=_preview(inputs, form_config, result),
        )

    # ----- Render -----

    async def render_form(self, form: Form) -> FormPreview:
        """Resolve an online form for a requester."""
        return _preview(*await self.resolve_published(form))

    async def record_view(self, form: Form) -> None:
        """Record that the calling user viewed a form. Best effort.

        One view per user per form in each dedupe window. A unique key on the
        window makes concurrent views insert one row. Service accounts are
        not recorded.
        """
        if self.role.type != "user" or self.role.user_id is None:
            return
        # Read before the try: a rollback expires the row.
        form_id = form.id
        window = int(FORM_VIEW_DEDUP_WINDOW.total_seconds())
        now = int(datetime.now(UTC).timestamp())
        statement = (
            insert(FormView)
            .values(
                workspace_id=self.workspace_id,
                form_id=form_id,
                user_id=self.role.user_id,
                window_start=datetime.fromtimestamp(now - now % window, UTC),
            )
            .on_conflict_do_nothing(constraint="uq_form_view_form_user_window")
        )
        try:
            await self.session.execute(statement)
            await self.session.commit()
        except SQLAlchemyError as e:
            await self.session.rollback()
            self.logger.warning(
                "Failed to record form view",
                form_id=form_id,
                error_type=type(e).__name__,
            )

    # ----- User widget -----

    async def search_users(
        self, form: Form, page: PageParams, *, search: str | None = None
    ) -> CursorPaginatedResponse[FormUserRead]:
        """Search organization members for a form's ``user`` widget.

        An editor searches whatever the form's config and status, so the
        preview of an unsaved config that adds a ``user`` field works. Anyone
        else needs a form they could fill in: online, with a ``user`` field.

        Raises:
            FormError: For a non-editor, ``form_offline`` or
                ``form_unavailable``, or ``not_found`` if the form has no
                ``user`` field.
        """
        if not can_edit_forms(self.role):
            _, _, result = await self.resolve_published(form)
            if all(field.widget is not FormWidget.USER for field in result.fields):
                raise FormError(FormErrorCode.NOT_FOUND, "This form has no user field")

        statement = org_users(self.organization_id)
        if search:
            statement = statement.where(
                or_(
                    USERS.c.email.icontains(search, autoescape=True),
                    USERS.c.first_name.icontains(search, autoescape=True),
                    USERS.c.last_name.icontains(search, autoescape=True),
                )
            )
        # Email is unique, so it is a total ordering on its own.
        return await paginate_rows(
            self.session,
            statement,
            FormUserRead.model_validate,
            page,
            USERS.c.email.asc(),
        )


class FormsIntakeService(BaseOrgService):
    """Requester-side lookups that span the caller's workspaces.

    These routes carry no workspace, so the request's RLS context cannot see
    form rows. Each method works out which workspaces the caller may read
    forms in, then reads with RLS bypassed and an explicit organization and
    workspace filter.
    """

    service_name = "forms_intake"

    async def _readable_workspace_ids(self) -> list[WorkspaceID]:
        """The workspaces the caller can open and holds ``form:read`` in.

        One scope query covers every workspace. Scopes are matched in
        Python: custom roles can hold wildcards.
        """
        if self.role.type != "user" or self.role.user_id is None:
            return []
        workspaces = WorkspaceService(self.session, role=self.role)
        workspace_ids = [
            workspace.id for workspace in await workspaces.list_accessible_workspaces()
        ]
        if not workspace_ids or self.role.is_platform_superuser:
            return workspace_ids
        async with get_async_session_auth_context_manager() as session:
            scopes = await query_effective_scopes_by_workspace(
                session, self.role.user_id, self.organization_id, workspace_ids
            )
        return [
            workspace_id
            for workspace_id in workspace_ids
            if has_scope(scopes[workspace_id], "form:read")
        ]

    async def locate_form(self, form_id: uuid.UUID) -> FormIntakeLocation | None:
        """Find the workspace of a form the caller may read.

        Returns None for an unknown form, a form in another organization, or
        a workspace where the caller lacks ``form:read``. Callers must not
        tell these cases apart.
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
        # The same check as the catalog: an organization-wide form:read alone
        # does not reach a workspace the caller cannot open.
        if workspace_id not in await self._readable_workspace_ids():
            return None
        return FormIntakeLocation(
            form_id=form_id, workspace_id=workspace_id, workspace_name=workspace_name
        )

    async def list_forms(
        self, page: PageParams
    ) -> CursorPaginatedResponse[FormIntakeRead]:
        """List online forms across the workspaces the caller may read forms in."""
        if not (workspace_ids := await self._readable_workspace_ids()):
            return CursorPaginatedResponse(items=[])
        statement = (
            select(
                Form.id,
                Form.workspace_id,
                Workspace.name.label("workspace_name"),
                _FORM_TITLE.label("title"),
                _FORM_DESCRIPTION.label("description"),
            )
            .join(Workspace, Workspace.id == Form.workspace_id)
            .join(Workflow, Workflow.id == Form.workflow_id)
            .where(
                Workspace.organization_id == self.organization_id,
                Form.workspace_id.in_(workspace_ids),
                Form.status == FormStatus.ONLINE,
            )
        )
        async with get_async_session_bypass_rls_context_manager() as session:
            return await paginate_rows(
                session,
                statement,
                FormIntakeRead.model_validate,
                page,
                Form.created_at.desc(),
                Form.surrogate_id.desc(),
            )

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
                _FORM_TITLE.label("form_title"),
                FormSubmission.workspace_id,
                Workspace.name.label("workspace_name"),
                FormSubmission.created_at.label("submitted_at"),
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
        async with get_async_session_bypass_rls_context_manager() as session:
            return await paginate_rows(
                session,
                statement,
                FormIntakeSubmissionRead.model_validate,
                page,
                FormSubmission.created_at.desc(),
                FormSubmission.surrogate_id.desc(),
            )
