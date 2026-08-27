"""Pure unit tests for the Google OAuth provider registry."""

import pytest
from google.auth.exceptions import GoogleAuthError, RefreshError

from tracecat.integrations.enums import OAuthGrantType
from tracecat.integrations.providers import PROVIDER_REGISTRY
from tracecat.integrations.providers.base import BaseOAuthProvider
from tracecat.integrations.providers.google.admin import (
    GOOGLE_ADMIN_AC_SCOPES,
    GOOGLE_ADMIN_SCOPES,
)
from tracecat.integrations.providers.google.chronicle import CHRONICLE_SCOPES
from tracecat.integrations.providers.google.service_account import (
    _is_unauthorized_client,
)
from tracecat.integrations.schemas import ProviderKey

AC = OAuthGrantType.AUTHORIZATION_CODE
CC = OAuthGrantType.CLIENT_CREDENTIALS

EXPECTED_GRANT_TYPES: dict[str, set[OAuthGrantType]] = {
    "google": {CC},
    "google_admin": {AC, CC},
    "google_chronicle": {AC, CC},
    "google_docs": {AC, CC},
    "google_drive": {AC, CC},
    "google_forms": {AC, CC},
    "google_gmail": {AC, CC},
    "google_sheets": {AC, CC},
    "google_slides": {AC, CC},
}


def _google_classes() -> list[tuple[ProviderKey, type[BaseOAuthProvider]]]:
    return [
        (key, cls)
        for key, cls in PROVIDER_REGISTRY.items()
        if key.id in EXPECTED_GRANT_TYPES
    ]


@pytest.mark.parametrize(
    ("provider_id", "grant_types"), sorted(EXPECTED_GRANT_TYPES.items())
)
def test_registered_grant_types(
    provider_id: str, grant_types: set[OAuthGrantType]
) -> None:
    """Each Google provider id registers exactly the expected grant types."""
    registered = {key.grant_type for key in PROVIDER_REGISTRY if key.id == provider_id}
    assert registered == grant_types


def test_no_unexpected_google_providers() -> None:
    """No Google provider id is registered outside the expected set."""
    registered = {key.id for key in PROVIDER_REGISTRY if key.id.startswith("google")}
    assert registered == set(EXPECTED_GRANT_TYPES)


@pytest.mark.parametrize(
    ("key", "cls"), _google_classes(), ids=lambda value: str(value)
)
def test_service_account_json_flag(
    key: ProviderKey, cls: type[BaseOAuthProvider]
) -> None:
    """Client credentials providers use a service account JSON key; AC ones do not."""
    assert cls.metadata.service_account_json is (key.grant_type is CC)


@pytest.mark.parametrize(
    ("key", "cls"), _google_classes(), ids=lambda value: str(value)
)
def test_id_matches_metadata_id(key: ProviderKey, cls: type[BaseOAuthProvider]) -> None:
    """Provider ``id`` and ``metadata.id`` agree."""
    assert cls.id == cls.metadata.id == key.id


def test_google_admin_default_scopes() -> None:
    """The Admin SDK catch-all ships the full default scope list."""
    admin_cls = PROVIDER_REGISTRY[ProviderKey(id="google_admin", grant_type=CC)]
    assert admin_cls.scopes.default == GOOGLE_ADMIN_SCOPES
    assert GOOGLE_ADMIN_SCOPES
    assert all(
        scope.startswith("https://www.googleapis.com/auth/")
        for scope in GOOGLE_ADMIN_SCOPES
    )
    # Admin SDK only by default; Workspace data scopes are opt-in on the integration.
    assert all(
        scope.rsplit("/", 1)[1].startswith(("admin.", "apps.alerts"))
        for scope in GOOGLE_ADMIN_SCOPES
    )


def test_google_chronicle_scopes() -> None:
    """Both Chronicle grants default to the single Chronicle API scope."""
    assert CHRONICLE_SCOPES == ["https://www.googleapis.com/auth/chronicle"]
    for grant_type in (AC, CC):
        cls = PROVIDER_REGISTRY[
            ProviderKey(id="google_chronicle", grant_type=grant_type)
        ]
        assert cls.scopes.default == CHRONICLE_SCOPES


def test_google_chronicle_is_independent_of_the_generic_google_provider() -> None:
    """Chronicle never borrows the generic `google` credential's scopes."""
    generic = PROVIDER_REGISTRY[ProviderKey(id="google", grant_type=CC)]
    chronicle = PROVIDER_REGISTRY[ProviderKey(id="google_chronicle", grant_type=CC)]
    assert chronicle.id != generic.id
    assert not set(chronicle.scopes.default) & set(generic.scopes.default)


def test_google_admin_ac_scopes_exclude_alert_center() -> None:
    """The user OAuth flow covers Directory and Reports, never Alert Center."""
    ac_cls = PROVIDER_REGISTRY[ProviderKey(id="google_admin", grant_type=AC)]
    cc_cls = PROVIDER_REGISTRY[ProviderKey(id="google_admin", grant_type=CC)]
    assert ac_cls.scopes.default == GOOGLE_ADMIN_AC_SCOPES
    assert not any(scope.endswith("apps.alerts") for scope in GOOGLE_ADMIN_AC_SCOPES)
    assert set(GOOGLE_ADMIN_AC_SCOPES) < set(cc_cls.scopes.default)


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        pytest.param(
            RefreshError(
                "unauthorized_client: Client is unauthorized",
                {"error": "unauthorized_client", "error_description": "..."},
            ),
            True,
            id="structured-unauthorized-client",
        ),
        pytest.param(
            RefreshError(
                "invalid_grant: Invalid grant",
                {"error": "invalid_grant"},
            ),
            False,
            id="structured-other-error",
        ),
        pytest.param(
            # Google raises this shape when the body is not JSON. The message text
            # matches but there is no structured code, so it must not be matched.
            RefreshError("unauthorized_client"),
            False,
            id="unstructured-message-only",
        ),
        pytest.param(
            GoogleAuthError("unauthorized_client"),
            False,
            id="not-a-refresh-error",
        ),
    ],
)
def test_is_unauthorized_client(exc: GoogleAuthError, expected: bool) -> None:
    """Only the structured ``unauthorized_client`` code is matched."""
    assert _is_unauthorized_client(exc) is expected
