"""Listing a session's user messages the model has not read yet."""

from __future__ import annotations

import uuid
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import get_args
from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tracecat.agent.backends import registry
from tracecat.agent.backends.default import DefaultBackend
from tracecat.agent.backends.types import AgentControlRejected, PendingMessage
from tracecat.agent.session.router import router
from tracecat.agent.session.service import AgentSessionService
from tracecat.agent.session.types import AgentSessionEntity
from tracecat.agent.session.views import build_session_read
from tracecat.auth.dependencies import WorkspaceActorRouteRole
from tracecat.auth.types import Role
from tracecat.db.engine import get_async_session
from tracecat.db.models import AgentSession
from tracecat.exceptions import ScopeDeniedError


class PendingBackend(DefaultBackend):
    """A backend that reports a fixed pending list."""

    listing_pending = True

    def __init__(self, pending: Sequence[PendingMessage] = ()) -> None:
        self.pending = pending
        self.calls: list[uuid.UUID] = []

    async def list_pending(self, session_id: uuid.UUID) -> Sequence[PendingMessage]:
        self.calls.append(session_id)
        return self.pending


class UnlistedPendingBackend(PendingBackend):
    """Implements `list_pending` but does not advertise it."""

    listing_pending = False


@pytest.fixture(autouse=True)
def clean_backend_registry() -> Iterator[None]:
    registry.get_agent_backends.cache_clear()
    yield
    registry.get_agent_backends.cache_clear()


def _role(
    workspace_id: uuid.UUID,
    *,
    user_id: uuid.UUID | None = None,
    scopes: frozenset[str] = frozenset({"agent:read"}),
) -> Role:
    return Role(
        type="user" if user_id else "service",
        service_id="tracecat-api",
        user_id=user_id,
        workspace_id=workspace_id,
        organization_id=uuid.uuid4(),
        scopes=scopes,
    )


def _session(
    backend_id: str = "external", created_by: uuid.UUID | None = None
) -> AgentSession:
    workspace_id = uuid.uuid4()
    return AgentSession(
        id=uuid.uuid4(),
        workspace_id=workspace_id,
        created_by=created_by,
        title="Test",
        entity_type=AgentSessionEntity.WORKSPACE_CHAT.value,
        entity_id=workspace_id,
        backend_id=backend_id,
        harness_type="claude_code",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


def _get_pending(
    agent_session: AgentSession | None,
    backends: dict[str, DefaultBackend],
    session_id: uuid.UUID | None = None,
    role: Role | None = None,
    get_session: AsyncMock | None = None,
):
    workspace_id = agent_session.workspace_id if agent_session else uuid.uuid4()
    actor = role or _role(workspace_id)
    app = FastAPI()
    app.include_router(router)
    role_dependency = get_args(WorkspaceActorRouteRole)[1].dependency
    app.dependency_overrides[role_dependency] = lambda: actor
    app.dependency_overrides[get_async_session] = lambda: AsyncMock()
    target = session_id or (agent_session.id if agent_session else uuid.uuid4())
    with (
        patch.object(
            AgentSessionService,
            "get_session",
            get_session or AsyncMock(return_value=agent_session),
        ),
        patch.object(registry, "get_agent_backends", return_value=backends),
        TestClient(app) as client,
    ):
        return client.get(f"/agent/sessions/{target}/pending")


def test_pending_route_returns_the_backend_list_in_send_order() -> None:
    """The route keeps the backend's send order and exposes text for restore."""
    agent_session = _session()
    sent = datetime.now(UTC)
    pending = [
        PendingMessage(id=uuid.uuid4(), type="steer", text="first", created_at=sent),
        PendingMessage(
            id=uuid.uuid4(),
            type="message",
            text="second",
            created_at=sent + timedelta(seconds=1),
        ),
        PendingMessage(
            id=uuid.uuid4(),
            type="steer",
            text="third",
            created_at=sent + timedelta(seconds=2),
        ),
    ]
    backend = PendingBackend(pending)

    response = _get_pending(agent_session, {"external": backend})

    assert response.status_code == 200
    body = response.json()
    assert body["session_id"] == str(agent_session.id)
    assert [(item["id"], item["type"], item["text"]) for item in body["items"]] == [
        (str(message.id), message.type, message.text) for message in pending
    ]
    assert backend.calls == [agent_session.id]


def test_pending_route_returns_an_empty_list() -> None:
    response = _get_pending(_session(), {"external": PendingBackend()})

    assert response.status_code == 200
    assert response.json()["items"] == []


def test_pending_route_refuses_a_backend_without_the_capability() -> None:
    """The built-in backend does not list pending messages: 409, not an empty list."""
    response = _get_pending(_session(backend_id="oss"), {"oss": DefaultBackend()})

    assert response.status_code == 409
    assert "listing pending messages" in response.json()["detail"]


def test_pending_route_does_not_call_a_backend_that_unsets_the_capability() -> None:
    backend = UnlistedPendingBackend()

    response = _get_pending(_session(), {"external": backend})

    assert response.status_code == 409
    assert backend.calls == []


def test_pending_route_refuses_an_unavailable_backend() -> None:
    response = _get_pending(_session(backend_id="missing"), {})

    assert response.status_code == 409


def test_pending_route_lets_the_owner_read_their_pending_messages() -> None:
    owner = uuid.uuid4()
    agent_session = _session(created_by=owner)
    role = _role(agent_session.workspace_id, user_id=owner)

    response = _get_pending(agent_session, {"external": PendingBackend()}, role=role)

    assert response.status_code == 200


def test_pending_route_hides_pending_messages_from_teammates() -> None:
    """A teammate's unread messages stay theirs until the model reads them."""
    agent_session = _session(created_by=uuid.uuid4())
    backend = PendingBackend()
    role = _role(agent_session.workspace_id, user_id=uuid.uuid4())

    response = _get_pending(agent_session, {"external": backend}, role=role)

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "session_read_only"
    assert backend.calls == []


def test_pending_route_requires_the_agent_read_scope() -> None:
    agent_session = _session()
    backend = PendingBackend()
    get_session = AsyncMock(return_value=agent_session)
    role = _role(agent_session.workspace_id, scopes=frozenset())

    # The API app maps this error to 403; the bare test app lets it propagate.
    with pytest.raises(ScopeDeniedError):
        _get_pending(
            agent_session, {"external": backend}, role=role, get_session=get_session
        )

    get_session.assert_not_awaited()
    assert backend.calls == []


def test_pending_route_returns_404_for_an_unknown_session() -> None:
    response = _get_pending(None, {"external": PendingBackend()})

    assert response.status_code == 404


@pytest.mark.parametrize("listing_pending", [True, False])
def test_session_read_reports_the_listing_pending_capability(
    listing_pending: bool,
) -> None:
    backend = PendingBackend() if listing_pending else UnlistedPendingBackend()
    agent_session = _session()
    with patch.object(
        registry, "get_agent_backends", return_value={"external": backend}
    ):
        read = build_session_read(agent_session, _role(agent_session.workspace_id))

    assert read.capabilities.listing_pending is listing_pending


def test_session_read_reports_no_listing_pending_for_a_missing_backend() -> None:
    agent_session = _session()
    with patch.object(registry, "get_agent_backends", return_value={}):
        read = build_session_read(agent_session, _role(agent_session.workspace_id))

    assert read.capabilities.listing_pending is False


@pytest.mark.anyio
async def test_the_builtin_backend_does_not_list_pending_messages() -> None:
    assert DefaultBackend.listing_pending is False
    with pytest.raises(AgentControlRejected):
        await DefaultBackend().list_pending(uuid.uuid4())


def _entry(name: str, factory: object) -> SimpleNamespace:
    return SimpleNamespace(name=name, load=Mock(return_value=factory))


def test_discovery_rejects_listing_pending_without_a_list_pending_method() -> None:
    class Unlisted(DefaultBackend):
        listing_pending = True

    with (
        patch.object(
            registry, "entry_points", return_value=[_entry("external", Unlisted)]
        ),
        pytest.raises(TypeError, match="external: listing_pending requires"),
    ):
        registry.get_agent_backends()


@pytest.mark.parametrize(
    ("field", "value"), [("listing_pending", "yes"), ("listing_pending", 1)]
)
def test_discovery_rejects_a_non_bool_listing_pending(
    field: str, value: object
) -> None:
    class Malformed(PendingBackend):
        pass

    setattr(Malformed, field, value)
    with (
        patch.object(
            registry, "entry_points", return_value=[_entry("external", Malformed)]
        ),
        pytest.raises(TypeError, match="external: listing_pending must be a bool"),
    ):
        registry.get_agent_backends()


def test_discovery_accepts_a_backend_that_lists_pending_messages() -> None:
    with patch.object(
        registry, "entry_points", return_value=[_entry("external", PendingBackend)]
    ):
        assert registry.get_agent_backends()["external"].listing_pending is True
