"""Sub-case hierarchy: one-level parent/child grouping of cases.

Every operation here requires the ``case_addons`` entitlement. Without it the
hierarchy is invisible: every case reads as top-level and lists stay flat.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import load_only, raiseload

from tracecat.auth.types import Role
from tracecat.cases.event_schemas import (
    ParentChangedEvent,
    SubCasesAddedEvent,
    SubCasesRemovedEvent,
)
from tracecat.cases.events import CaseEventsService
from tracecat.cases.schemas import (
    CaseBatchItemResult,
    CaseHierarchyFilter,
    CaseParentRead,
    CaseRef,
)
from tracecat.contexts import ctx_run
from tracecat.db.models import Case
from tracecat.exceptions import (
    EntitlementRequired,
    TracecatNotFoundError,
    TracecatValidationError,
)
from tracecat.service import BaseWorkspaceService
from tracecat.tiers.enums import Entitlement


@dataclass(frozen=True, slots=True)
class CaseHierarchy:
    """A case's parent summary and direct sub-case count."""

    parent: CaseParentRead | None = None
    num_sub_cases: int = 0

    def read_fields(self) -> dict[str, Any]:
        """Return the hierarchy fields of a case read model."""
        return {"parent": self.parent, "num_sub_cases": self.num_sub_cases}


TOP_LEVEL = CaseHierarchy()


def case_ref(case: Case) -> CaseRef:
    return CaseRef(id=case.id, short_id=case.short_id)


class CaseHierarchyService(BaseWorkspaceService):
    """Reads, validates, and records parent/sub-case relationships."""

    service_name = "case_hierarchy"

    def __init__(self, session: AsyncSession, role: Role | None = None):
        super().__init__(session, role)
        self.events = CaseEventsService(session=self.session, role=self.role)

    async def enabled(self) -> bool:
        return await self.has_entitlement(Entitlement.CASE_ADDONS)

    async def resolve_filters(
        self,
        *,
        parent_id: uuid.UUID | None,
        hierarchy: CaseHierarchyFilter,
    ) -> tuple[uuid.UUID | None, bool]:
        """Return ``(parent_id, top_level_only)`` for a case search.

        Without ``case_addons`` the list stays flat: stored sub-cases are not
        hidden, and filtering by parent is rejected.

        Raises:
            EntitlementRequired: If ``parent_id`` is set without ``case_addons``.
            TracecatValidationError: If ``parent_id`` is combined with ``top_level``.
        """
        if parent_id is not None and hierarchy == "top_level":
            raise TracecatValidationError(
                "parent_id cannot be combined with hierarchy=top_level"
            )
        if not await self.enabled():
            if parent_id is not None:
                raise EntitlementRequired(Entitlement.CASE_ADDONS.value)
            return None, False
        return parent_id, hierarchy == "top_level"

    async def get_hierarchy(
        self, cases: Sequence[Case]
    ) -> dict[uuid.UUID, CaseHierarchy]:
        """Return each case's parent summary and sub-case count in two queries.

        Cases missing from the result are top-level with no sub-cases, which is
        how every case reads without ``case_addons``.
        """
        if not cases or not await self.enabled():
            return {}
        counts = await self._get_sub_case_counts([case.id for case in cases])
        parents = await self._get_parent_reads([case.parent_id for case in cases])
        return {
            case.id: CaseHierarchy(
                parent=parents.get(case.parent_id) if case.parent_id else None,
                num_sub_cases=counts.get(case.id, 0),
            )
            for case in cases
        }

    async def _get_sub_case_counts(
        self, case_ids: Sequence[uuid.UUID]
    ) -> dict[uuid.UUID, int]:
        if not case_ids:
            return {}
        stmt = (
            select(Case.parent_id, func.count())
            .where(
                Case.workspace_id == self.workspace_id,
                Case.parent_id.in_(case_ids),
            )
            .group_by(Case.parent_id)
        )
        rows = (await self.session.execute(stmt)).tuples().all()
        return {
            parent_id: int(count) for parent_id, count in rows if parent_id is not None
        }

    async def _get_parent_reads(
        self, parent_ids: Sequence[uuid.UUID | None]
    ) -> dict[uuid.UUID, CaseParentRead]:
        unique_ids = {parent_id for parent_id in parent_ids if parent_id is not None}
        if not unique_ids:
            return {}
        stmt = (
            select(Case)
            .options(load_only(Case.id, Case.case_number, Case.summary), raiseload("*"))
            .where(Case.workspace_id == self.workspace_id, Case.id.in_(unique_ids))
        )
        parents = (await self.session.execute(stmt)).scalars().all()
        return {
            parent.id: CaseParentRead(
                id=parent.id, short_id=parent.short_id, summary=parent.summary
            )
            for parent in parents
        }

    @staticmethod
    def validate_parent(parent: Case | None, parent_id: uuid.UUID) -> Case:
        """Check that a prospective parent exists and is top-level.

        Raises:
            TracecatNotFoundError: If the parent case does not exist.
            TracecatValidationError: If the parent case is itself a sub-case.
        """
        if parent is None:
            raise TracecatNotFoundError(f"Parent case {parent_id} not found")
        if parent.parent_id is not None:
            raise TracecatValidationError(
                f"{parent.short_id} is a sub-case and cannot have sub-cases"
            )
        return parent

    async def lock_parent_for_new_sub_case(self, parent_id: uuid.UUID) -> Case:
        """Share-lock a prospective parent so it cannot become a sub-case concurrently.

        FOR SHARE lets concurrent creates under the same parent proceed while
        conflicting with the FOR NO KEY UPDATE taken by parent reassignment.

        Raises:
            EntitlementRequired: Without ``case_addons``.
        """
        await self.require_entitlement(Entitlement.CASE_ADDONS)
        parent = await self.session.scalar(
            select(Case)
            .options(raiseload("*"))
            .where(Case.workspace_id == self.workspace_id, Case.id == parent_id)
            .with_for_update(read=True)
        )
        return self.validate_parent(parent, parent_id)

    async def plan_parent_changes(
        self,
        case_ids: list[uuid.UUID],
        locked: dict[uuid.UUID, Case],
        parent_id: uuid.UUID | None,
    ) -> tuple[list[CaseBatchItemResult], list[Case], Case | None]:
        """Validate a batch parent change against locked rows.

        Returns per-case results, the cases whose parent actually changes, and
        the new parent (None when clearing).
        """
        parent: Case | None = None
        nested_counts: dict[uuid.UUID, int] = {}
        if parent_id is not None:
            parent = self.validate_parent(locked.get(parent_id), parent_id)
            nested_counts = await self._get_sub_case_counts(
                [case_id for case_id in case_ids if case_id in locked]
            )

        results: list[CaseBatchItemResult] = []
        changed: list[Case] = []
        for case_id in case_ids:
            case = locked.get(case_id)
            error: str | None = None
            if case_id == parent_id:
                error = "A case cannot be its own parent"
            elif case is None:
                error = "Case not found"
            elif nested_counts.get(case_id):
                error = f"{case.short_id} has sub-cases and cannot become a sub-case"
            elif case.parent_id != parent_id:
                changed.append(case)
            results.append(
                CaseBatchItemResult(case_id=case_id, success=error is None, error=error)
            )
        return results, changed, parent

    async def apply_parent_changes(
        self, changed: list[Case], new_parent: Case | None
    ) -> None:
        """Reassign parents and record the matching activity events."""
        if not changed:
            return
        old_parent_ids = {case.parent_id for case in changed if case.parent_id}
        old_parents: dict[uuid.UUID, Case] = {}
        if old_parent_ids:
            result = await self.session.execute(
                select(Case)
                .options(raiseload("*"))
                .where(
                    Case.workspace_id == self.workspace_id,
                    Case.id.in_(old_parent_ids),
                )
            )
            old_parents = {case.id: case for case in result.scalars().all()}
        moves = [
            (case, old_parents.get(case.parent_id) if case.parent_id else None)
            for case in changed
        ]
        for case, _ in moves:
            case.parent_id = new_parent.id if new_parent is not None else None
        await self.record_parent_events(moves, new_parent)

    async def record_parent_events(
        self,
        moves: Sequence[tuple[Case, Case | None]],
        new_parent: Case | None,
    ) -> None:
        """Record events for cases moved from their old parent to ``new_parent``.

        Each child gets its own ``parent_changed`` event; each affected parent
        gets one aggregated ``sub_cases_added``/``sub_cases_removed`` event per
        batch so a large grouping does not flood the parent's activity feed or
        case triggers.
        """
        run_ctx = ctx_run.get()
        wf_exec_id = run_ctx.wf_exec_id if run_ctx else None
        removed: dict[uuid.UUID, tuple[Case, list[CaseRef]]] = {}
        for case, old_parent in moves:
            if old_parent is not None:
                removed.setdefault(old_parent.id, (old_parent, []))[1].append(
                    case_ref(case)
                )
            await self.events.create_event(
                case=case,
                event=ParentChangedEvent(
                    old=case_ref(old_parent) if old_parent is not None else None,
                    new=case_ref(new_parent) if new_parent is not None else None,
                    wf_exec_id=wf_exec_id,
                ),
            )
        for old_parent, refs in removed.values():
            await self.events.create_event(
                case=old_parent,
                event=SubCasesRemovedEvent(sub_cases=refs, wf_exec_id=wf_exec_id),
            )
        if new_parent is not None:
            await self.events.create_event(
                case=new_parent,
                event=SubCasesAddedEvent(
                    sub_cases=[case_ref(case) for case, _ in moves],
                    wf_exec_id=wf_exec_id,
                ),
            )
