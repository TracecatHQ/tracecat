"""Workspace routes for event sources, triggers and trigger bindings.

``TriggerError`` becomes its HTTP response in ``trigger_error_handler``.
Nothing here has a delete for event sources: that would drop queued events.
"""

import uuid
from collections.abc import Awaitable
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status

from tracecat.auth.dependencies import WorkspaceActorRouteRole
from tracecat.authz.controls import check_scopes, require_scope
from tracecat.db.dependencies import AsyncDBSession
from tracecat.identifiers.workflow import OptionalAnyWorkflowIDQuery
from tracecat.pagination import CursorPaginatedResponse, PageParams, PaginationError
from tracecat.triggers.dependencies import require_triggers_enabled
from tracecat.triggers.enums import TriggerKind
from tracecat.triggers.schemas import (
    EventSourceCreate,
    EventSourceRead,
    EventSourceUpdate,
    TriggerBindingCreate,
    TriggerBindingRead,
    TriggerBindingUpdate,
    TriggerCreate,
    TriggerRead,
    TriggerUpdate,
)
from tracecat.triggers.service import TriggersService, read_event_source
from tracecat.triggers.types import EXECUTE_SCOPE_BY_TARGET

_GATES = [Depends(require_triggers_enabled)]

event_sources_router = APIRouter(
    prefix="/event-sources", tags=["triggers"], dependencies=_GATES
)
triggers_router = APIRouter(prefix="/triggers", tags=["triggers"], dependencies=_GATES)

PageQuery = Annotated[PageParams, Depends()]


def _errors(*codes: int) -> dict[int | str, dict[str, Any]]:
    return {code: {"description": "See detail.code"} for code in codes}


async def _paged[T](items: Awaitable[T]) -> T:
    """Answer a bad pagination cursor with 400."""
    try:
        return await items
    except PaginationError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=e.detail or str(e)
        ) from e


# ----- Event sources -----


@event_sources_router.get("")
@require_scope("trigger:read")
async def list_event_sources(
    *, role: WorkspaceActorRouteRole, session: AsyncDBSession, page: PageQuery
) -> CursorPaginatedResponse[EventSourceRead]:
    """List event sources, newest first."""
    return await _paged(TriggersService(session, role).list_event_sources(page))


@event_sources_router.post(
    "", status_code=status.HTTP_201_CREATED, responses=_errors(409)
)
@require_scope("trigger:create")
async def create_event_source(
    *, role: WorkspaceActorRouteRole, session: AsyncDBSession, params: EventSourceCreate
) -> EventSourceRead:
    """Create an event source. Its endpoint URL is fixed from now on."""
    source = await TriggersService(session, role).create_event_source(params)
    return read_event_source(source)


@event_sources_router.get("/{source_id}", responses=_errors(404))
@require_scope("trigger:read")
async def get_event_source(
    *, role: WorkspaceActorRouteRole, session: AsyncDBSession, source_id: uuid.UUID
) -> EventSourceRead:
    """Get an event source with its endpoint URL and subscription state."""
    source = await TriggersService(session, role).get_event_source(source_id)
    return read_event_source(source)


@event_sources_router.patch("/{source_id}", responses=_errors(400, 404, 409))
@require_scope("trigger:update")
async def update_event_source(
    *,
    role: WorkspaceActorRouteRole,
    session: AsyncDBSession,
    source_id: uuid.UUID,
    params: EventSourceUpdate,
) -> EventSourceRead:
    """Rename a source, change its feed, or take it online or offline."""
    service = TriggersService(session, role)
    source = await service.update_event_source(source_id, params)
    return read_event_source(source)


# ----- Triggers -----


@triggers_router.get("")
@require_scope("trigger:read")
async def list_triggers(
    *,
    role: WorkspaceActorRouteRole,
    session: AsyncDBSession,
    page: PageQuery,
    workflow_id: OptionalAnyWorkflowIDQuery,
    kind: TriggerKind | None = Query(default=None),
    source_id: uuid.UUID | None = Query(default=None),
    agent_preset_id: uuid.UUID | None = Query(default=None),
) -> CursorPaginatedResponse[TriggerRead]:
    """List triggers newest first, each with its config and bindings.

    ``workflow_id`` or ``agent_preset_id`` keeps the triggers bound to that
    target.
    """
    service = TriggersService(session, role)
    return await _paged(
        service.list_triggers(
            page,
            kind=kind,
            source_id=source_id,
            workflow_id=workflow_id,
            agent_preset_id=agent_preset_id,
        )
    )


@triggers_router.post(
    "", status_code=status.HTTP_201_CREATED, responses=_errors(400, 404)
)
@require_scope("trigger:create")
async def create_trigger(
    *, role: WorkspaceActorRouteRole, session: AsyncDBSession, params: TriggerCreate
) -> TriggerRead:
    """Create a trigger. It has no bindings yet."""
    service = TriggersService(session, role)
    trigger = await service.create_trigger(params)
    return await service.get_trigger(trigger.id)


@triggers_router.get("/{trigger_id}", responses=_errors(404))
@require_scope("trigger:read")
async def get_trigger(
    *, role: WorkspaceActorRouteRole, session: AsyncDBSession, trigger_id: uuid.UUID
) -> TriggerRead:
    """Get a trigger with its config and bindings."""
    return await TriggersService(session, role).get_trigger(trigger_id)


@triggers_router.patch("/{trigger_id}", responses=_errors(400, 404))
@require_scope("trigger:update")
async def update_trigger(
    *,
    role: WorkspaceActorRouteRole,
    session: AsyncDBSession,
    trigger_id: uuid.UUID,
    params: TriggerUpdate,
) -> TriggerRead:
    """Rename a trigger, replace its config, or take it online or offline."""
    service = TriggersService(session, role)
    await service.update_trigger(trigger_id, params)
    return await service.get_trigger(trigger_id)


@triggers_router.delete(
    "/{trigger_id}", status_code=status.HTTP_204_NO_CONTENT, responses=_errors(404)
)
@require_scope("trigger:delete")
async def delete_trigger(
    *, role: WorkspaceActorRouteRole, session: AsyncDBSession, trigger_id: uuid.UUID
) -> None:
    """Delete a trigger and its bindings."""
    await TriggersService(session, role).delete_trigger(trigger_id)


# ----- Bindings -----


@triggers_router.post(
    "/{trigger_id}/bindings",
    status_code=status.HTTP_201_CREATED,
    responses=_errors(404, 409),
)
@require_scope("trigger:update")
async def create_trigger_binding(
    *,
    role: WorkspaceActorRouteRole,
    session: AsyncDBSession,
    trigger_id: uuid.UUID,
    params: TriggerBindingCreate,
) -> TriggerBindingRead:
    """Bind a trigger to a workflow or an agent preset.

    Also needs ``workflow:execute`` for a workflow or ``agent:execute`` for an
    agent: every matching event will run the target.
    """
    check_scopes(role, EXECUTE_SCOPE_BY_TARGET[params.target.type])
    service = TriggersService(session, role)
    binding = await service.create_binding(trigger_id, params)
    return await service.read_binding(binding)


@triggers_router.patch(
    "/{trigger_id}/bindings/{binding_id}", responses=_errors(400, 404)
)
@require_scope("trigger:update")
async def update_trigger_binding(
    *,
    role: WorkspaceActorRouteRole,
    session: AsyncDBSession,
    trigger_id: uuid.UUID,
    binding_id: uuid.UUID,
    params: TriggerBindingUpdate,
) -> TriggerBindingRead:
    """Take a binding online or offline, or change an agent's instruction."""
    service = TriggersService(session, role)
    binding = await service.update_binding(trigger_id, binding_id, params)
    return await service.read_binding(binding)


@triggers_router.delete(
    "/{trigger_id}/bindings/{binding_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=_errors(404),
)
@require_scope("trigger:delete")
async def delete_trigger_binding(
    *,
    role: WorkspaceActorRouteRole,
    session: AsyncDBSession,
    trigger_id: uuid.UUID,
    binding_id: uuid.UUID,
) -> None:
    """Delete one binding of a trigger."""
    await TriggersService(session, role).delete_binding(trigger_id, binding_id)
