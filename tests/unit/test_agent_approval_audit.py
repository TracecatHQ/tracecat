from __future__ import annotations

import asyncio
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any, Literal
from unittest.mock import AsyncMock, Mock

import pytest
from pydantic_ai.tools import ToolApproved, ToolDenied

from tracecat.agent.session import service as session_service_module
from tracecat.agent.session.service import (
    AgentSessionService,
    _emit_approval_audit_events,
    _PendingApproval,
    _schedule_approval_audit_events,
    _should_emit_approval_audit,
    _ValidatedContinuation,
)
from tracecat.audit.sanitization import sanitize_audit_metadata
from tracecat.audit.types import AuditEventInput
from tracecat.auth.types import Role
from tracecat.chat.schemas import ApprovalDecision, ContinueRunRequest
from tracecat.contexts import RequestAuditContext


def _user_role() -> Role:
    return Role(
        type="user",
        workspace_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        service_id="tracecat-api",
    )


def _event() -> AuditEventInput:
    return AuditEventInput(
        resource_type="agent_approval",
        resource_id=uuid.uuid4(),
        action="accept",
        data={"tool_call_id": "tool-call-1"},
    )


def test_build_approval_audit_events_maps_decisions_without_sensitive_values() -> None:
    pending = {
        "approve-call": _PendingApproval(
            uuid.uuid4(), "approve-call", "tools.ticket.get"
        ),
        "override-call": _PendingApproval(
            uuid.uuid4(), "override-call", "tools.ticket.update"
        ),
        "deny-call": _PendingApproval(uuid.uuid4(), "deny-call", "core.http_request"),
    }
    validated = _ValidatedContinuation(
        approval_map={
            "approve-call": True,
            "override-call": ToolApproved(
                override_args={"authorization": "credential-value"}
            ),
            "deny-call": ToolDenied(message="Needs explicit review"),
        },
        decision_metadata={},
    )
    request = ContinueRunRequest(
        source="inbox",
        decisions=[
            ApprovalDecision(tool_call_id="approve-call", action="approve"),
            ApprovalDecision(
                tool_call_id="override-call",
                action="override",
                override_args={"authorization": "credential-value"},
                metadata={"prompt": "sensitive-prompt"},
            ),
            ApprovalDecision(
                tool_call_id="deny-call",
                action="deny",
                reason="Needs explicit review",
            ),
        ],
    )
    session_id = uuid.uuid4()
    run_id = uuid.uuid4()
    dedupe_id = uuid.uuid4()
    decided_at = datetime.now(UTC)

    events = AgentSessionService._build_approval_audit_events(
        pending_approvals=pending,
        validated=validated,
        request=request,
        session_id=session_id,
        run_id=run_id,
        dedupe_id=dedupe_id,
        decided_at=decided_at,
    )

    assert [event.action for event in events] == ["accept", "accept", "reject"]
    assert [event.resource_id for event in events] == [
        pending["approve-call"].approval_id,
        pending["override-call"].approval_id,
        pending["deny-call"].approval_id,
    ]
    assert events[1].data is not None
    assert events[1].data["arguments_overridden"] is True
    assert events[2].data is not None
    assert events[2].data["denial_reason"] == "Needs explicit review"
    assert "credential-value" not in repr(events)
    assert "sensitive-prompt" not in repr(events)
    assert all(event.created_at == decided_at for event in events)


def test_approval_audit_sanitizer_drops_secret_bearing_denial_reason() -> None:
    sanitized = sanitize_audit_metadata(
        {
            "decision": "deny",
            "denial_reason": "Authorization: Bearer secret-token",
            "arguments_overridden": False,
        }
    )

    assert sanitized == {
        "decision": "deny",
        "arguments_overridden": False,
    }


def test_approval_audit_build_is_noop_without_accepted_decisions() -> None:
    events = AgentSessionService._build_approval_audit_events(
        pending_approvals={},
        validated=_ValidatedContinuation(
            approval_map={},
            decision_metadata={},
        ),
        request=ContinueRunRequest(source="inbox", decisions=[]),
        session_id=uuid.uuid4(),
        run_id=uuid.uuid4(),
        dedupe_id=uuid.uuid4(),
        decided_at=datetime.now(UTC),
    )

    assert events == ()


@pytest.mark.parametrize(
    ("role", "source", "expected"),
    [
        (_user_role(), "inbox", True),
        (_user_role(), "slack", False),
        (
            Role(
                type="service",
                workspace_id=uuid.uuid4(),
                user_id=uuid.uuid4(),
                service_id="tracecat-api",
            ),
            "inbox",
            False,
        ),
    ],
)
def test_approval_audit_only_includes_first_party_user_submissions(
    role: Role,
    source: Literal["inbox", "slack"],
    expected: bool,
) -> None:
    assert _should_emit_approval_audit(role=role, source=source) is expected


@pytest.mark.anyio
async def test_approval_audit_deduplicates_concurrent_successful_submissions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class RedisDouble:
        def __init__(self) -> None:
            self.acquired = False
            self.lock = asyncio.Lock()
            self.calls = 0

        async def set_if_not_exists(
            self, key: str, value: str, *, expire_seconds: int
        ) -> bool:
            del key, value, expire_seconds
            async with self.lock:
                self.calls += 1
                if self.acquired:
                    return False
                self.acquired = True
                return True

    redis = RedisDouble()
    delivered: list[tuple[AuditEventInput, ...]] = []

    class AuditServiceDouble:
        @classmethod
        @asynccontextmanager
        async def with_session(cls, *, role: Role):
            del role
            yield cls()

        async def create_events(
            self,
            events: tuple[AuditEventInput, ...],
            *,
            request_audit: RequestAuditContext | None,
        ) -> None:
            del self, request_audit
            delivered.append(events)

    monkeypatch.setattr(
        session_service_module, "get_redis_client", AsyncMock(return_value=redis)
    )
    monkeypatch.setattr(session_service_module, "AuditService", AuditServiceDouble)
    kwargs: dict[str, Any] = {
        "events": (_event(),),
        "role": _user_role(),
        "request_audit": RequestAuditContext(
            client_ip="192.0.2.1", user_agent="TracecatTest/1.0"
        ),
        "dedupe_id": uuid.uuid4(),
    }

    await asyncio.gather(
        _emit_approval_audit_events(**kwargs),
        _emit_approval_audit_events(**kwargs),
    )

    assert redis.calls == 2
    assert len(delivered) == 1


@pytest.mark.anyio
async def test_slow_approval_audit_enrichment_does_not_block_scheduler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gate = asyncio.Event()
    started = asyncio.Event()

    async def slow_emit(**kwargs: Any) -> None:
        del kwargs
        started.set()
        await gate.wait()

    monkeypatch.setattr(
        session_service_module, "_emit_approval_audit_events", slow_emit
    )
    session_service_module._approval_audit_tasks.clear()

    _schedule_approval_audit_events(
        events=(_event(),),
        role=_user_role(),
        request_audit=None,
        dedupe_id=uuid.uuid4(),
    )
    await started.wait()

    assert len(session_service_module._approval_audit_tasks) == 1
    gate.set()
    await asyncio.gather(*session_service_module._approval_audit_tasks)
    await asyncio.sleep(0)
    assert session_service_module._approval_audit_tasks == set()


def test_approval_audit_scheduler_sheds_at_capacity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    emit = AsyncMock()
    warning = Mock()
    monkeypatch.setattr(session_service_module, "MAX_PENDING_APPROVAL_AUDIT_TASKS", 0)
    monkeypatch.setattr(session_service_module, "_emit_approval_audit_events", emit)
    monkeypatch.setattr(session_service_module.logger, "warning", warning)
    session_service_module._approval_audit_tasks.clear()

    _schedule_approval_audit_events(
        events=(_event(),),
        role=_user_role(),
        request_audit=None,
        dedupe_id=uuid.uuid4(),
    )

    emit.assert_not_called()
    warning.assert_called_once_with(
        "Dropped approval audit batch; pending limit reached",
        event_count=1,
        max_pending=0,
    )
