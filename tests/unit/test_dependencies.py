import pytest
from fastapi import HTTPException, status
from pytest_mock import MockerFixture

from tracecat.auth.dependencies import require_auth_type_enabled, verify_auth_type
from tracecat.auth.enums import AuthType


@pytest.mark.anyio
async def test_verify_auth_type_invalid_type():
    """Test that invalid auth types raise ValueError."""
    with pytest.raises(ValueError, match="Invalid auth type"):
        await require_auth_type_enabled("invalid_type")  # type: ignore


@pytest.mark.parametrize(
    "target_type,allowed_types",
    [
        pytest.param(
            AuthType.BASIC,
            [],
            id="basic_auth",
        ),
        pytest.param(
            AuthType.SAML,
            [AuthType.GOOGLE_OAUTH, AuthType.BASIC],
            id="saml_auth",
        ),
    ],
)
@pytest.mark.anyio
async def test_verify_auth_type_not_allowed(
    mocker: MockerFixture, target_type: AuthType, allowed_types: list[AuthType]
):
    """Test that unauthorized auth types raise HTTPException."""
    mocker.patch("tracecat.config.TRACECAT__AUTH_TYPES", allowed_types)

    with pytest.raises(HTTPException) as exc:
        await verify_auth_type(target_type)

    assert exc.value.status_code == status.HTTP_403_FORBIDDEN
    assert exc.value.detail == "Auth type not allowed"


@pytest.mark.anyio
async def test_verify_auth_type_setting_disabled(mocker: MockerFixture):
    """Test that disabled auth types raise HTTPException."""
    mocker.patch("tracecat.config.TRACECAT__AUTH_TYPES", [AuthType.BASIC])
    mocker.patch("tracecat.auth.dependencies.get_setting", return_value=False)

    with pytest.raises(HTTPException) as exc:
        await verify_auth_type(AuthType.BASIC)

    assert exc.value.status_code == status.HTTP_403_FORBIDDEN
    assert exc.value.detail == f"Auth type {AuthType.BASIC.value} is not enabled"


@pytest.mark.anyio
async def test_verify_auth_type_invalid_setting(mocker: MockerFixture):
    """Test that invalid settings raise HTTPException."""
    mocker.patch("tracecat.config.TRACECAT__AUTH_TYPES", [AuthType.BASIC])
    mocker.patch("tracecat.auth.dependencies.get_setting", return_value=None)

    with pytest.raises(HTTPException) as exc:
        await verify_auth_type(AuthType.BASIC)

    assert exc.value.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
    assert exc.value.detail == "Invalid setting configuration"


@pytest.mark.anyio
async def test_verify_auth_type_success(mocker: MockerFixture):
    """Test successful auth type verification."""
    mocker.patch("tracecat.config.TRACECAT__AUTH_TYPES", [AuthType.BASIC])
    mocker.patch("tracecat.auth.dependencies.get_setting", return_value=True)

    # Should not raise any exceptions
    await verify_auth_type(AuthType.BASIC)
