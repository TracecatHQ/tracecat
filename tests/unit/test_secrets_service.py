import pytest
from pydantic import SecretStr
from sqlmodel.ext.asyncio.session import AsyncSession

from tracecat.secrets.enums import SecretType
from tracecat.secrets.models import (
    SecretCreate,
    SecretKeyValue,
    SecretSearch,
    SecretUpdate,
)
from tracecat.secrets.service import SecretsService
from tracecat.types.auth import Role
from tracecat.types.exceptions import TracecatNotFoundError

pytestmark = pytest.mark.usefixtures("db")


@pytest.fixture
async def service(session: AsyncSession, svc_role: Role) -> SecretsService:
    """Create a secrets service instance for testing."""
    return SecretsService(session=session, role=svc_role)


@pytest.fixture
def secret_create_params() -> SecretCreate:
    """Sample secret creation parameters."""
    return SecretCreate(
        name="test-secret",
        type=SecretType.SSH_KEY,
        description="Test secret",
        tags={"test": "test"},
        keys=[SecretKeyValue(key="private_key", value=SecretStr("test-private-key"))],
        environment="test",
    )


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

    async def test_update_secret(
        self, service: SecretsService, secret_create_params: SecretCreate
    ) -> None:
        """Test updating a secret."""
        # Create initial secret
        await service.create_secret(secret_create_params)

        # Update parameters
        update_params = SecretUpdate(
            description="Updated description",
            keys=[SecretKeyValue(key="new_key", value=SecretStr("new_value"))],
        )

        # Get secret
        secret = await service.get_secret_by_name(secret_create_params.name)
        assert secret is not None

        # Update secret
        await service.update_secret(secret, update_params)

        # Verify updates
        updated_secret = await service.get_secret_by_name(secret_create_params.name)
        assert updated_secret.description == update_params.description
        decrypted_keys = service.decrypt_keys(updated_secret.encrypted_keys)
        assert len(decrypted_keys) == 1
        assert decrypted_keys[0].key == "new_key"
        assert decrypted_keys[0].value.get_secret_value() == "new_value"

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
        self, service: SecretsService, secret_create_params: SecretCreate
    ) -> None:
        """Test retrieving SSH key."""
        # Create SSH key secret
        await service.create_org_secret(secret_create_params)

        # Retrieve SSH key
        ssh_key = await service.get_ssh_key(
            secret_create_params.name, secret_create_params.environment
        )
        assert isinstance(ssh_key, SecretStr)
        assert ssh_key.get_secret_value() == "test-private-key\n"
        await service.create_org_secret(
            SecretCreate(
                name="test-secret-2",
                type=SecretType.SSH_KEY,
                description="Test secret",
                keys=[
                    SecretKeyValue(
                        key="private_key", value=SecretStr("test-private-key-again\n")
                    )
                ],
                environment="test",
            )
        )

        # Retrieve SSH key
        ssh_key = await service.get_ssh_key("test-secret-2", "test")
        assert isinstance(ssh_key, SecretStr)
        assert ssh_key.get_secret_value() == "test-private-key-again\n"

    async def test_get_nonexistent_ssh_key(self, service: SecretsService) -> None:
        """Test retrieving non-existent SSH key."""
        with pytest.raises(TracecatNotFoundError):
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
