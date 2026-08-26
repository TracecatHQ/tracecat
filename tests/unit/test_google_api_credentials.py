"""Credential resolution and request shaping for `tools.google_api`.

Covers the Tracecat-owned credential boundary: which of the three configured
sources `_get_google_credentials` picks, and how `call_api` shapes the request
before it reaches `googleapiclient`.
"""

from __future__ import annotations

import base64
import contextlib
from collections.abc import Iterator
from typing import Any
from unittest.mock import patch

import orjson
import pytest
from google.oauth2.credentials import Credentials as OAuthCredentials
from googleapiclient.http import MediaIoBaseUpload
from tracecat_registry import SecretNotFoundError
from tracecat_registry._internal import secrets as registry_secrets
from tracecat_registry.integrations import google_api

SERVICE_ACCOUNT_INFO: dict[str, Any] = {
    "type": "service_account",
    "project_id": "example-project",
    "private_key_id": "stub-key-id",
    "private_key": "stub-key",
    "client_email": "sa@example-project.iam.gserviceaccount.com",
    "client_id": "000000000000000000000",
    "token_uri": "https://oauth2.googleapis.com/token",
}

FROM_SERVICE_ACCOUNT_INFO = (
    "tracecat_registry.integrations.google_api."
    "service_account.Credentials.from_service_account_info"
)


def _credentials_json(**overrides: Any) -> str:
    return orjson.dumps({**SERVICE_ACCOUNT_INFO, **overrides}).decode()


@contextlib.contextmanager
def registry_secrets_sandbox(values: dict[str, str]) -> Iterator[None]:
    """Populate the registry-owned secrets context read by `secrets.get*`."""
    token = registry_secrets.set_context(values)
    try:
        yield
    finally:
        registry_secrets.reset_context(token)


class StubServiceAccountCredentials:
    """Stand-in for `service_account.Credentials` that records what it was given."""

    def __init__(self, info: dict[str, Any], scopes: list[str] | None) -> None:
        self.info = info
        self.scopes = scopes
        self.subject: str | None = None

    def with_subject(self, subject: str) -> StubServiceAccountCredentials:
        clone = StubServiceAccountCredentials(self.info, self.scopes)
        clone.subject = subject
        return clone


@pytest.fixture
def service_account_stub() -> Iterator[list[StubServiceAccountCredentials]]:
    """Patch the Google SDK factory and record every credential it mints."""
    minted: list[StubServiceAccountCredentials] = []

    def _from_service_account_info(
        info: dict[str, Any], scopes: list[str] | None = None, **_: Any
    ) -> StubServiceAccountCredentials:
        credentials = StubServiceAccountCredentials(dict(info), scopes)
        minted.append(credentials)
        return credentials

    with patch(FROM_SERVICE_ACCOUNT_INFO, side_effect=_from_service_account_info):
        yield minted


class RecordingRequest:
    def __init__(self, response: Any) -> None:
        self._response = response

    def execute(self) -> Any:
        return self._response


class RecordingResource:
    """Fake discovery resource that captures the kwargs each method receives."""

    def __init__(self, response: Any = None) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.response = response if response is not None else {}

    def __getattr__(self, name: str) -> Any:
        def _method(**kwargs: Any) -> RecordingRequest:
            self.calls.append((name, kwargs))
            return RecordingRequest(self.response)

        return _method


def test_access_token_wins_over_json_and_service_token(
    service_account_stub: list[StubServiceAccountCredentials],
) -> None:
    with registry_secrets_sandbox(
        {
            "GOOGLE_API_CREDENTIALS": _credentials_json(subject="embedded@example.com"),
            "GOOGLE_SERVICE_TOKEN": "service-token",
            "GOOGLE_API_SUBJECT": "env@example.com",
        }
    ):
        credentials = google_api._get_google_credentials(
            scopes=["https://www.googleapis.com/auth/drive"],
            subject="input@example.com",
            access_token="oauth-token",
        )

    assert isinstance(credentials, OAuthCredentials)
    assert credentials.token == "oauth-token"
    assert service_account_stub == []


def test_json_uses_subject_input_over_env_and_embedded(
    service_account_stub: list[StubServiceAccountCredentials],
) -> None:
    with registry_secrets_sandbox(
        {
            "GOOGLE_API_CREDENTIALS": _credentials_json(subject="embedded@example.com"),
            "GOOGLE_API_SUBJECT": "env@example.com",
        }
    ):
        credentials = google_api._get_google_credentials(subject="input@example.com")

    assert isinstance(credentials, StubServiceAccountCredentials)
    assert credentials.subject == "input@example.com"
    assert len(service_account_stub) == 1
    assert service_account_stub[0].scopes == google_api.DEFAULT_SCOPES


def test_json_uses_subject_secret_key_over_embedded(
    service_account_stub: list[StubServiceAccountCredentials],
) -> None:
    with registry_secrets_sandbox(
        {
            "GOOGLE_API_CREDENTIALS": _credentials_json(subject="embedded@example.com"),
            "GOOGLE_API_SUBJECT": "env@example.com",
        }
    ):
        credentials = google_api._get_google_credentials()

    assert isinstance(credentials, StubServiceAccountCredentials)
    assert credentials.subject == "env@example.com"


def test_json_falls_back_to_embedded_subject_and_pops_it(
    service_account_stub: list[StubServiceAccountCredentials],
) -> None:
    with registry_secrets_sandbox(
        {"GOOGLE_API_CREDENTIALS": _credentials_json(subject="embedded@example.com")}
    ):
        credentials = google_api._get_google_credentials()

    assert isinstance(credentials, StubServiceAccountCredentials)
    assert credentials.subject == "embedded@example.com"
    assert len(service_account_stub) == 1
    assert "subject" not in service_account_stub[0].info
    assert service_account_stub[0].scopes == google_api.DEFAULT_SCOPES


def test_json_without_any_subject_is_not_delegated(
    service_account_stub: list[StubServiceAccountCredentials],
) -> None:
    with registry_secrets_sandbox({"GOOGLE_API_CREDENTIALS": _credentials_json()}):
        credentials = google_api._get_google_credentials()

    assert isinstance(credentials, StubServiceAccountCredentials)
    assert credentials.subject is None


def test_service_token_precedes_json_without_overrides(
    service_account_stub: list[StubServiceAccountCredentials],
) -> None:
    with registry_secrets_sandbox(
        {
            "GOOGLE_API_CREDENTIALS": _credentials_json(),
            "GOOGLE_SERVICE_TOKEN": "service-token",
        }
    ):
        credentials = google_api._get_google_credentials()

    assert isinstance(credentials, OAuthCredentials)
    assert credentials.token == "service-token"
    assert service_account_stub == []


def test_subject_secret_key_beats_service_token(
    service_account_stub: list[StubServiceAccountCredentials],
) -> None:
    """A configured GOOGLE_API_SUBJECT must not be silently ignored."""
    with registry_secrets_sandbox(
        {
            "GOOGLE_API_CREDENTIALS": _credentials_json(),
            "GOOGLE_API_SUBJECT": "delegate@example.com",
            "GOOGLE_SERVICE_TOKEN": "service-token",
        }
    ):
        credentials = google_api._get_google_credentials()

    assert len(service_account_stub) == 1
    assert isinstance(credentials, StubServiceAccountCredentials)
    assert credentials.subject == "delegate@example.com"


def test_subject_without_json_raises() -> None:
    with registry_secrets_sandbox({"GOOGLE_SERVICE_TOKEN": "service-token"}):
        with pytest.raises(SecretNotFoundError):
            google_api._get_google_credentials(subject="input@example.com")


def test_scopes_only_fall_back_to_service_token(
    service_account_stub: list[StubServiceAccountCredentials],
) -> None:
    """Templates always pass scopes; a lone `google` integration must still work."""
    with registry_secrets_sandbox({"GOOGLE_SERVICE_TOKEN": "service-token"}):
        credentials = google_api._get_google_credentials(
            scopes=["https://www.googleapis.com/auth/drive"]
        )

    assert isinstance(credentials, OAuthCredentials)
    assert credentials.token == "service-token"
    assert service_account_stub == []


def test_scopes_only_without_any_credentials_raises() -> None:
    with registry_secrets_sandbox({}):
        with pytest.raises(SecretNotFoundError):
            google_api._get_google_credentials(
                scopes=["https://www.googleapis.com/auth/drive"]
            )


def test_no_credentials_configured_raises() -> None:
    with registry_secrets_sandbox({}):
        with pytest.raises(SecretNotFoundError) as exc_info:
            google_api._get_google_credentials()

    message = str(exc_info.value)
    assert "GOOGLE_API_CREDENTIALS" in message
    assert "GOOGLE_SERVICE_TOKEN" in message
    assert "access_token" in message


def test_call_api_sends_media_as_media_body() -> None:
    content = b"col_a,col_b\n1,2\n"
    resource = RecordingResource({"id": "file-id"})

    with (
        patch.object(google_api, "_build_google_service", return_value=object()),
        patch.object(google_api, "_resolve_resource", return_value=resource),
    ):
        google_api.call_api(
            service_name="drive",
            version="v3",
            resource="files",
            method_name="create",
            params={"body": {"name": "report.csv"}},
            access_token="oauth-token",
            media={
                "content_base64": base64.b64encode(content).decode(),
                "mime_type": "text/csv",
            },
        )

    _, kwargs = resource.calls[0]
    media_body = kwargs["media_body"]
    assert isinstance(media_body, MediaIoBaseUpload)
    assert media_body.mimetype() == "text/csv"
    assert media_body.size() == len(content)
    assert media_body.getbytes(0, len(content)) == content
    assert media_body.resumable() is False


def test_media_accepts_wrapped_base64_and_rejects_garbage() -> None:
    """Line-wrapped Base64 (GNU `base64` default) decodes; junk does not."""
    content = bytes(range(256)) * 3
    wrapped = base64.encodebytes(content).decode()
    assert "\n" in wrapped

    media_body = google_api._build_media_upload(
        {"content_base64": wrapped, "mime_type": "application/octet-stream"}
    )
    assert media_body.getbytes(0, len(content)) == content

    with pytest.raises(ValueError):
        google_api._build_media_upload(
            {"content_base64": "not*base64!", "mime_type": "text/plain"}
        )


def test_media_accepts_url_safe_unpadded_base64() -> None:
    """Gmail attachment `data` is URL-safe Base64 without padding."""
    content = bytes(range(256)) * 3 + b"\x00"
    url_safe = base64.urlsafe_b64encode(content).decode().rstrip("=")
    assert "-" in url_safe and "_" in url_safe and not url_safe.endswith("=")

    media_body = google_api._build_media_upload(
        {"content_base64": url_safe, "mime_type": "application/octet-stream"}
    )
    assert media_body.getbytes(0, len(content)) == content
