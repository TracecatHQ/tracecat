"""Tests for resolving which workflows run each agent preset."""

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.agent.preset.schemas import AgentPresetCreate
from tracecat.agent.preset.service import AgentPresetService
from tracecat.agent.preset.workflow_references import (
    AgentPresetWorkflowReferenceService,
    _literal_preset_slug,
)
from tracecat.auth.types import Role
from tracecat.db.models import Action, Workflow, Workspace
from tracecat.identifiers.workflow import WorkflowUUID
from tracecat.pagination import PageParams, PaginationError

pytestmark = pytest.mark.usefixtures("db")


def _preset_params(name: str) -> AgentPresetCreate:
    return AgentPresetCreate(
        name=name,
        model_name="gpt-4o-mini",
        model_provider="openai",
    )


async def _add_workflow(
    session: AsyncSession,
    workspace: Workspace,
    title: str,
    actions: list[tuple[str, str]],
) -> Workflow:
    workflow = Workflow(
        workspace_id=workspace.id,
        title=title,
        description="",
        status="offline",
    )
    session.add(workflow)
    await session.flush()
    session.add_all(
        Action(
            workspace_id=workspace.id,
            workflow_id=workflow.id,
            type=action_type,
            title=f"{action_type} {index}",
            description="",
            inputs=inputs,
        )
        for index, (action_type, inputs) in enumerate(actions)
    )
    await session.flush()
    return workflow


async def _all_references(
    service: AgentPresetWorkflowReferenceService, limit: int
) -> tuple[set[tuple[uuid.UUID, str, str]], int]:
    """Walk every page; return distinct (preset, workflow, title) and page count."""
    references: set[tuple[uuid.UUID, str, str]] = set()
    cursor: str | None = None
    pages = 0
    while True:
        page = await service.list_references(PageParams(limit=limit, cursor=cursor))
        pages += 1
        references.update(
            (ref.preset_id, ref.workflow_id, ref.workflow_title) for ref in page.items
        )
        if not page.has_more:
            assert page.next_cursor is None
            return references, pages
        cursor = page.next_cursor


@pytest.mark.parametrize(
    ("inputs", "expected"),
    [
        ("preset: triage-agent\nuser_prompt: hi", "triage-agent"),
        ("preset: ${{ TRIGGER.preset }}", None),
        ("preset: ''", None),
        ("preset: 3", None),
        ("user_prompt: hi", None),
        ("", None),
        ("preset: [unclosed", None),
        ("preset: 2026-01-01", "2026-01-01"),
        ("since: 2024-02-30\npreset: triage-agent", "triage-agent"),
        ("preset: !!timestamp 2024-02-30", None),
        ("preset: !!int abc", None),
        ("preset: '  '", None),
        ("preset: !!bool abc", None),
        ("preset: !!timestamp abc", None),
        ("preset: x\nk: " + "[" * 30_000 + "]" * 30_000, None),
    ],
    ids=lambda value: value[:40] if isinstance(value, str) else None,
)
def test_literal_preset_slug(inputs: str, expected: str | None) -> None:
    assert _literal_preset_slug(inputs) == expected


@pytest.mark.anyio
async def test_list_workflow_references_resolves_literal_slugs(
    session: AsyncSession, svc_role: Role, svc_workspace: Workspace
) -> None:
    service = AgentPresetService(session=session, role=svc_role)
    triage = await service.create_preset(_preset_params("Triage agent"))
    enrich = await service.create_preset(_preset_params("Enrich agent"))
    deleted = await service.create_preset(_preset_params("Deleted agent"))
    deleted.deleted_at = datetime.now(UTC)
    await session.flush()

    beta = await _add_workflow(
        session,
        svc_workspace,
        "Beta",
        [
            ("ai.preset_agent", f"preset: {triage.slug}"),
            ("ai.preset_agent", f"preset: {triage.slug}\nuser_prompt: again"),
            ("ai.preset_agent", f"preset: {enrich.slug}"),
        ],
    )
    alpha = await _add_workflow(
        session,
        svc_workspace,
        "Alpha",
        [("ai.preset_agent", f"preset: {triage.slug}")],
    )
    await _add_workflow(
        session,
        svc_workspace,
        "Ignored",
        [
            ("ai.preset_agent", "preset: ${{ TRIGGER.preset }}"),
            ("ai.preset_agent", "preset: unknown-agent"),
            ("ai.preset_agent", f"preset: {deleted.slug}"),
            ("core.http_request", f"preset: {enrich.slug}"),
        ],
    )

    # Seven `ai.preset_agent` actions, one scanned per page.
    references_service = AgentPresetWorkflowReferenceService(
        session=session, role=svc_role
    )
    references, pages = await _all_references(references_service, limit=1)

    assert pages == 7
    assert references == {
        (triage.id, WorkflowUUID.new(alpha.id).short(), "Alpha"),
        (triage.id, WorkflowUUID.new(beta.id).short(), "Beta"),
        (enrich.id, WorkflowUUID.new(beta.id).short(), "Beta"),
    }
    assert await _all_references(references_service, limit=200) == (references, 1)


@pytest.mark.anyio
async def test_list_workflow_references_empty_without_preset_actions(
    session: AsyncSession, svc_role: Role, svc_workspace: Workspace
) -> None:
    service = AgentPresetService(session=session, role=svc_role)
    await service.create_preset(_preset_params("Unused agent"))
    await _add_workflow(
        session, svc_workspace, "Plain", [("core.http_request", "url: x")]
    )

    page = await AgentPresetWorkflowReferenceService(
        session=session, role=svc_role
    ).list_references(PageParams())
    assert page.items == []
    assert page.has_more is False


@pytest.mark.anyio
async def test_list_workflow_references_skips_other_workspace_workflows(
    session: AsyncSession, svc_role: Role, svc_workspace: Workspace
) -> None:
    preset = await AgentPresetService(session=session, role=svc_role).create_preset(
        _preset_params("Triage agent")
    )
    other_workspace = Workspace(
        name="Other workspace", organization_id=svc_workspace.organization_id
    )
    session.add(other_workspace)
    await session.flush()
    foreign = await _add_workflow(session, other_workspace, "Foreign", [])
    session.add(
        Action(
            workspace_id=svc_workspace.id,
            workflow_id=foreign.id,
            type="ai.preset_agent",
            title="Cross-workspace action",
            description="",
            inputs=f"preset: {preset.slug}",
        )
    )
    await session.flush()

    page = await AgentPresetWorkflowReferenceService(
        session=session, role=svc_role
    ).list_references(PageParams())
    assert page.items == []


@pytest.mark.anyio
async def test_list_workflow_references_rejects_invalid_cursor(
    session: AsyncSession, svc_role: Role
) -> None:
    service = AgentPresetWorkflowReferenceService(session=session, role=svc_role)
    with pytest.raises(PaginationError):
        await service.list_references(PageParams(cursor="not-a-cursor"))
