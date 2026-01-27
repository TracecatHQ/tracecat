"""Tests for OAuth state database-backed CSRF protection."""

import uuid
from datetime import UTC, datetime, timedelta
from typing import ClassVar
from unittest.mock import patch

import pytest
from cryptography.fernet import Fernet
from pydantic import BaseModel, SecretStr
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.auth.types import AccessLevel, Role
from tracecat.db.models import OAuthStateDB, User, Workspace
from tracecat.integrations.enums import OAuthGrantType
from tracecat.integrations.providers.base import AuthorizationCodeOAuthProvider
from tracecat.integrations.schemas import (
    ProviderKey,
    ProviderMetadata,
    ProviderScopes,
)
from tracecat.integrations.service import IntegrationService

pytestmark = pytest.mark.usefixtures("db")


# Mock OAuth Provider for testing
class MockProviderConfig(BaseModel):
    """Configuration for mock OAuth provider."""

    redirect_uri: str | None = None


class MockOAuthProvider(AuthorizationCodeOAuthProvider):
    """Mock OAuth provider for testing."""

    id: ClassVar[str] = "mock_oauth_state_provider"
    _authorization_endpoint: ClassVar[str] = "https://mock.provider/oauth/authorize"  # type: ignore[assignment]
    _token_endpoint: ClassVar[str] = "https://mock.provider/oauth/token"  # type: ignore[assignment]
    config_model: ClassVar[type[BaseModel]] = MockProviderConfig  # type: ignore[assignment]
    scopes: ClassVar[ProviderScopes] = ProviderScopes(
        default=["read", "write"],
    )
    metadata: ClassVar[ProviderMetadata] = ProviderMetadata(
        id="mock_oauth_state_provider",
        name="Mock OAuth State Provider",
        description="A mock OAuth provider for testing state",
        api_docs_url="https://mock.provider/docs",
        enabled=True,
    )


@pytest.fixture
def encryption_key(monkeypatch: pytest.MonkeyPatch) -> str:
    """Set up encryption key for testing."""
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("TRACECAT__DB_ENCRYPTION_KEY", key)
    return key


@pytest.fixture
async def test_user(session: AsyncSession) -> User:
    """Create a test user for OAuth state tests."""
    user = User(
        id=uuid.uuid4(),
        email="test@example.com",
        hashed_password="hashed_password_placeholder",
        last_login_at=None,
        is_active=True,
        is_superuser=False,
        is_verified=True,
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


@pytest.fixture
async def integration_service(
    session: AsyncSession, svc_workspace, test_user: User, encryption_key: str
) -> IntegrationService:
    """Create an integration service instance for testing."""
    role = Role(
        type="user",
        access_level=AccessLevel.BASIC,
        workspace_id=svc_workspace.id,
        user_id=test_user.id,
        service_id="tracecat-api",
    )
    return IntegrationService(session=session, role=role)


@pytest.fixture
async def test_role_with_user(svc_workspace, test_user: User) -> Role:
    """Create a test role with a real user for OAuth state tests."""
    return Role(
        type="user",
        workspace_id=svc_workspace.id,
        user_id=test_user.id,
        service_id="tracecat-api",
    )


@pytest.fixture(autouse=True)
def register_mock_provider():
    """Mock the provider registry to return our mock provider."""
    with patch("tracecat.integrations.service.get_provider_class") as mock_get_provider:
        mock_get_provider.return_value = MockOAuthProvider
        yield


@pytest.mark.anyio
class TestOAuthState:
    """Test OAuth state creation and validation."""

    async def test_oauth_state_creation(
        self,
        session: AsyncSession,
        test_role_with_user: Role,
    ) -> None:
        """Test that OAuth state can be created in the database."""
        state_id = uuid.uuid4()
        expires_at = datetime.now(UTC) + timedelta(minutes=10)

        # Ensure role has required fields
        assert test_role_with_user.workspace_id is not None
        assert test_role_with_user.user_id is not None
        oauth_state = OAuthStateDB(
            state=state_id,
            workspace_id=test_role_with_user.workspace_id,
            user_id=test_role_with_user.user_id,
            provider_id=MockOAuthProvider.id,
            expires_at=expires_at,
        )
        session.add(oauth_state)
        await session.commit()

        # Verify state exists
        saved_state = await session.get(OAuthStateDB, state_id)
        assert saved_state is not None

        assert saved_state.state == state_id
        assert saved_state.workspace_id == test_role_with_user.workspace_id
        assert saved_state.user_id == test_role_with_user.user_id
        assert saved_state.provider_id == MockOAuthProvider.id
        assert saved_state.expires_at == expires_at

    async def test_oauth_state_cleanup(
        self,
        session: AsyncSession,
        test_role_with_user: Role,
    ) -> None:
        """Test that expired OAuth states can be cleaned up."""
        # Create multiple states with different expiration times
        current_time = datetime.now(UTC)

        # Ensure role has required fields
        assert test_role_with_user.workspace_id is not None
        assert test_role_with_user.user_id is not None
        # Expired states
        for i in range(3):
            expired_state = OAuthStateDB(
                state=uuid.uuid4(),
                workspace_id=test_role_with_user.workspace_id,
                user_id=test_role_with_user.user_id,
                provider_id=MockOAuthProvider.id,
                expires_at=current_time - timedelta(minutes=i + 1),
            )
            session.add(expired_state)

        # Valid state
        valid_state = OAuthStateDB(
            state=uuid.uuid4(),
            workspace_id=test_role_with_user.workspace_id,
            user_id=test_role_with_user.user_id,
            provider_id=MockOAuthProvider.id,
            expires_at=current_time + timedelta(minutes=10),
        )
        session.add(valid_state)
        await session.commit()

        # Perform cleanup
        stmt = delete(OAuthStateDB).where(OAuthStateDB.expires_at < current_time)
        await session.execute(stmt)
        await session.commit()

        # Verify only valid state remains
        stmt = select(OAuthStateDB).where(OAuthStateDB.state == valid_state.state)
        result = await session.execute(stmt)
        remaining_states = result.scalars().all()

        assert len(remaining_states) == 1
        assert remaining_states[0].state == valid_state.state

    async def test_oauth_state_with_for_update_lock(
        self,
        session: AsyncSession,
        test_role_with_user: Role,
    ) -> None:
        """Test that OAuth state can be selected with FOR UPDATE lock."""
        state_id = uuid.uuid4()
        expires_at = datetime.now(UTC) + timedelta(minutes=10)

        # Ensure role has required fields
        assert test_role_with_user.workspace_id is not None
        assert test_role_with_user.user_id is not None
        oauth_state = OAuthStateDB(
            state=state_id,
            workspace_id=test_role_with_user.workspace_id,
            user_id=test_role_with_user.user_id,
            provider_id=MockOAuthProvider.id,
            expires_at=expires_at,
        )
        session.add(oauth_state)
        await session.commit()

        # Select with FOR UPDATE lock
        locked_state = await session.get(OAuthStateDB, state_id, with_for_update=True)

        assert locked_state is not None
        assert locked_state.state == state_id

        # Delete the state
        await session.delete(locked_state)
        await session.commit()

        # Verify it's deleted
        stmt = select(OAuthStateDB).where(OAuthStateDB.state == state_id)
        result = await session.execute(stmt)
        assert result.scalars().first() is None

    async def test_oauth_state_validation(
        self,
        session: AsyncSession,
        test_role_with_user: Role,
    ) -> None:
        """Test OAuth state validation scenarios."""
        # Ensure role has required fields
        assert test_role_with_user.workspace_id is not None
        assert test_role_with_user.user_id is not None
        # Create a valid state
        valid_state_id = uuid.uuid4()
        valid_expires_at = datetime.now(UTC) + timedelta(minutes=10)

        valid_state = OAuthStateDB(
            state=valid_state_id,
            workspace_id=test_role_with_user.workspace_id,
            user_id=test_role_with_user.user_id,
            provider_id=MockOAuthProvider.id,
            expires_at=valid_expires_at,
        )
        session.add(valid_state)

        # Create an expired state
        expired_state_id = uuid.uuid4()
        expired_expires_at = datetime.now(UTC) - timedelta(minutes=1)

        expired_state = OAuthStateDB(
            state=expired_state_id,
            workspace_id=test_role_with_user.workspace_id,
            user_id=test_role_with_user.user_id,
            provider_id=MockOAuthProvider.id,
            expires_at=expired_expires_at,
        )
        session.add(expired_state)

        # Create a second workspace for the "wrong workspace" test
        wrong_workspace = Workspace(
            name="wrong-test-workspace",
            organization_id=uuid.uuid4(),
        )
        session.add(wrong_workspace)
        await session.commit()
        await session.refresh(wrong_workspace)

        # Create a state with wrong workspace
        wrong_workspace_state_id = uuid.uuid4()

        wrong_workspace_state = OAuthStateDB(
            state=wrong_workspace_state_id,
            workspace_id=wrong_workspace.id,
            user_id=test_role_with_user.user_id,
            provider_id=MockOAuthProvider.id,
            expires_at=valid_expires_at,
        )
        session.add(wrong_workspace_state)

        await session.commit()

        # Test valid state
        state = await session.get(OAuthStateDB, valid_state_id)
        assert state is not None
        assert state.workspace_id == test_role_with_user.workspace_id
        assert state.user_id == test_role_with_user.user_id
        assert datetime.now(UTC) < state.expires_at

        # Test expired state
        state = await session.get(OAuthStateDB, expired_state_id)
        assert state is not None
        assert datetime.now(UTC) >= state.expires_at

        # Test wrong workspace state
        state = await session.get(OAuthStateDB, wrong_workspace_state_id)
        assert state is not None
        assert state.workspace_id != test_role_with_user.workspace_id

    async def test_oauth_flow_with_state(
        self,
        session: AsyncSession,
        integration_service: IntegrationService,
        test_role_with_user: Role,
    ) -> None:
        """Test the full OAuth flow with state management."""
        provider_key = ProviderKey(
            id=MockOAuthProvider.id,
            grant_type=OAuthGrantType.AUTHORIZATION_CODE,
        )

        # Store provider configuration
        await integration_service.store_provider_config(
            provider_key=provider_key,
            client_id="test_client_id",
            client_secret=SecretStr("test_client_secret"),
            authorization_endpoint="https://login.example.com/oauth2/v2.0/authorize",
            token_endpoint="https://login.example.com/oauth2/v2.0/token",
        )

        # Ensure role has required fields
        assert test_role_with_user.workspace_id is not None
        assert test_role_with_user.user_id is not None
        # Simulate creating state for authorization
        state_id = uuid.uuid4()
        expires_at = datetime.now(UTC) + timedelta(minutes=10)

        oauth_state = OAuthStateDB(
            state=state_id,
            workspace_id=test_role_with_user.workspace_id,
            user_id=test_role_with_user.user_id,
            provider_id=provider_key.id,
            expires_at=expires_at,
        )
        session.add(oauth_state)
        await session.commit()

        # Verify state exists
        state = await session.get(OAuthStateDB, state_id)
        assert state is not None

        # Simulate callback - validate and delete state
        state = await session.get(OAuthStateDB, state_id, with_for_update=True)

        assert state is not None
        assert state.workspace_id == test_role_with_user.workspace_id
        assert state.user_id == test_role_with_user.user_id
        assert datetime.now(UTC) < state.expires_at

        # Delete state after validation
        await session.delete(state)
        await session.commit()

        # Verify state is deleted
        state = await session.get(OAuthStateDB, state_id)
        assert state is None
