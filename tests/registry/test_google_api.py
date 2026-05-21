from typing import Any

import pytest
from tracecat_registry import SecretNotFoundError
from tracecat_registry.integrations import google_api


class FakeRequest:
    def __init__(self, response: Any) -> None:
        self._response = response

    def execute(self) -> Any:
        return self._response


class FakeMethodResource:
    def __init__(self, calls: list[dict[str, Any]], responses: list[Any]):
        self._calls = calls
        self._responses = responses

    def list(self, **params: Any) -> FakeRequest:
        self._calls.append(params)
        return FakeRequest(self._responses.pop(0))


class FakeValuesResource:
    def __init__(self, calls: list[dict[str, Any]], responses: list[Any]):
        self._calls = calls
        self._responses = responses

    def get(self, **params: Any) -> FakeRequest:
        self._calls.append(params)
        return FakeRequest(self._responses.pop(0))


class FakeSpreadsheetsResource:
    def __init__(self, calls: list[dict[str, Any]], responses: list[Any]):
        self._calls = calls
        self._responses = responses

    def values(self) -> FakeValuesResource:
        return FakeValuesResource(self._calls, self._responses)


class FakeService:
    def __init__(self, calls: list[dict[str, Any]], responses: list[Any]):
        self._calls = calls
        self._responses = responses

    def files(self) -> FakeMethodResource:
        return FakeMethodResource(self._calls, self._responses)

    def spreadsheets(self) -> FakeSpreadsheetsResource:
        return FakeSpreadsheetsResource(self._calls, self._responses)


def test_call_api_prefers_oauth_token(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []
    built: dict[str, Any] = {}

    def build(*args: Any, **kwargs: Any) -> FakeService:
        built["args"] = args
        built["kwargs"] = kwargs
        return FakeService(calls, [{"files": [{"id": "1"}]}])

    monkeypatch.setattr(
        google_api.secrets,
        "get_or_default",
        lambda key: "service-token" if key == "GOOGLE_SERVICE_TOKEN" else None,
    )
    monkeypatch.setattr(google_api, "build", build)

    result = google_api.call_api(
        service_name="drive",
        version="v3",
        resource="files",
        method_name="list",
        params={"pageSize": 10},
    )

    assert result == {"files": [{"id": "1"}]}
    assert calls == [{"pageSize": 10}]
    assert built["args"] == ("drive", "v3")
    assert built["kwargs"]["cache_discovery"] is False
    assert built["kwargs"]["credentials"].token == "service-token"


def test_call_api_uses_service_account_json_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []
    captured: dict[str, Any] = {}
    credentials = object()

    def from_service_account_info(info: dict[str, Any], scopes: list[str]) -> object:
        captured["info"] = info
        captured["scopes"] = scopes
        return credentials

    def build(*args: Any, **kwargs: Any) -> FakeService:
        captured["build_args"] = args
        captured["build_kwargs"] = kwargs
        return FakeService(calls, [{"values": [["a"]]}])

    monkeypatch.setattr(
        google_api.secrets,
        "get_or_default",
        lambda key: (
            None if key == "GOOGLE_SERVICE_TOKEN" else '{"type":"service_account"}'
        ),
    )
    monkeypatch.setattr(
        google_api.secrets, "get", lambda _key: '{"type":"service_account"}'
    )
    monkeypatch.setattr(
        google_api.service_account.Credentials,
        "from_service_account_info",
        from_service_account_info,
    )
    monkeypatch.setattr(google_api, "build", build)

    result = google_api.call_api(
        service_name="sheets",
        version="v4",
        resource="spreadsheets.values",
        method_name="get",
        params={"spreadsheetId": "sheet-id", "range": "A1:B2"},
    )

    assert result == {"values": [["a"]]}
    assert calls == [{"spreadsheetId": "sheet-id", "range": "A1:B2"}]
    assert captured["info"] == {"type": "service_account"}
    assert captured["scopes"] == google_api.DEFAULT_SCOPES
    assert captured["build_kwargs"]["credentials"] is credentials


def test_call_api_uses_service_account_when_scopes_override_oauth_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []
    captured: dict[str, Any] = {}
    credentials = object()

    def from_service_account_info(info: dict[str, Any], scopes: list[str]) -> object:
        captured["info"] = info
        captured["scopes"] = scopes
        return credentials

    def build(*_args: Any, **kwargs: Any) -> FakeService:
        captured["build_kwargs"] = kwargs
        return FakeService(calls, [{"files": [{"id": "1"}]}])

    monkeypatch.setattr(
        google_api.secrets,
        "get_or_default",
        lambda key: (
            "service-token"
            if key == "GOOGLE_SERVICE_TOKEN"
            else '{"type":"service_account"}'
        ),
    )
    monkeypatch.setattr(
        google_api.secrets, "get", lambda _key: '{"type":"service_account"}'
    )
    monkeypatch.setattr(
        google_api.service_account.Credentials,
        "from_service_account_info",
        from_service_account_info,
    )
    monkeypatch.setattr(google_api, "build", build)

    result = google_api.call_api(
        service_name="drive",
        version="v3",
        resource="files",
        method_name="list",
        scopes=["scope-from-input"],
    )

    assert result == {"files": [{"id": "1"}]}
    assert captured["info"] == {"type": "service_account"}
    assert captured["scopes"] == ["scope-from-input"]
    assert captured["build_kwargs"]["credentials"] is credentials


def test_call_api_applies_service_account_subject(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeCredentials:
        def with_subject(self, subject: str) -> "FakeCredentials":
            captured["subject"] = subject
            return self

    captured: dict[str, Any] = {}
    credentials = FakeCredentials()

    monkeypatch.setattr(
        google_api.secrets,
        "get_or_default",
        lambda key: (
            None if key == "GOOGLE_SERVICE_TOKEN" else '{"type":"service_account"}'
        ),
    )
    monkeypatch.setattr(
        google_api.secrets, "get", lambda _key: '{"type":"service_account"}'
    )
    monkeypatch.setattr(
        google_api.service_account.Credentials,
        "from_service_account_info",
        lambda _info, scopes: credentials,
    )

    result = google_api._get_google_credentials(subject="user@example.test")

    assert result is credentials
    assert captured["subject"] == "user@example.test"


def test_call_api_rejects_service_account_overrides_without_service_account_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        google_api.secrets,
        "get_or_default",
        lambda key: "service-token" if key == "GOOGLE_SERVICE_TOKEN" else None,
    )

    with pytest.raises(SecretNotFoundError, match="GOOGLE_API_CREDENTIALS"):
        google_api.call_api(
            service_name="drive",
            version="v3",
            resource="files",
            method_name="list",
            subject="user@example.test",
        )


def test_call_api_rejects_invalid_service_account_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        google_api.secrets,
        "get_or_default",
        lambda key: None if key == "GOOGLE_SERVICE_TOKEN" else "{invalid",
    )
    monkeypatch.setattr(google_api.secrets, "get", lambda _key: "{invalid")

    with pytest.raises(ValueError, match="not a valid JSON string"):
        google_api.call_api(
            service_name="drive",
            version="v3",
            resource="files",
            method_name="list",
        )


def test_call_api_requires_oauth_or_service_account_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(google_api.secrets, "get_or_default", lambda _key: None)

    with pytest.raises(SecretNotFoundError, match="GOOGLE_SERVICE_TOKEN"):
        google_api.call_api(
            service_name="drive",
            version="v3",
            resource="files",
            method_name="list",
        )


def test_call_api_returns_non_dict_response(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []

    def build(*_args: Any, **_kwargs: Any) -> FakeService:
        return FakeService(calls, [["not-a-dict"]])

    monkeypatch.setattr(
        google_api.secrets,
        "get_or_default",
        lambda key: "service-token" if key == "GOOGLE_SERVICE_TOKEN" else None,
    )
    monkeypatch.setattr(google_api, "build", build)

    result = google_api.call_api(
        service_name="drive",
        version="v3",
        resource="files",
        method_name="list",
    )

    assert result == ["not-a-dict"]


def test_call_paginated_api_returns_pages(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []

    def build(*_args: Any, **_kwargs: Any) -> FakeService:
        return FakeService(
            calls,
            [
                {"files": [{"id": "1"}], "nextPageToken": "next-token"},
                {"files": [{"id": "2"}]},
            ],
        )

    monkeypatch.setattr(
        google_api.secrets,
        "get_or_default",
        lambda key: "service-token" if key == "GOOGLE_SERVICE_TOKEN" else None,
    )
    monkeypatch.setattr(google_api, "build", build)

    result = google_api.call_paginated_api(
        service_name="drive",
        version="v3",
        resource="files",
        method_name="list",
        params={"pageSize": 1},
    )

    assert result == [
        {"files": [{"id": "1"}], "nextPageToken": "next-token"},
        {"files": [{"id": "2"}]},
    ]
    assert calls == [{"pageSize": 1}, {"pageSize": 1, "pageToken": "next-token"}]


def test_call_paginated_api_supports_custom_token_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []

    def build(*_args: Any, **_kwargs: Any) -> FakeService:
        return FakeService(
            calls,
            [
                {
                    "voidedPurchases": [{"orderId": "1"}],
                    "tokenPagination": {"nextPageToken": "next-token"},
                },
                {
                    "voidedPurchases": [{"orderId": "2"}],
                    "tokenPagination": {},
                },
            ],
        )

    monkeypatch.setattr(
        google_api.secrets,
        "get_or_default",
        lambda key: "service-token" if key == "GOOGLE_SERVICE_TOKEN" else None,
    )
    monkeypatch.setattr(google_api, "build", build)

    result = google_api.call_paginated_api(
        service_name="androidpublisher",
        version="v3",
        resource="files",
        method_name="list",
        params={"packageName": "com.example.app"},
        page_token_param="token",
        next_page_token_path="tokenPagination.nextPageToken",
    )

    assert result == [
        {
            "voidedPurchases": [{"orderId": "1"}],
            "tokenPagination": {"nextPageToken": "next-token"},
        },
        {
            "voidedPurchases": [{"orderId": "2"}],
            "tokenPagination": {},
        },
    ]
    assert calls == [
        {"packageName": "com.example.app"},
        {"packageName": "com.example.app", "token": "next-token"},
    ]
