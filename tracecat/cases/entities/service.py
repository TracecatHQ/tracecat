import uuid
from typing import Any

from sqlmodel import select

from tracecat.cases.entities.models import (
    CaseEntityRead,
    CaseRecordLinkRead,
    CaseRecordRead,
)
from tracecat.cases.service import CasesService
from tracecat.db.schemas import (
    Case,
    CaseRecordLink,
    FieldMetadata,
    Record,
    RecordRelationLink,
)
from tracecat.entities.service import CustomEntitiesService
from tracecat.entities.types import FieldType
from tracecat.service import BaseWorkspaceService
from tracecat.types.exceptions import TracecatNotFoundError


class CaseEntitiesService(BaseWorkspaceService):
    service_name = "case_entities"

    async def validate_case_exists(self, case_id: uuid.UUID) -> Case:
        """Validate that a case exists and belongs to the workspace.

        Args:
            case_id: The case ID to validate

        Returns:
            Case object

        Raises:
            TracecatNotFoundError: If case not found
        """
        cases_service = CasesService(self.session, role=self.role)
        case = await cases_service.get_case(case_id)
        if not case:
            raise TracecatNotFoundError(f"Case {case_id} not found")
        return case

    async def _resolve_relation_fields(
        self,
        record: Record,
        fields: list[FieldMetadata],
        entity_service: CustomEntitiesService,
    ) -> tuple[dict[str, Any], list[str]]:
        """Resolve relation fields for a record.

        Args:
            record: The record to resolve relations for
            fields: Field metadata for the entity
            entity_service: Service to fetch related records

        Returns:
            Tuple of (field_data with resolved relations, list of relation field keys)
        """
        field_data = dict(record.field_data)
        relation_fields = []

        for field in fields:
            if not field.relation_kind:
                continue

            relation_fields.append(field.field_key)

            # Get related records for this field
            if field.field_type == FieldType.RELATION_BELONGS_TO.value:
                # For BELONGS_TO, fetch the single related record
                link_stmt = select(RecordRelationLink).where(
                    RecordRelationLink.source_record_id == record.id,
                    RecordRelationLink.source_field_id == field.id,
                )
                link_result = await self.session.exec(link_stmt)
                link = link_result.first()

                if link:
                    try:
                        target_record = await entity_service.get_record(
                            link.target_record_id
                        )
                        # Include the target record's field_data directly
                        field_data[field.field_key] = target_record.field_data
                    except TracecatNotFoundError:
                        field_data[field.field_key] = None
                else:
                    field_data[field.field_key] = None

            elif field.field_type == FieldType.RELATION_HAS_MANY.value:
                # For HAS_MANY, fetch multiple related records (limited)
                links_stmt = (
                    select(RecordRelationLink)
                    .where(
                        RecordRelationLink.source_record_id == record.id,
                        RecordRelationLink.source_field_id == field.id,
                    )
                    .limit(10)
                )  # Limit to prevent performance issues

                links_result = await self.session.exec(links_stmt)
                links = links_result.all()

                related_records = []
                for link in links:
                    try:
                        target_record = await entity_service.get_record(
                            link.target_record_id
                        )
                        related_records.append(target_record.field_data)
                    except TracecatNotFoundError:
                        continue

                field_data[field.field_key] = related_records

        return field_data, relation_fields

    async def list_records(
        self, case_id: uuid.UUID, entity_id: uuid.UUID | None = None
    ) -> list[CaseRecordLinkRead]:
        """List entity records with their entity metadata and record data.

        Args:
            case_id: The case ID to list records for
            entity_id: Optional filter by entity

        Returns:
            List of CaseRecordLinkRead models with optional nested entity and record
        """
        # Validate case exists
        await self.validate_case_exists(case_id)

        # Get all links
        statement = select(CaseRecordLink).where(CaseRecordLink.case_id == case_id)

        if entity_id:
            statement = statement.where(CaseRecordLink.entity_id == entity_id)

        result = await self.session.exec(statement)
        links = result.all()

        # Get entities service
        entity_service = CustomEntitiesService(session=self.session, role=self.role)

        # Build detailed response
        results: list[CaseRecordLinkRead] = []
        for link in links:
            # Get entity metadata
            try:
                entity = await entity_service.get_entity(link.entity_id)
                entity_read = CaseEntityRead.model_validate(
                    entity, from_attributes=True
                )
            except TracecatNotFoundError:
                entity_read = None

            # Get record data
            try:
                record = await entity_service.get_record(link.record_id)

                # Get all fields for this entity (including relation fields)
                fields = await entity_service.list_fields(
                    link.entity_id, include_inactive=False
                )

                # Resolve relation fields
                (
                    resolved_field_data,
                    relation_fields,
                ) = await self._resolve_relation_fields(record, fields, entity_service)

                # Create the record read model with resolved relations
                record_read = CaseRecordRead(
                    id=record.id,
                    entity_id=record.entity_id,
                    field_data=resolved_field_data,
                    relation_fields=relation_fields,
                )
            except TracecatNotFoundError:
                record_read = None

            results.append(
                CaseRecordLinkRead(
                    id=link.id,
                    case_id=link.case_id,
                    entity_id=link.entity_id,
                    record_id=link.record_id,
                    entity=entity_read,
                    record=record_read,
                )
            )

        return results

    async def get_record(
        self, case_id: uuid.UUID, record_id: uuid.UUID
    ) -> CaseRecordRead:
        """Get a specific entity record linked to a case.

        Args:
            case_id: The case ID
            record_id: The entity record ID

        Returns:
            Record record

        Raises:
            TracecatNotFoundError: If record not found or not linked to case
        """
        # Validate case exists
        await self.validate_case_exists(case_id)

        # Check if record is linked to this case
        link_stmt = select(CaseRecordLink).where(
            CaseRecordLink.case_id == case_id,
            CaseRecordLink.record_id == record_id,
        )
        link_result = await self.session.exec(link_stmt)
        link = link_result.first()

        if not link:
            raise TracecatNotFoundError(
                f"Record {record_id} not found or not linked to case {case_id}"
            )

        # Get the actual record using entities service
        entity_service = CustomEntitiesService(session=self.session, role=self.role)
        record = await entity_service.get_record(record_id)

        # Get all fields for this entity (including relation fields)
        fields = await entity_service.list_fields(
            record.entity_id, include_inactive=False
        )

        # Resolve relation fields
        resolved_field_data, relation_fields = await self._resolve_relation_fields(
            record, fields, entity_service
        )

        return CaseRecordRead(
            id=record.id,
            entity_id=record.entity_id,
            field_data=resolved_field_data,
            relation_fields=relation_fields,
        )

    async def get_record_by_slug(
        self,
        case_id: uuid.UUID,
        entity_name: str,
        slug_value: str,
        slug_field: str = "name",
    ) -> CaseRecordRead:
        """Get a specific entity record linked to a case by its slug.

        Args:
            case_id: The case ID
            entity_name: The entity name (e.g., "customer", "incident")
            slug_value: The slug value to search for
            slug_field: Field to use as slug (default: "name")

        Returns:
            Record record

        Raises:
            TracecatNotFoundError: If record not found or not linked to case
        """
        # Get entity by name
        entity_service = CustomEntitiesService(session=self.session, role=self.role)
        entity = await entity_service.get_entity_by_name(entity_name)

        # Get the record by slug
        record = await entity_service.get_record_by_slug(
            entity.id, slug_value, slug_field
        )

        # Check if this record is linked to the case
        link_stmt = select(CaseRecordLink).where(
            CaseRecordLink.case_id == case_id,
            CaseRecordLink.record_id == record.id,
        )
        link_result = await self.session.exec(link_stmt)
        link = link_result.first()

        if not link:
            raise TracecatNotFoundError(
                f"Record '{slug_value}' (entity: {entity_name}) not linked to case {case_id}"
            )

        return CaseRecordRead.model_validate(record, from_attributes=True)

    async def get_case_entity_link(
        self, case_id: uuid.UUID, link_id: uuid.UUID
    ) -> CaseRecordLink | None:
        """Get a specific case-entity association."""
        stmt = select(CaseRecordLink).where(
            CaseRecordLink.id == link_id,
            CaseRecordLink.case_id == case_id,
        )
        result = await self.session.exec(stmt)
        return result.one_or_none()

    async def add_record(
        self,
        case_id: uuid.UUID,
        record_id: uuid.UUID,
        entity_id: uuid.UUID,
    ) -> CaseRecordLinkRead:
        """Associate an existing entity record with a case.

        Args:
            case_id: The case ID to associate with
            record_id: The entity record ID
            entity_id: The entity ID

        Returns:
            Created CaseRecordLink

        Raises:
            ValueError: If entity doesn't exist or already linked
        """
        # Validate case exists
        await self.validate_case_exists(case_id)

        # Verify entity record exists and belongs to workspace
        entity_stmt = select(Record).where(
            Record.id == record_id,
            Record.entity_id == entity_id,
            Record.owner_id == self.workspace_id,
        )
        entity_result = await self.session.exec(entity_stmt)
        if not entity_result.first():
            raise ValueError(f"Entity record {record_id} not found")

        # Check if already linked
        existing_stmt = select(CaseRecordLink).where(
            CaseRecordLink.case_id == case_id,
            CaseRecordLink.record_id == record_id,
        )
        existing_result = await self.session.exec(existing_stmt)
        if existing_result.first():
            raise ValueError("Entity record already associated with this case")

        # Create link
        link = CaseRecordLink(
            owner_id=self.workspace_id,
            case_id=case_id,
            entity_id=entity_id,
            record_id=record_id,
        )

        self.session.add(link)
        await self.session.commit()
        await self.session.refresh(link)

        # Build response model with metadata
        entity_service = CustomEntitiesService(session=self.session, role=self.role)
        try:
            entity = await entity_service.get_entity(entity_id)
            entity_read = CaseEntityRead.model_validate(entity, from_attributes=True)
        except TracecatNotFoundError:
            entity_read = None

        try:
            record = await entity_service.get_record(record_id)
            record_read = CaseRecordRead.model_validate(record, from_attributes=True)
        except TracecatNotFoundError:
            record_read = None

        return CaseRecordLinkRead(
            id=link.id,
            case_id=link.case_id,
            entity_id=link.entity_id,
            record_id=link.record_id,
            entity=entity_read,
            record=record_read,
        )

    async def create_record(
        self,
        case_id: uuid.UUID,
        entity_id: uuid.UUID,
        entity_data: dict[str, Any],
    ) -> CaseRecordLinkRead:
        """Create a new entity record and associate it with a case.

        Args:
            case_id: The case ID to associate with
            entity_id: The entity ID
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
            entity_id=entity_id, data=entity_data
        )

        # Associate with case and build response
        link_read = await self.add_record(
            case_id=case_id,
            record_id=entity_record.id,
            entity_id=entity_id,
        )

        # Get all fields for this entity (including relation fields)
        entity_service = CustomEntitiesService(session=self.session, role=self.role)
        fields = await entity_service.list_fields(
            entity_record.entity_id, include_inactive=False
        )

        # Resolve relation fields
        resolved_field_data, relation_fields = await self._resolve_relation_fields(
            entity_record, fields, entity_service
        )

        # Ensure record is included (avoid refetch; override with freshly created)
        return CaseRecordLinkRead(
            id=link_read.id,
            case_id=link_read.case_id,
            entity_id=link_read.entity_id,
            record_id=link_read.record_id,
            entity=link_read.entity,
            record=CaseRecordRead(
                id=entity_record.id,
                entity_id=entity_record.entity_id,
                field_data=resolved_field_data,
                relation_fields=relation_fields,
            ),
        )

    async def update_record(
        self,
        case_id: uuid.UUID,
        record_id: uuid.UUID,
        updates: dict[str, Any],
    ) -> CaseRecordRead:
        """Update an entity record linked to a case.

        Args:
            case_id: The case ID
            record_id: The entity record ID to update
            updates: Field updates

        Returns:
            Updated Record

        Raises:
            TracecatNotFoundError: If record not found or not linked to case
            ValueError: If validation fails
        """
        # Verify record is linked to this case
        link_stmt = select(CaseRecordLink).where(
            CaseRecordLink.case_id == case_id,
            CaseRecordLink.record_id == record_id,
        )
        link_result = await self.session.exec(link_stmt)
        link = link_result.first()

        if not link:
            raise TracecatNotFoundError(
                f"Record {record_id} not found or not linked to case {case_id}"
            )

        # Update the record using entities service
        entity_service = CustomEntitiesService(session=self.session, role=self.role)
        updated = await entity_service.update_record(record_id, updates)

        # Get all fields for this entity (including relation fields)
        fields = await entity_service.list_fields(
            updated.entity_id, include_inactive=False
        )

        # Resolve relation fields
        resolved_field_data, relation_fields = await self._resolve_relation_fields(
            updated, fields, entity_service
        )

        return CaseRecordRead(
            id=updated.id,
            entity_id=updated.entity_id,
            field_data=resolved_field_data,
            relation_fields=relation_fields,
        )

    async def remove_record(self, case_id: uuid.UUID, link_id: uuid.UUID) -> None:
        """Remove an entity record association from a case.

        Args:
            case_id: The case ID
            link_id: The CaseRecordLink ID to remove

        Raises:
            TracecatNotFoundError: If case not found
            ValueError: If link not found
        """
        # Validate case exists
        await self.validate_case_exists(case_id)

        # Find the link
        link = await self.get_case_entity_link(case_id, link_id)

        if not link:
            raise ValueError(f"Entity association {link_id} not found for this case")

        # Delete the link (entity record remains)
        await self.session.delete(link)
        await self.session.commit()

    async def delete_record(self, case_id: uuid.UUID, record_id: uuid.UUID) -> None:
        """Delete an entity record linked to a case.

        Args:
            case_id: The case ID
            record_id: The entity record ID to delete

        Raises:
            TracecatNotFoundError: If case not found
            ValueError: If record not found
        """
        # Validate case exists
        await self.validate_case_exists(case_id)

        # Find the link by case_id and record_id
        stmt = select(CaseRecordLink).where(
            CaseRecordLink.case_id == case_id,
            CaseRecordLink.record_id == record_id,
        )
        result = await self.session.exec(stmt)
        link = result.first()

        if not link:
            raise ValueError(f"Entity record {record_id} not found for this case")

        # Delete the record using entities service
        # This will cascade delete the link due to FK constraint
        entity_service = CustomEntitiesService(session=self.session, role=self.role)
        await entity_service.delete_record(record_id)

        # No need to delete the link - it's cascade deleted with the record
        await self.session.commit()
