"""Services for Watchtower monitor APIs and MCP ingestion."""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from sqlalchemy import and_, case, delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat import config
from tracecat.agent.mcp.metadata import strip_proxy_tool_metadata
from tracecat.db.engine import get_async_session_context_manager
from tracecat.db.models import (
    OrganizationMembership,
    User,
    WatchtowerAgent,
    WatchtowerAgentSession,
    WatchtowerAgentToolCall,
    Workspace,
)
from tracecat.exceptions import TracecatNotFoundError
from tracecat.pagination import BaseCursorPaginator
from tracecat.service import BaseOrgService
from tracecat.tiers.access import is_org_entitled
from tracecat.tiers.enums import Entitlement
from tracecat_ee.watchtower.schemas import (
    WatchtowerAgentListResponse,
    WatchtowerAgentRead,
    WatchtowerAgentSessionListResponse,
    WatchtowerAgentSessionRead,
    WatchtowerAgentToolCallListResponse,
    WatchtowerAgentToolCallRead,
)
from tracecat_ee.watchtower.types import (
    WatchtowerAgentSessionStatus,
    WatchtowerAgentStatus,
    WatchtowerAgentType,
    WatchtowerToolCallStatus,
)

SESSION_STALE_WINDOW_MINUTES = 30
OAUTH_PROVISIONAL_MATCH_WINDOW_MINUTES = 30
WATCHTOWER_RETENTION_DAYS = 30
WATCHTOWER_MAX_REDACTED_ITEMS = 32
WATCHTOWER_RETENTION_PRUNE_INTERVAL_SECONDS = 300

# Process-local throttle for opportunistic retention pruning. This is
# best-effort and intentionally avoids per-request cleanup on hot write paths.
_PRUNE_LAST_RUN_BY_ORG: dict[uuid.UUID, datetime] = {}


@dataclass(slots=True)
class WatchtowerAgentCallContext:
    """Minimal context required to persist a Watchtower tool-call event."""

    organization_id: uuid.UUID
    agent_id: uuid.UUID
    session_row_id: uuid.UUID


class WatchtowerService(BaseOrgService):
    """Org-scoped Watchtower management APIs."""

    service_name = "watchtower"

    async def list_agents(
        self,
        *,
        limit: int,
        cursor: str | None,
        agent_type: WatchtowerAgentType | None,
        status: WatchtowerAgentStatus | None,
    ) -> WatchtowerAgentListResponse:
        await maybe_prune_watchtower_retention(
            self.session,
            organization_id=self.organization_id,
        )
        retention_cutoff = _retention_cutoff()
        stale_cutoff = _session_stale_cutoff()

        # 1. Select paginated agent IDs with all filters applied first,
        #    so the counts aggregation is scoped to only the page.
        page_stmt = select(WatchtowerAgent.id).where(
            WatchtowerAgent.organization_id == self.organization_id,
            WatchtowerAgent.last_seen_at >= retention_cutoff,
        )
        if agent_type:
            page_stmt = page_stmt.where(WatchtowerAgent.agent_type == agent_type)
        if status == WatchtowerAgentStatus.BLOCKED:
            page_stmt = page_stmt.where(WatchtowerAgent.blocked_at.is_not(None))
        elif status == WatchtowerAgentStatus.ACTIVE:
            page_stmt = page_stmt.where(
                WatchtowerAgent.blocked_at.is_(None),
                WatchtowerAgent.last_seen_at >= stale_cutoff,
            )
        elif status == WatchtowerAgentStatus.IDLE:
            page_stmt = page_stmt.where(
                WatchtowerAgent.blocked_at.is_(None),
                WatchtowerAgent.last_seen_at < stale_cutoff,
            )

        page_stmt = _apply_desc_cursor_filter(
            page_stmt,
            model=WatchtowerAgent,
            cursor=cursor,
            sort_attr="last_seen_at",
        )
        page_stmt = page_stmt.order_by(
            WatchtowerAgent.last_seen_at.desc(), WatchtowerAgent.id.desc()
        )
        page_stmt = page_stmt.limit(limit + 1)
        page_subq = page_stmt.subquery("page")

        # 2. Aggregate session counts only for agents in the page.
        counts_subq = (
            select(
                WatchtowerAgentSession.agent_id,
                func.count().label("total"),
                func.count(
                    case(
                        (
                            and_(
                                WatchtowerAgentSession.session_state == "connected",
                                WatchtowerAgentSession.revoked_at.is_(None),
                                WatchtowerAgentSession.last_seen_at >= stale_cutoff,
                            ),
                            1,
                        )
                    )
                ).label("active"),
            )
            .where(
                WatchtowerAgentSession.organization_id == self.organization_id,
                WatchtowerAgentSession.last_seen_at >= retention_cutoff,
                WatchtowerAgentSession.agent_id.in_(select(page_subq.c.id)),
            )
            .group_by(WatchtowerAgentSession.agent_id)
            .subquery("counts")
        )

        # 3. Join full agent objects with counts for the page.
        stmt = (
            select(
                WatchtowerAgent,
                func.coalesce(counts_subq.c.total, 0).label("total_sessions"),
                func.coalesce(counts_subq.c.active, 0).label("active_sessions"),
            )
            .join(page_subq, WatchtowerAgent.id == page_subq.c.id)
            .outerjoin(counts_subq, WatchtowerAgent.id == counts_subq.c.agent_id)
            .order_by(WatchtowerAgent.last_seen_at.desc(), WatchtowerAgent.id.desc())
        )

        result = await self.session.execute(stmt)
        rows: list[tuple[WatchtowerAgent, int, int]] = list(result.tuples().all())
        has_more = len(rows) > limit
        if has_more:
            rows = rows[:limit]

        items = [
            WatchtowerAgentRead(
                id=agent.id,
                organization_id=agent.organization_id,
                fingerprint_hash=agent.fingerprint_hash,
                agent_type=_coerce_agent_type(agent.agent_type),
                agent_source=agent.agent_source,
                agent_icon_key=agent.agent_icon_key,
                raw_user_agent=agent.raw_user_agent,
                raw_client_info=cast(dict[str, object] | None, agent.raw_client_info),
                auth_client_id=agent.auth_client_id,
                last_user_id=agent.last_user_id,
                last_user_email=agent.last_user_email,
                last_user_name=agent.last_user_name,
                first_seen_at=agent.first_seen_at,
                last_seen_at=agent.last_seen_at,
                blocked_at=agent.blocked_at,
                blocked_reason=agent.blocked_reason,
                status=_derive_agent_status(agent),
                active_session_count=active,
                inactive_session_count=max(total - active, 0),
            )
            for agent, total, active in rows
        ]

        next_cursor = None
        if has_more and rows:
            last_agent = rows[-1][0]
            next_cursor = BaseCursorPaginator.encode_cursor(
                id=last_agent.id,
                sort_column="last_seen_at",
                sort_value=last_agent.last_seen_at,
            )

        return WatchtowerAgentListResponse(
            items=items,
            next_cursor=next_cursor,
            has_more=has_more,
        )

    async def list_agent_sessions(
        self,
        *,
        agent_id: uuid.UUID,
        limit: int,
        cursor: str | None,
        workspace_id: uuid.UUID | None,
        state: str | None,
    ) -> WatchtowerAgentSessionListResponse:
        await maybe_prune_watchtower_retention(
            self.session,
            organization_id=self.organization_id,
        )
        await self._assert_agent_exists(agent_id)
        retention_cutoff = _retention_cutoff()
        stmt = select(WatchtowerAgentSession).where(
            WatchtowerAgentSession.organization_id == self.organization_id,
            WatchtowerAgentSession.agent_id == agent_id,
            WatchtowerAgentSession.last_seen_at >= retention_cutoff,
        )
        if workspace_id is not None:
            stmt = stmt.where(WatchtowerAgentSession.workspace_id == workspace_id)
        if state is not None:
            stmt = stmt.where(WatchtowerAgentSession.session_state == state)

        stmt = _apply_desc_cursor_filter(
            stmt,
            model=WatchtowerAgentSession,
            cursor=cursor,
            sort_attr="last_seen_at",
        )
        stmt = stmt.order_by(
            WatchtowerAgentSession.last_seen_at.desc(),
            WatchtowerAgentSession.id.desc(),
        )
        stmt = stmt.limit(limit + 1)

        session_rows = await self.session.scalars(stmt)
        sessions: list[WatchtowerAgentSession] = list(session_rows.all())
        has_more = len(sessions) > limit
        if has_more:
            sessions = sessions[:limit]

        items = [
            WatchtowerAgentSessionRead(
                id=session.id,
                organization_id=session.organization_id,
                agent_id=session.agent_id,
                session_state=session.session_state,
                auth_transaction_id=session.auth_transaction_id,
                auth_client_id=session.auth_client_id,
                oauth_callback_seen_at=session.oauth_callback_seen_at,
                agent_session_id=session.agent_session_id,
                initialize_seen_at=session.initialize_seen_at,
                user_id=session.user_id,
                user_email=session.user_email,
                user_name=session.user_name,
                workspace_id=session.workspace_id,
                first_seen_at=session.first_seen_at,
                last_seen_at=session.last_seen_at,
                revoked_at=session.revoked_at,
                revoked_reason=session.revoked_reason,
                status=_derive_session_status(session),
            )
            for session in sessions
        ]

        next_cursor = None
        if has_more and sessions:
            last = sessions[-1]
            next_cursor = BaseCursorPaginator.encode_cursor(
                id=last.id,
                sort_column="last_seen_at",
                sort_value=last.last_seen_at,
            )

        return WatchtowerAgentSessionListResponse(
            items=items,
            next_cursor=next_cursor,
            has_more=has_more,
        )

    async def list_session_tool_calls(
        self,
        *,
        session_id: uuid.UUID,
        limit: int,
        cursor: str | None,
        status: WatchtowerToolCallStatus | None,
    ) -> WatchtowerAgentToolCallListResponse:
        await maybe_prune_watchtower_retention(
            self.session,
            organization_id=self.organization_id,
        )
        await self._assert_session_exists(session_id)
        retention_cutoff = _retention_cutoff()

        stmt = select(WatchtowerAgentToolCall).where(
            WatchtowerAgentToolCall.organization_id == self.organization_id,
            WatchtowerAgentToolCall.agent_session_id == session_id,
            WatchtowerAgentToolCall.called_at >= retention_cutoff,
        )
        if status is not None:
            stmt = stmt.where(WatchtowerAgentToolCall.call_status == status)

        stmt = _apply_desc_cursor_filter(
            stmt,
            model=WatchtowerAgentToolCall,
            cursor=cursor,
            sort_attr="called_at",
        )
        stmt = stmt.order_by(
            WatchtowerAgentToolCall.called_at.desc(),
            WatchtowerAgentToolCall.id.desc(),
        )
        stmt = stmt.limit(limit + 1)

        call_rows = await self.session.scalars(stmt)
        calls: list[WatchtowerAgentToolCall] = list(call_rows.all())
        has_more = len(calls) > limit
        if has_more:
            calls = calls[:limit]

        items = [
            WatchtowerAgentToolCallRead(
                id=call.id,
                organization_id=call.organization_id,
                agent_id=call.agent_id,
                agent_session_id=call.agent_session_id,
                workspace_id=call.workspace_id,
                tool_name=call.tool_name,
                call_status=_coerce_tool_call_status(call.call_status),
                latency_ms=call.latency_ms,
                args_redacted=cast(dict[str, object], call.args_redacted),
                error_redacted=call.error_redacted,
                called_at=call.called_at,
            )
            for call in calls
        ]

        next_cursor = None
        if has_more and calls:
            last = calls[-1]
            next_cursor = BaseCursorPaginator.encode_cursor(
                id=last.id,
                sort_column="called_at",
                sort_value=last.called_at,
            )

        return WatchtowerAgentToolCallListResponse(
            items=items,
            next_cursor=next_cursor,
            has_more=has_more,
        )

    async def revoke_session(self, session_id: uuid.UUID, reason: str | None) -> None:
        now = datetime.now(UTC)
        stmt = (
            update(WatchtowerAgentSession)
            .where(
                WatchtowerAgentSession.organization_id == self.organization_id,
                WatchtowerAgentSession.id == session_id,
            )
            .values(
                session_state="revoked",
                revoked_at=now,
                revoked_reason=reason,
                revoked_by_user_id=self.role.user_id,
                last_seen_at=now,
            )
            .returning(WatchtowerAgentSession.id)
        )
        updated_id = await self.session.scalar(stmt)
        if updated_id is None:
            raise TracecatNotFoundError("Watchtower session not found")
        await self.session.commit()

    async def disable_agent(self, agent_id: uuid.UUID, reason: str | None) -> None:
        now = datetime.now(UTC)
        stmt = (
            update(WatchtowerAgent)
            .where(
                WatchtowerAgent.organization_id == self.organization_id,
                WatchtowerAgent.id == agent_id,
            )
            .values(
                blocked_at=now,
                blocked_reason=reason,
                blocked_by_user_id=self.role.user_id,
                last_seen_at=now,
            )
            .returning(WatchtowerAgent.id)
        )
        updated_id = await self.session.scalar(stmt)
        if updated_id is None:
            raise TracecatNotFoundError("Watchtower agent not found")
        await self.session.commit()

    async def enable_agent(self, agent_id: uuid.UUID) -> None:
        stmt = (
            update(WatchtowerAgent)
            .where(
                WatchtowerAgent.organization_id == self.organization_id,
                WatchtowerAgent.id == agent_id,
            )
            .values(
                blocked_at=None,
                blocked_reason=None,
                blocked_by_user_id=None,
            )
            .returning(WatchtowerAgent.id)
        )
        updated_id = await self.session.scalar(stmt)
        if updated_id is None:
            raise TracecatNotFoundError("Watchtower agent not found")
        await self.session.commit()

    async def _assert_agent_exists(self, agent_id: uuid.UUID) -> None:
        stmt = select(
            select(WatchtowerAgent.id)
            .where(
                WatchtowerAgent.organization_id == self.organization_id,
                WatchtowerAgent.id == agent_id,
            )
            .exists()
        )
        if not await self.session.scalar(stmt):
            raise TracecatNotFoundError("Watchtower agent not found")

    async def _assert_session_exists(self, session_id: uuid.UUID) -> None:
        stmt = select(
            select(WatchtowerAgentSession.id)
            .where(
                WatchtowerAgentSession.organization_id == self.organization_id,
                WatchtowerAgentSession.id == session_id,
            )
            .exists()
        )
        if not await self.session.scalar(stmt):
            raise TracecatNotFoundError("Watchtower session not found")


def _apply_desc_cursor_filter(
    stmt: Any,
    *,
    model: type[WatchtowerAgent]
    | type[WatchtowerAgentSession]
    | type[WatchtowerAgentToolCall],
    cursor: str | None,
    sort_attr: str,
) -> Any:
    if not cursor:
        return stmt

    cursor_data = BaseCursorPaginator.decode_cursor(cursor)
    sort_value = cursor_data.sort_value
    if not isinstance(sort_value, datetime):
        raise ValueError("Invalid cursor sort value")

    cursor_id = uuid.UUID(cursor_data.id)
    sort_col = getattr(model, sort_attr)
    return stmt.where(
        or_(
            sort_col < sort_value,
            and_(sort_col == sort_value, model.id < cursor_id),
        )
    )


def _derive_agent_status(agent: WatchtowerAgent) -> WatchtowerAgentStatus:
    if agent.blocked_at is not None:
        return WatchtowerAgentStatus.BLOCKED
    if agent.last_seen_at >= _session_stale_cutoff():
        return WatchtowerAgentStatus.ACTIVE
    return WatchtowerAgentStatus.IDLE


def _derive_session_status(
    session: WatchtowerAgentSession,
) -> WatchtowerAgentSessionStatus:
    if session.session_state == "revoked" or session.revoked_at is not None:
        return WatchtowerAgentSessionStatus.REVOKED
    if session.last_seen_at >= _session_stale_cutoff():
        return WatchtowerAgentSessionStatus.ACTIVE
    return WatchtowerAgentSessionStatus.IDLE


def normalize_agent_identity(
    *,
    user_agent: str | None,
    client_info: dict[str, Any] | None,
) -> tuple[WatchtowerAgentType, str, str]:
    """Normalize local agent identity from MCP initialize/client metadata."""
    client_name = str(client_info.get("name") or "") if client_info else ""
    ua_text = user_agent or ""

    client_type = _classify_agent_text(client_name)
    ua_type = _classify_agent_text(ua_text)

    if (
        client_type != WatchtowerAgentType.UNKNOWN
        and ua_type != WatchtowerAgentType.UNKNOWN
        and client_type != ua_type
    ):
        return client_type, "mixed", client_type
    if client_type != WatchtowerAgentType.UNKNOWN:
        return client_type, "client_info", client_type
    if ua_type != WatchtowerAgentType.UNKNOWN:
        return ua_type, "user_agent", ua_type
    return WatchtowerAgentType.UNKNOWN, "unknown", "unknown"


def _classify_agent_text(value: str) -> WatchtowerAgentType:
    text = value.lower()
    if "claude" in text:
        return WatchtowerAgentType.CLAUDE_CODE
    if "codex" in text or "openai" in text:
        return WatchtowerAgentType.CODEX
    if "gemini" in text:
        return WatchtowerAgentType.GEMINI
    if "cursor" in text:
        return WatchtowerAgentType.CURSOR
    if "windsurf" in text:
        return WatchtowerAgentType.WINDSURF
    if "opencode" in text:
        return WatchtowerAgentType.OPENCODE
    if "openclaw" in text:
        return WatchtowerAgentType.OPENCLAW
    return WatchtowerAgentType.UNKNOWN


def _build_agent_fingerprint(
    *,
    organization_id: uuid.UUID,
    auth_client_id: str | None,
    agent_type: WatchtowerAgentType,
    user_agent: str | None,
    client_info: dict[str, Any] | None,
) -> str:
    del client_info
    raw = "|".join(
        [
            str(organization_id),
            auth_client_id or "",
            str(agent_type),
            user_agent or "",
        ]
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _display_name(user: User) -> str:
    parts = [user.first_name, user.last_name]
    display_name = " ".join(part for part in parts if part)
    return display_name or user.email


async def _resolve_user_by_email(
    session: AsyncSession,
    email: str,
) -> User | None:
    result = await session.execute(select(User).filter_by(email=email))
    return result.scalar_one_or_none()


def _coerce_agent_type(value: str | None) -> WatchtowerAgentType:
    if value is None:
        return WatchtowerAgentType.UNKNOWN
    try:
        return WatchtowerAgentType(value)
    except ValueError:
        return WatchtowerAgentType.UNKNOWN


def _coerce_tool_call_status(value: str | None) -> WatchtowerToolCallStatus:
    if value is None:
        return WatchtowerToolCallStatus.ERROR
    try:
        return WatchtowerToolCallStatus(value)
    except ValueError:
        return WatchtowerToolCallStatus.ERROR


async def _resolve_unambiguous_org(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    claimed_org_ids: frozenset[uuid.UUID] | None,
    claimed_workspace_ids: frozenset[uuid.UUID] | None,
) -> uuid.UUID | None:
    if claimed_org_ids and len(claimed_org_ids) == 1:
        return next(iter(claimed_org_ids))

    if claimed_workspace_ids and len(claimed_workspace_ids) == 1:
        workspace_id = next(iter(claimed_workspace_ids))
        ws_result = await session.execute(
            select(Workspace.organization_id).where(Workspace.id == workspace_id)
        )
        workspace_org_id = ws_result.scalar_one_or_none()
        if workspace_org_id is None:
            return None
        if claimed_org_ids and workspace_org_id not in claimed_org_ids:
            return None
        return workspace_org_id

    memberships_result = await session.execute(
        select(OrganizationMembership.organization_id).where(
            OrganizationMembership.user_id == user_id
        )
    )
    organization_ids = set(memberships_result.scalars().all())
    if claimed_org_ids:
        organization_ids &= set(claimed_org_ids)

    if len(organization_ids) == 1:
        return next(iter(organization_ids))
    return None


async def maybe_create_oauth_provisional_session(
    *,
    email: str,
    auth_client_id: str | None,
    auth_transaction_id: str | None,
    user_agent: str | None,
) -> None:
    """Create/update a provisional session at OAuth callback when org is unambiguous.

    Called from the OIDC proxy callback (browser-mediated), *before* the MCP
    client sends its ``initialize`` request.  The session is created in the
    ``awaiting_initialize`` state with ``agent_id=None`` because we don't yet
    have the client metadata needed to fingerprint the agent.  The subsequent
    ``ingest_watchtower_initialize_event`` call will match this provisional
    session and promote it to ``connected``.
    """
    # user_agent from the OAuth callback is the browser's UA, not the MCP
    # client's — discard it to avoid misleading fingerprints.
    del user_agent
    if not config.TRACECAT__EE_MULTI_TENANT:
        return

    async with get_async_session_context_manager() as session:
        user = await _resolve_user_by_email(session, email)
        if user is None:
            return

        # No org/workspace hints are available at the OAuth callback stage,
        # so we rely solely on the user's org memberships to resolve the org.
        organization_id = await _resolve_unambiguous_org(
            session,
            user_id=user.id,
            claimed_org_ids=None,
            claimed_workspace_ids=None,
        )
        if organization_id is None:
            return

        if not await is_org_entitled(session, organization_id, Entitlement.WATCHTOWER):
            return

        now = datetime.now(UTC)
        display_name = _display_name(user)

        # Match on auth_client_id exactly (including NULL) to avoid
        # cross-client collisions when the same user has multiple agents.
        auth_client_clause = (
            WatchtowerAgentSession.auth_client_id == auth_client_id
            if auth_client_id is not None
            else WatchtowerAgentSession.auth_client_id.is_(None)
        )
        recent_cutoff = now - timedelta(minutes=OAUTH_PROVISIONAL_MATCH_WINDOW_MINUTES)

        # Check for an existing provisional session from a recent OAuth
        # callback that hasn't yet been promoted by initialize.
        existing_result = await session.execute(
            select(WatchtowerAgentSession)
            .where(
                WatchtowerAgentSession.organization_id == organization_id,
                WatchtowerAgentSession.user_id == user.id,
                WatchtowerAgentSession.session_state == "awaiting_initialize",
                auth_client_clause,
                WatchtowerAgentSession.last_seen_at >= recent_cutoff,
            )
            .order_by(WatchtowerAgentSession.created_at.desc())
            .limit(1)
        )
        existing = existing_result.scalar_one_or_none()

        if existing is None:
            # First OAuth callback for this user/client pair — create a new
            # provisional session that initialize will pick up later.
            session.add(
                WatchtowerAgentSession(
                    id=uuid.uuid4(),
                    organization_id=organization_id,
                    agent_id=None,
                    session_state="awaiting_initialize",
                    auth_transaction_id=auth_transaction_id,
                    auth_client_id=auth_client_id,
                    oauth_callback_seen_at=now,
                    user_id=user.id,
                    user_email=user.email,
                    user_name=display_name,
                    first_seen_at=now,
                    last_seen_at=now,
                )
            )
        else:
            # Re-use the existing provisional session. Reset agent_id because
            # OAuth callback metadata is browser-mediated and may not match
            # the eventual MCP initialize fingerprint.
            existing.agent_id = None
            existing.auth_transaction_id = auth_transaction_id
            existing.oauth_callback_seen_at = now
            existing.last_seen_at = now
            existing.user_email = user.email
            existing.user_name = display_name

        await session.commit()


async def ingest_watchtower_initialize_event(
    *,
    email: str,
    auth_client_id: str | None,
    mcp_session_id: str,
    user_agent: str | None,
    client_info: dict[str, Any] | None,
    claimed_org_ids: frozenset[uuid.UUID] | None,
    claimed_workspace_ids: frozenset[uuid.UUID] | None,
) -> WatchtowerAgentCallContext | None:
    """Persist/associate Watchtower agent + session records on MCP initialize.

    Called from ``WatchtowerMonitorMiddleware.on_initialize`` when an MCP
    client sends its ``initialize`` request.  This is the second half of the
    two-phase session lifecycle:

    1. ``maybe_create_oauth_provisional_session`` (OAuth callback) creates a
       provisional ``awaiting_initialize`` session without agent metadata.
    2. This function matches/creates the agent fingerprint and promotes the
       provisional session to ``connected``, or creates a fresh session if
       no provisional match is found.

    Returns a ``WatchtowerAgentCallContext`` that the middleware caches for
    subsequent tool-call telemetry within the same MCP session.
    """
    if not config.TRACECAT__EE_MULTI_TENANT:
        return None

    async with get_async_session_context_manager() as session:
        user = await _resolve_user_by_email(session, email)
        if user is None:
            return None

        organization_id = await _resolve_unambiguous_org(
            session,
            user_id=user.id,
            claimed_org_ids=claimed_org_ids,
            claimed_workspace_ids=claimed_workspace_ids,
        )
        if organization_id is None:
            return None

        if not await is_org_entitled(session, organization_id, Entitlement.WATCHTOWER):
            return None

        # Classify the agent from client_info (MCP initialize params) and
        # user-agent header, then build a stable fingerprint for dedup.
        now = datetime.now(UTC)
        agent_type, agent_source, icon_key = normalize_agent_identity(
            user_agent=user_agent,
            client_info=client_info,
        )
        fingerprint_hash = _build_agent_fingerprint(
            organization_id=organization_id,
            auth_client_id=auth_client_id,
            agent_type=agent_type,
            user_agent=user_agent,
            client_info=client_info,
        )

        # Upsert the agent record keyed on fingerprint_hash.
        agent_result = await session.execute(
            select(WatchtowerAgent).where(
                WatchtowerAgent.organization_id == organization_id,
                WatchtowerAgent.fingerprint_hash == fingerprint_hash,
            )
        )
        agent = agent_result.scalar_one_or_none()
        if agent is None:
            agent = WatchtowerAgent(
                id=uuid.uuid4(),
                organization_id=organization_id,
                fingerprint_hash=fingerprint_hash,
                agent_type=agent_type,
                agent_source=agent_source,
                agent_icon_key=icon_key,
                raw_user_agent=user_agent,
                raw_client_info=client_info,
                auth_client_id=auth_client_id,
                last_user_id=user.id,
                last_user_email=user.email,
                last_user_name=_display_name(user),
                first_seen_at=now,
                last_seen_at=now,
            )
            session.add(agent)
        else:
            # Refresh metadata — the same fingerprint may reconnect with
            # updated client_info or a different user.
            agent.agent_type = agent_type
            agent.agent_source = agent_source
            agent.agent_icon_key = icon_key
            agent.raw_user_agent = user_agent
            agent.raw_client_info = client_info
            agent.auth_client_id = auth_client_id
            agent.last_user_id = user.id
            agent.last_user_email = user.email
            agent.last_user_name = _display_name(user)
            agent.last_seen_at = now

        # --- Session matching ---
        # Try to find a provisional session created by the OAuth callback
        # (awaiting_initialize), scoped to the same user and client.
        auth_client_clause = (
            WatchtowerAgentSession.auth_client_id == auth_client_id
            if auth_client_id is not None
            else WatchtowerAgentSession.auth_client_id.is_(None)
        )
        recent_cutoff = now - timedelta(minutes=OAUTH_PROVISIONAL_MATCH_WINDOW_MINUTES)
        provisional_result = await session.execute(
            select(WatchtowerAgentSession)
            .where(
                WatchtowerAgentSession.organization_id == organization_id,
                WatchtowerAgentSession.session_state == "awaiting_initialize",
                WatchtowerAgentSession.user_id == user.id,
                auth_client_clause,
                WatchtowerAgentSession.last_seen_at >= recent_cutoff,
            )
            .order_by(WatchtowerAgentSession.oauth_callback_seen_at.desc().nullslast())
            .limit(1)
        )
        tracked_session = provisional_result.scalar_one_or_none()

        # Fallback: check for an existing session with the same MCP session ID
        # (e.g. a reconnect after a transient disconnect).
        if tracked_session is None:
            existing_result = await session.execute(
                select(WatchtowerAgentSession).where(
                    WatchtowerAgentSession.organization_id == organization_id,
                    WatchtowerAgentSession.agent_session_id == mcp_session_id,
                )
            )
            tracked_session = existing_result.scalar_one_or_none()

        if tracked_session is None:
            # No provisional or existing session — create a fresh one.
            tracked_session = WatchtowerAgentSession(
                id=uuid.uuid4(),
                organization_id=organization_id,
                agent_id=agent.id,
                session_state="connected",
                auth_client_id=auth_client_id,
                agent_session_id=mcp_session_id,
                initialize_seen_at=now,
                user_id=user.id,
                user_email=user.email,
                user_name=_display_name(user),
                first_seen_at=now,
                last_seen_at=now,
            )
            session.add(tracked_session)
        else:
            # Promote the provisional/existing session to connected and
            # associate it with the resolved agent.
            tracked_session.agent_id = agent.id
            tracked_session.session_state = "connected"
            tracked_session.agent_session_id = mcp_session_id
            tracked_session.initialize_seen_at = now
            tracked_session.user_id = user.id
            tracked_session.user_email = user.email
            tracked_session.user_name = _display_name(user)
            tracked_session.last_seen_at = now

        await session.commit()
        return WatchtowerAgentCallContext(
            organization_id=organization_id,
            agent_id=agent.id,
            session_row_id=tracked_session.id,
        )


async def get_watchtower_tool_call_context(
    *,
    email: str,
    mcp_session_id: str,
    claimed_org_ids: frozenset[uuid.UUID] | None,
    claimed_workspace_ids: frozenset[uuid.UUID] | None,
) -> tuple[WatchtowerAgentCallContext | None, str | None]:
    """Resolve Watchtower call context and block reason (if blocked/revoked).

    Called by ``WatchtowerMonitorMiddleware.on_call_tool`` on every tool
    invocation.  Returns ``(context, None)`` for allowed calls, or
    ``(context, reason)`` when the agent or session has been blocked/revoked
    by an org admin — the middleware uses the reason string to raise a
    ``ToolError`` and reject the call.

    Returns ``(None, None)`` when Watchtower tracking doesn't apply (e.g.
    single-tenant, unresolvable user/org, missing entitlement, or no
    matching session).
    """
    if not config.TRACECAT__EE_MULTI_TENANT:
        return None, None

    async with get_async_session_context_manager() as session:
        user = await _resolve_user_by_email(session, email)
        if user is None:
            return None, None

        organization_id = await _resolve_unambiguous_org(
            session,
            user_id=user.id,
            claimed_org_ids=claimed_org_ids,
            claimed_workspace_ids=claimed_workspace_ids,
        )
        if organization_id is None:
            return None, None

        if not await is_org_entitled(session, organization_id, Entitlement.WATCHTOWER):
            return None, None

        retention_cutoff = _retention_cutoff()
        context_result = await session.execute(
            select(
                WatchtowerAgentSession.id,
                WatchtowerAgentSession.session_state,
                WatchtowerAgentSession.revoked_at,
                WatchtowerAgent.id,
                WatchtowerAgent.blocked_at,
            )
            .join(
                WatchtowerAgent,
                and_(
                    WatchtowerAgent.id == WatchtowerAgentSession.agent_id,
                    WatchtowerAgent.organization_id
                    == WatchtowerAgentSession.organization_id,
                    WatchtowerAgent.last_seen_at >= retention_cutoff,
                ),
            )
            .where(
                WatchtowerAgentSession.organization_id == organization_id,
                WatchtowerAgentSession.agent_session_id == mcp_session_id,
                WatchtowerAgentSession.last_seen_at >= retention_cutoff,
            )
        )
        row = context_result.tuples().one_or_none()
        if row is None:
            return None, None

        session_row_id, session_state, revoked_at, agent_id, blocked_at = row

        ctx = WatchtowerAgentCallContext(
            organization_id=organization_id,
            agent_id=agent_id,
            session_row_id=session_row_id,
        )

        # Check agent-level and session-level access controls.
        if blocked_at is not None:
            return ctx, "This local agent has been disabled by your organization admin."
        if session_state == "revoked" or revoked_at is not None:
            return (
                ctx,
                "This local agent session has been revoked by your organization admin.",
            )

        return ctx, None


async def record_watchtower_tool_call(
    *,
    call_context: WatchtowerAgentCallContext,
    tool_name: str,
    call_status: str,
    latency_ms: int | None,
    workspace_id: uuid.UUID | None,
    tool_args: Mapping[str, Any] | None,
    error_redacted: str | None,
    email: str | None,
) -> None:
    """Persist a Watchtower tool-call event and refresh activity timestamps.

    Called from ``WatchtowerMonitorMiddleware.on_call_tool`` after the tool
    executes (or is blocked).  Each invocation:

    1. Re-resolves the session and agent rows (they may have been revoked
       or pruned between the context lookup and the actual recording).
    2. Refreshes ``last_seen_at`` on both the session and agent so the
       dashboard reflects recent activity.
    3. Inserts a ``WatchtowerAgentToolCall`` row with redacted arguments
       and an optional truncated error summary.
    """
    if not config.TRACECAT__EE_MULTI_TENANT:
        return

    async with get_async_session_context_manager() as session:
        # Re-fetch session and agent — they may have been revoked or pruned
        # between the context lookup and now.
        tracked_session_result = await session.execute(
            select(WatchtowerAgentSession).where(
                WatchtowerAgentSession.organization_id == call_context.organization_id,
                WatchtowerAgentSession.id == call_context.session_row_id,
            )
        )
        tracked_session = tracked_session_result.scalar_one_or_none()
        if tracked_session is None:
            return

        agent_result = await session.execute(
            select(WatchtowerAgent).where(
                WatchtowerAgent.organization_id == call_context.organization_id,
                WatchtowerAgent.id == call_context.agent_id,
            )
        )
        agent = agent_result.scalar_one_or_none()
        if agent is None:
            return

        now = datetime.now(UTC)

        # Keep user attribution up to date — the email from the access
        # token may differ from what was stored at session creation.
        user: User | None = None
        if email is not None:
            user = await _resolve_user_by_email(session, email)

        if user is not None:
            display_name = _display_name(user)
            tracked_session.user_id = user.id
            tracked_session.user_email = user.email
            tracked_session.user_name = display_name
            agent.last_user_id = user.id
            agent.last_user_email = user.email
            agent.last_user_name = display_name

        # Refresh activity timestamps so the dashboard shows recent activity.
        tracked_session.last_seen_at = now
        tracked_session.workspace_id = workspace_id or tracked_session.workspace_id
        agent.last_seen_at = now

        tool_call = WatchtowerAgentToolCall(
            id=uuid.uuid4(),
            organization_id=call_context.organization_id,
            agent_id=call_context.agent_id,
            agent_session_id=call_context.session_row_id,
            workspace_id=workspace_id,
            tool_name=tool_name,
            call_status=call_status,
            latency_ms=latency_ms,
            args_redacted=redact_tool_call_args(tool_args),
            error_redacted=_sanitize_error_redacted(error_redacted),
            called_at=now,
        )
        session.add(tool_call)
        await session.commit()


def _sanitize_error_redacted(error_summary: str | None) -> str | None:
    if error_summary is None:
        return None
    normalized = error_summary.strip()
    if not normalized:
        return None
    if len(normalized) > 2000:
        return f"{normalized[:1997]}..."
    return normalized


def _retention_cutoff(now: datetime | None = None) -> datetime:
    reference = now or datetime.now(UTC)
    return reference - timedelta(days=WATCHTOWER_RETENTION_DAYS)


def _session_stale_cutoff(now: datetime | None = None) -> datetime:
    reference = now or datetime.now(UTC)
    return reference - timedelta(minutes=SESSION_STALE_WINDOW_MINUTES)


async def maybe_prune_watchtower_retention(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    now: datetime | None = None,
) -> None:
    """Prune retention at a throttled cadence to avoid hot-path write overhead."""
    if not isinstance(session, AsyncSession):
        return

    reference = now or datetime.now(UTC)
    last_run = _PRUNE_LAST_RUN_BY_ORG.get(organization_id)
    if (
        last_run is not None
        and (reference - last_run).total_seconds()
        < WATCHTOWER_RETENTION_PRUNE_INTERVAL_SECONDS
    ):
        return
    async with get_async_session_context_manager() as prune_session:
        await prune_watchtower_retention(
            prune_session,
            organization_id=organization_id,
            now=reference,
        )
        await prune_session.commit()
    _PRUNE_LAST_RUN_BY_ORG[organization_id] = reference


async def prune_watchtower_retention(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    now: datetime | None = None,
) -> None:
    """Opportunistically prune Watchtower telemetry older than the retention window."""
    cutoff = _retention_cutoff(now)
    await session.execute(
        delete(WatchtowerAgentToolCall).where(
            WatchtowerAgentToolCall.organization_id == organization_id,
            WatchtowerAgentToolCall.called_at < cutoff,
        )
    )
    await session.execute(
        delete(WatchtowerAgentSession).where(
            WatchtowerAgentSession.organization_id == organization_id,
            WatchtowerAgentSession.last_seen_at < cutoff,
        )
    )
    await session.execute(
        delete(WatchtowerAgent).where(
            WatchtowerAgent.organization_id == organization_id,
            WatchtowerAgent.last_seen_at < cutoff,
        )
    )


def redact_tool_call_args(
    arguments: Mapping[str, Any] | None,
) -> dict[str, object]:
    """Create a structural argument summary without persisting raw values."""
    sanitized_arguments = strip_proxy_tool_metadata(arguments)
    if not sanitized_arguments:
        return {"arg_count": 0, "keys": [], "args": {}}

    truncated = False
    entries: list[tuple[str, Any]] = []
    for key, value in sanitized_arguments.items():
        entries.append((str(key), value))
        if len(entries) >= WATCHTOWER_MAX_REDACTED_ITEMS:
            truncated = len(sanitized_arguments) > WATCHTOWER_MAX_REDACTED_ITEMS
            break

    redacted: dict[str, object] = {key: _redact_value(value) for key, value in entries}
    return {
        "arg_count": len(sanitized_arguments),
        "keys": [key for key, _ in entries],
        "truncated": truncated,
        "args": redacted,
    }


def _redact_value(value: Any) -> dict[str, object]:
    if value is None:
        return {"type": "null"}
    if isinstance(value, bool):
        return {"type": "bool"}
    if isinstance(value, int):
        return {"type": "int"}
    if isinstance(value, float):
        return {"type": "float"}
    if isinstance(value, str):
        return {"type": "str", "length": len(value)}
    if isinstance(value, Mapping):
        keys = [str(key) for key in value.keys()]
        return {
            "type": "object",
            "key_count": len(value),
            "keys": keys[:WATCHTOWER_MAX_REDACTED_ITEMS],
            "truncated": len(keys) > WATCHTOWER_MAX_REDACTED_ITEMS,
        }
    if isinstance(value, (list, tuple, set, frozenset)):
        return {
            "type": "array",
            "length": len(value),
        }
    return {"type": type(value).__name__}
