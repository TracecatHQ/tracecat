"""Shared state for the MCP SAML bridge."""

from __future__ import annotations

import secrets
import time
import uuid
from dataclasses import dataclass
from urllib.parse import urlencode, urlparse, urlunparse

from key_value.aio._utils.compound import compound_key, prefix_collection
from key_value.aio.adapters.pydantic import PydanticAdapter
from mcp.server.auth.provider import AuthorizationCode
from pydantic import AnyUrl, BaseModel, EmailStr
from redis.asyncio import Redis as AsyncRedis

from tracecat.identifiers import OrganizationID, UserID
from tracecat.mcp.storage import create_mcp_client_storage, create_mcp_redis_client

MCP_SAML_IDENTIFY_PATH = "/_/mcp/auth/identify"
MCP_SAML_START_PATH = "/_/mcp/auth/saml/start"

_CLIENTS_COLLECTION = "mcp-saml-clients"
_TRANSACTIONS_COLLECTION = "mcp-saml-transactions"
_CODES_COLLECTION = "mcp-saml-authorization-codes"
_SESSIONS_COLLECTION = "mcp-saml-sessions"
_TRANSACTION_CONSUMPTION_COLLECTION = "mcp-saml-transaction-consumption"
_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


class SAMLMCPAuthTransaction(BaseModel):
    id: str
    client_id: str
    client_redirect_uri: AnyUrl
    client_state: str | None = None
    code_challenge: str
    redirect_uri_provided_explicitly: bool
    scopes: list[str]
    resource: str | None = None
    created_at: float
    expires_at: float
    email: EmailStr | None = None
    organization_id: OrganizationID | None = None
    organization_slug: str | None = None
    user_id: UserID | None = None
    authenticated_at: float | None = None


class SAMLMCPSession(BaseModel):
    id: str
    client_id: str
    user_id: UserID
    organization_id: OrganizationID
    user_email: EmailStr
    jti: str
    created_at: float
    expires_at: float


class SAMLAuthorizationCode(AuthorizationCode):
    session_id: str


@dataclass(frozen=True)
class SAMLBridgeStores:
    clients: PydanticAdapter
    transactions: PydanticAdapter[SAMLMCPAuthTransaction]
    codes: PydanticAdapter[SAMLAuthorizationCode]
    sessions: PydanticAdapter[SAMLMCPSession]
    redis: AsyncRedis


def create_saml_bridge_stores() -> SAMLBridgeStores:
    storage = create_mcp_client_storage()
    return SAMLBridgeStores(
        clients=PydanticAdapter(
            key_value=storage,
            pydantic_model=dict,
            default_collection=_CLIENTS_COLLECTION,
            raise_on_validation_error=True,
        ),
        transactions=PydanticAdapter(
            key_value=storage,
            pydantic_model=SAMLMCPAuthTransaction,
            default_collection=_TRANSACTIONS_COLLECTION,
            raise_on_validation_error=True,
        ),
        codes=PydanticAdapter(
            key_value=storage,
            pydantic_model=SAMLAuthorizationCode,
            default_collection=_CODES_COLLECTION,
            raise_on_validation_error=True,
        ),
        sessions=PydanticAdapter(
            key_value=storage,
            pydantic_model=SAMLMCPSession,
            default_collection=_SESSIONS_COLLECTION,
            raise_on_validation_error=True,
        ),
        redis=create_mcp_redis_client(),
    )


def _normalize_loopback_browser_callback(uri: str) -> str:
    parsed = urlparse(uri)
    if parsed.scheme != "http" or parsed.hostname not in _LOOPBACK_HOSTS:
        return uri
    if parsed.hostname != "localhost":
        return uri

    userinfo = ""
    if parsed.username is not None:
        userinfo = parsed.username
        if parsed.password is not None:
            userinfo = f"{userinfo}:{parsed.password}"
        userinfo = f"{userinfo}@"

    port = f":{parsed.port}" if parsed.port is not None else ""
    return urlunparse(parsed._replace(netloc=f"{userinfo}127.0.0.1{port}"))


def _build_transaction_consume_key(transaction_id: str) -> str:
    prefixed_collection = prefix_collection("mcp", _TRANSACTION_CONSUMPTION_COLLECTION)
    return compound_key(prefixed_collection, transaction_id)


async def mark_saml_transaction_authenticated(
    *,
    stores: SAMLBridgeStores,
    transaction_id: str,
    user_id: UserID,
    organization_id: OrganizationID,
    email: str,
) -> SAMLMCPAuthTransaction | None:
    transaction = await stores.transactions.get(transaction_id)
    if transaction is None:
        return None
    remaining_ttl = max(1, int(transaction.expires_at - time.time()))
    authenticated = transaction.model_copy(
        update={
            "user_id": user_id,
            "organization_id": organization_id,
            "email": email,
            "authenticated_at": time.time(),
        }
    )
    await stores.transactions.put(
        transaction_id,
        authenticated,
        ttl=remaining_ttl,
    )
    return authenticated


async def list_saml_bridge_sessions(stores: SAMLBridgeStores) -> list[SAMLMCPSession]:
    prefixed_collection = prefix_collection("mcp", _SESSIONS_COLLECTION)
    pattern = compound_key(prefixed_collection, "*")
    sessions: list[SAMLMCPSession] = []
    async for compound_session_key in stores.redis.scan_iter(match=pattern):
        _, _, session_id = compound_session_key.partition("::")
        if not session_id:
            continue
        if session := await stores.sessions.get(session_id):
            sessions.append(session)
    return sessions


async def delete_saml_bridge_session(
    stores: SAMLBridgeStores,
    session_id: str,
) -> bool:
    return await stores.sessions.delete(session_id)


async def revoke_prior_saml_mcp_sessions(
    stores: SAMLBridgeStores,
    *,
    user_id: UserID,
    organization_id: OrganizationID,
    client_id: str,
) -> None:
    for session in await list_saml_bridge_sessions(stores):
        if (
            session.user_id == user_id
            and session.organization_id == organization_id
            and session.client_id == client_id
        ):
            await delete_saml_bridge_session(stores, session.id)


async def complete_saml_mcp_transaction(
    *,
    stores: SAMLBridgeStores,
    transaction_id: str,
    access_token_ttl_seconds: int,
    auth_code_ttl_seconds: int,
) -> str | None:
    transaction = await stores.transactions.get(transaction_id)
    if (
        transaction is None
        or transaction.user_id is None
        or transaction.email is None
        or transaction.authenticated_at is None
    ):
        return None
    if transaction.organization_id is None:
        return None
    if not await stores.redis.set(
        _build_transaction_consume_key(transaction_id),
        "1",
        ex=max(1, auth_code_ttl_seconds),
        nx=True,
    ):
        return None

    await revoke_prior_saml_mcp_sessions(
        stores,
        user_id=transaction.user_id,
        organization_id=transaction.organization_id,
        client_id=transaction.client_id,
    )

    session_id = str(uuid.uuid4())
    code = secrets.token_urlsafe(32)
    jti = secrets.token_urlsafe(32)
    expires_at = time.time() + access_token_ttl_seconds
    session = SAMLMCPSession(
        id=session_id,
        client_id=transaction.client_id,
        user_id=transaction.user_id,
        organization_id=transaction.organization_id,
        user_email=transaction.email,
        jti=jti,
        created_at=time.time(),
        expires_at=expires_at,
    )
    auth_code = SAMLAuthorizationCode(
        code=code,
        scopes=transaction.scopes,
        expires_at=time.time() + auth_code_ttl_seconds,
        client_id=transaction.client_id,
        code_challenge=transaction.code_challenge,
        redirect_uri=transaction.client_redirect_uri,
        redirect_uri_provided_explicitly=transaction.redirect_uri_provided_explicitly,
        resource=transaction.resource,
        session_id=session_id,
    )
    await stores.sessions.put(
        session_id,
        session,
        ttl=access_token_ttl_seconds,
    )
    await stores.codes.put(
        code,
        auth_code,
        ttl=auth_code_ttl_seconds,
    )
    await stores.transactions.delete(transaction_id)

    params = {"code": code}
    if transaction.client_state:
        params["state"] = transaction.client_state
    client_callback_uri = _normalize_loopback_browser_callback(
        str(transaction.client_redirect_uri)
    )
    separator = "&" if "?" in client_callback_uri else "?"
    return f"{client_callback_uri}{separator}{urlencode(params)}"
