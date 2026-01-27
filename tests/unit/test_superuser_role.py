import uuid

import pytest
from fastapi import HTTPException, status

from tracecat.auth.credentials import _require_superuser
from tracecat.auth.types import AccessLevel
from tracecat.contexts import ctx_role
from tracecat.db.models import User


@pytest.mark.anyio
async def test_require_superuser_allows_superuser() -> None:
    token = ctx_role.set(None)  # type: ignore[arg-type]
    try:
        user = User(id=uuid.uuid4(), is_superuser=True)
        role = await _require_superuser(user=user)

        assert role.type == "user"
        assert role.user_id == user.id
        assert role.access_level == AccessLevel.ADMIN
        assert role.service_id == "tracecat-api"
        # Superuser roles are platform-level (PlatformRole), not org-scoped (Role)
        # PlatformRole intentionally has no organization_id attribute
        assert not hasattr(role, "organization_id")
        assert role.is_platform_superuser is True
        assert ctx_role.get() == role
    finally:
        ctx_role.reset(token)


@pytest.mark.anyio
async def test_require_superuser_denies_non_superuser() -> None:
    user = User(id=uuid.uuid4(), is_superuser=False)
    with pytest.raises(HTTPException) as exc:
        await _require_superuser(user=user)

    assert exc.value.status_code == status.HTTP_403_FORBIDDEN
