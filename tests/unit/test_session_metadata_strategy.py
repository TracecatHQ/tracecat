import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi_users_db_sqlalchemy.access_token import SQLAlchemyAccessTokenDatabase

from tracecat import config
from tracecat.auth.users import SessionMetadataDatabaseStrategy
from tracecat.contexts import RequestAuditContext, ctx_request_audit


def _strategy() -> tuple[SessionMetadataDatabaseStrategy, MagicMock]:
    database = MagicMock(spec=SQLAlchemyAccessTokenDatabase)
    database.get_by_token = AsyncMock()
    database.update = AsyncMock()
    database.session = MagicMock()
    database.session.execute = AsyncMock()
    database.session.commit = AsyncMock()
    return SessionMetadataDatabaseStrategy(database, lifetime_seconds=3600), database


def test_create_token_dict_records_client_metadata() -> None:
    strategy, _ = _strategy()
    user = MagicMock(id=uuid.uuid4())
    token = ctx_request_audit.set(
        RequestAuditContext(
            client_ip="203.0.113.7",
            user_agent="chrome/1.0",
            raw_user_agent="Mozilla/5.0 Chrome/1.0",
        )
    )
    try:
        token_dict = strategy._create_access_token_dict(user)
    finally:
        ctx_request_audit.reset(token)

    assert token_dict["user_id"] == user.id
    assert token_dict["ip_address"] == "203.0.113.7"
    assert token_dict["user_agent"] == "Mozilla/5.0 Chrome/1.0"
    assert token_dict["last_seen_at"] is not None


def test_create_token_dict_without_request_context() -> None:
    strategy, _ = _strategy()
    token_dict = strategy._create_access_token_dict(MagicMock(id=uuid.uuid4()))
    assert "ip_address" not in token_dict
    assert "user_agent" not in token_dict


@pytest.mark.anyio
async def test_read_token_touches_last_seen_only_when_stale(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "SESSION_LAST_SEEN_UPDATE_INTERVAL_SECONDS", 300)
    strategy, database = _strategy()
    user = MagicMock()
    user_manager = MagicMock()
    user_manager.parse_id = MagicMock(side_effect=lambda value: value)
    user_manager.get = AsyncMock(return_value=user)

    fresh = MagicMock(
        user_id=uuid.uuid4(), id=uuid.uuid4(), last_seen_at=datetime.now(UTC)
    )
    database.get_by_token.return_value = fresh
    assert await strategy.read_token("tok", user_manager) is user
    database.session.execute.assert_not_called()

    stale = MagicMock(
        user_id=uuid.uuid4(),
        id=uuid.uuid4(),
        last_seen_at=datetime.now(UTC) - timedelta(seconds=600),
    )
    database.get_by_token.return_value = stale
    assert await strategy.read_token("tok", user_manager) is user
    database.session.execute.assert_awaited_once()
    database.session.commit.assert_awaited_once()
    # The write is a conditional UPDATE keyed on the token that only advances a
    # null or stale last_seen_at, so concurrent readers cannot regress it.
    (statement,) = database.session.execute.await_args.args
    sql = str(statement.compile(compile_kwargs={"literal_binds": False}))
    assert sql.startswith("UPDATE access_token SET last_seen_at=")
    assert "access_token.id = " in sql
    assert "access_token.last_seen_at IS NULL" in sql
    assert "access_token.last_seen_at < " in sql


@pytest.mark.anyio
async def test_read_token_returns_none_for_unknown_token() -> None:
    strategy, database = _strategy()
    database.get_by_token.return_value = None
    assert await strategy.read_token("tok", MagicMock()) is None
    database.session.execute.assert_not_called()
