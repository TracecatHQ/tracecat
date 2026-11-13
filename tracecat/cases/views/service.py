"""Saved case views: storage, visibility rules, pins, and running a view."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Literal, cast

import sqlalchemy as sa
from sqlalchemy import func, select
from sqlalchemy.sql.elements import ColumnElement

from tracecat.authz.controls import has_scope
from tracecat.cases.enums import CaseViewVisibility
from tracecat.cases.search_schemas import CaseQuery, CaseSearchOrder
from tracecat.cases.tags.search import split_case_tag_filters
from tracecat.cases.views.builtins import BUILTIN_CASE_VIEWS
from tracecat.cases.views.schemas import (
    CaseViewCreate,
    CaseViewRead,
    CaseViewStaleRef,
    CaseViewUpdate,
    validate_view_order,
)
from tracecat.cases.views.types import BUILTIN_CASE_VIEW_IDS, BuiltinCaseViewId
from tracecat.db.models import (
    CaseDropdownDefinition,
    CaseDropdownOption,
    CaseView,
    CaseViewPin,
)
from tracecat.exceptions import (
    TracecatAuthorizationError,
    TracecatNotFoundError,
    TracecatValidationError,
)
from tracecat.pagination import CursorPaginatedResponse, PageParams, paginate
from tracecat.service import BaseWorkspaceService

CASE_VIEW_QUERY_VERSION = 1
SHARED_VIEW_SCOPE = "case:update"

# Matching fields a request may not combine with ``view_id``. ``filters`` is
# allowed and ANDed with the view's own tree.
_VIEW_EXCLUSIVE_FIELDS = frozenset(CaseQuery.model_fields) - {"view_id", "filters"}


class CaseViewNotFoundError(TracecatNotFoundError):
    """The view does not exist, or the requesting user cannot see it."""

    def __init__(self) -> None:
        super().__init__("Case view not found")


class CaseViewPermissionError(TracecatAuthorizationError):
    """The requesting user can see the view but may not change it."""


@dataclass(frozen=True, slots=True)
class ResolvedCaseView:
    """A view's query, ready to run, after deleted references are removed."""

    query: CaseQuery
    order_by: CaseSearchOrder | None
    sort: Literal["asc", "desc"] | None
    match_none: bool
    """A rule in the view now names only deleted tags or options."""


@dataclass(frozen=True, slots=True)
class _CleanedQuery:
    query: CaseQuery
    stale_refs: list[CaseViewStaleRef]
    match_none: bool


def parse_view_id(view_id: str | uuid.UUID) -> uuid.UUID | BuiltinCaseViewId:
    """Return a saved view's UUID or a built-in ID, or raise not found."""
    if isinstance(view_id, uuid.UUID):
        return view_id
    if view_id in BUILTIN_CASE_VIEW_IDS:
        return cast(BuiltinCaseViewId, view_id)
    try:
        return uuid.UUID(view_id)
    except ValueError as exc:
        raise CaseViewNotFoundError() from exc


class CaseViewsService(BaseWorkspaceService):
    """Manage saved case views for the requesting user and workspace."""

    service_name = "case_views"

    # Visibility and permissions

    @property
    def _user_id(self) -> uuid.UUID | None:
        return self.role.user_id

    @property
    def _can_edit_shared(self) -> bool:
        return has_scope(self.role.scopes or frozenset(), SHARED_VIEW_SCOPE)

    def _visible(self) -> ColumnElement[bool]:
        """Shared views, plus the requesting user's personal views."""
        shared = CaseView.visibility == CaseViewVisibility.WORKSPACE
        if self._user_id is None:
            return shared
        return sa.or_(shared, CaseView.owner_id == self._user_id)

    def _can_edit(self, view: CaseView) -> bool:
        is_owner = self._user_id is not None and view.owner_id == self._user_id
        if view.visibility == CaseViewVisibility.PERSONAL:
            return is_owner
        return is_owner or self._can_edit_shared

    def _require_user(self) -> uuid.UUID:
        if self._user_id is None:
            raise CaseViewPermissionError("Saved views belong to a signed-in user")
        return self._user_id

    # Reads

    def list_builtins(self) -> list[CaseViewRead]:
        """Return the views every workspace has, in display order."""
        return [self._builtin_read(view_id) for view_id in BUILTIN_CASE_VIEW_IDS]

    async def list_views(
        self, page: PageParams, *, pinned_only: bool = False
    ) -> CursorPaginatedResponse[CaseViewRead]:
        """Page through saved views the requesting user can see.

        Args:
            page: Page size and continuation cursor.
            pinned_only: Only the user's pinned views, in pin order.
        """
        pin = self._pin_join()
        statement = (
            select(CaseView, pin.c.position)
            .outerjoin(pin, pin.c.view_id == CaseView.id)
            .where(CaseView.workspace_id == self.workspace_id, self._visible())
        )
        if pinned_only:
            statement = statement.where(pin.c.position.is_not(None))
            order_by = (pin.c.position.asc(), CaseView.id.asc())
        else:
            order_by = (CaseView.created_at.asc(), CaseView.id.asc())
        result = await paginate(
            self.session,
            statement,
            page=page,
            order_by=order_by,
            row_factory=lambda row: self._custom_read(
                cast(CaseView, row[0]), cast(int | None, row[1])
            ),
        )
        return CursorPaginatedResponse(
            items=result.items,
            next_cursor=result.next_cursor,
            prev_cursor=result.prev_cursor,
            has_more=result.has_more,
            has_previous=result.has_previous,
        )

    async def get_view(self, view_id: str | uuid.UUID) -> CaseViewRead:
        """Return one view, including any references to deleted tags or options."""
        parsed = parse_view_id(view_id)
        if not isinstance(parsed, uuid.UUID):
            return self._builtin_read(parsed)
        view = await self._get_visible(parsed)
        position = await self._pin_position(view.id)
        read = self._custom_read(view, position)
        cleaned = await self._clean_query(read.query)
        return read.model_copy(update={"stale_refs": cleaned.stale_refs})

    async def resolve_for_search(self, view_id: str | uuid.UUID) -> ResolvedCaseView:
        """Load a visible view's query so a search can run it."""
        parsed = parse_view_id(view_id)
        if not isinstance(parsed, uuid.UUID):
            builtin = BUILTIN_CASE_VIEWS[parsed]
            return ResolvedCaseView(
                query=builtin.query,
                order_by=builtin.order_by,
                sort=cast(Literal["asc", "desc"], builtin.sort),
                match_none=False,
            )
        view = await self._get_visible(parsed)
        cleaned = await self._clean_query(self._stored_query(view))
        return ResolvedCaseView(
            query=cleaned.query,
            order_by=cast(CaseSearchOrder | None, view.order_by),
            sort=cast(Literal["asc", "desc"] | None, view.sort),
            match_none=cleaned.match_none,
        )

    # Writes

    async def create_view(self, params: CaseViewCreate) -> CaseViewRead:
        """Save a new view owned by the requesting user."""
        owner_id = self._require_user()
        if (
            params.visibility == CaseViewVisibility.WORKSPACE
            and not self._can_edit_shared
        ):
            raise CaseViewPermissionError(
                f"Sharing a view with the workspace requires {SHARED_VIEW_SCOPE}"
            )
        view = CaseView(
            workspace_id=self.workspace_id,
            owner_id=owner_id,
            name=params.name,
            visibility=params.visibility,
            query=params.query.model_dump(mode="json", exclude_defaults=True),
            query_version=CASE_VIEW_QUERY_VERSION,
            order_by=params.order_by,
            sort=params.sort,
            columns=params.columns,
        )
        self.session.add(view)
        await self.session.commit()
        await self.session.refresh(view)
        return self._custom_read(view, None)

    async def update_view(
        self, view_id: str | uuid.UUID, params: CaseViewUpdate
    ) -> CaseViewRead:
        """Change a view the requesting user may edit."""
        view = await self._get_editable(view_id)
        changes = params.model_fields_set
        if "visibility" in changes and params.visibility != view.visibility:
            self._check_visibility_change(view, params.visibility)
            view.visibility = cast(CaseViewVisibility, params.visibility)
        if "name" in changes and params.name is not None:
            view.name = params.name
        if "query" in changes and params.query is not None:
            view.query = params.query.model_dump(mode="json", exclude_defaults=True)
            view.query_version = CASE_VIEW_QUERY_VERSION
        if "order_by" in changes:
            view.order_by = params.order_by
        if "sort" in changes:
            view.sort = params.sort
        if "columns" in changes:
            view.columns = params.columns
        try:
            validate_view_order(self._stored_query(view), view.order_by, view.sort)
        except ValueError as exc:
            raise TracecatValidationError(str(exc)) from exc
        await self.session.commit()
        await self.session.refresh(view)
        return self._custom_read(view, await self._pin_position(view.id))

    async def delete_view(self, view_id: str | uuid.UUID) -> None:
        """Delete a view the requesting user may edit. Pins go with it."""
        view = await self._get_editable(view_id)
        await self.session.delete(view)
        await self.session.commit()

    async def pin_view(
        self, view_id: str | uuid.UUID, *, position: int | None = None
    ) -> CaseViewRead:
        """Show a saved view in the requesting user's sidebar."""
        user_id = self._require_user()
        parsed = parse_view_id(view_id)
        if not isinstance(parsed, uuid.UUID):
            raise TracecatValidationError("Built-in views are always shown")
        view = await self._get_visible(parsed)
        pin = (
            await self.session.execute(
                select(CaseViewPin).where(
                    CaseViewPin.user_id == user_id, CaseViewPin.view_id == view.id
                )
            )
        ).scalar_one_or_none()
        if position is None:
            position = (
                pin.position if pin is not None else await self._next_pin_position()
            )
        if pin is None:
            self.session.add(
                CaseViewPin(
                    workspace_id=self.workspace_id,
                    user_id=user_id,
                    view_id=view.id,
                    position=position,
                )
            )
        else:
            pin.position = position
        await self.session.commit()
        await self.session.refresh(view)
        return self._custom_read(view, position)

    async def unpin_view(self, view_id: str | uuid.UUID) -> None:
        """Remove a view from the requesting user's sidebar."""
        user_id = self._require_user()
        parsed = parse_view_id(view_id)
        if not isinstance(parsed, uuid.UUID):
            raise TracecatValidationError("Built-in views are always shown")
        await self.session.execute(
            sa.delete(CaseViewPin).where(
                CaseViewPin.workspace_id == self.workspace_id,
                CaseViewPin.user_id == user_id,
                CaseViewPin.view_id == parsed,
            )
        )
        await self.session.commit()

    # Running a view

    async def apply_to_query[QueryT: CaseQuery](
        self, request: QueryT
    ) -> tuple[QueryT, ResolvedCaseView | None]:
        """Replace a request's matching criteria with its view's, if it names one.

        The request keeps its own pagination, sort, and result options. Its
        ``filters`` tree, if any, is ANDed with the view's.

        Returns:
            The request to run, and the resolved view (``None`` without one).

        Raises:
            TracecatValidationError: The request also sets other matching fields.
        """
        if request.view_id is None:
            return request, None
        defaults = CaseQuery()
        conflicting = sorted(
            field
            for field in _VIEW_EXCLUSIVE_FIELDS
            if getattr(request, field) != getattr(defaults, field)
        )
        if conflicting:
            raise TracecatValidationError(
                "A view supplies its own matching criteria. Use `filters` to "
                f"narrow it; remove {', '.join(conflicting)}."
            )
        resolved = await self.resolve_for_search(request.view_id)
        view_query = resolved.query
        filters = view_query.filters
        if request.filters is not None:
            filters = (
                request.filters
                if filters is None
                else CaseQuery.model_validate(
                    {"filters": {"and": [filters, request.filters]}}
                ).filters
            )
        update = {field: getattr(view_query, field) for field in _VIEW_EXCLUSIVE_FIELDS}
        update.update(view_id=None, filters=filters)
        return request.model_copy(update=update), resolved

    # Helpers

    def _builtin_read(self, view_id: BuiltinCaseViewId) -> CaseViewRead:
        builtin = BUILTIN_CASE_VIEWS[view_id]
        return CaseViewRead(
            id=builtin.id,
            kind="builtin",
            name=builtin.name,
            visibility=CaseViewVisibility.WORKSPACE,
            query=builtin.query,
            order_by=builtin.order_by,
            sort=cast(Literal["asc", "desc"], builtin.sort),
            can_edit=False,
        )

    def _custom_read(self, view: CaseView, pin_position: int | None) -> CaseViewRead:
        return CaseViewRead(
            id=str(view.id),
            kind="custom",
            name=view.name,
            visibility=view.visibility,
            owner_id=view.owner_id,
            query=self._stored_query(view),
            order_by=cast(CaseSearchOrder | None, view.order_by),
            sort=cast(Literal["asc", "desc"] | None, view.sort),
            columns=view.columns,
            can_edit=self._can_edit(view),
            pinned=pin_position is not None,
            pin_position=pin_position,
            created_at=view.created_at,
            updated_at=view.updated_at,
        )

    @staticmethod
    def _stored_query(view: CaseView) -> CaseQuery:
        try:
            return CaseQuery.model_validate(view.query)
        except ValueError as exc:
            raise TracecatValidationError(
                f"Case view {view.name!r} has a query this version cannot read: {exc}"
            ) from exc

    def _pin_join(self) -> sa.Subquery:
        user_id = self._user_id
        statement = select(CaseViewPin.view_id, CaseViewPin.position).where(
            CaseViewPin.workspace_id == self.workspace_id
        )
        statement = statement.where(
            sa.false() if user_id is None else CaseViewPin.user_id == user_id
        )
        return statement.subquery("pin")

    async def _pin_position(self, view_id: uuid.UUID) -> int | None:
        if self._user_id is None:
            return None
        return (
            await self.session.execute(
                select(CaseViewPin.position).where(
                    CaseViewPin.workspace_id == self.workspace_id,
                    CaseViewPin.user_id == self._user_id,
                    CaseViewPin.view_id == view_id,
                )
            )
        ).scalar_one_or_none()

    async def _next_pin_position(self) -> int:
        current = (
            await self.session.execute(
                select(func.max(CaseViewPin.position)).where(
                    CaseViewPin.workspace_id == self.workspace_id,
                    CaseViewPin.user_id == self._user_id,
                )
            )
        ).scalar_one_or_none()
        return 0 if current is None else current + 1

    async def _get_visible(self, view_id: uuid.UUID) -> CaseView:
        view = (
            await self.session.execute(
                select(CaseView).where(
                    CaseView.workspace_id == self.workspace_id,
                    CaseView.id == view_id,
                    self._visible(),
                )
            )
        ).scalar_one_or_none()
        if view is None:
            raise CaseViewNotFoundError()
        return view

    async def _get_editable(self, view_id: str | uuid.UUID) -> CaseView:
        parsed = parse_view_id(view_id)
        if not isinstance(parsed, uuid.UUID):
            raise TracecatValidationError("Built-in views cannot be changed")
        view = await self._get_visible(parsed)
        if not self._can_edit(view):
            raise CaseViewPermissionError("You cannot change this view")
        return view

    def _check_visibility_change(
        self, view: CaseView, visibility: CaseViewVisibility | None
    ) -> None:
        if visibility == CaseViewVisibility.WORKSPACE and not self._can_edit_shared:
            raise CaseViewPermissionError(
                f"Sharing a view with the workspace requires {SHARED_VIEW_SCOPE}"
            )
        if visibility == CaseViewVisibility.PERSONAL and view.owner_id != self._user_id:
            # Making it personal would hide it from everyone but its owner.
            raise CaseViewPermissionError("Only the view's owner can make it personal")

    async def _clean_query(self, query: CaseQuery) -> _CleanedQuery:
        """Drop deleted tags and dropdown options from a stored query.

        A rule left with no values would otherwise widen the results, so it
        marks the whole view as matching nothing instead.
        """
        stale: list[CaseViewStaleRef] = []
        match_none = False
        update: dict[str, object] = {}

        if query.tags:
            _, missing_tags = await split_case_tag_filters(
                session=self.session, workspace_id=self.workspace_id, tags=query.tags
            )
            if missing_tags:
                stale.extend(
                    CaseViewStaleRef(kind="tag", value=tag) for tag in missing_tags
                )
                kept = [tag for tag in query.tags if tag not in missing_tags]
                match_none = match_none or not kept
                update["tags"] = kept or None

        if query.dropdown:
            existing = await self._existing_dropdown_options(query.dropdown)
            missing_options = [
                entry for entry in query.dropdown if entry not in existing
            ]
            if missing_options:
                stale.extend(
                    CaseViewStaleRef(kind="dropdown", value=entry)
                    for entry in missing_options
                )
                kept = [entry for entry in query.dropdown if entry in existing]
                definitions = {entry.split(":", 1)[0] for entry in query.dropdown}
                kept_definitions = {entry.split(":", 1)[0] for entry in kept}
                match_none = match_none or definitions != kept_definitions
                update["dropdown"] = kept or None

        cleaned = query.model_copy(update=update) if update else query
        return _CleanedQuery(query=cleaned, stale_refs=stale, match_none=match_none)

    async def _existing_dropdown_options(self, entries: list[str]) -> set[str]:
        definition_refs = {entry.split(":", 1)[0] for entry in entries if ":" in entry}
        if not definition_refs:
            return set()
        rows = (
            await self.session.execute(
                select(CaseDropdownDefinition.ref, CaseDropdownOption.ref)
                .join(
                    CaseDropdownOption,
                    CaseDropdownOption.definition_id == CaseDropdownDefinition.id,
                )
                .where(
                    CaseDropdownDefinition.workspace_id == self.workspace_id,
                    CaseDropdownDefinition.ref.in_(definition_refs),
                )
            )
        ).tuples()
        return {f"{definition}:{option}" for definition, option in rows}
