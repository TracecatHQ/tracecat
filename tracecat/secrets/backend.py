"""Pluggable secrets backends.

A secrets backend controls where workflow secret *values* live at runtime.
The default ``db`` backend keeps today's behavior: values are Fernet-encrypted
with ``TRACECAT__DB_ENCRYPTION_KEY`` and stored in the Tracecat database.
External backends (e.g. HashiCorp Vault) resolve values from an external
secret manager at execution time, while the database keeps a value-less
*registration* of each secret (name, key names, type, environment) so the UI
and workflow validation continue to work.

Select the backend with ``TRACECAT__SECRETS_BACKEND`` (default: ``db``).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable

from tracecat import config
from tracecat.auth.types import Role
from tracecat.secrets.enums import SecretType

SecretScope = Literal["workspace", "organization"]
"""Ownership scope of a secret. Workspace secrets are resolved for workflow
actions; organization secrets back registry SSH keys and similar org-level
credentials."""


@dataclass(frozen=True, slots=True)
class SecretRegistration:
    """Value-less description of a secret: what exists, not what it contains."""

    name: str
    keys: tuple[str, ...]
    type: SecretType
    environment: str


@runtime_checkable
class SecretsBackend(Protocol):
    """Read interface for resolving secret values at runtime.

    Implementations must never log secret values. Values returned from
    :meth:`get_secret_values` are plaintext and must only live in the service
    process that resolves action contexts - never in action sandboxes.
    """

    @property
    def can_write(self) -> bool:
        """Whether Tracecat owns value storage for this backend.

        ``True`` only for the database backend. When ``False``, secret values
        cannot be created or updated through Tracecat; the database only holds
        registrations.
        """
        ...

    async def get_secret_values(
        self,
        names: set[str],
        environment: str,
        *,
        scope: SecretScope = "workspace",
        role: Role | None = None,
    ) -> dict[str, dict[str, str]]:
        """Resolve plaintext values for the given secret names.

        Returns ``{name: {key: value}}``. Names that do not exist are simply
        omitted from the result; callers are responsible for enforcing
        required vs. optional secrets.
        """
        ...

    async def list_registrations(
        self,
        environment: str | None = None,
        *,
        scope: SecretScope = "workspace",
        role: Role | None = None,
    ) -> list[SecretRegistration]:
        """List secret registrations (names/keys/types) without values."""
        ...


_backends: dict[str, SecretsBackend] = {}


def get_secrets_backend() -> SecretsBackend:
    """Return the process-wide secrets backend selected by config.

    Instances are cached per backend name so external backends can reuse
    auth sessions and their value cache across calls.
    """
    backend_name = config.TRACECAT__SECRETS_BACKEND
    if (backend := _backends.get(backend_name)) is not None:
        return backend
    match backend_name:
        case "db":
            from tracecat.secrets.backends.database import DatabaseSecretsBackend

            backend = DatabaseSecretsBackend()
        case "vault":
            from tracecat.secrets.backends.vault import VaultSecretsBackend

            backend = VaultSecretsBackend()
        case _:
            raise ValueError(
                f"Unknown secrets backend {backend_name!r}. "
                "Expected one of: 'db', 'vault'."
            )
    _backends[backend_name] = backend
    return backend


def reset_secrets_backend() -> None:
    """Drop cached backend instances. Intended for tests."""
    _backends.clear()
