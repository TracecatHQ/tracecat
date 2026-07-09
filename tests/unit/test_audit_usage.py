from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, call

import pytest

import tracecat.audit.usage as audit_usage
from tracecat.audit.service import AuditService
from tracecat.audit.usage import AUDIT_CREDENTIAL_USAGE_IDLE_SECONDS
from tracecat.auth.types import Role
from tracecat.contexts import ctx_client_ip

pytestmark = pytest.mark.anyio


def _role() -> Role:
    return Role(
        type="user",
        user_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        service_id="tracecat-api",
    )


@pytest.mark.parametrize(
    ("results", "source_ips"),
    [
        pytest.param([None], ["203.0.113.10"], id="new-session"),
        pytest.param(["1"], ["203.0.113.10"], id="duplicate-session"),
        pytest.param([RuntimeError("redis down")], [None], id="redis-failure"),
        pytest.param(
            [None, None],
            ["203.0.113.10", "203.0.113.11"],
            id="new-source-ip",
        ),
    ],
)
async def test_credential_usage_session_emission_rules(
    monkeypatch: pytest.MonkeyPatch,
    results: list[str | None | Exception],
    source_ips: list[str | None],
) -> None:
    expected_emissions = sum(result is None for result in results)
    set_marker = AsyncMock(side_effect=results)
    monkeypatch.setattr(
        audit_usage,
        "get_redis_client",
        AsyncMock(return_value=SimpleNamespace(set_audit_get=set_marker)),
    )
    schedule = MagicMock()
    monkeypatch.setattr(audit_usage, "_schedule_credential_usage_event", schedule)
    role = _role()

    for source_ip in source_ips:
        token = ctx_client_ip.set(source_ip)
        try:
            await audit_usage.emit_credential_usage_audit(
                role=role,
                credential_type="service_account_api_key",
                credential_key_id="key_123",
                resource_id="key_123",
            )
        finally:
            ctx_client_ip.reset(token)

    assert set_marker.await_args_list == [
        call(
            f"audit:usage:service_account_api_key:key_123:{source_ip or '-'}",
            "1",
            expire_seconds=AUDIT_CREDENTIAL_USAGE_IDLE_SECONDS,
        )
        for source_ip in source_ips
    ]
    assert schedule.call_count == expected_emissions
    assert all(
        scheduled
        == call(
            role=role,
            credential_type="service_account_api_key",
            resource_id="key_123",
        )
        for scheduled in schedule.call_args_list
    )


async def test_credential_usage_payload_attribution_and_failure_consumption(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recipients: list[Role | None] = []
    create_event = AsyncMock(side_effect=[None, RuntimeError("delivery down")])

    @asynccontextmanager
    async def fake_with_session(
        role: Role | None = None, **_kwargs: object
    ) -> AsyncIterator[SimpleNamespace]:
        recipients.append(role)
        yield SimpleNamespace(create_event=create_event)

    monkeypatch.setattr(AuditService, "with_session", staticmethod(fake_with_session))
    role = _role()
    resource_id = uuid.uuid4()

    for _ in range(2):
        await audit_usage._emit_credential_usage_event(
            role=role,
            credential_type="mcp_personal_access_token",
            resource_id=resource_id,
        )

    assert recipients == [role, role]
    assert (
        create_event.await_args_list
        == [
            call(
                resource_type="mcp_personal_access_token",
                action="use",
                resource_id=resource_id,
                data={"credential_kind": "mcp_personal_access_token"},
                include_actor_label=False,
            )
        ]
        * 2
    )
