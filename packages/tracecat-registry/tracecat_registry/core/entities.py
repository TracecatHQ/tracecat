"""UDFs for entity and entity field management.

This module exposes high-level UDFs over:
- `tracecat.entities.service.EntityService` (entity management)
- `tracecat.entities.service.EntityFieldsService` (entity field management)

Entities are data structure definitions that can be used to create structured records.
Fields define the schema for entities, supporting various types including text, numbers,
dates, and select dropdowns.
"""

from typing import Annotated, Any, Literal
from uuid import UUID

from typing_extensions import Doc

from tracecat.entities.models import (
    EntityCreate,
    EntityFieldCreate,
    EntityFieldOptionCreate,
    EntityFieldRead,
    EntityFieldUpdate,
    EntityRead,
    EntityUpdate,
)
from tracecat.entities.service import EntityService
from tracecat_registry import registry


@registry.register(
    default_title="List entities",
    display_group="Entities",
    description="List all entity definitions in the workspace. Returns active entities by default.",
    namespace="core.entities",
)
async def list_entities(
    include_inactive: Annotated[
        bool,
        Doc("Include inactive entities in the results. Default is False."),
    ] = False,
) -> dict[str, Any]:
    async with EntityService.with_session() as service:
        entities = await service.list_entities(include_inactive=include_inactive)
    return {
        "items": [
            EntityRead.model_validate(entity, from_attributes=True).model_dump(
                mode="json"
            )
            for entity in entities
        ],
        "total": len(entities),
    }


@registry.register(
    default_title="Get entity",
    display_group="Entities",
    description="Get an entity definition by ID or key. Accepts either a UUID or entity key string.",
    namespace="core.entities",
)
async def get_entity(
    id_or_key: Annotated[
        str,
        Doc(
            "Entity identifier - either a UUID (e.g., '123e4567-e89b-12d3-a456-426614174000') "
            "or entity key (e.g., 'security_incident', 'user_account')."
        ),
    ],
) -> dict[str, Any]:
    async with EntityService.with_session() as service:
        # Try to parse as UUID first
        try:
            entity_id = UUID(id_or_key)
            entity = await service.get_entity(entity_id)
        except ValueError:
            # Not a UUID, treat as key
            entity = await service.get_entity_by_key(id_or_key)
    return EntityRead.model_validate(entity, from_attributes=True).model_dump(
        mode="json"
    )


@registry.register(
    default_title="Create entity",
    display_group="Entities",
    description="Create a new entity definition. Entity key must be unique and will be normalized to snake_case.",
    namespace="core.entities",
)
async def create_entity(
    key: Annotated[
        str,
        Doc(
            "Unique entity key in snake_case (e.g., 'security_incident', 'user_account'). "
            "Will be automatically normalized if not in snake_case."
        ),
    ],
    display_name: Annotated[
        str,
        Doc(
            "Human-readable name for the entity (e.g., 'Security Incident', 'User Account')."
        ),
    ],
    description: Annotated[
        str | None,
        Doc("Optional description explaining the entity's purpose and usage."),
    ] = None,
    icon: Annotated[
        str | None,
        Doc(
            "Optional icon identifier for UI display (e.g., 'shield', 'user', 'alert')."
        ),
    ] = None,
) -> dict[str, Any]:
    async with EntityService.with_session() as service:
        entity = await service.create_entity(
            EntityCreate(
                key=key,
                display_name=display_name,
                description=description,
                icon=icon,
            )
        )
    return EntityRead.model_validate(entity, from_attributes=True).model_dump(
        mode="json"
    )


@registry.register(
    default_title="Create entities",
    display_group="Entities",
    description="Create multiple entity definitions in a single operation. All entities are created atomically.",
    namespace="core.entities",
)
async def create_entities(
    entities: Annotated[
        list[dict[str, Any]],
        Doc(
            "List of entity definitions to create. Each item must contain: "
            "'key' (unique snake_case identifier), 'display_name' (human-readable name), "
            "and optionally 'description' and 'icon'. "
            "Example: [{'key': 'incident', 'display_name': 'Security Incident', "
            "'description': 'Security incidents and breaches'}, "
            "{'key': 'alert', 'display_name': 'Security Alert'}]"
        ),
    ],
) -> dict[str, Any]:
    """Create multiple entities in a single atomic operation.

    This is more efficient than calling create_entity multiple times and ensures
    all entities are created together or none at all if there's an error.
    """
    created_entities = []
    async with EntityService.with_session() as service:
        for entity_data in entities:
            entity = await service.create_entity(
                EntityCreate(
                    key=entity_data["key"],
                    display_name=entity_data["display_name"],
                    description=entity_data.get("description"),
                    icon=entity_data.get("icon"),
                )
            )
            created_entities.append(entity)
        # Commit happens when exiting the session context

    return {
        "items": [
            EntityRead.model_validate(entity, from_attributes=True).model_dump(
                mode="json"
            )
            for entity in created_entities
        ],
        "total": len(created_entities),
    }


@registry.register(
    default_title="Update entity",
    display_group="Entities",
    description="Update an existing entity's properties. Entity key cannot be changed.",
    namespace="core.entities",
)
async def update_entity(
    id_or_key: Annotated[
        str,
        Doc(
            "Entity identifier - either a UUID or entity key. "
            "Use the same identifier format used when creating or retrieving the entity."
        ),
    ],
    display_name: Annotated[
        str | None,
        Doc("New human-readable name for the entity."),
    ] = None,
    description: Annotated[
        str | None,
        Doc("New description for the entity."),
    ] = None,
    icon: Annotated[
        str | None,
        Doc("New icon identifier for the entity."),
    ] = None,
) -> dict[str, Any]:
    async with EntityService.with_session() as service:
        # Get the entity first
        try:
            entity_id = UUID(id_or_key)
            entity = await service.get_entity(entity_id)
        except ValueError:
            entity = await service.get_entity_by_key(id_or_key)

        # Update with provided fields
        updated = await service.update_entity(
            entity,
            EntityUpdate(
                display_name=display_name,
                description=description,
                icon=icon,
            ),
        )
    return EntityRead.model_validate(updated, from_attributes=True).model_dump(
        mode="json"
    )


@registry.register(
    default_title="Activate entity",
    display_group="Entities",
    description="Activate an inactive entity, making it available for use.",
    namespace="core.entities",
)
async def activate_entity(
    id_or_key: Annotated[
        str,
        Doc("Entity identifier - either a UUID or entity key."),
    ],
) -> dict[str, Any]:
    async with EntityService.with_session() as service:
        try:
            entity_id = UUID(id_or_key)
            entity = await service.get_entity(entity_id)
        except ValueError:
            entity = await service.get_entity_by_key(id_or_key)

        activated = await service.activate_entity(entity)
    return EntityRead.model_validate(activated, from_attributes=True).model_dump(
        mode="json"
    )


@registry.register(
    default_title="Deactivate entity",
    display_group="Entities",
    description="Deactivate an active entity, preventing new records from being created.",
    namespace="core.entities",
)
async def deactivate_entity(
    id_or_key: Annotated[
        str,
        Doc("Entity identifier - either a UUID or entity key."),
    ],
) -> dict[str, Any]:
    async with EntityService.with_session() as service:
        try:
            entity_id = UUID(id_or_key)
            entity = await service.get_entity(entity_id)
        except ValueError:
            entity = await service.get_entity_by_key(id_or_key)

        deactivated = await service.deactivate_entity(entity)
    return EntityRead.model_validate(deactivated, from_attributes=True).model_dump(
        mode="json"
    )


@registry.register(
    default_title="Delete entity",
    display_group="Entities",
    description="Permanently delete an entity and all its fields. This action cannot be undone.",
    namespace="core.entities",
)
async def delete_entity(
    id_or_key: Annotated[
        str,
        Doc("Entity identifier - either a UUID or entity key."),
    ],
) -> None:
    async with EntityService.with_session() as service:
        try:
            entity_id = UUID(id_or_key)
            entity = await service.get_entity(entity_id)
        except ValueError:
            entity = await service.get_entity_by_key(id_or_key)

        await service.delete_entity(entity)


@registry.register(
    default_title="List entity fields",
    display_group="Entities",
    description="List all fields defined for an entity. Returns active fields by default.",
    namespace="core.entities",
)
async def list_entity_fields(
    entity_id_or_key: Annotated[
        str,
        Doc("Entity identifier - either a UUID or entity key."),
    ],
    include_inactive: Annotated[
        bool,
        Doc("Include inactive fields in the results. Default is False."),
    ] = False,
) -> dict[str, Any]:
    async with EntityService.with_session() as service:
        # Get the entity first
        try:
            entity_id = UUID(entity_id_or_key)
            entity = await service.get_entity(entity_id)
        except ValueError:
            entity = await service.get_entity_by_key(entity_id_or_key)

        fields = await service.fields.list_fields(
            entity, include_inactive=include_inactive
        )
    return {
        "items": [
            EntityFieldRead.model_validate(field, from_attributes=True).model_dump(
                mode="json"
            )
            for field in fields
        ],
        "total": len(fields),
    }


@registry.register(
    default_title="Get entity field",
    display_group="Entities",
    description="Get a specific field from an entity by field ID or field key.",
    namespace="core.entities",
)
async def get_entity_field(
    entity_id_or_key: Annotated[
        str,
        Doc("Entity identifier - either a UUID or entity key."),
    ],
    field_id_or_key: Annotated[
        str,
        Doc(
            "Field identifier - either a field UUID or field key (e.g., 'status', 'priority')."
        ),
    ],
) -> dict[str, Any]:
    async with EntityService.with_session() as service:
        # Get the entity first
        try:
            entity_id = UUID(entity_id_or_key)
            entity = await service.get_entity(entity_id)
        except ValueError:
            entity = await service.get_entity_by_key(entity_id_or_key)

        # Get the field
        try:
            field_id = UUID(field_id_or_key)
            field = await service.fields.get_field(entity, field_id)
        except ValueError:
            field = await service.fields.get_field_by_key(entity, field_id_or_key)

    return EntityFieldRead.model_validate(field, from_attributes=True).model_dump(
        mode="json"
    )


@registry.register(
    default_title="Create entity field",
    display_group="Entities",
    description=(
        "Create a new field for an entity. "
        "Field types: INTEGER (whole numbers), NUMBER (decimals), TEXT (strings), "
        "BOOL (true/false), JSON (structured data), DATETIME (timestamp), DATE (date only), "
        "SELECT (single choice dropdown), MULTI_SELECT (multiple choice dropdown)."
    ),
    namespace="core.entities",
)
async def create_entity_field(
    entity_id_or_key: Annotated[
        str,
        Doc("Entity identifier - either a UUID or entity key."),
    ],
    key: Annotated[
        str,
        Doc(
            "Unique field key in snake_case (e.g., 'status', 'priority', 'assigned_to'). "
            "Will be automatically normalized if not in snake_case."
        ),
    ],
    type: Annotated[
        Literal[
            "INTEGER",
            "NUMBER",
            "TEXT",
            "BOOL",
            "JSON",
            "DATETIME",
            "DATE",
            "SELECT",
            "MULTI_SELECT",
        ],
        Doc(
            "Field data type. Use: "
            "INTEGER for whole numbers (e.g., count, score); "
            "NUMBER for decimals (e.g., price, percentage); "
            "TEXT for strings (e.g., name, description); "
            "BOOL for true/false values; "
            "JSON for structured data (dicts/lists); "
            "DATETIME for timestamps; "
            "DATE for dates without time; "
            "SELECT for single-choice dropdown (requires options); "
            "MULTI_SELECT for multiple-choice dropdown (requires options)."
        ),
    ],
    display_name: Annotated[
        str,
        Doc(
            "Human-readable field label (e.g., 'Status', 'Priority Level', 'Assigned To')."
        ),
    ],
    description: Annotated[
        str | None,
        Doc("Optional field description to guide users on proper usage."),
    ] = None,
    default_value: Annotated[
        Any | None,
        Doc(
            "Default value for the field. Must match the field type: "
            "INTEGER/NUMBER: numeric value; TEXT: string; BOOL: true/false; "
            "JSON: dict or list; DATETIME/DATE: ISO string; "
            "SELECT: single option key; MULTI_SELECT: list of option keys."
        ),
    ] = None,
    options: Annotated[
        list[dict[str, str]] | None,
        Doc(
            "Required for SELECT/MULTI_SELECT types. List of options with format: "
            "[{'key': 'option_key', 'label': 'Display Label'}]. "
            "Key is optional and will be auto-generated from label if not provided. "
            "Example: [{'label': 'High'}, {'label': 'Medium'}, {'label': 'Low'}] "
            "or [{'key': 'high', 'label': 'High Priority'}]."
        ),
    ] = None,
) -> dict[str, Any]:
    async with EntityService.with_session() as service:
        # Get the entity first
        try:
            entity_id = UUID(entity_id_or_key)
            entity = await service.get_entity(entity_id)
        except ValueError:
            entity = await service.get_entity_by_key(entity_id_or_key)

        # Convert options dict to proper model
        field_options = None
        if options:
            field_options = [
                EntityFieldOptionCreate(
                    key=opt.get("key"),
                    label=opt["label"],
                )
                for opt in options
            ]

        # Import FieldType here to avoid circular imports
        from tracecat.entities.enums import FieldType

        field = await service.fields.create_field(
            entity,
            EntityFieldCreate(
                key=key,
                type=FieldType(type),
                display_name=display_name,
                description=description,
                default_value=default_value,
                options=field_options,
            ),
        )
    return EntityFieldRead.model_validate(field, from_attributes=True).model_dump(
        mode="json"
    )


@registry.register(
    default_title="Create entity fields",
    display_group="Entities",
    description=(
        "Create multiple fields for an entity in a single operation. "
        "Perfect for defining a complete entity schema at once."
    ),
    namespace="core.entities",
)
async def create_entity_fields(
    entity_id_or_key: Annotated[
        str,
        Doc("Entity identifier - either a UUID or entity key."),
    ],
    fields: Annotated[
        list[dict[str, Any]],
        Doc(
            "List of field definitions to create. Each field must contain: "
            "'key' (unique snake_case identifier), 'type' (INTEGER, NUMBER, TEXT, BOOL, JSON, "
            "DATETIME, DATE, SELECT, MULTI_SELECT), 'display_name' (human-readable label), "
            "and optionally 'description', 'default_value', and 'options' (for SELECT types). "
            "Example: [{'key': 'status', 'type': 'SELECT', 'display_name': 'Status', "
            "'options': [{'label': 'Open'}, {'label': 'Closed'}]}, "
            "{'key': 'priority', 'type': 'INTEGER', 'display_name': 'Priority', 'default_value': 3}]"
        ),
    ],
) -> dict[str, Any]:
    """Create multiple fields for an entity in a single atomic operation.

    This is ideal for setting up a complete entity schema efficiently. All fields
    are created together or none at all if there's an error.
    """
    from tracecat.entities.enums import FieldType

    created_fields = []
    async with EntityService.with_session() as service:
        # Get the entity first
        try:
            entity_id = UUID(entity_id_or_key)
            entity = await service.get_entity(entity_id)
        except ValueError:
            entity = await service.get_entity_by_key(entity_id_or_key)

        # Create all fields
        for field_data in fields:
            # Convert options dict to proper model
            field_options = None
            if field_data.get("options"):
                field_options = [
                    EntityFieldOptionCreate(
                        key=opt.get("key"),
                        label=opt["label"],
                    )
                    for opt in field_data["options"]
                ]

            field = await service.fields.create_field(
                entity,
                EntityFieldCreate(
                    key=field_data["key"],
                    type=FieldType(field_data["type"]),
                    display_name=field_data["display_name"],
                    description=field_data.get("description"),
                    default_value=field_data.get("default_value"),
                    options=field_options,
                ),
            )
            created_fields.append(field)
        # Commit happens when exiting the session context

    return {
        "items": [
            EntityFieldRead.model_validate(field, from_attributes=True).model_dump(
                mode="json"
            )
            for field in created_fields
        ],
        "total": len(created_fields),
    }


@registry.register(
    default_title="Update entity field",
    display_group="Entities",
    description="Update an existing entity field. Field key and type cannot be changed.",
    namespace="core.entities",
)
async def update_entity_field(
    entity_id_or_key: Annotated[
        str,
        Doc("Entity identifier - either a UUID or entity key."),
    ],
    field_id_or_key: Annotated[
        str,
        Doc("Field identifier - either a field UUID or field key."),
    ],
    display_name: Annotated[
        str | None,
        Doc("New display name for the field."),
    ] = None,
    description: Annotated[
        str | None,
        Doc("New description for the field."),
    ] = None,
    default_value: Annotated[
        Any | None,
        Doc("New default value. Must match the field's type."),
    ] = None,
    options: Annotated[
        list[dict[str, str]] | None,
        Doc(
            "New options list for SELECT/MULTI_SELECT fields. "
            "Replaces all existing options. Same format as create_entity_field."
        ),
    ] = None,
) -> dict[str, Any]:
    async with EntityService.with_session() as service:
        # Get the entity first
        try:
            entity_id = UUID(entity_id_or_key)
            entity = await service.get_entity(entity_id)
        except ValueError:
            entity = await service.get_entity_by_key(entity_id_or_key)

        # Get the field
        try:
            field_id = UUID(field_id_or_key)
            field = await service.fields.get_field(entity, field_id)
        except ValueError:
            field = await service.fields.get_field_by_key(entity, field_id_or_key)

        # Convert options dict to proper model
        field_options = None
        if options is not None:
            field_options = [
                EntityFieldOptionCreate(
                    key=opt.get("key"),
                    label=opt["label"],
                )
                for opt in options
            ]

        updated = await service.fields.update_field(
            field,
            EntityFieldUpdate(
                display_name=display_name,
                description=description,
                default_value=default_value,
                options=field_options,
            ),
        )
    return EntityFieldRead.model_validate(updated, from_attributes=True).model_dump(
        mode="json"
    )


@registry.register(
    default_title="Activate entity field",
    display_group="Entities",
    description="Activate an inactive entity field, making it available for use in records.",
    namespace="core.entities",
)
async def activate_entity_field(
    entity_id_or_key: Annotated[
        str,
        Doc("Entity identifier - either a UUID or entity key."),
    ],
    field_id_or_key: Annotated[
        str,
        Doc("Field identifier - either a field UUID or field key."),
    ],
) -> dict[str, Any]:
    async with EntityService.with_session() as service:
        # Get the entity first
        try:
            entity_id = UUID(entity_id_or_key)
            entity = await service.get_entity(entity_id)
        except ValueError:
            entity = await service.get_entity_by_key(entity_id_or_key)

        # Get the field
        try:
            field_id = UUID(field_id_or_key)
            field = await service.fields.get_field(entity, field_id)
        except ValueError:
            field = await service.fields.get_field_by_key(entity, field_id_or_key)

        activated = await service.fields.activate_field(field)
    return EntityFieldRead.model_validate(activated, from_attributes=True).model_dump(
        mode="json"
    )


@registry.register(
    default_title="Deactivate entity field",
    display_group="Entities",
    description="Deactivate an active entity field. Existing records retain their values.",
    namespace="core.entities",
)
async def deactivate_entity_field(
    entity_id_or_key: Annotated[
        str,
        Doc("Entity identifier - either a UUID or entity key."),
    ],
    field_id_or_key: Annotated[
        str,
        Doc("Field identifier - either a field UUID or field key."),
    ],
) -> dict[str, Any]:
    async with EntityService.with_session() as service:
        # Get the entity first
        try:
            entity_id = UUID(entity_id_or_key)
            entity = await service.get_entity(entity_id)
        except ValueError:
            entity = await service.get_entity_by_key(entity_id_or_key)

        # Get the field
        try:
            field_id = UUID(field_id_or_key)
            field = await service.fields.get_field(entity, field_id)
        except ValueError:
            field = await service.fields.get_field_by_key(entity, field_id_or_key)

        deactivated = await service.fields.deactivate_field(field)
    return EntityFieldRead.model_validate(deactivated, from_attributes=True).model_dump(
        mode="json"
    )


@registry.register(
    default_title="Delete entity field",
    display_group="Entities",
    description="Permanently delete an entity field. This action cannot be undone.",
    namespace="core.entities",
)
async def delete_entity_field(
    entity_id_or_key: Annotated[
        str,
        Doc("Entity identifier - either a UUID or entity key."),
    ],
    field_id_or_key: Annotated[
        str,
        Doc("Field identifier - either a field UUID or field key."),
    ],
) -> None:
    async with EntityService.with_session() as service:
        # Get the entity first
        try:
            entity_id = UUID(entity_id_or_key)
            entity = await service.get_entity(entity_id)
        except ValueError:
            entity = await service.get_entity_by_key(entity_id_or_key)

        # Get the field
        try:
            field_id = UUID(field_id_or_key)
            field = await service.fields.get_field(entity, field_id)
        except ValueError:
            field = await service.fields.get_field_by_key(entity, field_id_or_key)

        await service.fields.delete_field(field)
