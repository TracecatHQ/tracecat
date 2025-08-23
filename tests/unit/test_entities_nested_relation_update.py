"""Test nested relation updates in entity records."""

import pytest
from sqlmodel.ext.asyncio.session import AsyncSession

from tracecat.entities.enums import RelationType
from tracecat.entities.models import RelationDefinitionCreate
from tracecat.entities.service import CustomEntitiesService
from tracecat.entities.types import FieldType
from tracecat.types.auth import Role

pytestmark = pytest.mark.usefixtures("db")


@pytest.mark.anyio
class TestNestedRelationUpdate:
    """Test updating related records through nested updates."""

    async def test_update_record_with_nested_one_to_one(
        self,
        session: AsyncSession,
        svc_admin_role: Role,
    ):
        """Test updating a record with nested one_to_one relation data."""
        service = CustomEntitiesService(session, svc_admin_role)

        # Create two entities: employee and manager
        employee_entity = await service.create_entity(
            name="test_employee_update",
            display_name="Test Employee",
        )
        manager_entity = await service.create_entity(
            name="test_manager_update",
            display_name="Test Manager",
        )

        # Add fields to manager entity
        await service.create_field(
            entity_id=manager_entity.id,
            field_key="name",
            field_type=FieldType.TEXT,
            display_name="Manager Name",
        )
        await service.create_field(
            entity_id=manager_entity.id,
            field_key="department",
            field_type=FieldType.TEXT,
            display_name="Department",
        )

        # Add fields to employee entity
        await service.create_field(
            entity_id=employee_entity.id,
            field_key="employee_name",
            field_type=FieldType.TEXT,
            display_name="Employee Name",
        )

        # Add one_to_one relation from employee to manager
        await service.create_relation(
            employee_entity.id,
            RelationDefinitionCreate(
                source_key="manager",
                display_name="Manager",
                relation_type=RelationType.ONE_TO_ONE,
                target_entity_id=manager_entity.id,
            ),
        )

        # Create a manager record
        manager_record = await service.create_record(
            entity_id=manager_entity.id,
            data={
                "name": "John Manager",
                "department": "Engineering",
            },
        )

        # Create an employee record with the manager relation
        employee_record = await service.create_record(
            entity_id=employee_entity.id,
            data={
                "employee_name": "Jane Employee",
                "manager": manager_record.id,
            },
        )

        # Update the employee record with nested manager data
        updated_employee = await service.update_record(
            record_id=employee_record.id,
            updates={
                "employee_name": "Jane Senior Employee",
                "manager": {
                    "name": "John Senior Manager",
                    "department": "Advanced Engineering",
                },
            },
        )

        # Verify the employee record was updated
        assert updated_employee.field_data["employee_name"] == "Jane Senior Employee"

        # Verify the manager record was updated through the nested update
        updated_manager = await service.get_record(manager_record.id)
        assert updated_manager.field_data["name"] == "John Senior Manager"
        assert updated_manager.field_data["department"] == "Advanced Engineering"

    async def test_update_preserves_relation_link(
        self,
        session: AsyncSession,
        svc_admin_role: Role,
    ):
        """Test that updating nested relation data preserves the relation link."""
        service = CustomEntitiesService(session, svc_admin_role)

        # Create entities
        project_entity = await service.create_entity(
            name="test_project_link",
            display_name="Test Project",
        )
        owner_entity = await service.create_entity(
            name="test_owner_link",
            display_name="Test Owner",
        )

        # Add fields
        await service.create_field(
            entity_id=owner_entity.id,
            field_key="owner_name",
            field_type=FieldType.TEXT,
            display_name="Owner Name",
        )
        await service.create_field(
            entity_id=project_entity.id,
            field_key="project_name",
            field_type=FieldType.TEXT,
            display_name="Project Name",
        )

        # Add relation
        await service.create_relation(
            project_entity.id,
            RelationDefinitionCreate(
                source_key="owner",
                display_name="Owner",
                relation_type=RelationType.ONE_TO_ONE,
                target_entity_id=owner_entity.id,
            ),
        )

        # Create records
        owner_record = await service.create_record(
            entity_id=owner_entity.id,
            data={"owner_name": "Initial Owner"},
        )
        project_record = await service.create_record(
            entity_id=project_entity.id,
            data={
                "project_name": "Test Project",
                "owner": owner_record.id,
            },
        )

        # Get initial relation links
        from sqlmodel import select

        from tracecat.db.schemas import RecordRelationLink

        initial_link_stmt = select(RecordRelationLink).where(
            RecordRelationLink.source_record_id == project_record.id
        )
        initial_link_result = await session.exec(initial_link_stmt)
        initial_link = initial_link_result.first()

        assert initial_link is not None
        assert initial_link.target_record_id == owner_record.id

        # Update with nested data
        await service.update_record(
            record_id=project_record.id,
            updates={
                "project_name": "Updated Project",
                "owner": {"owner_name": "Updated Owner"},
            },
        )

        # Verify the link is preserved (same target record ID)
        updated_link_stmt = select(RecordRelationLink).where(
            RecordRelationLink.source_record_id == project_record.id
        )
        updated_link_result = await session.exec(updated_link_stmt)
        updated_link = updated_link_result.first()

        assert updated_link is not None
        assert updated_link.id == initial_link.id  # Same link record
        assert updated_link.target_record_id == owner_record.id  # Same target

        # Verify the owner data was updated
        updated_owner = await service.get_record(owner_record.id)
        assert updated_owner.field_data["owner_name"] == "Updated Owner"

    async def test_update_ignores_non_dict_relation_values(
        self,
        session: AsyncSession,
        svc_admin_role: Role,
    ):
        """Test that non-dict values for relations are ignored (link remains immutable)."""
        service = CustomEntitiesService(session, svc_admin_role)

        # Create entities
        item_entity = await service.create_entity(
            name="test_item_ignore",
            display_name="Test Item",
        )
        category_entity = await service.create_entity(
            name="test_category_ignore",
            display_name="Test Category",
        )

        # Add fields
        await service.create_field(
            entity_id=category_entity.id,
            field_key="category_name",
            field_type=FieldType.TEXT,
            display_name="Category Name",
        )
        await service.create_field(
            entity_id=item_entity.id,
            field_key="item_name",
            field_type=FieldType.TEXT,
            display_name="Item Name",
        )

        # Add relation
        await service.create_relation(
            item_entity.id,
            RelationDefinitionCreate(
                source_key="category",
                display_name="Category",
                relation_type=RelationType.ONE_TO_ONE,
                target_entity_id=category_entity.id,
            ),
        )

        # Create records
        category1 = await service.create_record(
            entity_id=category_entity.id,
            data={"category_name": "Category 1"},
        )
        category2 = await service.create_record(
            entity_id=category_entity.id,
            data={"category_name": "Category 2"},
        )
        item_record = await service.create_record(
            entity_id=item_entity.id,
            data={
                "item_name": "Test Item",
                "category": category1.id,
            },
        )

        # Try to update with a UUID (should be ignored - link is immutable)
        await service.update_record(
            record_id=item_record.id,
            updates={
                "item_name": "Updated Item",
                "category": str(category2.id),  # This should be ignored
            },
        )

        # Verify the relation link is unchanged
        from sqlmodel import select

        from tracecat.db.schemas import RecordRelationLink

        link_stmt = select(RecordRelationLink).where(
            RecordRelationLink.source_record_id == item_record.id
        )
        link_result = await session.exec(link_stmt)
        link = link_result.first()

        assert link is not None
        assert link.target_record_id == category1.id  # Still points to category1

        # Verify the item was updated but relation unchanged
        updated_item = await service.get_record(item_record.id)
        assert updated_item.field_data["item_name"] == "Updated Item"

    async def test_update_with_empty_nested_relation(
        self,
        session: AsyncSession,
        svc_admin_role: Role,
    ):
        """Test that empty nested relation updates are handled gracefully."""
        service = CustomEntitiesService(session, svc_admin_role)

        # Create entities
        task_entity = await service.create_entity(
            name="test_task_empty",
            display_name="Test Task",
        )
        assignee_entity = await service.create_entity(
            name="test_assignee_empty",
            display_name="Test Assignee",
        )

        # Add fields
        await service.create_field(
            entity_id=assignee_entity.id,
            field_key="assignee_name",
            field_type=FieldType.TEXT,
            display_name="Assignee Name",
        )
        await service.create_field(
            entity_id=task_entity.id,
            field_key="task_name",
            field_type=FieldType.TEXT,
            display_name="Task Name",
        )

        # Add relation
        await service.create_relation(
            task_entity.id,
            RelationDefinitionCreate(
                source_key="assignee",
                display_name="Assignee",
                relation_type=RelationType.ONE_TO_ONE,
                target_entity_id=assignee_entity.id,
            ),
        )

        # Create records
        assignee_record = await service.create_record(
            entity_id=assignee_entity.id,
            data={"assignee_name": "Original Assignee"},
        )
        task_record = await service.create_record(
            entity_id=task_entity.id,
            data={
                "task_name": "Test Task",
                "assignee": assignee_record.id,
            },
        )

        # Update with empty nested data (should not change assignee)
        await service.update_record(
            record_id=task_record.id,
            updates={
                "task_name": "Updated Task",
                "assignee": {},  # Empty nested update
            },
        )

        # Verify assignee record is unchanged
        assignee = await service.get_record(assignee_record.id)
        assert assignee.field_data["assignee_name"] == "Original Assignee"

        # Verify task was updated
        updated_task = await service.get_record(task_record.id)
        assert updated_task.field_data["task_name"] == "Updated Task"
