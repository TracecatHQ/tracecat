"""Submitting a form and reading its submissions."""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any, cast

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.sql.selectable import Select

from tracecat.audit.logger import audit_log
from tracecat.auth.types import Role
from tracecat.authz.controls import has_scope
from tracecat.authz.scopes import SERVICE_PRINCIPAL_SCOPES
from tracecat.db.models import Form, FormEvent, FormSubmission, ServiceAccount
from tracecat.dsl.validation import normalize_trigger_inputs
from tracecat.forms.enums import (
    FormAutofillSource,
    FormErrorCode,
    FormEventType,
    FormFieldErrorCode,
    FormWidget,
)
from tracecat.forms.exceptions import FormError
from tracecat.forms.schemas import (
    FormFieldBlock,
    FormFieldError,
    FormSubmissionCreate,
    FormSubmissionRead,
    FormSubmissionReceipt,
    FormUserRead,
)
from tracecat.forms.service import (
    USERS,
    FormsService,
    actor_read,
    org_users,
    page_response,
    submission_reference,
    user_read,
)
from tracecat.forms.values import check_values
from tracecat.identifiers import InternalServiceID
from tracecat.identifiers.workflow import WorkflowUUID, generate_exec_id
from tracecat.pagination import CursorPaginatedResponse, PageParams, paginate
from tracecat.registry.lock.types import RegistryLock
from tracecat.service import BaseWorkspaceService
from tracecat.workflow.executions.enums import (
    TriggerType,
    WorkflowExecutionStatusLiteral,
)
from tracecat.workflow.executions.service import WorkflowExecutionsService

_RUN_SERVICE_ID: InternalServiceID = "tracecat-runner"


def _pydantic_field_errors(error: ValidationError) -> list[FormFieldError]:
    """Map input-schema validation errors to the fields they belong to."""
    errors: list[FormFieldError] = []
    for detail in error.errors(include_input=False, include_url=False):
        match detail["type"]:
            case "missing":
                code = FormFieldErrorCode.REQUIRED
            case "extra_forbidden":
                code = FormFieldErrorCode.UNKNOWN_FIELD
            case _:
                code = FormFieldErrorCode.INVALID_VALUE
        loc = detail["loc"]
        errors.append(
            FormFieldError(
                field=str(loc[0]) if loc else "", code=code, message=detail["msg"]
            )
        )
    return errors


def _requester_value(source: FormAutofillSource, requester: FormUserRead) -> Any:
    match source:
        case FormAutofillSource.REQUESTER:
            return requester.model_dump(mode="json")
        case FormAutofillSource.REQUESTER_USER_ID:
            return str(requester.user_id)
        case FormAutofillSource.REQUESTER_EMAIL:
            return requester.email
        case FormAutofillSource.REQUESTER_FIRST_NAME:
            return requester.first_name
        case FormAutofillSource.REQUESTER_LAST_NAME:
            return requester.last_name
        case FormAutofillSource.SUBMISSION_ID:
            raise ValueError("submission.id is not a requester value")


class FormSubmissionsService(BaseWorkspaceService):
    """Submit forms and manage what was submitted."""

    service_name = "form_submissions"

    def _has_scope(self, scope: str) -> bool:
        return has_scope(self.role.scopes or frozenset(), scope)

    # ----- Submit -----

    async def submit(
        self, form: Form, params: FormSubmissionCreate
    ) -> FormSubmissionReceipt:
        """Validate a submission, store it and start the workflow.

        The submission row and the run start succeed or fail together: the row
        is flushed, the run is started, and only then is the row committed.

        Raises:
            FormError: ``form_offline``, ``form_unavailable``, ``form_changed``,
                ``form_validation_error`` with per-field errors, or
                ``workflow_start_failed``.
        """
        # Read before any rollback: a rollback expires the row.
        form_id = form.id
        workflow_id = WorkflowUUID.new(form.workflow_id)

        inputs, config, resolved = await FormsService(
            self.session, self.role
        ).resolve_published(form)
        definition, dsl = inputs.definition, inputs.dsl
        if definition is None or dsl is None:
            raise FormError(
                FormErrorCode.FORM_UNAVAILABLE, "This form is not available right now"
            )
        if (
            params.definition_version is not None
            and params.definition_version != definition.version
        ):
            raise FormError(
                FormErrorCode.FORM_CHANGED,
                "This form changed since you opened it. Reload and try again.",
            )

        values = dict(params.values)
        # A service account has no user to autofill from, so it may send the
        # autofilled inputs itself. From a user they are unknown keys.
        supplied: dict[str, Any] = {}
        if self.role.type == "service_account":
            supplied = {
                name: values.pop(name) for name in resolved.autofill if name in values
            }
        checked = check_values(resolved.blocks, values)
        answers = checked.values
        errors = [
            *checked.errors,
            *await self._expand_users(checked.visible, answers),
            *await self._claim_files(checked.visible, answers),
        ]
        if errors:
            raise FormError(
                FormErrorCode.FORM_VALIDATION_ERROR,
                "Some answers are not valid",
                errors=errors,
            )

        submission_id = uuid.uuid4()
        answers |= await self._autofill(resolved.autofill, supplied, submission_id)
        try:
            # The same normalized inputs become the stored answers and the
            # run's trigger inputs. Hidden inputs take their defaults here.
            normalized = cast(
                dict[str, Any], normalize_trigger_inputs(inputs.expects, answers)
            )
        except ValidationError as e:
            raise FormError(
                FormErrorCode.FORM_VALIDATION_ERROR,
                "Some answers are not valid",
                errors=_pydantic_field_errors(e),
            ) from e

        wf_exec_id = generate_exec_id(workflow_id)
        submission = FormSubmission(
            id=submission_id,
            workspace_id=self.workspace_id,
            form_id=form_id,
            submitted_by_user_id=self._user_id,
            submitted_by_service_account_id=self.role.service_account_id,
            answers=normalized,
            definition_version=definition.version,
            wf_exec_id=wf_exec_id,
        )
        self.session.add(submission)
        await self.session.flush()
        self.session.add(
            FormEvent(
                workspace_id=self.workspace_id,
                form_id=form_id,
                type=FormEventType.FORM_SUBMITTED.value,
                user_id=self._user_id,
                service_account_id=self.role.service_account_id,
                submission_id=submission_id,
            )
        )
        await self.session.flush()

        try:
            executions = await WorkflowExecutionsService.connect(role=self.run_role())
            await executions.create_workflow_execution_wait_for_start(
                dsl=dsl,
                wf_id=workflow_id,
                payload=normalized,
                trigger_type=TriggerType.FORM,
                wf_exec_id=wf_exec_id,
                registry_lock=(
                    RegistryLock.model_validate(definition.registry_lock)
                    if definition.registry_lock
                    else None
                ),
                memo={
                    "form_id": str(form_id),
                    "form_submission_id": str(submission_id),
                },
            )
        except Exception as e:
            await self.session.rollback()
            # Never log the answers.
            self.logger.error(
                "Failed to start the workflow of a form submission",
                form_id=form_id,
                wf_exec_id=wf_exec_id,
                error_type=type(e).__name__,
            )
            raise FormError(
                FormErrorCode.WORKFLOW_START_FAILED,
                "The request could not be started. Try again.",
            ) from e

        await self.session.commit()
        await self.session.refresh(submission)
        return FormSubmissionReceipt(
            id=submission_id,
            reference=submission_reference(submission_id),
            submitted_at=submission.created_at,
            success_message=config.success_message,
        )

    @property
    def _user_id(self) -> uuid.UUID | None:
        """The submitting user. None for a service account."""
        return self.role.user_id if self.role.type == "user" else None

    def run_role(self) -> Role:
        """The identity a form-triggered run executes as.

        A requester may hold nothing but ``form:read`` and ``form:submit``,
        which would fail every ``action:*:execute`` check in the executor. The
        run therefore executes as the runner service, the way a webhook run
        does, and carries the requester's ``user_id`` so the run still records
        who triggered it.
        """
        return Role(
            type="service",
            service_id=_RUN_SERVICE_ID,
            user_id=self._user_id,
            workspace_id=self.workspace_id,
            organization_id=self.organization_id,
            scopes=SERVICE_PRINCIPAL_SCOPES[_RUN_SERVICE_ID],
        )

    async def _expand_users(
        self, fields: Sequence[FormFieldBlock], answers: dict[str, Any]
    ) -> list[FormFieldError]:
        """Replace the user IDs of ``user`` fields with user objects, in place.

        One query covers every user field. An ID that is not an active member
        of the organization is a field error.
        """
        user_ids_by_field: dict[str, list[uuid.UUID]] = {}
        errors: list[FormFieldError] = []
        for field in fields:
            if field.widget is not FormWidget.USER or field.name not in answers:
                continue
            answer = answers[field.name]
            try:
                user_ids_by_field[field.name] = [
                    uuid.UUID(item) for item in (answer if field.multiple else [answer])
                ]
            except (AttributeError, TypeError, ValueError):
                errors.append(
                    FormFieldError(
                        field=field.name,
                        code=FormFieldErrorCode.INVALID_USER,
                        message="Choose a person from the list",
                    )
                )
        if not user_ids_by_field:
            return errors

        wanted = {
            user_id for user_ids in user_ids_by_field.values() for user_id in user_ids
        }
        statement = org_users(self.organization_id).where(USERS.c.id.in_(wanted))
        users = {
            user.user_id: user.model_dump(mode="json")
            for user in map(user_read, (await self.session.execute(statement)).tuples())
        }
        multiple = {field.name: field.multiple for field in fields}
        for name, user_ids in user_ids_by_field.items():
            if any(user_id not in users for user_id in user_ids):
                errors.append(
                    FormFieldError(
                        field=name,
                        code=FormFieldErrorCode.INVALID_USER,
                        message="This person is not a member of the organization",
                    )
                )
                continue
            expanded = [users[user_id] for user_id in user_ids]
            answers[name] = expanded if multiple[name] else expanded[0]
        return errors

    async def _claim_files(
        self, fields: Sequence[FormFieldBlock], answers: dict[str, Any]
    ) -> list[FormFieldError]:
        """Replace the file IDs of ``file`` fields with file objects, in place.

        File uploads are not built yet. This is the seam: until the upload
        milestone replaces this method, a submission that shows a ``file``
        field is rejected, whether or not a file was sent.
        """
        return [
            FormFieldError(
                field=field.name,
                code=FormFieldErrorCode.FILE_UPLOADS_UNAVAILABLE,
                message="File uploads are not available yet",
            )
            for field in fields
            if field.widget is FormWidget.FILE
        ]

    async def _autofill(
        self,
        autofill: Mapping[str, FormAutofillSource],
        supplied: Mapping[str, Any],
        submission_id: uuid.UUID,
    ) -> dict[str, Any]:
        """Work out the values of autofilled inputs.

        A user's values come from their user row. A service account has none,
        so it gets the value it supplied, or null.
        """
        if not autofill:
            return {}
        requester: FormUserRead | None = None
        if self._user_id is not None:
            statement = org_users(self.organization_id).where(
                USERS.c.id == self._user_id
            )
            row = (await self.session.execute(statement)).tuples().one_or_none()
            requester = user_read(row) if row is not None else None

        values: dict[str, Any] = {}
        for name, source in autofill.items():
            if source is FormAutofillSource.SUBMISSION_ID:
                values[name] = str(submission_id)
            elif requester is not None:
                values[name] = _requester_value(source, requester)
            else:
                values[name] = supplied.get(name)
        return values

    # ----- Read and delete -----

    def _submissions(self, form: Form) -> Select[Any]:
        return (
            select(
                FormSubmission.id,
                FormSubmission.form_id,
                FormSubmission.answers,
                FormSubmission.definition_version,
                FormSubmission.wf_exec_id,
                FormSubmission.created_at,
                FormSubmission.submitted_by_user_id,
                USERS.c.email,
                USERS.c.first_name,
                USERS.c.last_name,
                FormSubmission.submitted_by_service_account_id,
                ServiceAccount.name,
            )
            .outerjoin(USERS, USERS.c.id == FormSubmission.submitted_by_user_id)
            .outerjoin(
                ServiceAccount,
                ServiceAccount.id == FormSubmission.submitted_by_service_account_id,
            )
            .where(
                FormSubmission.workspace_id == self.workspace_id,
                FormSubmission.form_id == form.id,
            )
        )

    @staticmethod
    def _submission_read(values: tuple[object, ...]) -> FormSubmissionRead:
        submission_id = cast(uuid.UUID, values[0])
        return FormSubmissionRead(
            id=submission_id,
            form_id=cast(uuid.UUID, values[1]),
            reference=submission_reference(submission_id),
            answers=cast(dict[str, Any], values[2]),
            definition_version=cast(int, values[3]),
            wf_exec_id=cast(str, values[4]),
            submitted_at=cast(datetime, values[5]),
            submitted_by=actor_read(
                cast(uuid.UUID | None, values[6]),
                cast(str | None, values[7]),
                cast(str | None, values[8]),
                cast(str | None, values[9]),
                cast(uuid.UUID | None, values[10]),
                cast(str | None, values[11]),
            ),
        )

    async def _with_run_status(
        self, submissions: Sequence[FormSubmissionRead]
    ) -> list[FormSubmissionRead]:
        """Attach run statuses with one Temporal visibility query. Best effort."""
        wf_exec_ids = [s.wf_exec_id for s in submissions if s.wf_exec_id is not None]
        if not wf_exec_ids:
            return list(submissions)
        # The IDs are generated by the server, never taken from a request.
        id_list = ", ".join(f"'{wf_exec_id}'" for wf_exec_id in wf_exec_ids)
        try:
            executions = await WorkflowExecutionsService.connect(role=self.role)
            found = await executions.query_executions(
                query=f"WorkflowId IN ({id_list})", limit=len(wf_exec_ids)
            )
        except Exception as e:
            self.logger.warning(
                "Failed to look up run status for form submissions",
                error_type=type(e).__name__,
            )
            return list(submissions)
        statuses = {
            execution.id: cast(WorkflowExecutionStatusLiteral, execution.status.name)
            for execution in found
            if execution.status is not None
        }
        return [
            submission.model_copy(
                update={"run_status": statuses.get(submission.wf_exec_id or "")}
            )
            for submission in submissions
        ]

    async def list_submissions(
        self,
        form: Form,
        page: PageParams,
        *,
        submitted_after: datetime | None = None,
        submitted_before: datetime | None = None,
    ) -> CursorPaginatedResponse[FormSubmissionRead]:
        """List a form's submissions newest first, with answers and run status."""
        statement = self._submissions(form)
        if submitted_after is not None:
            statement = statement.where(FormSubmission.created_at >= submitted_after)
        if submitted_before is not None:
            statement = statement.where(FormSubmission.created_at < submitted_before)
        result = await paginate(
            self.session,
            statement,
            page=page,
            order_by=(
                FormSubmission.created_at.desc(),
                FormSubmission.surrogate_id.desc(),
            ),
            row_factory=self._submission_read,
        )
        response = page_response(result)
        return response.model_copy(
            update={"items": await self._with_run_status(response.items)}
        )

    async def read_submission(
        self, form: Form, submission_id: uuid.UUID
    ) -> FormSubmissionRead | None:
        """Read one submission.

        A caller reads their own submissions. Reading anyone else's, and the
        run fields of any, needs ``form:submission:read``.

        Returns:
            The submission, or None if it does not exist or is not the
            caller's to read.
        """
        statement = self._submissions(form).where(FormSubmission.id == submission_id)
        row = (await self.session.execute(statement)).tuples().one_or_none()
        if row is None:
            return None
        submission = self._submission_read(row)
        if self._has_scope("form:submission:read"):
            [submission] = await self._with_run_status([submission])
            return submission
        if not self._is_own(submission):
            return None
        # Requesters never see the run.
        return submission.model_copy(update={"wf_exec_id": None, "run_status": None})

    def _is_own(self, submission: FormSubmissionRead) -> bool:
        submitter = submission.submitted_by
        if submitter is None:
            return False
        if submitter.type == "user":
            return self._user_id is not None and submitter.id == self._user_id
        return submitter.id == self.role.service_account_id

    async def get_form_of_submission(self, submission_id: uuid.UUID) -> Form | None:
        """Find the form a submission belongs to, for callers with only its ID."""
        statement = (
            select(Form)
            .join(FormSubmission, FormSubmission.form_id == Form.id)
            .where(
                FormSubmission.workspace_id == self.workspace_id,
                FormSubmission.id == submission_id,
            )
        )
        return (await self.session.execute(statement)).scalar_one_or_none()

    async def get_submission(
        self, form: Form, submission_id: uuid.UUID
    ) -> FormSubmission | None:
        statement = select(FormSubmission).where(
            FormSubmission.workspace_id == self.workspace_id,
            FormSubmission.form_id == form.id,
            FormSubmission.id == submission_id,
        )
        return (await self.session.execute(statement)).scalar_one_or_none()

    @audit_log(resource_type="form_submission", action="delete")
    async def delete_submission(self, submission: FormSubmission) -> None:
        """Delete a submission. Its activity events stay, without the link."""
        await self.session.delete(submission)
        await self.session.commit()
