from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, status
from pydantic import UUID4
from sqlalchemy.exc import IntegrityError

from tracecat.auth.dependencies import WorkspaceUserRole
from tracecat.cases.dropdowns.schemas import (
    CaseDropdownDefinitionCreate,
    CaseDropdownDefinitionRead,
    CaseDropdownDefinitionUpdate,
    CaseDropdownOptionCreate,
    CaseDropdownOptionRead,
    CaseDropdownOptionUpdate,
    CaseDropdownValueRead,
    CaseDropdownValueSet,
)
from tracecat.cases.dropdowns.service import (
    CaseDropdownDefinitionsService,
    CaseDropdownValuesService,
)
from tracecat.db.dependencies import AsyncDBSession
from tracecat.exceptions import TracecatNotFoundError

# --- Definition CRUD router ---

definitions_router = APIRouter(prefix="/case-dropdowns", tags=["case-dropdowns"])


@definitions_router.get("", response_model=list[CaseDropdownDefinitionRead])
async def list_dropdown_definitions(
    *,
    role: WorkspaceUserRole,
    session: AsyncDBSession,
) -> list[CaseDropdownDefinitionRead]:
    """List all dropdown definitions for the workspace."""
    service = CaseDropdownDefinitionsService(session=session, role=role)
    definitions = await service.list_definitions()
    return [
        CaseDropdownDefinitionRead.model_validate(d, from_attributes=True)
        for d in definitions
    ]


@definitions_router.post(
    "",
    response_model=CaseDropdownDefinitionRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_dropdown_definition(
    *,
    role: WorkspaceUserRole,
    session: AsyncDBSession,
    params: CaseDropdownDefinitionCreate,
) -> CaseDropdownDefinitionRead:
    """Create a new dropdown definition with initial options."""
    service = CaseDropdownDefinitionsService(session=session, role=role)
    try:
        definition = await service.create_definition(params)
    except IntegrityError as err:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Dropdown definition with this ref already exists",
        ) from err
    return CaseDropdownDefinitionRead.model_validate(definition, from_attributes=True)


@definitions_router.get("/{definition_id}", response_model=CaseDropdownDefinitionRead)
async def get_dropdown_definition(
    *,
    role: WorkspaceUserRole,
    session: AsyncDBSession,
    definition_id: UUID4,
) -> CaseDropdownDefinitionRead:
    """Get a single dropdown definition."""
    service = CaseDropdownDefinitionsService(session=session, role=role)
    try:
        definition = await service.get_definition(definition_id)
    except TracecatNotFoundError as err:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(err)
        ) from err
    return CaseDropdownDefinitionRead.model_validate(definition, from_attributes=True)


@definitions_router.patch("/{definition_id}", response_model=CaseDropdownDefinitionRead)
async def update_dropdown_definition(
    *,
    role: WorkspaceUserRole,
    session: AsyncDBSession,
    definition_id: UUID4,
    params: CaseDropdownDefinitionUpdate,
) -> CaseDropdownDefinitionRead:
    """Update a dropdown definition."""
    service = CaseDropdownDefinitionsService(session=session, role=role)
    try:
        definition = await service.get_definition(definition_id)
    except TracecatNotFoundError as err:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(err)
        ) from err
    try:
        updated = await service.update_definition(definition, params)
    except IntegrityError as err:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Dropdown definition with this ref already exists",
        ) from err
    return CaseDropdownDefinitionRead.model_validate(updated, from_attributes=True)


@definitions_router.delete("/{definition_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_dropdown_definition(
    *,
    role: WorkspaceUserRole,
    session: AsyncDBSession,
    definition_id: UUID4,
) -> None:
    """Delete a dropdown definition and all its options/values."""
    service = CaseDropdownDefinitionsService(session=session, role=role)
    try:
        definition = await service.get_definition(definition_id)
    except TracecatNotFoundError as err:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(err)
        ) from err
    await service.delete_definition(definition)


# --- Option endpoints ---


@definitions_router.post(
    "/{definition_id}/options",
    response_model=CaseDropdownOptionRead,
    status_code=status.HTTP_201_CREATED,
)
async def add_dropdown_option(
    *,
    role: WorkspaceUserRole,
    session: AsyncDBSession,
    definition_id: UUID4,
    params: CaseDropdownOptionCreate,
) -> CaseDropdownOptionRead:
    """Add a new option to a dropdown definition."""
    service = CaseDropdownDefinitionsService(session=session, role=role)
    # Verify definition exists
    try:
        await service.get_definition(definition_id)
    except TracecatNotFoundError as err:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(err)
        ) from err
    try:
        option = await service.add_option(definition_id, params)
    except IntegrityError as err:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Option with this ref already exists for this dropdown",
        ) from err
    return CaseDropdownOptionRead.model_validate(option, from_attributes=True)


@definitions_router.patch(
    "/{definition_id}/options/{option_id}",
    response_model=CaseDropdownOptionRead,
)
async def update_dropdown_option(
    *,
    role: WorkspaceUserRole,
    session: AsyncDBSession,
    definition_id: UUID4,
    option_id: UUID4,
    params: CaseDropdownOptionUpdate,
) -> CaseDropdownOptionRead:
    """Update an option within a dropdown definition."""
    service = CaseDropdownDefinitionsService(session=session, role=role)
    # Verify definition belongs to this workspace
    try:
        await service.get_definition(definition_id)
    except TracecatNotFoundError as err:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(err)
        ) from err
    try:
        option = await service.update_option(option_id, params)
    except TracecatNotFoundError as err:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(err)
        ) from err
    except IntegrityError as err:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Option with this ref already exists for this dropdown",
        ) from err
    return CaseDropdownOptionRead.model_validate(option, from_attributes=True)


@definitions_router.delete(
    "/{definition_id}/options/{option_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_dropdown_option(
    *,
    role: WorkspaceUserRole,
    session: AsyncDBSession,
    definition_id: UUID4,
    option_id: UUID4,
) -> None:
    """Delete an option from a dropdown definition."""
    service = CaseDropdownDefinitionsService(session=session, role=role)
    # Verify definition belongs to this workspace
    try:
        await service.get_definition(definition_id)
    except TracecatNotFoundError as err:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(err)
        ) from err
    try:
        await service.delete_option(option_id)
    except TracecatNotFoundError as err:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(err)
        ) from err


@definitions_router.put(
    "/{definition_id}/options/reorder",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def reorder_dropdown_options(
    *,
    role: WorkspaceUserRole,
    session: AsyncDBSession,
    definition_id: UUID4,
    option_ids: list[uuid.UUID],
) -> None:
    """Reorder options within a dropdown definition."""
    service = CaseDropdownDefinitionsService(session=session, role=role)
    await service.reorder_options(definition_id, option_ids)


# --- Per-case dropdown value endpoints (mounted on /cases) ---

values_router = APIRouter(prefix="/cases", tags=["cases"])


@values_router.get("/{case_id}/dropdowns", response_model=list[CaseDropdownValueRead])
async def list_case_dropdown_values(
    *,
    role: WorkspaceUserRole,
    session: AsyncDBSession,
    case_id: UUID4,
) -> list[CaseDropdownValueRead]:
    """List all dropdown values for a case."""
    service = CaseDropdownValuesService(session=session, role=role)
    return await service.list_values_for_case(case_id)


@values_router.put(
    "/{case_id}/dropdowns/{definition_id}",
    response_model=CaseDropdownValueRead,
)
async def set_case_dropdown_value(
    *,
    role: WorkspaceUserRole,
    session: AsyncDBSession,
    case_id: UUID4,
    definition_id: UUID4,
    params: CaseDropdownValueSet,
) -> CaseDropdownValueRead:
    """Set or clear a dropdown value for a case."""
    service = CaseDropdownValuesService(session=session, role=role)
    try:
        return await service.set_value(case_id, definition_id, params)
    except TracecatNotFoundError as err:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(err)
        ) from err
