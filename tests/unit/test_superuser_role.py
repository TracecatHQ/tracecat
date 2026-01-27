import uuid

import pytest
from fastapi import HTTPException, status

from tracecat.auth.credentials import _require_superuser
from tracecat.auth.types import AccessLevel, PlatformRole
from tracecat.db.models import User


@pytest.mark.anyio
async def test_require_superuser_allows_superuser() -> None:
    user = User(id=uuid.uuid4(), is_superuser=True)
    role = await _require_superuser(user=user)

    # Verify PlatformRole is returned
    assert isinstance(role, PlatformRole)
    assert role.type == "user"
    assert role.user_id == user.id
    assert role.access_level == AccessLevel.ADMIN
    assert role.service_id == "tracecat-api"
    # PlatformRole is always a platform superuser
    assert role.is_platform_superuser is True
    # Note: PlatformRole doesn't have organization_id - it's platform-scoped


@pytest.mark.anyio
async def test_require_superuser_denies_non_superuser() -> None:
    user = User(id=uuid.uuid4(), is_superuser=False)
    with pytest.raises(HTTPException) as exc:
        await _require_superuser(user=user)

    assert exc.value.status_code == status.HTTP_403_FORBIDDEN
