"""Tests for NestedUpdateValidator in validation.py."""

from uuid import uuid4

import pytest
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from tracecat.db.schemas import Entity, FieldMetadata, Record, RecordRelationLink
from tracecat.entities.enums import RelationKind
from tracecat.entities.types import FieldType
from tracecat.entities.validation import NestedUpdateValidator
from tracecat.types.exceptions import TracecatNotFoundError, TracecatValidationError

pytestmark = pytest.mark.usefixtures("db")


@pytest.fixture
async def entity_with_relations(session: AsyncSession, svc_workspace) -> Entity:
    """Create an entity with relation fields."""
    entity = Entity(
        id=uuid4(),
        name="test_entity",
        display_name="Test Entity",
        owner_id=svc_workspace.id,
        is_active=True,
    )
    session.add(entity)

    # Add regular field
    text_field = FieldMetadata(
        id=uuid4(),
        entity_id=entity.id,
        field_key="text_col",
        field_type=FieldType.TEXT,
        display_name="Text Column",
        is_active=True,
    )
    session.add(text_field)

    # Add relation field
    relation_field = FieldMetadata(
        id=uuid4(),
        entity_id=entity.id,
        field_key="manager",
        field_type=FieldType.RELATION_BELONGS_TO,
        display_name="Manager",
        relation_kind=RelationKind.ONE_TO_ONE,
        target_entity_id=entity.id,  # Self-reference for testing
        is_active=True,
    )
    session.add(relation_field)

    await session.commit()
    return entity


@pytest.fixture
async def linked_records(
    session: AsyncSession, entity_with_relations: Entity, svc_workspace
) -> tuple[Record, Record, RecordRelationLink]:
    """Create two linked records."""
    # Get the relation field
    stmt = select(FieldMetadata).where(
        FieldMetadata.entity_id == entity_with_relations.id,
        FieldMetadata.field_key == "manager",
    )
    result = await session.exec(stmt)
    relation_field = result.first()

    # Create main record
    main_record = Record(
        id=uuid4(),
        entity_id=entity_with_relations.id,
        field_data={"text_col": "main"},
        owner_id=svc_workspace.id,
    )
    session.add(main_record)

    # Create target record
    target_record = Record(
        id=uuid4(),
        entity_id=entity_with_relations.id,
        field_data={"text_col": "target"},
        owner_id=svc_workspace.id,
    )
    session.add(target_record)

    # Create relation link
    link = RecordRelationLink(
        id=uuid4(),
        source_record_id=main_record.id,
        source_field_id=relation_field.id,
        target_record_id=target_record.id,
        source_entity_id=entity_with_relations.id,
        target_entity_id=entity_with_relations.id,
        owner_id=svc_workspace.id,
    )
    session.add(link)

    await session.commit()
    return main_record, target_record, link


@pytest.mark.anyio
class TestNestedUpdateValidator:
    """Test NestedUpdateValidator functionality."""

    async def test_validate_simple_update_no_relations(
        self, session: AsyncSession, entity_with_relations: Entity, svc_workspace
    ):
        """Test validation of simple update without relation changes."""
        # Create a record
        record = Record(
            id=uuid4(),
            entity_id=entity_with_relations.id,
            field_data={"text_col": "original"},
            owner_id=svc_workspace.id,
        )
        session.add(record)
        await session.commit()

        # Create validator
        validator = NestedUpdateValidator(session, svc_workspace.id)

        # Plan simple update
        updates = {"text_col": "updated"}
        plan = await validator.validate_and_plan_updates(record.id, updates)

        # Verify plan
        assert len(plan.steps) == 1
        assert plan.steps[0].record_id == record.id
        assert plan.steps[0].field_updates == {"text_col": "updated"}
        assert plan.steps[0].depth == 0

    async def test_validate_nested_relation_update(
        self,
        session: AsyncSession,
        linked_records: tuple[Record, Record, RecordRelationLink],
        svc_workspace,
    ):
        """Test validation of nested relation update."""
        main_record, target_record, link = linked_records

        # Create validator
        validator = NestedUpdateValidator(session, svc_workspace.id)

        # Plan nested update
        updates = {
            "text_col": "main_updated",
            "manager": {"text_col": "target_updated"},
        }
        plan = await validator.validate_and_plan_updates(main_record.id, updates)

        # Verify plan has both updates
        assert len(plan.steps) == 2

        # Check main record update
        main_step = next(s for s in plan.steps if s.record_id == main_record.id)
        assert main_step.field_updates == {"text_col": "main_updated"}
        assert main_step.depth == 0

        # Check target record update
        target_step = next(s for s in plan.steps if s.record_id == target_record.id)
        assert target_step.field_updates == {"text_col": "target_updated"}
        assert target_step.depth == 1

        # Check relation link is tracked
        assert (main_record.id, link.source_field_id) in plan.relation_links
        assert (
            plan.relation_links[(main_record.id, link.source_field_id)]
            == target_record.id
        )

    async def test_max_depth_exceeded(
        self, session: AsyncSession, entity_with_relations: Entity, svc_workspace
    ):
        """Test that max depth is enforced."""
        # Create chain of linked records
        records = []
        for i in range(4):
            record = Record(
                id=uuid4(),
                entity_id=entity_with_relations.id,
                field_data={"text_col": f"record_{i}"},
                owner_id=svc_workspace.id,
            )
            session.add(record)
            records.append(record)

        # Get relation field
        stmt = select(FieldMetadata).where(
            FieldMetadata.entity_id == entity_with_relations.id,
            FieldMetadata.field_key == "manager",
        )
        result = await session.exec(stmt)
        relation_field = result.first()

        # Create chain of links
        for i in range(3):
            link = RecordRelationLink(
                id=uuid4(),
                source_record_id=records[i].id,
                source_field_id=relation_field.id,
                target_record_id=records[i + 1].id,
                source_entity_id=entity_with_relations.id,
                target_entity_id=entity_with_relations.id,
                owner_id=svc_workspace.id,
            )
            session.add(link)

        await session.commit()

        # Create validator
        validator = NestedUpdateValidator(session, svc_workspace.id)

        # Try to update with deep nesting (exceeds MAX_RELATION_DEPTH=2)
        updates = {
            "text_col": "level_0",
            "manager": {
                "text_col": "level_1",
                "manager": {
                    "text_col": "level_2",
                    "manager": {"text_col": "level_3"},  # This exceeds depth
                },
            },
        }

        with pytest.raises(TracecatValidationError) as exc_info:
            await validator.validate_and_plan_updates(records[0].id, updates)

        assert "Maximum relation depth" in str(exc_info.value)

    async def test_circular_reference_detection(
        self, session: AsyncSession, entity_with_relations: Entity, svc_workspace
    ):
        """Test that circular references are detected and handled."""
        # Create two records that reference each other
        record1 = Record(
            id=uuid4(),
            entity_id=entity_with_relations.id,
            field_data={"text_col": "record1"},
            owner_id=svc_workspace.id,
        )
        record2 = Record(
            id=uuid4(),
            entity_id=entity_with_relations.id,
            field_data={"text_col": "record2"},
            owner_id=svc_workspace.id,
        )
        session.add(record1)
        session.add(record2)

        # Get relation field
        stmt = select(FieldMetadata).where(
            FieldMetadata.entity_id == entity_with_relations.id,
            FieldMetadata.field_key == "manager",
        )
        result = await session.exec(stmt)
        relation_field = result.first()

        # Create circular links
        link1 = RecordRelationLink(
            id=uuid4(),
            source_record_id=record1.id,
            source_field_id=relation_field.id,
            target_record_id=record2.id,
            source_entity_id=entity_with_relations.id,
            target_entity_id=entity_with_relations.id,
            owner_id=svc_workspace.id,
        )
        link2 = RecordRelationLink(
            id=uuid4(),
            source_record_id=record2.id,
            source_field_id=relation_field.id,
            target_record_id=record1.id,
            source_entity_id=entity_with_relations.id,
            target_entity_id=entity_with_relations.id,
            owner_id=svc_workspace.id,
        )
        session.add(link1)
        session.add(link2)
        await session.commit()

        # Create validator
        validator = NestedUpdateValidator(session, svc_workspace.id)

        # Try update with circular reference
        updates = {
            "text_col": "updated1",
            "manager": {
                "text_col": "updated2",
                "manager": {"text_col": "back_to_1"},  # This would be circular
            },
        }

        # Should not raise, but should skip the circular update
        plan = await validator.validate_and_plan_updates(record1.id, updates)

        # Verify only two updates in plan (circular reference skipped)
        assert len(plan.steps) == 2
        assert record1.id in plan.visited_records
        assert record2.id in plan.visited_records

    async def test_json_depth_validation(
        self, session: AsyncSession, entity_with_relations: Entity, svc_workspace
    ):
        """Test that JSON depth is validated."""
        record = Record(
            id=uuid4(),
            entity_id=entity_with_relations.id,
            field_data={},
            owner_id=svc_workspace.id,
        )
        session.add(record)
        await session.commit()

        validator = NestedUpdateValidator(session, svc_workspace.id)

        # Create deeply nested JSON (exceeds MAX_JSON_DEPTH=5)
        deep_json = {
            "level1": {
                "level2": {"level3": {"level4": {"level5": {"level6": "too_deep"}}}}
            }
        }  # 6 levels

        updates = {"text_col": deep_json}

        with pytest.raises(TracecatValidationError) as exc_info:
            await validator.validate_and_plan_updates(record.id, updates)

        assert "exceeds maximum JSON depth" in str(exc_info.value)

    async def test_batch_load_relation_links(
        self,
        session: AsyncSession,
        linked_records: tuple[Record, Record, RecordRelationLink],
        svc_workspace,
    ):
        """Test batch loading of relation links."""
        main_record, target_record, link = linked_records

        validator = NestedUpdateValidator(session, svc_workspace.id)

        # Batch load links
        links_map = await validator.batch_load_relation_links(
            [main_record.id, target_record.id]
        )

        # Verify results
        assert main_record.id in links_map
        assert len(links_map[main_record.id]) == 1
        assert links_map[main_record.id][0].target_record_id == target_record.id

        # Target record has no outgoing links
        assert (
            target_record.id not in links_map
            or len(links_map.get(target_record.id, [])) == 0
        )

    async def test_nonexistent_record(self, session: AsyncSession, svc_workspace):
        """Test validation fails for nonexistent record."""
        validator = NestedUpdateValidator(session, svc_workspace.id)

        fake_id = uuid4()
        updates = {"text_col": "test"}

        with pytest.raises(TracecatNotFoundError) as exc_info:
            await validator.validate_and_plan_updates(fake_id, updates)

        assert str(fake_id) in str(exc_info.value) and "not found" in str(
            exc_info.value
        )

    async def test_empty_nested_update_skipped(
        self,
        session: AsyncSession,
        linked_records: tuple[Record, Record, RecordRelationLink],
        svc_workspace,
    ):
        """Test that empty nested updates are skipped."""
        main_record, target_record, link = linked_records

        validator = NestedUpdateValidator(session, svc_workspace.id)

        # Plan update with empty nested dict
        updates = {
            "text_col": "main_updated",
            "manager": {},  # Empty dict should be skipped
        }
        plan = await validator.validate_and_plan_updates(main_record.id, updates)

        # Should only have main record update
        assert len(plan.steps) == 1
        assert plan.steps[0].record_id == main_record.id
        assert plan.steps[0].field_updates == {"text_col": "main_updated"}

    async def test_max_update_size_exceeded(
        self, session: AsyncSession, entity_with_relations: Entity, svc_workspace
    ):
        """Test that max update size is enforced."""
        # Create many records to exceed the step limit
        records = []
        for _i in range(5):
            record = Record(
                id=uuid4(),
                entity_id=entity_with_relations.id,
                field_data={},
                owner_id=svc_workspace.id,
            )
            session.add(record)
            records.append(record)
        await session.commit()

        # Get relation field
        stmt = select(FieldMetadata).where(
            FieldMetadata.entity_id == entity_with_relations.id,
            FieldMetadata.field_key == "manager",
        )
        result = await session.exec(stmt)
        relation_field = result.first()

        # Link them in a chain
        for i in range(4):
            link = RecordRelationLink(
                id=uuid4(),
                source_record_id=records[i].id,
                source_field_id=relation_field.id,
                target_record_id=records[i + 1].id,
                source_entity_id=entity_with_relations.id,
                target_entity_id=entity_with_relations.id,
                owner_id=svc_workspace.id,
            )
            session.add(link)
        await session.commit()

        validator = NestedUpdateValidator(session, svc_workspace.id)
        # Override max size for testing
        validator.MAX_UPDATE_SIZE = 2

        # Create nested updates that would exceed step limit
        updates = {
            "text_col": "level_0",
            "manager": {
                "text_col": "level_1",
                "manager": {
                    "text_col": "level_2"  # This would create 3 steps
                },
            },
        }

        # This would create too many update steps
        with pytest.raises(TracecatValidationError) as exc_info:
            await validator.validate_and_plan_updates(records[0].id, updates)

        assert "exceeds maximum size" in str(exc_info.value)
