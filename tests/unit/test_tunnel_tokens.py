import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from tracecat_ee.tunnels.tokens import (
    TUNNEL_TOKEN_AUDIENCE,
    TUNNEL_TOKEN_ISSUER,
    TunnelTokenError,
    generate_gateway_key,
    mint_tunnel_token,
    verify_tunnel_token,
)

KEY = generate_gateway_key()
TUNNEL_ID = uuid.uuid4()
WORKSPACE_ID = uuid.uuid4()


def _mint(**overrides: object) -> str:
    params: dict[str, object] = {
        "gateway_key": KEY,
        "tunnel_id": TUNNEL_ID,
        "workspace_id": WORKSPACE_ID,
        "subject": "wf-exec-1",
        "action_ref": "search_events",
    }
    params.update(overrides)
    return mint_tunnel_token(**params)  # pyright: ignore[reportArgumentType]


def _verify(token: str, **overrides: object):
    params: dict[str, object] = {
        "gateway_key": KEY,
        "tunnel_id": TUNNEL_ID,
        "workspace_id": WORKSPACE_ID,
    }
    params.update(overrides)
    return verify_tunnel_token(token, **params)  # pyright: ignore[reportArgumentType]


def test_generated_keys_are_unique_and_long() -> None:
    assert generate_gateway_key() != generate_gateway_key()
    assert len(KEY) >= 43


def test_round_trip() -> None:
    claims = _verify(_mint())
    assert claims.tunnel_id == TUNNEL_ID
    assert claims.workspace_id == WORKSPACE_ID
    assert claims.subject == "wf-exec-1"
    assert claims.action_ref == "search_events"


@pytest.mark.parametrize(
    "overrides",
    [
        {"gateway_key": generate_gateway_key()},
        {"tunnel_id": uuid.uuid4()},
        {"workspace_id": uuid.uuid4()},
    ],
)
def test_rejects_other_key_tunnel_or_workspace(overrides: dict[str, object]) -> None:
    with pytest.raises(TunnelTokenError):
        _verify(_mint(), **overrides)


def test_rejects_token_minted_for_another_tunnel_with_same_key() -> None:
    with pytest.raises(TunnelTokenError):
        _verify(_mint(tunnel_id=uuid.uuid4()))


def _raw(payload_overrides: dict[str, object], algorithm: str = "HS256") -> str:
    now = datetime.now(UTC)
    payload: dict[str, object] = {
        "iss": TUNNEL_TOKEN_ISSUER,
        "aud": TUNNEL_TOKEN_AUDIENCE,
        "sub": str(TUNNEL_ID),
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=60)).timestamp()),
        "workspace_id": str(WORKSPACE_ID),
        "subject": "wf-exec-1",
    }
    payload.update(payload_overrides)
    payload = {k: v for k, v in payload.items() if v is not None}
    return jwt.encode(payload, KEY if algorithm != "none" else "", algorithm=algorithm)


@pytest.mark.parametrize(
    "payload",
    [
        {"exp": int((datetime.now(UTC) - timedelta(seconds=5)).timestamp())},
        {"aud": "tracecat-api"},
        {"iss": "someone-else"},
        {"subject": None},
        {"workspace_id": None},
        {"sub": "not-a-uuid"},
        {"exp": int((datetime.now(UTC) + timedelta(days=1)).timestamp())},
    ],
)
def test_rejects_invalid_claims(payload: dict[str, object]) -> None:
    with pytest.raises(TunnelTokenError):
        _verify(_raw(payload))


def test_rejects_unsigned_token() -> None:
    with pytest.raises(TunnelTokenError):
        _verify(_raw({}, algorithm="none"))


@pytest.mark.parametrize("ttl", [0, -1, 901])
def test_mint_rejects_bad_ttl(ttl: int) -> None:
    with pytest.raises(ValueError):
        _mint(ttl_seconds=ttl)


def test_gateway_policy_token_round_trip_and_rejections() -> None:
    from tracecat_ee.tunnels.tokens import (
        mint_gateway_policy_token,
        unverified_policy_token_tunnel_id,
        verify_gateway_policy_token,
    )

    key = generate_gateway_key()
    tunnel_id = uuid.uuid4()
    token = mint_gateway_policy_token(gateway_key=key, tunnel_id=tunnel_id)
    assert unverified_policy_token_tunnel_id(token) == tunnel_id
    verify_gateway_policy_token(token, gateway_key=key, tunnel_id=tunnel_id)
    with pytest.raises(TunnelTokenError):
        verify_gateway_policy_token(
            token, gateway_key=generate_gateway_key(), tunnel_id=tunnel_id
        )
    with pytest.raises(TunnelTokenError):
        verify_gateway_policy_token(token, gateway_key=key, tunnel_id=uuid.uuid4())
    with pytest.raises(TunnelTokenError):
        unverified_policy_token_tunnel_id("not-a-jwt")


def test_proxy_token_is_not_a_policy_token() -> None:
    from tracecat_ee.tunnels.tokens import verify_gateway_policy_token

    key = generate_gateway_key()
    tunnel_id = uuid.uuid4()
    proxy_token = mint_tunnel_token(
        gateway_key=key,
        tunnel_id=tunnel_id,
        workspace_id=uuid.uuid4(),
        subject="wf-exec-1",
    )
    with pytest.raises(TunnelTokenError):
        verify_gateway_policy_token(proxy_token, gateway_key=key, tunnel_id=tunnel_id)
