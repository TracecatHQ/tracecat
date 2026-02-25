from __future__ import annotations

import uuid
from collections.abc import Sequence

from slugify import slugify
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from tracecat.cases.dropdowns.schemas import (
    CaseDropdownDefinitionCreate,
    CaseDropdownDefinitionUpdate,
    CaseDropdownOptionCreate,
    CaseDropdownOptionUpdate,
    CaseDropdownValueInput,
    CaseDropdownValueRead,
    CaseDropdownValueSet,
)
from tracecat.contexts import ctx_run
from tracecat.db.models import (
    Case,
    CaseDropdownDefinition,
    CaseDropdownOption,
    CaseDropdownValue,
)
from tracecat.exceptions import TracecatNotFoundError, TracecatValidationError
from tracecat.service import BaseWorkspaceService, requires_entitlement
from tracecat.tiers.enums import Entitlement


class CaseDropdownDefinitionsService(BaseWorkspaceService):
    """Service for managing workspace-scoped dropdown definitions and their options."""

    service_name = "case_dropdown_definitions"

    @requires_entitlement(Entitlement.CASE_ADDONS)
    async def list_definitions(self) -> Sequence[CaseDropdownDefinition]:
        """List all dropdown definitions for the workspace, ordered by position."""
        stmt = (
            select(CaseDropdownDefinition)
            .where(CaseDropdownDefinition.workspace_id == self.workspace_id)
            .options(selectinload(CaseDropdownDefinition.options))
            .order_by(CaseDropdownDefinition.position)
        )
        result = await self.session.execute(stmt)
        return result.scalars().all()

    @requires_entitlement(Entitlement.CASE_ADDONS)
    async def get_definition(self, definition_id: uuid.UUID) -> CaseDropdownDefinition:
        """Get a single dropdown definition by ID."""
        stmt = (
            select(CaseDropdownDefinition)
            .where(
                CaseDropdownDefinition.workspace_id == self.workspace_id,
                CaseDropdownDefinition.id == definition_id,
            )
            .options(selectinload(CaseDropdownDefinition.options))
        )
        result = await self.session.execute(stmt)
        definition = result.scalar_one_or_none()
        if definition is None:
            raise TracecatNotFoundError(
                f"Dropdown definition {definition_id} not found"
            )
        return definition

    @requires_entitlement(Entitlement.CASE_ADDONS)
    async def get_definition_by_ref(self, ref: str) -> CaseDropdownDefinition:
        """Get a single dropdown definition by its slug ref."""
        stmt = (
            select(CaseDropdownDefinition)
            .where(
                CaseDropdownDefinition.workspace_id == self.workspace_id,
                CaseDropdownDefinition.ref == ref,
            )
            .options(selectinload(CaseDropdownDefinition.options))
        )
        result = await self.session.execute(stmt)
        definition = result.scalar_one_or_none()
        if definition is None:
            raise TracecatNotFoundError(
                f"Dropdown definition with ref '{ref}' not found"
            )
        return definition

    @requires_entitlement(Entitlement.CASE_ADDONS)
    async def create_definition(
        self, params: CaseDropdownDefinitionCreate
    ) -> CaseDropdownDefinition:
        """Create a dropdown definition with initial options."""
        definition = CaseDropdownDefinition(
            workspace_id=self.workspace_id,
            name=params.name,
            ref=params.ref,
            is_ordered=params.is_ordered,
            icon_name=params.icon_name,
            position=params.position,
        )
        self.session.add(definition)
        await self.session.flush()

        for opt in params.options:
            option = CaseDropdownOption(
                definition_id=definition.id,
                label=opt.label,
                ref=opt.ref,
                icon_name=opt.icon_name,
                color=opt.color,
                position=opt.position,
            )
            self.session.add(option)

        try:
            await self.session.commit()
        except IntegrityError as err:
            await self.session.rollback()
            raise TracecatValidationError(
                "Dropdown definition with this ref already exists"
            ) from err
        await self.session.refresh(definition)
        return definition

    @requires_entitlement(Entitlement.CASE_ADDONS)
    async def update_definition(
        self,
        definition: CaseDropdownDefinition,
        params: CaseDropdownDefinitionUpdate,
    ) -> CaseDropdownDefinition:
        """Update an existing dropdown definition."""
        update_data = params.model_dump(exclude_unset=True)

        # Keep name/ref aligned when only the name is edited.
        if "name" in update_data:
            if not update_data["name"]:
                raise TracecatValidationError("Dropdown name cannot be empty")
            if "ref" not in update_data:
                generated_ref = slugify(update_data["name"], separator="_")
                if not generated_ref:
                    raise TracecatValidationError(
                        "Dropdown name must produce a valid reference"
                    )
                update_data["ref"] = generated_ref

        # Reject explicitly empty refs.
        if "ref" in update_data:
            ref = update_data["ref"]
            if ref is None:
                raise TracecatValidationError("Dropdown reference cannot be empty")
            stripped_ref = ref.strip()
            if not stripped_ref:
                raise TracecatValidationError("Dropdown reference cannot be empty")
            update_data["ref"] = stripped_ref

        for key, value in update_data.items():
            setattr(definition, key, value)
        try:
            await self.session.commit()
        except IntegrityError as err:
            await self.session.rollback()
            raise TracecatValidationError(
                "Dropdown definition with this ref already exists"
            ) from err
        await self.session.refresh(definition)
        return definition

    @requires_entitlement(Entitlement.CASE_ADDONS)
    async def delete_definition(self, definition: CaseDropdownDefinition) -> None:
        """Delete a dropdown definition and all associated options/values."""
        await self.session.delete(definition)
        await self.session.commit()

    # --- Option CRUD ---

    async def _get_option(self, option_id: uuid.UUID) -> CaseDropdownOption:
        """Get an option by ID, scoped to the current workspace."""
        stmt = (
            select(CaseDropdownOption)
            .join(
                CaseDropdownDefinition,
                CaseDropdownOption.definition_id == CaseDropdownDefinition.id,
            )
            .where(
                CaseDropdownOption.id == option_id,
                CaseDropdownDefinition.workspace_id == self.workspace_id,
            )
        )
        result = await self.session.execute(stmt)
        option = result.scalar_one_or_none()
        if option is None:
            raise TracecatNotFoundError(f"Dropdown option {option_id} not found")
        return option

    @requires_entitlement(Entitlement.CASE_ADDONS)
    async def add_option(
        self,
        definition_id: uuid.UUID,
        params: CaseDropdownOptionCreate,
    ) -> CaseDropdownOption:
        """Add an option to a dropdown definition."""
        option = CaseDropdownOption(
            definition_id=definition_id,
            label=params.label,
            ref=params.ref,
            icon_name=params.icon_name,
            color=params.color,
            position=params.position,
        )
        self.session.add(option)
        try:
            await self.session.commit()
        except IntegrityError as err:
            await self.session.rollback()
            raise TracecatValidationError(
                "Option with this ref already exists for this dropdown"
            ) from err
        await self.session.refresh(option)
        return option

    @requires_entitlement(Entitlement.CASE_ADDONS)
    async def update_option(
        self,
        option_id: uuid.UUID,
        params: CaseDropdownOptionUpdate,
    ) -> CaseDropdownOption:
        """Update a dropdown option."""
        option = await self._get_option(option_id)
        for key, value in params.model_dump(exclude_unset=True).items():
            setattr(option, key, value)
        try:
            await self.session.commit()
        except IntegrityError as err:
            await self.session.rollback()
            raise TracecatValidationError(
                "Option with this ref already exists for this dropdown"
            ) from err
        await self.session.refresh(option)
        return option

    @requires_entitlement(Entitlement.CASE_ADDONS)
    async def delete_option(self, option_id: uuid.UUID) -> None:
        """Delete a dropdown option."""
        option = await self._get_option(option_id)
        await self.session.delete(option)
        await self.session.commit()

    @requires_entitlement(Entitlement.CASE_ADDONS)
    async def reorder_options(
        self, definition_id: uuid.UUID, option_ids: list[uuid.UUID]
    ) -> None:
        """Reorder options by setting positions according to the given ID list."""
        for position, oid in enumerate(option_ids):
            stmt = select(CaseDropdownOption).where(
                CaseDropdownOption.definition_id == definition_id,
                CaseDropdownOption.id == oid,
            )
            result = await self.session.execute(stmt)
            option = result.scalar_one_or_none()
            if option is not None:
                option.position = position
        await self.session.commit()


class CaseDropdownValuesService(BaseWorkspaceService):
    """Service for reading/writing per-case dropdown values."""

    service_name = "case_dropdown_values"

    async def _get_case(self, case_id: uuid.UUID) -> Case:
        stmt = select(Case).where(
            Case.workspace_id == self.workspace_id,
            Case.id == case_id,
        )
        result = await self.session.execute(stmt)
        case = result.scalar_one_or_none()
        if case is None:
            raise TracecatNotFoundError(f"Case {case_id} not found")
        return case

    async def _resolve_definition_id(
        self,
        *,
        definition_id: uuid.UUID | None,
        definition_ref: str | None,
    ) -> uuid.UUID:
        if definition_id is not None:
            stmt = select(CaseDropdownDefinition.id).where(
                CaseDropdownDefinition.workspace_id == self.workspace_id,
                CaseDropdownDefinition.id == definition_id,
            )
            resolved_definition_id = await self.session.scalar(stmt)
            if resolved_definition_id is None:
                raise TracecatNotFoundError(
                    f"Dropdown definition {definition_id} not found"
                )
            return resolved_definition_id

        if definition_ref is None:
            raise TracecatValidationError(
                "Either definition_id or definition_ref must be provided"
            )

        stmt = select(CaseDropdownDefinition.id).where(
            CaseDropdownDefinition.workspace_id == self.workspace_id,
            CaseDropdownDefinition.ref == definition_ref,
        )
        resolved_definition_id = await self.session.scalar(stmt)
        if resolved_definition_id is None:
            raise TracecatNotFoundError(
                f"Dropdown definition with ref '{definition_ref}' not found"
            )
        return resolved_definition_id

    async def _resolve_option_id(
        self,
        *,
        definition_id: uuid.UUID,
        option_id: uuid.UUID | None,
        option_ref: str | None,
    ) -> uuid.UUID | None:
        if option_id is not None:
            stmt = select(CaseDropdownOption.id).where(
                CaseDropdownOption.id == option_id,
                CaseDropdownOption.definition_id == definition_id,
            )
            resolved_option_id = await self.session.scalar(stmt)
            if resolved_option_id is None:
                raise TracecatNotFoundError(
                    f"Dropdown option {option_id} not found for definition {definition_id}"
                )
            return resolved_option_id

        if option_ref is None:
            return None

        stmt = select(CaseDropdownOption.id).where(
            CaseDropdownOption.ref == option_ref,
            CaseDropdownOption.definition_id == definition_id,
        )
        resolved_option_id = await self.session.scalar(stmt)
        if resolved_option_id is None:
            raise TracecatNotFoundError(
                f"Dropdown option with ref '{option_ref}' not found for definition {definition_id}"
            )
        return resolved_option_id

    @requires_entitlement(Entitlement.CASE_ADDONS)
    async def list_values_for_case(
        self, case_id: uuid.UUID
    ) -> list[CaseDropdownValueRead]:
        """List all dropdown values for a case with definition/option info."""
        # Validate case belongs to this workspace
        await self._get_case(case_id)
        stmt = (
            select(CaseDropdownValue)
            .where(CaseDropdownValue.case_id == case_id)
            .options(
                selectinload(CaseDropdownValue.definition).selectinload(
                    CaseDropdownDefinition.options
                ),
                selectinload(CaseDropdownValue.option),
            )
        )
        result = await self.session.execute(stmt)
        rows = result.scalars().all()

        return [
            CaseDropdownValueRead(
                id=row.id,
                definition_id=row.definition_id,
                definition_ref=row.definition.ref,
                definition_name=row.definition.name,
                option_id=row.option.id if row.option else None,
                option_label=row.option.label if row.option else None,
                option_ref=row.option.ref if row.option else None,
                option_icon_name=row.option.icon_name if row.option else None,
                option_color=row.option.color if row.option else None,
            )
            for row in rows
        ]

    async def _set_value(
        self,
        *,
        case: Case,
        definition_id: uuid.UUID,
        params: CaseDropdownValueSet,
    ) -> CaseDropdownValueRead:
        case_id = case.id
        # Resolve old value
        stmt = select(CaseDropdownValue).where(
            CaseDropdownValue.case_id == case_id,
            CaseDropdownValue.definition_id == definition_id,
        )
        result = await self.session.execute(stmt)
        existing = result.scalar_one_or_none()

        old_option_id: uuid.UUID | None = None
        old_option_label: str | None = None

        if existing is not None:
            # Eagerly loaded
            if existing.option:
                old_option_id = existing.option.id
                old_option_label = existing.option.label
            existing.option_id = params.option_id
            await self.session.flush()
            row = existing
        else:
            row = CaseDropdownValue(
                case_id=case_id,
                definition_id=definition_id,
                option_id=params.option_id,
            )
            self.session.add(row)
            await self.session.flush()

        # Resolve definition info (workspace-scoped)
        def_stmt = select(CaseDropdownDefinition).where(
            CaseDropdownDefinition.id == definition_id,
            CaseDropdownDefinition.workspace_id == self.workspace_id,
        )
        def_result = await self.session.execute(def_stmt)
        definition = def_result.scalar_one_or_none()
        if definition is None:
            raise TracecatNotFoundError(
                f"Dropdown definition {definition_id} not found"
            )

        # Resolve new option label and validate it belongs to the definition
        new_option_label: str | None = None
        new_option: CaseDropdownOption | None = None
        if params.option_id is not None:
            opt_stmt = select(CaseDropdownOption).where(
                CaseDropdownOption.id == params.option_id,
                CaseDropdownOption.definition_id == definition_id,
            )
            opt_result = await self.session.execute(opt_stmt)
            new_option = opt_result.scalar_one_or_none()
            if new_option is None:
                raise TracecatNotFoundError(
                    f"Dropdown option {params.option_id} not found for definition {definition_id}"
                )
            new_option_label = new_option.label

        # Create event
        run_ctx = ctx_run.get()
        wf_exec_id = run_ctx.wf_exec_id if run_ctx else None

        from tracecat.cases.schemas import DropdownValueChangedEvent
        from tracecat.cases.service import CaseEventsService

        event = DropdownValueChangedEvent(
            definition_id=str(definition_id),
            definition_ref=definition.ref,
            definition_name=definition.name,
            old_option_id=str(old_option_id) if old_option_id else None,
            old_option_label=old_option_label,
            new_option_id=str(params.option_id) if params.option_id else None,
            new_option_label=new_option_label,
            wf_exec_id=wf_exec_id,
        )
        events_service = CaseEventsService(session=self.session, role=self.role)
        await events_service.create_event(case=case, event=event)

        return CaseDropdownValueRead(
            id=row.id,
            definition_id=definition_id,
            definition_ref=definition.ref,
            definition_name=definition.name,
            option_id=params.option_id,
            option_label=new_option_label,
            option_ref=new_option.ref if new_option else None,
            option_icon_name=new_option.icon_name if new_option else None,
            option_color=new_option.color if new_option else None,
        )

    @requires_entitlement(Entitlement.CASE_ADDONS)
    async def set_value(
        self,
        case_id: uuid.UUID,
        definition_id: uuid.UUID,
        params: CaseDropdownValueSet,
        *,
        commit: bool = True,
    ) -> CaseDropdownValueRead:
        """Set (upsert) or clear a dropdown value for a case. Records an event."""
        case = await self._get_case(case_id)
        result = await self._set_value(
            case=case,
            definition_id=definition_id,
            params=params,
        )
        if commit:
            await self.session.commit()
        else:
            await self.session.flush()
        return result

    @requires_entitlement(Entitlement.CASE_ADDONS)
    async def apply_values(
        self,
        case_id: uuid.UUID,
        values: Sequence[CaseDropdownValueInput],
        *,
        commit: bool = True,
    ) -> list[CaseDropdownValueRead]:
        """Apply multiple dropdown selections to a case in a single transaction."""
        case = await self._get_case(case_id)
        seen_definition_ids: set[uuid.UUID] = set()
        results: list[CaseDropdownValueRead] = []

        for value in values:
            definition_id = await self._resolve_definition_id(
                definition_id=value.definition_id,
                definition_ref=value.definition_ref,
            )
            if definition_id in seen_definition_ids:
                raise TracecatValidationError(
                    f"Duplicate dropdown definition {definition_id} in request"
                )
            seen_definition_ids.add(definition_id)

            option_id = await self._resolve_option_id(
                definition_id=definition_id,
                option_id=value.option_id,
                option_ref=value.option_ref,
            )

            result = await self._set_value(
                case=case,
                definition_id=definition_id,
                params=CaseDropdownValueSet(option_id=option_id),
            )
            results.append(result)

        if commit:
            await self.session.commit()
        else:
            await self.session.flush()
        return results
