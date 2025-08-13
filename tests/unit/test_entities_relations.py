"""Integration tests for entity relations feature.

Tests the complete relation fields implementation including:
- Relation field creation
- Belongs-to and has-many operations
- Batch operations
- Cascade deletion
- Large cardinality performance
"""

import time
import uuid

import pytest
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from tracecat.db.schemas import EntityRelationLink
from tracecat.entities.models import (
    HasManyRelationUpdate,
    RelationOperation,
    RelationSettings,
    RelationType,
)
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
    entity = await entities_service.create_entity_type(
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
    entity = await entities_service.create_entity_type(
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

    async def test_create_paired_relation_fields(
        self,
        entities_service: CustomEntitiesService,
        customer_entity,
        organization_entity,
    ):
        """Test atomic creation of bidirectional fields."""
        # Create paired relation fields
        (
            belongs_to_field,
            has_many_field,
        ) = await entities_service.create_paired_relation_fields(
            source_entity_id=customer_entity.id,
            source_field_key="organization",
            source_display_name="Organization",
            target_entity_id=organization_entity.id,
            target_field_key="customers",
            target_display_name="Customers",
            cascade_delete=True,
        )

        # Verify belongs_to field
        assert belongs_to_field.field_type == FieldType.RELATION_BELONGS_TO
        assert belongs_to_field.entity_metadata_id == customer_entity.id
        assert belongs_to_field.relation_kind == "belongs_to"
        assert belongs_to_field.relation_target_entity_id == organization_entity.id
        assert belongs_to_field.relation_backref_field_id == has_many_field.id

        # Verify has_many field
        assert has_many_field.field_type == FieldType.RELATION_HAS_MANY
        assert has_many_field.entity_metadata_id == organization_entity.id
        assert has_many_field.relation_kind == "has_many"
        assert has_many_field.relation_target_entity_id == customer_entity.id
        assert has_many_field.relation_backref_field_id == belongs_to_field.id

        # Verify field settings
        assert belongs_to_field.relation_cascade_delete is True
        assert has_many_field.relation_cascade_delete is True

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
            backref_field_key=None,
            cascade_delete=False,
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
        assert field.relation_kind == "belongs_to"
        assert field.relation_target_entity_id == organization_entity.id
        assert field.relation_cascade_delete is False

    async def test_belongs_to_relation_crud(
        self,
        entities_service: CustomEntitiesService,
        customer_entity,
        organization_entity,
        session: AsyncSession,
    ):
        """Test belongs-to operations."""
        # Create paired fields
        belongs_to_field, _ = await entities_service.create_paired_relation_fields(
            source_entity_id=customer_entity.id,
            source_field_key="organization",
            source_display_name="Organization",
            target_entity_id=organization_entity.id,
            target_field_key="customers",
            target_display_name="Customers",
        )

        # Create organization and customer records
        org = await entities_service.create_record(
            entity_id=organization_entity.id,
            data={"name": "Acme Corp", "industry": "Technology"},
        )

        customer = await entities_service.create_record(
            entity_id=customer_entity.id,
            data={"name": "John Doe", "email": "john@example.com"},
        )

        # Update belongs-to relation
        await entities_service.update_belongs_to_relation(
            source_record_id=customer.id,
            field=belongs_to_field,
            target_record_id=org.id,
        )

        # Verify link was created
        link_stmt = select(EntityRelationLink).where(
            EntityRelationLink.source_record_id == customer.id,
            EntityRelationLink.target_record_id == org.id,
        )
        link_result = await session.exec(link_stmt)
        link = link_result.first()

        assert link is not None
        assert link.source_field_id == belongs_to_field.id

        # Verify field_data cache
        refreshed_customer = await entities_service.get_record(customer.id)
        assert "organization" in refreshed_customer.field_data
        cached_value = refreshed_customer.field_data["organization"]
        assert cached_value["id"] == str(org.id)
        assert cached_value["display"] == "Acme Corp"  # Uses name field

        # Clear the relation
        await entities_service.update_belongs_to_relation(
            source_record_id=customer.id,
            field=belongs_to_field,
            target_record_id=None,
        )

        # Verify link was deleted
        link_result = await session.exec(link_stmt)
        assert link_result.first() is None

        # Verify cache was cleared
        refreshed_customer = await entities_service.get_record(customer.id)
        assert "organization" not in refreshed_customer.field_data

    async def test_belongs_to_uniqueness_constraint(
        self,
        entities_service: CustomEntitiesService,
        customer_entity,
        organization_entity,
        session: AsyncSession,
    ):
        """Test that belongs-to enforces uniqueness."""
        # Create paired fields
        belongs_to_field, _ = await entities_service.create_paired_relation_fields(
            source_entity_id=customer_entity.id,
            source_field_key="organization",
            source_display_name="Organization",
            target_entity_id=organization_entity.id,
            target_field_key="customers",
            target_display_name="Customers",
        )

        # Create organizations and customer
        org1 = await entities_service.create_record(
            entity_id=organization_entity.id,
            data={"name": "Org 1"},
        )

        org2 = await entities_service.create_record(
            entity_id=organization_entity.id,
            data={"name": "Org 2"},
        )

        customer = await entities_service.create_record(
            entity_id=customer_entity.id,
            data={"name": "John Doe"},
        )

        # Set initial relation
        await entities_service.update_belongs_to_relation(
            source_record_id=customer.id,
            field=belongs_to_field,
            target_record_id=org1.id,
        )

        # Update to different organization (should replace, not add)
        await entities_service.update_belongs_to_relation(
            source_record_id=customer.id,
            field=belongs_to_field,
            target_record_id=org2.id,
        )

        # Verify only one link exists
        link_stmt = select(EntityRelationLink).where(
            EntityRelationLink.source_record_id == customer.id,
            EntityRelationLink.source_field_id == belongs_to_field.id,
        )
        link_result = await session.exec(link_stmt)
        links = list(link_result.all())

        assert len(links) == 1
        assert links[0].target_record_id == org2.id

    async def test_has_many_batch_operations(
        self,
        entities_service: CustomEntitiesService,
        customer_entity,
        organization_entity,
    ):
        """Test has-many batch operations."""
        # Create paired fields
        _, has_many_field = await entities_service.create_paired_relation_fields(
            source_entity_id=customer_entity.id,
            source_field_key="organization",
            source_display_name="Organization",
            target_entity_id=organization_entity.id,
            target_field_key="customers",
            target_display_name="Customers",
        )

        # Create organization and multiple customers
        org = await entities_service.create_record(
            entity_id=organization_entity.id,
            data={"name": "Acme Corp"},
        )

        customers = []
        for i in range(5):
            customer = await entities_service.create_record(
                entity_id=customer_entity.id,
                data={"name": f"Customer {i}", "email": f"customer{i}@example.com"},
            )
            customers.append(customer)

        # ADD operation
        add_op = HasManyRelationUpdate(
            operation=RelationOperation.ADD,
            target_ids=[c.id for c in customers[:3]],
        )

        stats = await entities_service.update_has_many_relation(
            source_record_id=org.id,
            field=has_many_field,
            operation=add_op,
        )

        assert stats["added"] == 3
        assert stats["removed"] == 0
        assert stats["unchanged"] == 0

        # ADD with duplicates (idempotent)
        add_dup_op = HasManyRelationUpdate(
            operation=RelationOperation.ADD,
            target_ids=[customers[2].id, customers[3].id],
        )

        stats = await entities_service.update_has_many_relation(
            source_record_id=org.id,
            field=has_many_field,
            operation=add_dup_op,
        )

        assert stats["added"] == 1  # Only customer[3] was new
        assert stats["unchanged"] == 1  # customer[2] already existed

        # REMOVE operation
        remove_op = HasManyRelationUpdate(
            operation=RelationOperation.REMOVE,
            target_ids=[customers[0].id, customers[1].id],
        )

        stats = await entities_service.update_has_many_relation(
            source_record_id=org.id,
            field=has_many_field,
            operation=remove_op,
        )

        assert stats["removed"] == 2

        # REPLACE operation
        replace_op = HasManyRelationUpdate(
            operation=RelationOperation.REPLACE,
            target_ids=[customers[4].id],
        )

        stats = await entities_service.update_has_many_relation(
            source_record_id=org.id,
            field=has_many_field,
            operation=replace_op,
        )

        assert stats["removed"] == 2  # Removed customers[2] and customers[3]
        assert stats["added"] == 1  # Added customers[4]

        # Verify final state
        count = await entities_service.query_builder.count_related(
            source_record_id=org.id,
            field_id=has_many_field.id,
        )
        assert count == 1

    @pytest.mark.parametrize("num_customers", [100, 500, 1000, 5000])
    @pytest.mark.slow
    @pytest.mark.anyio
    async def test_cardinality_query_performance(
        self,
        entities_service: CustomEntitiesService,
        customer_entity,
        organization_entity,
        num_customers: int,
    ):
        """Test paginated query performance with different cardinality sizes."""
        # Setup: Create paired fields and records with unique field names
        field_suffix = f"_{num_customers}"
        _, has_many_field = await entities_service.create_paired_relation_fields(
            source_entity_id=customer_entity.id,
            source_field_key=f"organization{field_suffix}",
            source_display_name="Organization",
            target_entity_id=organization_entity.id,
            target_field_key=f"customers{field_suffix}",
            target_display_name="Customers",
        )

        org = await entities_service.create_record(
            entity_id=organization_entity.id,
            data={"name": f"Corp with {num_customers} customers"},
        )

        # Create customers in batches
        batch_size = min(500, num_customers)

        for batch_num in range(0, num_customers, batch_size):
            batch_ids = []
            for i in range(batch_num, min(batch_num + batch_size, num_customers)):
                customer = await entities_service.create_record(
                    entity_id=customer_entity.id,
                    data={"name": f"Customer {i}", "email": f"c{i}@example.com"},
                )
                batch_ids.append(customer.id)

            add_op = HasManyRelationUpdate(
                operation=RelationOperation.ADD,
                target_ids=batch_ids,
            )
            await entities_service.update_has_many_relation(
                source_record_id=org.id,
                field=has_many_field,
                operation=add_op,
            )

        # Measure query performance (average of 5 runs)
        query_times = []
        for _run in range(5):
            start_time = time.perf_counter()
            records, total = await entities_service.query_builder.has_related(
                source_record_id=org.id,
                field_id=has_many_field.id,
                page=1,
                page_size=50,
            )
            query_time = (time.perf_counter() - start_time) * 1000  # Convert to ms
            query_times.append(query_time)

            assert total == num_customers
            assert len(records) == min(50, num_customers)

        avg_query_time = sum(query_times) / len(query_times)
        min_time = min(query_times)
        max_time = max(query_times)
        print(
            f"\n[{num_customers} customers] Query time - Avg: {avg_query_time:.2f}ms, Min: {min_time:.2f}ms, Max: {max_time:.2f}ms"
        )

        # Performance assertions
        assert avg_query_time < 200  # Should be < 200ms for page fetch

    @pytest.mark.parametrize(
        "num_customers,batch_size",
        [
            (100, 50),
            (500, 100),
            (1000, 200),
            (5000, 500),
        ],
    )
    @pytest.mark.slow
    @pytest.mark.anyio
    async def test_cardinality_batch_add_performance(
        self,
        entities_service: CustomEntitiesService,
        customer_entity,
        organization_entity,
        num_customers: int,
        batch_size: int,
    ):
        """Test batch add operations performance with different sizes."""
        # Setup: Create paired fields and pre-create customers with unique field names
        field_suffix = f"_add_{num_customers}_{batch_size}"
        _, has_many_field = await entities_service.create_paired_relation_fields(
            source_entity_id=customer_entity.id,
            source_field_key=f"organization{field_suffix}",
            source_display_name="Organization",
            target_entity_id=organization_entity.id,
            target_field_key=f"customers{field_suffix}",
            target_display_name="Customers",
        )

        org = await entities_service.create_record(
            entity_id=organization_entity.id,
            data={"name": f"Corp for {num_customers} batch add"},
        )

        # Pre-create all customers
        customer_ids = []
        for i in range(num_customers):
            customer = await entities_service.create_record(
                entity_id=customer_entity.id,
                data={"name": f"Customer {i}", "email": f"c{i}@example.com"},
            )
            customer_ids.append(customer.id)

        # Measure batch add performance
        start_time = time.perf_counter()
        stats_list = []
        for batch_num in range(0, num_customers, batch_size):
            batch_ids = customer_ids[batch_num : batch_num + batch_size]
            add_op = HasManyRelationUpdate(
                operation=RelationOperation.ADD,
                target_ids=batch_ids,
            )
            stats = await entities_service.update_has_many_relation(
                source_record_id=org.id,
                field=has_many_field,
                operation=add_op,
            )
            stats_list.append(stats)

        total_time = (time.perf_counter() - start_time) * 1000  # Convert to ms
        print(
            f"\n[{num_customers} customers, batch size {batch_size}] Total add time: {total_time:.2f}ms"
        )

        # Verify all were added
        final_count = await entities_service.query_builder.count_related(
            source_record_id=org.id,
            field_id=has_many_field.id,
        )
        assert final_count == num_customers

        # Performance assertion
        assert total_time < num_customers * 10  # Should be < 10ms per record

    @pytest.mark.parametrize(
        "num_customers,remove_size",
        [
            (100, 50),
            (500, 250),
            (1000, 500),
            (5000, 1000),
        ],
    )
    @pytest.mark.slow
    @pytest.mark.anyio
    async def test_cardinality_batch_remove_performance(
        self,
        entities_service: CustomEntitiesService,
        customer_entity,
        organization_entity,
        num_customers: int,
        remove_size: int,
    ):
        """Test batch remove operations performance with different sizes."""
        # Setup: Create paired fields and populate data with unique field names
        field_suffix = f"_remove_{num_customers}_{remove_size}"
        _, has_many_field = await entities_service.create_paired_relation_fields(
            source_entity_id=customer_entity.id,
            source_field_key=f"organization{field_suffix}",
            source_display_name="Organization",
            target_entity_id=organization_entity.id,
            target_field_key=f"customers{field_suffix}",
            target_display_name="Customers",
        )

        org = await entities_service.create_record(
            entity_id=organization_entity.id,
            data={"name": f"Corp for {num_customers} removal test"},
        )

        # Create and add all customers
        customer_ids = []
        batch_size = min(500, num_customers)

        for batch_num in range(0, num_customers, batch_size):
            batch_ids = []
            for i in range(batch_num, min(batch_num + batch_size, num_customers)):
                customer = await entities_service.create_record(
                    entity_id=customer_entity.id,
                    data={"name": f"Customer {i}", "email": f"c{i}@example.com"},
                )
                batch_ids.append(customer.id)

            add_op = HasManyRelationUpdate(
                operation=RelationOperation.ADD,
                target_ids=batch_ids,
            )
            await entities_service.update_has_many_relation(
                source_record_id=org.id,
                field=has_many_field,
                operation=add_op,
            )
            customer_ids.extend(batch_ids)

        # Select IDs to remove
        remove_ids = customer_ids[:remove_size]

        # Measure batch remove performance (average of 3 runs)
        remove_times = []
        for _ in range(3):
            start_time = time.perf_counter()
            remove_op = HasManyRelationUpdate(
                operation=RelationOperation.REMOVE,
                target_ids=remove_ids,
            )
            stats = await entities_service.update_has_many_relation(
                source_record_id=org.id,
                field=has_many_field,
                operation=remove_op,
            )
            remove_time = (time.perf_counter() - start_time) * 1000  # Convert to ms
            remove_times.append(remove_time)

            assert stats["removed"] == remove_size

            # Re-add for next iteration (except last)
            if _ < 2:
                add_op = HasManyRelationUpdate(
                    operation=RelationOperation.ADD,
                    target_ids=remove_ids,
                )
                await entities_service.update_has_many_relation(
                    source_record_id=org.id,
                    field=has_many_field,
                    operation=add_op,
                )

        avg_remove_time = sum(remove_times) / len(remove_times)
        print(
            f"\n[{num_customers} customers, removing {remove_size}] Avg remove time: {avg_remove_time:.2f}ms"
        )

        # Verify final count
        final_count = await entities_service.query_builder.count_related(
            source_record_id=org.id,
            field_id=has_many_field.id,
        )
        assert final_count == num_customers - remove_size

        # Performance assertion
        assert avg_remove_time < remove_size * 10  # Should be < 10ms per record removed

    async def test_cascade_deletion(
        self,
        entities_service: CustomEntitiesService,
        customer_entity,
        organization_entity,
        session: AsyncSession,
    ):
        """Test cascade delete scenarios."""
        # Create fields with cascade_delete=True
        belongs_to_field, _ = await entities_service.create_paired_relation_fields(
            source_entity_id=customer_entity.id,
            source_field_key="organization",
            source_display_name="Organization",
            target_entity_id=organization_entity.id,
            target_field_key="customers",
            target_display_name="Customers",
            cascade_delete=True,
        )

        # Create organization and customers
        org = await entities_service.create_record(
            entity_id=organization_entity.id,
            data={"name": "Doomed Corp"},
        )

        customer1 = await entities_service.create_record(
            entity_id=customer_entity.id,
            data={"name": "Customer 1"},
        )

        customer2 = await entities_service.create_record(
            entity_id=customer_entity.id,
            data={"name": "Customer 2"},
        )

        # Set relations
        await entities_service.update_belongs_to_relation(
            source_record_id=customer1.id,
            field=belongs_to_field,
            target_record_id=org.id,
        )

        await entities_service.update_belongs_to_relation(
            source_record_id=customer2.id,
            field=belongs_to_field,
            target_record_id=org.id,
        )

        # Handle deletion with cascade
        await entities_service.handle_record_deletion(
            record_id=org.id,
            cascade_relations=True,
        )

        # Verify customers were deleted
        with pytest.raises(TracecatNotFoundError):
            await entities_service.get_record(customer1.id)

        with pytest.raises(TracecatNotFoundError):
            await entities_service.get_record(customer2.id)

        # Test with cascade_delete=False
        # Create new fields with cascade_delete=False
        no_cascade_field = await entities_service.create_relation_field(
            entity_id=customer_entity.id,
            field_key="optional_org",
            field_type=FieldType.RELATION_BELONGS_TO,
            display_name="Optional Org",
            relation_settings=RelationSettings(
                relation_type=RelationType.BELONGS_TO,
                target_entity_id=organization_entity.id,
                cascade_delete=False,
            ),
        )

        # Create new org and customer
        org2 = await entities_service.create_record(
            entity_id=organization_entity.id,
            data={"name": "Safe Corp"},
        )

        customer3 = await entities_service.create_record(
            entity_id=customer_entity.id,
            data={"name": "Customer 3"},
        )

        await entities_service.update_belongs_to_relation(
            source_record_id=customer3.id,
            field=no_cascade_field,
            target_record_id=org2.id,
        )

        # Handle deletion without cascade
        await entities_service.handle_record_deletion(
            record_id=org2.id,
            cascade_relations=False,
        )

        # Verify customer still exists but relation is cleared
        customer3_after = await entities_service.get_record(customer3.id)
        assert customer3_after is not None
        assert "optional_org" not in customer3_after.field_data

        # Verify link was deleted
        link_stmt = select(EntityRelationLink).where(
            EntityRelationLink.source_record_id == customer3.id,
            EntityRelationLink.target_record_id == org2.id,
        )
        link_result = await session.exec(link_stmt)
        assert link_result.first() is None

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
        other_entity = await service2.create_entity_type(
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

        # Create records in each workspace
        customer = await service1.create_record(
            entity_id=customer_entity.id,
            data={"name": "Customer in WS1"},
        )

        other_record = await service2.create_record(
            entity_id=other_entity.id,
            data={},
        )

        # Create a valid relation field within same workspace
        valid_field = await service1.create_relation_field(
            entity_id=customer_entity.id,
            field_key="valid_org",
            field_type=FieldType.RELATION_BELONGS_TO,
            display_name="Valid Org",
            relation_settings=RelationSettings(
                relation_type=RelationType.BELONGS_TO,
                target_entity_id=organization_entity.id,
            ),
        )

        # Try to link to record in different workspace
        with pytest.raises(TracecatNotFoundError):
            await service1.update_belongs_to_relation(
                source_record_id=customer.id,
                field=valid_field,
                target_record_id=other_record.id,  # Different workspace!
            )

    async def test_paginated_relation_queries(
        self,
        entities_service: CustomEntitiesService,
        customer_entity,
        organization_entity,
    ):
        """Test paginated queries for related records."""
        # Create paired fields
        _, has_many_field = await entities_service.create_paired_relation_fields(
            source_entity_id=customer_entity.id,
            source_field_key="organization",
            source_display_name="Organization",
            target_entity_id=organization_entity.id,
            target_field_key="customers",
            target_display_name="Customers",
        )

        # Create organization and 150 customers
        org = await entities_service.create_record(
            entity_id=organization_entity.id,
            data={"name": "Big Corp"},
        )

        customer_ids = []
        for i in range(150):
            customer = await entities_service.create_record(
                entity_id=customer_entity.id,
                data={"name": f"Customer {i:03d}", "email": f"c{i}@example.com"},
            )
            customer_ids.append(customer.id)

        # Add all customers to organization
        add_op = HasManyRelationUpdate(
            operation=RelationOperation.ADD,
            target_ids=customer_ids,
        )

        await entities_service.update_has_many_relation(
            source_record_id=org.id,
            field=has_many_field,
            operation=add_op,
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
