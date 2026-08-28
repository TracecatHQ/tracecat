"""Historical reconstruction of agent-driven case mutation interactions."""

from __future__ import annotations

import uuid
from collections import Counter, defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

import orjson
from pydantic import ValidationError
from sqlalchemy import exists, select, tuple_
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.agent.mcp.utils import (
    LEGACY_REGISTRY_MCP_SERVER_NAME,
    REGISTRY_MCP_SERVER_NAME,
    normalize_mcp_tool_name,
)
from tracecat.agent.session.history import decode_raw_session_line
from tracecat.agent.subagents import ResolvedAgentsConfig
from tracecat.cases.agent_sessions.types import (
    CaseAgentSessionBackfillReport,
    CaseAgentSessionBackfillSkipReason,
)
from tracecat.cases.enums import CaseAgentSessionInteractionOperation
from tracecat.db.models import (
    AgentSession,
    AgentSessionHistory,
    Case,
    CaseAgentSessionInteraction,
    CaseComment,
)
from tracecat.logger import logger

_CREATE_CASE = "core.cases.create_case"
_UPDATE_COMMENT = "core.cases.update_comment"
# Successful case deletions leave no case row for the interaction foreign key.
_CASE_ID_ACTIONS = frozenset(
    {
        "core.cases.add_case_tag",
        "core.cases.assign_user",
        "core.cases.assign_user_by_email",
        "core.cases.create_comment",
        "core.cases.delete_attachment",
        "core.cases.insert_row",
        "core.cases.link_row",
        "core.cases.remove_case_tag",
        "core.cases.reply_to_comment",
        "core.cases.unlink_row",
        "core.cases.update_case",
        "core.cases.upload_attachment",
        "core.cases.upload_attachment_from_url",
    }
)
_SUPPORTED_ACTIONS = _CASE_ID_ACTIONS | {_CREATE_CASE, _UPDATE_COMMENT}


@dataclass(frozen=True, slots=True)
class _PendingMutation:
    operation: CaseAgentSessionInteractionOperation
    target: Literal["case", "comment"] | None = None
    target_id: uuid.UUID | None = None


@dataclass(frozen=True, slots=True)
class _Mutation:
    target: Literal["case", "comment"]
    target_id: uuid.UUID
    operation: CaseAgentSessionInteractionOperation


@dataclass(frozen=True, slots=True)
class _Session:
    surrogate_id: int
    id: uuid.UUID
    workspace_id: uuid.UUID
    trusted_mcp_server_names: frozenset[str]


type _SourceMutation = tuple[_Session, _Mutation]
type _ResolvedMutation = tuple[_Session, _Mutation, uuid.UUID]
type _InteractionKey = tuple[
    uuid.UUID,
    uuid.UUID,
    uuid.UUID,
    CaseAgentSessionInteractionOperation,
]


def _parse_uuid(value: object) -> uuid.UUID | None:
    if isinstance(value, uuid.UUID):
        return value
    if not isinstance(value, str):
        return None
    try:
        return uuid.UUID(value)
    except ValueError:
        return None


def _trusted_mcp_server_names(
    agents_binding: dict[str, Any] | None,
) -> frozenset[str]:
    names = {REGISTRY_MCP_SERVER_NAME, LEGACY_REGISTRY_MCP_SERVER_NAME}
    if agents_binding is None:
        return frozenset(names)
    try:
        subagents = ResolvedAgentsConfig.model_validate(agents_binding).subagents
    except ValidationError:
        return frozenset(names)
    for subagent in subagents:
        names.add(f"{REGISTRY_MCP_SERVER_NAME}-{subagent.alias}")
        names.add(f"{LEGACY_REGISTRY_MCP_SERVER_NAME}-{subagent.alias}")
    return frozenset(names)


def _is_tracecat_owned_tool(
    name: str,
    trusted_mcp_server_names: frozenset[str],
) -> bool:
    """Reject tools routed through MCP servers Tracecat does not own."""
    for separator in ("__", "."):
        if not name.startswith(f"mcp{separator}"):
            continue
        parts = name.split(separator, 2)
        if len(parts) != 3:
            return False
        return parts[1] in trusted_mcp_server_names
    return True


def _resolve_tool(
    block: Mapping[str, Any],
    trusted_mcp_server_names: frozenset[str],
) -> tuple[str, Mapping[str, Any] | None] | None:
    """Resolve a supported direct or legacy wrapper tool call."""
    name = block.get("name")
    if not isinstance(name, str) or not _is_tracecat_owned_tool(
        name, trusted_mcp_server_names
    ):
        return None

    action = normalize_mcp_tool_name(name)
    arguments = block.get("input")
    if not isinstance(arguments, dict):
        arguments = None

    if action == "execute_tool":
        if arguments is None:
            return None
        wrapped_name = arguments.get("tool_name")
        if not isinstance(wrapped_name, str) or not _is_tracecat_owned_tool(
            wrapped_name,
            trusted_mcp_server_names,
        ):
            return None
        action = normalize_mcp_tool_name(wrapped_name)
        arguments = arguments.get("args", arguments)
        if not isinstance(arguments, dict):
            arguments = None

    if action not in _SUPPORTED_ACTIONS:
        return None
    return action, arguments


def _pending_mutation(
    action: str,
    arguments: Mapping[str, Any] | None,
) -> _PendingMutation | None:
    if arguments is None:
        return None
    if action == _CREATE_CASE:
        return _PendingMutation(CaseAgentSessionInteractionOperation.CREATE)

    if action == _UPDATE_COMMENT:
        target: Literal["case", "comment"] = "comment"
        target_id = _parse_uuid(arguments.get("comment_id"))
    else:
        target = "case"
        target_id = _parse_uuid(arguments.get("case_id"))
    if target_id is None:
        return None
    return _PendingMutation(
        operation=CaseAgentSessionInteractionOperation.UPDATE,
        target=target,
        target_id=target_id,
    )


def _result_mapping(value: object, *, depth: int = 0) -> Mapping[str, Any] | None:
    """Unwrap JSON and MCP text-block result shapes stored by Claude."""
    if depth > 3:
        return None
    match value:
        case str() as text:
            try:
                decoded = orjson.loads(text)
            except orjson.JSONDecodeError:
                return None
            return _result_mapping(decoded, depth=depth + 1)
        case list() as items:
            for item in items:
                if mapping := _result_mapping(item, depth=depth + 1):
                    return mapping
            return None
        case dict() as mapping:
            if "id" in mapping or "success" in mapping:
                return mapping
            if mapping.get("type") == "text":
                return _result_mapping(mapping.get("text"), depth=depth + 1)
            if "content" in mapping:
                return _result_mapping(mapping["content"], depth=depth + 1)
            return mapping
        case _:
            return None


def _is_failed_result(tool_result: Mapping[str, Any]) -> bool:
    if tool_result.get("is_error") is True:
        return True
    result = _result_mapping(tool_result.get("content"))
    return result is not None and result.get("success") is False


def _history_content(entry: AgentSessionHistory) -> Mapping[str, Any]:
    if entry.raw_session_line is not None:
        try:
            return decode_raw_session_line(entry.raw_session_line).content
        except (UnicodeDecodeError, ValueError):
            pass
    return entry.content


def _parse_mutations(
    entries: Sequence[AgentSessionHistory],
    trusted_mcp_server_names: frozenset[str],
) -> tuple[list[_Mutation], Counter[CaseAgentSessionBackfillSkipReason]]:
    """Extract successful case mutations from ordered session history."""
    pending: dict[str, _PendingMutation] = {}
    mutations: list[_Mutation] = []
    skipped: Counter[CaseAgentSessionBackfillSkipReason] = Counter()

    for entry in entries:
        content = _history_content(entry)
        message = content.get("message")
        if not isinstance(message, dict):
            continue
        blocks = message.get("content")
        if not isinstance(blocks, list):
            continue

        if content.get("type") == "assistant":
            for block in blocks:
                if not isinstance(block, dict) or block.get("type") != "tool_use":
                    continue
                tool = _resolve_tool(block, trusted_mcp_server_names)
                if tool is None:
                    continue
                tool_call_id = block.get("id")
                mutation = _pending_mutation(*tool)
                if not isinstance(tool_call_id, str) or mutation is None:
                    skipped[
                        CaseAgentSessionBackfillSkipReason.UNPARSEABLE_TOOL_CALL
                    ] += 1
                    continue
                if tool_call_id in pending:
                    skipped[
                        CaseAgentSessionBackfillSkipReason.UNPARSEABLE_TOOL_CALL
                    ] += 1
                pending[tool_call_id] = mutation
            continue

        if content.get("type") != "user":
            continue
        for block in blocks:
            if not isinstance(block, dict) or block.get("type") != "tool_result":
                continue
            tool_call_id = block.get("tool_use_id")
            if not isinstance(tool_call_id, str):
                continue
            mutation = pending.pop(tool_call_id, None)
            if mutation is None:
                continue
            if _is_failed_result(block):
                skipped[CaseAgentSessionBackfillSkipReason.FAILED_TOOL_CALL] += 1
                continue

            target = mutation.target
            target_id = mutation.target_id
            if mutation.operation == CaseAgentSessionInteractionOperation.CREATE:
                target = "case"
                result = _result_mapping(block.get("content"))
                target_id = _parse_uuid(result.get("id")) if result else None
            if target is None or target_id is None:
                skipped[CaseAgentSessionBackfillSkipReason.UNPARSEABLE_TOOL_CALL] += 1
                continue
            mutations.append(
                _Mutation(
                    target=target,
                    target_id=target_id,
                    operation=mutation.operation,
                )
            )

    if pending:
        skipped[CaseAgentSessionBackfillSkipReason.INCOMPLETE_TOOL_CALL] = len(pending)
    return mutations, skipped


class CaseAgentSessionBackfill:
    """Backfill historical case mutations in bounded transactions."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def _next_batch(
        self,
        *,
        after_surrogate_id: int,
        batch_size: int,
    ) -> list[_Session]:
        history_exists = exists(
            select(AgentSessionHistory.surrogate_id).where(
                AgentSessionHistory.session_id == AgentSession.id
            )
        )
        rows = (
            await self.session.execute(
                select(
                    AgentSession.surrogate_id,
                    AgentSession.id,
                    AgentSession.workspace_id,
                    AgentSession.agents_binding,
                )
                .where(
                    AgentSession.surrogate_id > after_surrogate_id,
                    history_exists,
                )
                .order_by(AgentSession.surrogate_id)
                .limit(batch_size)
            )
        ).tuples()
        return [
            _Session(
                surrogate_id=surrogate_id,
                id=session_id,
                workspace_id=workspace_id,
                trusted_mcp_server_names=_trusted_mcp_server_names(agents_binding),
            )
            for surrogate_id, session_id, workspace_id, agents_binding in rows
        ]

    async def _load_histories(
        self,
        sessions: Sequence[_Session],
    ) -> dict[uuid.UUID, list[AgentSessionHistory]]:
        entries = (
            await self.session.scalars(
                select(AgentSessionHistory)
                .where(
                    AgentSessionHistory.session_id.in_(
                        session.id for session in sessions
                    )
                )
                .order_by(
                    AgentSessionHistory.session_id,
                    AgentSessionHistory.surrogate_id,
                )
            )
        ).all()
        histories: dict[uuid.UUID, list[AgentSessionHistory]] = defaultdict(list)
        for entry in entries:
            histories[entry.session_id].append(entry)
        return histories

    async def _resolve_root_session_ids(
        self,
        sources: set[_Session],
    ) -> dict[_Session, uuid.UUID | None]:
        """Resolve one lineage level at a time for every source in the batch."""
        unresolved = {source: source.id for source in sources}
        visited = {source: {source.id} for source in sources}
        roots: dict[_Session, uuid.UUID | None] = {}

        while unresolved:
            session_keys = {
                (source.workspace_id, session_id)
                for source, session_id in unresolved.items()
            }
            rows = (
                await self.session.execute(
                    select(
                        AgentSession.workspace_id,
                        AgentSession.id,
                        AgentSession.parent_session_id,
                    ).where(
                        tuple_(AgentSession.workspace_id, AgentSession.id).in_(
                            session_keys
                        )
                    )
                )
            ).tuples()
            parents = {
                (workspace_id, session_id): parent_id
                for workspace_id, session_id, parent_id in rows
            }

            next_level: dict[_Session, uuid.UUID] = {}
            for source, session_id in unresolved.items():
                key = (source.workspace_id, session_id)
                if key not in parents:
                    roots[source] = None
                    continue

                parent_id = parents[key]
                if parent_id is None:
                    roots[source] = session_id
                elif parent_id in visited[source]:
                    roots[source] = None
                else:
                    visited[source].add(parent_id)
                    next_level[source] = parent_id
            unresolved = next_level

        return roots

    async def _resolve_interactions(
        self,
        source_mutations: Sequence[_SourceMutation],
    ) -> tuple[set[_InteractionKey], Counter[CaseAgentSessionBackfillSkipReason]]:
        """Resolve comment targets, cases, and root sessions for one batch."""
        comment_keys = {
            (source.workspace_id, mutation.target_id)
            for source, mutation in source_mutations
            if mutation.target == "comment"
        }
        comment_cases: dict[tuple[uuid.UUID, uuid.UUID], uuid.UUID] = {}
        if comment_keys:
            rows = (
                await self.session.execute(
                    select(
                        CaseComment.workspace_id,
                        CaseComment.id,
                        CaseComment.case_id,
                    ).where(
                        tuple_(CaseComment.workspace_id, CaseComment.id).in_(
                            comment_keys
                        )
                    )
                )
            ).tuples()
            comment_cases = {
                (workspace_id, comment_id): case_id
                for workspace_id, comment_id, case_id in rows
            }

        skipped: Counter[CaseAgentSessionBackfillSkipReason] = Counter()
        resolved: list[_ResolvedMutation] = []
        for source, mutation in source_mutations:
            if mutation.target == "case":
                case_id = mutation.target_id
            else:
                case_id = comment_cases.get((source.workspace_id, mutation.target_id))
                if case_id is None:
                    skipped[CaseAgentSessionBackfillSkipReason.MISSING_COMMENT] += 1
                    continue
            resolved.append((source, mutation, case_id))

        case_keys = {(source.workspace_id, case_id) for source, _, case_id in resolved}
        existing_cases: set[tuple[uuid.UUID, uuid.UUID]] = set()
        if case_keys:
            existing_cases = set(
                (
                    await self.session.execute(
                        select(Case.workspace_id, Case.id).where(
                            tuple_(Case.workspace_id, Case.id).in_(case_keys)
                        )
                    )
                )
                .tuples()
                .all()
            )

        valid: list[_ResolvedMutation] = []
        for source, mutation, case_id in resolved:
            if (source.workspace_id, case_id) not in existing_cases:
                skipped[CaseAgentSessionBackfillSkipReason.MISSING_CASE] += 1
                continue
            valid.append((source, mutation, case_id))

        root_ids = await self._resolve_root_session_ids(
            {source for source, _, _ in valid}
        )
        interactions: set[_InteractionKey] = set()
        for source, mutation, case_id in valid:
            root_id = root_ids[source]
            if root_id is None:
                skipped[CaseAgentSessionBackfillSkipReason.INVALID_SESSION_LINEAGE] += 1
                continue

            interaction = (
                source.workspace_id,
                root_id,
                case_id,
                mutation.operation,
            )
            interactions.add(interaction)

        return interactions, skipped

    async def _insert_interactions(
        self,
        interactions: set[_InteractionKey],
    ) -> tuple[int, int]:
        if not interactions:
            return 0, 0

        values = [
            {
                "id": uuid.uuid4(),
                "workspace_id": workspace_id,
                "agent_session_id": root_id,
                "case_id": case_id,
                "operation": operation,
            }
            for workspace_id, root_id, case_id, operation in sorted(
                interactions,
                key=lambda item: tuple(str(value) for value in item),
            )
        ]
        inserted_ids = await self.session.scalars(
            pg_insert(CaseAgentSessionInteraction)
            .values(values)
            .on_conflict_do_nothing(
                index_elements=[
                    "workspace_id",
                    "case_id",
                    "agent_session_id",
                    "operation",
                ]
            )
            .returning(CaseAgentSessionInteraction.id)
        )
        inserted = len(inserted_ids.all())
        return inserted, len(interactions) - inserted

    async def run(
        self,
        *,
        batch_size: int = 100,
        on_batch_complete: Callable[[], None] | None = None,
    ) -> CaseAgentSessionBackfillReport:
        """Run the restart-safe backfill, committing after each session batch."""
        if batch_size < 1:
            raise ValueError("batch_size must be positive")

        after_surrogate_id = 0
        batches_processed = 0
        sessions_scanned = 0
        history_rows_scanned = 0
        mutation_candidates = 0
        inserted = 0
        existing = 0
        skipped: Counter[CaseAgentSessionBackfillSkipReason] = Counter()

        while sessions := await self._next_batch(
            after_surrogate_id=after_surrogate_id,
            batch_size=batch_size,
        ):
            histories = await self._load_histories(sessions)
            source_mutations: list[_SourceMutation] = []
            batch_skips: Counter[CaseAgentSessionBackfillSkipReason] = Counter()
            for source in sessions:
                mutations, parse_skips = _parse_mutations(
                    histories.get(source.id, []),
                    source.trusted_mcp_server_names,
                )
                source_mutations.extend((source, mutation) for mutation in mutations)
                batch_skips.update(parse_skips)

            interactions, resolution_skips = await self._resolve_interactions(
                source_mutations
            )
            batch_inserted, batch_existing = await self._insert_interactions(
                interactions
            )
            batch_skips.update(resolution_skips)

            batches_processed += 1
            batch_history_rows = sum(len(entries) for entries in histories.values())
            sessions_scanned += len(sessions)
            history_rows_scanned += batch_history_rows
            mutation_candidates += len(source_mutations)
            inserted += batch_inserted
            existing += batch_existing
            skipped.update(batch_skips)
            after_surrogate_id = sessions[-1].surrogate_id

            await self.session.commit()
            if on_batch_complete is not None:
                on_batch_complete()
            logger.info(
                "Processed case-agent interaction backfill batch",
                batch_number=batches_processed,
                sessions=len(sessions),
                history_rows=batch_history_rows,
                mutation_candidates=len(source_mutations),
                inserted=batch_inserted,
                existing=batch_existing,
                skipped=sum(batch_skips.values()),
            )

        return CaseAgentSessionBackfillReport(
            batches_processed=batches_processed,
            sessions_scanned=sessions_scanned,
            history_rows_scanned=history_rows_scanned,
            mutation_candidates=mutation_candidates,
            inserted=inserted,
            existing=existing,
            skipped=dict(sorted(skipped.items(), key=lambda item: item[0].value)),
        )
