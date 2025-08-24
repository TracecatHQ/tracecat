"""Tests for relation update operators and array-add semantics."""

import pytest
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from tracecat.db.schemas import RecordRelationLink
from tracecat.entities.enums import RelationType
from tracecat.entities.models import RelationDefinitionCreate
from tracecat.entities.service import CustomEntitiesService
from tracecat.entities.types import FieldType
from tracecat.types.auth import Role

pytestmark = pytest.mark.usefixtures("db")


@pytest.mark.anyio
async def test_array_add_for_one_to_many(
    session: AsyncSession, svc_admin_role: Role
) -> None:
    svc = CustomEntitiesService(session, svc_admin_role)

    parent = await svc.create_entity(name="parent_aam", display_name="Parent")
    child = await svc.create_entity(name="child_aam", display_name="Child")

    await svc.create_field(
        entity_id=child.id,
        field_key="firstname",
        field_type=FieldType.TEXT,
        display_name="First Name",
    )

    await svc.create_relation(
        parent.id,
        RelationDefinitionCreate(
            source_key="children",
            display_name="Children",
            relation_type=RelationType.ONE_TO_MANY,
            target_entity_id=child.id,
        ),
    )

    parent_rec = await svc.create_record(parent.id, data={})

    # Add via plain array (additive)
    await svc.update_record(
        parent_rec.id,
        updates={
            "children": [
                {"firstname": "alpha"},
                {"firstname": "beta"},
            ]
        },
    )

    # Expect two links
    links = list(
        (
            await session.exec(
                select(RecordRelationLink).where(
                    RecordRelationLink.source_record_id == parent_rec.id
                )
            )
        ).all()
    )
    assert len(links) == 2


@pytest.mark.anyio
async def test_relation_add_set_remove_clear(
    session: AsyncSession, svc_admin_role: Role
) -> None:
    svc = CustomEntitiesService(session, svc_admin_role)

    a = await svc.create_entity(name="a_ops", display_name="A")
    b = await svc.create_entity(name="b_ops", display_name="B")

    await svc.create_field(
        entity_id=b.id,
        field_key="name",
        field_type=FieldType.TEXT,
        display_name="Name",
    )

    await svc.create_relation(
        a.id,
        RelationDefinitionCreate(
            source_key="bs",
            display_name="Bs",
            relation_type=RelationType.MANY_TO_MANY,
            target_entity_id=b.id,
        ),
    )

    a1 = await svc.create_record(a.id, data={})

    # __add: mix dict and UUID
    b1 = await svc.create_record(b.id, data={"name": "b1"})
    await svc.update_record(
        a1.id,
        updates={"bs__add": [str(b1.id), {"name": "b2"}]},
    )
    links_after_add = list(
        (
            await session.exec(
                select(RecordRelationLink).where(
                    RecordRelationLink.source_record_id == a1.id
                )
            )
        ).all()
    )
    assert len(links_after_add) == 2

    # __remove: remove b1
    await svc.update_record(a1.id, updates={"bs__remove": [str(b1.id)]})
    links_after_remove = list(
        (
            await session.exec(
                select(RecordRelationLink).where(
                    RecordRelationLink.source_record_id == a1.id
                )
            )
        ).all()
    )
    assert len(links_after_remove) == 1

    # __set: replace with new set [existing + new]
    remaining_target = links_after_remove[0].target_record_id
    await svc.update_record(
        a1.id,
        updates={"bs__set": [str(remaining_target), {"name": "b3"}]},
    )
    links_after_set = list(
        (
            await session.exec(
                select(RecordRelationLink).where(
                    RecordRelationLink.source_record_id == a1.id
                )
            )
        ).all()
    )
    assert len(links_after_set) == 2

    # __clear: remove all
    await svc.update_record(a1.id, updates={"bs__clear": True})
    links_after_clear = list(
        (
            await session.exec(
                select(RecordRelationLink).where(
                    RecordRelationLink.source_record_id == a1.id
                )
            )
        ).all()
    )
    assert len(links_after_clear) == 0


@pytest.mark.anyio
async def test_single_relation_set_and_clear(
    session: AsyncSession, svc_admin_role: Role
) -> None:
    svc = CustomEntitiesService(session, svc_admin_role)

    emp = await svc.create_entity(name="emp_ops", display_name="Emp")
    mgr = await svc.create_entity(name="mgr_ops", display_name="Mgr")

    await svc.create_field(
        entity_id=mgr.id,
        field_key="firstname",
        field_type=FieldType.TEXT,
        display_name="First Name",
    )

    await svc.create_relation(
        emp.id,
        RelationDefinitionCreate(
            source_key="manager",
            display_name="Manager",
            relation_type=RelationType.ONE_TO_ONE,
            target_entity_id=mgr.id,
        ),
    )

    emp_rec = await svc.create_record(emp.id, data={})

    # __set with dict
    await svc.update_record(emp_rec.id, updates={"manager__set": {"firstname": "d"}})
    links = list(
        (
            await session.exec(
                select(RecordRelationLink).where(
                    RecordRelationLink.source_record_id == emp_rec.id
                )
            )
        ).all()
    )
    assert len(links) == 1

    # __clear
    await svc.update_record(emp_rec.id, updates={"manager__clear": True})
    links2 = list(
        (
            await session.exec(
                select(RecordRelationLink).where(
                    RecordRelationLink.source_record_id == emp_rec.id
                )
            )
        ).all()
    )
    assert len(links2) == 0
