"""Service for managing case records."""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy.orm import selectinload
from sqlmodel import col, func, select
from sqlmodel.ext.asyncio.session import AsyncSession

from tracecat import config
from tracecat.cases.records.models import (
    CaseRecordCreate,
    CaseRecordLink,
    CaseRecordUpdate,
)
from tracecat.db.schemas import Case, CaseRecord, Entity, EntityRecord
from tracecat.entities.service import EntityService
from tracecat.logger import logger
from tracecat.records.model import RecordUpdate
from tracecat.records.service import RecordService
from tracecat.service import BaseWorkspaceService
from tracecat.types.auth import Role
from tracecat.types.exceptions import (
    TracecatNotFoundError,
    TracecatValidationError,
)


class CaseRecordService(BaseWorkspaceService):
    """Service for managing case records."""

    service_name = "case_records"

    def __init__(self, session: AsyncSession, role: Role | None = None):
        super().__init__(session, role)
        self.entity_service = EntityService(session, role)
        self.record_service = RecordService(session, role)

    async def _check_record_limit(self, case: Case) -> None:
        """Check if case has reached the maximum number of records."""
        stmt = (
            select(func.count())
            .select_from(CaseRecord)
            .where(CaseRecord.case_id == case.id)
        )
        result = await self.session.exec(stmt)
        count = result.one() or 0

        if count >= config.TRACECAT__MAX_RECORDS_PER_CASE:
            raise TracecatValidationError(
                f"Case already has {count} records. "
                f"Maximum allowed is {config.TRACECAT__MAX_RECORDS_PER_CASE}"
            )

    async def list_case_records(self, case: Case) -> Sequence[CaseRecord]:
        """List all records for a case with entity and record data.

        Args:
            case: The case to list records for

        Returns:
            List of case records with related data
        """
        stmt = (
            select(CaseRecord)
            .where(CaseRecord.case_id == case.id)
            .options(
                selectinload(CaseRecord.entity),  # type: ignore
                selectinload(CaseRecord.record),  # type: ignore
            )
            .order_by(col(CaseRecord.created_at).desc())
        )
        result = await self.session.exec(stmt)
        return result.all()

    async def get_case_record(
        self, case: Case, record_link_id: uuid.UUID
    ) -> CaseRecord | None:
        """Get a specific case record by link ID.

        Args:
            case: The case the record belongs to
            record_link_id: The case record link ID

        Returns:
            The case record if found, None otherwise
        """
        stmt = (
            select(CaseRecord)
            .where(
                CaseRecord.case_id == case.id,
                CaseRecord.id == record_link_id,
            )
            .options(
                selectinload(CaseRecord.entity),  # type: ignore
                selectinload(CaseRecord.record),  # type: ignore
            )
        )
        result = await self.session.exec(stmt)
        return result.first()

    async def create_case_record(
        self, case: Case, params: CaseRecordCreate
    ) -> CaseRecord:
        """Create a new entity record and link it to the case.

        Args:
            case: The case to link the record to
            params: Parameters for creating the record

        Returns:
            The created case record link
        """
        # Check record limit
        await self._check_record_limit(case)

        # Get entity by key
        try:
            entity = await self.entity_service.get_entity_by_key(params.entity_key)
        except TracecatNotFoundError as err:
            raise TracecatNotFoundError(
                f"Entity with key '{params.entity_key}' not found"
            ) from err

        # Create the entity record and link atomically in a single transaction
        # Validate and normalize payload using RecordService helpers
        fields = await self.record_service._get_active_fields(entity)
        normalized = self.record_service._validate_and_coerce(params.data, fields)

        entity_record = EntityRecord(
            owner_id=self.workspace_id,
            entity_id=entity.id,
            data=normalized,
        )
        self.session.add(entity_record)
        await self.session.flush()  # Ensure entity_record.id is available

        # Create the link
        case_record = CaseRecord(
            owner_id=self.workspace_id,
            case_id=case.id,
            entity_id=entity.id,
            record_id=entity_record.id,
        )
        self.session.add(case_record)
        await self.session.commit()
        await self.session.refresh(case_record)

        # Load relationships
        await self.session.refresh(case_record, ["entity", "record"])

        logger.info(
            "Created case record",
            case_id=case.id,
            entity_key=params.entity_key,
            record_id=entity_record.id,
            link_id=case_record.id,
        )

        return case_record

    async def link_entity_record(
        self, case: Case, params: CaseRecordLink
    ) -> CaseRecord:
        """Link an existing entity record to a case.

        Args:
            case: The case to link the record to
            params: Parameters containing the record ID to link

        Returns:
            The created case record link
        """
        # Check record limit
        await self._check_record_limit(case)

        # Verify the entity record exists and belongs to this workspace
        try:
            entity_record = await self.record_service.get_record_by_id(
                params.entity_record_id
            )
        except TracecatNotFoundError as err:
            raise TracecatNotFoundError(
                f"Entity record {params.entity_record_id} not found"
            ) from err

        # Check if already linked
        existing_stmt = select(CaseRecord).where(
            CaseRecord.case_id == case.id,
            CaseRecord.record_id == params.entity_record_id,
        )
        existing = await self.session.exec(existing_stmt)
        if existing.first():
            raise TracecatValidationError(
                f"Record {params.entity_record_id} is already linked to this case"
            )

        # Get entity for the record
        entity_stmt = select(Entity).where(Entity.id == entity_record.entity_id)
        entity = (await self.session.exec(entity_stmt)).one()

        # Create the link
        case_record = CaseRecord(
            owner_id=self.workspace_id,
            case_id=case.id,
            entity_id=entity.id,
            record_id=entity_record.id,
        )
        self.session.add(case_record)
        await self.session.commit()
        await self.session.refresh(case_record)

        # Load relationships
        await self.session.refresh(case_record, ["entity", "record"])

        logger.info(
            "Linked entity record to case",
            case_id=case.id,
            record_id=entity_record.id,
            link_id=case_record.id,
        )

        return case_record

    async def update_case_record(
        self, case_record: CaseRecord, params: CaseRecordUpdate
    ) -> CaseRecord:
        """Update the entity record data for a case record.

        Args:
            case_record: The case record to update
            params: Parameters containing the updated data

        Returns:
            The updated case record
        """
        # Verify ownership
        if case_record.owner_id != self.workspace_id:
            raise TracecatNotFoundError("Case record not found")

        # Update the entity record data
        update_params = RecordUpdate(data=params.data)
        await self.record_service.update_record(case_record.record, update_params)

        # Refresh to get updated data
        await self.session.refresh(case_record.record)

        logger.info(
            "Updated case record",
            link_id=case_record.id,
            record_id=case_record.record_id,
        )

        return case_record

    async def unlink_case_record(self, case_record: CaseRecord) -> None:
        """Unlink a record from a case (soft delete - removes link only).

        Args:
            case_record: The case record link to remove
        """
        # Verify ownership
        if case_record.owner_id != self.workspace_id:
            raise TracecatNotFoundError("Case record not found")

        # Delete only the link, not the entity record
        await self.session.delete(case_record)
        await self.session.commit()

        logger.info(
            "Unlinked case record",
            link_id=case_record.id,
            case_id=case_record.case_id,
            record_id=case_record.record_id,
        )

    async def delete_case_record(self, case_record: CaseRecord) -> None:
        """Delete a case record and its entity record (hard delete).

        Args:
            case_record: The case record to delete
        """
        # Verify ownership
        if case_record.owner_id != self.workspace_id:
            raise TracecatNotFoundError("Case record not found")

        # Ensure the related record is loaded
        await self.session.refresh(case_record, ["record"])

        # Store record ID for logging
        record_id = case_record.record_id

        # Delete both link and record atomically in one commit
        await self.session.delete(case_record.record)
        await self.session.delete(case_record)
        await self.session.commit()

        logger.info(
            "Deleted case record and entity record",
            link_id=case_record.id,
            record_id=record_id,
        )
