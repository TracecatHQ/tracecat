"""UDFs for entity records and case records.

This module exposes high-level UDFs over:
- `tracecat.records.service.RecordService` (entity records)
- `tracecat.cases.records.service.CaseRecordService` (case-linked records)

See `core.cases` and `core.table` for decorator patterns.
"""

from typing import Annotated, Any
from uuid import UUID

from typing_extensions import Doc

from tracecat.cases.records.models import (
    CaseRecordCreate,
    CaseRecordDeleteResponse,
    CaseRecordLink,
    CaseRecordRead,
    CaseRecordUpdate,
)
from tracecat.cases.records.service import CaseRecordService
from tracecat.cases.service import CasesService
from tracecat.entities.service import EntityService
from tracecat.records.model import RecordCreate, RecordRead, RecordUpdate
from tracecat.records.service import RecordService
from tracecat_registry import registry


@registry.register(
    default_title="List records",
    display_group="Records",
    description="List entity records with optional entity filter using cursor pagination.",
    namespace="core.records",
)
async def list_records(
    limit: Annotated[
        int,
        Doc("Maximum number of items to return (page size)."),
    ] = 20,
    cursor: Annotated[
        str | None,
        Doc("Opaque cursor for pagination; use value from previous response."),
    ] = None,
    reverse: Annotated[
        bool,
        Doc("Reverse pagination direction (use with cursor for previous page)."),
    ] = False,
    entity_id: Annotated[
        str | None,
        Doc("Optional entity ID to filter records for a specific entity."),
    ] = None,
) -> dict[str, Any]:
    entity_uuid: UUID | None = UUID(entity_id) if entity_id else None
    async with RecordService.with_session() as service:
        from tracecat.types.pagination import CursorPaginationParams

        cpp = CursorPaginationParams(limit=limit, cursor=cursor, reverse=reverse)
        resp = await service.list_records(cpp, entity_id=entity_uuid)
    return {
        "items": [item.model_dump(mode="json") for item in resp.items],
        "next_cursor": resp.next_cursor,
        "prev_cursor": resp.prev_cursor,
        "has_more": resp.has_more,
        "has_previous": resp.has_previous,
        "total_estimate": resp.total_estimate,
    }


@registry.register(
    default_title="List entity records",
    display_group="Records",
    description="List records for a specific entity using cursor pagination.",
    namespace="core.records",
)
async def list_entity_records(
    entity_id: Annotated[
        str,
        Doc("The entity ID to list records for."),
    ],
    limit: Annotated[
        int,
        Doc("Maximum number of items to return (page size)."),
    ] = 20,
    cursor: Annotated[
        str | None,
        Doc("Opaque cursor for pagination; use value from previous response."),
    ] = None,
    reverse: Annotated[
        bool,
        Doc("Reverse pagination direction (use with cursor for previous page)."),
    ] = False,
) -> dict[str, Any]:
    from tracecat.types.pagination import CursorPaginationParams

    entity_uuid = UUID(entity_id)
    async with EntityService.with_session() as entities:
        entity = await entities.get_entity(entity_uuid)
        async with RecordService.with_session() as records:
            resp = await records.list_entity_records(
                entity,
                CursorPaginationParams(limit=limit, cursor=cursor, reverse=reverse),
            )

    return {
        "items": [item.model_dump(mode="json") for item in resp.items],
        "next_cursor": resp.next_cursor,
        "prev_cursor": resp.prev_cursor,
        "has_more": resp.has_more,
        "has_previous": resp.has_previous,
        "total_estimate": resp.total_estimate,
    }


@registry.register(
    default_title="Get record",
    display_group="Records",
    description="Get a single entity record by record ID.",
    namespace="core.records",
)
async def get_record(
    record_id: Annotated[
        str,
        Doc("The entity record ID to retrieve."),
    ],
) -> dict[str, Any]:
    record_uuid = UUID(record_id)
    async with RecordService.with_session() as service:
        record = await service.get_record_by_id(record_uuid)
    return RecordRead.model_validate(record, from_attributes=True).model_dump(
        mode="json"
    )


@registry.register(
    default_title="Create record (by entity id)",
    display_group="Records",
    description="Create a new entity record for a given entity ID.",
    namespace="core.records",
)
async def create_record(
    entity_id: Annotated[
        str,
        Doc("The entity ID to create the record for."),
    ],
    data: Annotated[
        dict[str, Any],
        Doc("The JSON payload for the record (keys must match entity field keys)."),
    ],
) -> dict[str, Any]:
    entity_uuid = UUID(entity_id)
    async with EntityService.with_session() as entities:
        entity = await entities.get_entity(entity_uuid)
        async with RecordService.with_session() as records:
            created = await records.create_record(entity, RecordCreate(data=data))
    return RecordRead.model_validate(created, from_attributes=True).model_dump(
        mode="json"
    )


@registry.register(
    default_title="Create record (by entity key)",
    display_group="Records",
    description="Create a new entity record for a given entity key.",
    namespace="core.records",
)
async def create_record_by_key(
    entity_key: Annotated[
        str,
        Doc("The unique entity key to create the record for."),
    ],
    data: Annotated[
        dict[str, Any],
        Doc("The JSON payload for the record (keys must match entity field keys)."),
    ],
) -> dict[str, Any]:
    async with EntityService.with_session() as entities:
        entity = await entities.get_entity_by_key(entity_key)
        async with RecordService.with_session() as records:
            created = await records.create_record(entity, RecordCreate(data=data))
    return RecordRead.model_validate(created, from_attributes=True).model_dump(
        mode="json"
    )


@registry.register(
    default_title="Update record",
    display_group="Records",
    description="Update an existing entity record by merging provided fields.",
    namespace="core.records",
)
async def update_record(
    record_id: Annotated[
        str,
        Doc("The entity record ID to update."),
    ],
    data: Annotated[
        dict[str, Any],
        Doc("Partial data to merge into the existing record."),
    ],
) -> dict[str, Any]:
    record_uuid = UUID(record_id)
    async with RecordService.with_session() as service:
        record = await service.get_record_by_id(record_uuid)
        updated = await service.update_record(record, RecordUpdate(data=data))
    return RecordRead.model_validate(updated, from_attributes=True).model_dump(
        mode="json"
    )


@registry.register(
    default_title="Delete record",
    display_group="Records",
    description="Delete an entity record by ID.",
    namespace="core.records",
)
async def delete_record(
    record_id: Annotated[
        str,
        Doc("The entity record ID to delete."),
    ],
) -> None:
    record_uuid = UUID(record_id)
    async with RecordService.with_session() as service:
        record = await service.get_record_by_id(record_uuid)
        await service.delete_record(record)


@registry.register(
    default_title="List case records",
    display_group="Cases",
    description="List all records linked to a case.",
    namespace="core.cases",
)
async def list_case_records(
    case_id: Annotated[
        str,
        Doc("The case ID to list records for."),
    ],
) -> dict[str, Any]:
    case_uuid = UUID(case_id)
    async with CaseRecordService.with_session() as cr_service:
        # Reuse the same session to fetch the case
        cases = CasesService(cr_service.session, cr_service.role)
        case = await cases.get_case(case_uuid)
        if case is None:
            raise ValueError(f"Case with ID {case_id} not found")
        records = await cr_service.list_case_records(case)

    items = [
        CaseRecordRead(
            id=rec.id,
            case_id=rec.case_id,
            entity_id=rec.entity_id,
            record_id=rec.record_id,
            entity_key=rec.entity.key,
            entity_display_name=rec.entity.display_name,
            data=rec.record.data,
            created_at=rec.created_at,
            updated_at=rec.updated_at,
        ).model_dump(mode="json")
        for rec in records
    ]
    return {"items": items, "total": len(items)}


@registry.register(
    default_title="Get case record",
    display_group="Cases",
    description="Get a specific case-linked record by link ID.",
    namespace="core.cases",
)
async def get_case_record(
    case_id: Annotated[
        str,
        Doc("The case ID containing the record."),
    ],
    case_record_id: Annotated[
        str,
        Doc("The case record link ID to retrieve."),
    ],
) -> dict[str, Any]:
    case_uuid = UUID(case_id)
    link_uuid = UUID(case_record_id)
    async with CaseRecordService.with_session() as cr_service:
        cases = CasesService(cr_service.session, cr_service.role)
        case = await cases.get_case(case_uuid)
        if case is None:
            raise ValueError(f"Case with ID {case_id} not found")
        record_link = await cr_service.get_case_record(case, link_uuid)
        if record_link is None:
            raise ValueError(f"Case record with ID {case_record_id} not found")
    return CaseRecordRead(
        id=record_link.id,
        case_id=record_link.case_id,
        entity_id=record_link.entity_id,
        record_id=record_link.record_id,
        entity_key=record_link.entity.key,
        entity_display_name=record_link.entity.display_name,
        data=record_link.record.data,
        created_at=record_link.created_at,
        updated_at=record_link.updated_at,
    ).model_dump(mode="json")


@registry.register(
    default_title="Create case record",
    display_group="Cases",
    description="Create a new entity record and link it to a case.",
    namespace="core.cases",
)
async def create_case_record(
    case_id: Annotated[
        str,
        Doc("The case ID to add the record to."),
    ],
    entity_key: Annotated[
        str,
        Doc("The entity key for the record to create."),
    ],
    data: Annotated[
        dict[str, Any],
        Doc("The JSON payload for the new entity record."),
    ],
) -> dict[str, Any]:
    case_uuid = UUID(case_id)
    async with CaseRecordService.with_session() as cr_service:
        cases = CasesService(cr_service.session, cr_service.role)
        case = await cases.get_case(case_uuid)
        if case is None:
            raise ValueError(f"Case with ID {case_id} not found")
        created = await cr_service.create_case_record(
            case,
            CaseRecordCreate(entity_key=entity_key, data=data),
        )
    return CaseRecordRead(
        id=created.id,
        case_id=created.case_id,
        entity_id=created.entity_id,
        record_id=created.record_id,
        entity_key=created.entity.key,
        entity_display_name=created.entity.display_name,
        data=created.record.data,
        created_at=created.created_at,
        updated_at=created.updated_at,
    ).model_dump(mode="json")


@registry.register(
    default_title="Link entity record to case",
    display_group="Cases",
    description="Link an existing entity record to a case.",
    namespace="core.cases",
)
async def link_entity_record(
    case_id: Annotated[
        str,
        Doc("The case ID to link the record to."),
    ],
    entity_record_id: Annotated[
        str,
        Doc("The entity record ID to link."),
    ],
) -> dict[str, Any]:
    case_uuid = UUID(case_id)
    er_uuid = UUID(entity_record_id)
    async with CaseRecordService.with_session() as cr_service:
        cases = CasesService(cr_service.session, cr_service.role)
        case = await cases.get_case(case_uuid)
        if case is None:
            raise ValueError(f"Case with ID {case_id} not found")
        linked = await cr_service.link_entity_record(
            case,
            CaseRecordLink(entity_record_id=er_uuid),
        )
    return CaseRecordRead(
        id=linked.id,
        case_id=linked.case_id,
        entity_id=linked.entity_id,
        record_id=linked.record_id,
        entity_key=linked.entity.key,
        entity_display_name=linked.entity.display_name,
        data=linked.record.data,
        created_at=linked.created_at,
        updated_at=linked.updated_at,
    ).model_dump(mode="json")


@registry.register(
    default_title="Update case record",
    display_group="Cases",
    description="Update the entity record data for a case-linked record.",
    namespace="core.cases",
)
async def update_case_record(
    case_id: Annotated[
        str,
        Doc("The case ID containing the record."),
    ],
    case_record_id: Annotated[
        str,
        Doc("The case record link ID to update."),
    ],
    data: Annotated[
        dict[str, Any],
        Doc("Partial data to merge into the underlying entity record."),
    ],
) -> dict[str, Any]:
    case_uuid = UUID(case_id)
    link_uuid = UUID(case_record_id)
    async with CaseRecordService.with_session() as cr_service:
        cases = CasesService(cr_service.session, cr_service.role)
        case = await cases.get_case(case_uuid)
        if case is None:
            raise ValueError(f"Case with ID {case_id} not found")
        record = await cr_service.get_case_record(case, link_uuid)
        if record is None:
            raise ValueError(f"Case record with ID {case_record_id} not found")
        updated = await cr_service.update_case_record(
            record, CaseRecordUpdate(data=data)
        )
    return CaseRecordRead(
        id=updated.id,
        case_id=updated.case_id,
        entity_id=updated.entity_id,
        record_id=updated.record_id,
        entity_key=updated.entity.key,
        entity_display_name=updated.entity.display_name,
        data=updated.record.data,
        created_at=updated.created_at,
        updated_at=updated.updated_at,
    ).model_dump(mode="json")


@registry.register(
    default_title="Unlink case record",
    display_group="Cases",
    description="Unlink a record from a case (soft delete of the link only).",
    namespace="core.cases",
)
async def unlink_case_record(
    case_id: Annotated[
        str,
        Doc("The case ID containing the record to unlink."),
    ],
    case_record_id: Annotated[
        str,
        Doc("The case record link ID to unlink."),
    ],
) -> dict[str, Any]:
    case_uuid = UUID(case_id)
    link_uuid = UUID(case_record_id)
    async with CaseRecordService.with_session() as cr_service:
        cases = CasesService(cr_service.session, cr_service.role)
        case = await cases.get_case(case_uuid)
        if case is None:
            raise ValueError(f"Case with ID {case_id} not found")
        record_link = await cr_service.get_case_record(case, link_uuid)
        if record_link is None:
            raise ValueError(f"Case record with ID {case_record_id} not found")
        rec_id = record_link.record_id
        await cr_service.unlink_case_record(record_link)
    return CaseRecordDeleteResponse(
        action="unlink",
        case_id=UUID(case_id),
        record_id=rec_id,
        case_record_id=UUID(case_record_id),
    ).model_dump(mode="json")


@registry.register(
    default_title="Delete case record",
    display_group="Cases",
    description="Delete a case-linked record and its underlying entity record.",
    namespace="core.cases",
)
async def delete_case_record(
    case_id: Annotated[
        str,
        Doc("The case ID containing the record to delete."),
    ],
    case_record_id: Annotated[
        str,
        Doc("The case record link ID to delete."),
    ],
) -> dict[str, Any]:
    case_uuid = UUID(case_id)
    link_uuid = UUID(case_record_id)
    async with CaseRecordService.with_session() as cr_service:
        cases = CasesService(cr_service.session, cr_service.role)
        case = await cases.get_case(case_uuid)
        if case is None:
            raise ValueError(f"Case with ID {case_id} not found")
        record_link = await cr_service.get_case_record(case, link_uuid)
        if record_link is None:
            raise ValueError(f"Case record with ID {case_record_id} not found")
        rec_id = record_link.record_id
        await cr_service.delete_case_record(record_link)
    return CaseRecordDeleteResponse(
        action="delete",
        case_id=UUID(case_id),
        record_id=rec_id,
        case_record_id=UUID(case_record_id),
    ).model_dump(mode="json")
