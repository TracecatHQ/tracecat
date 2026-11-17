"""Tests for role derivation from users."""

import uuid

from tracecat.auth.credentials import get_role_from_user
from tracecat.auth.schemas import UserRole
from tracecat.db.models import User


def test_get_role_from_superuser() -> None:
    """Test tenant roles do not inherit platform superuser privileges."""
    user = User(
        id=uuid.uuid4(),
        email=f"superuser-{uuid.uuid4().hex[:8]}@example.com",
        hashed_password="hashed",
        role=UserRole.BASIC,
        is_active=True,
        is_superuser=True,
        is_verified=True,
    )

    role = get_role_from_user(
        user=user,
        organization_id=uuid.uuid4(),
    )

    assert role.is_platform_superuser is False
