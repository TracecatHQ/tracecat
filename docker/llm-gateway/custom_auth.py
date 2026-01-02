"""Standalone custom authentication for LiteLLM proxy.

This module implements multi-tenant credential resolution for LiteLLM
with minimal dependencies (no tracecat imports). The gateway receives
requests from jailed agent runtimes that authenticate via JWT tokens.

Dependencies: pyjwt, sqlalchemy[asyncio], asyncpg, cryptography, orjson
"""

import logging
import os
import uuid
from typing import Any

import jwt
import orjson
from cryptography.fernet import Fernet
from fastapi import Request
from litellm.caching.dual_cache import DualCache
from litellm.integrations.custom_logger import CustomLogger
from litellm.proxy.proxy_server import ProxyException, UserAPIKeyAuth
from litellm.types.utils import CallTypesLiteral
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# -----------------------------------------------------------------------------
# Configuration (from environment)
# -----------------------------------------------------------------------------

DB_URI = os.environ.get("TRACECAT__DB_URI", "")
DB_ENCRYPTION_KEY = os.environ.get("TRACECAT__DB_ENCRYPTION_KEY", "")
SERVICE_KEY = os.environ.get("TRACECAT__SERVICE_KEY", "")

# SQLAlchemy async engine (lazy initialized)
_engine = None


def get_engine():
    """Get or create the async SQLAlchemy engine."""
    global _engine
    if _engine is None:
        if not DB_URI:
            raise ValueError("TRACECAT__DB_URI is not set")
        _engine = create_async_engine(DB_URI, pool_pre_ping=True)
    return _engine


# -----------------------------------------------------------------------------
# Minimal SQLAlchemy Models (just what we need for secrets)
# -----------------------------------------------------------------------------


class Base(DeclarativeBase):
    pass


class OrganizationSecret(Base):
    __tablename__ = "organization_secret"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    organization_id: Mapped[uuid.UUID]
    name: Mapped[str]
    environment: Mapped[str]
    encrypted_keys: Mapped[bytes]


class Secret(Base):
    __tablename__ = "secret"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    owner_id: Mapped[uuid.UUID]
    name: Mapped[str]
    environment: Mapped[str]
    encrypted_keys: Mapped[bytes]


class Workspace(Base):
    __tablename__ = "workspace"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    organization_id: Mapped[uuid.UUID]


# LLM Token constants
LLM_TOKEN_ISSUER = "tracecat-agent-executor"
LLM_TOKEN_AUDIENCE = "tracecat-llm-gateway"
LLM_TOKEN_SUBJECT = "tracecat-agent-runtime"

# Allowed providers
ALLOWED_PROVIDERS = frozenset(
    {"openai", "anthropic", "bedrock", "custom-model-provider"}
)

# Model to provider mapping (must match tracecat/agent/config.py)
MODEL_PROVIDER_MAP = {
    "gpt-4o-mini": "openai",
    "gpt-5-mini": "openai",
    "gpt-5-nano": "openai",
    "gpt-5": "openai",
    "claude-sonnet-4-5-20250929": "anthropic",
    "claude-haiku-4-5-20251001": "anthropic",
    "claude-opus-4-5-20251101": "anthropic",
    "bedrock": "bedrock",
    "custom": "custom-model-provider",
}
logger = logging.getLogger("llm_gateway")


# -----------------------------------------------------------------------------
# LLM Token Claims
# -----------------------------------------------------------------------------


class LLMTokenClaims(BaseModel):
    """Claims extracted from a verified LLM token."""

    workspace_id: str = Field(..., description="Workspace UUID as string")
    session_id: str = Field(..., description="Agent session UUID as string")
    model: str = Field(..., description="The model to use for this run")
    model_settings: dict[str, Any] = Field(default_factory=dict)
    output_type: str | dict | None = Field(default=None)
    use_workspace_credentials: bool = Field(default=False)


def verify_llm_token(token: str) -> LLMTokenClaims:
    """Verify LLM JWT and return extracted claims."""
    if not SERVICE_KEY:
        raise ValueError("TRACECAT__SERVICE_KEY is not set")

    try:
        payload = jwt.decode(
            token,
            SERVICE_KEY,
            algorithms=["HS256"],
            audience=LLM_TOKEN_AUDIENCE,
            issuer=LLM_TOKEN_ISSUER,
        )
    except jwt.PyJWTError as exc:
        raise ValueError(f"Invalid LLM token: {exc}") from exc

    if payload.get("sub") != LLM_TOKEN_SUBJECT:
        raise ValueError("Invalid LLM token subject")

    return LLMTokenClaims.model_validate(payload)


# -----------------------------------------------------------------------------
# Credential Fetching (SQLAlchemy) with Caching
# -----------------------------------------------------------------------------

# Cache TTL for encrypted credentials (2 minutes)
# Short TTL for multi-tenant isolation - credentials cycle out quickly
CREDENTIAL_CACHE_TTL = 120


async def get_provider_credentials(
    workspace_id: str,
    provider: str,
    use_workspace_creds: bool = False,
    cache: DualCache | None = None,
) -> dict[str, str] | None:
    """Fetch and decrypt provider credentials from DB with caching.

    Caches encrypted bytes in DualCache to avoid DB roundtrips.
    Decryption happens on every call for security.
    Cache TTL is 2 minutes.
    """
    if not DB_ENCRYPTION_KEY:
        raise ValueError("DB_ENCRYPTION_KEY not configured")

    # Build cache key
    scope = "workspace" if use_workspace_creds else "org"
    cache_key = f"tracecat_creds:{scope}:{workspace_id}:{provider}"

    # Check cache for encrypted bytes
    encrypted_keys: bytes | None = None
    if cache is not None:
        cached = await cache.async_get_cache(cache_key)
        if cached is not None:
            logger.debug(f"Cache hit for credentials: {cache_key}")
            encrypted_keys = cached

    # Fetch from DB if not cached
    if encrypted_keys is None:
        if use_workspace_creds:
            secret_name = f"workspace-agent-{provider}-credentials"
        else:
            secret_name = f"agent-{provider}-credentials"

        engine = get_engine()
        async with AsyncSession(engine) as session:
            if use_workspace_creds:
                # Workspace-level credentials
                stmt = select(Secret).where(
                    Secret.name == secret_name,
                    Secret.owner_id == uuid.UUID(workspace_id),
                    Secret.environment == "default",
                )
            else:
                # Organization-level credentials - lookup org_id from workspace
                ws_stmt = select(Workspace.organization_id).where(
                    Workspace.id == uuid.UUID(workspace_id)
                )
                ws_result = await session.execute(ws_stmt)
                org_id = ws_result.scalar_one_or_none()

                if org_id is None:
                    logger.warning(f"Workspace not found: {workspace_id}")
                    return None

                stmt = select(OrganizationSecret).where(
                    OrganizationSecret.name == secret_name,
                    OrganizationSecret.organization_id == org_id,
                    OrganizationSecret.environment == "default",
                )

            result = await session.execute(stmt)
            secret = result.scalar_one_or_none()

            if not secret:
                return None

            encrypted_keys = secret.encrypted_keys

        # Cache the encrypted bytes
        if cache is not None:
            await cache.async_set_cache(
                cache_key, encrypted_keys, ttl=CREDENTIAL_CACHE_TTL
            )
            logger.debug(f"Cached encrypted credentials: {cache_key}")

    # Always decrypt on every call
    # encrypted_keys is guaranteed non-None: cache hit assigns bytes,
    # or DB path either returns None early or assigns secret.encrypted_keys
    assert encrypted_keys is not None
    fernet = Fernet(DB_ENCRYPTION_KEY.encode())
    decrypted = fernet.decrypt(encrypted_keys)
    return orjson.loads(decrypted)


# -----------------------------------------------------------------------------
# LiteLLM Auth Hooks
# -----------------------------------------------------------------------------


async def user_api_key_auth(request: Request, api_key: str) -> UserAPIKeyAuth:
    """Validate LLM token from jailed agent runtime.

    The api_key parameter is the Bearer token sent by the SDK, which is
    actually our JWT token minted by the Loop.
    """
    try:
        claims = verify_llm_token(api_key)
    except ValueError as e:
        logger.warning(f"LLM token validation failed: {e}")
        raise ProxyException(
            message=f"Invalid LLM token: {e}",
            type="auth_error",
            param=None,
            code=401,
        ) from e

    logger.info(
        f"Authenticated via LLM token: workspace={claims.workspace_id}, "
        f"session={claims.session_id}, model={claims.model}"
    )

    return UserAPIKeyAuth(
        api_key="llm-token",
        team_id=claims.workspace_id,
        user_id=f"agent-session:{claims.session_id}",
        metadata={
            "workspace_id": claims.workspace_id,
            "session_id": claims.session_id,
            "use_workspace_credentials": claims.use_workspace_credentials,
            "model": claims.model,
            "model_settings": claims.model_settings,
            "output_type": claims.output_type,
        },
    )


class TracecatCallbackHandler(CustomLogger):
    """Custom callback handler for LiteLLM proxy.

    Implements async_pre_call_hook to inject credentials and model settings
    before each LLM call.
    """

    async def async_pre_call_hook(
        self,
        user_api_key_dict: UserAPIKeyAuth,
        cache: DualCache,
        data: dict,
        call_type: CallTypesLiteral,
    ):
        """Inject credentials and model settings before LLM call."""
        workspace_id: str = user_api_key_dict.metadata.get("workspace_id", "")
        use_workspace_creds: bool = user_api_key_dict.metadata.get(
            "use_workspace_credentials", False
        )

        # Use model from token metadata (trusted)
        model = user_api_key_dict.metadata.get("model") or data.get("model", "")

        # Get provider from model
        provider = _get_provider_for_model(model)

        # Fetch credentials (uses cache to avoid DB roundtrip)
        creds = await get_provider_credentials(
            workspace_id, provider, use_workspace_creds, cache=cache
        )

        if not creds:
            raise ProxyException(
                message=f"No credentials configured for provider '{provider}'",
                type="auth_error",
                param=None,
                code=401,
            )

        # Inject credentials based on provider type
        _inject_provider_credentials(data, provider, creds)

        # Inject model settings from token
        model_settings = user_api_key_dict.metadata.get("model_settings", {})
        if model_settings:
            data.update(model_settings)

        # Handle structured outputs via output_type -> response_format
        output_type = user_api_key_dict.metadata.get("output_type")
        if output_type is not None:
            response_format = _build_response_format(output_type)
            if response_format:
                data["response_format"] = response_format

        logger.info(
            f"Injected credentials: workspace={workspace_id}, provider={provider}, model={model}"
        )

        return data


# Callback handler instance for LiteLLM config
callback_handler = TracecatCallbackHandler()


# -----------------------------------------------------------------------------
# Helper Functions
# -----------------------------------------------------------------------------


def _get_provider_for_model(model: str) -> str:
    """Get provider for a model name."""
    # Handle custom model provider
    if model == "custom" or model.startswith("custom-model-provider/"):
        return "custom-model-provider"

    if model not in MODEL_PROVIDER_MAP:
        raise ProxyException(
            message=f"Model '{model}' not found in allowed models",
            type="invalid_request_error",
            param=None,
            code=400,
        )

    provider = MODEL_PROVIDER_MAP[model]

    if provider not in ALLOWED_PROVIDERS:
        raise ProxyException(
            message=f"Provider '{provider}' not allowed",
            type="invalid_request_error",
            param=None,
            code=400,
        )

    return provider


def _inject_provider_credentials(
    data: dict,
    provider: str,
    creds: dict[str, str],
) -> None:
    """Inject provider-specific credentials into request."""
    if provider == "openai":
        data["api_key"] = creds.get("OPENAI_API_KEY")

    elif provider == "anthropic":
        data["api_key"] = creds.get("ANTHROPIC_API_KEY")

    elif provider == "bedrock":
        if bearer_token := creds.get("AWS_BEARER_TOKEN_BEDROCK"):
            data["api_key"] = bearer_token
        else:
            data["aws_access_key_id"] = creds.get("AWS_ACCESS_KEY_ID")
            data["aws_secret_access_key"] = creds.get("AWS_SECRET_ACCESS_KEY")
        data["aws_region_name"] = creds.get("AWS_REGION")
        if arn := creds.get("AWS_MODEL_ARN"):
            data["model"] = f"bedrock/{arn}"

    elif provider == "custom-model-provider":
        if api_key := creds.get("CUSTOM_MODEL_PROVIDER_API_KEY"):
            data["api_key"] = api_key
        if base_url := creds.get("CUSTOM_MODEL_PROVIDER_BASE_URL"):
            data["api_base"] = base_url
        if model_name := creds.get("CUSTOM_MODEL_PROVIDER_MODEL_NAME"):
            data["model"] = model_name

    else:
        raise ProxyException(
            message=f"Unsupported provider: {provider}",
            type="invalid_request_error",
            param=None,
            code=400,
        )


def _build_response_format(output_type: str | dict) -> dict | None:
    """Build LiteLLM response_format from output_type."""
    if isinstance(output_type, dict):
        return {
            "type": "json_schema",
            "json_schema": {
                "name": "structured_output",
                "strict": True,
                "schema": output_type,
            },
        }

    primitive_schemas = {
        "str": {"type": "string"},
        "int": {"type": "integer"},
        "float": {"type": "number"},
        "bool": {"type": "boolean"},
        "list[str]": {"type": "array", "items": {"type": "string"}},
        "list[int]": {"type": "array", "items": {"type": "integer"}},
        "list[float]": {"type": "array", "items": {"type": "number"}},
        "list[bool]": {"type": "array", "items": {"type": "boolean"}},
    }

    if output_type in primitive_schemas:
        return {
            "type": "json_schema",
            "json_schema": {
                "name": "structured_output",
                "strict": True,
                "schema": {
                    "type": "object",
                    "properties": {"result": primitive_schemas[output_type]},
                    "required": ["result"],
                    "additionalProperties": False,
                },
            },
        }

    logger.warning(f"Unknown output_type: {output_type}, skipping response_format")
    return None
