"""Tests for pluggable secrets backends (factory, Vault backend, dispatch)."""

import time
import uuid

import httpx
import pytest
import respx

from tracecat import config
from tracecat.auth.types import Role
from tracecat.secrets.backend import (
    get_secrets_backend,
    reset_secrets_backend,
)
from tracecat.secrets.backends.database import DatabaseSecretsBackend
from tracecat.secrets.backends.vault import VaultSecretsBackend, VaultSecretsError

VAULT_ADDR = "http://vault.test:8200"

WORKSPACE_ID = uuid.uuid4()
ORG_ID = uuid.uuid4()


@pytest.fixture
def test_backend_role() -> Role:
    return Role(
        type="service",
        service_id="tracecat-service",
        workspace_id=WORKSPACE_ID,
        organization_id=ORG_ID,
    )


@pytest.fixture
def vault_config(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(config, "TRACECAT__VAULT_ADDR", VAULT_ADDR)
    monkeypatch.setattr(config, "TRACECAT__VAULT_KV_MOUNT", "secret")
    monkeypatch.setattr(config, "TRACECAT__VAULT_PATH_PREFIX", "tracecat")
    monkeypatch.setattr(config, "TRACECAT__VAULT_AUTH_METHOD", "token")
    monkeypatch.setattr(config, "TRACECAT__VAULT_TOKEN", "dev-token")
    monkeypatch.setattr(config, "TRACECAT__VAULT_NAMESPACE", None)
    monkeypatch.setattr(config, "TRACECAT__VAULT_CACHE_TTL_SECONDS", 45.0)
    monkeypatch.setattr(config, "TRACECAT__VAULT_CACHE_MAX_SIZE", 1024)
    reset_secrets_backend()
    yield
    reset_secrets_backend()


def _kv_url(scope: str, owner: uuid.UUID, environment: str, name: str) -> str:
    return (
        f"{VAULT_ADDR}/v1/secret/data/tracecat/{scope}/{owner}/{environment}/{name}"
    )


def _kv_response(data: dict[str, str]) -> httpx.Response:
    return httpx.Response(200, json={"data": {"data": data, "metadata": {}}})


class TestBackendFactory:
    def test_default_is_database(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(config, "TRACECAT__SECRETS_BACKEND", "db")
        reset_secrets_backend()
        backend = get_secrets_backend()
        assert isinstance(backend, DatabaseSecretsBackend)
        assert backend.can_write is True
        # Instances are cached per backend name
        assert get_secrets_backend() is backend
        reset_secrets_backend()

    def test_unknown_backend_raises(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(config, "TRACECAT__SECRETS_BACKEND", "s3")
        reset_secrets_backend()
        with pytest.raises(ValueError, match="Unknown secrets backend"):
            get_secrets_backend()
        reset_secrets_backend()

    def test_vault_backend_selected(
        self, monkeypatch: pytest.MonkeyPatch, vault_config
    ):
        monkeypatch.setattr(config, "TRACECAT__SECRETS_BACKEND", "vault")
        backend = get_secrets_backend()
        assert isinstance(backend, VaultSecretsBackend)
        assert backend.can_write is False

    def test_vault_requires_addr(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(config, "TRACECAT__VAULT_ADDR", None)
        with pytest.raises(VaultSecretsError, match="TRACECAT__VAULT_ADDR"):
            VaultSecretsBackend()

    def test_vault_rejects_unknown_auth_method(
        self, monkeypatch: pytest.MonkeyPatch, vault_config
    ):
        monkeypatch.setattr(config, "TRACECAT__VAULT_AUTH_METHOD", "ldap")
        with pytest.raises(VaultSecretsError, match="AUTH_METHOD"):
            VaultSecretsBackend()


@pytest.mark.anyio
class TestVaultSecretsBackend:
    @respx.mock
    async def test_read_custom_secret(self, vault_config, test_backend_role):
        route = respx.get(_kv_url("workspace", WORKSPACE_ID, "default", "my_secret")).mock(
            return_value=_kv_response({"API_KEY": "s3cret", "USER": "alice"})
        )
        backend = VaultSecretsBackend()
        values = await backend.get_secret_values(
            {"my_secret"}, "default", scope="workspace", role=test_backend_role
        )
        assert values == {"my_secret": {"API_KEY": "s3cret", "USER": "alice"}}
        assert route.called
        request = route.calls.last.request
        assert request.headers["X-Vault-Token"] == "dev-token"

    @respx.mock
    async def test_missing_secret_is_omitted(self, vault_config, test_backend_role):
        respx.get(_kv_url("workspace", WORKSPACE_ID, "default", "nope")).mock(
            return_value=httpx.Response(404, json={"errors": []})
        )
        backend = VaultSecretsBackend()
        values = await backend.get_secret_values(
            {"nope"}, "default", scope="workspace", role=test_backend_role
        )
        assert values == {}

    @respx.mock
    async def test_organization_scope_uses_org_path(
        self, vault_config, test_backend_role
    ):
        route = respx.get(
            _kv_url("organization", ORG_ID, "default", "tracecat_registry_ssh_key")
        ).mock(return_value=_kv_response({"PRIVATE_KEY": "---key---"}))
        backend = VaultSecretsBackend()
        values = await backend.get_secret_values(
            {"tracecat_registry_ssh_key"},
            "default",
            scope="organization",
            role=test_backend_role,
        )
        assert values == {"tracecat_registry_ssh_key": {"PRIVATE_KEY": "---key---"}}
        assert route.called

    @respx.mock
    async def test_cache_hit_avoids_second_read(self, vault_config, test_backend_role):
        route = respx.get(_kv_url("workspace", WORKSPACE_ID, "default", "cached")).mock(
            return_value=_kv_response({"K": "v"})
        )
        backend = VaultSecretsBackend()
        for _ in range(3):
            values = await backend.get_secret_values(
                {"cached"}, "default", scope="workspace", role=test_backend_role
            )
            assert values == {"cached": {"K": "v"}}
        assert route.call_count == 1

    @respx.mock
    async def test_cache_expires(
        self, monkeypatch: pytest.MonkeyPatch, vault_config, test_backend_role
    ):
        monkeypatch.setattr(config, "TRACECAT__VAULT_CACHE_TTL_SECONDS", 0.05)
        route = respx.get(_kv_url("workspace", WORKSPACE_ID, "default", "ttl")).mock(
            return_value=_kv_response({"K": "v"})
        )
        backend = VaultSecretsBackend()
        await backend.get_secret_values(
            {"ttl"}, "default", scope="workspace", role=test_backend_role
        )
        time.sleep(0.06)
        await backend.get_secret_values(
            {"ttl"}, "default", scope="workspace", role=test_backend_role
        )
        assert route.call_count == 2

    @respx.mock
    async def test_cache_disabled_with_zero_ttl(
        self, monkeypatch: pytest.MonkeyPatch, vault_config, test_backend_role
    ):
        monkeypatch.setattr(config, "TRACECAT__VAULT_CACHE_TTL_SECONDS", 0.0)
        route = respx.get(_kv_url("workspace", WORKSPACE_ID, "default", "nocache")).mock(
            return_value=_kv_response({"K": "v"})
        )
        backend = VaultSecretsBackend()
        for _ in range(2):
            await backend.get_secret_values(
                {"nocache"}, "default", scope="workspace", role=test_backend_role
            )
        assert route.call_count == 2

    @respx.mock
    async def test_permission_denied_raises(self, vault_config, test_backend_role):
        respx.get(_kv_url("workspace", WORKSPACE_ID, "default", "denied")).mock(
            return_value=httpx.Response(403, json={"errors": ["permission denied"]})
        )
        backend = VaultSecretsBackend()
        with pytest.raises(VaultSecretsError, match="denied access"):
            await backend.get_secret_values(
                {"denied"}, "default", scope="workspace", role=test_backend_role
            )

    @respx.mock
    async def test_jwt_login_flow_and_token_reuse(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path,
        vault_config,
        test_backend_role,
    ):
        token_file = tmp_path / "vault-token"
        token_file.write_text("sa-jwt-token\n")
        monkeypatch.setattr(config, "TRACECAT__VAULT_AUTH_METHOD", "jwt")
        monkeypatch.setattr(config, "TRACECAT__VAULT_JWT_AUTH_MOUNT", "jwt")
        monkeypatch.setattr(config, "TRACECAT__VAULT_JWT_ROLE", "tracecat-executor")
        monkeypatch.setattr(
            config, "TRACECAT__VAULT_JWT_TOKEN_PATH", str(token_file)
        )
        monkeypatch.setattr(config, "TRACECAT__VAULT_CACHE_TTL_SECONDS", 0.0)

        login_route = respx.post(f"{VAULT_ADDR}/v1/auth/jwt/login").mock(
            return_value=httpx.Response(
                200,
                json={"auth": {"client_token": "vault-tok", "lease_duration": 3600}},
            )
        )
        kv_route = respx.get(_kv_url("workspace", WORKSPACE_ID, "default", "jwt_s")).mock(
            return_value=_kv_response({"K": "v"})
        )

        backend = VaultSecretsBackend()
        for _ in range(2):
            values = await backend.get_secret_values(
                {"jwt_s"}, "default", scope="workspace", role=test_backend_role
            )
            assert values == {"jwt_s": {"K": "v"}}

        # Login happens once; the lease-cached token is reused.
        assert login_route.call_count == 1
        assert kv_route.call_count == 2
        login_body = login_route.calls.last.request.content
        assert b"sa-jwt-token" in login_body
        assert b"tracecat-executor" in login_body
        assert kv_route.calls.last.request.headers["X-Vault-Token"] == "vault-tok"

    @respx.mock
    async def test_auth_failure_clears_cached_token(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path,
        vault_config,
        test_backend_role,
    ):
        token_file = tmp_path / "vault-token"
        token_file.write_text("sa-jwt-token")
        monkeypatch.setattr(config, "TRACECAT__VAULT_AUTH_METHOD", "jwt")
        monkeypatch.setattr(config, "TRACECAT__VAULT_JWT_ROLE", "tracecat-executor")
        monkeypatch.setattr(
            config, "TRACECAT__VAULT_JWT_TOKEN_PATH", str(token_file)
        )
        monkeypatch.setattr(config, "TRACECAT__VAULT_CACHE_TTL_SECONDS", 0.0)

        login_route = respx.post(f"{VAULT_ADDR}/v1/auth/jwt/login").mock(
            return_value=httpx.Response(
                200,
                json={"auth": {"client_token": "vault-tok", "lease_duration": 3600}},
            )
        )
        respx.get(_kv_url("workspace", WORKSPACE_ID, "default", "s")).mock(
            return_value=httpx.Response(403, json={"errors": ["permission denied"]})
        )

        backend = VaultSecretsBackend()
        for _ in range(2):
            with pytest.raises(VaultSecretsError):
                await backend.get_secret_values(
                    {"s"}, "default", scope="workspace", role=test_backend_role
                )
        # 403 invalidates the cached Vault token, forcing a fresh login.
        assert login_route.call_count == 2

    async def test_workspace_scope_requires_workspace_id(self, vault_config):
        backend = VaultSecretsBackend()
        role = Role(type="service", service_id="tracecat-service")
        with pytest.raises(VaultSecretsError, match="Workspace context"):
            await backend.get_secret_values(
                {"s"}, "default", scope="workspace", role=role
            )

    async def test_path_traversal_rejected(self, vault_config, test_backend_role):
        backend = VaultSecretsBackend()
        with pytest.raises(VaultSecretsError, match="Invalid"):
            await backend.get_secret_values(
                {"../../sys/policy"},
                "default",
                scope="workspace",
                role=test_backend_role,
            )
        with pytest.raises(VaultSecretsError, match="Invalid"):
            await backend.get_secret_values(
                {"ok_name"},
                "../other-env",
                scope="workspace",
                role=test_backend_role,
            )


@pytest.mark.anyio
class TestExternalSshKeyDispatch:
    """SecretsService resolves SSH keys via the backend when it is read-only."""

    def _service(self, role: Role):
        from tracecat.secrets.service import SecretsService

        service = SecretsService.__new__(SecretsService)
        service.role = role
        return service

    class _StubBackend:
        def __init__(self, values: dict[str, dict[str, str]]):
            self.values = values
            self.calls: list[tuple[set[str], str, str]] = []

        @property
        def can_write(self) -> bool:
            return False

        async def get_secret_values(
            self, names, environment, *, scope="workspace", role=None
        ):
            self.calls.append((set(names), environment, scope))
            return {
                name: kv for name, kv in self.values.items() if name in names
            }

        async def list_registrations(
            self, environment=None, *, scope="workspace", role=None
        ):
            return []

    async def test_ssh_key_resolved_from_backend(self, test_backend_role):
        backend = self._StubBackend(
            {"my_ssh_key": {"PRIVATE_KEY": "-----BEGIN KEY-----"}}
        )
        service = self._service(test_backend_role)
        key = await service._get_ssh_key_from_backend(backend, "my_ssh_key", None)
        # A trailing newline is enforced for libcrypto compatibility.
        assert key.get_secret_value() == "-----BEGIN KEY-----\n"
        assert backend.calls == [({"my_ssh_key"}, "default", "organization")]

    async def test_single_key_fallback(self, test_backend_role):
        backend = self._StubBackend({"legacy_key": {"legacy_name": "keydata\n"}})
        service = self._service(test_backend_role)
        key = await service._get_ssh_key_from_backend(backend, "legacy_key", None)
        assert key.get_secret_value() == "keydata\n"

    async def test_missing_ssh_key_raises(self, test_backend_role):
        from tracecat.exceptions import TracecatCredentialsNotFoundError

        backend = self._StubBackend({})
        service = self._service(test_backend_role)
        with pytest.raises(TracecatCredentialsNotFoundError, match="not found"):
            await service._get_ssh_key_from_backend(backend, "absent", None)
