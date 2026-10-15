"""Resolve which workflows run each agent preset."""

import uuid
from typing import cast

import yaml
from sqlalchemy import select

from tracecat.agent.preset.schemas import AgentPresetWorkflowReference
from tracecat.db.models import Action, AgentPreset, Workflow
from tracecat.dsl.enums import PlatformAction
from tracecat.expressions import patterns
from tracecat.identifiers.workflow import WorkflowUUID
from tracecat.pagination import CursorPaginatedResponse, PageParams, paginate
from tracecat.service import BaseWorkspaceService

_YAML_TIMESTAMP_TAG = "tag:yaml.org,2002:timestamp"


# Pure-Python loader: libyaml segfaults on deeply nested input, which no
# except clause can catch.
class _ActionInputsLoader(yaml.SafeLoader):
    """Safe loader that keeps date-like scalars as strings."""


_ActionInputsLoader.yaml_implicit_resolvers = {
    char: [resolver for resolver in resolvers if resolver[0] != _YAML_TIMESTAMP_TAG]
    for char, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}


def _literal_preset_slug(inputs: str) -> str | None:
    """Return the literal `preset` slug from draft `ai.preset_agent` inputs."""
    try:
        parsed = yaml.load(inputs, Loader=_ActionInputsLoader)
    except Exception:
        # Drafts are stored unvalidated; bad tags and deep nesting raise
        # non-YAML errors, and one bad draft must not fail the page.
        return None
    match parsed:
        case {"preset": str(slug)} if (
            slug.strip() and not patterns.TEMPLATE_STRING.search(slug)
        ):
            return slug
        case _:
            return None


def _slug_from_row(values: tuple[object, ...]) -> tuple[str | None, uuid.UUID, str]:
    """Parse one `(inputs, workflow_id, workflow_title)` action row."""
    inputs, workflow_id, workflow_title = values
    return (
        _literal_preset_slug(cast(str, inputs)),
        cast(uuid.UUID, workflow_id),
        cast(str, workflow_title),
    )


class AgentPresetWorkflowReferenceService(BaseWorkspaceService):
    """Find the workflows whose drafts run each agent preset."""

    service_name = "agent_preset_workflow_references"

    async def list_references(
        self, page: PageParams
    ) -> CursorPaginatedResponse[AgentPresetWorkflowReference]:
        """Page through the workflows whose drafts run a preset.

        Each page scans at most ``page.limit`` `ai.preset_agent` actions in
        ``Action.id`` order, so a page may hold fewer references than actions
        and the same pair may repeat across pages. Only literal `preset` slugs
        resolve; templated slugs are skipped.
        """
        statement = (
            select(Action.inputs, Workflow.id, Workflow.title)
            .join(Workflow, Action.workflow_id == Workflow.id)
            .where(
                Action.workspace_id == self.workspace_id,
                Action.type == PlatformAction.AI_PRESET_AGENT,
            )
        )
        actions = await paginate(
            self.session,
            statement,
            page=page,
            order_by=(Action.id.asc(),),
            row_factory=_slug_from_row,
        )

        workflows_by_slug: dict[str, dict[uuid.UUID, str]] = {}
        for slug, workflow_id, workflow_title in actions.items:
            if slug is not None:
                workflows_by_slug.setdefault(slug, {})[workflow_id] = workflow_title
        references: list[AgentPresetWorkflowReference] = []
        if workflows_by_slug:
            preset_rows = await self.session.execute(
                select(AgentPreset.id, AgentPreset.slug).where(
                    AgentPreset.workspace_id == self.workspace_id,
                    AgentPreset.deleted_at.is_(None),
                    AgentPreset.slug.in_(workflows_by_slug),
                )
            )
            references = [
                AgentPresetWorkflowReference(
                    preset_id=preset_id,
                    workflow_id=WorkflowUUID.new(workflow_id).short(),
                    workflow_title=workflow_title,
                )
                for preset_id, slug in preset_rows.tuples().all()
                for workflow_id, workflow_title in workflows_by_slug[slug].items()
            ]
        return CursorPaginatedResponse(
            items=references,
            next_cursor=actions.next_cursor,
            prev_cursor=actions.prev_cursor,
            has_more=actions.has_more,
            has_previous=actions.has_previous,
        )
