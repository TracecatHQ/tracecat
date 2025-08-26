import uuid

import pytest
from sqlalchemy.exc import IntegrityError
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from tracecat import config
from tracecat.db.schemas import EntityField, EntityFieldOption, Workspace
from tracecat.entities.enums import FieldType
from tracecat.entities.models import (
    EntityCreate,
    EntityFieldCreate,
    EntityFieldOptionCreate,
    EntityFieldUpdate,
    EntityUpdate,
)
from tracecat.entities.service import EntityService
from tracecat.types.auth import AccessLevel, Role
from tracecat.types.exceptions import TracecatAuthorizationError, TracecatNotFoundError

pytestmark = pytest.mark.usefixtures("db")


@pytest.mark.anyio
async def test_service_initialization_requires_workspace(session: AsyncSession) -> None:
    """EntityService requires a workspace_id in role."""
    role_without_workspace = Role(
        type="service",
        user_id=uuid.uuid4(),
        workspace_id=None,
        service_id="tracecat-service",
        access_level=AccessLevel.BASIC,
    )

    with pytest.raises(TracecatAuthorizationError):
        EntityService(session=session, role=role_without_workspace)


@pytest.fixture
async def entities_service(session: AsyncSession, svc_role: Role) -> EntityService:
    return EntityService(session=session, role=svc_role)


@pytest.fixture
def simple_entity_create() -> EntityCreate:
    return EntityCreate(key="asset", display_name="Asset", description="Assets")


@pytest.mark.anyio
class TestEntityService:
    async def test_create_get_update_delete_entity(
        self, entities_service: EntityService, simple_entity_create: EntityCreate
    ) -> None:
        # Create
        entity = await entities_service.create_entity(simple_entity_create)
        assert entity.key == "asset"
        assert entity.display_name == "Asset"
        assert entity.owner_id == entities_service.workspace_id

        # Get
        fetched = await entities_service.get_entity(entity.id)
        assert fetched.id == entity.id

        # Update
        updated = await entities_service.update_entity(
            entity, EntityUpdate(display_name="Asset Record")
        )
        assert updated.display_name == "Asset Record"

        # Deactivate and Activate
        deactivated = await entities_service.deactivate_entity(entity)
        assert deactivated.is_active is False
        activated = await entities_service.activate_entity(entity)
        assert activated.is_active is True

        # List
        active_only = await entities_service.list_entities()
        assert any(e.id == entity.id for e in active_only)
        # When deactivated, should not appear unless include_inactive=True
        await entities_service.deactivate_entity(entity)
        active_only = await entities_service.list_entities()
        assert all(e.id != entity.id for e in active_only)
        all_entities = await entities_service.list_entities(include_inactive=True)
        assert any(e.id == entity.id for e in all_entities)

        # Delete
        await entities_service.delete_entity(entity)
        with pytest.raises(TracecatNotFoundError):
            await entities_service.get_entity(entity.id)


@pytest.mark.anyio
class TestEntityFieldsService:
    async def test_create_list_get_update_delete_field(
        self, entities_service: EntityService
    ) -> None:
        # Prepare entity
        entity = await entities_service.create_entity(
            EntityCreate(key="user_profile", display_name="User Profile")
        )

        # Create field with type coercion (int from string)
        field = await entities_service.fields.create_field(
            entity,
            EntityFieldCreate(
                key="age",
                type=FieldType.INTEGER,
                display_name="Age",
                default_value="42",
            ),
        )
        assert field.default_value == 42

        # Create a select field with options and default
        select_field = await entities_service.fields.create_field(
            entity,
            EntityFieldCreate(
                key="status",
                type=FieldType.SELECT,
                display_name="Status",
                options=[
                    EntityFieldOptionCreate(label="Active", key="active"),
                    EntityFieldOptionCreate(label="Inactive", key="inactive"),
                ],
                default_value="active",
            ),
        )
        assert select_field.default_value == "active"
        assert {opt.key for opt in select_field.options} == {"active", "inactive"}

        # List fields (default excludes inactive)
        fields = await entities_service.fields.list_fields(entity)
        keys = {f.key for f in fields}
        assert {"age", "status"}.issubset(keys)

        # Get by id
        fetched = await entities_service.fields.get_field(entity, field.id)
        assert fetched.id == field.id

        # Update default value
        updated = await entities_service.fields.update_field(
            field, EntityFieldUpdate(default_value=100)
        )
        assert updated.default_value == 100

        # Options sync: update label, add one, remove one
        # Initial keys: active, inactive
        updated_select = await entities_service.fields.update_field(
            select_field,
            EntityFieldUpdate(
                options=[
                    EntityFieldOptionCreate(
                        label="ACTIVE", key="active"
                    ),  # update label
                    EntityFieldOptionCreate(label="Pending", key="pending"),  # add new
                    # omit "inactive" to remove
                ]
            ),
        )
        updated_keys = {opt.key for opt in updated_select.options}
        assert updated_keys == {"active", "pending"}
        # Check label updated for existing key
        updated_active = next(o for o in updated_select.options if o.key == "active")
        assert updated_active.label == "ACTIVE"

        # Deactivate and Activate field
        deactivated_field = await entities_service.fields.deactivate_field(field)
        assert deactivated_field.is_active is False
        activated_field = await entities_service.fields.activate_field(field)
        assert activated_field.is_active is True

        # Delete field
        await entities_service.fields.delete_field(field)
        with pytest.raises(TracecatNotFoundError):
            await entities_service.fields.get_field(entity, field.id)


@pytest.mark.anyio
class TestCrossWorkspaceAndUniqueness:
    async def test_cross_workspace_access_denied(
        self, session: AsyncSession, svc_role: Role
    ) -> None:
        svc_a = EntityService(session=session, role=svc_role)
        entity = await svc_a.create_entity(
            EntityCreate(key="device", display_name="Device")
        )

        # Create second workspace and role
        ws_b = Workspace(name="other-ws", owner_id=config.TRACECAT__DEFAULT_ORG_ID)
        session.add(ws_b)
        await session.commit()
        await session.refresh(ws_b)
        role_b = Role(
            type="user",
            access_level=AccessLevel.BASIC,
            workspace_id=ws_b.id,
            user_id=uuid.uuid4(),
            service_id="tracecat-api",
        )
        svc_b = EntityService(session=session, role=role_b)

        # Entity from A not visible to B
        with pytest.raises(TracecatNotFoundError):
            await svc_b.get_entity(entity.id)

        # Field creation on A's entity with B's service should fail
        with pytest.raises(TracecatNotFoundError):
            await svc_b.fields.create_field(
                entity,
                EntityFieldCreate(key="foo", type=FieldType.TEXT, display_name="Foo"),
            )

    async def test_entity_key_uniqueness_same_workspace(
        self, entities_service: EntityService
    ) -> None:
        await entities_service.create_entity(
            EntityCreate(key="dup", display_name="Dup")
        )
        with pytest.raises(IntegrityError):
            await entities_service.create_entity(
                EntityCreate(key="dup", display_name="Dup2")
            )

    async def test_entity_key_uniqueness_across_workspaces(
        self, session: AsyncSession, svc_role: Role
    ) -> None:
        svc_a = EntityService(session=session, role=svc_role)
        await svc_a.create_entity(EntityCreate(key="shared", display_name="Shared A"))

        # New workspace and role
        ws_b = Workspace(name="ws-b", owner_id=config.TRACECAT__DEFAULT_ORG_ID)
        session.add(ws_b)
        await session.commit()
        await session.refresh(ws_b)
        role_b = Role(
            type="user",
            access_level=AccessLevel.BASIC,
            workspace_id=ws_b.id,
            user_id=uuid.uuid4(),
            service_id="tracecat-api",
        )
        svc_b = EntityService(session=session, role=role_b)
        # Same key should be allowed in different workspace
        await svc_b.create_entity(EntityCreate(key="shared", display_name="Shared B"))

    async def test_field_key_uniqueness_per_entity(
        self, entities_service: EntityService
    ) -> None:
        entity = await entities_service.create_entity(
            EntityCreate(key="ek1", display_name="E1")
        )
        await entities_service.fields.create_field(
            entity, EntityFieldCreate(key="k", type=FieldType.TEXT, display_name="K")
        )
        with pytest.raises(IntegrityError):
            await entities_service.fields.create_field(
                entity,
                EntityFieldCreate(key="k", type=FieldType.TEXT, display_name="K2"),
            )

    async def test_field_key_can_repeat_across_entities(
        self, entities_service: EntityService
    ) -> None:
        e1 = await entities_service.create_entity(
            EntityCreate(key="e1", display_name="E1")
        )
        e2 = await entities_service.create_entity(
            EntityCreate(key="e2", display_name="E2")
        )
        await entities_service.fields.create_field(
            e1, EntityFieldCreate(key="k", type=FieldType.TEXT, display_name="K")
        )
        await entities_service.fields.create_field(
            e2, EntityFieldCreate(key="k", type=FieldType.TEXT, display_name="K")
        )

    async def test_get_field_by_key(self, entities_service: EntityService) -> None:
        e = await entities_service.create_entity(
            EntityCreate(key="gfbk", display_name="E")
        )
        f = await entities_service.fields.create_field(
            e, EntityFieldCreate(key="alpha", type=FieldType.TEXT, display_name="Alpha")
        )
        got = await entities_service.fields.get_field_by_key(e, "alpha")
        assert got.id == f.id
        with pytest.raises(TracecatNotFoundError):
            await entities_service.fields.get_field_by_key(e, "missing")


@pytest.mark.anyio
class TestDefaultValueEdgeCases:
    async def test_clear_default_value(self, entities_service: EntityService) -> None:
        e = await entities_service.create_entity(
            EntityCreate(key="clr", display_name="Clr")
        )
        f = await entities_service.fields.create_field(
            e,
            EntityFieldCreate(
                key="flag",
                type=FieldType.BOOL,
                display_name="Flag",
                default_value=True,
            ),
        )
        updated = await entities_service.fields.update_field(
            f, EntityFieldUpdate(default_value=None)
        )
        assert updated.default_value is None

    async def test_bool_coercion_edge_cases(
        self, entities_service: EntityService
    ) -> None:
        e = await entities_service.create_entity(
            EntityCreate(key="bools", display_name="B")
        )
        f1 = await entities_service.fields.create_field(
            e,
            EntityFieldCreate(
                key="b1", type=FieldType.BOOL, display_name="b1", default_value="true"
            ),
        )
        f2 = await entities_service.fields.create_field(
            e,
            EntityFieldCreate(
                key="b2", type=FieldType.BOOL, display_name="b2", default_value=0
            ),
        )
        f3 = await entities_service.fields.create_field(
            e,
            EntityFieldCreate(
                key="b3", type=FieldType.BOOL, display_name="b3", default_value="0"
            ),
        )
        assert f1.default_value is True
        assert f2.default_value is False
        # Current behavior: bool("0") is True
        assert f3.default_value is True

    async def test_date_and_datetime_parsing(
        self, entities_service: EntityService
    ) -> None:
        e = await entities_service.create_entity(
            EntityCreate(key="dates", display_name="D")
        )
        df = await entities_service.fields.create_field(
            e,
            EntityFieldCreate(
                key="d",
                type=FieldType.DATE,
                display_name="d",
                default_value="2024-01-01",
            ),
        )
        dtf = await entities_service.fields.create_field(
            e,
            EntityFieldCreate(
                key="dt",
                type=FieldType.DATETIME,
                display_name="dt",
                default_value="2024-01-01T12:34:56",
            ),
        )
        # Stored as ISO strings
        assert isinstance(df.default_value, str)
        assert df.default_value == "2024-01-01"
        assert isinstance(dtf.default_value, str)
        assert dtf.default_value.startswith("2024-01-01T12:34:56")

    async def test_number_coercion(self, entities_service: EntityService) -> None:
        e = await entities_service.create_entity(
            EntityCreate(key="nums", display_name="N")
        )
        nf = await entities_service.fields.create_field(
            e,
            EntityFieldCreate(
                key="pi",
                type=FieldType.NUMBER,
                display_name="pi",
                default_value="3.14159",
            ),
        )
        assert isinstance(nf.default_value, float)
        assert abs(nf.default_value - 3.14159) < 1e-9


@pytest.mark.anyio
class TestCascadeDeletion:
    async def test_delete_entity_cascades_fields_and_options(
        self, session: AsyncSession, entities_service: EntityService
    ) -> None:
        e = await entities_service.create_entity(
            EntityCreate(key="cascade", display_name="C")
        )
        f = await entities_service.fields.create_field(
            e,
            EntityFieldCreate(
                key="s",
                type=FieldType.SELECT,
                display_name="S",
                options=[
                    EntityFieldOptionCreate(label="A", key="a"),
                    EntityFieldOptionCreate(label="B", key="b"),
                ],
                default_value="a",
            ),
        )
        # Verify exists
        assert (
            await session.exec(select(EntityField).where(EntityField.entity_id == e.id))
        ).first() is not None
        # Delete entity
        await entities_service.delete_entity(e)
        # Fields should be gone
        assert (
            await session.exec(select(EntityField).where(EntityField.entity_id == e.id))
        ).first() is None
        # Options should also be gone
        assert (
            await session.exec(
                select(EntityFieldOption).where(EntityFieldOption.field_id == f.id)
            )
        ).first() is None


@pytest.mark.anyio
class TestEntityFieldValidation:
    async def test_create_field_options_not_allowed_for_type(
        self, entities_service: EntityService
    ) -> None:
        _ = await entities_service.create_entity(
            EntityCreate(key="sys_config", display_name="Sys Config")
        )
        with pytest.raises(ValueError):
            _ = EntityFieldCreate(
                key="threshold",
                type=FieldType.INTEGER,
                display_name="Threshold",
                options=[EntityFieldOptionCreate(label="A", key="a")],
            )

    async def test_create_field_select_default_not_in_options(
        self, entities_service: EntityService
    ) -> None:
        _ = await entities_service.create_entity(
            EntityCreate(key="proj", display_name="Project")
        )
        with pytest.raises(ValueError):
            _ = EntityFieldCreate(
                key="state",
                type=FieldType.SELECT,
                display_name="State",
                options=[EntityFieldOptionCreate(label="Open", key="open")],
                default_value="closed",  # not in options
            )

    async def test_create_field_multiselect_default_not_list(
        self, entities_service: EntityService
    ) -> None:
        _ = await entities_service.create_entity(
            EntityCreate(key="asset2", display_name="Asset2")
        )
        with pytest.raises(ValueError):
            _ = EntityFieldCreate(
                key="labels",
                type=FieldType.MULTI_SELECT,
                display_name="Labels",
                options=[
                    EntityFieldOptionCreate(label="Red", key="red"),
                    EntityFieldOptionCreate(label="Blue", key="blue"),
                ],
                default_value="red",  # must be list
            )

    async def test_create_field_multiselect_default_missing_option_keys(
        self, entities_service: EntityService
    ) -> None:
        _ = await entities_service.create_entity(
            EntityCreate(key="ticket", display_name="Ticket")
        )
        with pytest.raises(ValueError):
            _ = EntityFieldCreate(
                key="tags",
                type=FieldType.MULTI_SELECT,
                display_name="Tags",
                options=[
                    EntityFieldOptionCreate(label="Bug", key="bug"),
                    EntityFieldOptionCreate(label="Feature", key="feature"),
                ],
                default_value=["bug", "unknown"],  # unknown not in options
            )

    async def test_create_field_json_default_invalid_type(
        self, entities_service: EntityService
    ) -> None:
        _ = await entities_service.create_entity(
            EntityCreate(key="profile", display_name="Profile")
        )
        with pytest.raises(ValueError):
            _ = EntityFieldCreate(
                key="metadata",
                type=FieldType.JSON,
                display_name="Metadata",
                default_value="not-json",  # must be dict or list
            )

    async def test_create_field_datetime_invalid_default(
        self, entities_service: EntityService
    ) -> None:
        _ = await entities_service.create_entity(
            EntityCreate(key="events", display_name="Events")
        )
        with pytest.raises(ValueError):
            _ = EntityFieldCreate(
                key="start",
                type=FieldType.DATETIME,
                display_name="Start",
                default_value="not-a-date",
            )

    async def test_update_field_options_duplicate_keys(
        self, entities_service: EntityService
    ) -> None:
        entity = await entities_service.create_entity(
            EntityCreate(key="doc", display_name="Document")
        )
        _ = await entities_service.fields.create_field(
            entity,
            EntityFieldCreate(
                key="category",
                type=FieldType.SELECT,
                display_name="Category",
                options=[
                    EntityFieldOptionCreate(label="One", key="one"),
                    EntityFieldOptionCreate(label="Two", key="two"),
                ],
                default_value="one",
            ),
        )
        with pytest.raises(ValueError):
            _ = EntityFieldUpdate(
                options=[
                    EntityFieldOptionCreate(label="One", key="dup"),
                    EntityFieldOptionCreate(label="Two", key="dup"),  # duplicate key
                ]
            )

    async def test_get_entity_by_key_not_found(
        self, entities_service: EntityService
    ) -> None:
        with pytest.raises(TracecatNotFoundError):
            await entities_service.get_entity_by_key("does_not_exist")
