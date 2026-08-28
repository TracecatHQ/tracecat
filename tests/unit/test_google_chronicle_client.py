"""Credential-isolation tests for the Google Chronicle REST client."""

import contextlib
from collections.abc import Iterator

import pytest
from tracecat_registry import SecretNotFoundError
from tracecat_registry._internal import secrets as registry_secrets
from tracecat_registry.integrations import google_chronicle


@contextlib.contextmanager
def registry_secrets_sandbox(values: dict[str, str]) -> Iterator[None]:
    token = registry_secrets.set_context(values)
    try:
        yield
    finally:
        registry_secrets.reset_context(token)


def test_user_token_wins_over_service_token() -> None:
    with registry_secrets_sandbox(
        {
            "GOOGLE_CHRONICLE_USER_TOKEN": "user-token",
            "GOOGLE_CHRONICLE_SERVICE_TOKEN": "service-token",
        }
    ):
        assert google_chronicle.get_access_token() == "user-token"


def test_service_token_is_used_without_a_user_token() -> None:
    with registry_secrets_sandbox({"GOOGLE_CHRONICLE_SERVICE_TOKEN": "service-token"}):
        assert google_chronicle.get_access_token() == "service-token"


def test_missing_chronicle_credentials_raise() -> None:
    with registry_secrets_sandbox({}), pytest.raises(SecretNotFoundError):
        google_chronicle.get_access_token()


def test_other_google_credentials_are_not_chronicle_fallbacks() -> None:
    with (
        registry_secrets_sandbox(
            {
                "GOOGLE_SERVICE_TOKEN": "generic-google-token",
                "GOOGLE_API_CREDENTIALS": '{"type": "service_account"}',
            }
        ),
        pytest.raises(SecretNotFoundError),
    ):
        google_chronicle.get_access_token()
