import pytest
from fastapi import HTTPException, status
from pytest_mock import MockerFixture

from tracecat.auth.dependencies import (
    require_any_auth_type_enabled,
    require_auth_type_enabled,
    verify_auth_type,
)
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
    """Test that disabled SAML setting raises HTTPException."""
    mocker.patch("tracecat.config.TRACECAT__AUTH_TYPES", [AuthType.SAML])
    mocker.patch("tracecat.auth.dependencies.get_setting_override", return_value=None)
    mocker.patch("tracecat.auth.dependencies.get_setting", return_value=False)

    with pytest.raises(HTTPException) as exc:
        await verify_auth_type(AuthType.SAML)

    assert exc.value.status_code == status.HTTP_403_FORBIDDEN
    assert exc.value.detail == f"Auth type {AuthType.SAML.value} is not enabled"


@pytest.mark.anyio
async def test_verify_auth_type_invalid_setting(mocker: MockerFixture):
    """Test that invalid settings raise HTTPException."""
    mocker.patch("tracecat.config.TRACECAT__AUTH_TYPES", [AuthType.SAML])
    mocker.patch("tracecat.auth.dependencies.get_setting_override", return_value=None)
    mocker.patch("tracecat.auth.dependencies.get_setting", return_value=None)

    with pytest.raises(HTTPException) as exc:
        await verify_auth_type(AuthType.SAML)

    assert exc.value.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
    assert exc.value.detail == "Invalid setting configuration"


@pytest.mark.parametrize(
    "auth_type",
    [AuthType.BASIC, AuthType.OIDC, AuthType.GOOGLE_OAUTH],
)
@pytest.mark.anyio
async def test_verify_auth_type_non_saml_is_platform_controlled(
    mocker: MockerFixture, auth_type: AuthType
) -> None:
    """Non-SAML auth types are platform-configured only, no DB lookups."""
    mocker.patch("tracecat.config.TRACECAT__AUTH_TYPES", [auth_type])
    get_setting_mock = mocker.patch(
        "tracecat.auth.dependencies.get_setting",
        return_value=False,
    )

    await verify_auth_type(auth_type)

    # No DB calls needed for non-SAML auth types
    get_setting_mock.assert_not_called()


@pytest.mark.anyio
async def test_require_any_auth_type_enabled_succeeds_on_first_match(
    mocker: MockerFixture,
) -> None:
    """First matching auth type is accepted without further checks."""
    mocker.patch(
        "tracecat.config.TRACECAT__AUTH_TYPES",
        [AuthType.OIDC, AuthType.GOOGLE_OAUTH],
    )
    verify_mock = mocker.patch(
        "tracecat.auth.dependencies.verify_auth_type",
    )
    dependency = require_any_auth_type_enabled([AuthType.OIDC, AuthType.GOOGLE_OAUTH])
    check_any = dependency.dependency
    assert check_any is not None

    await check_any()

    # Only the first matching type is checked
    verify_mock.assert_awaited_once_with(AuthType.OIDC)


@pytest.mark.anyio
async def test_require_any_auth_type_enabled_rejects_when_none_allowed(
    mocker: MockerFixture,
) -> None:
    """Raises 403 when no candidate auth type is in the allowed list."""
    mocker.patch(
        "tracecat.config.TRACECAT__AUTH_TYPES",
        [AuthType.BASIC],  # Neither OIDC nor Google OAuth
    )
    dependency = require_any_auth_type_enabled([AuthType.OIDC, AuthType.GOOGLE_OAUTH])
    check_any = dependency.dependency
    assert check_any is not None

    with pytest.raises(HTTPException) as exc:
        await check_any()

    assert exc.value.status_code == status.HTTP_403_FORBIDDEN
    assert exc.value.detail == "Auth type not allowed"
