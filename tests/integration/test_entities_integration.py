import pytest
from sqlmodel.ext.asyncio.session import AsyncSession

from tracecat.entities.enums import FieldType
from tracecat.entities.models import (
    EntityCreate,
    EntityFieldCreate,
    EntityFieldOptionCreate,
    EntityFieldUpdate,
)
from tracecat.entities.service import EntityService
from tracecat.types.auth import Role
from tracecat.types.exceptions import TracecatNotFoundError

pytestmark = pytest.mark.usefixtures("db")


@pytest.mark.anyio
class TestEntitiesEndToEnd:
    async def test_full_crud_flow(self, session: AsyncSession, svc_role: Role) -> None:
        svc = EntityService(session=session, role=svc_role)

        # Create entity
        e = await svc.create_entity(
            EntityCreate(key="inventory", display_name="Inventory")
        )

        # Create numeric field
        f_num = await svc.fields.create_field(
            e,
            EntityFieldCreate(
                key="qty",
                type=FieldType.INTEGER,
                display_name="Quantity",
                default_value="10",
            ),
        )
        assert f_num.default_value == 10

        # Create select field with options
        f_sel = await svc.fields.create_field(
            e,
            EntityFieldCreate(
                key="state",
                type=FieldType.SELECT,
                display_name="State",
                options=[
                    EntityFieldOptionCreate(label="New", key="new"),
                    EntityFieldOptionCreate(label="Used", key="used"),
                ],
                default_value="new",
            ),
        )
        assert {opt.key for opt in f_sel.options} == {"new", "used"}

        # List fields and ensure options are present (eager-loaded)
        fields = await svc.fields.list_fields(e)
        by_key = {f.key: f for f in fields}
        assert "qty" in by_key and "state" in by_key
        assert {opt.key for opt in by_key["state"].options} == {"new", "used"}

        # Update field default and options
        f_num = await svc.fields.update_field(
            f_num, EntityFieldUpdate(default_value=25)
        )
        assert f_num.default_value == 25

        f_sel = await svc.fields.update_field(
            f_sel,
            EntityFieldUpdate(
                options=[
                    EntityFieldOptionCreate(label="NEW", key="new"),  # label change
                    EntityFieldOptionCreate(
                        label="Refurbished", key="refurb"
                    ),  # new key
                ]
            ),
        )
        assert {opt.key for opt in f_sel.options} == {"new", "refurb"}

        # Deactivate and re-activate field
        df = await svc.fields.deactivate_field(f_num)
        assert df.is_active is False
        af = await svc.fields.activate_field(f_num)
        assert af.is_active is True

        # Deactivate and re-activate entity
        de = await svc.deactivate_entity(e)
        assert de.is_active is False
        ae = await svc.activate_entity(e)
        assert ae.is_active is True

        # Delete field then entity
        await svc.fields.delete_field(f_num)
        with pytest.raises(TracecatNotFoundError):
            await svc.fields.get_field(e, f_num.id)

        await svc.delete_entity(e)
        with pytest.raises(TracecatNotFoundError):
            await svc.get_entity(e.id)


@pytest.mark.anyio
class TestSequentialOptionUpdates:
    async def test_sequential_option_updates(
        self, session: AsyncSession, svc_role: Role
    ) -> None:
        svc = EntityService(session=session, role=svc_role)
        e = await svc.create_entity(EntityCreate(key="catalog", display_name="Catalog"))
        f = await svc.fields.create_field(
            e,
            EntityFieldCreate(
                key="tier",
                type=FieldType.SELECT,
                display_name="Tier",
                options=[
                    EntityFieldOptionCreate(label="Bronze", key="bronze"),
                    EntityFieldOptionCreate(label="Silver", key="silver"),
                    EntityFieldOptionCreate(label="Gold", key="gold"),
                ],
                default_value="bronze",
            ),
        )

        # First update: rename silver->SILVER, add platinum
        f = await svc.fields.update_field(
            f,
            EntityFieldUpdate(
                options=[
                    EntityFieldOptionCreate(label="Bronze", key="bronze"),
                    EntityFieldOptionCreate(label="SILVER", key="silver"),
                    EntityFieldOptionCreate(label="Gold", key="gold"),
                    EntityFieldOptionCreate(label="Platinum", key="platinum"),
                ]
            ),
        )
        assert {o.key for o in f.options} == {"bronze", "silver", "gold", "platinum"}
        assert next(o for o in f.options if o.key == "silver").label == "SILVER"

        # Second update: remove bronze, keep others
        f = await svc.fields.update_field(
            f,
            EntityFieldUpdate(
                options=[
                    EntityFieldOptionCreate(label="SILVER", key="silver"),
                    EntityFieldOptionCreate(label="Gold", key="gold"),
                    EntityFieldOptionCreate(label="Platinum", key="platinum"),
                ]
            ),
        )
        assert {o.key for o in f.options} == {"silver", "gold", "platinum"}


@pytest.mark.anyio
class TestEntitiesServiceIntegration:
    async def test_list_fields_include_inactive(
        self, session: AsyncSession, svc_role: Role
    ) -> None:
        svc = EntityService(session=session, role=svc_role)
        e = await svc.create_entity(EntityCreate(key="orders", display_name="Orders"))

        await svc.fields.create_field(
            e, EntityFieldCreate(key="id", type=FieldType.TEXT, display_name="ID")
        )
        f2 = await svc.fields.create_field(
            e,
            EntityFieldCreate(
                key="archived", type=FieldType.BOOL, display_name="Archived"
            ),
        )

        # Deactivate one field
        await svc.fields.deactivate_field(f2)

        # Default list excludes inactive
        fields_default = await svc.fields.list_fields(e)
        keys_default = {f.key for f in fields_default}
        assert keys_default == {"id"}

        # Include inactive shows both
        fields_all = await svc.fields.list_fields(e, include_inactive=True)
        keys_all = {f.key for f in fields_all}
        assert keys_all == {"id", "archived"}

    async def test_get_by_key_helpers(
        self, session: AsyncSession, svc_role: Role
    ) -> None:
        svc = EntityService(session=session, role=svc_role)
        e = await svc.create_entity(EntityCreate(key="tickets", display_name="Tickets"))
        # get_entity_by_key
        efetch = await svc.get_entity_by_key("tickets")
        assert efetch.id == e.id

        f = await svc.fields.create_field(
            e, EntityFieldCreate(key="title", type=FieldType.TEXT, display_name="Title")
        )
        # get_field_by_key
        ffetch = await svc.fields.get_field_by_key(e, "title")
        assert ffetch.id == f.id

    async def test_slugification_and_option_key_autofill(
        self, session: AsyncSession, svc_role: Role
    ) -> None:
        svc = EntityService(session=session, role=svc_role)
        # Non-snake keys are normalized via slugify
        e = await svc.create_entity(EntityCreate(key="User Profile", display_name="UP"))
        assert e.key == "user_profile"

        # Field key normalization
        f = await svc.fields.create_field(
            e,
            EntityFieldCreate(
                key="Display Name",
                type=FieldType.TEXT,
                display_name="DN",
            ),
        )
        assert f.key == "display_name"

        # Option key autofill from label + slugify (key omitted)
        s = await svc.fields.create_field(
            e,
            EntityFieldCreate(
                key="state",
                type=FieldType.SELECT,
                display_name="State",
                options=[
                    EntityFieldOptionCreate(label="In Progress", key="in_progress")
                ],
                default_value="in_progress",
            ),
        )
        assert {opt.key for opt in s.options} == {"in_progress"}
