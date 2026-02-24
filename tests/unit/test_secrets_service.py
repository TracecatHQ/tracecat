from datetime import UTC, datetime, timedelta

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ed25519, rsa
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
)
from cryptography.x509.oid import NameOID
from pydantic import SecretStr, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.auth.types import Role
from tracecat.exceptions import (
    TracecatCredentialsNotFoundError,
    TracecatNotFoundError,
)
from tracecat.secrets.enums import SecretType
from tracecat.secrets.schemas import (
    SecretCreate,
    SecretKeyValue,
    SecretSearch,
    SecretUpdate,
)
from tracecat.secrets.service import SecretsService

pytestmark = pytest.mark.usefixtures("db")


@pytest.fixture
async def service(session: AsyncSession, svc_admin_role: Role) -> SecretsService:
    """Create a secrets service instance for testing."""
    return SecretsService(session=session, role=svc_admin_role)


@pytest.fixture(scope="session")
def ssh_private_key() -> str:
    """Generate a valid SSH private key without a trailing newline."""
    key = ed25519.Ed25519PrivateKey.generate()
    pem = key.private_bytes(
        encoding=Encoding.PEM,
        format=PrivateFormat.PKCS8,
        encryption_algorithm=NoEncryption(),
    )
    return pem.decode("utf-8").rstrip("\n")


@pytest.fixture(scope="session")
def ssh_private_key_crlf(ssh_private_key: str) -> str:
    """Valid SSH private key with CRLF line endings."""
    return ssh_private_key.replace("\n", "\r\n")


@pytest.fixture(scope="session")
def ssh_private_key_with_whitespace(ssh_private_key: str) -> str:
    """Valid SSH private key with leading/trailing whitespace."""
    return f"\n  {ssh_private_key}\n\n"


@pytest.fixture(scope="session")
def tls_keypair() -> tuple[str, str]:
    """Generate a PEM-encoded TLS private key and certificate without trailing newlines."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name(
        [x509.NameAttribute(NameOID.COMMON_NAME, "tracecat.test")]
    )
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.now(UTC) - timedelta(days=1))
        .not_valid_after(datetime.now(UTC) + timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    key_pem = key.private_bytes(
        encoding=Encoding.PEM,
        format=PrivateFormat.PKCS8,
        encryption_algorithm=NoEncryption(),
    )
    cert_pem = cert.public_bytes(Encoding.PEM)
    return key_pem.decode("utf-8").rstrip("\n"), cert_pem.decode("utf-8").rstrip("\n")


@pytest.fixture(scope="session")
def tls_private_key(tls_keypair: tuple[str, str]) -> str:
    return tls_keypair[0]


@pytest.fixture(scope="session")
def tls_certificate(tls_keypair: tuple[str, str]) -> str:
    return tls_keypair[1]


@pytest.fixture
def secret_create_params(ssh_private_key: str) -> SecretCreate:
    """Sample secret creation parameters."""
    return SecretCreate(
        name="test-secret",
        type=SecretType.SSH_KEY,
        description="Test secret",
        tags={"test": "test"},
        keys=[SecretKeyValue(key="PRIVATE_KEY", value=SecretStr(ssh_private_key))],
        environment="test",
    )


def test_secret_create_rejects_invalid_ssh_key() -> None:
    with pytest.raises(ValidationError, match="Invalid SSH private key format"):
        SecretCreate(
            name="invalid-ssh-key",
            type=SecretType.SSH_KEY,
            keys=[SecretKeyValue(key="PRIVATE_KEY", value=SecretStr("not-a-key"))],
            environment="test",
        )


def test_secret_create_rejects_non_private_key_name(
    ssh_private_key: str,
) -> None:
    with pytest.raises(ValidationError, match="PRIVATE_KEY"):
        SecretCreate(
            name="invalid-ssh-key-name",
            type=SecretType.SSH_KEY,
            keys=[SecretKeyValue(key="private_key", value=SecretStr(ssh_private_key))],
            environment="test",
        )


def test_secret_create_normalizes_crlf_ssh_key(
    ssh_private_key: str,
    ssh_private_key_crlf: str,
) -> None:
    secret = SecretCreate(
        name="ssh-key-crlf",
        type=SecretType.SSH_KEY,
        keys=[SecretKeyValue(key="PRIVATE_KEY", value=SecretStr(ssh_private_key_crlf))],
        environment="test",
    )
    stored_key = secret.keys[0].value.get_secret_value()
    assert "\r" not in stored_key
    assert stored_key.endswith("\n")
    assert stored_key.rstrip("\n") == ssh_private_key


def test_secret_create_strips_ssh_key_whitespace(
    ssh_private_key: str,
    ssh_private_key_with_whitespace: str,
) -> None:
    secret = SecretCreate(
        name="ssh-key-whitespace",
        type=SecretType.SSH_KEY,
        keys=[
            SecretKeyValue(
                key="PRIVATE_KEY", value=SecretStr(ssh_private_key_with_whitespace)
            )
        ],
        environment="test",
    )
    stored_key = secret.keys[0].value.get_secret_value()
    assert stored_key.rstrip("\n") == ssh_private_key


def test_secret_create_rejects_invalid_mtls_certificate(
    tls_private_key: str,
) -> None:
    with pytest.raises(ValidationError, match="TLS certificate"):
        SecretCreate(
            name="invalid-mtls-cert",
            type=SecretType.MTLS,
            keys=[
                SecretKeyValue(
                    key="TLS_CERTIFICATE", value=SecretStr("not-a-certificate")
                ),
                SecretKeyValue(key="TLS_PRIVATE_KEY", value=SecretStr(tls_private_key)),
            ],
            environment="test",
        )


def test_secret_create_rejects_invalid_mtls_private_key(
    tls_certificate: str,
) -> None:
    with pytest.raises(ValidationError, match="TLS private key"):
        SecretCreate(
            name="invalid-mtls-key",
            type=SecretType.MTLS,
            keys=[
                SecretKeyValue(key="TLS_CERTIFICATE", value=SecretStr(tls_certificate)),
                SecretKeyValue(key="TLS_PRIVATE_KEY", value=SecretStr("not-a-key")),
            ],
            environment="test",
        )


def test_secret_create_rejects_invalid_ca_certificate() -> None:
    with pytest.raises(ValidationError, match="CA certificate"):
        SecretCreate(
            name="invalid-ca-cert",
            type=SecretType.CA_CERT,
            keys=[
                SecretKeyValue(
                    key="CA_CERTIFICATE", value=SecretStr("not-a-certificate")
                )
            ],
            environment="test",
        )


def test_secret_create_normalizes_mtls_values(
    tls_private_key: str, tls_certificate: str
) -> None:
    secret = SecretCreate(
        name="mtls-secret",
        type=SecretType.MTLS,
        keys=[
            SecretKeyValue(key="TLS_CERTIFICATE", value=SecretStr(tls_certificate)),
            SecretKeyValue(key="TLS_PRIVATE_KEY", value=SecretStr(tls_private_key)),
        ],
        environment="test",
    )
    cert = next(kv for kv in secret.keys if kv.key == "TLS_CERTIFICATE").value
    key = next(kv for kv in secret.keys if kv.key == "TLS_PRIVATE_KEY").value
    assert cert.get_secret_value().endswith("\n")
    assert key.get_secret_value().endswith("\n")


@pytest.mark.anyio
class TestSecretsService:
    async def test_create_and_get_secret(
        self, service: SecretsService, secret_create_params: SecretCreate
    ) -> None:
        """Test creating and retrieving a secret."""
        # Create secret
        await service.create_secret(secret_create_params)

        # Retrieve by name
        secret = await service.get_secret_by_name(secret_create_params.name)
        assert secret is not None
        assert secret.name == secret_create_params.name
        assert secret.type == secret_create_params.type
        assert secret.description == secret_create_params.description
        assert secret.tags == secret_create_params.tags
        assert secret.environment == secret_create_params.environment

        # Verify decrypted keys
        decrypted_keys = service.decrypt_keys(secret.encrypted_keys)
        assert len(decrypted_keys) == 1
        assert decrypted_keys[0].key == secret_create_params.keys[0].key
        assert decrypted_keys[0].value == secret_create_params.keys[0].value

    async def test_update_secret_preserves_empty_values(
        self, service: SecretsService, secret_create_params: SecretCreate
    ) -> None:
        """Test that updating a secret with empty values preserves existing values."""
        # Create initial secret with multiple keys
        initial_keys = [
            SecretKeyValue(key="key1", value=SecretStr("value1")),
            SecretKeyValue(key="key2", value=SecretStr("value2")),
            SecretKeyValue(key="key3", value=SecretStr("value3")),
        ]
        create_params = SecretCreate(
            name="test-preserve-secret",
            type=SecretType.CUSTOM,
            description="Initial description",
            keys=initial_keys,
        )
        await service.create_secret(create_params)

        # Update with a mix of scenarios:
        # - key1: provide empty value (should preserve original)
        # - key2: provide new value (should update)
        # - key3: not included (will be removed in this update)
        # - key4: new key being added
        update_params = SecretUpdate(
            description="Updated description",
            keys=[
                SecretKeyValue(key="key1", value=SecretStr("")),  # Empty value
                SecretKeyValue(
                    key="key2", value=SecretStr("updated_value2")
                ),  # Updated
                SecretKeyValue(key="key4", value=SecretStr("new_value4")),  # New key
            ],
        )

        # Get secret
        secret = await service.get_secret_by_name(create_params.name)
        assert secret is not None

        # Update secret
        await service.update_secret(secret, update_params)

        # Verify updates
        updated_secret = await service.get_secret_by_name(create_params.name)
        assert updated_secret.description == update_params.description

        # Check the keys were handled correctly
        decrypted_keys = {
            k.key: k.value.get_secret_value()
            for k in service.decrypt_keys(updated_secret.encrypted_keys)
        }

        assert len(decrypted_keys) == 3
        assert decrypted_keys["key1"] == "value1"  # Original value preserved
        assert decrypted_keys["key2"] == "updated_value2"  # Value updated
        assert "key3" not in decrypted_keys  # Key removed
        assert decrypted_keys["key4"] == "new_value4"  # New key added

    async def test_update_secret_recovers_from_corrupted_encrypted_keys(
        self, service: SecretsService
    ) -> None:
        """Test that corrupted encrypted keys can be recovered with full key input."""
        create_params = SecretCreate(
            name="test-corrupted-secret-recovery",
            type=SecretType.CUSTOM,
            description="Initial description",
            keys=[
                SecretKeyValue(key="api_key", value=SecretStr("old-api-key")),
                SecretKeyValue(key="api_secret", value=SecretStr("old-api-secret")),
            ],
        )
        await service.create_secret(create_params)
        secret = await service.get_secret_by_name(create_params.name)

        # Simulate a bad DB encryption key by corrupting the stored ciphertext.
        secret.encrypted_keys = b"not-a-valid-fernet-token"
        service.session.add(secret)
        await service.session.commit()

        update_params = SecretUpdate(
            description="Recovered description",
            keys=[
                SecretKeyValue(key="api_key", value=SecretStr("new-api-key")),
                SecretKeyValue(key="api_secret", value=SecretStr("new-api-secret")),
            ],
        )
        await service.update_secret(secret, update_params)

        recovered_secret = await service.get_secret_by_name(create_params.name)
        recovered_keys = {
            k.key: k.value.get_secret_value()
            for k in service.decrypt_keys(recovered_secret.encrypted_keys)
        }
        assert recovered_secret.description == "Recovered description"
        assert recovered_keys == {
            "api_key": "new-api-key",
            "api_secret": "new-api-secret",
        }

    async def test_update_secret_corrupted_encrypted_keys_require_full_values(
        self, service: SecretsService
    ) -> None:
        """Test that corrupted secrets reject blank key values on recovery."""
        create_params = SecretCreate(
            name="test-corrupted-secret-empty-values",
            type=SecretType.CUSTOM,
            description="Initial description",
            keys=[SecretKeyValue(key="token", value=SecretStr("old-token"))],
        )
        await service.create_secret(create_params)
        secret = await service.get_secret_by_name(create_params.name)

        secret.encrypted_keys = b"not-a-valid-fernet-token"
        service.session.add(secret)
        await service.session.commit()

        with pytest.raises(ValueError, match="Re-enter all key values"):
            await service.update_secret(
                secret,
                SecretUpdate(
                    keys=[SecretKeyValue(key="token", value=SecretStr(""))],
                ),
            )

    async def test_update_secret_corrupted_encrypted_keys_require_keys_payload(
        self, service: SecretsService
    ) -> None:
        """Test that corrupted secrets reject updates that omit keys entirely."""
        create_params = SecretCreate(
            name="test-corrupted-secret-missing-keys",
            type=SecretType.CUSTOM,
            description="Initial description",
            keys=[SecretKeyValue(key="token", value=SecretStr("old-token"))],
        )
        await service.create_secret(create_params)
        secret = await service.get_secret_by_name(create_params.name)

        secret.encrypted_keys = b"not-a-valid-fernet-token"
        service.session.add(secret)
        await service.session.commit()

        with pytest.raises(
            ValueError,
            match="Re-enter all key names and values",
        ):
            await service.update_secret(
                secret,
                SecretUpdate(description="Updated description"),
            )

    async def test_update_mtls_secret_preserves_empty_values(
        self,
        service: SecretsService,
        tls_private_key: str,
        tls_certificate: str,
    ) -> None:
        create_params = SecretCreate(
            name="mtls-update-secret",
            type=SecretType.MTLS,
            description="Initial description",
            keys=[
                SecretKeyValue(key="TLS_CERTIFICATE", value=SecretStr(tls_certificate)),
                SecretKeyValue(key="TLS_PRIVATE_KEY", value=SecretStr(tls_private_key)),
            ],
            environment="test",
        )
        await service.create_secret(create_params)
        secret = await service.get_secret_by_name(create_params.name)
        update_params = SecretUpdate(
            description="Updated description",
            keys=[
                SecretKeyValue(key="TLS_CERTIFICATE", value=SecretStr("")),
                SecretKeyValue(key="TLS_PRIVATE_KEY", value=SecretStr("")),
            ],
        )
        await service.update_secret(secret, update_params)

        updated_secret = await service.get_secret_by_name(create_params.name)
        updated_keys = {
            k.key: k.value.get_secret_value()
            for k in service.decrypt_keys(updated_secret.encrypted_keys)
        }
        expected_keys = {k.key: k.value.get_secret_value() for k in create_params.keys}
        assert updated_secret.description == update_params.description
        assert updated_keys == expected_keys

    async def test_update_ca_cert_preserves_empty_values(
        self, service: SecretsService, tls_certificate: str
    ) -> None:
        create_params = SecretCreate(
            name="ca-cert-update-secret",
            type=SecretType.CA_CERT,
            description="Initial description",
            keys=[
                SecretKeyValue(key="CA_CERTIFICATE", value=SecretStr(tls_certificate)),
            ],
            environment="test",
        )
        await service.create_secret(create_params)
        secret = await service.get_secret_by_name(create_params.name)
        update_params = SecretUpdate(
            description="Updated description",
            keys=[SecretKeyValue(key="CA_CERTIFICATE", value=SecretStr(""))],
        )
        await service.update_secret(secret, update_params)

        updated_secret = await service.get_secret_by_name(create_params.name)
        updated_keys = {
            k.key: k.value.get_secret_value()
            for k in service.decrypt_keys(updated_secret.encrypted_keys)
        }
        expected_keys = {k.key: k.value.get_secret_value() for k in create_params.keys}
        assert updated_secret.description == update_params.description
        assert updated_keys == expected_keys

    async def test_delete_secret(
        self, service: SecretsService, secret_create_params: SecretCreate
    ) -> None:
        """Test deleting a secret."""
        # Create secret
        await service.create_secret(secret_create_params)

        # Get secret to obtain ID
        secret = await service.get_secret_by_name(secret_create_params.name)
        assert secret is not None

        # Delete secret
        await service.delete_secret(secret)

        # Verify deletion
        with pytest.raises(TracecatNotFoundError):
            await service.get_secret_by_name(secret_create_params.name)

    async def test_update_secret_rejects_ssh_key_rotation(
        self, service: SecretsService, secret_create_params: SecretCreate
    ) -> None:
        """Test that updating an SSH key secret rejects key changes."""
        await service.create_secret(secret_create_params)
        secret = await service.get_secret_by_name(secret_create_params.name)
        update_params = SecretUpdate(
            keys=[SecretKeyValue(key="PRIVATE_KEY", value=SecretStr("not-a-key"))]
        )
        with pytest.raises(ValueError, match="write-once"):
            await service.update_secret(secret, update_params)

    async def test_update_secret_rejects_type_change_to_ssh_key(
        self, service: SecretsService
    ) -> None:
        """Test that changing a secret to SSH key type via update is rejected."""
        create_params = SecretCreate(
            name="custom-to-ssh",
            type=SecretType.CUSTOM,
            description="Initial description",
            keys=[SecretKeyValue(key="token", value=SecretStr("value"))],
        )
        await service.create_secret(create_params)
        secret = await service.get_secret_by_name(create_params.name)
        update_params = SecretUpdate(type=SecretType.SSH_KEY)
        with pytest.raises(ValueError, match="SSH key secrets must be created"):
            await service.update_secret(secret, update_params)

    async def test_list_secrets(
        self, service: SecretsService, secret_create_params: SecretCreate
    ) -> None:
        """Test listing secrets."""
        # Create multiple secrets
        await service.create_secret(secret_create_params)

        second_secret = SecretCreate(
            name="test-secret-2",
            type=SecretType.CUSTOM,
            description="Second test secret",
            tags={"test": "test"},
            keys=[SecretKeyValue(key="api_key", value=SecretStr("test-api-key"))],
            environment="test",
        )
        await service.create_secret(second_secret)

        # List all secrets
        secrets = await service.list_secrets()
        assert len(secrets) >= 2

        # List secrets by type
        api_secrets = await service.list_secrets(types={SecretType.CUSTOM})
        assert len(api_secrets) >= 1
        assert all(s.type == SecretType.CUSTOM for s in api_secrets)

    async def test_get_ssh_key(
        self,
        service: SecretsService,
        secret_create_params: SecretCreate,
        ssh_private_key: str,
    ) -> None:
        """Test retrieving SSH key."""
        # Create SSH key secret
        await service.create_org_secret(secret_create_params)

        # Retrieve SSH key
        ssh_key = await service.get_ssh_key(
            secret_create_params.name, secret_create_params.environment
        )
        assert isinstance(ssh_key, SecretStr)
        assert ssh_key.get_secret_value() == f"{ssh_private_key}\n"
        await service.create_org_secret(
            SecretCreate(
                name="test-secret-2",
                type=SecretType.SSH_KEY,
                description="Test secret",
                keys=[
                    SecretKeyValue(
                        key="PRIVATE_KEY",
                        value=SecretStr(f"{ssh_private_key}\n"),
                    )
                ],
                environment="test",
            )
        )

        # Retrieve SSH key
        ssh_key = await service.get_ssh_key("test-secret-2", "test")
        assert isinstance(ssh_key, SecretStr)
        assert ssh_key.get_secret_value() == f"{ssh_private_key}\n"

    async def test_get_nonexistent_ssh_key(self, service: SecretsService) -> None:
        """Test retrieving non-existent SSH key."""
        with pytest.raises(TracecatCredentialsNotFoundError):
            await service.get_ssh_key("nonexistent-key")

    async def test_search_secrets(
        self, service: SecretsService, secret_create_params: SecretCreate
    ) -> None:
        """Test searching secrets."""
        # Create a secret
        await service.create_secret(secret_create_params)

        # Search by name
        found_secrets = await service.search_secrets(
            params=SecretSearch(
                names={secret_create_params.name},
                environment=secret_create_params.environment,
            )
        )
        assert len(found_secrets) == 1
        assert found_secrets[0].name == secret_create_params.name

        # Search by environment
        env_secrets = await service.search_secrets(
            params=SecretSearch(
                environment=secret_create_params.environment,
            )
        )
        assert len(env_secrets) >= 1
        assert all(
            s.environment == secret_create_params.environment for s in env_secrets
        )
