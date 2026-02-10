import pytest
from fastapi import HTTPException, status
from pytest_mock import MockerFixture
from starlette.requests import Request

from tracecat.auth.dependencies import (
    require_any_auth_type_enabled,
    require_auth_type_enabled,
    verify_auth_type,
)
from tracecat.auth.enums import AuthType


def make_request(path: str = "/auth/login?org=default") -> Request:
    scope = {
        "type": "http",
        "method": "GET",
        "path": path.split("?")[0],
        "query_string": (path.split("?", 1)[1] if "?" in path else "").encode(),
        "headers": [],
    }
    return Request(scope)


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
        await verify_auth_type(target_type, make_request())

    assert exc.value.status_code == status.HTTP_403_FORBIDDEN
    assert exc.value.detail == "Auth type not allowed"


@pytest.mark.anyio
async def test_verify_auth_type_setting_disabled(mocker: MockerFixture):
    """Test that disabled auth types raise HTTPException."""
    mocker.patch("tracecat.config.TRACECAT__AUTH_TYPES", [AuthType.SAML])
    mocker.patch("tracecat.auth.dependencies.get_setting_override", return_value=None)
    mocker.patch("tracecat.auth.dependencies.get_setting", return_value=False)

    mocker.patch(
        "tracecat.auth.dependencies.resolve_auth_organization_id",
        return_value="00000000-0000-0000-0000-000000000000",
    )

    with pytest.raises(HTTPException) as exc:
        await verify_auth_type(AuthType.SAML, make_request())

    assert exc.value.status_code == status.HTTP_403_FORBIDDEN
    assert exc.value.detail == f"Auth type {AuthType.SAML.value} is not enabled"


@pytest.mark.anyio
async def test_verify_auth_type_invalid_setting(mocker: MockerFixture):
    """Test that invalid settings raise HTTPException."""
    mocker.patch("tracecat.config.TRACECAT__AUTH_TYPES", [AuthType.SAML])
    mocker.patch("tracecat.auth.dependencies.get_setting", return_value=None)

    mocker.patch(
        "tracecat.auth.dependencies.resolve_auth_organization_id",
        return_value="00000000-0000-0000-0000-000000000000",
    )

    with pytest.raises(HTTPException) as exc:
        await verify_auth_type(AuthType.SAML, make_request())

    assert exc.value.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
    assert exc.value.detail == "Invalid setting configuration"


@pytest.mark.anyio
async def test_verify_auth_type_success(mocker: MockerFixture):
    """Test successful auth type verification."""
    mocker.patch("tracecat.config.TRACECAT__AUTH_TYPES", [AuthType.BASIC])
    mocker.patch("tracecat.auth.dependencies.get_setting_override", return_value=None)
    mocker.patch(
        "tracecat.auth.dependencies.get_setting",
        return_value=False,  # saml_enforced disabled
    )

    mocker.patch(
        "tracecat.auth.dependencies.resolve_auth_organization_id",
        return_value="00000000-0000-0000-0000-000000000000",
    )

    # Should not raise any exceptions
    await verify_auth_type(AuthType.BASIC, make_request())


@pytest.mark.anyio
async def test_verify_auth_type_non_saml_blocked_when_saml_enforced(
    mocker: MockerFixture,
) -> None:
    """When SAML is enforced for org, basic auth is blocked."""
    mocker.patch("tracecat.config.TRACECAT__AUTH_TYPES", [AuthType.BASIC])
    mocker.patch("tracecat.auth.dependencies.get_setting", return_value=True)
    mocker.patch(
        "tracecat.auth.dependencies.resolve_auth_organization_id",
        return_value="00000000-0000-0000-0000-000000000000",
    )

    with pytest.raises(HTTPException) as exc:
        await verify_auth_type(AuthType.BASIC, make_request())

    assert exc.value.status_code == status.HTTP_403_FORBIDDEN
    assert exc.value.detail == "SAML authentication is enforced for this organization"


@pytest.mark.parametrize(
    "auth_type",
    [AuthType.BASIC, AuthType.OIDC, AuthType.GOOGLE_OAUTH],
)
@pytest.mark.anyio
async def test_verify_auth_type_oidc_is_platform_controlled(
    mocker: MockerFixture, auth_type: AuthType
) -> None:
    """OIDC-style auth availability should not read auth-type enable settings."""
    mocker.patch("tracecat.config.TRACECAT__AUTH_TYPES", [auth_type])
    get_setting_mock = mocker.patch(
        "tracecat.auth.dependencies.get_setting",
        return_value=False,
    )
    mocker.patch(
        "tracecat.auth.dependencies.resolve_auth_organization_id",
        return_value="00000000-0000-0000-0000-000000000000",
    )

    await verify_auth_type(auth_type, make_request())

    get_setting_mock.assert_called_once()


@pytest.mark.anyio
async def test_require_any_auth_type_enabled_tries_next_candidate(
    mocker: MockerFixture,
) -> None:
    mocker.patch(
        "tracecat.config.TRACECAT__AUTH_TYPES",
        [AuthType.OIDC, AuthType.GOOGLE_OAUTH],
    )
    verify_auth_type_mock = mocker.patch(
        "tracecat.auth.dependencies.verify_auth_type",
        side_effect=[
            HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="OIDC disabled for organization",
            ),
            None,
        ],
    )
    dependency = require_any_auth_type_enabled([AuthType.OIDC, AuthType.GOOGLE_OAUTH])
    check_any = dependency.dependency
    assert check_any is not None

    await check_any(make_request())

    assert verify_auth_type_mock.await_count == 2


@pytest.mark.anyio
async def test_require_any_auth_type_enabled_raises_last_candidate_error(
    mocker: MockerFixture,
) -> None:
    mocker.patch(
        "tracecat.config.TRACECAT__AUTH_TYPES",
        [AuthType.OIDC, AuthType.GOOGLE_OAUTH],
    )
    mocker.patch(
        "tracecat.auth.dependencies.verify_auth_type",
        side_effect=[
            HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="OIDC disabled for organization",
            ),
            HTTPException(
                status_code=status.HTTP_428_PRECONDITION_REQUIRED,
                detail="Organization selection required",
            ),
        ],
    )
    dependency = require_any_auth_type_enabled([AuthType.OIDC, AuthType.GOOGLE_OAUTH])
    check_any = dependency.dependency
    assert check_any is not None

    with pytest.raises(HTTPException) as exc:
        await check_any(make_request())

    assert exc.value.status_code == status.HTTP_428_PRECONDITION_REQUIRED
    assert exc.value.detail == "Organization selection required"
