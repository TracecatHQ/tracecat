"""Submitting a form and reading its submissions."""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any, cast

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.sql.selectable import Select
from temporalio.service import RPCError, RPCStatusCode

from tracecat.audit.logger import audit_log
from tracecat.auth.types import Role
from tracecat.authz.scopes import SERVICE_PRINCIPAL_SCOPES
from tracecat.db.models import (
    Form,
    FormFile,
    FormSubmission,
    ServiceAccount,
    WorkflowDefinition,
)
from tracecat.dsl.common import DSLInput
from tracecat.dsl.validation import normalize_trigger_inputs
from tracecat.expressions.schemas import ExpectedField
from tracecat.forms.access import caller_user_id, can_read_submissions, is_caller
from tracecat.forms.enums import (
    FormAutofillSource,
    FormErrorCode,
    FormFieldErrorCode,
    FormWidget,
)
from tracecat.forms.exceptions import FormError
from tracecat.forms.files import FormFilesService
from tracecat.forms.schemas import (
    FormActorRead,
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
    config_version,
    org_users,
    paginate_rows,
)
from tracecat.forms.storage import delete_file_blobs, file_blob_keys
from tracecat.forms.values import check_values
from tracecat.identifiers import InternalServiceID
from tracecat.identifiers.workflow import WorkflowUUID, generate_exec_id
from tracecat.pagination import CursorPaginatedResponse, PageParams
from tracecat.registry.lock.types import RegistryLock
from tracecat.service import BaseWorkspaceService
from tracecat.workflow.executions.enums import (
    TriggerType,
    WorkflowExecutionStatusLiteral,
)
from tracecat.workflow.executions.service import WorkflowExecutionsService

_RUN_SERVICE_ID: InternalServiceID = "tracecat-runner"

_UNKNOWN_OUTCOME_RPC_STATUSES = frozenset(
    {
        RPCStatusCode.DEADLINE_EXCEEDED,
        RPCStatusCode.CANCELLED,
        RPCStatusCode.UNAVAILABLE,
        RPCStatusCode.UNKNOWN,
    }
)


def _run_may_have_started(error: Exception) -> bool:
    """Whether a failed start request may still have reached Temporal."""
    if isinstance(error, TimeoutError):
        return True
    return isinstance(error, RPCError) and error.status in _UNKNOWN_OUTCOME_RPC_STATUSES


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


def _typed_controllers(
    fields: Sequence[FormFieldBlock],
    expects: Mapping[str, ExpectedField],
    values: Mapping[str, Any],
) -> dict[str, Any]:
    """Read the answers that a ``visible_if`` compares as their input's type.

    An API caller can send ``"1"`` for an ``int`` input, and the run receives
    ``1``, so visibility must compare ``1`` too. An answer that does not
    convert is left as sent, for the checks that follow to report.
    """
    typed = dict(values)
    controllers = {field.visible_if.field for field in fields if field.visible_if}
    for name in controllers & typed.keys() & expects.keys():
        try:
            normalized = normalize_trigger_inputs(
                {name: expects[name]}, {name: typed[name]}
            )
        except ValidationError:
            continue
        typed[name] = normalized[name]
    return typed


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


def _submission_read(row: Mapping[Any, Any]) -> FormSubmissionRead:
    """Build a submission from a row of ``_submissions``."""
    submitted_by = None
    if (user_id := row["submitted_by_user_id"]) is not None:
        submitted_by = FormActorRead(
            type="user",
            id=user_id,
            email=row["email"],
            first_name=row["first_name"],
            last_name=row["last_name"],
        )
    elif (account_id := row["submitted_by_service_account_id"]) is not None:
        submitted_by = FormActorRead(
            type="service_account", id=account_id, name=row["name"]
        )
    return FormSubmissionRead.model_validate({**row, "submitted_by": submitted_by})


class FormSubmissionsService(BaseWorkspaceService):
    """Submit forms and manage what was submitted."""

    service_name = "form_submissions"

    # ----- Submit -----

    async def submit(
        self, form: Form, params: FormSubmissionCreate
    ) -> FormSubmissionReceipt:
        """Validate a submission, store it and start the workflow.

        The submission is committed before the run starts, so the run's first
        action can already read it. If the start definitely failed, the
        submission is deleted again. If the outcome is not known, such as a
        timeout, it is kept: the run may exist.

        Raises:
            FormError: If the form cannot be filled in or changed since it was
                opened, an answer is not valid, or the run did not start.
        """
        # Read before any rollback: a rollback expires the row.
        form_id = form.id
        workflow_id = WorkflowUUID.new(form.workflow_id)

        forms = FormsService(self.session, self.role)
        inputs, form_config, resolved = await forms.resolve_published(form)
        # resolve_published only returns the inputs of a published definition.
        definition = cast(WorkflowDefinition, inputs.definition)
        # Either change can hide a field the requester answered, whose answer
        # would then be dropped without an error.
        if params.definition_version not in (
            None,
            definition.version,
        ) or params.config_version not in (None, config_version(form_config)):
            raise FormError(
                FormErrorCode.FORM_CHANGED,
                "This form changed since you opened it. Reload and try again.",
            )

        # Autofilled inputs are never taken from the caller, so a requester
        # value is a verified Tracecat user or null. Sent, they are unknown keys.
        values = _typed_controllers(resolved.fields, inputs.expects, params.values)
        checked = check_values(resolved.blocks, values)
        answers = checked.values
        file_errors, claimed_files = await FormFilesService(
            self.session, self.role
        ).claim(form_id, checked.visible, answers)
        errors = [
            *checked.errors,
            *await self._expand_users(checked.visible, answers),
            *file_errors,
        ]
        if errors:
            raise FormError(
                FormErrorCode.FORM_VALIDATION_ERROR,
                "Some answers are not valid",
                errors=errors,
            )

        submission_id = uuid.uuid4()
        answers |= await self._autofill(resolved.autofill, submission_id)
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
            submitted_by_user_id=caller_user_id(self.role),
            submitted_by_service_account_id=self.role.service_account_id,
            answers=normalized,
            visible_fields=[field.name for field in checked.visible],
            definition_version=definition.version,
            wf_exec_id=wf_exec_id,
        )
        self.session.add(submission)
        await self.session.flush()
        for file in claimed_files:
            file.submission_id = submission_id
        await self.session.commit()
        await self.session.refresh(submission)
        submitted_at = submission.created_at

        executions: WorkflowExecutionsService | None = None
        try:
            executions = await WorkflowExecutionsService.connect(role=self.run_role())
            await executions.create_workflow_execution_wait_for_start(
                dsl=cast(DSLInput, inputs.dsl),
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
            # A failed connect never reached Temporal.
            kept = executions is not None and _run_may_have_started(e)
            if not kept:
                await self._discard(submission, claimed_files)
            # Never log the answers.
            self.logger.error(
                "Failed to start the workflow of a form submission",
                form_id=form_id,
                wf_exec_id=wf_exec_id,
                error_type=type(e).__name__,
                submission_kept=kept,
            )
            raise FormError(
                FormErrorCode.WORKFLOW_START_FAILED,
                "The request could not be started. Try again.",
            ) from e

        return FormSubmissionReceipt(
            id=submission_id,
            submitted_at=submitted_at,
            success_message=form_config.success_message,
        )

    async def _discard(
        self, submission: FormSubmission, claimed_files: Sequence[FormFile]
    ) -> None:
        """Undo a committed submission whose run never started. Best effort.

        The files go back to pending so the requester can submit again.
        Deleting the submission first would delete them with it.
        """
        submission_id = submission.id
        try:
            for file in claimed_files:
                file.submission_id = None
            await self.session.flush()
            await self.session.delete(submission)
            await self.session.commit()
        except SQLAlchemyError as e:
            await self.session.rollback()
            self.logger.error(
                "Failed to remove a form submission whose run did not start",
                submission_id=submission_id,
                error_type=type(e).__name__,
            )

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
            user_id=caller_user_id(self.role),
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

        wanted = {user_id for ids in user_ids_by_field.values() for user_id in ids}
        statement = org_users(self.organization_id).where(USERS.c.id.in_(wanted))
        users = {
            user.user_id: user.model_dump(mode="json")
            for user in map(
                FormUserRead.model_validate,
                (await self.session.execute(statement)).mappings(),
            )
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

    async def _autofill(
        self,
        autofill: Mapping[str, FormAutofillSource],
        submission_id: uuid.UUID,
    ) -> dict[str, Any]:
        """Work out the values of autofilled inputs.

        A user's values come from their user row. A service account has no
        user, so its requester values are null.
        """
        if not autofill:
            return {}
        requester: FormUserRead | None = None
        if (user_id := caller_user_id(self.role)) is not None:
            statement = org_users(self.organization_id).where(USERS.c.id == user_id)
            if row := (await self.session.execute(statement)).mappings().first():
                requester = FormUserRead.model_validate(row)
        values: dict[str, Any] = {}
        for name, source in autofill.items():
            if source is FormAutofillSource.SUBMISSION_ID:
                values[name] = str(submission_id)
            elif requester is not None:
                values[name] = _requester_value(source, requester)
            else:
                values[name] = None
        return values

    # ----- Read and delete -----

    def _submissions(self, form_id: uuid.UUID) -> Select[Any]:
        return (
            select(
                FormSubmission.id,
                FormSubmission.form_id,
                FormSubmission.answers,
                FormSubmission.definition_version,
                FormSubmission.wf_exec_id,
                FormSubmission.created_at.label("submitted_at"),
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
                FormSubmission.form_id == form_id,
            )
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
        statement = self._submissions(form.id)
        if submitted_after is not None:
            statement = statement.where(FormSubmission.created_at >= submitted_after)
        if submitted_before is not None:
            statement = statement.where(FormSubmission.created_at < submitted_before)
        response = await paginate_rows(
            self.session,
            statement,
            _submission_read,
            page,
            FormSubmission.created_at.desc(),
            FormSubmission.surrogate_id.desc(),
        )
        response.items = await self._with_run_status(response.items)
        return response

    async def read_submission(
        self, form: Form, submission_id: uuid.UUID
    ) -> FormSubmissionRead | None:
        """Read one submission, or None if it is not the caller's to read.

        A caller reads their own submissions. Reading anyone else's, and the
        run fields, the workflow version and the answers to fields the caller
        did not see when submitting, needs ``form:submission:read``.
        """
        statement = (
            self._submissions(form.id)
            .add_columns(FormSubmission.visible_fields)
            .where(FormSubmission.id == submission_id)
        )
        reader = can_read_submissions(self.role)
        if not reader:
            statement = statement.where(
                is_caller(
                    self.role,
                    FormSubmission.submitted_by_user_id,
                    FormSubmission.submitted_by_service_account_id,
                )
            )
        if (row := (await self.session.execute(statement)).mappings().first()) is None:
            return None
        submission = _submission_read(row)
        if reader:
            [submission] = await self._with_run_status([submission])
            return submission
        # Requesters never see the run, nor answers they were not shown, such
        # as what the form filled in for them. Later form changes do not count.
        visible = set(row["visible_fields"])
        return submission.model_copy(
            update={
                "answers": {
                    name: answer
                    for name, answer in submission.answers.items()
                    if name in visible
                },
                "definition_version": None,
                "wf_exec_id": None,
            }
        )

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
        """Delete a submission and its files. The run it started is unaffected.

        The file rows go with the submission; their blobs are deleted after
        the commit, best effort.
        """
        keys = await file_blob_keys(
            self.session, self.workspace_id, FormFile.submission_id == submission.id
        )
        await self.session.delete(submission)
        await self.session.commit()
        await delete_file_blobs(keys)
