from __future__ import annotations

import asyncio
from collections.abc import Generator
from contextlib import contextmanager
from typing import Any

from pydantic import BaseModel
from sqlalchemy.exc import MultipleResultsFound
from sqlmodel import Session, select
from temporalio import activity

from tracecat import identifiers
from tracecat.contexts import RunContext, ctx_role, ctx_run
from tracecat.db.engine import create_db_engine
from tracecat.db.schemas import Workflow, WorkflowDefinition
from tracecat.dsl.common import DSLInput, DSLRunArgs
from tracecat.dsl.models import ActionStatement
from tracecat.logging import logger
from tracecat.types.auth import Role
from tracecat.types.exceptions import TracecatException


class WorkflowDefinitionsService:
    def __init__(self, session: Session, role: Role | None = None):
        self.role = role or ctx_role.get()
        self._session = session
        self.logger = logger.bind(service="workflow_definitions")

    @contextmanager
    @staticmethod
    def get_session(role: Role) -> Generator[WorkflowDefinitionsService, None, None]:
        engine = create_db_engine()
        with Session(engine) as session:
            yield WorkflowDefinitionsService(session, role)

    def get_definition_by_workflow_id(
        self, workflow_id: identifiers.WorkflowID, *, version: int | None = None
    ) -> WorkflowDefinition | None:
        statement = select(WorkflowDefinition).where(
            WorkflowDefinition.owner_id == self.role.user_id,
            WorkflowDefinition.workflow_id == workflow_id,
        )
        if version:
            statement = statement.where(WorkflowDefinition.version == version)
        else:
            # Get the latest version
            statement = statement.order_by(WorkflowDefinition.version.desc())

        return self._session.exec(statement).first()

    def get_definition_by_workflow_title(
        self, workflow_title: str, *, version: int | None = None
    ) -> WorkflowDefinition | None:
        self.logger.warning(
            "Getting workflow definition by ref",
            workflow_title=workflow_title,
            role=self.role,
        )
        wf_statement = select(Workflow.id).where(
            Workflow.owner_id == self.role.user_id,
            Workflow.title == workflow_title,
        )

        try:
            wf_id = self._session.exec(wf_statement).one_or_none()
        except MultipleResultsFound as e:
            self.logger.error(
                "Multiple workflows found with the same title. Please ensure that the workflow title is unique.",
                workflow_title=workflow_title,
            )
            raise e
        self.logger.warning("Workflow ID", wf_id=wf_id)
        if not wf_id:
            self.logger.error("Workflow name not found", workflow_title=workflow_title)
            return None

        wf_defn_statement = select(WorkflowDefinition).where(
            WorkflowDefinition.owner_id == self.role.user_id,
            WorkflowDefinition.workflow_id == wf_id,
        )

        if version:
            wf_defn_statement = wf_defn_statement.where(
                WorkflowDefinition.version == version
            )
        else:
            # Get the latest version
            wf_defn_statement = wf_defn_statement.order_by(
                WorkflowDefinition.version.desc()
            )

        return self._session.exec(wf_defn_statement).first()


class GetWorkflowDefinitionActivityInputs(BaseModel):
    role: Role
    task: ActionStatement
    workflow_title: str
    trigger_inputs: dict[str, Any]
    version: int | None = None
    run_context: RunContext


@activity.defn
async def get_workflow_definition_activity(
    input: GetWorkflowDefinitionActivityInputs,
) -> DSLRunArgs:
    def _get_definition() -> WorkflowDefinition | None:
        with WorkflowDefinitionsService.get_session(role=input.role) as service:
            return service.get_definition_by_workflow_title(
                input.workflow_title, version=input.version
            )

    logger.trace("Getting workflow definition", workflow_title=input.workflow_title)
    defn = await asyncio.to_thread(_get_definition)
    if not defn:
        raise TracecatException(
            f"Workflow definition not found. {input.workflow_title!r}, version={input.version}"
        )
    dsl = DSLInput(**defn.content)
    parent_run_context = ctx_run.get()
    return DSLRunArgs(
        role=input.role,
        dsl=dsl,
        wf_id=defn.workflow_id,
        parent_run_context=parent_run_context,
        trigger_inputs=input.trigger_inputs,
    )
