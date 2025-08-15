"""Integration tests for entity relations feature.

Tests the complete relation fields implementation including:
- Relation field creation
- Relations set at record creation time
- Validation of immutable relations
"""

import uuid

import pytest
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from tracecat.db.schemas import RecordRelationLink
from tracecat.entities.enums import RelationKind
from tracecat.entities.models import RelationSettings, RelationType
from tracecat.entities.service import CustomEntitiesService
from tracecat.entities.types import FieldType
from tracecat.types.auth import Role
from tracecat.types.exceptions import TracecatNotFoundError

pytestmark = pytest.mark.usefixtures("db")


@pytest.fixture
async def entities_service(
    session: AsyncSession, svc_admin_role: Role
) -> CustomEntitiesService:
    """Create an entities service instance with admin role."""
    return CustomEntitiesService(session=session, role=svc_admin_role)


@pytest.fixture(scope="function")
async def customer_entity(entities_service: CustomEntitiesService):
    """Create customer entity for testing - fresh for each test."""
    entity = await entities_service.create_entity(
        name="customer",
        display_name="Customer",
        description="Customer entity for testing",
    )

    # Add some basic fields
    await entities_service.create_field(
        entity_id=entity.id,
        field_key="name",
        field_type=FieldType.TEXT,
        display_name="Name",
    )

    await entities_service.create_field(
        entity_id=entity.id,
        field_key="email",
        field_type=FieldType.TEXT,
        display_name="Email",
    )

    return entity


@pytest.fixture(scope="function")
async def organization_entity(entities_service: CustomEntitiesService):
    """Create organization entity for testing - fresh for each test."""
    entity = await entities_service.create_entity(
        name="organization",
        display_name="Organization",
        description="Organization entity for testing",
    )

    # Add some basic fields
    await entities_service.create_field(
        entity_id=entity.id,
        field_key="name",
        field_type=FieldType.TEXT,
        display_name="Name",
    )

    await entities_service.create_field(
        entity_id=entity.id,
        field_key="industry",
        field_type=FieldType.TEXT,
        display_name="Industry",
    )

    return entity


@pytest.mark.anyio
class TestEntityRelations:
    """Integration tests for entity relations."""

    async def test_create_unidirectional_relation_fields(
        self,
        entities_service: CustomEntitiesService,
        customer_entity,
        organization_entity,
    ):
        """Test creation of unidirectional relation fields.

        Note: Due to the nesting policy, we can only create one direction
        of the relation - either belongs_to OR has_many, not both.
        """
        # Create belongs_to field (customer -> organization)
        relation_settings = RelationSettings(
            relation_type=RelationType.BELONGS_TO,
            target_entity_id=organization_entity.id,
        )

        belongs_to_field = await entities_service.create_relation_field(
            entity_id=customer_entity.id,
            field_key="organization",
            field_type=FieldType.RELATION_BELONGS_TO,
            display_name="Organization",
            relation_settings=relation_settings,
        )

        # Verify belongs_to field
        assert belongs_to_field.field_type == FieldType.RELATION_BELONGS_TO
        assert belongs_to_field.entity_id == customer_entity.id
        assert belongs_to_field.relation_kind == RelationKind.ONE_TO_ONE
        assert belongs_to_field.target_entity_id == organization_entity.id

        # Try to create has_many field in the opposite direction - should fail
        # because customer entity now has a relation field pointing to organization
        has_many_settings = RelationSettings(
            relation_type=RelationType.HAS_MANY,
            target_entity_id=customer_entity.id,
        )

        with pytest.raises(ValueError, match="Cannot create relation"):
            await entities_service.create_relation_field(
                entity_id=organization_entity.id,
                field_key="customers",
                field_type=FieldType.RELATION_HAS_MANY,
                display_name="Customers",
                relation_settings=has_many_settings,
            )

    async def test_create_relation_field_with_settings(
        self,
        entities_service: CustomEntitiesService,
        customer_entity,
        organization_entity,
    ):
        """Test creating a single relation field with settings."""
        relation_settings = RelationSettings(
            relation_type=RelationType.BELONGS_TO,
            target_entity_id=organization_entity.id,
        )

        field = await entities_service.create_relation_field(
            entity_id=customer_entity.id,
            field_key="org",
            field_type=FieldType.RELATION_BELONGS_TO,
            display_name="Organization",
            relation_settings=relation_settings,
            description="Customer's organization",
        )

        assert field.field_type == FieldType.RELATION_BELONGS_TO
        assert field.relation_kind == RelationKind.ONE_TO_ONE
        assert field.target_entity_id == organization_entity.id
        # v1: cascade_delete is always true, field removed

    async def test_create_record_with_belongs_to_relation(
        self,
        entities_service: CustomEntitiesService,
        customer_entity,
        organization_entity,
        session: AsyncSession,
    ):
        """Test creating a record with a belongs-to relation set at creation time."""
        # Create belongs_to field
        relation_settings = RelationSettings(
            relation_type=RelationType.BELONGS_TO,
            target_entity_id=organization_entity.id,
        )

        belongs_to_field = await entities_service.create_relation_field(
            entity_id=customer_entity.id,
            field_key="organization",
            field_type=FieldType.RELATION_BELONGS_TO,
            display_name="Organization",
            relation_settings=relation_settings,
        )

        # Create organization first
        org = await entities_service.create_record(
            entity_id=organization_entity.id,
            data={"name": "Acme Corp", "industry": "Technology"},
        )

        # Create customer with relation set at creation
        customer = await entities_service.create_record(
            entity_id=customer_entity.id,
            data={
                "name": "John Doe",
                "email": "john@example.com",
                "organization": str(org.id),  # Set relation at creation
            },
        )

        # Verify link was created
        link_stmt = select(RecordRelationLink).where(
            RecordRelationLink.source_record_id == customer.id,
            RecordRelationLink.target_record_id == org.id,
        )
        link_result = await session.exec(link_stmt)
        link = link_result.first()

        assert link is not None
        assert link.source_field_id == belongs_to_field.id
        assert link.source_entity_id == customer_entity.id
        assert link.target_entity_id == organization_entity.id

    async def test_create_record_with_has_many_relation(
        self,
        entities_service: CustomEntitiesService,
        customer_entity,
        organization_entity,
        session: AsyncSession,
    ):
        """Test creating a record with a has-many relation set at creation time."""
        # Create has_many field
        has_many_settings = RelationSettings(
            relation_type=RelationType.HAS_MANY,
            target_entity_id=customer_entity.id,
        )

        has_many_field = await entities_service.create_relation_field(
            entity_id=organization_entity.id,
            field_key="customers",
            field_type=FieldType.RELATION_HAS_MANY,
            display_name="Customers",
            relation_settings=has_many_settings,
        )

        # Create customers first
        customers = []
        for i in range(3):
            customer = await entities_service.create_record(
                entity_id=customer_entity.id,
                data={"name": f"Customer {i}", "email": f"customer{i}@example.com"},
            )
            customers.append(customer)

        # Create organization with has_many relation set at creation
        org = await entities_service.create_record(
            entity_id=organization_entity.id,
            data={
                "name": "Acme Corp",
                "industry": "Technology",
                "customers": [str(c.id) for c in customers],  # Set relation at creation
            },
        )

        # Verify links were created
        link_stmt = select(RecordRelationLink).where(
            RecordRelationLink.source_record_id == org.id,
            RecordRelationLink.source_field_id == has_many_field.id,
        )
        link_result = await session.exec(link_stmt)
        links = list(link_result.all())

        assert len(links) == 3
        linked_customer_ids = {link.target_record_id for link in links}
        expected_customer_ids = {c.id for c in customers}
        assert linked_customer_ids == expected_customer_ids

    async def test_create_record_with_invalid_relation(
        self,
        entities_service: CustomEntitiesService,
        customer_entity,
        organization_entity,
    ):
        """Test that invalid relation IDs are rejected at creation."""
        # Create belongs_to field
        relation_settings = RelationSettings(
            relation_type=RelationType.BELONGS_TO,
            target_entity_id=organization_entity.id,
        )

        await entities_service.create_relation_field(
            entity_id=customer_entity.id,
            field_key="organization",
            field_type=FieldType.RELATION_BELONGS_TO,
            display_name="Organization",
            relation_settings=relation_settings,
        )

        # Try to create customer with non-existent organization ID
        fake_org_id = uuid.uuid4()
        with pytest.raises(TracecatNotFoundError, match="not found"):
            await entities_service.create_record(
                entity_id=customer_entity.id,
                data={
                    "name": "John Doe",
                    "email": "john@example.com",
                    "organization": str(fake_org_id),
                },
            )

    async def test_cross_workspace_rejection(
        self,
        session: AsyncSession,
        svc_admin_role: Role,
        customer_entity,
        organization_entity,
    ):
        """Verify cross-workspace relations are rejected."""
        # Create service with first workspace
        service1 = CustomEntitiesService(session=session, role=svc_admin_role)

        # Create a different workspace role
        other_workspace_role = Role(
            type="service",
            user_id=uuid.uuid4(),
            workspace_id=uuid.uuid4(),  # Different workspace
            service_id="tracecat-service",
            access_level=svc_admin_role.access_level,
        )

        # Create service with different workspace
        service2 = CustomEntitiesService(session=session, role=other_workspace_role)

        # Create entity in second workspace
        other_entity = await service2.create_entity(
            name="other_entity",
            display_name="Other Entity",
        )

        # Try to create relation field pointing to entity in different workspace
        with pytest.raises(ValueError, match="not found"):
            await service1.create_relation_field(
                entity_id=customer_entity.id,
                field_key="cross_workspace",
                field_type=FieldType.RELATION_BELONGS_TO,
                display_name="Cross Workspace",
                relation_settings=RelationSettings(
                    relation_type=RelationType.BELONGS_TO,
                    target_entity_id=other_entity.id,  # Different workspace!
                ),
            )

    async def test_relation_fields_are_immutable(
        self,
        entities_service: CustomEntitiesService,
        customer_entity,
        organization_entity,
    ):
        """Test that relation fields cannot be updated after record creation."""
        # Create belongs_to field
        relation_settings = RelationSettings(
            relation_type=RelationType.BELONGS_TO,
            target_entity_id=organization_entity.id,
        )

        await entities_service.create_relation_field(
            entity_id=customer_entity.id,
            field_key="organization",
            field_type=FieldType.RELATION_BELONGS_TO,
            display_name="Organization",
            relation_settings=relation_settings,
        )

        # Create records
        org1 = await entities_service.create_record(
            entity_id=organization_entity.id,
            data={"name": "Org 1"},
        )

        org2 = await entities_service.create_record(
            entity_id=organization_entity.id,
            data={"name": "Org 2"},
        )

        # Create customer with initial relation
        customer = await entities_service.create_record(
            entity_id=customer_entity.id,
            data={
                "name": "John Doe",
                "organization": str(org1.id),
            },
        )

        # Try to update the record with a different organization
        # Since relations are handled separately and not through update_record,
        # this should be ignored
        updated_customer = await entities_service.update_record(
            record_id=customer.id,
            updates={
                "name": "John Updated",
                "organization": str(org2.id),  # This should be ignored
            },
        )

        # Verify name was updated but relation wasn't changed
        assert updated_customer.field_data["name"] == "John Updated"
        # The relation field shouldn't be in field_data since it's stored in links
        assert "organization" not in updated_customer.field_data

    async def test_paginated_relation_queries(
        self,
        entities_service: CustomEntitiesService,
        customer_entity,
        organization_entity,
    ):
        """Test paginated queries for related records."""
        # Create has_many field
        has_many_settings = RelationSettings(
            relation_type=RelationType.HAS_MANY,
            target_entity_id=customer_entity.id,
        )

        has_many_field = await entities_service.create_relation_field(
            entity_id=organization_entity.id,
            field_key="customers",
            field_type=FieldType.RELATION_HAS_MANY,
            display_name="Customers",
            relation_settings=has_many_settings,
        )

        # Create 150 customers
        customer_ids = []
        for i in range(150):
            customer = await entities_service.create_record(
                entity_id=customer_entity.id,
                data={"name": f"Customer {i:03d}", "email": f"c{i}@example.com"},
            )
            customer_ids.append(str(customer.id))

        # Create organization with all customers linked at creation
        org = await entities_service.create_record(
            entity_id=organization_entity.id,
            data={
                "name": "Big Corp",
                "customers": customer_ids,  # Add all 150 customers at creation
            },
        )

        # Test first page
        page1_records, total = await entities_service.query_builder.has_related(
            source_record_id=org.id,
            field_id=has_many_field.id,
            page=1,
            page_size=50,
        )

        assert total == 150
        assert len(page1_records) == 50

        # Test second page
        page2_records, total = await entities_service.query_builder.has_related(
            source_record_id=org.id,
            field_id=has_many_field.id,
            page=2,
            page_size=50,
        )

        assert len(page2_records) == 50

        # Test last page
        page3_records, total = await entities_service.query_builder.has_related(
            source_record_id=org.id,
            field_id=has_many_field.id,
            page=3,
            page_size=50,
        )

        assert len(page3_records) == 50

        # Test with filters
        (
            filtered_records,
            filtered_total,
        ) = await entities_service.query_builder.has_related(
            source_record_id=org.id,
            field_id=has_many_field.id,
            target_filters=[{"field": "name", "operator": "ilike", "value": "%00%"}],
            page=1,
            page_size=50,
        )

        # Should match Customer 000-009, 100 (11 total)
        assert filtered_total == 11
        assert len(filtered_records) == 11
