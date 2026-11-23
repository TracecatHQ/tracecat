"""Bitwarden Secrets Manager provider for external secret stores.

A store points at one Bitwarden organization (US cloud, EU cloud, or a
self-hosted server) and reads with one machine-account access token, stored
encrypted on the store. A reference is the Bitwarden secret's ID.

Each fetch runs the SDK in a short-lived child process
(:mod:`.bitwarden_sm_child`). The SDK's native calls hold the GIL for the
whole network round trip and have no timeout of their own, so in-process they
would freeze the event loop and every action on the worker. The child gets
the settings, token and IDs on stdin, never in its arguments or environment,
starts in isolated mode with an allow-listed environment, and is killed with
its whole process group on timeout, cancellation or any error. Nothing is
kept after a fetch: no values, no login.
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from collections import defaultdict
from collections.abc import Collection, Sequence
from pathlib import Path
from typing import Any
from uuid import UUID

import orjson

from tracecat.exceptions import TracecatConflictError
from tracecat.logger import logger
from tracecat.network import DisallowedUrlError, validate_url_resolves_public_async
from tracecat.sandbox.utils import terminate_process_group
from tracecat.secrets.enums import SecretResolutionErrorCode, SecretStoreProvider
from tracecat.secrets.schemas import (
    BitwardenSecretsManagerStoreConfig,
    BitwardenSecretsManagerStoreCreate,
    BitwardenSecretsManagerStoreUpdate,
)
from tracecat.secrets.types import Failure, FetchOutcome, StoreContext
from tracecat_ee.secrets.errors import SecretResolutionError
from tracecat_ee.secrets.providers.backend_helpers import (
    VENDOR_CODE,
    apply_config_update,
    fail_all,
)

FETCH_TIMEOUT_SECONDS = 20.0
"""One deadline for waiting for a slot, starting the child, and its work."""

MAX_CONCURRENT_FETCHES = 16
"""Children one process runs at once, across all Bitwarden servers."""

MAX_CONCURRENT_FETCHES_PER_SERVER = 4
"""Children one process runs at once against one Bitwarden server."""

BITWARDEN_CLOUD_SERVERS: dict[str, tuple[str, str]] = {
    "us": ("https://api.bitwarden.com", "https://identity.bitwarden.com"),
    "eu": ("https://api.bitwarden.eu", "https://identity.bitwarden.eu"),
}
"""Bitwarden cloud regions and their ``(api_url, identity_url)``."""

_CHILD_SCRIPT = Path(__file__).with_name("bitwarden_sm_child.py")
_CHILD_ENV_ALLOWLIST = (
    "PATH",
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
    "REQUESTS_CA_BUNDLE",
    "HTTPS_PROXY",
    "https_proxy",
    "HTTP_PROXY",
    "http_proxy",
    "NO_PROXY",
    "no_proxy",
    "ALL_PROXY",
    "all_proxy",
)
_CHILD_CODES = frozenset(
    {
        SecretResolutionErrorCode.AUTH_FAILED,
        SecretResolutionErrorCode.NOT_FOUND,
        SecretResolutionErrorCode.ACCESS_DENIED,
        SecretResolutionErrorCode.UNAVAILABLE,
        SecretResolutionErrorCode.THROTTLED,
        SecretResolutionErrorCode.TIMEOUT,
        SecretResolutionErrorCode.INVALID_REFERENCE,
        SecretResolutionErrorCode.UNKNOWN,
    }
)

# Every fetch runs on its process's main event loop, so asyncio semaphores
# hold. The per-server cap keeps one hung server from taking every slot.
_SLOTS = asyncio.Semaphore(MAX_CONCURRENT_FETCHES)
_SERVER_SLOTS: defaultdict[str, asyncio.Semaphore] = defaultdict(
    lambda: asyncio.Semaphore(MAX_CONCURRENT_FETCHES_PER_SERVER)
)


def bitwarden_endpoints(server: str) -> tuple[str, str]:
    """Return the ``(api_url, identity_url)`` the SDK needs for a server."""
    if server in BITWARDEN_CLOUD_SERVERS:
        return BITWARDEN_CLOUD_SERVERS[server]
    return f"{server}/api", f"{server}/identity"


def _child_env() -> dict[str, str]:
    """The child's environment: only what TLS and proxies need, no Tracecat keys."""
    return {key: os.environ[key] for key in _CHILD_ENV_ALLOWLIST if key in os.environ}


def _vendor_code(value: object) -> str | None:
    """Accept only a short identifier from the child, never free text."""
    if isinstance(value, str) and VENDOR_CODE.fullmatch(value):
        return value
    return None if value is None else "invalid"


def _parse_child_output(
    stdout: bytes,
    returncode: int | None,
    references: Sequence[str],
    organization_id: UUID,
) -> dict[str, FetchOutcome]:
    """Turn the child's JSON result into outcomes. Never logs or raises."""
    if returncode != 0:
        return fail_all(
            references,
            Failure(SecretResolutionErrorCode.UNAVAILABLE, f"exit_{returncode}"),
        )
    try:
        document: Any = orjson.loads(stdout)
        results: Any = document["results"]
        fatal: Any = document.get("fatal")
    except (orjson.JSONDecodeError, KeyError, TypeError, AttributeError):
        return fail_all(
            references, Failure(SecretResolutionErrorCode.UNAVAILABLE, "unparseable")
        )
    if fatal or not isinstance(results, dict):
        return fail_all(
            references, Failure(SecretResolutionErrorCode.UNAVAILABLE, "fatal")
        )

    outcomes: dict[str, FetchOutcome] = {}
    for reference in references:
        entry = results.get(reference)
        if not isinstance(entry, dict):
            outcomes[reference] = Failure(
                SecretResolutionErrorCode.UNAVAILABLE, "missing_result"
            )
        elif isinstance(value := entry.get("value"), str):
            if entry.get("organization_id") != str(organization_id):
                # A token can only read its own organization, but the store
                # pins one; never hand out a secret from another.
                outcomes[reference] = Failure(
                    SecretResolutionErrorCode.ACCESS_DENIED, "organization_mismatch"
                )
            else:
                outcomes[reference] = value
        else:
            code = entry.get("error")
            outcomes[reference] = Failure(
                SecretResolutionErrorCode(code)
                if code in _CHILD_CODES
                else SecretResolutionErrorCode.UNAVAILABLE,
                _vendor_code(entry.get("vendor_code")),
            )
    return outcomes


async def _run_child(payload: bytes) -> tuple[bytes, int | None]:
    """Run one child to completion and always reap its process group."""
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-I",
        str(_CHILD_SCRIPT),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
        env=_child_env(),
        start_new_session=True,
    )
    try:
        stdout, _ = await process.communicate(payload)
        return stdout, process.returncode
    finally:
        await terminate_process_group(process)


async def server_resolves_public(server: str) -> bool:
    """Whether a store's server is a Bitwarden cloud or resolves publicly.

    The executor posts the machine account's credentials to this server, so a
    private, loopback, link-local or metadata address would make it an
    internal-network oracle. A name that does not resolve fails as well.
    """
    if server in BITWARDEN_CLOUD_SERVERS:
        return True
    try:
        await validate_url_resolves_public_async(server)
    except DisallowedUrlError:
        return False
    return True


async def fetch_with_child(
    config: BitwardenSecretsManagerStoreConfig,
    access_token: str,
    references: Sequence[str],
) -> dict[str, FetchOutcome]:
    """Read every reference in one child process within one deadline."""
    api_url, identity_url = bitwarden_endpoints(config.server)
    deadline = time.monotonic() + FETCH_TIMEOUT_SECONDS
    try:
        async with asyncio.timeout(FETCH_TIMEOUT_SECONDS):
            # Checked on save, and again here because the name may resolve
            # elsewhere by now. The SDK resolves it once more, so this narrows
            # the rebinding window rather than closing it.
            if not await server_resolves_public(config.server):
                logger.warning("Bitwarden server did not resolve to a public address")
                return fail_all(
                    references,
                    Failure(SecretResolutionErrorCode.UNAVAILABLE, "server_not_public"),
                )
            async with _SERVER_SLOTS[api_url], _SLOTS:
                stdout, returncode = await _run_child(
                    orjson.dumps(
                        {
                            "api_url": api_url,
                            "identity_url": identity_url,
                            "access_token": access_token,
                            "secret_ids": list(references),
                            # Leave the parent a moment to read the result.
                            "deadline_seconds": max(
                                deadline - time.monotonic() - 1.0, 0.5
                            ),
                        }
                    )
                )
    except TimeoutError:
        logger.warning(
            "Bitwarden fetch timed out",
            references=len(references),
            timeout_seconds=FETCH_TIMEOUT_SECONDS,
        )
        return fail_all(
            references, Failure(SecretResolutionErrorCode.TIMEOUT, "deadline")
        )
    except OSError as e:
        logger.warning("Bitwarden child could not start", error_type=type(e).__name__)
        return fail_all(
            references,
            Failure(SecretResolutionErrorCode.UNAVAILABLE, "spawn_failed"),
        )
    return _parse_child_output(stdout, returncode, references, config.organization_id)


def _bitwarden_config(config: object) -> BitwardenSecretsManagerStoreConfig:
    if not isinstance(config, BitwardenSecretsManagerStoreConfig):
        raise TypeError("Bitwarden Secrets Manager store requires a Bitwarden config")
    return config


class BitwardenSecretsManagerBackend:
    """Bitwarden Secrets Manager implementation of ``SecretStoreBackend``."""

    provider = SecretStoreProvider.BITWARDEN_SECRETS_MANAGER
    display_name = "Bitwarden"
    uses_access_token = True

    def new_config(
        self, params: BitwardenSecretsManagerStoreCreate
    ) -> BitwardenSecretsManagerStoreConfig:
        """Build a store config. Bitwarden has no server-owned fields."""
        return BitwardenSecretsManagerStoreConfig(
            server=params.server, organization_id=params.organization_id
        )

    def update_config(
        self,
        config: BitwardenSecretsManagerStoreConfig,
        params: BitwardenSecretsManagerStoreUpdate,
    ) -> BitwardenSecretsManagerStoreConfig:
        """Apply server/organization updates."""
        return apply_config_update(config, params)

    def check_change(
        self,
        old: BitwardenSecretsManagerStoreConfig,
        new: BitwardenSecretsManagerStoreConfig,
        *,
        references: Collection[str],
    ) -> None:
        """Saved references are secret IDs in one organization; moving the
        store to another server or organization would point them at other
        secrets, so neither can change while references exist. The access
        token is not part of the config and may change at any time.
        """
        moved = new.server != old.server or new.organization_id != old.organization_id
        if moved and references:
            raise TracecatConflictError(
                "Update or remove secrets that reference this store before"
                " changing its server or organization."
            )

    def resolution_error(
        self,
        code: SecretResolutionErrorCode,
        *,
        alias: str,
        environment: str,
        vendor_code: str | None,
    ) -> SecretResolutionError:
        """The generic error, with ``provider_error_code`` in its detail."""
        return SecretResolutionError(
            code,
            alias=alias,
            environment=environment,
            provider_label=self.display_name,
            vendor_code=vendor_code,
        )

    def is_ready(self, config: BitwardenSecretsManagerStoreConfig) -> bool:
        """The config is complete once created."""
        return True

    async def check_config(self, config: BitwardenSecretsManagerStoreConfig) -> None:
        """Refuse a self-hosted server that resolves to a non-public address.

        Reaching a private server would need a tunnel, which this provider
        does not use. Each fetch checks again (:func:`fetch_with_child`).
        """
        if not await server_resolves_public(config.server):
            raise ValueError("The Bitwarden server must resolve to a public address.")

    def validate_reference(
        self, config: BitwardenSecretsManagerStoreConfig, key: str
    ) -> None:
        """A reference is the Bitwarden secret's ID, in canonical lowercase form.

        Bitwarden returns IDs in that form, and results are matched by it.
        """
        try:
            canonical = str(UUID(key))
        except ValueError:
            canonical = None
        if canonical != key:
            raise ValueError(
                "A Bitwarden secret reference is the secret's ID in lowercase,"
                " for example 3f2a1b4c-5d6e-4f70-8a9b-0c1d2e3f4a5b."
            )

    async def fetch(
        self, store: StoreContext, references: Sequence[str]
    ) -> dict[str, FetchOutcome]:
        """Read every reference of the store in one child process."""
        if store.access_token is None:
            # The store was enabled with a token, but it may no longer
            # decrypt (for example after a failed key rotation).
            return fail_all(
                references, Failure(SecretResolutionErrorCode.CREDENTIAL_MISSING)
            )
        return await fetch_with_child(
            _bitwarden_config(store.config), store.access_token, references
        )
