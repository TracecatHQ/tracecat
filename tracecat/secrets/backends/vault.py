"""HashiCorp Vault secrets backend (read-only, KV v2).

Secret values live in Vault and are resolved at execution time; the Tracecat
database only holds value-less registrations. Writes through Tracecat are
rejected (``can_write`` is ``False``).

Path convention (KV v2, configurable mount and prefix)::

    {mount}/data/{prefix}/{scope}/{owner}/{environment}/{name}

where ``owner`` is the workspace UUID for workspace secrets and the
organization UUID for organization secrets. Each Vault secret's key/value
data maps 1:1 to Tracecat secret keys, e.g. an ``ssh_key`` secret stores its
private key under the ``PRIVATE_KEY`` key.

Auth methods:

- ``jwt`` (default): the pod's projected service-account token is exchanged
  for a short-lived Vault token via the JWT auth method. This only needs
  outbound connectivity from Tracecat to Vault, so it works in isolated
  clusters where Vault cannot call back into the kube-apiserver (configure
  the JWT auth method with static ``jwt_validation_pubkeys``).
- ``token``: a static token from ``TRACECAT__VAULT_TOKEN``. Development only.

Security invariants:

- The Vault client, its token, and the value cache live in the executor
  *service* process only. Action sandboxes receive per-action secret
  projections and never see Vault credentials.
- Secret values and Vault tokens are never logged.
"""

from __future__ import annotations

import re
import threading
import time
from collections import OrderedDict
from pathlib import Path as FilePath

import httpx

from tracecat import config
from tracecat.auth.types import Role
from tracecat.exceptions import TracecatException
from tracecat.logger import logger
from tracecat.secrets.backend import SecretRegistration, SecretScope

_PATH_SEGMENT_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_REQUEST_TIMEOUT_SECONDS = 10.0
# Refresh the Vault token well before its lease expires.
_TOKEN_LEASE_SAFETY_FACTOR = 0.8


class VaultSecretsError(TracecatException):
    """Raised when the Vault secrets backend cannot resolve secrets."""


def _validate_path_segment(value: str, *, label: str) -> str:
    """Reject path segments that could traverse outside the configured prefix."""
    if not _PATH_SEGMENT_PATTERN.match(value):
        raise VaultSecretsError(
            f"Invalid {label} {value!r} for Vault secret path. "
            "Only alphanumerics, '_', '-' and '.' are allowed."
        )
    return value


class VaultSecretsBackend:
    """Resolve secret values from HashiCorp Vault KV v2 (read-only)."""

    def __init__(self) -> None:
        if not config.TRACECAT__VAULT_ADDR:
            raise VaultSecretsError(
                "TRACECAT__VAULT_ADDR must be set when "
                "TRACECAT__SECRETS_BACKEND=vault."
            )
        self._addr = config.TRACECAT__VAULT_ADDR.rstrip("/")
        self._kv_mount = config.TRACECAT__VAULT_KV_MOUNT
        self._path_prefix = config.TRACECAT__VAULT_PATH_PREFIX.strip("/")
        self._auth_method = config.TRACECAT__VAULT_AUTH_METHOD
        self._namespace = config.TRACECAT__VAULT_NAMESPACE
        self._cache_ttl = config.TRACECAT__VAULT_CACHE_TTL_SECONDS
        self._cache_max_size = config.TRACECAT__VAULT_CACHE_MAX_SIZE
        if self._auth_method not in ("jwt", "token"):
            raise VaultSecretsError(
                f"Unsupported TRACECAT__VAULT_AUTH_METHOD {self._auth_method!r}. "
                "Expected 'jwt' or 'token'."
            )
        # The cache and token are shared across event loops (AuthSandbox may
        # run under transient loops), so guard them with a threading lock
        # instead of an asyncio lock. A racy double login is harmless.
        self._lock = threading.Lock()
        self._cache: OrderedDict[
            tuple[str, str, str, str], tuple[float, dict[str, str]]
        ] = OrderedDict()
        self._client_token: str | None = None
        self._token_expires_at = 0.0

    @property
    def can_write(self) -> bool:
        return False

    async def get_secret_values(
        self,
        names: set[str],
        environment: str,
        *,
        scope: SecretScope = "workspace",
        role: Role | None = None,
    ) -> dict[str, dict[str, str]]:
        owner = self._owner_segment(scope, role)
        results: dict[str, dict[str, str]] = {}
        async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT_SECONDS) as client:
            for name in sorted(names):
                values = await self._get_one(
                    client, scope=scope, owner=owner, environment=environment, name=name
                )
                if values is not None:
                    results[name] = values
        return results

    async def list_registrations(
        self,
        environment: str | None = None,
        *,
        scope: SecretScope = "workspace",
        role: Role | None = None,
    ) -> list[SecretRegistration]:
        # Registrations are database-backed in this MVP: the UI registers
        # which secrets exist (name/keys/type), Vault only serves the values.
        from tracecat.secrets.backends.database import DatabaseSecretsBackend

        return await DatabaseSecretsBackend().list_registrations(
            environment, scope=scope, role=role
        )

    # === Internals ===

    def _owner_segment(self, scope: SecretScope, role: Role | None) -> str:
        if role is None:
            raise VaultSecretsError(
                "A role is required to resolve Vault secrets."
            )
        if scope == "workspace":
            if role.workspace_id is None:
                raise VaultSecretsError(
                    "Workspace context is required to resolve workspace secrets "
                    "from Vault."
                )
            return str(role.workspace_id)
        if role.organization_id is None:
            raise VaultSecretsError(
                "Organization context is required to resolve organization "
                "secrets from Vault."
            )
        return str(role.organization_id)

    def _secret_path(
        self, *, scope: SecretScope, owner: str, environment: str, name: str
    ) -> str:
        segments = [
            _validate_path_segment(scope, label="scope"),
            _validate_path_segment(owner, label="owner"),
            _validate_path_segment(environment, label="environment"),
            _validate_path_segment(name, label="secret name"),
        ]
        if self._path_prefix:
            segments.insert(0, self._path_prefix)
        return "/".join(segments)

    async def _get_one(
        self,
        client: httpx.AsyncClient,
        *,
        scope: SecretScope,
        owner: str,
        environment: str,
        name: str,
    ) -> dict[str, str] | None:
        cache_key = (scope, owner, environment, name)
        if (cached := self._cache_get(cache_key)) is not None:
            return cached
        path = self._secret_path(
            scope=scope, owner=owner, environment=environment, name=name
        )
        try:
            data = await self._read_kv2(client, path)
        except Exception:
            # Never serve stale data after a failed read.
            self._cache_evict(cache_key)
            raise
        if data is None:
            logger.debug("Secret not found in Vault", secret_name=name, path=path)
            return None
        values = {str(key): str(value) for key, value in data.items()}
        self._cache_put(cache_key, values)
        return values

    async def _read_kv2(
        self, client: httpx.AsyncClient, path: str
    ) -> dict[str, object] | None:
        token = await self._get_token(client)
        url = f"{self._addr}/v1/{self._kv_mount}/data/{path}"
        response = await client.get(url, headers=self._headers(token))
        if response.status_code == 404:
            return None
        if response.status_code in (401, 403):
            # Force a re-login on the next call in case the token expired
            # server-side (e.g. revocation or lease drift).
            with self._lock:
                self._client_token = None
                self._token_expires_at = 0.0
            raise VaultSecretsError(
                f"Vault denied access to secret path {path!r} "
                f"(HTTP {response.status_code}). Check the Vault policy for the "
                "configured auth role."
            )
        if response.status_code >= 400:
            raise VaultSecretsError(
                f"Vault read failed for path {path!r} (HTTP {response.status_code})."
            )
        payload = response.json()
        try:
            data = payload["data"]["data"]
        except (KeyError, TypeError) as e:
            raise VaultSecretsError(
                f"Unexpected Vault KV v2 response shape for path {path!r}."
            ) from e
        if not isinstance(data, dict):
            raise VaultSecretsError(
                f"Vault secret at path {path!r} is not a key-value mapping."
            )
        return data

    def _headers(self, token: str) -> dict[str, str]:
        headers = {"X-Vault-Token": token}
        if self._namespace:
            headers["X-Vault-Namespace"] = self._namespace
        return headers

    async def _get_token(self, client: httpx.AsyncClient) -> str:
        if self._auth_method == "token":
            if not config.TRACECAT__VAULT_TOKEN:
                raise VaultSecretsError(
                    "TRACECAT__VAULT_TOKEN must be set when "
                    "TRACECAT__VAULT_AUTH_METHOD=token."
                )
            return config.TRACECAT__VAULT_TOKEN
        with self._lock:
            if self._client_token and time.monotonic() < self._token_expires_at:
                return self._client_token
        return await self._login_jwt(client)

    async def _login_jwt(self, client: httpx.AsyncClient) -> str:
        if not config.TRACECAT__VAULT_JWT_ROLE:
            raise VaultSecretsError(
                "TRACECAT__VAULT_JWT_ROLE must be set when "
                "TRACECAT__VAULT_AUTH_METHOD=jwt."
            )
        token_path = FilePath(config.TRACECAT__VAULT_JWT_TOKEN_PATH)
        try:
            jwt = token_path.read_text(encoding="utf-8").strip()
        except OSError as e:
            raise VaultSecretsError(
                f"Could not read the service account token at {token_path}. "
                "Ensure the projected token volume is mounted."
            ) from e
        if not jwt:
            raise VaultSecretsError(
                f"The service account token at {token_path} is empty."
            )
        url = f"{self._addr}/v1/auth/{config.TRACECAT__VAULT_JWT_AUTH_MOUNT}/login"
        headers = (
            {"X-Vault-Namespace": self._namespace} if self._namespace else None
        )
        response = await client.post(
            url,
            json={"role": config.TRACECAT__VAULT_JWT_ROLE, "jwt": jwt},
            headers=headers,
        )
        if response.status_code >= 400:
            raise VaultSecretsError(
                f"Vault JWT login failed (HTTP {response.status_code}) for role "
                f"{config.TRACECAT__VAULT_JWT_ROLE!r}."
            )
        try:
            auth = response.json()["auth"]
            client_token = auth["client_token"]
            lease_duration = float(auth.get("lease_duration") or 300)
        except (KeyError, TypeError, ValueError) as e:
            raise VaultSecretsError(
                "Unexpected Vault JWT login response shape."
            ) from e
        with self._lock:
            self._client_token = client_token
            self._token_expires_at = time.monotonic() + max(
                lease_duration * _TOKEN_LEASE_SAFETY_FACTOR, 10.0
            )
        logger.debug(
            "Authenticated to Vault via JWT",
            role=config.TRACECAT__VAULT_JWT_ROLE,
            lease_duration=lease_duration,
        )
        return client_token

    def _cache_get(
        self, key: tuple[str, str, str, str]
    ) -> dict[str, str] | None:
        if self._cache_ttl <= 0:
            return None
        with self._lock:
            entry = self._cache.get(key)
            if entry is None:
                return None
            expires_at, values = entry
            if time.monotonic() >= expires_at:
                self._cache.pop(key, None)
                return None
            self._cache.move_to_end(key)
            return dict(values)

    def _cache_put(self, key: tuple[str, str, str, str], values: dict[str, str]) -> None:
        if self._cache_ttl <= 0 or self._cache_max_size <= 0:
            return
        with self._lock:
            self._cache[key] = (time.monotonic() + self._cache_ttl, dict(values))
            self._cache.move_to_end(key)
            while len(self._cache) > self._cache_max_size:
                self._cache.popitem(last=False)

    def _cache_evict(self, key: tuple[str, str, str, str]) -> None:
        with self._lock:
            self._cache.pop(key, None)
