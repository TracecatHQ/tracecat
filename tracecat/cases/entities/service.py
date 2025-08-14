import uuid
from collections.abc import Sequence
from typing import Any

from sqlmodel import select

from tracecat.db.schemas import CaseEntityLink, EntityData
from tracecat.entities.service import CustomEntitiesService
from tracecat.service import BaseWorkspaceService
from tracecat.types.exceptions import TracecatNotFoundError


class CaseEntitiesService(BaseWorkspaceService):
    service_name = "case_entities"

    async def list_records(
        self, case_id: uuid.UUID, entity_metadata_id: uuid.UUID | None = None
    ) -> Sequence[CaseEntityLink]:
        """List entity records associated with a case.

        Args:
            case_id: The case ID to list records for
            entity_metadata_id: Optional filter by entity type

        Returns:
            List of CaseEntityLink records with relationships loaded
        """
        statement = select(CaseEntityLink).where(CaseEntityLink.case_id == case_id)

        if entity_metadata_id:
            statement = statement.where(
                CaseEntityLink.entity_metadata_id == entity_metadata_id
            )

        result = await self.session.exec(statement)
        return result.all()

    async def get_record(self, case_id: uuid.UUID, record_id: uuid.UUID) -> EntityData:
        """Get a specific entity record linked to a case.

        Args:
            case_id: The case ID
            record_id: The entity record ID

        Returns:
            EntityData record

        Raises:
            TracecatNotFoundError: If record not found or not linked to case
        """
        # Check if record is linked to this case
        link_stmt = select(CaseEntityLink).where(
            CaseEntityLink.case_id == case_id,
            CaseEntityLink.entity_data_id == record_id,
        )
        link_result = await self.session.exec(link_stmt)
        link = link_result.first()

        if not link:
            raise TracecatNotFoundError(
                f"Record {record_id} not found or not linked to case {case_id}"
            )

        # Get the actual record using entities service
        entity_service = CustomEntitiesService(session=self.session, role=self.role)
        return await entity_service.get_record(record_id)

    async def get_record_by_slug(
        self,
        case_id: uuid.UUID,
        entity_type_name: str,
        slug_value: str,
        slug_field: str = "name",
    ) -> EntityData:
        """Get a specific entity record linked to a case by its slug.

        Args:
            case_id: The case ID
            entity_type_name: The entity type name (e.g., "customer", "incident")
            slug_value: The slug value to search for
            slug_field: Field to use as slug (default: "name")

        Returns:
            EntityData record

        Raises:
            TracecatNotFoundError: If record not found or not linked to case
        """
        # Get entity type by name
        entity_service = CustomEntitiesService(session=self.session, role=self.role)
        entity_type = await entity_service.get_entity_type_by_name(entity_type_name)

        # Get the record by slug
        record = await entity_service.get_record_by_slug(
            entity_type.id, slug_value, slug_field
        )

        # Check if this record is linked to the case
        link_stmt = select(CaseEntityLink).where(
            CaseEntityLink.case_id == case_id,
            CaseEntityLink.entity_data_id == record.id,
        )
        link_result = await self.session.exec(link_stmt)
        link = link_result.first()

        if not link:
            raise TracecatNotFoundError(
                f"Record '{slug_value}' (entity: {entity_type_name}) not linked to case {case_id}"
            )

        return record

    async def get_case_entity_link(
        self, case_id: uuid.UUID, link_id: uuid.UUID
    ) -> CaseEntityLink | None:
        """Get a specific case-entity association."""
        stmt = select(CaseEntityLink).where(
            CaseEntityLink.id == link_id,
            CaseEntityLink.case_id == case_id,
        )
        result = await self.session.exec(stmt)
        return result.one_or_none()

    async def add_record_to_case(
        self,
        case_id: uuid.UUID,
        entity_data_id: uuid.UUID,
        entity_metadata_id: uuid.UUID,
    ) -> CaseEntityLink:
        """Associate an existing entity record with a case.

        Args:
            case_id: The case ID to associate with
            entity_data_id: The entity record ID
            entity_metadata_id: The entity type ID

        Returns:
            Created CaseEntityLink

        Raises:
            ValueError: If entity doesn't exist or already linked
        """
        # Verify entity record exists and belongs to workspace
        entity_stmt = select(EntityData).where(
            EntityData.id == entity_data_id,
            EntityData.entity_metadata_id == entity_metadata_id,
            EntityData.owner_id == self.workspace_id,
        )
        entity_result = await self.session.exec(entity_stmt)
        if not entity_result.first():
            raise ValueError(f"Entity record {entity_data_id} not found")

        # Check if already linked
        existing_stmt = select(CaseEntityLink).where(
            CaseEntityLink.case_id == case_id,
            CaseEntityLink.entity_data_id == entity_data_id,
        )
        existing_result = await self.session.exec(existing_stmt)
        if existing_result.first():
            raise ValueError("Entity record already associated with this case")

        # Create link
        link = CaseEntityLink(
            owner_id=self.workspace_id,
            case_id=case_id,
            entity_metadata_id=entity_metadata_id,
            entity_data_id=entity_data_id,
        )

        self.session.add(link)
        await self.session.commit()
        await self.session.refresh(link)
        return link

    async def create_record(
        self,
        case_id: uuid.UUID,
        entity_metadata_id: uuid.UUID,
        entity_data: dict[str, Any],
    ) -> tuple[EntityData, CaseEntityLink]:
        """Create a new entity record and associate it with a case.

        Args:
            case_id: The case ID to associate with
            entity_metadata_id: The entity type ID
            entity_data: Field data for the entity

        Returns:
            Tuple of (created entity record, case-entity link)

        Raises:
            ValueError: If validation fails
        """
        # Create entity record using the entities service
        entity_service = CustomEntitiesService(session=self.session, role=self.role)

        # Create the entity record
        entity_record = await entity_service.create_record(
            entity_id=entity_metadata_id, data=entity_data
        )

        # Associate with case
        link = await self.add_record_to_case(
            case_id=case_id,
            entity_data_id=entity_record.id,
            entity_metadata_id=entity_metadata_id,
        )

        return entity_record, link

    async def update_record(
        self,
        case_id: uuid.UUID,
        record_id: uuid.UUID,
        updates: dict[str, Any],
    ) -> EntityData:
        """Update an entity record linked to a case.

        Args:
            case_id: The case ID
            record_id: The entity record ID to update
            updates: Field updates

        Returns:
            Updated EntityData

        Raises:
            TracecatNotFoundError: If record not found or not linked to case
            ValueError: If validation fails
        """
        # Verify record is linked to this case
        link_stmt = select(CaseEntityLink).where(
            CaseEntityLink.case_id == case_id,
            CaseEntityLink.entity_data_id == record_id,
        )
        link_result = await self.session.exec(link_stmt)
        link = link_result.first()

        if not link:
            raise TracecatNotFoundError(
                f"Record {record_id} not found or not linked to case {case_id}"
            )

        # Update the record using entities service
        entity_service = CustomEntitiesService(session=self.session, role=self.role)
        return await entity_service.update_record(record_id, updates)

    async def remove_record(self, case_id: uuid.UUID, link_id: uuid.UUID) -> None:
        """Remove an entity record association from a case.

        Args:
            case_id: The case ID
            link_id: The CaseEntityLink ID to remove

        Raises:
            ValueError: If link not found
        """
        # Find the link
        link = await self.get_case_entity_link(case_id, link_id)

        if not link:
            raise ValueError(f"Entity association {link_id} not found for this case")

        # Delete the link (entity record remains)
        await self.session.delete(link)
        await self.session.commit()
