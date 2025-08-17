"""Test nested record creation in relations."""

import pytest
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from tracecat.db.schemas import RecordRelationLink
from tracecat.entities.models import RelationSettings, RelationType
from tracecat.entities.service import CustomEntitiesService
from tracecat.entities.types import FieldType
from tracecat.types.auth import Role

pytestmark = pytest.mark.usefixtures("db")


@pytest.mark.anyio
class TestNestedRecordCreation:
    """Test creating records with nested relation data."""

    async def test_create_record_with_nested_belongs_to(
        self,
        session: AsyncSession,
        svc_admin_role: Role,
    ):
        """Test creating a record with a nested belongs_to relation."""
        service = CustomEntitiesService(session, svc_admin_role)

        # Create two entities: employee and manager
        employee_entity = await service.create_entity(
            name="test_employee",
            display_name="Test Employee",
            description="Employee entity for testing",
        )

        manager_entity = await service.create_entity(
            name="test_manager",
            display_name="Test Manager",
            description="Manager entity for testing",
        )

        # Add fields to manager entity
        await service.create_field(
            entity_id=manager_entity.id,
            field_key="name",
            field_type=FieldType.TEXT,
            display_name="Name",
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
            field_key="name",
            field_type=FieldType.TEXT,
            display_name="Name",
        )
        await service.create_field(
            entity_id=employee_entity.id,
            field_key="title",
            field_type=FieldType.TEXT,
            display_name="Title",
        )

        # Add a relation field pointing to manager
        manager_relation_field = await service.create_relation_field(
            entity_id=employee_entity.id,
            field_key="manager",
            field_type=FieldType.RELATION_BELONGS_TO,
            display_name="Manager",
            relation_settings=RelationSettings(
                relation_type=RelationType.BELONGS_TO,
                target_entity_id=manager_entity.id,
            ),
        )

        # Create an employee with a nested manager (inline creation)
        employee_data = {
            "name": "John Doe",
            "title": "Software Engineer",
            "manager": {
                # This should create a new manager record inline
                "name": "Jane Smith",
                "department": "Engineering",
            },
        }

        # Create the employee record
        employee_record = await service.create_record(
            entity_id=employee_entity.id,
            data=employee_data,
        )

        # Verify the employee record was created
        assert employee_record.id is not None
        assert employee_record.field_data["name"] == "John Doe"
        assert employee_record.field_data["title"] == "Software Engineer"
        # Relations are not stored in field_data
        assert "manager" not in employee_record.field_data

        # Verify the relation link was created
        link_stmt = select(RecordRelationLink).where(
            RecordRelationLink.source_record_id == employee_record.id,
            RecordRelationLink.source_field_id == manager_relation_field.id,
        )
        result = await session.exec(link_stmt)
        link = result.first()

        assert link is not None
        assert link.source_entity_id == employee_entity.id
        assert link.target_entity_id == manager_entity.id

        # Verify the manager record was created
        manager_record = await service.get_record(link.target_record_id)
        assert manager_record.field_data["name"] == "Jane Smith"
        assert manager_record.field_data["department"] == "Engineering"

    async def test_create_record_with_nested_has_many(
        self,
        session: AsyncSession,
        svc_admin_role: Role,
    ):
        """Test creating a record with nested has_many relations."""
        service = CustomEntitiesService(session, svc_admin_role)

        # Create two entities: team and member
        team_entity = await service.create_entity(
            name="test_team",
            display_name="Test Team",
            description="Team entity for testing",
        )

        member_entity = await service.create_entity(
            name="test_member",
            display_name="Test Member",
            description="Member entity for testing",
        )

        # Add fields to member entity
        await service.create_field(
            entity_id=member_entity.id,
            field_key="name",
            field_type=FieldType.TEXT,
            display_name="Name",
        )
        await service.create_field(
            entity_id=member_entity.id,
            field_key="role",
            field_type=FieldType.TEXT,
            display_name="Role",
        )

        # Add fields to team entity
        await service.create_field(
            entity_id=team_entity.id,
            field_key="name",
            field_type=FieldType.TEXT,
            display_name="Team Name",
        )

        # Add a has_many relation field
        members_relation_field = await service.create_relation_field(
            entity_id=team_entity.id,
            field_key="members",
            field_type=FieldType.RELATION_HAS_MANY,
            display_name="Members",
            relation_settings=RelationSettings(
                relation_type=RelationType.HAS_MANY,
                target_entity_id=member_entity.id,
            ),
        )

        # Create a team with nested members (inline creation)
        team_data = {
            "name": "Engineering Team",
            "members": [
                {
                    # Create first member inline
                    "name": "Alice Johnson",
                    "role": "Senior Engineer",
                },
                {
                    # Create second member inline
                    "name": "Bob Wilson",
                    "role": "Junior Engineer",
                },
            ],
        }

        # Create the team record
        team_record = await service.create_record(
            entity_id=team_entity.id,
            data=team_data,
        )

        # Verify the team record was created
        assert team_record.id is not None
        assert team_record.field_data["name"] == "Engineering Team"
        assert "members" not in team_record.field_data

        # Verify the relation links were created
        links_stmt = select(RecordRelationLink).where(
            RecordRelationLink.source_record_id == team_record.id,
            RecordRelationLink.source_field_id == members_relation_field.id,
        )
        result = await session.exec(links_stmt)
        links = result.all()

        assert len(links) == 2

        # Verify the member records were created
        member_records = []
        for link in links:
            member = await service.get_record(link.target_record_id)
            member_records.append(member)

        # Check member data (order may vary)
        member_names = {r.field_data["name"] for r in member_records}
        member_roles = {r.field_data["role"] for r in member_records}

        assert "Alice Johnson" in member_names
        assert "Bob Wilson" in member_names
        assert "Senior Engineer" in member_roles
        assert "Junior Engineer" in member_roles

    async def test_mixed_nested_and_existing_records(
        self,
        session: AsyncSession,
        svc_admin_role: Role,
    ):
        """Test creating relations with both nested and existing records."""
        service = CustomEntitiesService(session, svc_admin_role)

        # Create project and task entities
        project_entity = await service.create_entity(
            name="test_project",
            display_name="Test Project",
        )

        task_entity = await service.create_entity(
            name="test_task",
            display_name="Test Task",
        )

        # Add fields
        await service.create_field(
            entity_id=task_entity.id,
            field_key="title",
            field_type=FieldType.TEXT,
            display_name="Title",
        )

        await service.create_field(
            entity_id=project_entity.id,
            field_key="name",
            field_type=FieldType.TEXT,
            display_name="Project Name",
        )

        # Add has_many relation
        await service.create_relation_field(
            entity_id=project_entity.id,
            field_key="tasks",
            field_type=FieldType.RELATION_HAS_MANY,
            display_name="Tasks",
            relation_settings=RelationSettings(
                relation_type=RelationType.HAS_MANY,
                target_entity_id=task_entity.id,
            ),
        )

        # Create an existing task first
        existing_task = await service.create_record(
            entity_id=task_entity.id,
            data={"title": "Existing Task"},
        )

        # Create project with mixed relations
        project_data = {
            "name": "Test Project",
            "tasks": [
                existing_task.id,  # Reference to existing record
                {
                    # Inline creation of new task
                    "title": "New Task Created Inline",
                },
            ],
        }

        # Create the project record
        project_record = await service.create_record(
            entity_id=project_entity.id,
            data=project_data,
        )

        # Verify relations were created
        links_stmt = select(RecordRelationLink).where(
            RecordRelationLink.source_record_id == project_record.id,
        )
        result = await session.exec(links_stmt)
        links = result.all()

        assert len(links) == 2

        # Verify both tasks exist and have correct data
        task_ids = {link.target_record_id for link in links}
        assert existing_task.id in task_ids

        for task_id in task_ids:
            task = await service.get_record(task_id)
            assert task.field_data["title"] in [
                "Existing Task",
                "New Task Created Inline",
            ]
